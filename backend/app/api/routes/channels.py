from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.schemas.auth import CurrentUser
from app.services.channel_store import get_channel, list_channels
from app.services.topology import get_server_map

router = APIRouter()


@router.get("")
async def channels(_: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """Единый список каналов выдачи (direct + cascade), производный от topology."""
    return list_channels()


@router.get("/map/topology")
async def topology(_: CurrentUser = Depends(get_current_user)) -> dict:
    """Карта серверов: узлы (с health) + связи каскадов (OBS2)."""
    return get_server_map()


@router.get("/{channel_id}")
async def channel(channel_id: str, _: CurrentUser = Depends(get_current_user)) -> dict:
    found = get_channel(channel_id)
    if not found:
        raise HTTPException(status_code=404, detail="Канал не найден.")
    return found
