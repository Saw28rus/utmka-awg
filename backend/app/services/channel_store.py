"""PA2 инкремент 1 — единая модель каналов (Channel), производный read-слой.

Канал = путь, по которому клиент получает доступ:
- ``direct``  — один узел, один протокол;
- ``cascade`` — entry→exit (AmneziaWG 2.0 и/или 3.1, плюс отдельный Xray-каскад).

Слой ПРОИЗВОДНЫЙ и НЕ мигрирует живые данные: каналы вычисляются из
``server_store`` + ``cascade_store`` + ``client_store`` на лету. Это безопасный
фундамент для Xray-каскада (CX1) и карты серверов (OBS2).

Инвариант обратной совместимости (§3.2): ``channel_id`` клиента вычисляется
детерминированно; старые клиенты автоматически попадают в ``direct``-канал
своего сервера, а клиенты на entry активного каскада — в ``cascade``-канал.
"""

from __future__ import annotations

from typing import Optional

from app.services.cascade_protocol import (
    cascade_channel_id,
    link_protocols,
    protocol_on_cascade,
)
from app.services.cascade_store import cascade_store
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.services.transit_allocator import resolve_profile
from app.services.xray_cascade_store import xray_cascade_store


def _cascade_links() -> dict[str, dict]:
    """Каскадные звенья с заданным exit, ключ = entry_server_id."""
    return {
        link["entry_server_id"]: link
        for link in cascade_store.list_links()
        if link.get("entry_server_id") and link.get("exit_server_id")
    }


def channel_id_for(server_id: str, protocol: str, cascade_entries: Optional[set[str]] = None) -> str:
    """Детерминированный id канала для пары (узел, протокол)."""
    links = _cascade_links()
    link = links.get(server_id)
    if cascade_entries is None:
        cascade_entries = set(links.keys())
    if server_id in cascade_entries and protocol_on_cascade(protocol, link):
        return cascade_channel_id(server_id, protocol)
    return f"direct:{server_id}:{(protocol or 'awg2').lower()}"


def _client_counts(cascade_entries: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in client_store.channel_index():
        if (item.get("protocol") or "").lower() == "xray" and item.get("channel_entry_id"):
            cid = f"xcascade:{item['channel_entry_id']}"
        else:
            cid = channel_id_for(item["server_id"], item["protocol"], cascade_entries)
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def _server_label(server_id: str) -> dict:
    record = server_store.get_record(server_id)
    if not record:
        return {"name": "(удалён)", "host": None, "missing": True}
    return {"name": record.get("name"), "host": record.get("host"), "missing": False}


def list_channels() -> list[dict]:
    """Единый список каналов (direct + cascade) с числом клиентов."""
    links = _cascade_links()
    cascade_entries = set(links.keys())
    counts = _client_counts(cascade_entries)

    channels: list[dict] = []

    for entry_id, link in links.items():
        entry = _server_label(entry_id)
        if entry["missing"]:
            continue  # осиротевшее звено (entry-узел удалён) — не показываем
        exit_id = link["exit_server_id"]
        exit_label = _server_label(exit_id)
        for proto in link_protocols(link):
            cid = cascade_channel_id(entry_id, proto)
            profile = resolve_profile((link.get("legs") or {}).get(proto) or link)
            channels.append(
                {
                    "id": cid,
                    "kind": "cascade",
                    "protocol": proto,
                    "entry_server_id": entry_id,
                    "entry_name": entry["name"],
                    "entry_host": entry["host"],
                    "exit_server_id": exit_id,
                    "exit_name": exit_label["name"],
                    "exit_host": exit_label["host"],
                    "state": link.get("state") or "unknown",
                    "clients": counts.get(cid, 0),
                    "transit_slot": profile.slot,
                    "transit_subnet": profile.subnet,
                    "transit_port": profile.transit_port,
                }
            )

    for link in xray_cascade_store.list_links():
        entry_id = link.get("entry_server_id")
        exit_id = link.get("exit_server_id")
        state = link.get("state") or "none"
        if state == "none" or not entry_id or not exit_id:
            continue
        entry = _server_label(entry_id)
        if entry["missing"]:
            continue
        exit_label = _server_label(exit_id)
        cid = f"xcascade:{entry_id}"
        channels.append(
            {
                "id": cid,
                "kind": "cascade",
                "protocol": "xray",
                "entry_server_id": entry_id,
                "entry_name": entry["name"],
                "entry_host": entry["host"],
                "exit_server_id": exit_id,
                "exit_name": exit_label["name"],
                "exit_host": exit_label["host"],
                "state": state,
                "clients": counts.get(cid, 0),
                "relay_port": link.get("relay_port"),
                "sni": link.get("sni"),
                "split_ru": bool(link.get("split_ru")),
            }
        )

    for record in server_store.list_records():
        sid = record["id"]
        for protocol in server_store.client_protocols(record):
            link = links.get(sid)
            if protocol_on_cascade(protocol, link) and sid in cascade_entries:
                continue
            cid = f"direct:{sid}:{protocol.lower()}"
            channels.append(
                {
                    "id": cid,
                    "kind": "direct",
                    "protocol": protocol.lower(),
                    "server_id": sid,
                    "server_name": record.get("name"),
                    "host": record.get("host"),
                    "state": record.get("status") or "unknown",
                    "clients": counts.get(cid, 0),
                }
            )

    channels.sort(key=lambda c: (c["kind"] != "cascade", (c.get("entry_name") or c.get("server_name") or "").lower()))
    return channels


def get_channel(channel_id: str) -> Optional[dict]:
    for channel in list_channels():
        if channel["id"] == channel_id:
            return channel
    return None


def active_cascades_for_server(server_id: str) -> list[dict]:
    """Активные каскады (AWG + Xray), где узел — entry или exit (fail-closed)."""
    out: list[dict] = []
    for link in list(cascade_store.list_links()) + list(xray_cascade_store.list_links()):
        if (link.get("state") or "") != "active":
            continue
        if not link.get("exit_server_id"):
            continue
        if link.get("entry_server_id") == server_id or link.get("exit_server_id") == server_id:
            out.append(link)
    return out


def describe_blocking_cascades(server_id: str) -> list[str]:
    """Человекочитаемое описание активных каскадов, мешающих удалению узла."""
    notes: list[str] = []
    for link in active_cascades_for_server(server_id):
        entry_id = link.get("entry_server_id") or ""
        exit_id = link.get("exit_server_id") or ""
        entry = _server_label(entry_id)["name"]
        exit_name = _server_label(exit_id)["name"]
        role = "entry" if entry_id == server_id else "exit"
        notes.append(f"{role} активного каскада «{entry} → {exit_name}»")
    return notes
