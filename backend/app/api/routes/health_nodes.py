"""API здоровья узлов (OBS1)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_admin
from app.schemas.auth import CurrentUser
from app.services.health import check_server, get_health_overview
from app.services.server_store import server_store

router = APIRouter()


@router.get("")
async def health_overview(_: CurrentUser = Depends(require_admin)) -> list[dict]:
    return await asyncio.to_thread(get_health_overview)


@router.post("/{server_id}/check")
async def health_check_one(
    server_id: str,
    auto_restart: bool = True,
    _: CurrentUser = Depends(require_admin),
) -> dict:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(check_server, server_id, auto_restart=auto_restart)
