"""Определение страны сервера по его IP (для флага на карточке).

Калашников: best-effort и fail-open — если интернет/провайдер недоступен или host
приватный, просто возвращаем None и ничего не ломаем. Резолвим домен→IP, отсекаем
приватные/зарезервированные адреса, спрашиваем бесплатный гео-провайдер (HTTPS, без
ключа) с фолбэком. Результат — ISO-3166 alpha-2 в нижнем регистре + имя страны.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional

import httpx

logger = logging.getLogger("utmka.geoip")

# Провайдеры без ключа и с HTTPS. Пробуем по очереди до первого успеха.
_PROVIDERS = (
    ("https://ipwho.is/{ip}", "success", "country_code", "country"),
    ("https://ipapi.co/{ip}/json/", None, "country_code", "country_name"),
)

_TIMEOUT = 6.0


def _resolve_ip(host: str) -> Optional[str]:
    """host может быть IP или доменом панели/сервера → возвращаем публичный IP."""
    host = (host or "").strip()
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        ip = host
    except ValueError:
        try:
            ip = socket.gethostbyname(host)
        except (OSError, UnicodeError):
            return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
        return None
    return ip


def lookup_country(host: str) -> Optional[dict]:
    """Страна по host (IP или домен). None — если определить не удалось.

    Возвращает {"country_code": "nl", "country_name": "Netherlands"}.
    """
    ip = _resolve_ip(host)
    if not ip:
        return None

    for url_tpl, ok_field, code_field, name_field in _PROVIDERS:
        try:
            with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": "utmka-panel"}) as client:
                resp = client.get(url_tpl.format(ip=ip))
                resp.raise_for_status()
                data = resp.json()
        except Exception:  # noqa: BLE001 — гео не критично, пробуем следующего
            continue
        if not isinstance(data, dict):
            continue
        if ok_field and not data.get(ok_field):
            continue
        code = (data.get(code_field) or "").strip().lower()
        if len(code) != 2 or not code.isalpha():
            continue
        name = (data.get(name_field) or "").strip() or code.upper()
        return {"country_code": code, "country_name": name}

    return None


def backfill_countries() -> int:
    """Дозаполнить страну у серверов, где её ещё нет (для старых записей).

    Синхронно (вызывать через asyncio.to_thread). Возвращает число обновлённых.
    """
    from app.services.server_store import server_store

    updated = 0
    for record in server_store.list_records():
        if record.get("country_code"):
            continue
        # geo_checked — чтобы не долбить провайдера по серверам без определяемой страны
        if record.get("geo_checked"):
            continue
        sid = record.get("id")
        host = record.get("host")
        if not sid or not host:
            continue
        geo = lookup_country(host)
        if geo:
            server_store.update_runtime(
                sid,
                country_code=geo["country_code"],
                country_name=geo["country_name"],
                geo_checked=True,
            )
            updated += 1
        else:
            server_store.update_runtime(sid, geo_checked=True)
    return updated
