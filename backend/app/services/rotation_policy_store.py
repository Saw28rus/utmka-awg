"""RES1 — хранилище политик авто-ротации маскировки (per-server).

Off-by-default. Ротация меняет клиентские конфиги, поэтому включается осознанно
на конкретный сервер. Персистентно (panel_data).
"""

from __future__ import annotations

from typing import Optional

from app.services.persistence import read_json, write_json

ROTATION_FILE = "rotation_policy.json"

DEFAULT_POLICY = {
    "enabled": False,
    "preset": "balance",
    "interval_days": 14,
    "window_start": 3,  # час UTC начала окна обслуживания
    "window_end": 6,    # час UTC конца окна
    "trigger_on_dpi": True,
    "last_rotated_at": None,
    "last_status": None,
    "last_error": None,
}


class RotationPolicyStore:
    def __init__(self) -> None:
        data = read_json(ROTATION_FILE, {})
        self._policies: dict[str, dict] = data.get("policies", {})

    def _persist(self) -> None:
        write_json(ROTATION_FILE, {"policies": self._policies})

    def get(self, server_id: str) -> dict:
        policy = dict(DEFAULT_POLICY)
        policy.update(self._policies.get(server_id) or {})
        return policy

    def all(self) -> dict[str, dict]:
        return {sid: self.get(sid) for sid in self._policies}

    def set(self, server_id: str, **fields) -> dict:
        record = self._policies.get(server_id) or dict(DEFAULT_POLICY)
        record.update(fields)
        self._policies[server_id] = record
        self._persist()
        return self.get(server_id)

    def mark_rotated(self, server_id: str, *, when: str, status: str, error: Optional[str] = None) -> None:
        record = self._policies.get(server_id) or dict(DEFAULT_POLICY)
        record["last_rotated_at"] = when
        record["last_status"] = status
        record["last_error"] = error
        self._policies[server_id] = record
        self._persist()

    def forget_server(self, server_id: str) -> None:
        if server_id in self._policies:
            del self._policies[server_id]
            self._persist()


rotation_policy_store = RotationPolicyStore()
