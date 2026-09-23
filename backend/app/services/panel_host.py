"""Скрытая запись «этот хост = панель», не VPN-сервер.

РУ1 не добавляют в список узлов: на нём нет AWG/каскада. HTTPS и чат живут здесь.
Состояние panel_ssl / chat_domain / harden хранится в той же servers.json,
но запись отфильтрована из UI и из выбора каскада.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.server_store import SshTarget, server_store

PANEL_HOST_ID = "__panel__"
PANEL_HOST_KIND = "panel_host"


def is_panel_host_id(server_id: str | None) -> bool:
    return (server_id or "") == PANEL_HOST_ID


def is_panel_host_record(record: dict | None) -> bool:
    if not record:
        return False
    return record.get("id") == PANEL_HOST_ID or record.get("kind") == PANEL_HOST_KIND


def ensure_panel_host_record() -> dict:
    rec = server_store.get_record(PANEL_HOST_ID)
    if rec:
        changed = False
        if rec.get("kind") != PANEL_HOST_KIND:
            rec["kind"] = PANEL_HOST_KIND
            changed = True
        rec.setdefault("name", "Панель")
        rec.setdefault("ssh_username", "local")
        if rec.get("ssh_port") != 0:
            rec["ssh_port"] = 0
            changed = True
        if changed:
            server_store._persist()
        return rec
    record = {
        "id": PANEL_HOST_ID,
        "name": "Панель",
        "host": "local-panel",
        "ssh_port": 0,
        "ssh_username": "local",
        "ssh_password_enc": None,
        "ssh_key_enc": None,
        "kind": PANEL_HOST_KIND,
        "status": "online",
        "notes": "Хост панели (не VPN-узел)",
        "detect_branch": "panel",
        "awg2_detected": False,
        "awg2_imported": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    server_store._servers[PANEL_HOST_ID] = record
    server_store._persist()
    return record


def panel_host_target() -> SshTarget:
    ensure_panel_host_record()
    return SshTarget(
        host="local-panel",
        port=0,
        username="local",
        password=None,
        key=None,
        local=True,
    )
