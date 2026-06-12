"""Интеграция ЮKassa — проверка ключей и работа с API v3."""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

YOOKASSA_API_BASE = "https://api.yookassa.ru/v3"


def _auth_header(shop_id: str, secret_key: str) -> str:
    token = base64.b64encode(f"{shop_id}:{secret_key}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def mask_secret_key(secret_key: str) -> str:
    if len(secret_key) <= 5:
        return secret_key
    tail = min(len(secret_key) - 8, 20)
    return secret_key[:8] + ("•" * tail)


def _format_expires_at(expires_at: datetime) -> str:
    """ISO 8601 в UTC, как ожидает ЮKassa (например 2024-10-18T10:51:18Z)."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    utc = expires_at.astimezone(timezone.utc).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _yookassa_error_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return ""
    parts = [
        payload.get("description") or payload.get("type") or "",
        payload.get("parameter") or "",
    ]
    return " ".join(p for p in parts if p).strip()


@dataclass
class YooKassaVerifyResult:
    ok: bool
    error: Optional[str] = None


async def verify_credentials(shop_id: str, secret_key: str) -> YooKassaVerifyResult:
    """Проверка shop_id и secret_key через GET /v3/payments?limit=1 (Basic Auth)."""
    shop_id = shop_id.strip()
    secret_key = secret_key.strip()
    if not shop_id or not secret_key:
        return YooKassaVerifyResult(ok=False, error="Укажите shop ID и секретный ключ.")

    headers = {"Authorization": _auth_header(shop_id, secret_key)}
    url = f"{YOOKASSA_API_BASE}/payments"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers, params={"limit": 1})
    except httpx.TimeoutException:
        return YooKassaVerifyResult(ok=False, error="Таймаут при обращении к ЮKassa. Проверьте интернет.")
    except httpx.RequestError as exc:
        return YooKassaVerifyResult(ok=False, error=f"Ошибка сети: {exc}")

    if resp.status_code == 200:
        return YooKassaVerifyResult(ok=True)
    if resp.status_code == 401:
        return YooKassaVerifyResult(
            ok=False,
            error="Неверный идентификатор магазина или секретный ключ.",
        )
    return YooKassaVerifyResult(
        ok=False,
        error=f"ЮKassa вернула ошибку ({resp.status_code}).",
    )


@dataclass
class CreateInvoiceResult:
    ok: bool
    invoice_id: Optional[str] = None
    pay_url: Optional[str] = None
    status: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


@dataclass
class InvoiceStatusResult:
    ok: bool
    status: Optional[str] = None
    cancellation_reason: Optional[str] = None
    error: Optional[str] = None


async def create_invoice(
    *,
    shop_id: str,
    secret_key: str,
    amount_kopecks: int,
    description: str,
    expires_at: datetime,
    currency: str = "RUB",
    metadata: Optional[dict[str, Any]] = None,
    cart_description: Optional[str] = None,
) -> CreateInvoiceResult:
    """Создаёт счёт через POST /v3/invoices (delivery_method type=self) и возвращает ссылку оплаты."""
    shop_id = shop_id.strip()
    secret_key = secret_key.strip()
    if not shop_id or not secret_key:
        return CreateInvoiceResult(ok=False, error="ЮKassa не подключена.")

    amount_value = f"{amount_kopecks / 100:.2f}"
    description = (description or "Оплата").strip()[:128]
    cart_description = (cart_description or description)[:128]
    amount_obj = {"value": amount_value, "currency": currency}

    body: dict[str, Any] = {
        "payment_data": {
            "amount": amount_obj,
            "capture": True,
            "description": description,
        },
        "cart": [
            {
                "description": cart_description,
                "quantity": 1.0,
                "price": amount_obj,
            }
        ],
        "delivery_method_data": {"type": "self"},
        "expires_at": _format_expires_at(expires_at),
        "locale": "ru_RU",
        "description": description,
    }
    if metadata:
        body["payment_data"]["metadata"] = metadata
        body["metadata"] = metadata

    headers = {
        "Authorization": _auth_header(shop_id, secret_key),
        "Idempotence-Key": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    url = f"{YOOKASSA_API_BASE}/invoices"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return CreateInvoiceResult(ok=False, error="Таймаут при обращении к ЮKassa.")
    except httpx.RequestError as exc:
        return CreateInvoiceResult(ok=False, error=f"Ошибка сети: {exc}")

    if resp.status_code not in (200, 201):
        detail = _yookassa_error_detail(resp)
        if resp.status_code == 401:
            return CreateInvoiceResult(ok=False, error="ЮKassa отклонила ключи (401).")
        return CreateInvoiceResult(
            ok=False,
            error=f"ЮKassa вернула ошибку ({resp.status_code}). {detail}".strip(),
        )

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return CreateInvoiceResult(ok=False, error="Некорректный ответ ЮKassa.")

    delivery = data.get("delivery_method") or {}
    pay_url = delivery.get("url")
    if not pay_url:
        return CreateInvoiceResult(ok=False, error="ЮKassa не вернула ссылку для оплаты.")

    return CreateInvoiceResult(
        ok=True,
        invoice_id=data.get("id"),
        pay_url=pay_url,
        status=data.get("status"),
        expires_at=data.get("expires_at"),
    )


async def get_invoice_status(
    *,
    shop_id: str,
    secret_key: str,
    invoice_id: str,
) -> InvoiceStatusResult:
    """Опрашивает статус счёта через GET /v3/invoices/{id}."""
    shop_id = shop_id.strip()
    secret_key = secret_key.strip()
    if not shop_id or not secret_key or not invoice_id:
        return InvoiceStatusResult(ok=False, error="Недостаточно данных для запроса.")

    headers = {"Authorization": _auth_header(shop_id, secret_key)}
    url = f"{YOOKASSA_API_BASE}/invoices/{invoice_id}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return InvoiceStatusResult(ok=False, error="Таймаут при обращении к ЮKassa.")
    except httpx.RequestError as exc:
        return InvoiceStatusResult(ok=False, error=f"Ошибка сети: {exc}")

    if resp.status_code != 200:
        return InvoiceStatusResult(ok=False, error=f"ЮKassa вернула ошибку ({resp.status_code}).")

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return InvoiceStatusResult(ok=False, error="Некорректный ответ ЮKassa.")

    cancellation = data.get("cancellation_details") or {}
    return InvoiceStatusResult(
        ok=True,
        status=data.get("status"),
        cancellation_reason=cancellation.get("reason"),
    )
