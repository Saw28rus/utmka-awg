"""Настройки server.json для мониторинга Xray (Stats API + учёт по email)."""

from __future__ import annotations

import json
import shlex
from copy import deepcopy

from app.services.amnezia_ssh import run_container_script
from app.services.xray_install import CONTAINER_NAME

SERVER_CONFIG_PATH = "/opt/amnezia/xray/server.json"
VLESS_INBOUND_TAG = "vless-in"
API_INBOUND_TAG = "api"
STATS_API_PORT = 10085
CLIENT_LEVEL = 0

_API_INBOUND = {
    "listen": "127.0.0.1",
    "port": STATS_API_PORT,
    "protocol": "dokodemo-door",
    "settings": {"address": "127.0.0.1"},
    "tag": API_INBOUND_TAG,
}

_STATS_BLOCK = {
    "stats": {},
    "api": {
        "services": ["StatsService", "LoggerService"],
        "tag": API_INBOUND_TAG,
    },
    "policy": {
        "levels": {
            str(CLIENT_LEVEL): {
                "statsUserUplink": True,
                "statsUserDownlink": True,
            }
        },
        "system": {
            "statsInboundUplink": True,
            "statsInboundDownlink": True,
            "statsOutboundUplink": True,
            "statsOutboundDownlink": True,
        },
    },
    "routing": {
        "rules": [
            {
                "inboundTag": [API_INBOUND_TAG],
                "outboundTag": API_INBOUND_TAG,
                "type": "field",
            }
        ]
    },
}


def make_client_entry(client_uuid: str, flow: str) -> dict:
    entry: dict = {
        "id": client_uuid,
        "email": client_uuid,
        "level": CLIENT_LEVEL,
    }
    if flow:
        entry["flow"] = flow
    return entry


def ensure_monitoring_config(server_config: dict) -> bool:
    """Добавляет Stats API и email/level у клиентов — без этого трафик не считается."""
    changed = False
    config = deepcopy(server_config)
    inbounds = list(config.get("inbounds") or [])
    if not inbounds:
        return False

    vless = dict(inbounds[0])
    if vless.get("tag") != VLESS_INBOUND_TAG:
        vless["tag"] = VLESS_INBOUND_TAG
        changed = True

    settings = dict(vless.get("settings") or {})
    clients = list(settings.get("clients") or [])
    normalized: list[dict] = []
    for raw in clients:
        if not isinstance(raw, dict) or not raw.get("id"):
            normalized.append(raw)
            continue
        entry = dict(raw)
        cid = entry["id"]
        if entry.get("email") != cid:
            entry["email"] = cid
            changed = True
        if entry.get("level") != CLIENT_LEVEL:
            entry["level"] = CLIENT_LEVEL
            changed = True
        normalized.append(entry)
    settings["clients"] = normalized
    vless["settings"] = settings
    inbounds[0] = vless

    has_api = any(isinstance(ib, dict) and ib.get("tag") == API_INBOUND_TAG for ib in inbounds[1:])
    if not has_api:
        inbounds.append(dict(_API_INBOUND))
        changed = True

    config["inbounds"] = inbounds

    # stats/api/policy — как раньше; routing НЕ затираем целиком (иначе ломается
    # Xray-каскад: ensure_monitoring_config вызывается при каждом create_client).
    for key in ("stats", "api", "policy"):
        if config.get(key) != _STATS_BLOCK[key]:
            config[key] = deepcopy(_STATS_BLOCK[key])
            changed = True

    routing = dict(config.get("routing") or {})
    rules = list(routing.get("rules") or [])
    api_rule = dict(_STATS_BLOCK["routing"]["rules"][0])
    has_api = any(
        isinstance(r, dict)
        and r.get("type") == "field"
        and r.get("outboundTag") == API_INBOUND_TAG
        and API_INBOUND_TAG in (r.get("inboundTag") or [])
        for r in rules
    )
    if not has_api:
        rules.insert(0, api_rule)
        changed = True
    routing["rules"] = rules
    if config.get("routing") != routing:
        config["routing"] = routing
        changed = True

    if changed:
        server_config.clear()
        server_config.update(config)
    return changed


def write_server_config(ssh, server_config: dict) -> bool:
    payload = json.dumps(server_config, ensure_ascii=False, indent=4)
    script = f"cat > {shlex.quote(SERVER_CONFIG_PATH)} <<'EOF'\n{payload}\nEOF"
    result = run_container_script(ssh, CONTAINER_NAME, script, timeout=30)
    return result.exit_code == 0
