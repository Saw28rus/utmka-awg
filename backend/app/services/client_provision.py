"""Единая точка создания VPN-клиента (без HTTP/audit слоя).

Вынесено из api/routes/clients.py, чтобы и обычный роут `POST /clients`, и
операторский чат-эндпоинт (`provision-client`) создавали клиента ОДИНАКОВО:
движок протокола (awg2/xray/...) или Xray-каскад + применение billing-полей.

Слой выше отвечает за права, audit и HTTP-ошибки. Здесь — только доменная логика.
Исключения пробрасываются как есть (ClientCreateError / XrayCascadeError и т.п.).
"""

from __future__ import annotations

import asyncio

from app.schemas.clients import ClientCreate, ClientDetail
from app.services.client_store import client_store
from app.services.protocol_engine import ClientSpec, get_engine


class ProvisionError(ValueError):
    """Бизнес-ошибка провижининга (например, противоречивый billing)."""


async def provision_client(payload: ClientCreate) -> ClientDetail:
    """Создать клиента по payload и применить billing. Возвращает ClientDetail.

    Поведение идентично прежнему телу clients.py:create_client (до audit-лога).
    """
    protocol = (payload.protocol or "awg2").lower()

    if protocol == "xray_cascade":
        # Каскадная выдача (chain): server_id — entry (РФ) со своим Xray. Клиент
        # обычный (UUID/ключи на entry), раздвоение РФ/заграница делает серверный
        # routing на entry.
        from app.services.xray_cascade import create_xray_cascade_client

        detail = await asyncio.to_thread(
            create_xray_cascade_client,
            payload.server_id,
            payload.name.strip(),
            format=payload.format,
            traffic_limit_bytes=payload.traffic_limit_bytes,
            expires_at=payload.expires_at,
            fingerprint=payload.fingerprint,
        )
    else:
        spec = ClientSpec(
            server_id=payload.server_id,
            name=payload.name.strip(),
            protocol=protocol,
            format=payload.format,
            traffic_limit_bytes=payload.traffic_limit_bytes,
            expires_at=payload.expires_at,
            keepalive=payload.keepalive,
            link_host=(payload.link_host or "").strip() or None,
            fingerprint=(payload.fingerprint or "").strip() or None,
        )
        detail = await asyncio.to_thread(get_engine(protocol).create_client, spec)

    if payload.billing_mode == "paid" and not payload.billing_amount_kopecks:
        raise ProvisionError("Для платного тарифа укажите сумму.")
    billing_changes = {
        "billing_mode": payload.billing_mode,
        "billing_amount_kopecks": payload.billing_amount_kopecks
        if payload.billing_mode == "paid"
        else None,
        "billing_period_months": payload.billing_period_months,
    }
    refreshed = client_store.update_limits(detail.id, changes=billing_changes)
    if refreshed:
        detail = refreshed

    return detail
