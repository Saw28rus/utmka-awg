"""Доставка перевыпущенных VPN-ключей в чат (после ротации маскировки).

Вызывать только из синхронного потока (apply_rotation идёт через to_thread):
внутри — asyncio.run со своей сессией БД.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("utmka.chat.delivery")

ROTATION_BODY = (
    "Маскировка VPN обновлена. Старый ключ больше не работает — "
    "откройте вложение и импортируйте новый конфиг в AmneziaWG / AmneziaVPN."
)


def deliver_keys_for_clients(
    client_ids: list[str],
    *,
    reason: str = "reissue",
) -> int:
    """Отправить актуальный ключ каждому чат-пользователю, привязанному к client_ids.

    Возвращает число успешно отправленных вложений. Ошибки по отдельным
    диалогам глотаются — ротация сервера уже прошла.
    """
    ids = [c for c in dict.fromkeys(client_ids) if c]
    if not ids:
        return 0

    from app.services.client_store import client_store

    expanded: list[str] = []
    for cid in ids:
        expanded.append(cid)
        rec = client_store.get_record_raw(cid) or {}
        other = rec.get("fallback_client_id") or rec.get("fallback_of_client_id")
        if other:
            expanded.append(other)
    ids = [c for c in dict.fromkeys(expanded) if c]

    async def _run() -> int:
        from app.db.session import AsyncSessionLocal
        from app.services.chat_service import ChatService, ChatServiceError
        from app.services import push_service

        delivered = 0
        body = ROTATION_BODY if reason == "masking_rotation" else None
        async with AsyncSessionLocal() as session:
            svc = ChatService(session)
            pairs = await svc.users_linked_to_clients(ids)
            for user, thread in pairs:
                try:
                    await svc.issue_key_attachment(
                        thread, user, created_by=None, body=body
                    )
                    delivered += 1
                except ChatServiceError as exc:
                    logger.info("chat key skip user=%s: %s", user.username, exc)
                    continue
                try:
                    await push_service.notify(user.id, "key")
                except Exception:  # noqa: BLE001
                    logger.debug("push after key delivery failed", exc_info=True)
        return delivered

    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001
        logger.exception("доставка ключей в чат не удалась")
        return 0
