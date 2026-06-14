from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.client_store import client_store
from app.services.server_store import server_store

WEEK = timedelta(days=7)
HANDSHAKE_ONLINE = timedelta(minutes=5)


def get_dashboard_summary() -> dict:
    servers = server_store.list()
    clients = client_store.list_all()

    online_servers = sum(1 for server in servers if server.status == "online")
    active_clients = sum(1 for client in clients if client.status == "active")
    online_clients = sum(1 for client in clients if _client_recently_online(client.last_handshake_at))
    expiring_soon = sum(1 for client in clients if _expires_within_week(client.expires_at))
    total_traffic = sum(client.traffic_used_bytes for client in clients)

    alerts = _build_alerts(clients)

    return {
        "servers": {"online": online_servers, "total": len(servers)},
        "clients": {
            "active": active_clients,
            "online": online_clients,
            "expiring_soon": expiring_soon,
        },
        "traffic_total_bytes": total_traffic,
        "alerts": alerts,
    }


def _client_recently_online(last_handshake_at: Optional[str]) -> bool:
    if not last_handshake_at:
        return False
    try:
        parsed = datetime.fromisoformat(last_handshake_at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - parsed < HANDSHAKE_ONLINE


def _expires_within_week(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return now < parsed <= now + WEEK


def _build_alerts(clients) -> list[dict]:
    alerts: list[dict] = []
    for client in clients:
        if client.status == "expired":
            alerts.append(
                {
                    "level": "warning",
                    "code": "client_expired",
                    "message": f"Клиент «{client.name}» — срок действия истёк.",
                }
            )
        elif client.status == "over_limit":
            alerts.append(
                {
                    "level": "warning",
                    "code": "client_over_limit",
                    "message": f"Клиент «{client.name}» — превышен лимит трафика.",
                }
            )
        elif _expires_within_week(client.expires_at):
            alerts.append(
                {
                    "level": "info",
                    "code": "client_expiring",
                    "message": f"Клиент «{client.name}» — истекает в ближайшие 7 дней.",
                }
            )
    return alerts[:10]
