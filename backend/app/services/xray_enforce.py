"""Блокировка Xray-клиентов: убираем UUID из server.json при disable/лимите/сроке."""

from __future__ import annotations

import json
import shlex

from app.services.amnezia_ssh import read_container_file, run_container_script
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.services.xray_client import _restart_xray
from app.services.xray_server_config import SERVER_CONFIG_PATH, ensure_monitoring_config
from app.services.xray_install import CONTAINER_NAME
from app.ssh import exec as ssh_exec

BLOCKED_STATUSES = {"expired", "over_limit", "disabled"}


def enforce_xray_server(ssh, server_id: str) -> int:
    record = server_store.get_record(server_id)
    if not record:
        return 0
    if not server_store.has_xray(record):
        return 0

    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
    ).stdout.strip()
    if status != "running":
        return 0

    raw = read_container_file(ssh, CONTAINER_NAME, SERVER_CONFIG_PATH)
    if not raw:
        return 0
    try:
        server_config = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    inbound = (server_config.get("inbounds") or [{}])[0]
    settings = inbound.get("settings") or {}
    clients = list(settings.get("clients") or [])
    by_id = {c.get("id"): c for c in clients if isinstance(c, dict) and c.get("id")}

    xray_ids = {c.id for c in client_store.list_all(server_id) if c.protocol == "xray"}
    view = [item for item in client_store.enforcement_view(server_id) if item["id"] in xray_ids]
    if not view:
        return 0

    changed = False
    pending: list[tuple[str, bool, str | None]] = []

    for item in view:
        uid = item["public_key"]
        in_config = uid in by_id
        if item["should_block"]:
            if in_config:
                saved = json.dumps(by_id.pop(uid), ensure_ascii=False)
                pending.append((item["id"], True, saved))
                changed = True
            elif not item["blocked_on_server"]:
                pending.append((item["id"], True, item.get("peer_block")))
        else:
            if not in_config and item["blocked_on_server"] and item.get("peer_block"):
                try:
                    restored = json.loads(item["peer_block"])
                    if isinstance(restored, dict) and restored.get("id"):
                        by_id[restored["id"]] = restored
                        pending.append((item["id"], False, None))
                        changed = True
                except json.JSONDecodeError:
                    pass
            elif in_config and item["blocked_on_server"]:
                pending.append((item["id"], False, None))

    if changed:
        settings["clients"] = list(by_id.values())
        inbound["settings"] = settings
        server_config["inbounds"][0] = inbound
        ensure_monitoring_config(server_config)
        payload = json.dumps(server_config, ensure_ascii=False, indent=4)
        script = f"cat > {shlex.quote(SERVER_CONFIG_PATH)} <<'EOF'\n{payload}\nEOF"
        result = run_container_script(ssh, CONTAINER_NAME, script, timeout=30)
        if result.exit_code == 0:
            _restart_xray(ssh)

    for client_id, blocked, peer_block in pending:
        client_store.set_blocked(client_id, blocked, peer_block)

    return len(pending)
