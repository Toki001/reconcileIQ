"""Tenant-scoped candidate evidence and deterministic matching; never makes a payment."""

import hashlib
import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any, cast

from reconcileiq_ai.invoice import InvoiceCandidate, evidence_fields
from reconcileiq_reconciliation.accounting import TaxMode, normalize_invoice_number
from reconcileiq_reconciliation.matching import (
    InvoiceFacts,
    LineFacts,
    MatchResult,
    OrderFacts,
    OrderLine,
    reconcile,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from reconcileiq_database.financial import audit_record, identity, validate_and_publish_invoice
from reconcileiq_database.models import (
    Discrepancy,
    ExtractedField,
    ExtractionVersion,
    ExtractionVersionSeal,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    PurchaseOrder,
    PurchaseOrderLine,
    ReconciliationEvidence,
    ReconciliationRun,
    Vendor,
)

SCHEMA = "invoice-evidence-v1"


def latest_candidate(session: Session, invoice: Invoice) -> ExtractionVersion | None:
    return session.scalar(
        select(ExtractionVersion)
        .where(
            ExtractionVersion.organization_id == invoice.organization_id,
            ExtractionVersion.invoice_id == invoice.id,
            ExtractionVersion.schema_version == SCHEMA,
        )
        .order_by(ExtractionVersion.number.desc())
        .limit(1)
    )


def facts(candidate: InvoiceCandidate) -> InvoiceFacts | None:
    required = {
        "invoice_number",
        "vendor_name",
        "invoice_date",
        "purchase_order_reference",
        "currency",
        "tax_mode",
        "subtotal_minor",
        "discount_minor",
        "tax_minor",
        "total_minor",
    }
    for path, field in evidence_fields(candidate):
        mandatory = path in required or (
            path.startswith("lines[") and not path.endswith(".reference")
        )
        if mandatory and (field.value is None or field.status == "inferred"):
            return None
    if not candidate.lines:
        return None
    return InvoiceFacts(
        number=cast(str, candidate.invoice_number.value),
        purchase_order_reference=cast(str, candidate.purchase_order_reference.value),
        currency=cast(str, candidate.currency.value),
        tax_mode=cast(TaxMode, candidate.tax_mode.value),
        subtotal_minor=cast(int, candidate.subtotal_minor.value),
        discount_minor=cast(int, candidate.discount_minor.value),
        tax_minor=cast(int, candidate.tax_minor.value),
        total_minor=cast(int, candidate.total_minor.value),
        lines=tuple(
            LineFacts(
                line.reference.value if line.reference.status != "inferred" else None,
                cast(str, line.description.value),
                Decimal(cast(str, line.quantity.value)),
                cast(int, line.unit_price_minor.value),
                cast(int, line.gross_minor.value),
                cast(int, line.discount_minor.value),
                Decimal(cast(str, line.tax_rate.value)),
                cast(int, line.tax_minor.value),
                cast(int, line.total_minor.value),
            )
            for line in candidate.lines
        ),
    )


def append_candidate(
    session: Session,
    invoice: Invoice,
    candidate: InvoiceCandidate,
    origin: str,
    metadata: dict[str, Any],
    actor: str,
    *,
    version_id: str,
) -> ExtractionVersion:
    previous = (
        session.scalar(
            select(ExtractionVersion.number)
            .where(
                ExtractionVersion.organization_id == invoice.organization_id,
                ExtractionVersion.invoice_id == invoice.id,
            )
            .order_by(ExtractionVersion.number.desc())
            .limit(1)
        )
        or 0
    )
    row = ExtractionVersion(
        id=version_id,
        organization_id=invoice.organization_id,
        invoice_id=invoice.id,
        number=previous + 1,
        document_sha256=invoice.document_sha256,
        origin=origin,
        schema_version=SCHEMA,
        validation_state="untrusted",
        payload={"candidate": candidate.model_dump(), **metadata},
    )
    session.add(row)
    session.flush()
    for path, field in evidence_fields(candidate):
        session.add(
            ExtractedField(
                id=identity(f"evidence:{row.id}:{path}"),
                organization_id=invoice.organization_id,
                invoice_id=invoice.id,
                extraction_version_id=row.id,
                path=path,
                value=field.model_dump(),
                page=field.pages[0] if field.pages else 1,
                source="human" if field.status == "human" else origin,
            )
        )
    session.flush()
    session.add(
        ExtractionVersionSeal(
            organization_id=invoice.organization_id,
            extraction_version_id=row.id,
            invoice_id=invoice.id,
        )
    )
    audit_record(
        session,
        invoice.organization_id,
        actor,
        row.id,
        "candidate_evidence_recorded",
        {
            "invoice_id": invoice.id,
            "version": row.number,
            "origin": origin,
            "model": metadata.get("model"),
            "prompt_version": metadata.get("prompt_version"),
            "source_version_id": metadata.get("source_version_id"),
            "reason": metadata.get("reason"),
        },
    )
    return row


def matching_context(
    session: Session, invoice: Invoice, candidate: InvoiceCandidate
) -> dict[str, Any]:
    org = invoice.organization_id
    name = (candidate.vendor_name.value or "").strip().casefold()
    supplier_code = (
        candidate.vendor_identifier.value
        if candidate.vendor_identifier.status != "inferred"
        else None
    )
    query = select(Vendor).where(
        Vendor.organization_id == org, Vendor.active.is_(True), Vendor.normalized_name == name
    )
    if supplier_code:
        query = query.where(
            (Vendor.code == supplier_code) | (Vendor.external_reference == supplier_code)
        )
    vendors = session.scalars(query.limit(2)).all()
    vendor = vendors[0] if len(vendors) == 1 else None
    reference = candidate.purchase_order_reference.value
    order_query = select(PurchaseOrder).where(PurchaseOrder.organization_id == org)
    if invoice.purchase_order_id:
        order_query = order_query.where(PurchaseOrder.id == invoice.purchase_order_id)
    else:
        order_query = order_query.where(
            (PurchaseOrder.reference == reference) | (PurchaseOrder.id == reference)
        )
    order = session.scalar(order_query.with_for_update().limit(1)) if reference else None
    duplicate = False
    if (
        vendor
        and candidate.invoice_number.value
        and candidate.invoice_number.status != "inferred"
        and candidate.vendor_name.status != "inferred"
    ):
        try:
            normalized = normalize_invoice_number(candidate.invoice_number.value)
            duplicate = (
                session.scalar(
                    select(Invoice.id)
                    .where(
                        Invoice.organization_id == org,
                        Invoice.vendor_id == vendor.id,
                        Invoice.normalized_invoice_number == normalized,
                        Invoice.id != invoice.id,
                    )
                    .limit(1)
                )
                is not None
            )
        except ValueError:
            pass  # The pure engine classifies the invalid number without coercion.
    data: dict[str, Any] = {
        "vendor_id": vendor.id if vendor else None,
        "vendor_matches": None if not vendor else (order.vendor_id == vendor.id if order else True),
        "duplicate": duplicate,
        "order": None,
        "received": {},
        "reservations": [],
        "document_sha256": invoice.document_sha256,
    }
    if order is None:
        return data
    rows = session.scalars(
        select(PurchaseOrderLine)
        .where(
            PurchaseOrderLine.organization_id == org,
            PurchaseOrderLine.purchase_order_id == order.id,
        )
        .order_by(PurchaseOrderLine.reference)
    ).all()
    received: dict[str, Decimal] = {line.reference: Decimal(0) for line in rows}
    available: dict[str, Decimal] = {line.reference: line.quantity for line in rows}
    by_id = {line.id: line.reference for line in rows}
    for receipt in session.scalars(
        select(GoodsReceiptLine).where(
            GoodsReceiptLine.organization_id == org, GoodsReceiptLine.purchase_order_id == order.id
        )
    ):
        received[by_id[receipt.purchase_order_line_id]] += receipt.quantity
    # Only actual human-reviewed publications reserve capacity. Historical synthetic
    # arithmetic fixtures and untrusted candidates do not silently consume receipts.
    allocated = session.execute(
        select(InvoiceLine, ExtractionVersion.id)
        .join(
            Invoice,
            (Invoice.id == InvoiceLine.invoice_id)
            & (Invoice.organization_id == InvoiceLine.organization_id)
            & (Invoice.validated_extraction_id == InvoiceLine.extraction_version_id),
        )
        .join(
            ExtractionVersion,
            (ExtractionVersion.id == InvoiceLine.extraction_version_id)
            & (ExtractionVersion.organization_id == InvoiceLine.organization_id),
        )
        .where(
            InvoiceLine.organization_id == org,
            InvoiceLine.purchase_order_id == order.id,
            InvoiceLine.invoice_id != invoice.id,
            ExtractionVersion.origin == "human_review",
        )
        .order_by(InvoiceLine.id)
    ).all()
    for line, version_id in allocated:
        reference = by_id.get(line.purchase_order_line_id or "")
        if reference:
            received[reference] -= line.quantity
            available[reference] -= line.quantity
            data["reservations"].append(
                {"line_id": line.id, "version_id": version_id, "quantity": str(line.quantity)}
            )
    data["order"] = {
        "id": order.id,
        "reference": order.reference or order.id,
        "version": order.version,
        "vendor_id": order.vendor_id,
        "currency": order.currency,
        "tax_mode": order.tax_mode,
        "status": order.status,
        "subtotal_minor": order.subtotal_minor,
        "discount_minor": order.discount_minor,
        "tax_minor": order.tax_minor,
        "total_minor": order.amount_minor,
        "lines": [
            {
                "id": line.id,
                "reference": line.reference,
                "description": line.description,
                "quantity": str(max(Decimal(0), available[line.reference])),
                "unit_price_minor": line.unit_price_minor,
            }
            for line in rows
        ],
    }
    data["received"] = {key: str(max(Decimal(0), value)) for key, value in received.items()}
    return data


def context_digest(context: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_candidate(
    candidate: InvoiceCandidate, context: dict[str, Any], policy: str
) -> MatchResult:
    row = context["order"]
    order = None
    if (
        row
        and row["status"] != "cancelled"
        and all(row[key] is not None for key in ("subtotal_minor", "discount_minor", "tax_minor"))
    ):
        order = OrderFacts(
            row["reference"],
            row["currency"],
            row["tax_mode"],
            row["subtotal_minor"],
            row["discount_minor"],
            row["tax_minor"],
            row["total_minor"],
            tuple(
                OrderLine(
                    line["reference"],
                    line["description"],
                    Decimal(line["quantity"]),
                    line["unit_price_minor"],
                )
                for line in row["lines"]
            ),
            aliases=(row["id"],),
        )
    return reconcile(
        facts(candidate),
        order,
        vendor_matches=context["vendor_matches"],
        duplicate=context["duplicate"],
        received={key: Decimal(value) for key, value in context["received"].items()},
        policy=cast(Any, policy),
    )


def persist_reconciliation(
    session: Session,
    invoice: Invoice,
    run: ReconciliationRun,
    source: ExtractionVersion,
    actor: str,
) -> MatchResult:
    candidate = InvoiceCandidate.model_validate(source.payload["candidate"])
    context = matching_context(session, invoice, candidate)
    result = evaluate_candidate(candidate, context, run.policy_id)
    run.status, run.outcome = "completed", result.outcome
    run.extraction_version_id = source.id
    invoice.status = {
        "matched": "matched",
        "unmatched": "needs_review",
        "discrepancy": "exception",
        "duplicate": "duplicate",
        "needs_review": "needs_review",
        "extraction_failed": "needs_review",
    }[result.outcome]
    invoice.version += 1
    invoice.exception_code = result.differences[0].code if result.differences else None
    invoice.exception_detail = (
        "Review the deterministic result and source evidence." if result.differences else None
    )
    session.add(
        ReconciliationEvidence(
            reconciliation_id=run.id,
            organization_id=invoice.organization_id,
            invoice_id=invoice.id,
            extraction_version_id=source.id,
            invoice_version=invoice.version,
            context_sha256=context_digest(context),
            context=context,
            result=asdict(result),
        )
    )
    for index, difference in enumerate(result.differences):
        session.add(
            Discrepancy(
                id=identity(f"difference:{run.id}:{index}"),
                organization_id=invoice.organization_id,
                invoice_id=invoice.id,
                reconciliation_id=run.id,
                code=difference.code,
                detail=f"Expected {difference.expected}; observed {difference.observed}.",
                field=difference.field,
                expected=difference.expected,
                observed=difference.observed,
                currency=candidate.currency.value,
            )
        )
    audit_record(
        session,
        invoice.organization_id,
        actor,
        run.id,
        "deterministic_reconciliation_completed",
        {
            "invoice_id": invoice.id,
            "extraction_version_id": source.id,
            "outcome": result.outcome,
            "policy_id": run.policy_id,
            "context_sha256": context_digest(context),
            "invoice_version": invoice.version,
        },
    )
    return result


def publish_review(
    session: Session, invoice: Invoice, source: ExtractionVersion, actor: str, confirmation_id: str
) -> ExtractionVersion:
    candidate = InvoiceCandidate.model_validate(source.payload["candidate"])
    values = facts(candidate)
    context = matching_context(session, invoice, candidate)
    if values is None or context["order"] is None or not context["vendor_id"]:
        raise ValueError("Complete evidence and a known supplier/order are required")
    order = context["order"]
    if invoice.financial_state == "validated" and (
        invoice.currency != values.currency
        or invoice.vendor_id != context["vendor_id"]
        or invoice.purchase_order_id != order["id"]
    ):
        raise ValueError(
            "Published currency/supplier/order bindings require a separate corrective invoice"
        )
    new_invoice = invoice.financial_state != "validated"
    if new_invoice:
        invoice.currency, invoice.vendor_id = values.currency, context["vendor_id"]
        invoice.purchase_order_id = order["id"]
        session.flush()
    number = (
        session.scalar(
            select(ExtractionVersion.number)
            .where(
                ExtractionVersion.organization_id == invoice.organization_id,
                ExtractionVersion.invoice_id == invoice.id,
            )
            .order_by(ExtractionVersion.number.desc())
            .limit(1)
        )
        or 0
    ) + 1
    confirmed = ExtractionVersion(
        id=confirmation_id,
        organization_id=invoice.organization_id,
        invoice_id=invoice.id,
        number=number,
        document_sha256=invoice.document_sha256,
        origin="human_review",
        schema_version=SCHEMA,
        validation_state="validated",
        payload={
            "candidate": candidate.model_dump(),
            "source_version_id": source.id,
            "confirmed_by": actor,
        },
    )
    session.add(confirmed)
    session.flush()
    references = {line["reference"]: line for line in order["lines"]}
    for index, line in enumerate(values.lines, 1):
        matched = references.get(line.reference or "") or next(
            row
            for row in order["lines"]
            if row["description"].strip().casefold() == line.description.strip().casefold()
        )
        session.add(
            InvoiceLine(
                id=identity(f"review-line:{confirmed.id}:{index}"),
                organization_id=invoice.organization_id,
                invoice_id=invoice.id,
                extraction_version_id=confirmed.id,
                line_number=index,
                purchase_order_id=order["id"],
                purchase_order_line_id=matched["id"],
                description=line.description,
                currency=values.currency,
                quantity=line.quantity,
                unit_price_minor=line.unit_price_minor,
                line_total_minor=line.gross_minor,
                discount_minor=line.discount_minor,
                tax_rate=line.tax_rate,
                tax_mode=values.tax_mode,
                tax_minor=line.tax_minor,
                total_minor=line.total_minor,
            )
        )
    session.flush()
    with session.no_autoflush:
        invoice.vendor = candidate.vendor_name.value
        invoice.supplier_invoice_number = values.number
        invoice.invoice_date = date.fromisoformat(cast(str, candidate.invoice_date.value))
        invoice.due_on = (
            date.fromisoformat(candidate.due_date.value)
            if candidate.due_date.value and candidate.due_date.status != "inferred"
            else None
        )
        invoice.subtotal_minor, invoice.discount_minor = (
            values.subtotal_minor,
            values.discount_minor,
        )
        invoice.tax_minor, invoice.amount_minor, invoice.tax_mode = (
            values.tax_minor,
            values.total_minor,
            values.tax_mode,
        )
        validate_and_publish_invoice(session, invoice, confirmed, actor)
    return confirmed
