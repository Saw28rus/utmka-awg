"""Короткий in-memory кэш метрик серверов (снижает SSH-нагрузку при частых открытиях UI)."""

from __future__ import annotations

import time
from typing import Optional

from app.schemas.servers import ServerMetrics

_DEFAULT_TTL_SEC = 25.0


class MetricsCache:
    def __init__(self, ttl_sec: float = _DEFAULT_TTL_SEC) -> None:
        self._ttl = ttl_sec
        self._entries: dict[str, tuple[float, ServerMetrics]] = {}

    def get(self, server_id: str) -> Optional[ServerMetrics]:
        entry = self._entries.get(server_id)
        if not entry:
            return None
        expires_at, metrics = entry
        if time.monotonic() >= expires_at:
            del self._entries[server_id]
            return None
        return metrics.model_copy(deep=True)

    def set(self, server_id: str, metrics: ServerMetrics) -> None:
        self._entries[server_id] = (time.monotonic() + self._ttl, metrics.model_copy(deep=True))

    def invalidate(self, server_id: Optional[str] = None) -> None:
        if server_id is None:
            self._entries.clear()
            return
        self._entries.pop(server_id, None)


metrics_cache = MetricsCache()
