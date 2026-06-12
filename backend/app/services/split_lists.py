"""Источники и сборка списков «РФ напрямую» для split-routing каскада.

Идея: split по IP назначения. Российские сети (GeoIP) + локальные RFC1918 +
свои CIDR попадают в ipset `utmka_direct`. Такой трафик клиента идёт напрямую
через entry (Россия), остальное — через каскад на exit.

Источники (выбраны как самые стабильные и готовые для ipset):
- IPdeny RU aggregated — страновой GeoIP РФ, готовые CIDR (Яндекс/VK/итд внутри).
- sapics ip-location-db RU — второй GeoIP (диапазоны start-end → CIDR) для полноты.
- RFC1918 — приватные сети, всегда direct.

Списки скачиваются и парсятся на стороне панели (полный контроль над разбором),
кешируются, агрегируются (collapse) и заливаются на entry готовым ipset-файлом.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

import httpx

from app.services.persistence import read_json, write_json

CACHE_FILE = "split_lists_cache.json"
CACHE_TTL_SECONDS = 24 * 3600  # сутки

# RFC1918 + CGNAT — всегда локально/direct.
RFC1918_CIDRS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
)


@dataclass
class SplitSource:
    id: str
    label: str
    description: str
    url: Optional[str] = None
    parser: str = "cidr"  # cidr | sapics_csv | static_rfc1918
    static_cidrs: tuple[str, ...] = field(default_factory=tuple)
    default_enabled: bool = True


SOURCES: dict[str, SplitSource] = {
    "ipdeny_ru": SplitSource(
        id="ipdeny_ru",
        label="Основная база России",
        description="Главный список российских адресов в интернете.",
        url="https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone",
        parser="cidr",
        default_enabled=True,
    ),
    "sapics_ru": SplitSource(
        id="sapics_ru",
        label="Дополнительная база",
        description="Расширяет список — меньше пропусков.",
        url="https://raw.githubusercontent.com/sapics/ip-location-db/main/geo-whois-asn-country/geo-whois-asn-country-ipv4.csv",
        parser="sapics_csv",
        default_enabled=True,
    ),
    "rfc1918": SplitSource(
        id="rfc1918",
        label="Локальные сети",
        description="Домашние и служебные адреса.",
        parser="static_rfc1918",
        static_cidrs=RFC1918_CIDRS,
        default_enabled=True,
    ),
}

DEFAULT_SOURCE_IDS = [s.id for s in SOURCES.values() if s.default_enabled]


class SplitListError(Exception):
    pass


# ---------------------------------------------------------------------------
# Парсеры
# ---------------------------------------------------------------------------


def _parse_cidr_lines(text: str) -> list[ipaddress.IPv4Network]:
    nets: list[ipaddress.IPv4Network] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            nets.append(ipaddress.ip_network(line, strict=False))  # type: ignore[arg-type]
        except ValueError:
            continue
    return [n for n in nets if isinstance(n, ipaddress.IPv4Network)]


def _parse_sapics_csv(text: str) -> list[ipaddress.IPv4Network]:
    """Формат строки: start_ip,end_ip,COUNTRY. Берём только RU, диапазон → CIDR."""
    nets: list[ipaddress.IPv4Network] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.endswith(",RU"):
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            start = ipaddress.ip_address(parts[0])
            end = ipaddress.ip_address(parts[1])
            if start.version != 4 or end.version != 4:
                continue
            for net in ipaddress.summarize_address_range(start, end):  # type: ignore[arg-type]
                if isinstance(net, ipaddress.IPv4Network):
                    nets.append(net)
        except ValueError:
            continue
    return nets


# ---------------------------------------------------------------------------
# Кеш скачанного
# ---------------------------------------------------------------------------


def _load_cache() -> dict:
    return read_json(CACHE_FILE, {}) or {}


def _save_cache(cache: dict) -> None:
    write_json(CACHE_FILE, cache)


def _fetch_text(url: str, *, timeout: int = 40) -> str:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "utmka-cascade/1.0"})
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as exc:
        raise SplitListError(f"Не удалось скачать {url}: {exc}") from exc


def _source_networks(
    source: SplitSource, *, force_refresh: bool, cache: dict
) -> tuple[list[ipaddress.IPv4Network], Optional[str]]:
    """Возвращает (сети, fetched_at_iso). Использует кеш при свежести."""
    if source.parser == "static_rfc1918":
        return _parse_cidr_lines("\n".join(source.static_cidrs)), None

    if not source.url:
        return [], None

    entry = cache.get(source.id) or {}
    now = time.time()
    fresh = (
        not force_refresh
        and entry.get("raw")
        and (now - float(entry.get("ts", 0))) < CACHE_TTL_SECONDS
    )
    if fresh:
        raw = entry["raw"]
    else:
        raw = _fetch_text(source.url)
        cache[source.id] = {"raw": raw, "ts": now}

    if source.parser == "sapics_csv":
        return _parse_sapics_csv(raw), _iso(cache[source.id]["ts"] if not fresh else entry.get("ts", now))
    return _parse_cidr_lines(raw), _iso(cache[source.id]["ts"] if not fresh else entry.get("ts", now))


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Публичное API сборки
# ---------------------------------------------------------------------------


@dataclass
class SplitBuildResult:
    cidrs: list[str]
    total_count: int
    per_source: dict[str, int]
    custom_count: int
    fetched_at: dict[str, Optional[str]]


def _custom_networks(custom_cidrs: Iterable[str]) -> tuple[list[ipaddress.IPv4Network], list[str]]:
    nets: list[ipaddress.IPv4Network] = []
    invalid: list[str] = []
    for raw in custom_cidrs:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            net = ipaddress.ip_network(raw, strict=False)
            if isinstance(net, ipaddress.IPv4Network):
                nets.append(net)
            else:
                invalid.append(raw)
        except ValueError:
            invalid.append(raw)
    return nets, invalid


def validate_custom_cidrs(custom_cidrs: Iterable[str]) -> list[str]:
    """Возвращает список нераспознанных CIDR (для подсветки в UI)."""
    _, invalid = _custom_networks(custom_cidrs)
    return invalid


def build_direct_cidrs(
    source_ids: Iterable[str],
    custom_cidrs: Iterable[str] | None = None,
    *,
    extra_cidrs: Iterable[str] | None = None,
    force_refresh: bool = False,
) -> SplitBuildResult:
    """Скачивает/парсит выбранные источники, агрегирует в минимальный набор CIDR."""
    cache = _load_cache()
    per_source: dict[str, int] = {}
    fetched_at: dict[str, Optional[str]] = {}
    all_nets: list[ipaddress.IPv4Network] = []

    for sid in source_ids:
        source = SOURCES.get(sid)
        if not source:
            continue
        nets, fetched = _source_networks(source, force_refresh=force_refresh, cache=cache)
        per_source[sid] = len(nets)
        fetched_at[sid] = fetched
        all_nets.extend(nets)

    custom_nets, _invalid = _custom_networks(custom_cidrs or [])
    all_nets.extend(custom_nets)

    if extra_cidrs:
        extra_nets, _ = _custom_networks(extra_cidrs)
        all_nets.extend(extra_nets)

    _save_cache(cache)

    collapsed = list(ipaddress.collapse_addresses(all_nets))
    cidrs = [str(net) for net in collapsed]

    return SplitBuildResult(
        cidrs=cidrs,
        total_count=len(cidrs),
        per_source=per_source,
        custom_count=len(custom_nets),
        fetched_at=fetched_at,
    )


def source_catalog() -> list[dict]:
    """Описание источников для UI."""
    return [
        {
            "id": s.id,
            "label": s.label,
            "description": s.description,
            "default_enabled": s.default_enabled,
            "kind": s.parser,
        }
        for s in SOURCES.values()
    ]
