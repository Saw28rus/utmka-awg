"""Фоновый планировщик: поллинг статусов счетов ЮKassa и авто-продление клиентов."""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.session import AsyncSessionLocal
from app.services.invoice_service import InvoiceService

logger = logging.getLogger("utmka.scheduler")


def _suspended() -> bool:
    """В режиме standby (идёт миграция узла) фоновые джобы не должны действовать."""
    try:
        from app.services.panel_role import is_standby

        return is_standby()
    except Exception:  # noqa: BLE001
        return False


INVOICE_POLL_SECONDS = 120
CHAT_RETENTION_SECONDS = 24 * 3600
XRAY_CASCADE_RECONCILE_SECONDS = 90
# Самолечение AWG-каскада: правила каскада (policy-routing, транзит в netns)
# не персистентны и слетают при перезагрузке entry. Фоновый проход переподнимает.
CASCADE_RECONCILE_SECONDS = 90
HEALTH_CHECK_SECONDS = 120
DPI_SAMPLE_SECONDS = 300
ROTATION_CHECK_SECONDS = 3600
# Авто-снапшот AWG2-входов: чтобы к моменту аварии был свежий слепок ключей
# для «Замены Входа» (RES2a). Read-only docker exec tar, сервису не вредит.
ENTRY_SNAPSHOT_SECONDS = 12 * 3600


async def _health_check() -> None:
    if _suspended():
        return
    try:
        import asyncio

        from app.services.health import run_health_check_all

        result = await asyncio.to_thread(run_health_check_all, auto_restart=True)
        if result.get("degraded") or result.get("down") or result.get("restarted"):
            logger.info(
                "health: checked=%s degraded=%s down=%s restarted=%s",
                result.get("checked"), result.get("degraded"),
                result.get("down"), result.get("restarted"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка health-проверки узлов")


async def _sample_dpi() -> None:
    if _suspended():
        return
    try:
        import asyncio

        from app.services.dpi_stats import sample_all

        result = await asyncio.to_thread(sample_all)
        if result.get("degraded"):
            logger.info("dpi: checked=%s degraded=%s", result.get("checked"), result.get("degraded"))
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка сбора DPI-трендов")


async def _run_rotations() -> None:
    if _suspended():
        return
    try:
        import asyncio

        from app.services.rotation_runner import run_due_rotations

        result = await asyncio.to_thread(run_due_rotations)
        if result.get("rotated") or result.get("failed"):
            logger.info(
                "rotation: checked=%s rotated=%s failed=%s",
                result.get("checked"), result.get("rotated"), result.get("failed"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка авто-ротации маскировки")


async def _reconcile_xray_cascades() -> None:
    if _suspended():
        return
    try:
        import asyncio

        from app.services.xray_cascade import reconcile_all_xray_cascades

        result = await asyncio.to_thread(reconcile_all_xray_cascades)
        if result.get("healed"):
            logger.info(
                "xray-cascade reconcile: checked=%s healed=%s",
                result.get("checked"),
                result.get("healed"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка reconcile Xray-каскадов")


async def _reconcile_cascades() -> None:
    if _suspended():
        return
    try:
        import asyncio

        from app.services.cascade import reconcile_all_cascades

        result = await asyncio.to_thread(reconcile_all_cascades)
        if result.get("healed") or result.get("failed"):
            logger.info(
                "cascade reconcile: checked=%s healed=%s failed=%s",
                result.get("checked"),
                result.get("healed"),
                result.get("failed"),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка reconcile AWG-каскадов")


async def _chat_retention() -> None:
    if _suspended():
        return
    try:
        from app.services.chat_service import ChatService
        from app.services.panel_settings_service import PanelSettingsService

        async with AsyncSessionLocal() as session:
            settings_svc = PanelSettingsService(session)
            try:
                days = int(await settings_svc.get("chat_retention_days") or 90)
            except ValueError:
                days = 90
            removed = await ChatService(session).purge_old_messages(days)
            if removed:
                logger.info("chat retention: removed %s messages older than %s days", removed, days)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка retention чата")

async def _snapshot_entries() -> None:
    """Снять свежий слепок AWG2-входов каскадов (для disaster-recovery входа)."""
    if _suspended():
        return
    try:
        import asyncio

        from app.services.cascade_store import cascade_store
        from app.services.protocol_backup import snapshot_protocol
        from app.services.server_store import server_store

        entry_ids = {
            link.get("entry_server_id")
            for link in cascade_store.list_links()
            if link.get("entry_server_id")
        }
        done = 0
        for sid in entry_ids:
            rec = server_store.get_record(sid)
            if not rec:
                continue
            has_awg2 = rec.get("awg2_imported") or (rec.get("installed_protocols") or {}).get("awg2")
            if not has_awg2:
                continue
            try:
                await asyncio.to_thread(snapshot_protocol, sid, "awg2")
                done += 1
            except Exception:  # noqa: BLE001
                # узел недоступен — не критично, попробуем в следующий раз
                continue
        if done:
            logger.info("entry snapshot: captured %s entry node(s)", done)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка авто-снапшота входов")


_scheduler: Optional[AsyncIOScheduler] = None


async def _poll_invoices() -> None:
    if _suspended():
        return
    try:
        async with AsyncSessionLocal() as session:
            svc = InvoiceService(session)
            result = await svc.sync_pending()
            if result.get("updated"):
                logger.info(
                    "yookassa poll: checked=%s updated=%s paid=%s",
                    result.get("checked"),
                    result.get("updated"),
                    result.get("paid"),
                )
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка при поллинге счетов ЮKassa")


def start_scheduler() -> Optional[AsyncIOScheduler]:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    # Anti-split-brain: в режиме ожидания (идёт миграция узла) планировщик не
    # трогает узлы и не плодит фоновых действий — этим занимается активная панель.
    try:
        from app.services.panel_role import is_standby

        if is_standby():
            logger.info("Scheduler НЕ запущен: панель в режиме standby (миграция узла)")
            return None
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось определить роль панели — стартую планировщик по умолчанию")

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _poll_invoices,
        "interval",
        seconds=INVOICE_POLL_SECONDS,
        id="yookassa_invoice_poll",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _chat_retention,
        "interval",
        seconds=CHAT_RETENTION_SECONDS,
        id="chat_retention",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _reconcile_xray_cascades,
        "interval",
        seconds=XRAY_CASCADE_RECONCILE_SECONDS,
        id="xray_cascade_reconcile",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _reconcile_cascades,
        "interval",
        seconds=CASCADE_RECONCILE_SECONDS,
        id="cascade_reconcile",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _health_check,
        "interval",
        seconds=HEALTH_CHECK_SECONDS,
        id="node_health_check",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _sample_dpi,
        "interval",
        seconds=DPI_SAMPLE_SECONDS,
        id="dpi_sample",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_rotations,
        "interval",
        seconds=ROTATION_CHECK_SECONDS,
        id="masking_rotation",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _snapshot_entries,
        "interval",
        seconds=ENTRY_SNAPSHOT_SECONDS,
        id="entry_snapshot",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Scheduler started (invoice poll every %ss)", INVOICE_POLL_SECONDS)
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def create_scheduler():
    """Совместимость: возвращает активный планировщик."""
    return _scheduler
