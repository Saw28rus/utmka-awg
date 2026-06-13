"""Хранилище Xray-каскадов (CX1).

Отдельное от AWG-каскада (`cascade_store`), чтобы не трогать живой AWG-движок.
Ключ — entry_server_id (один Xray-каскад на entry на текущем этапе).

Модель проще AWG: entry держит прозрачный TCP-relay на exit, где терминируется
VLESS-Reality. Поэтому здесь не храним ключи/обфускацию — только координаты
relay и снятый с exit Reality-профиль для выдачи клиентов.
"""

from __future__ import annotations

from typing import Optional

from app.services.persistence import read_json, write_json

XRAY_CASCADE_FILE = "xray_cascade.json"
DEFAULT_RELAY_PORT = 443


class XrayCascadeStore:
    def __init__(self) -> None:
        data = read_json(XRAY_CASCADE_FILE, {})
        self._links: dict[str, dict] = data.get("links", {})

    def _persist(self) -> None:
        write_json(XRAY_CASCADE_FILE, {"links": self._links})

    def get_link(self, entry_server_id: str) -> Optional[dict]:
        return self._links.get(entry_server_id)

    def list_links(self) -> list[dict]:
        return list(self._links.values())

    def upsert_link(self, entry_server_id: str, **fields) -> dict:
        record = self._links.get(entry_server_id) or {"entry_server_id": entry_server_id}
        record.update(fields)
        self._links[entry_server_id] = record
        self._persist()
        return record

    def delete_link(self, entry_server_id: str) -> bool:
        if entry_server_id in self._links:
            del self._links[entry_server_id]
            self._persist()
            return True
        return False

    def forget_server(self, server_id: str) -> int:
        removed = 0
        for key, link in list(self._links.items()):
            if link.get("entry_server_id") == server_id or link.get("exit_server_id") == server_id:
                del self._links[key]
                removed += 1
        if removed:
            self._persist()
        return removed


xray_cascade_store = XrayCascadeStore()
