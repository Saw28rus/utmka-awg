from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.schemas.auth import CurrentUser
from app.services.channel_store import get_channel, list_channels

router = APIRouter()


@router.get("")
async def channels(_: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """Единый список каналов выдачи (direct + cascade), производный от topology."""
    return list_channels()


@router.get("/{channel_id}")
async def channel(channel_id: str, _: CurrentUser = Depends(get_current_user)) -> dict:
    found = get_channel(channel_id)
    if not found:
        raise HTTPException(status_code=404, detail="Канал не найден.")
    return found
