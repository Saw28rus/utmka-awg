import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

CLIENTS_TABLE_PATHS = (
    "/opt/amnezia/awg/clientsTable",
    "/etc/amnezia/amneziawg/clientsTable",
)

AWG_PARAM_KEYS = (
    "Jc",
    "Jmin",
    "Jmax",
    "S1",
    "S2",
    "S3",
    "S4",
    "H1",
    "H2",
    "H3",
    "H4",
    "I1",
    "I2",
    "I3",
    "I4",
    "I5",
)


@dataclass
class ParsedPeer:
    public_key: str
    client_ip: str
    index: int


@dataclass
class InterfaceInfo:
    private_key: Optional[str] = None
    listen_port: Optional[int] = None
    address: Optional[str] = None
    dns: Optional[str] = None
    awg_params: dict[str, str] = field(default_factory=dict)


def split_sections(config_text: str) -> tuple[str, list[str]]:
    """Возвращает текст [Interface]-секции и список текстов [Peer]-секций."""
    interface_text = ""
    peer_texts: list[str] = []
    current: Optional[list[str]] = None
    bucket = "none"

    for line in config_text.splitlines():
        header = line.strip().lower()
        if header == "[interface]":
            bucket = "interface"
            current = []
            continue
        if header == "[peer]":
            bucket = "peer"
            current = []
            peer_texts.append("")
            continue
        if current is not None:
            current.append(line)
            if bucket == "interface":
                interface_text = "\n".join(current)
            elif bucket == "peer":
                peer_texts[-1] = "\n".join(current)

    return interface_text, peer_texts


def parse_interface(config_text: str) -> InterfaceInfo:
    interface_text, _ = split_sections(config_text)
    info = InterfaceInfo()
    info.private_key = _line_value(interface_text, "PrivateKey")
    listen_port = _line_value(interface_text, "ListenPort")
    if listen_port and listen_port.isdigit():
        info.listen_port = int(listen_port)
    info.address = _line_value(interface_text, "Address")
    info.dns = _line_value(interface_text, "DNS")
    for key in AWG_PARAM_KEYS:
        value = _line_value(interface_text, key)
        if value is not None:
            info.awg_params[key] = value
    return info


def parse_peers(config_text: str) -> list[ParsedPeer]:
    _, peer_texts = split_sections(config_text)
    peers: list[ParsedPeer] = []
    for index, section in enumerate(peer_texts):
        public_key = _line_value(section, "PublicKey")
        if not public_key:
            continue
        allowed_ips = _line_value(section, "AllowedIPs") or ""
        client_ip = allowed_ips.split(",")[0].strip().split("/")[0] or "0.0.0.0"
        peers.append(ParsedPeer(public_key=public_key, client_ip=client_ip, index=index))
    return peers


def next_client_ip(address_cidr: Optional[str], used_ips: list[str]) -> str:
    """Подбираем следующий свободный адрес в подсети сервера.

    Резервируем сетевой/широковещательный адреса, адрес самого сервера и
    традиционный шлюз .1. Двигаемся по возрастанию от уже занятых адресов,
    как это делает сам Amnezia, чтобы не выдать клиенту адрес сервера.
    """
    try:
        if address_cidr and "/" in address_cidr:
            network = ipaddress.ip_network(address_cidr.split(",")[0].strip(), strict=False)
        else:
            network = ipaddress.ip_network("10.8.1.0/24")
    except ValueError:
        network = ipaddress.ip_network("10.8.1.0/24")

    used: set = set()
    for raw in used_ips:
        try:
            used.add(ipaddress.ip_address(raw.split("/")[0].strip()))
        except ValueError:
            continue

    reserved = {network.network_address, network.broadcast_address}
    # .1 — традиционный адрес сервера/шлюза в подсети Amnezia
    reserved.add(network.network_address + 1)
    if address_cidr:
        try:
            reserved.add(ipaddress.ip_address(address_cidr.split("/")[0].strip()))
        except ValueError:
            pass

    blocked = used | reserved

    # старт: на единицу выше максимального занятого адреса (как в Amnezia)
    host_ints = [int(ip) for ip in used if ip in network]
    if host_ints:
        start = max(host_ints) + 1
    else:
        start = int(network.network_address) + 2

    for value in range(start, int(network.broadcast_address)):
        candidate = ipaddress.ip_address(value)
        if candidate not in blocked:
            return str(candidate)

    # на случай дыр в начале диапазона — ищем любой свободный
    for host in network.hosts():
        if host not in blocked:
            return str(host)

    raise ValueError("В подсети не осталось свободных адресов.")


def parse_client_names(raw_text: str) -> dict[str, str]:
    """Возвращает map public_key -> clientName из Amnezia clientsTable."""
    text = raw_text.strip()
    if not text:
        return {}

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}

    mapping: dict[str, str] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            client_id = item.get("clientId") or item.get("publicKey") or item.get("public_key")
            user_data = item.get("userData") if isinstance(item.get("userData"), dict) else {}
            name = (
                user_data.get("clientName")
                or item.get("clientName")
                or item.get("name")
            )
            if client_id and isinstance(name, str) and name.strip():
                mapping[client_id.strip()] = name.strip()
    elif isinstance(data, dict):
        for client_id, value in data.items():
            if isinstance(value, dict):
                name = value.get("clientName") or value.get("name")
                if isinstance(name, str) and name.strip():
                    mapping[client_id.strip()] = name.strip()
    return mapping


def append_client_to_table(raw_text: str, public_key: str, name: str) -> str:
    text = raw_text.strip()
    data: list = []
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                data = parsed
        except json.JSONDecodeError:
            data = []

    entry = {
        "clientId": public_key,
        "userData": {
            "clientName": name,
            "creationDate": datetime.now(timezone.utc).isoformat(),
        },
    }
    data.append(entry)
    return json.dumps(data, ensure_ascii=False, indent=4)


def remove_client_from_table(raw_text: str, public_key: str) -> str:
    """Убирает запись клиента из Amnezia clientsTable по clientId (public_key).

    Идемпотентно: если записи нет или таблица не парсится — возвращает исходный
    текст без изменений (вызывающий код сам решит, писать ли файл).
    """
    text = raw_text.strip()
    if not text:
        return raw_text
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return raw_text
    if not isinstance(data, list):
        return raw_text
    filtered = [
        item
        for item in data
        if not (isinstance(item, dict) and item.get("clientId") == public_key)
    ]
    return json.dumps(filtered, ensure_ascii=False, indent=4)


def build_peer_block(public_key: str, preshared_key: Optional[str], client_ip: str) -> str:
    lines = ["", "[Peer]", f"PublicKey = {public_key}"]
    if preshared_key:
        lines.append(f"PresharedKey = {preshared_key}")
    lines.append(f"AllowedIPs = {client_ip}/32")
    return "\n".join(lines) + "\n"


def resolve_endpoint_host(record: dict, fallback_host: str) -> str:
    """Хост для `[Peer] Endpoint` в клиентском конфиге.

    Приоритет того, что увидит клиент в Endpoint:
    1. Явно заданный endpoint-домен (вкладка «Маскировка» → Endpoint-домен).
    2. Домен HTTPS-панели, если он активен — он уже DNS-проверен на этот сервер,
       поэтому VPN-порт доступен по тому же адресу. Так пользователю не нужно
       вводить домен второй раз: задал домен панели → конфиги ведут на домен.
    3. IP сервера (fallback).

    Чат-субдомен сюда НЕ берём: он изолирован под мини-апп. Для отдельного
    VPN-домена есть явное поле Endpoint-домен.
    """
    explicit = (record.get("endpoint_host") or "").strip()
    if explicit:
        return explicit
    panel_ssl = record.get("panel_ssl") or {}
    if panel_ssl.get("status") == "active":
        domain = (panel_ssl.get("domain") or "").strip()
        if domain:
            return domain
    return fallback_host


# MTU клиентского туннеля. 1280 — стандарт Amnezia и минимум IPv6: пакеты
# гарантированно проходят без фрагментации даже на мобильных сетях (где ICMP
# «fragmentation needed» часто режется → PMTU black-hole) и через каскад, где
# транзит utmka-cas0 имеет MTU 1280. Без явного MTU клиент берёт 1420 →
# «подключилось, но сайты не грузятся» на мобильном интернете.
CLIENT_MTU = 1280


def build_client_config(
    *,
    client_private_key: str,
    client_ip: str,
    dns: Optional[str],
    server_public_key: str,
    preshared_key: Optional[str],
    endpoint_host: str,
    endpoint_port: int,
    awg_params: dict[str, str],
    keepalive: int = 25,
    mtu: int = CLIENT_MTU,
) -> str:
    interface_lines = [
        "[Interface]",
        f"Address = {client_ip}/32",
        f"DNS = {dns or '1.1.1.1'}",
        f"PrivateKey = {client_private_key}",
        f"MTU = {mtu}",
    ]
    for key in AWG_PARAM_KEYS:
        if key in awg_params:
            interface_lines.append(f"{key} = {awg_params[key]}")

    peer_lines = [
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
    ]
    if preshared_key:
        peer_lines.append(f"PresharedKey = {preshared_key}")
    peer_lines.extend(
        [
            "AllowedIPs = 0.0.0.0/0, ::/0",
            f"Endpoint = {endpoint_host}:{endpoint_port}",
            f"PersistentKeepalive = {keepalive}",
        ]
    )

    return "\n".join(interface_lines + peer_lines) + "\n"


@dataclass
class PeerTransfer:
    rx_bytes: int
    tx_bytes: int
    handshake_unix: int


def parse_dump(text: str) -> dict[str, PeerTransfer]:
    """Парсит вывод `awg show all dump` / `wg show all dump`.

    Колонки peer (с конца): pubkey psk endpoint allowed handshake rx tx keepalive.
    Работает и для `show <iface> dump`, и для `show all dump` (с префиксом интерфейса).
    """
    stats: dict[str, PeerTransfer] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8:
            continue
        public_key = fields[-8]
        try:
            rx = int(fields[-3])
            tx = int(fields[-2])
        except ValueError:
            continue
        try:
            handshake = int(fields[-4])
        except ValueError:
            handshake = 0
        if "=" not in public_key:
            # публичные ключи WireGuard заканчиваются на '='
            continue
        stats[public_key] = PeerTransfer(rx_bytes=rx, tx_bytes=tx, handshake_unix=handshake)
    return stats


def _line_value(section: str, key: str) -> Optional[str]:
    match = re.search(rf"(?im)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(.+?)[ \t]*$", section)
    return match.group(1).strip() if match else None
