import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_admin
from app.schemas.auth import CurrentUser
from app.schemas.cascade import (
    CascadeApplyResult,
    CascadeLinkStatus,
    CascadeLinkSummary,
    CascadePreflightRequest,
    CascadePreflightResult,
    CascadeRulesApplyResult,
    CascadeRulesStatus,
    CascadeRulesUpdate,
)
from app.services.cascade import (
    CascadeError,
    get_cascade_status,
    list_cascade_links,
    run_preflight,
)
from app.services.cascade_apply import apply_cascade, rollback_cascade
from app.services.cascade_rules import (
    get_rules_status,
    refresh_lists,
    update_rules,
)
from app.services.server_store import server_store

router = APIRouter()


@router.get("/cascade/links", response_model=list[CascadeLinkSummary])
async def cascade_links(
    live: bool = False,
    _: CurrentUser = Depends(require_admin),
) -> list[CascadeLinkSummary]:
    return await asyncio.to_thread(list_cascade_links, live_probe=live)


@router.get("/{server_id}/cascade/status", response_model=CascadeLinkStatus)
async def cascade_status(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> CascadeLinkStatus:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        return await asyncio.to_thread(get_cascade_status, server_id)
    except CascadeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{server_id}/cascade/preflight", response_model=CascadePreflightResult)
async def cascade_preflight(
    server_id: str,
    payload: CascadePreflightRequest,
    _: CurrentUser = Depends(require_admin),
) -> CascadePreflightResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        return await asyncio.to_thread(run_preflight, server_id, payload.exit_server_id)
    except CascadeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{server_id}/cascade/apply", response_model=CascadeApplyResult)
async def cascade_apply(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> CascadeApplyResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        return await asyncio.to_thread(apply_cascade, server_id)
    except CascadeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{server_id}/cascade/rollback", response_model=CascadeApplyResult)
async def cascade_rollback(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> CascadeApplyResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        return await asyncio.to_thread(rollback_cascade, server_id)
    except CascadeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- split-правила (РФ напрямую, остальное через exit) ---


@router.get("/{server_id}/cascade/rules", response_model=CascadeRulesStatus)
async def cascade_rules_status(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> CascadeRulesStatus:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(get_rules_status, server_id)


@router.put("/{server_id}/cascade/rules", response_model=CascadeRulesApplyResult)
async def cascade_rules_update(
    server_id: str,
    payload: CascadeRulesUpdate,
    _: CurrentUser = Depends(require_admin),
) -> CascadeRulesApplyResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        return await asyncio.to_thread(
            update_rules,
            server_id,
            enabled=payload.enabled,
            source_ids=payload.source_ids,
            custom_cidrs=payload.custom_cidrs,
        )
    except CascadeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{server_id}/cascade/rules/refresh", response_model=CascadeRulesApplyResult)
async def cascade_rules_refresh(
    server_id: str, _: CurrentUser = Depends(require_admin)
) -> CascadeRulesApplyResult:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    try:
        return await asyncio.to_thread(refresh_lists, server_id)
    except CascadeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
