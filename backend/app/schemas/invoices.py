from typing import Optional

from pydantic import BaseModel, Field


class InvoiceRead(BaseModel):
    id: str
    client_id: Optional[str] = None
    client_name: str
    server_id: Optional[str] = None
    service: Optional[str] = None
    amount: float
    currency: str
    description: Optional[str] = None
    message_text: Optional[str] = None
    pay_url: Optional[str] = None
    status: str
    cancellation_reason: Optional[str] = None
    expires_at: Optional[str] = None
    paid_at: Optional[str] = None
    client_extended: bool = False
    created_at: Optional[str] = None
    deleted_at: Optional[str] = None


class InvoiceCreateRequest(BaseModel):
    client_ids: list[str] = Field(min_length=1)
    amount: float = Field(gt=0, le=1_000_000)
    service: Optional[str] = Field(default=None, max_length=200)
    template_id: Optional[str] = None
    message_override: Optional[str] = Field(default=None, max_length=4000)
    period_label: Optional[str] = Field(default=None, max_length=120)
    expires_days: int = Field(default=3, ge=1, le=60)
    extend_months: int = Field(default=1, ge=1, le=24)


class InvoiceCreateResultItem(BaseModel):
    client_id: str
    client_name: str
    ok: bool
    invoice_id: Optional[str] = None
    pay_url: Optional[str] = None
    message_text: Optional[str] = None
    error: Optional[str] = None


class InvoiceCreateResult(BaseModel):
    created: int
    failed: int
    items: list[InvoiceCreateResultItem]


class InvoiceTemplateRead(BaseModel):
    id: str
    title: str
    body: str
    default_service: Optional[str] = None
    default_amount: Optional[float] = None
    created_at: Optional[str] = None


class InvoiceTemplateWrite(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4000)
    default_service: Optional[str] = Field(default=None, max_length=200)
    default_amount: Optional[float] = Field(default=None, ge=0, le=1_000_000)


class InvoicePreviewRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    client_name: str = Field(default="Иван Иванов", max_length=255)
    service: Optional[str] = Field(default=None, max_length=200)
    amount: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    period_label: Optional[str] = Field(default=None, max_length=120)


class InvoicePreviewResponse(BaseModel):
    text: str


class InvoiceSyncResult(BaseModel):
    checked: int
    updated: int
    paid: int
