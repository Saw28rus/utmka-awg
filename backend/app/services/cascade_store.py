"""JSON-хранилище каскада (MVP). Один cascade link на entry-сервер.

Перед C1-production мигрируем в PostgreSQL (см. AMNEZIA_CASCADE_PLAN.md, DB0).
"""

from __future__ import annotations

from typing import Optional

from app.services.persistence import read_json, write_json

CASCADE_FILE = "cascade.json"

# Резерв подсетей/портов транзита для MVP (link 0).
DEFAULT_TRANSIT_SUBNET = "10.250.0.0/30"
DEFAULT_ENTRY_TRANSIT_IP = "10.250.0.2"
DEFAULT_EXIT_TRANSIT_IP = "10.250.0.1"
DEFAULT_TRANSIT_PORT = 51821

# Дефолты split-routing (РФ напрямую). По умолчанию split ВЫКЛЮЧЕН.
DEFAULT_SPLIT_SOURCES = ["ipdeny_ru", "sapics_ru", "rfc1918"]


def default_split() -> dict:
    return {
        "enabled": False,
        "source_ids": list(DEFAULT_SPLIT_SOURCES),
        "custom_cidrs": [],
        "applied": False,
        "direct_cidr_count": 0,
        "list_updated_at": None,
        "last_error": None,
    }


class CascadeStore:
    def __init__(self) -> None:
        data = read_json(CASCADE_FILE, {})
        self._links: dict[str, dict] = data.get("links", {})

    def _persist(self) -> None:
        write_json(CASCADE_FILE, {"links": self._links})

    def get_link(self, entry_server_id: str) -> Optional[dict]:
        return self._links.get(entry_server_id)

    def list_links(self) -> list[dict]:
        return list(self._links.values())

    def get_split(self, entry_server_id: str) -> dict:
        link = self._links.get(entry_server_id) or {}
        split = dict(default_split())
        split.update(link.get("split") or {})
        return split

    def set_split(self, entry_server_id: str, **fields) -> dict:
        record = self._links.get(entry_server_id) or {"entry_server_id": entry_server_id}
        split = dict(default_split())
        split.update(record.get("split") or {})
        split.update(fields)
        record["split"] = split
        self._links[entry_server_id] = record
        self._persist()
        return split

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


cascade_store = CascadeStore()
