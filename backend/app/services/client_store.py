from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from app.core.crypto import decrypt, encrypt
from app.schemas.clients import ClientDetail, ClientListItem
from app.services.awg_config import ParsedPeer, PeerTransfer
from app.services.persistence import read_json, write_json
from app.services.qr import build_qr_data_url

CLIENTS_FILE = "clients.json"
ONLINE_WINDOW = timedelta(seconds=180)
BLOCKED_STATUSES = {"expired", "over_limit", "disabled"}


class ClientStore:
    def __init__(self) -> None:
        self._clients: dict[str, dict] = read_json(CLIENTS_FILE, {})

    def _persist(self) -> None:
        write_json(CLIENTS_FILE, self._clients)

    def list_all(self, server_id: Optional[str] = None) -> list[ClientListItem]:
        items = [self._to_list_item(record) for record in self._clients.values()]
        if server_id:
            items = [item for item in items if item.server_id == server_id]
        return sorted(items, key=lambda item: ((item.server_name or ""), item.name.lower()))

    def get_detail(self, client_id: str) -> Optional[ClientDetail]:
        record = self._clients.get(client_id)
        if not record:
            return None
        config_text = decrypt(record.get("config_text_enc"))
        vpn_link = decrypt(record.get("vpn_link_enc"))
        return ClientDetail(
            **self._common_fields(record),
            config_text=config_text,
            vpn_link=vpn_link,
            qr_awg=build_qr_data_url(config_text) if config_text else None,
            qr_vpn=build_qr_data_url(vpn_link) if vpn_link else None,
            endpoint=record.get("endpoint"),
            has_private_key=bool(record.get("private_key_enc")),
        )

    def count_for_server(self, server_id: str) -> int:
        return len([r for r in self._clients.values() if r["server_id"] == server_id])

    def used_ips_for_server(self, server_id: str) -> list[str]:
        return [r["client_ip"] for r in self._clients.values() if r["server_id"] == server_id]

    def add_client(
        self,
        *,
        server_id: str,
        server_name: str,
        name: str,
        protocol: str,
        client_ip: str,
        public_key: str,
        private_key: Optional[str],
        preshared_key: Optional[str],
        config_text: Optional[str],
        vpn_link: Optional[str],
        endpoint: Optional[str],
        imported: bool,
        traffic_limit_bytes: Optional[int] = None,
        expires_at: Optional[str] = None,
        keepalive: int = 25,
    ) -> ClientDetail:
        client_id = str(uuid4())
        record = {
            "id": client_id,
            "name": name,
            "server_id": server_id,
            "server_name": server_name,
            "protocol": protocol,
            "status": "active",
            "client_ip": client_ip,
            "imported": imported,
            "keepalive": keepalive,
            "public_key": public_key,
            "private_key_enc": encrypt(private_key),
            "preshared_key_enc": encrypt(preshared_key),
            "config_text_enc": encrypt(config_text),
            "vpn_link_enc": encrypt(vpn_link),
            "endpoint": endpoint,
            "traffic_used_bytes": 0,
            "traffic_up_bytes": 0,
            "traffic_down_bytes": 0,
            "traffic_limit_bytes": traffic_limit_bytes,
            "expires_at": expires_at,
            "last_handshake_at": None,
            "blocked_on_server": False,
            "peer_block_enc": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._clients[client_id] = record
        self._persist()
        detail = self.get_detail(client_id)
        assert detail is not None
        return detail

    def get_secrets(self, client_id: str) -> Optional[dict]:
        """Расшифрованные ключи клиента для перевыпуска конфига. Не логировать."""
        record = self._clients.get(client_id)
        if not record:
            return None
        return {
            "private_key": decrypt(record.get("private_key_enc")),
            "preshared_key": decrypt(record.get("preshared_key_enc")),
        }

    def get_keepalive(self, client_id: str) -> int:
        record = self._clients.get(client_id)
        if not record:
            return 25
        try:
            return int(record.get("keepalive", 25))
        except (ValueError, TypeError):
            return 25

    def set_keepalive(self, client_id: str, keepalive: int) -> None:
        record = self._clients.get(client_id)
        if not record:
            return
        record["keepalive"] = keepalive
        self._persist()

    def get_record_raw(self, client_id: str) -> Optional[dict]:
        return self._clients.get(client_id)

    def update_issued_config(
        self,
        client_id: str,
        *,
        config_text: Optional[str],
        vpn_link: Optional[str],
        endpoint: Optional[str] = None,
    ) -> None:
        """Перевыпуск: обновляем выданный конфиг/ссылку, ключи не трогаем."""
        record = self._clients.get(client_id)
        if not record:
            return
        record["config_text_enc"] = encrypt(config_text)
        record["vpn_link_enc"] = encrypt(vpn_link)
        if endpoint:
            record["endpoint"] = endpoint
        self._persist()

    def channel_index(self) -> list[dict]:
        """Лёгкий индекс для группировки клиентов по каналам (PA2)."""
        return [
            {
                "id": cid,
                "server_id": record["server_id"],
                "protocol": (record.get("protocol") or "awg2"),
            }
            for cid, record in self._clients.items()
        ]

    def reissue_targets(self, server_id: str, protocol: str) -> list[dict]:
        """Клиенты для переиздания конфигов (смена маскировки Xray и т.п.)."""
        out: list[dict] = []
        for cid, record in self._clients.items():
            if record["server_id"] != server_id or record.get("protocol") != protocol:
                continue
            out.append(
                {
                    "id": cid,
                    "name": record.get("name") or "client",
                    "public_key": record.get("public_key"),
                    "has_config": bool(record.get("config_text_enc")),
                    "has_vpn": bool(record.get("vpn_link_enc")),
                }
            )
        return out

    def update_limits(
        self,
        client_id: str,
        *,
        changes: dict,
    ) -> Optional[ClientDetail]:
        record = self._clients.get(client_id)
        if not record:
            return None
        if "traffic_limit_bytes" in changes:
            record["traffic_limit_bytes"] = changes["traffic_limit_bytes"]
        if "expires_at" in changes:
            record["expires_at"] = changes["expires_at"]
        if "status" in changes and changes["status"] in {"active", "disabled"}:
            record["status"] = changes["status"]
        if "billing_mode" in changes and changes["billing_mode"] in {"free", "paid"}:
            record["billing_mode"] = changes["billing_mode"]
            if changes["billing_mode"] == "free":
                record["billing_amount_kopecks"] = None
        if "billing_amount_kopecks" in changes:
            record["billing_amount_kopecks"] = changes["billing_amount_kopecks"]
        if "billing_period_months" in changes and changes["billing_period_months"]:
            record["billing_period_months"] = int(changes["billing_period_months"])
        self._persist()
        return self.get_detail(client_id)

    def enforcement_view(self, server_id: str) -> list[dict]:
        """Данные для блокировки/разблокировки peer'ов на сервере."""
        view = []
        for record in self._clients.values():
            if record["server_id"] != server_id or not record.get("public_key"):
                continue
            view.append(
                {
                    "id": record["id"],
                    "public_key": record["public_key"],
                    "should_block": self._effective_status(record) in BLOCKED_STATUSES,
                    "blocked_on_server": record.get("blocked_on_server", False),
                    "peer_block": decrypt(record.get("peer_block_enc")),
                }
            )
        return view

    def set_blocked(self, client_id: str, blocked: bool, peer_block: Optional[str] = None) -> None:
        record = self._clients.get(client_id)
        if not record:
            return
        record["blocked_on_server"] = blocked
        if blocked and peer_block is not None:
            record["peer_block_enc"] = encrypt(peer_block)
        if not blocked:
            record["peer_block_enc"] = None
        self._persist()

    def import_peers(
        self,
        server_id: str,
        *,
        server_name: str,
        peers: list[ParsedPeer],
        names: dict[str, str],
    ) -> int:
        self._clients = {
            client_id: client
            for client_id, client in self._clients.items()
            if client["server_id"] != server_id
        }

        for peer in peers:
            name = names.get(peer.public_key) or f"Client {peer.index + 1}"
            client_id = str(uuid4())
            self._clients[client_id] = {
                "id": client_id,
                "name": name,
                "server_id": server_id,
                "server_name": server_name,
                "protocol": "awg2",
                "status": "active",
                "client_ip": peer.client_ip,
                "imported": True,
                "public_key": peer.public_key,
                "private_key_enc": None,
                "preshared_key_enc": None,
                "config_text_enc": None,
                "vpn_link_enc": None,
                "endpoint": None,
                "traffic_used_bytes": 0,
                "traffic_up_bytes": 0,
                "traffic_down_bytes": 0,
                "traffic_limit_bytes": None,
                "expires_at": None,
                "last_handshake_at": None,
                "blocked_on_server": False,
                "peer_block_enc": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        self._persist()
        return self.count_for_server(server_id)

    def update_traffic(self, server_id: str, stats: dict[str, PeerTransfer]) -> None:
        """Накопительный учёт трафика.

        Счётчики интерфейса (`awg show dump`) обнуляются при `awg-quick down/up`
        — то есть при ротации маскировки, рестарте контейнера и т.п. Поэтому
        храним накопленный offset + последнее «сырое» значение и при детекте
        сброса (raw < prev) прибавляем прошлую сессию к offset. Так лимиты не
        «обнуляются» после каждого рестарта.
        """
        changed = False
        for record in self._clients.values():
            if record["server_id"] != server_id:
                continue
            pub = record.get("public_key")
            if not pub or pub not in stats:
                continue
            transfer = stats[pub]
            old_used = int(record.get("traffic_used_bytes") or 0)
            raw_up = max(0, int(transfer.rx_bytes))
            raw_down = max(0, int(transfer.tx_bytes))

            prev_raw_up = record.get("traffic_raw_up")
            prev_raw_down = record.get("traffic_raw_down")
            off_up = int(record.get("traffic_offset_up", 0))
            off_down = int(record.get("traffic_offset_down", 0))

            if prev_raw_up is None or prev_raw_down is None:
                # Первое чтение/миграция со старой схемы: сохраняем уже накопленное,
                # не теряя историю и не задваивая текущую сессию.
                off_up = max(0, int(record.get("traffic_up_bytes", 0)) - raw_up)
                off_down = max(0, int(record.get("traffic_down_bytes", 0)) - raw_down)
            else:
                if raw_up < int(prev_raw_up):
                    off_up += int(prev_raw_up)
                if raw_down < int(prev_raw_down):
                    off_down += int(prev_raw_down)

            record["traffic_offset_up"] = off_up
            record["traffic_offset_down"] = off_down
            record["traffic_raw_up"] = raw_up
            record["traffic_raw_down"] = raw_down

            new_up = off_up + raw_up
            new_down = off_down + raw_down
            new_used = new_up + new_down
            record["traffic_up_bytes"] = new_up
            record["traffic_down_bytes"] = new_down
            record["traffic_used_bytes"] = new_used

            if transfer.handshake_unix > 0:
                record["last_handshake_at"] = datetime.fromtimestamp(
                    transfer.handshake_unix, tz=timezone.utc
                ).isoformat()
            elif new_used > old_used:
                record["last_handshake_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
        if changed:
            self._persist()

    def delete(self, client_id: str) -> bool:
        if client_id in self._clients:
            del self._clients[client_id]
            self._persist()
            return True
        return False

    def _effective_status(self, record: dict) -> str:
        base = record.get("status", "active")
        if base == "disabled":
            return "disabled"

        expires_at = record.get("expires_at")
        if expires_at and _is_expired(expires_at):
            return "expired"

        limit = record.get("traffic_limit_bytes")
        used = record.get("traffic_used_bytes", 0)
        if limit and used >= limit:
            return "over_limit"

        return base

    def _common_fields(self, record: dict) -> dict:
        return {
            "id": record["id"],
            "name": record["name"],
            "server_id": record["server_id"],
            "server_name": record.get("server_name"),
            "protocol": record.get("protocol", "awg2"),
            "status": self._effective_status(record),
            "client_ip": record.get("client_ip", "—"),
            "imported": record.get("imported", False),
            "public_key": record.get("public_key"),
            "traffic_used_bytes": record.get("traffic_used_bytes", 0),
            "traffic_up_bytes": record.get("traffic_up_bytes", 0),
            "traffic_down_bytes": record.get("traffic_down_bytes", 0),
            "traffic_limit_bytes": record.get("traffic_limit_bytes"),
            "expires_at": record.get("expires_at"),
            "last_handshake_at": record.get("last_handshake_at"),
            "online": _is_online(record.get("last_handshake_at")),
            "blocked": record.get("blocked_on_server", False),
            "created_at": record.get("created_at"),
            "keepalive": int(record.get("keepalive", 25) or 25),
            "billing_mode": record.get("billing_mode", "free"),
            "billing_amount_kopecks": record.get("billing_amount_kopecks"),
            "billing_period_months": int(record.get("billing_period_months", 1) or 1),
        }

    def _to_list_item(self, record: dict) -> ClientListItem:
        return ClientListItem(**self._common_fields(record))


def _is_expired(expires_at: str) -> bool:
    try:
        parsed = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < datetime.now(timezone.utc)


def _is_online(last_handshake_at: Optional[str]) -> bool:
    if not last_handshake_at:
        return False
    try:
        parsed = datetime.fromisoformat(last_handshake_at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - parsed < ONLINE_WINDOW


client_store = ClientStore()
