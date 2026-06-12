"""Бизнес-логика счетов ЮKassa: создание, статусы, шаблоны, авто-продление клиента."""

from __future__ import annotations

import asyncio
import calendar
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceTemplate
from app.services.awg_enforce import enforce_server_by_id
from app.services.client_store import client_store
from app.services.panel_settings_service import PanelSettingsService
from app.services.yookassa import create_invoice, get_invoice_status

DEFAULT_TEMPLATE_BODY = (
    "Здравствуйте, {{имя}}! Оплата за {{услуга}} на сумму {{сумма}}. "
    "Ссылка для оплаты: {{ссылка}}"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SelfPaymentError(Exception):
    """Ошибка самооплаты клиентом с конкретным HTTP-кодом."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


SELF_PAY_MONTHLY_LIMIT = 3


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_amount(amount_kopecks: int) -> str:
    rubles = amount_kopecks / 100
    if amount_kopecks % 100 == 0:
        return f"{int(rubles)} ₽"
    return f"{rubles:.2f} ₽"


def render_template(
    body: str,
    *,
    client_name: str,
    service: Optional[str],
    amount_kopecks: Optional[int],
    pay_url: str,
    period_label: Optional[str] = None,
) -> str:
    amount_text = format_amount(amount_kopecks) if amount_kopecks is not None else ""
    replacements = {
        "{{имя}}": client_name or "",
        "{{услуга}}": service or "",
        "{{сумма}}": amount_text,
        "{{ссылка}}": pay_url or "",
        "{{период}}": period_label or "",
    }
    text = body
    for token, value in replacements.items():
        text = text.replace(token, value)
    return text


class InvoiceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -- credentials -------------------------------------------------------

    async def _yookassa_creds(self) -> Optional[tuple[str, str]]:
        settings = PanelSettingsService(self.session)
        shop_id = (await settings.get("yookassa_shop_id") or "").strip()
        secret = await settings.get_yookassa_secret_key()
        if not shop_id or not secret:
            return None
        return shop_id, secret

    # -- creation ----------------------------------------------------------

    async def create_for_clients(
        self,
        *,
        client_ids: list[str],
        amount: float,
        service: Optional[str],
        template_id: Optional[str],
        message_override: Optional[str],
        period_label: Optional[str],
        expires_days: int,
        extend_months: int,
        created_by_user_id: Optional[uuid.UUID],
        source: str = "admin",
        metadata_extra: Optional[dict] = None,
    ) -> list[dict]:
        creds = await self._yookassa_creds()
        if not creds:
            raise ValueError("ЮKassa не подключена. Подключите её в настройках.")
        shop_id, secret = creds

        template_body = message_override
        if template_body is None and template_id:
            template = await self.get_template(template_id)
            template_body = template.body if template else None
        if template_body is None:
            template_body = DEFAULT_TEMPLATE_BODY

        amount_kopecks = int(round(amount * 100))
        if amount_kopecks <= 0:
            raise ValueError("Сумма должна быть больше нуля.")

        expires_at = _utcnow() + timedelta(days=expires_days)
        results: list[dict] = []

        for client_id in client_ids:
            detail = client_store.get_detail(client_id)
            if not detail:
                results.append(
                    {
                        "client_id": client_id,
                        "client_name": client_id,
                        "ok": False,
                        "error": "Клиент не найден.",
                    }
                )
                continue

            description = f"Оплата: {service}".strip() if service else f"Оплата: {detail.name}"
            cart_description = service or "Доступ"

            metadata = {
                "client_id": str(client_id),
                "client_name": detail.name,
                "cms_name": "utmka_awg",
            }
            if metadata_extra:
                metadata.update({k: str(v) for k, v in metadata_extra.items()})

            yk = await create_invoice(
                shop_id=shop_id,
                secret_key=secret,
                amount_kopecks=amount_kopecks,
                description=description,
                expires_at=expires_at,
                cart_description=cart_description,
                metadata=metadata,
            )

            if not yk.ok or not yk.pay_url:
                results.append(
                    {
                        "client_id": client_id,
                        "client_name": detail.name,
                        "ok": False,
                        "error": yk.error or "Не удалось создать счёт.",
                    }
                )
                continue

            message_text = render_template(
                template_body,
                client_name=detail.name,
                service=service,
                amount_kopecks=amount_kopecks,
                pay_url=yk.pay_url,
                period_label=period_label,
            )

            invoice = Invoice(
                client_id=str(client_id),
                client_name=detail.name,
                server_id=detail.server_id,
                service=service,
                amount_kopecks=amount_kopecks,
                currency="RUB",
                description=description,
                message_text=message_text,
                yk_invoice_id=yk.invoice_id,
                pay_url=yk.pay_url,
                status="pending",
                expires_at=_parse_iso(yk.expires_at) or expires_at,
                extend_months=extend_months,
                created_by_user_id=created_by_user_id,
                source=source,
            )
            self.session.add(invoice)
            await self.session.flush()
            results.append(
                {
                    "client_id": client_id,
                    "client_name": detail.name,
                    "ok": True,
                    "invoice_id": str(invoice.id),
                    "pay_url": yk.pay_url,
                    "message_text": message_text,
                    "expires_at": (invoice.expires_at.isoformat() if invoice.expires_at else None),
                    "amount_kopecks": amount_kopecks,
                }
            )

        await self.session.commit()
        return results

    # -- self-service payment (CH10) --------------------------------------

    async def yookassa_available(self) -> bool:
        return await self._yookassa_creds() is not None

    async def self_pay_count_this_month(self, client_id: str) -> int:
        month_start = _utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.client_id == client_id,
                Invoice.source == "client_self",
                Invoice.deleted_at.is_(None),
                Invoice.created_at >= month_start,
            )
        )
        return int(result.scalar() or 0)

    async def _active_self_pending(self, client_id: str) -> Optional[Invoice]:
        result = await self.session.execute(
            select(Invoice)
            .where(
                Invoice.client_id == client_id,
                Invoice.source == "client_self",
                Invoice.status == "pending",
                Invoice.deleted_at.is_(None),
                Invoice.expires_at > _utcnow(),
            )
            .order_by(Invoice.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_self_payment(self, *, chat_user) -> dict:
        """Самооплата клиентом: счёт по тарифу VPN-клиента, ссылка на 1 день,
        не чаще 3 раз в месяц. client_id берётся ТОЛЬКО из chat_user."""
        client_id = chat_user.client_id
        if not client_id:
            raise SelfPaymentError("VPN-клиент не привязан. Обратитесь в поддержку.", 400)
        detail = client_store.get_detail(client_id)
        if not detail:
            raise SelfPaymentError("VPN-клиент не найден. Обратитесь в поддержку.", 404)
        if detail.billing_mode != "paid" or not detail.billing_amount_kopecks:
            raise SelfPaymentError("Для этого доступа самостоятельная оплата недоступна.", 400)
        if not await self.yookassa_available():
            raise SelfPaymentError("Оплата временно недоступна.", 400)

        existing = await self._active_self_pending(client_id)
        if existing and existing.pay_url:
            return {
                "invoice_id": str(existing.id),
                "pay_url": existing.pay_url,
                "expires_at": existing.expires_at.isoformat() if existing.expires_at else None,
                "amount_kopecks": existing.amount_kopecks,
                "reused": True,
                "message_text": existing.message_text,
            }

        if await self.self_pay_count_this_month(client_id) >= SELF_PAY_MONTHLY_LIMIT:
            raise SelfPaymentError(
                f"Лимит самостоятельных оплат на этот месяц исчерпан "
                f"({SELF_PAY_MONTHLY_LIMIT}). Обратитесь в поддержку.",
                429,
            )

        months = detail.billing_period_months or 1
        period_label = "1 месяц" if months == 1 else f"{months} мес."
        results = await self.create_for_clients(
            client_ids=[client_id],
            amount=detail.billing_amount_kopecks / 100,
            service="Продление VPN",
            template_id=None,
            message_override=None,
            period_label=period_label,
            expires_days=1,
            extend_months=months,
            created_by_user_id=None,
            source="client_self",
            metadata_extra={"chat_user_id": str(chat_user.id), "source": "client_self"},
        )
        res = results[0] if results else {}
        if not res.get("ok"):
            raise SelfPaymentError(res.get("error") or "Не удалось создать счёт.", 400)
        return {
            "invoice_id": res["invoice_id"],
            "pay_url": res["pay_url"],
            "expires_at": res.get("expires_at"),
            "amount_kopecks": res.get("amount_kopecks"),
            "reused": False,
            "message_text": res.get("message_text"),
        }

    # -- listing -----------------------------------------------------------

    async def list_by_tab(self, tab: str) -> list[Invoice]:
        stmt = select(Invoice).order_by(Invoice.created_at.desc())
        if tab == "deleted":
            stmt = stmt.where(Invoice.deleted_at.is_not(None))
        else:
            stmt = stmt.where(Invoice.deleted_at.is_(None))
            if tab == "issued":
                stmt = stmt.where(Invoice.status == "pending")
            elif tab == "paid":
                stmt = stmt.where(Invoice.status == "succeeded")
            elif tab == "overdue":
                stmt = stmt.where(Invoice.status.in_(["expired", "canceled"]))
            # tab == "all" / иное — без доп. фильтра
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, invoice_id: str) -> Optional[Invoice]:
        try:
            uid = uuid.UUID(invoice_id)
        except ValueError:
            return None
        return await self.session.get(Invoice, uid)

    async def soft_delete(self, invoice_id: str) -> Optional[Invoice]:
        invoice = await self.get(invoice_id)
        if not invoice:
            return None
        if invoice.deleted_at is None:
            invoice.deleted_at = _utcnow()
            await self.session.commit()
            await self.session.refresh(invoice)
        return invoice

    async def restore(self, invoice_id: str) -> Optional[Invoice]:
        invoice = await self.get(invoice_id)
        if not invoice:
            return None
        invoice.deleted_at = None
        await self.session.commit()
        await self.session.refresh(invoice)
        return invoice

    # -- templates ---------------------------------------------------------

    async def list_templates(self) -> list[InvoiceTemplate]:
        result = await self.session.execute(
            select(InvoiceTemplate).order_by(InvoiceTemplate.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_template(self, template_id: str) -> Optional[InvoiceTemplate]:
        try:
            uid = uuid.UUID(template_id)
        except ValueError:
            return None
        return await self.session.get(InvoiceTemplate, uid)

    async def create_template(
        self,
        *,
        title: str,
        body: str,
        default_service: Optional[str],
        default_amount: Optional[float],
    ) -> InvoiceTemplate:
        template = InvoiceTemplate(
            title=title.strip(),
            body=body,
            default_service=default_service,
            default_amount_kopecks=int(round(default_amount * 100)) if default_amount else None,
        )
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def update_template(
        self,
        template_id: str,
        *,
        title: str,
        body: str,
        default_service: Optional[str],
        default_amount: Optional[float],
    ) -> Optional[InvoiceTemplate]:
        template = await self.get_template(template_id)
        if not template:
            return None
        template.title = title.strip()
        template.body = body
        template.default_service = default_service
        template.default_amount_kopecks = int(round(default_amount * 100)) if default_amount else None
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def delete_template(self, template_id: str) -> bool:
        template = await self.get_template(template_id)
        if not template:
            return False
        await self.session.delete(template)
        await self.session.commit()
        return True

    async def ensure_default_template(self) -> None:
        existing = await self.session.execute(select(InvoiceTemplate.id).limit(1))
        if existing.first() is not None:
            return
        self.session.add(
            InvoiceTemplate(
                title="Счёт за доступ",
                body=DEFAULT_TEMPLATE_BODY,
            )
        )
        await self.session.commit()

    # -- status sync + auto extend ----------------------------------------

    async def sync_pending(self) -> dict[str, int]:
        creds = await self._yookassa_creds()
        if not creds:
            return {"checked": 0, "updated": 0, "paid": 0}
        shop_id, secret = creds

        result = await self.session.execute(
            select(Invoice).where(
                Invoice.status == "pending",
                Invoice.deleted_at.is_(None),
                Invoice.yk_invoice_id.is_not(None),
            )
        )
        invoices = list(result.scalars().all())

        checked = 0
        updated = 0
        paid = 0
        now = _utcnow()

        for invoice in invoices:
            checked += 1
            try:
                status = await get_invoice_status(
                    shop_id=shop_id,
                    secret_key=secret,
                    invoice_id=invoice.yk_invoice_id or "",
                )
            except Exception:  # noqa: BLE001
                continue

            if not status.ok or not status.status:
                # Локально помечаем просроченным, если срок давно прошёл.
                if invoice.expires_at and invoice.expires_at < now:
                    invoice.status = "expired"
                    updated += 1
                continue

            if status.status == "succeeded":
                invoice.status = "succeeded"
                invoice.paid_at = now
                updated += 1
                paid += 1
                await self._extend_client(invoice)
            elif status.status == "canceled":
                if status.cancellation_reason == "invoice_expired":
                    invoice.status = "expired"
                else:
                    invoice.status = "canceled"
                invoice.cancellation_reason = status.cancellation_reason
                updated += 1
            else:
                if invoice.expires_at and invoice.expires_at < now:
                    invoice.status = "expired"
                    updated += 1

        if updated:
            await self.session.commit()
        return {"checked": checked, "updated": updated, "paid": paid}

    async def _extend_client(self, invoice: Invoice) -> None:
        if invoice.client_extended or not invoice.client_id:
            return
        detail = client_store.get_detail(invoice.client_id)
        if not detail:
            invoice.client_extended = True
            return

        current = _parse_iso(detail.expires_at)
        now = _utcnow()
        base = current if (current and current > now) else now
        new_expiry = _add_months(base, invoice.extend_months or 1)

        try:
            client_store.update_limits(
                invoice.client_id,
                changes={"expires_at": new_expiry.isoformat(), "status": "active"},
            )
            await asyncio.to_thread(enforce_server_by_id, detail.server_id)
        except Exception:  # noqa: BLE001
            # Не блокируем фиксацию оплаты, если enforce временно недоступен.
            pass

        invoice.client_extended = True
