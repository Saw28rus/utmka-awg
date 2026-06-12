"""Админский чат-API (panel JWT). Аккаунты — только админ; диалоги — админ и
модератор (если chat_moderator_access=true)."""

import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.core.deps import client_ip, get_current_user, require_admin
from app.db.session import get_db
from app.models.chat import ChatMessage, ChatUser
from app.models.invoice import Invoice
from app.schemas.auth import CurrentUser
from app.schemas.chat import (
    ChatInsertInvoiceRequest,
    ChatInvoiceItem,
    ChatLinkRequest,
    ChatMessageRead,
    ChatMessagesPage,
    ChatSendRequest,
    ChatStatusRead,
    ChatThreadRead,
    ChatThreadStatusRequest,
    ChatUserCreate,
    ChatUserRead,
    ChatUserWithPassword,
)
from app.services import push_service
from app.services.audit_service import AuditService
from app.services.chat_service import ChatService, ChatServiceError
from app.services.client_store import client_store
from app.services.panel_settings_service import PanelSettingsService

router = APIRouter()


async def require_chat_access(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if user.role == "admin":
        return user
    if user.role == "moderator":
        settings_svc = PanelSettingsService(db)
        if (await settings_svc.get("chat_moderator_access")) != "false":
            return user
    raise HTTPException(status_code=403, detail="Недостаточно прав.")


def _user_read(u: ChatUser) -> ChatUserRead:
    client_name = None
    if u.client_id:
        record = client_store.get_record_raw(u.client_id)
        client_name = (record.get("name") if record else None) or u.client_id
    return ChatUserRead(
        id=str(u.id),
        username=u.username,
        display_name=u.display_name,
        client_id=u.client_id,
        client_name=client_name,
        is_active=u.is_active,
        last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
        created_at=u.created_at.isoformat(),
    )


def _msg_read(m: ChatMessage, att_map: Optional[dict] = None) -> ChatMessageRead:
    from app.api.routes.chat_client import attachment_info

    att = (att_map or {}).get(m.attachment_id) if m.attachment_id else None
    return ChatMessageRead(
        id=m.id,
        sender=m.sender_type,
        body=m.body,
        created_at=m.created_at.isoformat(),
        attachment=attachment_info(att) if att else None,
    )


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Не найдено.")


@router.get("/status", response_model=ChatStatusRead)
async def chat_status(
    _: CurrentUser = Depends(require_chat_access),
    db: AsyncSession = Depends(get_db),
) -> ChatStatusRead:
    settings_svc = PanelSettingsService(db)
    enabled = (await settings_svc.get("chat_enabled")) == "true"
    counts = await ChatService(db).counts() if enabled else {"users": 0, "threads": 0}
    return ChatStatusRead(
        enabled=enabled,
        domain=await settings_svc.get("chat_domain") or None,
        public_url=await settings_svc.get("chat_public_url") or None,
        moderator_access=(await settings_svc.get("chat_moderator_access")) != "false",
        users=counts["users"],
        threads=counts["threads"],
    )


# --- аккаунты (только админ) -------------------------------------------------


@router.get("/users", response_model=list[ChatUserRead])
async def chat_users(
    _: CurrentUser = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[ChatUserRead]:
    return [_user_read(u) for u in await ChatService(db).list_users()]


@router.post("/users", response_model=ChatUserWithPassword)
async def chat_user_create(
    payload: ChatUserCreate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatUserWithPassword:
    svc = ChatService(db)
    try:
        user, password = await svc.create_user(
            payload.username, payload.display_name, uuid.UUID(admin.id)
        )
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await AuditService(db).log(
        "chat_user_created",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="chat_user",
        target_id=str(user.id),
        detail={"username": user.username},
        ip=client_ip(request),
    )
    return ChatUserWithPassword(user=_user_read(user), password=password)


@router.post("/users/{user_id}/reset-password", response_model=ChatUserWithPassword)
async def chat_user_reset_password(
    user_id: str,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatUserWithPassword:
    svc = ChatService(db)
    try:
        user, password = await svc.reset_password(_parse_uuid(user_id))
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await AuditService(db).log(
        "chat_password_reset",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="chat_user",
        target_id=str(user.id),
        detail={"username": user.username},
        ip=client_ip(request),
    )
    return ChatUserWithPassword(user=_user_read(user), password=password)


@router.post("/users/{user_id}/toggle-active", response_model=ChatUserRead)
async def chat_user_toggle_active(
    user_id: str,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatUserRead:
    svc = ChatService(db)
    uid = _parse_uuid(user_id)
    user = await svc.get_user(uid)
    if not user:
        raise HTTPException(status_code=404, detail="Аккаунт не найден.")
    user = await svc.set_active(uid, not user.is_active)
    return _user_read(user)


@router.post("/users/{user_id}/link", response_model=ChatUserRead)
async def chat_user_link(
    user_id: str,
    payload: ChatLinkRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatUserRead:
    svc = ChatService(db)
    try:
        user = await svc.link_client(_parse_uuid(user_id), payload.client_id)
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await AuditService(db).log(
        "chat_user_linked",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="chat_user",
        target_id=str(user.id),
        detail={"username": user.username, "client_id": user.client_id},
        ip=client_ip(request),
    )
    return _user_read(user)


# --- диалоги (админ + модератор) ----------------------------------------------


@router.get("/threads", response_model=list[ChatThreadRead])
async def chat_threads(
    _: CurrentUser = Depends(require_chat_access), db: AsyncSession = Depends(get_db)
) -> list[ChatThreadRead]:
    return [ChatThreadRead(**t) for t in await ChatService(db).admin_threads()]


@router.get("/threads/{thread_id}/messages", response_model=ChatMessagesPage)
async def chat_thread_messages(
    thread_id: str,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _: CurrentUser = Depends(require_chat_access),
    db: AsyncSession = Depends(get_db),
) -> ChatMessagesPage:
    svc = ChatService(db)
    thread = await svc.get_thread(_parse_uuid(thread_id))
    if not thread:
        raise HTTPException(status_code=404, detail="Диалог не найден.")
    messages = await svc.list_messages(thread.id, after_id=after_id, limit=limit)
    att_map = await svc.attachments_map([m.attachment_id for m in messages if m.attachment_id])
    return ChatMessagesPage(
        messages=[_msg_read(m, att_map) for m in messages], thread_status=thread.status
    )


@router.post("/threads/{thread_id}/messages", response_model=ChatMessageRead)
async def chat_thread_send(
    thread_id: str,
    payload: ChatSendRequest,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_chat_access),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageRead:
    svc = ChatService(db)
    thread = await svc.get_thread(_parse_uuid(thread_id))
    if not thread:
        raise HTTPException(status_code=404, detail="Диалог не найден.")
    try:
        msg = await svc.send_message(thread, "admin", payload.body, uuid.UUID(user.id))
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    background.add_task(push_service.notify, thread.chat_user_id, "message")
    await AuditService(db).log(
        "chat_message_sent",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="chat_thread",
        target_id=thread_id,
        detail={"length": len(payload.body)},
        ip=client_ip(request),
    )
    return _msg_read(msg)


def _amount_rub(kopecks: int) -> str:
    rub = kopecks // 100
    kop = kopecks % 100
    return f"{rub}" if kop == 0 else f"{rub}.{kop:02d}"


async def _thread_chat_user(svc: ChatService, db: AsyncSession, thread_id: str) -> tuple:
    thread = await svc.get_thread(_parse_uuid(thread_id))
    if not thread:
        raise HTTPException(status_code=404, detail="Диалог не найден.")
    chat_user = await db.get(ChatUser, thread.chat_user_id)
    if not chat_user:
        raise HTTPException(status_code=404, detail="Аккаунт чата не найден.")
    return thread, chat_user


@router.get("/threads/{thread_id}/invoices", response_model=list[ChatInvoiceItem])
async def chat_thread_invoices(
    thread_id: str,
    _: CurrentUser = Depends(require_chat_access),
    db: AsyncSession = Depends(get_db),
) -> list[ChatInvoiceItem]:
    svc = ChatService(db)
    _, chat_user = await _thread_chat_user(svc, db, thread_id)
    if not chat_user.client_id:
        return []
    rows = await db.execute(
        select(Invoice)
        .where(Invoice.client_id == chat_user.client_id, Invoice.deleted_at.is_(None))
        .order_by(Invoice.created_at.desc())
        .limit(20)
    )
    return [
        ChatInvoiceItem(
            id=str(inv.id),
            description=inv.description or inv.service,
            amount_rub=_amount_rub(inv.amount_kopecks),
            status=inv.status,
            pay_url=inv.pay_url,
            created_at=inv.created_at.isoformat(),
            expires_at=inv.expires_at.isoformat() if inv.expires_at else None,
        )
        for inv in rows.scalars().all()
    ]


@router.post("/threads/{thread_id}/insert-invoice", response_model=ChatMessageRead)
async def chat_thread_insert_invoice(
    thread_id: str,
    payload: ChatInsertInvoiceRequest,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_chat_access),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageRead:
    svc = ChatService(db)
    thread, chat_user = await _thread_chat_user(svc, db, thread_id)
    if not chat_user.client_id:
        raise HTTPException(status_code=400, detail="Сначала привяжите VPN-клиента к аккаунту чата.")

    invoice = await db.get(Invoice, _parse_uuid(payload.invoice_id))
    # Анти-IDOR: счёт должен принадлежать именно привязанному клиенту
    if not invoice or invoice.deleted_at is not None or invoice.client_id != chat_user.client_id:
        raise HTTPException(status_code=404, detail="Счёт не найден у привязанного клиента.")

    if invoice.message_text:
        body = invoice.message_text
    else:
        lines = [f"Счёт на оплату: {invoice.description or invoice.service or 'услуги VPN'}"]
        lines.append(f"Сумма: {_amount_rub(invoice.amount_kopecks)} ₽")
        if invoice.pay_url:
            lines.append(f"Оплатить: {invoice.pay_url}")
        if invoice.expires_at:
            lines.append(f"Счёт действителен до {invoice.expires_at.strftime('%d.%m.%Y %H:%M')} UTC")
        body = "\n".join(lines)

    try:
        msg = await svc.send_message(thread, "admin", body, uuid.UUID(user.id))
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    background.add_task(push_service.notify, thread.chat_user_id, "invoice")
    await AuditService(db).log(
        "chat_invoice_inserted",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="chat_thread",
        target_id=thread_id,
        detail={"invoice_id": str(invoice.id), "client_id": chat_user.client_id},
        ip=client_ip(request),
    )
    return _msg_read(msg)


@router.post("/threads/{thread_id}/send-key", response_model=ChatMessageRead)
async def chat_thread_send_key(
    thread_id: str,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_chat_access),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageRead:
    svc = ChatService(db)
    thread, chat_user = await _thread_chat_user(svc, db, thread_id)
    try:
        msg = await svc.issue_key_attachment(thread, chat_user, uuid.UUID(user.id))
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    background.add_task(push_service.notify, thread.chat_user_id, "key")
    await AuditService(db).log(
        "chat_key_sent",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="chat_thread",
        target_id=thread_id,
        detail={"client_id": chat_user.client_id, "attachment_id": str(msg.attachment_id)},
        ip=client_ip(request),
    )
    att_map = await svc.attachments_map([msg.attachment_id] if msg.attachment_id else [])
    return _msg_read(msg, att_map)


@router.post("/threads/{thread_id}/status", response_model=ChatThreadRead)
async def chat_thread_status(
    thread_id: str,
    payload: ChatThreadStatusRequest,
    _: CurrentUser = Depends(require_chat_access),
    db: AsyncSession = Depends(get_db),
) -> ChatThreadRead:
    svc = ChatService(db)
    try:
        await svc.set_thread_status(_parse_uuid(thread_id), payload.status)
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    threads = await svc.admin_threads()
    for t in threads:
        if t["id"] == thread_id:
            return ChatThreadRead(**t)
    raise HTTPException(status_code=404, detail="Диалог не найден.")
