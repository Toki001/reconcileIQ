from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class Credential(Base):
    __tablename__ = "api_credentials"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    issued_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32))
    organization_id: Mapped[str] = mapped_column(
        String(32), default="demo", server_default="demo", index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default="2026-10-21 00:00:00.000000"
    )
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    subtotal_minor: Mapped[int | None] = mapped_column(BigInteger)
    tax_minor: Mapped[int | None] = mapped_column(BigInteger)
    discount_minor: Mapped[int | None] = mapped_column(BigInteger)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vendor: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str | None] = mapped_column(String(3))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    due_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), index=True)
    purchase_order_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.id"))
    exception_code: Mapped[str | None] = mapped_column(String(64))
    exception_detail: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[str] = mapped_column(
        String(32), default="demo", server_default="demo", index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default="2026-10-21 00:00:00.000000"
    )
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    upload_key: Mapped[str | None] = mapped_column(String(256))
    document_key: Mapped[str | None] = mapped_column(String(256))
    document_sha256: Mapped[str | None] = mapped_column(String(64))
    document_size: Mapped[int | None] = mapped_column(Integer)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    organization_id: Mapped[str] = mapped_column(
        String(32), default="demo", server_default="demo", index=True
    )


class JobExecution(Base):
    __tablename__ = "job_execution_records"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    result: Mapped[str] = mapped_column(String(80))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    location: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"))
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32), default="pending_dispatch")
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    replay_of: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    policy_id: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str | None] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Discrepancy(Base):
    __tablename__ = "discrepancies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"))
    reconciliation_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
    field: Mapped[str] = mapped_column(String(80))
    expected: Mapped[str | None] = mapped_column(String(200))
    observed: Mapped[str | None] = mapped_column(String(200))
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (UniqueConstraint("invoice_id", "invoice_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"))
    reconciliation_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"))
    invoice_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(1000))
    actor: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    __table_args__ = (UniqueConstraint("organization_id", "reference"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"))
    reference: Mapped[str] = mapped_column(String(80))
    received_on: Mapped[date] = mapped_column(Date)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def prevent_audit_mutation(*args: object) -> None:
    raise ValueError("Audit events are append-only")


for _table in (Invoice, PurchaseOrder, ReconciliationRun, Discrepancy):
    Index(
        f"ix_{_table.__tablename__}_tenant_cursor",
        _table.organization_id,
        _table.created_at,
        _table.id,
    )
Index(
    "ix_audit_events_tenant_cursor",
    AuditEvent.organization_id,
    AuditEvent.occurred_at,
    AuditEvent.id,
)
