import asyncio

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.schemas.auth import CurrentUser
from app.services.dashboard import get_dashboard_overview, get_dashboard_summary


router = APIRouter()


@router.get("/summary")
async def summary(_: CurrentUser = Depends(get_current_user)) -> dict:
    return get_dashboard_summary()


@router.get("/overview")
async def overview(_: CurrentUser = Depends(get_current_user)) -> dict:
    """OBS2: нагрузка узлов (CPU/RAM/диск/uptime/трафик) + сводка каналов."""
    return await asyncio.to_thread(get_dashboard_overview)


@router.get("/alerts")
async def alerts(_: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return get_dashboard_summary()["alerts"]
