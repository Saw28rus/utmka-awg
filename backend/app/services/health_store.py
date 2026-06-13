"""Хранилище состояния здоровья узлов (OBS1).

Производный, восстановимый слой: периодически обновляется health-движком.
Лежит в panel_data, переживает рестарт панели. Секреты не хранит.
"""

from __future__ import annotations

from typing import Optional

from app.services.persistence import read_json, write_json

HEALTH_FILE = "health.json"


class HealthStore:
    def __init__(self) -> None:
        data = read_json(HEALTH_FILE, {})
        self._nodes: dict[str, dict] = data.get("nodes", {})

    def _persist(self) -> None:
        write_json(HEALTH_FILE, {"nodes": self._nodes})

    def get(self, server_id: str) -> Optional[dict]:
        return self._nodes.get(server_id)

    def all(self) -> dict[str, dict]:
        return dict(self._nodes)

    def upsert(self, server_id: str, **fields) -> dict:
        record = self._nodes.get(server_id) or {"server_id": server_id}
        record.update(fields)
        self._nodes[server_id] = record
        self._persist()
        return record

    def forget(self, server_id: str) -> bool:
        if server_id in self._nodes:
            del self._nodes[server_id]
            self._persist()
            return True
        return False


health_store = HealthStore()
