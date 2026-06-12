"""Публичный клиентский чат-API. Доступен ТОЛЬКО через chat-домен (Host-allowlist)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
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
    ChatSelfPaymentResponse,
    ChatSendRequest,
    ChatTokenResponse,
    ChatVpnInfo,
)
from app.services import push_service
from app.services.audit_service import AuditService
from app.services.chat_service import ChatService, ChatServiceError
from app.services.client_store import client_store
from app.services.invoice_service import InvoiceService, SelfPaymentError
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


def _days_left(expires_at: Optional[str]) -> Optional[int]:
    if not expires_at:
        return None
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    secs = (parsed - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return 0
    return int(secs // 86400) + (1 if secs % 86400 else 0)


@router.get("/me/vpn", response_model=ChatVpnInfo)
async def chat_me_vpn(
    user: ChatUser = Depends(get_current_chat_user),
    db: AsyncSession = Depends(get_db),
) -> ChatVpnInfo:
    if not user.client_id:
        return ChatVpnInfo(linked=False)
    detail = client_store.get_detail(user.client_id)
    if not detail:
        return ChatVpnInfo(linked=False)

    invoice_svc = InvoiceService(db)
    yk_available = await invoice_svc.yookassa_available()
    is_paid = detail.billing_mode == "paid" and bool(detail.billing_amount_kopecks)
    used_this_month = (
        await invoice_svc.self_pay_count_this_month(user.client_id) if is_paid else 0
    )
    remaining = max(0, 3 - used_this_month) if is_paid else 0
    can_self_pay = is_paid and yk_available and remaining > 0

    return ChatVpnInfo(
        linked=True,
        name=detail.name,
        status=detail.status,
        expires_at=detail.expires_at,
        days_left=_days_left(detail.expires_at),
        traffic_used_bytes=detail.traffic_used_bytes or 0,
        traffic_limit_bytes=detail.traffic_limit_bytes,
        billing_mode=detail.billing_mode,
        billing_amount_kopecks=detail.billing_amount_kopecks,
        billing_period_months=detail.billing_period_months,
        yookassa_available=yk_available,
        can_self_pay=can_self_pay,
        self_pay_remaining=remaining,
    )


@router.post("/me/request-payment", response_model=ChatSelfPaymentResponse)
async def chat_me_request_payment(
    request: Request,
    background: BackgroundTasks,
    user: ChatUser = Depends(get_current_chat_user),
    db: AsyncSession = Depends(get_db),
) -> ChatSelfPaymentResponse:
    invoice_svc = InvoiceService(db)
    try:
        result = await invoice_svc.create_self_payment(chat_user=user)
    except SelfPaymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # Дублируем ссылку в переписку, чтобы она была под рукой и у клиента, и у админа.
    svc = ChatService(db)
    thread = await svc.get_or_create_thread(user.id)
    body = result.get("message_text") or (
        "Счёт на продление доступа создан. "
        f"Оплатите по ссылке: {result['pay_url']}"
    )
    try:
        await svc.send_message(thread, "admin", body, sender_user_id=None)
    except ChatServiceError:
        pass
    background.add_task(push_service.notify, user.id, "invoice")

    await AuditService(db).log(
        "chat_self_payment",
        target_type="chat_user",
        target_id=str(user.id),
        detail={
            "client_id": user.client_id,
            "invoice_id": result["invoice_id"],
            "reused": result.get("reused", False),
        },
        ip=client_ip(request),
    )
    return ChatSelfPaymentResponse(
        pay_url=result["pay_url"],
        invoice_id=result["invoice_id"],
        expires_at=result.get("expires_at"),
        amount_kopecks=result.get("amount_kopecks"),
        reused=result.get("reused", False),
    )


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
