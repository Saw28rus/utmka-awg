import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Invoice(Base):
    """Счёт на оплату ЮKassa, выставленный клиенту панели."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Снимок данных клиента (клиенты хранятся в clients.json, не в БД).
    client_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    client_name: Mapped[str] = mapped_column(String(255))
    server_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    service: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    yk_invoice_id: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    pay_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # pending | succeeded | canceled | expired
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Продление клиента после оплаты — выполняется один раз (идемпотентность).
    extend_months: Mapped[int] = mapped_column(Integer, default=1)
    client_extended: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoiceTemplate(Base):
    """Шаблон текста счёта с токенами {{имя}}, {{услуга}}, {{сумма}}, {{ссылка}}, {{период}}."""

    __tablename__ = "invoice_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    default_service: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    default_amount_kopecks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
