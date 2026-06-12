"""Фоновый планировщик: поллинг статусов счетов ЮKassa и авто-продление клиентов."""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.session import AsyncSessionLocal
from app.services.invoice_service import InvoiceService

logger = logging.getLogger("utmka.scheduler")

INVOICE_POLL_SECONDS = 120
CHAT_RETENTION_SECONDS = 24 * 3600


async def _chat_retention() -> None:
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

_scheduler: Optional[AsyncIOScheduler] = None


async def _poll_invoices() -> None:
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
