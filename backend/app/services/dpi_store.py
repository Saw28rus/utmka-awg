"""OBS3 — хранилище трендов DPI-устойчивости по узлам.

Копит лёгкие агрегаты (не сырой трафик): доля клиентов со свежим handshake,
online/active. По ним строятся тренды и подсветка «возможна деградация».
Персистентно (panel_data), переживает рестарт.
"""

from __future__ import annotations

from typing import Optional

from app.services.persistence import read_json, write_json

DPI_FILE = "dpi_stats.json"
MAX_SAMPLES = 288  # ~24ч при шаге 5 минут


class DpiStore:
    def __init__(self) -> None:
        data = read_json(DPI_FILE, {})
        # series: {server_id: [ {ts, online, active, rate}, ... ]}
        self._series: dict[str, list[dict]] = data.get("series", {})
        # state: {server_id: {"level": ok|degraded, "since": iso}}
        self._state: dict[str, dict] = data.get("state", {})

    def _persist(self) -> None:
        write_json(DPI_FILE, {"series": self._series, "state": self._state})

    def append(self, server_id: str, sample: dict) -> None:
        series = self._series.setdefault(server_id, [])
        series.append(sample)
        del series[:-MAX_SAMPLES]
        self._persist()

    def series(self, server_id: str) -> list[dict]:
        return list(self._series.get(server_id, []))

    def all_series(self) -> dict[str, list[dict]]:
        return {sid: list(s) for sid, s in self._series.items()}

    def get_state(self, server_id: str) -> Optional[dict]:
        return self._state.get(server_id)

    def set_state(self, server_id: str, level: str, since: str) -> None:
        self._state[server_id] = {"level": level, "since": since}
        self._persist()

    def forget_server(self, server_id: str) -> None:
        changed = False
        if server_id in self._series:
            del self._series[server_id]
            changed = True
        if server_id in self._state:
            del self._state[server_id]
            changed = True
        if changed:
            self._persist()


dpi_store = DpiStore()
