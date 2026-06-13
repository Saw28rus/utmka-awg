import base64
import json
import zlib
from typing import Optional
from urllib.parse import quote, urlencode

from app.services.awg_config import AWG_PARAM_KEYS

DEFAULT_MTU = "1280"
PROTOCOL_NAME = "awg"
AWG_CONTAINER_KEY = "amnezia-awg"
XRAY_CONTAINER_KEY = "amnezia-xray"
XRAY_PROTOCOL_KEY = "xray"


def detect_protocol_version(awg_params: dict[str, str]) -> Optional[str]:
    """Версия протокола по правилам Amnezia (importController.cpp).

    S3 = cookieReplyPacketJunkSize, S4 = transportPacketJunkSize.
    Наличие S3 и S4 → AWG 2.0; только I1-I5 без S3/S4 → AWG 1.5.
    """
    has_s3 = bool(awg_params.get("S3"))
    has_s4 = bool(awg_params.get("S4"))
    has_special = any(awg_params.get(key) for key in ("I1", "I2", "I3", "I4", "I5"))

    if has_s3 and has_s4:
        return "2"
    if has_special and not (has_s3 and has_s4):
        return "1.5"
    return None


def build_vpn_link(
    *,
    host: str,
    port: int,
    dns: Optional[str],
    client_ip: str,
    client_private_key: str,
    client_public_key: str,
    server_public_key: str,
    preshared_key: Optional[str],
    awg_params: dict[str, str],
    wg_config_ini: str,
    description: str,
    keepalive: int = 25,
) -> str:
    dns1, dns2 = _split_dns(dns)
    protocol_version = detect_protocol_version(awg_params)

    last_config: dict = {
        "config": wg_config_ini,
        "hostName": host,
        "port": int(port),
        "client_priv_key": client_private_key,
        "client_pub_key": client_public_key,
        "client_ip": client_ip,
        "server_pub_key": server_public_key,
        "mtu": DEFAULT_MTU,
        "persistent_keep_alive": str(keepalive),
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "clientId": client_public_key,
    }
    if preshared_key:
        last_config["psk_key"] = preshared_key
    for key in AWG_PARAM_KEYS:
        if key in awg_params:
            last_config[key] = awg_params[key]

    awg_container: dict = {
        "last_config": json.dumps(last_config),
        "isThirdPartyConfig": True,
        "port": str(port),
        "transport_proto": "udp",
    }
    if protocol_version:
        awg_container["protocolVersion"] = protocol_version

    config = {
        "containers": [
            {
                "container": AWG_CONTAINER_KEY,
                PROTOCOL_NAME: awg_container,
            }
        ],
        "defaultContainer": AWG_CONTAINER_KEY,
        "description": description,
        "dns1": dns1,
        "dns2": dns2,
        "hostName": host,
    }
    return _encode(config)


def _encode(config: dict) -> str:
    raw = json.dumps(config, indent=4).encode("utf-8")
    compressed = zlib.compress(raw)
    header = len(raw).to_bytes(4, byteorder="big")
    encoded = base64.urlsafe_b64encode(header + compressed).decode("ascii").rstrip("=")
    return f"vpn://{encoded}"


def build_vless_uri(
    *,
    host: str,
    port: int,
    client_uuid: str,
    flow: str,
    site: str,
    public_key: str,
    short_id: str,
    name: str,
    fingerprint: str = "chrome",
) -> str:
    """VLESS-Reality URI (Amnezia exportController::nativeConfigString / vless::Serialize)."""
    params: dict[str, str] = {
        "security": "reality",
        "sni": site,
        "fp": fingerprint,
        "pbk": public_key,
        "sid": short_id,
    }
    if flow:
        params["flow"] = flow
    query = urlencode(params)
    fragment = quote(name, safe="")
    return f"vless://{client_uuid}@{host}:{port}?{query}#{fragment}"


def build_xray_native_config(
    *,
    host: str,
    port: int,
    client_uuid: str,
    flow: str,
    site: str,
    public_key: str,
    short_id: str,
    fingerprint: str = "chrome",
    split_ru: bool = False,
) -> str:
    """Полный Xray JSON для AmneziaVPN (xrayConfigurator::buildClientProtocolConfig).

    При ``split_ru=True`` добавляется client-side routing: трафик в РФ и приватные
    сети идёт напрямую (freedom), остальное — через прокси (каскад). geoip/geosite
    берутся из бандла AmneziaVPN.
    """
    user: dict = {"id": client_uuid, "encryption": "none"}
    if flow:
        user["flow"] = flow
    proxy_outbound = {
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": port,
                    "users": [user],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "fingerprint": fingerprint,
                "serverName": site,
                "publicKey": public_key,
                "shortId": short_id,
                "spiderX": "",
            },
        },
    }
    payload: dict = {
        "log": {"loglevel": "error"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
            }
        ],
        "outbounds": [proxy_outbound],
    }
    if split_ru:
        proxy_outbound["tag"] = "proxy"
        payload["outbounds"].append({"protocol": "freedom", "tag": "direct"})
        payload["routing"] = {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "outboundTag": "direct", "domain": ["geosite:category-ru"]},
                {"type": "field", "outboundTag": "direct", "ip": ["geoip:ru", "geoip:private"]},
                {"type": "field", "outboundTag": "proxy", "network": "tcp,udp"},
            ],
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_xray_vpn_link(*, host: str, native_config_json: str, description: str) -> str:
    """vpn:// контейнер Amnezia — единственный формат, который импортирует приложение AmneziaVPN."""
    xray_container: dict = {
        "last_config": native_config_json,
        "isThirdPartyConfig": True,
    }
    config = {
        "containers": [
            {
                "container": XRAY_CONTAINER_KEY,
                XRAY_PROTOCOL_KEY: xray_container,
            }
        ],
        "defaultContainer": XRAY_CONTAINER_KEY,
        "description": description,
        "hostName": host,
    }
    return _encode(config)


def _split_dns(dns: Optional[str]) -> tuple[str, str]:
    if not dns:
        return "1.1.1.1", "1.0.0.1"
    parts = [part.strip() for part in dns.replace(";", ",").split(",") if part.strip()]
    if not parts:
        return "1.1.1.1", "1.0.0.1"
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[1]
