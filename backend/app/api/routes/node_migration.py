"""API «Полной миграции узла» (NODE_MIGRATION_PLAN.md). Только admin.

Пути под префиксом /node-migration. Singleton: одна миграция на панель.
Тяжёлые шаги (provision/activate) запускаются в фоне, UI опрашивает /status.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_admin
from app.schemas.auth import CurrentUser
from app.schemas.node_migration import (
    MigrateActivateRequest,
    MigratePreflightRequest,
    MigrationStatus,
)
from app.services.node_migration import (
    NodeMigrationError,
    abort,
    activate,
    check_dns,
    preflight,
    provision,
)
from app.services.node_migration_store import node_migration_store as NM

router = APIRouter()
logger = logging.getLogger("utmka.node_migration")


@router.get("/status", response_model=Optional[MigrationStatus])
async def migration_status(_: CurrentUser = Depends(require_admin)) -> Optional[MigrationStatus]:
    rec = await asyncio.to_thread(NM.get_public)
    return MigrationStatus(**rec) if rec else None


@router.post("/preflight", response_model=MigrationStatus)
async def migration_preflight(
    payload: MigratePreflightRequest,
    _: CurrentUser = Depends(require_admin),
) -> MigrationStatus:
    try:
        result = await asyncio.to_thread(
            preflight,
            source_server_id=payload.source_server_id,
            new_host=payload.new_host,
            ssh_port=payload.ssh_port,
            ssh_username=payload.ssh_username,
            ssh_password=payload.ssh_password,
            ssh_key=payload.ssh_key,
            expected_domain=payload.expected_domain,
        )
    except NodeMigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return MigrationStatus(**result)


@router.post("/provision", response_model=MigrationStatus)
async def migration_provision(_: CurrentUser = Depends(require_admin)) -> MigrationStatus:
    # Тяжёлый шаг (установка стека + перенос данных) — в фон; UI опрашивает /status.
    if not NM.get():
        raise HTTPException(status_code=400, detail="Сначала выполните preflight.")

    async def _run() -> None:
        try:
            await asyncio.to_thread(provision)
        except Exception:  # noqa: BLE001
            # provision() уже записал статус FAILED со step'ом; логируем для диагностики.
            logger.exception("Фоновый provision миграции завершился ошибкой")

    asyncio.create_task(_run())
    rec = NM.get_public() or {}
    return MigrationStatus(**rec)


@router.post("/check-dns", response_model=MigrationStatus)
async def migration_check_dns(_: CurrentUser = Depends(require_admin)) -> MigrationStatus:
    try:
        result = await asyncio.to_thread(check_dns)
    except NodeMigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return MigrationStatus(**result)


@router.post("/activate", response_model=MigrationStatus)
async def migration_activate(
    payload: MigrateActivateRequest,
    _: CurrentUser = Depends(require_admin),
) -> MigrationStatus:
    if not NM.get():
        raise HTTPException(status_code=400, detail="Миграция не найдена.")

    force = payload.force

    async def _run() -> None:
        try:
            await asyncio.to_thread(activate, force=force)
        except Exception:  # noqa: BLE001
            logger.exception("Фоновая активация миграции завершилась ошибкой")

    asyncio.create_task(_run())
    rec = NM.get_public() or {}
    return MigrationStatus(**rec)


@router.post("/abort")
async def migration_abort(_: CurrentUser = Depends(require_admin)) -> dict:
    try:
        return await asyncio.to_thread(abort)
    except NodeMigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
