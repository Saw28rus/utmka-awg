"""Бизнес-логика Chat Mini-App (CH2): аккаунты, сессии, треды, сообщения."""

import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.chat_security import hash_refresh_token, new_refresh_token
from app.core.crypto import decrypt, encrypt
from app.core.security import hash_password, verify_password
from app.models.chat import (
    ChatAttachment,
    ChatMessage,
    ChatPushSubscription,
    ChatSession,
    ChatThread,
    ChatUser,
)

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")
MESSAGE_MAX_LEN = 4000
KEY_ATTACHMENT_TTL_HOURS = 72
# Без похожих символов (l/1, O/0), чтобы пароль легко диктовать клиенту
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


class ChatServiceError(Exception):
    pass


def generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- аккаунты (admin) ---------------------------------------------------

    async def list_users(self) -> list[ChatUser]:
        rows = await self.session.execute(select(ChatUser).order_by(ChatUser.created_at.desc()))
        return list(rows.scalars().all())

    async def create_user(
        self,
        username: str,
        display_name: Optional[str],
        created_by: Optional[uuid.UUID],
    ) -> tuple[ChatUser, str]:
        username = username.strip().lower()
        if not USERNAME_RE.match(username):
            raise ChatServiceError(
                "Логин: 3–32 символа, только латиница в нижнем регистре, цифры и «_»."
            )
        existing = await self.session.execute(select(ChatUser).where(ChatUser.username == username))
        if existing.scalar_one_or_none():
            raise ChatServiceError("Такой логин уже занят.")

        password = generate_password()
        user = ChatUser(
            username=username,
            display_name=(display_name or "").strip() or None,
            password_hash=hash_password(password),
            created_by_user_id=created_by,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user, password

    async def get_user(self, user_id: uuid.UUID) -> Optional[ChatUser]:
        return await self.session.get(ChatUser, user_id)

    async def reset_password(self, user_id: uuid.UUID) -> tuple[ChatUser, str]:
        user = await self.get_user(user_id)
        if not user:
            raise ChatServiceError("Аккаунт не найден.")
        password = generate_password()
        user.password_hash = hash_password(password)
        user.password_reset_at = _utcnow()
        await self._revoke_all_sessions(user.id)
        await self.session.commit()
        await self.session.refresh(user)
        return user, password

    async def link_client(self, user_id: uuid.UUID, client_id: Optional[str]) -> ChatUser:
        from app.services.client_store import client_store

        user = await self.get_user(user_id)
        if not user:
            raise ChatServiceError("Аккаунт не найден.")
        if client_id:
            if not client_store.get_record_raw(client_id):
                raise ChatServiceError("VPN-клиент не найден.")
            other = await self.session.execute(
                select(ChatUser).where(ChatUser.client_id == client_id, ChatUser.id != user_id)
            )
            if other.scalar_one_or_none():
                raise ChatServiceError("Этот VPN-клиент уже привязан к другому чат-аккаунту.")
        user.client_id = client_id or None
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def set_active(self, user_id: uuid.UUID, active: bool) -> ChatUser:
        user = await self.get_user(user_id)
        if not user:
            raise ChatServiceError("Аккаунт не найден.")
        user.is_active = active
        if not active:
            await self._revoke_all_sessions(user.id)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    # --- аутентификация клиента ----------------------------------------------

    async def authenticate(self, username: str, password: str) -> Optional[ChatUser]:
        rows = await self.session.execute(
            select(ChatUser).where(ChatUser.username == username.strip().lower())
        )
        user = rows.scalar_one_or_none()
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        user.last_login_at = _utcnow()
        await self.session.commit()
        return user

    async def create_session(self, chat_user_id: uuid.UUID) -> str:
        token, token_hash, expires = new_refresh_token()
        self.session.add(
            ChatSession(chat_user_id=chat_user_id, refresh_token_hash=token_hash, expires_at=expires)
        )
        await self.session.commit()
        return token

    async def rotate_session(self, refresh_token: str) -> Optional[tuple[ChatUser, str]]:
        token_hash = hash_refresh_token(refresh_token)
        rows = await self.session.execute(
            select(ChatSession).where(ChatSession.refresh_token_hash == token_hash)
        )
        sess = rows.scalar_one_or_none()
        if not sess or sess.revoked_at is not None or sess.expires_at < _utcnow():
            return None
        user = await self.get_user(sess.chat_user_id)
        if not user or not user.is_active:
            return None
        sess.revoked_at = _utcnow()
        new_token, new_hash, expires = new_refresh_token()
        self.session.add(
            ChatSession(chat_user_id=user.id, refresh_token_hash=new_hash, expires_at=expires)
        )
        await self.session.commit()
        return user, new_token

    async def revoke_session(self, refresh_token: str) -> None:
        token_hash = hash_refresh_token(refresh_token)
        await self.session.execute(
            update(ChatSession)
            .where(ChatSession.refresh_token_hash == token_hash)
            .values(revoked_at=_utcnow())
        )
        await self.session.commit()

    async def _revoke_all_sessions(self, chat_user_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ChatSession)
            .where(ChatSession.chat_user_id == chat_user_id, ChatSession.revoked_at.is_(None))
            .values(revoked_at=_utcnow())
        )

    # --- треды и сообщения ---------------------------------------------------

    async def get_or_create_thread(self, chat_user_id: uuid.UUID) -> ChatThread:
        rows = await self.session.execute(
            select(ChatThread).where(ChatThread.chat_user_id == chat_user_id)
        )
        thread = rows.scalar_one_or_none()
        if thread:
            return thread
        thread = ChatThread(chat_user_id=chat_user_id)
        self.session.add(thread)
        await self.session.commit()
        await self.session.refresh(thread)
        return thread

    async def list_messages(
        self, thread_id: uuid.UUID, after_id: int = 0, limit: int = 50
    ) -> list[ChatMessage]:
        limit = max(1, min(limit, 200))
        rows = await self.session.execute(
            select(ChatMessage)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.id > after_id,
                ChatMessage.deleted_at.is_(None),
            )
            .order_by(ChatMessage.id.asc())
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def send_message(
        self,
        thread: ChatThread,
        sender_type: str,
        body: str,
        sender_user_id: Optional[uuid.UUID] = None,
        attachment_id: Optional[uuid.UUID] = None,
    ) -> ChatMessage:
        body = body.strip()
        if not body:
            raise ChatServiceError("Пустое сообщение.")
        if len(body) > MESSAGE_MAX_LEN:
            raise ChatServiceError(f"Сообщение длиннее {MESSAGE_MAX_LEN} символов.")
        msg = ChatMessage(
            thread_id=thread.id,
            sender_type=sender_type,
            sender_user_id=sender_user_id,
            body=body,
            attachment_id=attachment_id,
        )
        thread.last_message_at = _utcnow()
        if sender_type == "admin":
            thread.last_admin_user_id = sender_user_id
        if sender_type == "client" and thread.status == "resolved":
            thread.status = "open"
        self.session.add(msg)
        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def get_thread(self, thread_id: uuid.UUID) -> Optional[ChatThread]:
        return await self.session.get(ChatThread, thread_id)

    async def set_thread_status(self, thread_id: uuid.UUID, status: str) -> ChatThread:
        if status not in ("open", "resolved"):
            raise ChatServiceError("Недопустимый статус.")
        thread = await self.get_thread(thread_id)
        if not thread:
            raise ChatServiceError("Диалог не найден.")
        thread.status = status
        await self.session.commit()
        await self.session.refresh(thread)
        return thread

    async def admin_threads(self) -> list[dict]:
        """Диалоги для админки: тред + пользователь + превью последнего сообщения."""
        from app.services.client_store import client_store

        rows = await self.session.execute(
            select(ChatThread, ChatUser)
            .join(ChatUser, ChatUser.id == ChatThread.chat_user_id)
            .order_by(ChatThread.last_message_at.desc().nulls_last())
        )
        result = []
        for thread, user in rows.all():
            client_name: Optional[str] = None
            client_missing = False
            if user.client_id:
                record = client_store.get_record_raw(user.client_id)
                if record:
                    client_name = record.get("name") or user.client_id
                else:
                    client_missing = True
            preview_rows = await self.session.execute(
                select(ChatMessage.body, ChatMessage.sender_type)
                .where(ChatMessage.thread_id == thread.id, ChatMessage.deleted_at.is_(None))
                .order_by(ChatMessage.id.desc())
                .limit(1)
            )
            preview = preview_rows.first()
            result.append(
                {
                    "id": str(thread.id),
                    "status": thread.status,
                    "last_message_at": thread.last_message_at.isoformat()
                    if thread.last_message_at
                    else None,
                    "username": user.username,
                    "display_name": user.display_name,
                    "user_is_active": user.is_active,
                    "client_id": user.client_id,
                    "client_name": client_name,
                    "client_missing": client_missing,
                    "chat_user_id": str(user.id),
                    "last_preview": (preview[0][:80] if preview else None),
                    "last_sender": (preview[1] if preview else None),
                }
            )
        return result

    # --- вложения (ключи подключения) -------------------------------------------

    async def issue_key_attachment(
        self,
        thread: ChatThread,
        chat_user: ChatUser,
        created_by: Optional[uuid.UUID],
    ) -> ChatMessage:
        """Создаёт защищённое вложение с ключом привязанного VPN-клиента и
        сообщение в диалоге. Содержимое шифруется, живёт KEY_ATTACHMENT_TTL_HOURS."""
        from app.services.client_store import client_store

        if not chat_user.client_id:
            raise ChatServiceError("Сначала привяжите VPN-клиента к аккаунту чата.")
        detail = client_store.get_detail(chat_user.client_id)
        if not detail:
            raise ChatServiceError("Привязанный VPN-клиент не найден — перепривяжите.")
        config_text = detail.config_text
        vpn_link = detail.vpn_link
        if not config_text and not vpn_link:
            raise ChatServiceError(
                "У клиента нет выданного конфига (импортированный peer без ключей). "
                "Перевыпустите конфиг на странице клиента."
            )

        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", (detail.name or chat_user.username))[:40]
        attachment = ChatAttachment(
            thread_id=thread.id,
            chat_user_id=chat_user.id,
            kind="vpn_key",
            filename=f"{safe_name or 'vpn'}.conf",
            content_enc=encrypt(json.dumps({"config_text": config_text, "vpn_link": vpn_link})),
            expires_at=_utcnow() + timedelta(hours=KEY_ATTACHMENT_TTL_HOURS),
            created_by_user_id=created_by,
        )
        self.session.add(attachment)
        await self.session.flush()

        body = (
            "Ключ подключения готов. Скачайте файл конфигурации или отсканируйте QR "
            "в приложении AmneziaWG/AmneziaVPN.\n"
            f"Ссылка действует {KEY_ATTACHMENT_TTL_HOURS} ч."
        )
        return await self.send_message(
            thread, "admin", body, sender_user_id=created_by, attachment_id=attachment.id
        )

    async def get_attachment(self, attachment_id: uuid.UUID) -> Optional[ChatAttachment]:
        return await self.session.get(ChatAttachment, attachment_id)

    async def open_attachment_for_user(
        self, attachment_id: uuid.UUID, chat_user_id: uuid.UUID
    ) -> tuple[ChatAttachment, dict]:
        """Владелец + срок действия; возвращает вложение и расшифрованное содержимое."""
        attachment = await self.get_attachment(attachment_id)
        if not attachment or attachment.chat_user_id != chat_user_id:
            raise ChatServiceError("Вложение не найдено.")
        if attachment.expires_at < _utcnow():
            raise ChatServiceError(
                "Срок действия ссылки истёк. Попросите в чате выдать ключ заново."
            )
        try:
            content = json.loads(decrypt(attachment.content_enc) or "{}")
        except (ValueError, TypeError):
            raise ChatServiceError("Вложение повреждено.")
        attachment.downloads += 1
        await self.session.commit()
        return attachment, content

    async def attachments_map(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, ChatAttachment]:
        if not ids:
            return {}
        rows = await self.session.execute(
            select(ChatAttachment).where(ChatAttachment.id.in_(ids))
        )
        return {a.id: a for a in rows.scalars().all()}

    # --- push-подписки ----------------------------------------------------------

    async def save_push_subscription(
        self, chat_user_id: uuid.UUID, endpoint: str, p256dh: str, auth: str
    ) -> None:
        rows = await self.session.execute(
            select(ChatPushSubscription).where(ChatPushSubscription.endpoint == endpoint)
        )
        sub = rows.scalar_one_or_none()
        if sub:
            sub.chat_user_id = chat_user_id
            sub.p256dh = p256dh
            sub.auth = auth
            sub.last_seen_at = _utcnow()
        else:
            self.session.add(
                ChatPushSubscription(
                    chat_user_id=chat_user_id, endpoint=endpoint, p256dh=p256dh, auth=auth
                )
            )
        await self.session.commit()

    async def delete_push_subscription(self, endpoint: str) -> None:
        await self.session.execute(
            delete(ChatPushSubscription).where(ChatPushSubscription.endpoint == endpoint)
        )
        await self.session.commit()

    # --- retention -------------------------------------------------------------

    async def purge_old_messages(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = _utcnow() - timedelta(days=retention_days)
        result = await self.session.execute(
            delete(ChatMessage).where(ChatMessage.created_at < cutoff)
        )
        await self.session.execute(
            delete(ChatSession).where(ChatSession.expires_at < _utcnow() - timedelta(days=30))
        )
        # Просроченные ключи держим неделю (чтобы показать «истёк»), потом удаляем
        await self.session.execute(
            delete(ChatAttachment).where(ChatAttachment.expires_at < _utcnow() - timedelta(days=7))
        )
        await self.session.commit()
        return result.rowcount or 0

    async def counts(self) -> dict:
        users = await self.session.scalar(select(func.count(ChatUser.id)))
        threads = await self.session.scalar(select(func.count(ChatThread.id)))
        return {"users": int(users or 0), "threads": int(threads or 0)}
