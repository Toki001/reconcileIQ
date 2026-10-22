"""Audited aggregate writes behind the unchanged milestone 02 HTTP contracts."""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from reconcileiq_reconciliation.accounting import (
    LineAmounts,
    TaxMode,
    normalize_invoice_number,
    validate_invoice,
)
from reconcileiq_reconciliation.money import validate_header_totals
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from reconcileiq_database.models import (
    AuditEvent,
    ExtractionVersion,
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    Membership,
    PurchaseOrder,
    PurchaseOrderLine,
    User,
    Vendor,
    utcnow,
)


def identity(label: str) -> str:
    return uuid5(NAMESPACE_URL, "reconcileiq:" + label).hex


def record_time(session: Session) -> datetime:
    value = session.info.get("fixture_time")
    return value if isinstance(value, datetime) else utcnow()


def audit_record(
    session: Session, org: str, actor: str, entity: str, action: str, detail: dict[str, Any]
) -> None:
    session.add(
        AuditEvent(
            id=identity(f"audit:{org}:{entity}:{action}"),
            organization_id=org,
            entity_id=entity,
            actor=actor,
            action=action,
            detail=json.dumps(detail, sort_keys=True, separators=(",", ":")),
            occurred_at=record_time(session),
        )
    )


def ensure_identity(session: Session, org: str, actor: str, role: str) -> str:
    user_id = identity(f"user:{org}:{actor}")
    if session.get(User, user_id) is None:
        session.add(
            User(
                id=user_id,
                subject=f"local:{org}:{actor}",
                display_name=actor,
                created_at=record_time(session),
            )
        )
        session.flush()
        session.add(
            Membership(
                organization_id=org, user_id=user_id, role=role, created_at=record_time(session)
            )
        )
        audit_record(session, org, actor, user_id, "identity_created", {"role": role})
        session.flush()
    return user_id


def ensure_vendor(session: Session, org: str, name: str, currency: str, actor: str) -> Vendor:
    normalized = name.strip().casefold()
    vendor_id = identity(f"vendor:{org}:{normalized}")
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    result = session.scalar(
        insert(Vendor)
        .values(
            id=vendor_id,
            organization_id=org,
            code="legacy-" + vendor_id[:12],
            name=name,
            normalized_name=normalized,
            default_currency=currency,
            active=True,
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "normalized_name"])
        .returning(Vendor.id)
    )
    row = session.scalar(
        select(Vendor).where(Vendor.organization_id == org, Vendor.normalized_name == normalized)
    )
    if row is None:
        raise ValueError("Vendor could not be resolved")
    if result:
        audit_record(session, org, actor, row.id, "vendor_created", {"currency": currency})
    return row


def normalize_po(session: Session, po: PurchaseOrder, actor: str) -> None:
    if po.lines_normalized:
        return
    if po.subtotal_minor is None or po.discount_minor is None or po.tax_minor is None:
        raise ValueError("Normalized purchase orders require complete header totals")
    if po.tax_mode == "exclusive":
        validate_header_totals(
            [line["line_total_minor"] for line in po.line_items],
            po.subtotal_minor,
            po.discount_minor,
            po.tax_minor,
            po.amount_minor,
        )
    if po.vendor_id is None:
        po.vendor_id = ensure_vendor(session, po.organization_id, po.vendor, po.currency, actor).id
    session.flush()
    for line in po.line_items:
        row = PurchaseOrderLine(
            id=identity(f"po-line:{po.id}:{line['id']}"),
            organization_id=po.organization_id,
            purchase_order_id=po.id,
            reference=line["id"],
            description=line["description"],
            currency=po.currency,
            quantity=Decimal(line["quantity"]),
            unit_price_minor=line["unit_price_minor"],
            line_total_minor=line["line_total_minor"],
            created_at=po.created_at,
        )
        session.add(row)
        audit_record(
            session,
            po.organization_id,
            actor,
            row.id,
            "purchase_order_line_created",
            {"purchase_order_id": po.id},
        )
    session.flush()
    po.lines_normalized = True
    session.flush()


def normalize_receipt(session: Session, receipt: GoodsReceipt, actor: str) -> None:
    if receipt.lines_normalized:
        return
    for line in receipt.lines:
        po_line = session.scalar(
            select(PurchaseOrderLine).where(
                PurchaseOrderLine.organization_id == receipt.organization_id,
                PurchaseOrderLine.purchase_order_id == receipt.purchase_order_id,
                PurchaseOrderLine.reference == line["purchase_order_line_id"],
            )
        )
        if po_line is None:
            raise ValueError("Receipt must reference a normalized line in its purchase order")
        row = GoodsReceiptLine(
            id=identity(f"receipt-line:{receipt.id}:{line['purchase_order_line_id']}"),
            organization_id=receipt.organization_id,
            goods_receipt_id=receipt.id,
            purchase_order_id=receipt.purchase_order_id,
            purchase_order_line_id=po_line.id,
            quantity=Decimal(line["quantity"]),
            created_at=receipt.created_at,
        )
        session.add(row)
        audit_record(
            session,
            receipt.organization_id,
            actor,
            row.id,
            "receipt_line_created",
            {"goods_receipt_id": receipt.id},
        )
    session.flush()
    receipt.lines_normalized = True
    session.flush()


def validate_and_publish_invoice(
    session: Session, invoice: Invoice, version: ExtractionVersion, actor: str
) -> None:
    if (
        version.organization_id != invoice.organization_id
        or version.invoice_id != invoice.id
        or version.validation_state != "validated"
    ):
        raise ValueError("A validated extraction from this invoice and organization is required")
    lines = list(
        session.scalars(
            select(InvoiceLine).where(
                InvoiceLine.organization_id == invoice.organization_id,
                InvoiceLine.invoice_id == invoice.id,
                InvoiceLine.extraction_version_id == version.id,
            )
        )
    )
    if any(
        line.tax_mode != invoice.tax_mode or line.currency != invoice.currency for line in lines
    ):
        raise ValueError("Line currency and tax mode must match the invoice header")
    if (
        invoice.supplier_invoice_number is None
        or invoice.subtotal_minor is None
        or invoice.discount_minor is None
        or invoice.tax_minor is None
        or invoice.amount_minor is None
    ):
        raise ValueError("Validated invoice headers must be complete")
    validate_invoice(
        [
            LineAmounts(
                line.line_total_minor, line.discount_minor, line.tax_minor, line.total_minor
            )
            for line in lines
        ],
        invoice.subtotal_minor,
        invoice.discount_minor,
        invoice.tax_minor,
        invoice.amount_minor,
        cast(TaxMode, invoice.tax_mode),
    )
    invoice.normalized_invoice_number = normalize_invoice_number(invoice.supplier_invoice_number)
    previous = invoice.financial_state
    invoice.validated_extraction_id, invoice.financial_state = version.id, "validated"
    invoice.version += 1
    audit_record(
        session,
        invoice.organization_id,
        actor,
        invoice.id,
        f"financial_version_{version.number}_validated",
        {
            "before": previous,
            "after": "validated",
            "extraction_id": version.id,
            "version": invoice.version,
            "currency": invoice.currency,
            "amount_minor": invoice.amount_minor,
        },
    )
    session.flush()
