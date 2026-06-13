"""PA2-2 — аллокатор ресурсов транзита каскадов.

Чтобы много каскадов сосуществовали без коллизий (особенно когда несколько
entry уходят на один общий exit), каждому каскаду выдаётся уникальный «слот».
Слот детерминированно задаёт все ресурсы транзита: подсеть /30, IP сторон,
UDP-порт, имя интерфейса, путь конфига, таблицу маршрутизации и приоритет
правила.

ВАЖНО (обратная совместимость, «калашников»): слот 0 даёт РОВНО прежние
значения (10.250.0.0/30, exit .1, entry .2, порт 51821, utmka-cas0, table 7770,
priority 300). Существующий рабочий каскад создавался по этим константам, поэтому
он автоматически = слот 0 и не затрагивается.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Optional

from app.services.cascade_store import cascade_store

BASE_PORT = 51821
BASE_TABLE = 7770
BASE_RULE_PRIORITY = 300
ENTRY_HOST_PORT_OFFSET = 1
TRANSIT_POOL = ipaddress.ip_network("10.250.0.0/16")
MAX_SLOTS = (TRANSIT_POOL.num_addresses // 4)  # число /30-блоков в пуле


@dataclass(frozen=True)
class TransitProfile:
    slot: int
    subnet: str
    exit_ip: str
    entry_ip: str
    transit_port: int
    entry_host_port: int
    iface: str
    conf_path: str
    table: str
    rule_priority: str


def _block(slot: int) -> ipaddress.IPv4Network:
    net_int = int(TRANSIT_POOL.network_address) + slot * 4
    return ipaddress.ip_network((net_int, 30))


def profile_for_slot(slot: int) -> TransitProfile:
    if slot < 0:
        slot = 0
    block = _block(slot)
    hosts = list(block.hosts())  # /30 → [.1, .2]
    exit_ip = str(hosts[0])
    entry_ip = str(hosts[1])
    iface = "utmka-cas0" if slot == 0 else f"utmka-cas{slot}"
    port = BASE_PORT + slot
    return TransitProfile(
        slot=slot,
        subnet=str(block),
        exit_ip=exit_ip,
        entry_ip=entry_ip,
        transit_port=port,
        entry_host_port=port + ENTRY_HOST_PORT_OFFSET,
        iface=iface,
        conf_path=f"/tmp/{iface}.conf",
        table=str(BASE_TABLE + slot),
        rule_priority=str(BASE_RULE_PRIORITY + slot),
    )


def resolve_slot(link: Optional[dict]) -> int:
    """Слот существующего звена: явный transit_slot → из порта → 0 (legacy)."""
    if not link:
        return 0
    if link.get("transit_slot") is not None:
        try:
            return max(0, int(link["transit_slot"]))
        except (TypeError, ValueError):
            return 0
    port = link.get("transit_port")
    if port:
        try:
            derived = int(port) - BASE_PORT
            if derived >= 0:
                return derived
        except (TypeError, ValueError):
            pass
    return 0


def resolve_profile(link: Optional[dict]) -> TransitProfile:
    return profile_for_slot(resolve_slot(link))


def allocate_slot(entry_server_id: str) -> int:
    """Свободный слот для каскада на этом entry.

    Идемпотентно: если звено уже существует и за ним закреплён слот/порт —
    возвращаем его (перенастройка не меняет ресурсы). Иначе берём наименьший
    слот, не занятый другими настроенными звеньями.
    """
    existing = cascade_store.get_link(entry_server_id)
    if existing and (existing.get("transit_slot") is not None or existing.get("transit_port")):
        return resolve_slot(existing)

    used: set[int] = set()
    for link in cascade_store.list_links():
        if link.get("entry_server_id") == entry_server_id:
            continue
        if not link.get("exit_server_id"):
            continue
        if (link.get("state") or "none") == "none":
            continue
        used.add(resolve_slot(link))

    slot = 0
    while slot in used and slot < MAX_SLOTS:
        slot += 1
    return slot
