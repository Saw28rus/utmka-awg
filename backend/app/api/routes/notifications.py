"""API операторских уведомлений (OBS1-2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import require_admin
from app.schemas.auth import CurrentUser
from app.services.notification_store import notification_store

router = APIRouter()


@router.get("")
async def list_notifications(limit: int = 50, _: CurrentUser = Depends(require_admin)) -> dict:
    return {
        "items": notification_store.list(limit=limit),
        "unread": notification_store.unread_count(),
    }


@router.post("/read")
async def mark_read(payload: dict | None = None, _: CurrentUser = Depends(require_admin)) -> dict:
    ids = (payload or {}).get("ids")
    changed = notification_store.mark_read(ids)
    return {"status": "ok", "marked": changed, "unread": notification_store.unread_count()}
