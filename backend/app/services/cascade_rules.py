"""Управление split-правилами каскада («РФ напрямую, остальное через exit»).

Тонкий слой поверх:
- split_lists  — сборка direct-CIDR из источников РФ GeoIP + RFC1918 + свои;
- cascade_split — data-plane (ipset/mangle/fwmark) в netns контейнера entry;
- cascade_store — хранение настроек split в link.

Split применяется только при активном каскаде (зависит от таблицы каскада для
остального трафика). Настройки можно менять и при выключенном каскаде — они
сохранятся и применятся при следующем apply.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.schemas.cascade import (
    CascadeRulesApplyResult,
    CascadeRulesStatus,
    CascadeStep,
    SplitSourceInfo,
)
from app.services import cascade_split
from app.services.cascade import CascadeError, _connect
from app.services.cascade_apply import _amnezia_container
from app.services.cascade_store import cascade_store
from app.services.server_store import server_store
from app.services.split_lists import (
    SOURCES,
    SplitListError,
    build_direct_cidrs,
    source_catalog,
    validate_custom_cidrs,
)


def _entry_container(entry_id: str) -> str:
    rec = server_store.get_record(entry_id)
    ctn = _amnezia_container(rec) if rec else None
    return ctn or "amnezia-awg2"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_rules_status(entry_id: str) -> CascadeRulesStatus:
    link = cascade_store.get_link(entry_id) or {}
    split = cascade_store.get_split(entry_id)
    entry_rec = server_store.get_record(entry_id)
    exit_id = link.get("exit_server_id")
    exit_rec = server_store.get_record(exit_id) if exit_id else None
    cascade_active = (link.get("state") == "active")

    health = None
    if cascade_active and split.get("applied"):
        try:
            ssh = _connect(entry_id)
            try:
                pid = cascade_split.netns_pid(ssh, _entry_container(entry_id))
                if pid:
                    health = cascade_split.split_health(ssh, pid)
            finally:
                ssh.close()
        except Exception:  # noqa: BLE001
            health = None

    return CascadeRulesStatus(
        entry_server_id=entry_id,
        cascade_active=cascade_active,
        enabled=bool(split.get("enabled")),
        applied=bool(split.get("applied")),
        source_ids=list(split.get("source_ids") or []),
        custom_cidrs=list(split.get("custom_cidrs") or []),
        direct_cidr_count=int(split.get("direct_cidr_count") or 0),
        list_updated_at=split.get("list_updated_at"),
        sources=[SplitSourceInfo(**s) for s in source_catalog()],
        entry_name=entry_rec.get("name") if entry_rec else None,
        exit_name=exit_rec.get("name") if exit_rec else None,
        entry_public_ip=(entry_rec or {}).get("host"),
        exit_public_ip=(exit_rec or {}).get("host"),
        health=health,
        last_error=split.get("last_error"),
        message=_status_message(cascade_active, split),
    )


def _status_message(cascade_active: bool, split: dict) -> str:
    if not cascade_active:
        return "Сначала включите каскад — правило применится автоматически."
    if split.get("enabled") and split.get("applied"):
        return "Правило работает."
    if split.get("enabled") and not split.get("applied"):
        return "Включено — нажмите «Сохранить», чтобы применить."
    return "Выключено — весь интернет идёт через выходной сервер."


def _valid_source_ids(source_ids: Optional[list[str]]) -> list[str]:
    if source_ids is None:
        return []
    return [s for s in source_ids if s in SOURCES]


def update_rules(
    entry_id: str,
    *,
    enabled: bool,
    source_ids: Optional[list[str]] = None,
    custom_cidrs: Optional[list[str]] = None,
    force_refresh: bool = False,
) -> CascadeRulesApplyResult:
    link = cascade_store.get_link(entry_id)
    if not link:
        raise CascadeError("Каскад для этого сервера не настроен.")

    current = cascade_store.get_split(entry_id)
    new_sources = _valid_source_ids(source_ids) if source_ids is not None else list(current.get("source_ids") or [])
    if enabled and not new_sources:
        new_sources = list(current.get("source_ids") or [])
    new_custom = custom_cidrs if custom_cidrs is not None else list(current.get("custom_cidrs") or [])
    invalid = validate_custom_cidrs(new_custom)

    steps: list[CascadeStep] = []
    cascade_active = (link.get("state") == "active")
    client_subnet = link.get("client_subnet") or "10.8.1.0/24"

    # endpoints всегда direct (управление/транзит не должны зацикливаться)
    extra: list[str] = []
    exit_id = link.get("exit_server_id")
    exit_rec = server_store.get_record(exit_id) if exit_id else None
    entry_rec = server_store.get_record(entry_id)
    for rec in (exit_rec, entry_rec):
        host = (rec or {}).get("host")
        if host and host.count(".") == 3:
            extra.append(f"{host}/32")

    # --- выключение ---
    if not enabled:
        if cascade_active and current.get("applied"):
            ssh = _connect(entry_id)
            try:
                pid = cascade_split.netns_pid(ssh, _entry_container(entry_id))
                if pid:
                    cascade_split.teardown_split(ssh, pid, client_subnet)
                    steps.append(CascadeStep(name="Отключение правила", status="ok"))
            finally:
                ssh.close()
        split = cascade_store.set_split(
            entry_id, enabled=False, applied=False,
            source_ids=new_sources, custom_cidrs=new_custom, last_error=None,
        )
        return CascadeRulesApplyResult(
            ok=True, enabled=False, applied=False,
            direct_cidr_count=int(split.get("direct_cidr_count") or 0),
            steps=steps, invalid_cidrs=invalid,
            message="Правило выключено. Весь интернет снова через выходной сервер.",
        )

    # --- включение ---
    # настройки сохраняем сразу (даже если каскад не активен)
    cascade_store.set_split(
        entry_id, enabled=True, source_ids=new_sources, custom_cidrs=new_custom,
    )
    if not cascade_active:
        return CascadeRulesApplyResult(
            ok=True, enabled=True, applied=False,
            direct_cidr_count=int(current.get("direct_cidr_count") or 0),
            steps=steps, invalid_cidrs=invalid,
            message="Сохранено. Применится, когда включите каскад.",
        )

    # каскад активен → собрать списки и применить
    try:
        build = build_direct_cidrs(
            new_sources, new_custom, extra_cidrs=extra, force_refresh=force_refresh
        )
        steps.append(CascadeStep(
            name="Подготовка списка России", status="ok",
            detail=f"{build.total_count} адресов",
        ))
    except SplitListError as exc:
        cascade_store.set_split(entry_id, last_error=str(exc))
        raise CascadeError(f"Не удалось собрать списки: {exc}")

    ssh = _connect(entry_id)
    try:
        pid = cascade_split.netns_pid(ssh, _entry_container(entry_id))
        if not pid:
            raise CascadeError("Не найден netns контейнера entry для split.")
        count = cascade_split.apply_split(ssh, pid, client_subnet, build.cidrs)
        steps.append(CascadeStep(
            name="Применение на сервере", status="ok",
            detail=f"{count} адресов в правиле",
        ))
        health = cascade_split.split_health(ssh, pid)
        steps.append(CascadeStep(
            name="Проверка",
            status="ok" if health.get("ok") else "failed",
            detail=_health_detail(health),
        ))
    except cascade_split.SplitError as exc:
        cascade_store.set_split(entry_id, last_error=str(exc))
        raise CascadeError(f"Split не применён: {exc}")
    finally:
        ssh.close()

    split = cascade_store.set_split(
        entry_id,
        enabled=True,
        applied=True,
        source_ids=new_sources,
        custom_cidrs=new_custom,
        direct_cidr_count=build.total_count,
        list_updated_at=_now(),
        last_error=None,
    )
    return CascadeRulesApplyResult(
        ok=True, enabled=True, applied=True,
        direct_cidr_count=build.total_count,
        steps=steps, health=health, invalid_cidrs=invalid,
        message="Правило применено: Россия — напрямую, зарубеж — через выходной сервер.",
    )


def refresh_lists(entry_id: str) -> CascadeRulesApplyResult:
    """Принудительно обновить источники и переприменить (если split активен)."""
    split = cascade_store.get_split(entry_id)
    return update_rules(
        entry_id,
        enabled=bool(split.get("enabled")),
        source_ids=list(split.get("source_ids") or []),
        custom_cidrs=list(split.get("custom_cidrs") or []),
        force_refresh=True,
    )


def _health_detail(health: dict) -> str:
    if health.get("ok"):
        return "Российские и зарубежные сайты маршрутизируются правильно."
    parts = []
    if not health.get("ru_in_set"):
        parts.append("российские адреса не распознаны")
    if not health.get("foreign_excluded"):
        parts.append("зарубеж попал в российский маршрут")
    if not health.get("rule_present"):
        parts.append("настройка на сервере не найдена")
    return "; ".join(parts) or "проверка не прошла"


# ---------------------------------------------------------------------------
# Интеграция с apply/rollback каскада
# ---------------------------------------------------------------------------


def apply_split_after_cascade(entry_id: str) -> Optional[CascadeStep]:
    """Вызывается из apply_cascade после успешного поднятия, если split включён."""
    split = cascade_store.get_split(entry_id)
    if not split.get("enabled"):
        return None
    link = cascade_store.get_link(entry_id) or {}
    client_subnet = link.get("client_subnet") or "10.8.1.0/24"
    exit_id = link.get("exit_server_id")
    extra: list[str] = []
    for rec in (server_store.get_record(exit_id) if exit_id else None, server_store.get_record(entry_id)):
        host = (rec or {}).get("host")
        if host and host.count(".") == 3:
            extra.append(f"{host}/32")
    try:
        build = build_direct_cidrs(
            list(split.get("source_ids") or []), list(split.get("custom_cidrs") or []),
            extra_cidrs=extra,
        )
        ssh = _connect(entry_id)
        try:
            pid = cascade_split.netns_pid(ssh, _entry_container(entry_id))
            if not pid:
                raise cascade_split.SplitError("нет netns контейнера")
            count = cascade_split.apply_split(ssh, pid, client_subnet, build.cidrs)
        finally:
            ssh.close()
        cascade_store.set_split(
            entry_id, applied=True, direct_cidr_count=count,
            list_updated_at=_now(), last_error=None,
        )
        return CascadeStep(
            name="Разделение трафика", status="ok",
            detail=f"{count} CIDR в direct-наборе",
        )
    except Exception as exc:  # noqa: BLE001
        cascade_store.set_split(entry_id, applied=False, last_error=str(exc))
        return CascadeStep(
            name="Разделение трафика", status="failed",
            detail=f"Каскад поднят, но split не применён: {exc}",
        )


def mark_split_down(entry_id: str) -> None:
    """Вызывается из teardown каскада — split-слой уходит вместе с netns-правилами."""
    cascade_store.set_split(entry_id, applied=False)
