from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from reconcileiq_database.types import ExactDecimal


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
    user_id: Mapped[str | None] = mapped_column(String(32))


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
    vendor_id: Mapped[str | None] = mapped_column(String(32))
    tax_mode: Mapped[str] = mapped_column(
        String(16), default="exclusive", server_default="exclusive"
    )
    lines_normalized: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    subtotal_minor: Mapped[int | None] = mapped_column(BigInteger)
    tax_minor: Mapped[int | None] = mapped_column(BigInteger)
    discount_minor: Mapped[int | None] = mapped_column(BigInteger)

    financial_lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        "PurchaseOrderLine",
        primaryjoin=(
            "and_(PurchaseOrder.organization_id == "
            "PurchaseOrderLine.organization_id, "
            "PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)"
        ),
        viewonly=True,
    )


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
    vendor_id: Mapped[str | None] = mapped_column(String(32))
    supplier_invoice_number: Mapped[str | None] = mapped_column(String(80))
    normalized_invoice_number: Mapped[str | None] = mapped_column(String(80))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    subtotal_minor: Mapped[int | None] = mapped_column(BigInteger)
    discount_minor: Mapped[int | None] = mapped_column(BigInteger)
    tax_minor: Mapped[int | None] = mapped_column(BigInteger)
    tax_mode: Mapped[str | None] = mapped_column(String(16))
    financial_state: Mapped[str] = mapped_column(
        String(16), default="legacy", server_default="legacy"
    )
    validated_extraction_id: Mapped[str | None] = mapped_column(String(32))
    upload_key: Mapped[str | None] = mapped_column(String(256))
    document_key: Mapped[str | None] = mapped_column(String(256))
    document_sha256: Mapped[str | None] = mapped_column(String(64))
    document_size: Mapped[int | None] = mapped_column(Integer)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    extractions: Mapped[list["ExtractionVersion"]] = relationship(
        "ExtractionVersion",
        primaryjoin=(
            "and_(Invoice.organization_id == "
            "ExtractionVersion.organization_id, Invoice.id == "
            "ExtractionVersion.invoice_id)"
        ),
        foreign_keys="[ExtractionVersion.organization_id, ExtractionVersion.invoice_id]",
        viewonly=True,
    )


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

    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True, default="demo", server_default="demo"
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(32))
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
    stage: Mapped[str] = mapped_column(String(32), default="admitted", server_default="admitted")
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    replay_of: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), default="demo", server_default="demo"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    policy_id: Mapped[str] = mapped_column(String(40))
    extraction_version_id: Mapped[str | None] = mapped_column(String(32))
    tolerance_policy_id: Mapped[str | None] = mapped_column(String(32))
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
    user_id: Mapped[str | None] = mapped_column(String(32))
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
    lines_normalized: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    financial_lines: Mapped[list["GoodsReceiptLine"]] = relationship(
        "GoodsReceiptLine",
        foreign_keys=(
            "[GoodsReceiptLine.organization_id, "
            "GoodsReceiptLine.goods_receipt_id, "
            "GoodsReceiptLine.purchase_order_id]"
        ),
        viewonly=True,
    )


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


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(160), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "organization_memberships"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120))
    external_reference: Mapped[str | None] = mapped_column(String(80))
    default_currency: Mapped[str] = mapped_column(String(3))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TolerancePolicy(Base):
    __tablename__ = "tolerance_policies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    purchase_order_id: Mapped[str] = mapped_column(String(32))
    reference: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal())
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    line_total_minor: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    goods_receipt_id: Mapped[str] = mapped_column(String(32))
    purchase_order_id: Mapped[str] = mapped_column(String(32))
    purchase_order_line_id: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractionVersion(Base):
    __tablename__ = "extraction_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(String(32))
    number: Mapped[int] = mapped_column(Integer)
    document_sha256: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[str] = mapped_column(String(32), default="invoice-v1")
    validation_state: Mapped[str] = mapped_column(String(16), default="untrusted")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    fields: Mapped[list["ExtractedField"]] = relationship(
        "ExtractedField",
        foreign_keys=(
            "[ExtractedField.organization_id, "
            "ExtractedField.extraction_version_id, "
            "ExtractedField.invoice_id]"
        ),
        viewonly=True,
    )
    financial_lines: Mapped[list["InvoiceLine"]] = relationship(
        "InvoiceLine",
        foreign_keys=(
            "[InvoiceLine.organization_id, "
            "InvoiceLine.extraction_version_id, "
            "InvoiceLine.invoice_id]"
        ),
        viewonly=True,
    )


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    extraction_version_id: Mapped[str] = mapped_column(String(32))
    invoice_id: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(String(160))
    value: Mapped[Any] = mapped_column(JSON)
    page: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractionVersionSeal(Base):
    __tablename__ = "extraction_version_seals"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    extraction_version_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String(32))
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(String(32))
    extraction_version_id: Mapped[str] = mapped_column(String(32))
    line_number: Mapped[int] = mapped_column(Integer)
    purchase_order_id: Mapped[str | None] = mapped_column(String(32))
    purchase_order_line_id: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal())
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    line_total_minor: Mapped[int] = mapped_column(BigInteger)
    discount_minor: Mapped[int] = mapped_column(BigInteger)
    tax_rate: Mapped[Decimal] = mapped_column(ExactDecimal())
    tax_mode: Mapped[str] = mapped_column(String(16))
    tax_minor: Mapped[int] = mapped_column(BigInteger)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DuplicateInvoiceAttempt(Base):
    __tablename__ = "duplicate_invoice_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(String(32))
    existing_invoice_id: Mapped[str] = mapped_column(String(32))
    supplier_invoice_number: Mapped[str] = mapped_column(String(80))
    document_sha256: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    job_id: Mapped[str] = mapped_column(String(32))
    number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    worker: Mapped[str] = mapped_column(String(80))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FailedJob(Base):
    __tablename__ = "failed_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    job_id: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean)
    next_action: Mapped[str] = mapped_column(String(200))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReconciliationLineResult(Base):
    __tablename__ = "reconciliation_line_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    reconciliation_id: Mapped[str] = mapped_column(String(32))
    invoice_id: Mapped[str] = mapped_column(String(32))
    invoice_line_id: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    amount_difference_minor: Mapped[int] = mapped_column(BigInteger)
    quantity_difference: Mapped[Decimal] = mapped_column(ExactDecimal())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _unique(table: str, *columns: str) -> None:
    Base.metadata.tables[table].append_constraint(
        UniqueConstraint(*columns, name=f"uq_{table}_{'_'.join(columns)}"[:63])
    )


def _fk(
    table: str,
    columns: list[str],
    parent: str,
    remote: list[str],
    tag: str,
    *,
    use_alter: bool = False,
) -> None:
    Base.metadata.tables[table].append_constraint(
        ForeignKeyConstraint(
            columns,
            [f"{parent}.{column}" for column in remote],
            name=f"fk_{table}_{tag}",
            use_alter=use_alter,
        )
    )


def _check(table: str, expression: str, name: str) -> None:
    Base.metadata.tables[table].append_constraint(
        CheckConstraint(expression, name=f"ck_{table}_{name}")
    )


# Native composite foreign keys bind tenant AND parent; row IDs alone are insufficient.
for _name in (
    "purchase_orders",
    "invoices",
    "audit_events",
    "jobs",
    "reconciliation_runs",
    "discrepancies",
    "review_decisions",
    "goods_receipts",
    "vendors",
    "tolerance_policies",
    "purchase_order_lines",
    "goods_receipt_lines",
    "extraction_versions",
    "invoice_lines",
):
    _unique(_name, "organization_id", "id")
for _name in ("purchase_orders", "invoices", "audit_events"):
    _fk(_name, ["organization_id"], "organizations", ["id"], "organization")
_unique("organization_memberships", "organization_id", "user_id", "role")
_check("organization_memberships", "role IN ('admin','reviewer','auditor')", "role")
_fk(
    "api_credentials",
    ["organization_id", "user_id", "role"],
    "organization_memberships",
    ["organization_id", "user_id", "role"],
    "membership",
)
_unique("vendors", "organization_id", "code")
_unique("vendors", "organization_id", "normalized_name")
_unique("tolerance_policies", "organization_id", "code", "currency", "version")
_fk(
    "purchase_orders",
    ["organization_id", "vendor_id"],
    "vendors",
    ["organization_id", "id"],
    "vendor",
)
_fk("invoices", ["organization_id", "vendor_id"], "vendors", ["organization_id", "id"], "vendor")
_unique("purchase_orders", "organization_id", "id", "currency")
_unique("purchase_orders", "organization_id", "id", "vendor_id")
_unique("invoices", "organization_id", "id", "currency")
_unique("invoices", "organization_id", "id", "purchase_order_id")
_unique("invoices", "organization_id", "id", "vendor_id")
_unique("invoices", "organization_id", "vendor_id", "normalized_invoice_number")
_fk(
    "invoices",
    ["organization_id", "purchase_order_id"],
    "purchase_orders",
    ["organization_id", "id"],
    "po_tenant",
)
_fk(
    "invoices",
    ["organization_id", "purchase_order_id", "currency"],
    "purchase_orders",
    ["organization_id", "id", "currency"],
    "po_currency",
)
_fk(
    "invoices",
    ["organization_id", "purchase_order_id", "vendor_id"],
    "purchase_orders",
    ["organization_id", "id", "vendor_id"],
    "po_vendor",
)
for _name in ("jobs", "reconciliation_runs", "discrepancies", "review_decisions"):
    _fk(
        _name,
        ["organization_id", "invoice_id"],
        "invoices",
        ["organization_id", "id"],
        "invoice_tenant",
    )
_fk("jobs", ["organization_id", "replay_of"], "jobs", ["organization_id", "id"], "replay_tenant")
for _name in (
    "outbox_events",
    "job_attempts",
    "failed_jobs",
    "webhook_receipts",
    "job_execution_records",
    "reconciliation_runs",
):
    _fk(_name, ["organization_id", "job_id"], "jobs", ["organization_id", "id"], "job_tenant")
_unique("reconciliation_runs", "organization_id", "id", "invoice_id")
for _name in ("discrepancies", "review_decisions", "reconciliation_line_results"):
    _fk(
        _name,
        ["organization_id", "reconciliation_id", "invoice_id"],
        "reconciliation_runs",
        ["organization_id", "id", "invoice_id"],
        "run_invoice",
    )
_fk(
    "review_decisions",
    ["organization_id", "user_id"],
    "organization_memberships",
    ["organization_id", "user_id"],
    "reviewer",
)
_fk(
    "goods_receipts",
    ["organization_id", "purchase_order_id"],
    "purchase_orders",
    ["organization_id", "id"],
    "po_tenant",
)
_unique("goods_receipts", "organization_id", "id", "purchase_order_id")
_fk(
    "purchase_order_lines",
    ["organization_id", "purchase_order_id", "currency"],
    "purchase_orders",
    ["organization_id", "id", "currency"],
    "po_currency",
)
_unique("purchase_order_lines", "organization_id", "purchase_order_id", "reference")
_unique("purchase_order_lines", "organization_id", "purchase_order_id", "id")
_fk(
    "goods_receipt_lines",
    ["organization_id", "goods_receipt_id", "purchase_order_id"],
    "goods_receipts",
    ["organization_id", "id", "purchase_order_id"],
    "receipt_po",
)
_fk(
    "goods_receipt_lines",
    ["organization_id", "purchase_order_id", "purchase_order_line_id"],
    "purchase_order_lines",
    ["organization_id", "purchase_order_id", "id"],
    "po_line",
)
_unique("goods_receipt_lines", "organization_id", "goods_receipt_id", "purchase_order_line_id")
_fk(
    "extraction_versions",
    ["organization_id", "invoice_id"],
    "invoices",
    ["organization_id", "id"],
    "invoice",
)
_unique("extraction_versions", "organization_id", "invoice_id", "number")
_unique("extraction_versions", "organization_id", "id", "invoice_id")
_fk(
    "invoices",
    ["organization_id", "validated_extraction_id", "id"],
    "extraction_versions",
    ["organization_id", "id", "invoice_id"],
    "validated_extraction",
    use_alter=True,
)
for _name in (
    "extracted_fields",
    "invoice_lines",
    "reconciliation_runs",
    "extraction_version_seals",
):
    _fk(
        _name,
        ["organization_id", "extraction_version_id", "invoice_id"],
        "extraction_versions",
        ["organization_id", "id", "invoice_id"],
        "extraction_invoice",
    )
_unique("extracted_fields", "organization_id", "extraction_version_id", "path", "page")
_fk(
    "invoice_lines",
    ["organization_id", "invoice_id", "currency"],
    "invoices",
    ["organization_id", "id", "currency"],
    "invoice_currency",
)
_fk(
    "invoice_lines",
    ["organization_id", "invoice_id", "purchase_order_id"],
    "invoices",
    ["organization_id", "id", "purchase_order_id"],
    "invoice_po",
)
_fk(
    "invoice_lines",
    ["organization_id", "purchase_order_id", "purchase_order_line_id"],
    "purchase_order_lines",
    ["organization_id", "purchase_order_id", "id"],
    "po_line",
)
_unique("invoice_lines", "organization_id", "extraction_version_id", "line_number")
_unique("invoice_lines", "organization_id", "id", "invoice_id")
_fk(
    "reconciliation_line_results",
    ["organization_id", "invoice_line_id", "invoice_id"],
    "invoice_lines",
    ["organization_id", "id", "invoice_id"],
    "invoice_line",
)
_fk(
    "reconciliation_runs",
    ["organization_id", "tolerance_policy_id"],
    "tolerance_policies",
    ["organization_id", "id"],
    "tolerance",
)
_fk(
    "duplicate_invoice_attempts",
    ["organization_id", "existing_invoice_id", "vendor_id"],
    "invoices",
    ["organization_id", "id", "vendor_id"],
    "original_vendor",
)
_unique("job_attempts", "organization_id", "job_id", "number")
_unique("failed_jobs", "organization_id", "job_id")
for _name, _currency in (
    ("vendors", "default_currency"),
    ("purchase_orders", "currency"),
    ("invoices", "currency"),
    ("invoice_lines", "currency"),
    ("purchase_order_lines", "currency"),
    ("tolerance_policies", "currency"),
):
    _check(_name, f"{_currency} IN ('USD','EUR','PHP','JPY','KWD')", "currency")
for _name, _fields in {
    "purchase_orders": ["subtotal_minor", "discount_minor", "tax_minor"],
    "invoices": ["subtotal_minor", "discount_minor", "tax_minor"],
    "purchase_order_lines": ["unit_price_minor", "line_total_minor"],
    "invoice_lines": [
        "unit_price_minor",
        "line_total_minor",
        "discount_minor",
        "tax_minor",
        "total_minor",
    ],
    "tolerance_policies": ["amount_minor"],
}.items():
    for _field in _fields:
        _check(_name, f"{_field} >= 0 AND {_field} <= 9007199254740991", _field)
for _name in ("purchase_order_lines", "invoice_lines", "goods_receipt_lines"):
    _check(_name, "riq_quantity_valid(quantity) = 1", "quantity")
for _name in ("purchase_order_lines", "invoice_lines"):
    _check(_name, "line_total_minor = riq_line_minor(quantity, unit_price_minor)", "line_total")
_check("invoice_lines", "riq_rate_valid(tax_rate) = 1", "rate")
_check(
    "invoice_lines",
    "tax_minor = riq_tax_minor(line_total_minor, discount_minor, tax_rate, tax_mode)",
    "tax",
)
_check(
    "invoice_lines",
    (
        "total_minor = line_total_minor - discount_minor + CASE WHEN tax_mode = 'exclusive' "
        "THEN tax_minor ELSE 0 END"
    ),
    "total",
)
_check(
    "invoice_lines",
    "tax_mode IN ('exclusive','inclusive') AND discount_minor <= line_total_minor",
    "mode",
)
_check("invoices", "financial_state IN ('legacy','draft','validated')", "financial_state")
_check(
    "invoices",
    "normalized_invoice_number = upper(replace(trim(supplier_invoice_number), ' ', ''))",
    "normalized_number",
)
_check(
    "invoices",
    (
        "financial_state != 'validated' OR (vendor_id IS NOT NULL AND supplier_invoice_number "
        "IS NOT NULL AND normalized_invoice_number IS NOT NULL AND "
        "length(normalized_invoice_number) > 0 AND currency IS NOT NULL AND subtotal_minor IS "
        "NOT NULL AND discount_minor IS NOT NULL AND tax_minor IS NOT NULL AND "
        "validated_extraction_id IS NOT NULL AND amount_minor IS NOT NULL AND tax_mode IS NOT "
        "NULL AND tax_mode IN ('exclusive','inclusive') AND discount_minor <= subtotal_minor "
        "AND amount_minor = subtotal_minor - discount_minor + CASE WHEN tax_mode = 'exclusive' "
        "THEN tax_minor ELSE 0 END)"
    ),
    "validated_header",
)
_check(
    "extraction_versions",
    "number >= 1 AND validation_state IN ('untrusted','validated','rejected')",
    "version",
)
_check("extracted_fields", "page >= 1", "page")
_check(
    "job_attempts",
    "number BETWEEN 1 AND 5 AND state IN ('running','succeeded','failed')",
    "attempt",
)
_check(
    "jobs",
    "max_attempts BETWEEN 1 AND 5 AND attempts >= 0 AND attempts <= max_attempts",
    "attempts",
)
Index("ix_jobs_recovery", Job.organization_id, Job.state, Job.next_attempt_at, Job.lease_expires_at)
Index(
    "ix_outbox_dispatch",
    OutboxEvent.published_at,
    OutboxEvent.available_at,
    OutboxEvent.lease_expires_at,
)

_check("purchase_orders", "tax_mode IN ('exclusive','inclusive')", "tax_mode")
_check("tolerance_policies", "version >= 1", "version")
_check("invoice_lines", "line_number >= 1", "line_number")
_check("extracted_fields", "confidence IS NULL OR riq_rate_valid(confidence) = 1", "confidence")
_check("outbox_events", "attempts >= 0", "attempts")
_check(
    "reconciliation_line_results",
    "amount_difference_minor BETWEEN -9007199254740991 AND 9007199254740991",
    "amount",
)
_unique("reconciliation_line_results", "organization_id", "reconciliation_id", "invoice_line_id")
Index(
    "uq_job_attempts_active",
    JobAttempt.organization_id,
    JobAttempt.job_id,
    unique=True,
    postgresql_where=JobAttempt.state == "running",
    sqlite_where=JobAttempt.state == "running",
)
_check(
    "job_attempts",
    (
        "(state = 'running' AND finished_at IS NULL) OR (state IN ('failed','succeeded') AND "
        "finished_at IS NOT NULL AND finished_at >= started_at)"
    ),
    "finished",
)

_unique("vendors", "organization_id", "external_reference")
Index(
    "ix_extraction_seals_invoice",
    ExtractionVersionSeal.organization_id,
    ExtractionVersionSeal.invoice_id,
)
