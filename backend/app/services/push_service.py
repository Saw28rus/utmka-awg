"""Web Push (CH5): VAPID-ключи + рассылка уведомлений клиентам чата.

Ключи генерируются один раз и хранятся в panel_settings (приватный — шифрованным).
Отправка синхронная (pywebpush/requests), поэтому вызывается из threadpool,
а «мёртвые» подписки (404/410) автоматически удаляются.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import delete, select

from app.core.crypto import decrypt, encrypt
from app.db.session import AsyncSessionLocal
from app.models.chat import ChatPushSubscription
from app.services.panel_settings_service import PanelSettingsService

logger = logging.getLogger(__name__)

PUBLIC_KEY = "chat_vapid_public"
PRIVATE_ENC = "chat_vapid_private_enc"

TITLE = "Чат поддержки"
_BODIES = {
    "message": "Новое сообщение от поддержки",
    "invoice": "Поддержка отправила счёт на оплату",
    "key": "Поддержка отправила ключ подключения",
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def ensure_vapid_keys(settings_svc: PanelSettingsService) -> tuple[str, str]:
    """Возвращает (public_b64url, private_pem); генерирует один раз при отсутствии."""
    public = await settings_svc.get(PUBLIC_KEY)
    private_enc = await settings_svc.get(PRIVATE_ENC)
    private_pem = decrypt(private_enc) if private_enc else None
    if public and private_pem:
        return public, private_pem

    priv = ec.generate_private_key(ec.SECP256R1())
    private_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    raw_pub = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public = _b64url(raw_pub)
    await settings_svc.set(PUBLIC_KEY, public)
    await settings_svc.set(PRIVATE_ENC, encrypt(private_pem) or "")
    return public, private_pem


async def get_public_key(settings_svc: PanelSettingsService) -> Optional[str]:
    try:
        public, _ = await ensure_vapid_keys(settings_svc)
        return public
    except Exception as exc:  # noqa: BLE001
        logger.warning("vapid keygen failed: %s", exc)
        return None


def _send_one(
    endpoint: str, p256dh: str, auth: str, payload: str, private_pem: str, sub_claim: str
) -> Optional[bool]:
    """True — доставлено; False — подписку нужно удалить; None — временная ошибка."""
    try:
        from py_vapid import Vapid02
        from pywebpush import WebPushException, webpush

        vapid = Vapid02.from_pem(private_pem.encode("utf8"))
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=payload,
            vapid_private_key=vapid,
            vapid_claims={"sub": sub_claim},
            ttl=86400,
            headers={"Urgency": "high"},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in (404, 410):
            return False
        logger.warning("web push failed (%s): %s", status, exc)
        return None


async def send_to_user(chat_user_id: uuid.UUID, title: str, body: str, url: str = "/") -> None:
    async with AsyncSessionLocal() as db:
        settings_svc = PanelSettingsService(db)
        try:
            _, private_pem = await ensure_vapid_keys(settings_svc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("no vapid keys, skip push: %s", exc)
            return
        domain = (await settings_svc.get("chat_domain") or "").strip()
        sub_claim = f"mailto:admin@{domain}" if domain else "mailto:admin@localhost"

        rows = await db.execute(
            select(ChatPushSubscription).where(ChatPushSubscription.chat_user_id == chat_user_id)
        )
        subs = list(rows.scalars().all())
        if not subs:
            return

        payload = json.dumps({"title": title, "body": body, "url": url})
        dead: list[uuid.UUID] = []
        for s in subs:
            result = await asyncio.to_thread(
                _send_one, s.endpoint, s.p256dh, s.auth, payload, private_pem, sub_claim
            )
            if result is False:
                dead.append(s.id)
        if dead:
            await db.execute(
                delete(ChatPushSubscription).where(ChatPushSubscription.id.in_(dead))
            )
            await db.commit()


async def notify(chat_user_id: uuid.UUID, kind: str = "message", url: str = "/") -> None:
    """Удобная обёртка: нейтральный текст без чувствительных данных."""
    body = _BODIES.get(kind, _BODIES["message"])
    await send_to_user(chat_user_id, TITLE, body, url)
