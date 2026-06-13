"""Уведомления оператора панели (OBS1-2).

Простой персистентный журнал событий для оператора (health-деградация,
авто-перезапуски, недоступность узла). Лежит в panel_data, переживает рестарт.
Не чат с клиентом — это внутренние операторские оповещения.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.services.persistence import read_json, write_json

NOTIFICATIONS_FILE = "notifications.json"
MAX_ITEMS = 200


class NotificationStore:
    def __init__(self) -> None:
        data = read_json(NOTIFICATIONS_FILE, {})
        self._items: list[dict] = data.get("items", [])

    def _persist(self) -> None:
        write_json(NOTIFICATIONS_FILE, {"items": self._items[:MAX_ITEMS]})

    def add(
        self,
        *,
        level: str,
        code: str,
        title: str,
        message: str,
        server_id: Optional[str] = None,
    ) -> dict:
        item = {
            "id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "code": code,
            "title": title,
            "message": message,
            "server_id": server_id,
            "read": False,
        }
        self._items.insert(0, item)
        del self._items[MAX_ITEMS:]
        self._persist()
        return item

    def list(self, limit: int = 50) -> list[dict]:
        return self._items[:limit]

    def unread_count(self) -> int:
        return sum(1 for i in self._items if not i.get("read"))

    def mark_read(self, ids: Optional[list[str]] = None) -> int:
        changed = 0
        target = set(ids) if ids else None
        for i in self._items:
            if i.get("read"):
                continue
            if target is None or i["id"] in target:
                i["read"] = True
                changed += 1
        if changed:
            self._persist()
        return changed

    def forget_server(self, server_id: str) -> int:
        before = len(self._items)
        self._items = [i for i in self._items if i.get("server_id") != server_id]
        removed = before - len(self._items)
        if removed:
            self._persist()
        return removed


notification_store = NotificationStore()
