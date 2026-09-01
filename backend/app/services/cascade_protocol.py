"""Протокол AWG-каскада: AmneziaWG 2.0 и/или 3.1.

Транзит Model A живёт в netns контейнера. 2.0 и 3.1 — разные контейнеры,
поэтому каскад для каждой версии поднимается отдельно (своя «нога», свой слот).
Клиенты другой версии на том же входе идут напрямую, если их протокол не выбран.
"""

from __future__ import annotations

from typing import Iterable, Optional

CASCADE_AWG_PROTOCOLS = ("awg2", "awg31")

PROTOCOL_CONTAINERS = {
    "awg2": ("amnezia-awg2", "amnezia-awg"),
    "awg31": ("amnezia-awg31",),
}

PROTOCOL_LABELS = {
    "awg2": "AmneziaWG 2.0",
    "awg31": "AmneziaWG 3.1",
}

CLIENT_HINT_31 = (
    "Ключи AmneziaWG 3.1 открываются только в Amnezia VPN 5.0.1.5 или новее. "
    "Приложение AmneziaWG и клиенты 4.x этот ключ не откроют — для них нужен каскад 2.0."
)


def normalize_protocol(value: Optional[str]) -> str:
    proto = (value or "").lower().strip()
    if proto in ("awg", "awg_legacy"):
        return "awg2"
    return proto


def normalize_cascade_protocols(values: Optional[Iterable[str]]) -> list[str]:
    if not values:
        return []
    chosen: set[str] = set()
    for raw in values:
        proto = normalize_protocol(str(raw))
        if proto in CASCADE_AWG_PROTOCOLS:
            chosen.add(proto)
    return [proto for proto in CASCADE_AWG_PROTOCOLS if proto in chosen]


def link_protocols(link: Optional[dict]) -> list[str]:
    """Какие AWG-версии покрывает звено. Старые записи без поля = только 2.0."""
    if not link:
        return ["awg2"]
    raw = link.get("protocols")
    if isinstance(raw, list) and raw:
        normalized = normalize_cascade_protocols(raw)
        if normalized:
            return normalized
    proto = normalize_protocol(link.get("protocol") or "awg2")
    if proto == "awg31":
        return ["awg31"]
    return ["awg2"]


def protocol_on_cascade(protocol: Optional[str], link: Optional[dict]) -> bool:
    proto = normalize_protocol(protocol)
    if proto not in CASCADE_AWG_PROTOCOLS:
        return False
    return proto in link_protocols(link)


def protocol_label(protocol: Optional[str]) -> str:
    proto = normalize_protocol(protocol)
    return PROTOCOL_LABELS.get(proto, proto or "AmneziaWG")


def containers_for(protocol: Optional[str]) -> tuple[str, ...]:
    proto = normalize_protocol(protocol)
    return PROTOCOL_CONTAINERS.get(proto, PROTOCOL_CONTAINERS["awg2"])


def preferred_container(record: Optional[dict], protocol: Optional[str]) -> Optional[str]:
    names = list((record or {}).get("container_names") or [])
    for name in containers_for(protocol):
        if name in names:
            return name
    return None


def persist_suffix(protocol: Optional[str]) -> str:
    return "-awg31" if normalize_protocol(protocol) == "awg31" else ""


def persist_host_dir(protocol: Optional[str]) -> str:
    if persist_suffix(protocol):
        return "/opt/utmka/cascade/awg31"
    return "/opt/utmka/cascade"


def entry_udp_comment(slot: int) -> str:
    if int(slot or 0) == 0:
        return "utmka-cascade-entry-udp"
    return f"utmka-cascade-entry-udp-{int(slot)}"


def exit_lock_comment(slot: int) -> str:
    if int(slot or 0) == 0:
        return "utmka-exit-lock"
    return f"utmka-exit-lock-{int(slot)}"


def cascade_channel_id(server_id: str, protocol: Optional[str]) -> str:
    proto = normalize_protocol(protocol)
    if proto == "awg31":
        return f"cascade:{server_id}:awg31"
    return f"cascade:{server_id}"


def slot_for_protocol(link: Optional[dict], protocol: Optional[str]) -> Optional[int]:
    """Явный слот ноги протокола, иначе None (нужно аллоцировать)."""
    from app.services.transit_allocator import resolve_slot

    if not link:
        return None
    proto = normalize_protocol(protocol)
    legs = link.get("legs") or {}
    data = legs.get(proto) if isinstance(legs, dict) else None
    if isinstance(data, dict) and (
        data.get("transit_slot") is not None or data.get("transit_port")
    ):
        return resolve_slot(data)
    protocols = link_protocols(link)
    if proto == protocols[0] and (
        link.get("transit_slot") is not None or link.get("transit_port")
    ):
        return resolve_slot(link)
    return None


def all_link_slots(link: Optional[dict]) -> set[int]:
    from app.services.transit_allocator import resolve_slot

    if not link:
        return set()
    slots: set[int] = set()
    legs = link.get("legs") or {}
    if isinstance(legs, dict) and legs:
        for proto, data in legs.items():
            if isinstance(data, dict) and (
                data.get("transit_slot") is not None or data.get("transit_port")
            ):
                slots.add(resolve_slot(data))
            else:
                reserved = slot_for_protocol(link, proto)
                if reserved is not None:
                    slots.add(reserved)
        return slots
    if link.get("exit_server_id") and (
        link.get("transit_slot") is not None or link.get("transit_port")
    ):
        slots.add(resolve_slot(link))
    return slots
