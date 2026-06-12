"""Публичный клиентский чат-API. Доступен ТОЛЬКО через chat-домен (Host-allowlist)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat_deps import get_current_chat_user, require_chat_host
from app.core.chat_security import (
    clear_login_fails,
    create_chat_access_token,
    login_locked,
    register_login_fail,
)
from app.core.deps import client_ip
from app.db.session import get_db
from app.models.chat import ChatAttachment, ChatMessage, ChatUser
from app.schemas.chat import (
    ChatAttachmentInfo,
    ChatAttachmentView,
    ChatClientConfig,
    ChatLoginRequest,
    ChatMessageRead,
    ChatMessagesPage,
    ChatProfile,
    ChatPushSubscribeRequest,
    ChatPushUnsubscribeRequest,
    ChatRefreshRequest,
    ChatSendRequest,
    ChatTokenResponse,
)
from app.services import push_service
from app.services.audit_service import AuditService
from app.services.chat_service import ChatService, ChatServiceError
from app.services.panel_settings_service import PanelSettingsService
from app.services.qr import build_qr_data_url

router = APIRouter(dependencies=[Depends(require_chat_host)])

GENERIC_LOGIN_ERROR = "Неверный логин или пароль."


def attachment_info(att: ChatAttachment) -> ChatAttachmentInfo:
    return ChatAttachmentInfo(
        id=str(att.id),
        kind=att.kind,
        filename=att.filename,
        expires_at=att.expires_at.isoformat(),
        expired=att.expires_at < datetime.now(timezone.utc),
    )


def _msg_read(msg: ChatMessage, att_map: Optional[dict] = None) -> ChatMessageRead:
    att = (att_map or {}).get(msg.attachment_id) if msg.attachment_id else None
    return ChatMessageRead(
        id=msg.id,
        sender=msg.sender_type,
        body=msg.body,
        created_at=msg.created_at.isoformat(),
        attachment=attachment_info(att) if att else None,
    )


def _profile(user: ChatUser) -> ChatProfile:
    return ChatProfile(username=user.username, display_name=user.display_name)


@router.post("/login", response_model=ChatTokenResponse)
async def chat_login(
    payload: ChatLoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> ChatTokenResponse:
    ip = client_ip(request) or "?"
    key = f"{payload.username.strip().lower()}|{ip}"
    locked = login_locked(key)
    if locked:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.")

    svc = ChatService(db)
    user = await svc.authenticate(payload.username, payload.password)
    if not user:
        register_login_fail(key)
        await AuditService(db).log(
            "chat_login_failed",
            target_type="chat_user",
            detail={"username": payload.username.strip().lower()[:32]},
            ip=ip,
        )
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)

    clear_login_fails(key)
    refresh = await svc.create_session(user.id)
    return ChatTokenResponse(
        access_token=create_chat_access_token(str(user.id)),
        refresh_token=refresh,
        profile=_profile(user),
    )


@router.get("/config", response_model=ChatClientConfig)
async def chat_config(db: AsyncSession = Depends(get_db)) -> ChatClientConfig:
    settings_svc = PanelSettingsService(db)
    enabled = (await settings_svc.get("chat_enabled")) == "true"
    public = await push_service.get_public_key(settings_svc) if enabled else None
    return ChatClientConfig(push_enabled=bool(enabled and public), vapid_public_key=public)


@router.post("/push/subscribe")
async def chat_push_subscribe(
    payload: ChatPushSubscribeRequest,
    user: ChatUser = Depends(get_current_chat_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await ChatService(db).save_push_subscription(
        user.id, payload.endpoint, payload.p256dh, payload.auth
    )
    return {"status": "ok"}


@router.post("/push/unsubscribe")
async def chat_push_unsubscribe(
    payload: ChatPushUnsubscribeRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    await ChatService(db).delete_push_subscription(payload.endpoint)
    return {"status": "ok"}


@router.post("/refresh", response_model=ChatTokenResponse)
async def chat_refresh(
    payload: ChatRefreshRequest, db: AsyncSession = Depends(get_db)
) -> ChatTokenResponse:
    svc = ChatService(db)
    rotated = await svc.rotate_session(payload.refresh_token)
    if not rotated:
        raise HTTPException(status_code=401, detail="Сессия истекла, войдите заново.")
    user, new_refresh = rotated
    return ChatTokenResponse(
        access_token=create_chat_access_token(str(user.id)),
        refresh_token=new_refresh,
        profile=_profile(user),
    )


@router.post("/logout")
async def chat_logout(payload: ChatRefreshRequest, db: AsyncSession = Depends(get_db)) -> dict:
    await ChatService(db).revoke_session(payload.refresh_token)
    return {"status": "ok"}


@router.get("/me", response_model=ChatProfile)
async def chat_me(user: ChatUser = Depends(get_current_chat_user)) -> ChatProfile:
    return _profile(user)


@router.get("/messages", response_model=ChatMessagesPage)
async def chat_messages(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: ChatUser = Depends(get_current_chat_user),
    db: AsyncSession = Depends(get_db),
) -> ChatMessagesPage:
    svc = ChatService(db)
    thread = await svc.get_or_create_thread(user.id)
    messages = await svc.list_messages(thread.id, after_id=after_id, limit=limit)
    att_map = await svc.attachments_map([m.attachment_id for m in messages if m.attachment_id])
    return ChatMessagesPage(
        messages=[_msg_read(m, att_map) for m in messages],
        thread_status=thread.status,
    )


@router.post("/messages", response_model=ChatMessageRead)
async def chat_send(
    payload: ChatSendRequest,
    user: ChatUser = Depends(get_current_chat_user),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageRead:
    svc = ChatService(db)
    thread = await svc.get_or_create_thread(user.id)
    try:
        msg = await svc.send_message(thread, "client", payload.body)
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _msg_read(msg)


def _attachment_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Вложение не найдено.")


@router.get("/attachments/{attachment_id}/view", response_model=ChatAttachmentView)
async def chat_attachment_view(
    attachment_id: str,
    request: Request,
    user: ChatUser = Depends(get_current_chat_user),
    db: AsyncSession = Depends(get_db),
) -> ChatAttachmentView:
    svc = ChatService(db)
    try:
        att, content = await svc.open_attachment_for_user(_attachment_uuid(attachment_id), user.id)
    except ChatServiceError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    config_text = content.get("config_text")
    vpn_link = content.get("vpn_link")
    qr_source = config_text or vpn_link
    await AuditService(db).log(
        "chat_attachment_download",
        target_type="chat_attachment",
        target_id=str(att.id),
        detail={"username": user.username, "format": "view"},
        ip=client_ip(request),
    )
    return ChatAttachmentView(
        filename=att.filename,
        expires_at=att.expires_at.isoformat(),
        has_conf=bool(config_text),
        config_text=config_text,
        vpn_link=vpn_link,
        qr_data_url=build_qr_data_url(qr_source) if qr_source else None,
        qr_awg_data_url=build_qr_data_url(config_text) if config_text else None,
        qr_vpn_data_url=build_qr_data_url(vpn_link) if vpn_link else None,
    )


@router.get("/attachments/{attachment_id}/file")
async def chat_attachment_file(
    attachment_id: str,
    request: Request,
    user: ChatUser = Depends(get_current_chat_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    svc = ChatService(db)
    try:
        att, content = await svc.open_attachment_for_user(_attachment_uuid(attachment_id), user.id)
    except ChatServiceError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    config_text = content.get("config_text")
    if not config_text:
        raise HTTPException(status_code=404, detail="Файл конфигурации недоступен для этого ключа.")
    await AuditService(db).log(
        "chat_attachment_download",
        target_type="chat_attachment",
        target_id=str(att.id),
        detail={"username": user.username, "format": "file"},
        ip=client_ip(request),
    )
    # octet-stream: iOS/Android скачивают как файл с расширением .conf,
    # а не открывают предпросмотр текста
    return Response(
        content=config_text,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{att.filename}"',
            "Cache-Control": "no-store",
        },
    )
