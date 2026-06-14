"""API DPI-трендов (OBS3)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import require_admin
from app.schemas.auth import CurrentUser
from app.services.dpi_stats import get_dpi_overview, get_server_dpi, sample_all
from app.services.server_store import server_store

router = APIRouter()


@router.get("")
async def dpi_overview(_: CurrentUser = Depends(require_admin)) -> list[dict]:
    return await asyncio.to_thread(get_dpi_overview)


@router.post("/sample")
async def dpi_sample_now(_: CurrentUser = Depends(require_admin)) -> dict:
    """Ручной срез DPI-трендов (для теста/обновления прямо сейчас)."""
    return await asyncio.to_thread(sample_all)


@router.get("/{server_id}")
async def dpi_server(server_id: str, _: CurrentUser = Depends(require_admin)) -> dict:
    if not server_store.get_record(server_id):
        raise HTTPException(status_code=404, detail="Сервер не найден.")
    return await asyncio.to_thread(get_server_dpi, server_id)
