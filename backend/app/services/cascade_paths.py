"""Путь каскада для UI: ключ на входе, интернет через выход."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.cascade_protocol import protocol_on_cascade
from app.services.cascade_store import cascade_store
from app.services.server_store import server_store


def resolve_client_cascade_exit(
    *,
    server_id: str,
    protocol: str,
    awg_link: Optional[dict],
    xray_link: Optional[dict],
    names: dict[str, str],
) -> tuple[Optional[str], bool]:
    """Имя выхода и флаг «каскад живой», если клиент едет через каскад."""
    proto = (protocol or "awg2").lower()
    if proto == "xray":
        if not xray_link or (xray_link.get("state") or "") != "active":
            return None, False
        exit_id = xray_link.get("exit_server_id")
        if not exit_id:
            return None, False
        return names.get(exit_id) or exit_id, True

    if not awg_link or (awg_link.get("state") or "") != "active":
        return None, False
    if not protocol_on_cascade(proto, awg_link):
        return None, False
    exit_id = awg_link.get("exit_server_id")
    if not exit_id:
        return None, False
    return names.get(exit_id) or exit_id, True


def build_awg_cascade_index(links: list[dict], names: dict[str, str]) -> dict[str, dict]:
    """server_id → role/entry/exit для активного AWG-каскада."""
    out: dict[str, dict] = {}
    for link in links:
        if (link.get("state") or "") != "active":
            continue
        entry_id = link.get("entry_server_id")
        exit_id = link.get("exit_server_id")
        if not entry_id or not exit_id:
            continue
        entry_name = names.get(entry_id) or entry_id
        exit_name = names.get(exit_id) or exit_id
        out[entry_id] = {
            "role": "entry",
            "peer_name": exit_name,
            "exit_name": exit_name,
            "entry_name": entry_name,
            "active": True,
        }
        out[exit_id] = {
            "role": "exit",
            "peer_name": entry_name,
            "exit_name": None,
            "entry_name": entry_name,
            "active": True,
        }
    return out


def _server_names() -> dict[str, str]:
    return {rec["id"]: rec.get("name") or rec["id"] for rec in server_store.list_records()}


@dataclass
class CascadePathIndex:
    names: dict[str, str]
    awg_by_entry: dict[str, dict]
    xray_by_entry: dict[str, dict]

    @classmethod
    def load(cls) -> "CascadePathIndex":
        from app.services.xray_cascade_store import xray_cascade_store

        awg: dict[str, dict] = {}
        for link in cascade_store.list_links():
            entry_id = link.get("entry_server_id")
            if entry_id:
                awg[entry_id] = link
        xray: dict[str, dict] = {}
        for link in xray_cascade_store.list_links():
            entry_id = link.get("entry_server_id")
            if entry_id:
                xray[entry_id] = link
        return cls(names=_server_names(), awg_by_entry=awg, xray_by_entry=xray)

    def client_fields(self, server_id: str, protocol: str) -> tuple[Optional[str], bool]:
        proto = (protocol or "awg2").lower()
        return resolve_client_cascade_exit(
            server_id=server_id,
            protocol=proto,
            awg_link=self.awg_by_entry.get(server_id),
            xray_link=self.xray_by_entry.get(server_id) if proto == "xray" else None,
            names=self.names,
        )


def client_cascade_fields(server_id: str, protocol: str) -> tuple[Optional[str], bool]:
    return CascadePathIndex.load().client_fields(server_id, protocol)


def awg_cascade_index() -> dict[str, dict]:
    return build_awg_cascade_index(cascade_store.list_links(), _server_names())


def apply_awg_cascade_to_servers(items) -> None:
    index = awg_cascade_index()
    for item in items:
        info = index.get(item.id)
        if not info:
            item.awg_cascade_active = False
            item.awg_cascade_role = None
            item.awg_cascade_exit_name = None
            item.awg_cascade_peer_name = None
            continue
        item.awg_cascade_active = True
        item.awg_cascade_role = info["role"]
        item.awg_cascade_exit_name = info.get("exit_name")
        item.awg_cascade_peer_name = info.get("peer_name")
