"""Зависимости клиентского чат-API: Host-allowlist и chat-JWT (aud=chat_client)."""

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat_security import decode_chat_token
from app.db.session import get_db
from app.models.chat import ChatUser
from app.services.panel_settings_service import PanelSettingsService

chat_bearer = HTTPBearer(auto_error=False)


def _request_host(request: Request) -> str:
    """Host, который видел внешний nginx (оба прокси пробрасывают Host as-is)."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return host.split(":")[0].strip().lower()


async def require_chat_host(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """Клиентский чат-API отвечает ТОЛЬКО на chat-домене. Иначе 404 (не 403,
    чтобы не раскрывать существование API на panel-домене/raw IP)."""
    settings_svc = PanelSettingsService(db)
    enabled = await settings_svc.get("chat_enabled")
    domain = (await settings_svc.get("chat_domain") or "").strip().lower()
    if enabled != "true" or not domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if _request_host(request) != domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return domain


async def get_current_chat_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(chat_bearer),
    db: AsyncSession = Depends(get_db),
) -> ChatUser:
    if not credentials:
        raise HTTPException(status_code=401, detail="Необходима авторизация.")
    try:
        payload = decode_chat_token(credentials.credentials)
    except Exception as exc:  # noqa: BLE001 — любые проблемы токена -> 401
        raise HTTPException(status_code=401, detail="Сессия недействительна.") from exc

    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Сессия недействительна.")

    user = await db.get(ChatUser, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Учётная запись недоступна.")
    return user
