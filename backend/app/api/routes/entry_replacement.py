"""API «Замены Входа» (RES2a). Все операции — только admin.

Пути под префиксом /servers: /servers/{server_id}/replace/...
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_admin
from app.schemas.auth import CurrentUser
from app.schemas.entry_replacement import (
    ReplaceCandidate,
    ReplacePreflightRequest,
    ReplacePreflightResult,
    ReplacementStatus,
)
from app.services.entry_replacement import (
    EntryReplacementError,
    abort,
    activate,
    check_dns,
    list_replace_candidates,
    preflight,
    provision,
)
from app.services.entry_replacement import status as replacement_status
from app.services.server_store import server_store

router = APIRouter()


def _require_server(server_id: str) -> None:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")


@router.get("/{server_id}/replace/candidates", response_model=list[ReplaceCandidate])
async def replace_candidates(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> list[ReplaceCandidate]:
    _require_server(server_id)
    try:
        items = await asyncio.to_thread(list_replace_candidates, server_id)
    except EntryReplacementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [ReplaceCandidate(**item) for item in items]


@router.post("/{server_id}/replace/preflight", response_model=ReplacePreflightResult)
async def replace_preflight(
    server_id: str,
    payload: ReplacePreflightRequest,
    _: CurrentUser = Depends(require_admin),
) -> ReplacePreflightResult:
    _require_server(server_id)
    try:
        result = await asyncio.to_thread(
            preflight,
            server_id,
            source_server_id=payload.source_server_id,
            new_host=payload.new_host,
            ssh_port=payload.ssh_port,
            ssh_username=payload.ssh_username,
            ssh_password=payload.ssh_password,
            ssh_key=payload.ssh_key,
        )
    except EntryReplacementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReplacePreflightResult(**result)


@router.post("/{server_id}/replace/provision", response_model=ReplacementStatus)
async def replace_provision(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> ReplacementStatus:
    _require_server(server_id)
    try:
        result = await asyncio.to_thread(provision, server_id)
    except EntryReplacementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReplacementStatus(**result)


@router.get("/{server_id}/replace/status", response_model=Optional[ReplacementStatus])
async def replace_status(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> Optional[ReplacementStatus]:
    _require_server(server_id)
    result = await asyncio.to_thread(replacement_status, server_id)
    return ReplacementStatus(**result) if result else None


@router.post("/{server_id}/replace/check-dns", response_model=ReplacementStatus)
async def replace_check_dns(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> ReplacementStatus:
    _require_server(server_id)
    try:
        result = await asyncio.to_thread(check_dns, server_id)
    except EntryReplacementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReplacementStatus(**result)


@router.post("/{server_id}/replace/activate", response_model=ReplacementStatus)
async def replace_activate(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> ReplacementStatus:
    _require_server(server_id)
    try:
        result = await asyncio.to_thread(activate, server_id)
    except EntryReplacementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReplacementStatus(**result)


@router.post("/{server_id}/replace/abort")
async def replace_abort(
    server_id: str,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    _require_server(server_id)
    try:
        return await asyncio.to_thread(abort, server_id)
    except EntryReplacementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
