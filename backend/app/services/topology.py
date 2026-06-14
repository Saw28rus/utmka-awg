"""OBS2 — карта серверов (топология entry→exit) с наложением health.

Производный read-слой поверх ``list_channels()`` + ``health_store``. Ничего не
мигрирует и не меняет: только агрегирует узлы и связи для визуализации.
"""

from __future__ import annotations

from app.services.channel_store import list_channels
from app.services.health_store import health_store
from app.services.server_store import server_store


def _node_health(server_id: str, runtime_status: str | None) -> str:
    h = health_store.get(server_id) or {}
    state = h.get("state")
    if state in ("ok", "degraded", "down"):
        return state
    # health-движок ещё не отрабатывал — берём runtime-статус сервера
    if runtime_status == "online":
        return "ok"
    if runtime_status == "offline":
        return "down"
    return "unknown"


def get_server_map() -> dict:
    """Возвращает {nodes, edges} для карты серверов."""
    channels = list_channels()

    nodes: dict[str, dict] = {}

    def _ensure(server_id: str | None, name: str | None, host: str | None) -> dict | None:
        if not server_id:
            return None
        node = nodes.get(server_id)
        if node:
            return node
        record = server_store.get_record(server_id)
        runtime = (record or {}).get("status")
        protocols = server_store.client_protocols(record) if record else []
        node = {
            "id": server_id,
            "name": name or (record or {}).get("name") or "(удалён)",
            "host": host or (record or {}).get("host"),
            "missing": record is None,
            "protocols": protocols,
            "health": _node_health(server_id, runtime),
            "roles": set(),
            "clients": 0,
        }
        nodes[server_id] = node
        return node

    edges: list[dict] = []

    for ch in channels:
        if ch["kind"] == "cascade":
            entry = _ensure(ch.get("entry_server_id"), ch.get("entry_name"), ch.get("entry_host"))
            exit_node = _ensure(ch.get("exit_server_id"), ch.get("exit_name"), ch.get("exit_host"))
            if entry:
                entry["roles"].add("entry")
                entry["clients"] += int(ch.get("clients") or 0)
            if exit_node:
                exit_node["roles"].add("exit")
            edges.append(
                {
                    "id": ch["id"],
                    "protocol": ch["protocol"],
                    "entry_server_id": ch.get("entry_server_id"),
                    "exit_server_id": ch.get("exit_server_id"),
                    "state": ch.get("state"),
                    "clients": int(ch.get("clients") or 0),
                    "relay_port": ch.get("relay_port"),
                    "transit_port": ch.get("transit_port"),
                    "sni": ch.get("sni"),
                    "split_ru": bool(ch.get("split_ru")),
                }
            )
        else:
            node = _ensure(ch.get("server_id"), ch.get("server_name"), ch.get("host"))
            if node:
                node["roles"].add("direct")
                node["clients"] += int(ch.get("clients") or 0)

    node_list = []
    for node in nodes.values():
        node["roles"] = sorted(node["roles"])
        node_list.append(node)
    node_list.sort(key=lambda n: ((n.get("name") or "").lower()))

    totals = {
        "nodes": len(node_list),
        "cascades": len(edges),
        "clients": sum(n["clients"] for n in node_list),
        "degraded": sum(1 for n in node_list if n["health"] == "degraded"),
        "down": sum(1 for n in node_list if n["health"] == "down"),
    }

    return {"nodes": node_list, "edges": edges, "totals": totals}
