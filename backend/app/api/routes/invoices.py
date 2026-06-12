import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_client_manager
from app.db.session import get_db
from app.models.invoice import Invoice, InvoiceTemplate
from app.schemas.auth import CurrentUser
from app.schemas.invoices import (
    InvoiceCreateRequest,
    InvoiceCreateResult,
    InvoiceCreateResultItem,
    InvoicePreviewRequest,
    InvoicePreviewResponse,
    InvoiceRead,
    InvoiceSyncResult,
    InvoiceTemplateRead,
    InvoiceTemplateWrite,
)
from app.services.audit_service import AuditService
from app.services.invoice_service import InvoiceService, render_template

router = APIRouter()


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _invoice_read(inv: Invoice) -> InvoiceRead:
    return InvoiceRead(
        id=str(inv.id),
        client_id=inv.client_id,
        client_name=inv.client_name,
        server_id=inv.server_id,
        service=inv.service,
        amount=inv.amount_kopecks / 100,
        currency=inv.currency,
        description=inv.description,
        message_text=inv.message_text,
        pay_url=inv.pay_url,
        status=inv.status,
        cancellation_reason=inv.cancellation_reason,
        expires_at=_iso(inv.expires_at),
        paid_at=_iso(inv.paid_at),
        client_extended=inv.client_extended,
        created_at=_iso(inv.created_at),
        deleted_at=_iso(inv.deleted_at),
    )


def _template_read(tpl: InvoiceTemplate) -> InvoiceTemplateRead:
    return InvoiceTemplateRead(
        id=str(tpl.id),
        title=tpl.title,
        body=tpl.body,
        default_service=tpl.default_service,
        default_amount=(tpl.default_amount_kopecks / 100) if tpl.default_amount_kopecks else None,
        created_at=_iso(tpl.created_at),
    )


# -- templates (литеральные пути регистрируем раньше /{invoice_id}) --------


@router.get("/templates", response_model=list[InvoiceTemplateRead])
async def list_templates(
    _: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceTemplateRead]:
    svc = InvoiceService(db)
    await svc.ensure_default_template()
    return [_template_read(t) for t in await svc.list_templates()]


@router.post("/templates", response_model=InvoiceTemplateRead)
async def create_template(
    payload: InvoiceTemplateWrite,
    _: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> InvoiceTemplateRead:
    svc = InvoiceService(db)
    tpl = await svc.create_template(
        title=payload.title,
        body=payload.body,
        default_service=payload.default_service,
        default_amount=payload.default_amount,
    )
    return _template_read(tpl)


@router.put("/templates/{template_id}", response_model=InvoiceTemplateRead)
async def update_template(
    template_id: str,
    payload: InvoiceTemplateWrite,
    _: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> InvoiceTemplateRead:
    svc = InvoiceService(db)
    tpl = await svc.update_template(
        template_id,
        title=payload.title,
        body=payload.body,
        default_service=payload.default_service,
        default_amount=payload.default_amount,
    )
    if not tpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден.")
    return _template_read(tpl)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    _: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = InvoiceService(db)
    if not await svc.delete_template(template_id):
        raise HTTPException(status_code=404, detail="Шаблон не найден.")
    return {"ok": True}


@router.post("/templates/preview", response_model=InvoicePreviewResponse)
async def preview_template(
    payload: InvoicePreviewRequest,
    _: CurrentUser = Depends(require_client_manager),
) -> InvoicePreviewResponse:
    amount_kopecks = int(round(payload.amount * 100)) if payload.amount is not None else None
    text = render_template(
        payload.body,
        client_name=payload.client_name,
        service=payload.service,
        amount_kopecks=amount_kopecks,
        pay_url="https://yookassa.ru/my/i/example/l",
        period_label=payload.period_label,
    )
    return InvoicePreviewResponse(text=text)


# -- invoices --------------------------------------------------------------


@router.get("", response_model=list[InvoiceRead])
async def list_invoices(
    tab: str = Query(default="all"),
    _: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceRead]:
    svc = InvoiceService(db)
    return [_invoice_read(i) for i in await svc.list_by_tab(tab)]


@router.post("", response_model=InvoiceCreateResult)
async def create_invoices(
    payload: InvoiceCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> InvoiceCreateResult:
    svc = InvoiceService(db)
    try:
        results = await svc.create_for_clients(
            client_ids=payload.client_ids,
            amount=payload.amount,
            service=payload.service,
            template_id=payload.template_id,
            message_override=payload.message_override,
            period_label=payload.period_label,
            expires_days=payload.expires_days,
            extend_months=payload.extend_months,
            created_by_user_id=uuid.UUID(user.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    items = [InvoiceCreateResultItem(**r) for r in results]
    created = sum(1 for r in results if r["ok"])
    failed = len(results) - created

    audit = AuditService(db)
    await audit.log(
        "invoices_created",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="invoice",
        detail={"created": created, "failed": failed, "amount": payload.amount},
        ip=client_ip(request),
    )
    return InvoiceCreateResult(created=created, failed=failed, items=items)


@router.post("/refresh", response_model=InvoiceSyncResult)
async def refresh_invoices(
    _: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> InvoiceSyncResult:
    svc = InvoiceService(db)
    data = await svc.sync_pending()
    return InvoiceSyncResult(**data)


@router.delete("/{invoice_id}", response_model=InvoiceRead)
async def delete_invoice(
    invoice_id: str,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> InvoiceRead:
    svc = InvoiceService(db)
    invoice = await svc.soft_delete(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Счёт не найден.")
    audit = AuditService(db)
    await audit.log(
        "invoice_deleted",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="invoice",
        target_id=invoice_id,
        ip=client_ip(request),
    )
    return _invoice_read(invoice)


@router.post("/{invoice_id}/restore", response_model=InvoiceRead)
async def restore_invoice(
    invoice_id: str,
    _: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> InvoiceRead:
    svc = InvoiceService(db)
    invoice = await svc.restore(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Счёт не найден.")
    return _invoice_read(invoice)
