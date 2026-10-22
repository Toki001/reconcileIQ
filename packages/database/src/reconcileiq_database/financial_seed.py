"""Idempotent, atomic financial fixtures in their own organization. Never resets user records."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from reconcileiq_reconciliation.accounting import normalize_invoice_number
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reconcileiq_database.config import get_settings
from reconcileiq_database.financial import (
    audit_record,
    ensure_identity,
    identity,
    normalize_po,
    normalize_receipt,
    validate_and_publish_invoice,
)
from reconcileiq_database.fixture_data import ORG, dataset
from reconcileiq_database.models import (
    AuditEvent,
    Discrepancy,
    DuplicateInvoiceAttempt,
    ExtractedField,
    ExtractionVersion,
    ExtractionVersionSeal,
    GoodsReceipt,
    Invoice,
    InvoiceLine,
    Organization,
    PurchaseOrder,
    PurchaseOrderLine,
    ReconciliationLineResult,
    ReconciliationRun,
    ReviewDecision,
    TolerancePolicy,
    Vendor,
)
from reconcileiq_database.session import build_engine

STAMP = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
ACTOR = "synthetic-seed-v1"


def seed_financial(engine: Engine) -> dict[str, int]:
    data = dataset()
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    marker = identity("financial-fixture-manifest-v1")
    expected = {
        "vendors": 20,
        "purchase_orders": 100,
        "invoices": 200,
        "goods_receipts": 100,
        "duplicate_invoice_attempts": 20,
    }
    with Session(engine, info={"fixture_time": STAMP}) as session, session.begin():
        previous = session.get(AuditEvent, marker)
        if previous:
            if json.loads(previous.detail)["sha256"] != digest:
                raise ValueError(
                    "Fixture definition changed; create a versioned dataset instead of "
                    "resetting records"
                )
            for model in (Vendor, PurchaseOrder, Invoice, GoodsReceipt, DuplicateInvoiceAttempt):
                count = session.scalar(
                    select(func.count()).select_from(model).where(model.organization_id == ORG)
                )
                if count != expected[model.__tablename__]:
                    raise ValueError(
                        "Fixture aggregate is incomplete; restore or repair through an audited "
                        "migration"
                    )
            return expected
        session.add(Organization(id=ORG, name="Synthetic financial fixtures - no real records"))
        session.flush()
        audit_record(session, ORG, ACTOR, ORG, "organization_created", {"synthetic": True})
        for role in ("admin", "reviewer", "auditor"):
            ensure_identity(session, ORG, f"synthetic-{role}", role)
        for currency in ("USD", "EUR", "PHP", "JPY", "KWD"):
            for code, amount in (("exact", 0), ("rounding", 1)):
                row = TolerancePolicy(
                    id=identity(f"tolerance:{ORG}:{currency}:{code}"),
                    organization_id=ORG,
                    code=code,
                    currency=currency,
                    amount_minor=amount,
                    version=1,
                    created_at=STAMP,
                )
                session.add(row)
                audit_record(
                    session,
                    ORG,
                    ACTOR,
                    row.id,
                    "tolerance_policy_created",
                    {"amount_minor": amount, "currency": currency},
                )
        vendor_ids = {}
        for vendor in data["vendors"]:
            vendor_id = identity(f"vendor:{ORG}:{vendor['name'].casefold()}")
            vendor_ids[vendor["index"]] = vendor_id
            session.add(
                Vendor(
                    id=vendor_id,
                    organization_id=ORG,
                    code=vendor["code"],
                    name=vendor["name"],
                    normalized_name=vendor["name"].casefold(),
                    default_currency=vendor["currency"],
                    active=True,
                    created_at=STAMP,
                )
            )
            audit_record(session, ORG, ACTOR, vendor_id, "vendor_created", {"synthetic": True})
        session.flush()
        orders = {}
        for spec in data["purchase_orders"]:
            vendor = data["vendors"][spec["vendor_index"]]
            po = PurchaseOrder(
                id=identity(spec["reference"]),
                reference=spec["reference"],
                organization_id=ORG,
                vendor_id=vendor_ids[spec["vendor_index"]],
                vendor=vendor["name"],
                description=f"Synthetic order {spec['reference']}",
                currency=spec["currency"],
                amount_minor=spec["amount_minor"],
                subtotal_minor=spec["subtotal_minor"],
                discount_minor=spec["discount_minor"],
                tax_minor=spec["tax_minor"],
                tax_mode=spec["tax_mode"],
                issued_on=date(2026, 10, 1),
                status="open",
                created_at=STAMP,
                line_items=[
                    {
                        "id": line["reference"],
                        "description": line["description"],
                        "quantity": line["quantity"],
                        "unit_price_minor": line["unit_price_minor"],
                        "line_total_minor": line["line_total_minor"],
                    }
                    for line in spec["lines"]
                ],
            )
            session.add(po)
            session.flush()
            normalize_po(session, po, ACTOR)
            orders[spec["reference"]] = po
            audit_record(session, ORG, ACTOR, po.id, "purchase_order_created", {"synthetic": True})
            receipt = GoodsReceipt(
                id=identity("receipt:" + po.id),
                organization_id=ORG,
                purchase_order_id=po.id,
                reference="SYN-GR-" + str(spec["index"] + 1),
                received_on=date(2026, 10, 5),
                created_at=STAMP,
                lines=[
                    {
                        "purchase_order_line_id": row["reference"],
                        "quantity": str(
                            Decimal(row["quantity"]) * Decimal(spec["receipt_fraction"])
                        ),
                    }
                    for row in spec["lines"]
                ],
            )
            session.add(receipt)
            session.flush()
            normalize_receipt(session, receipt, ACTOR)
            audit_record(
                session, ORG, ACTOR, receipt.id, "goods_receipt_created", {"synthetic": True}
            )
        for spec in data["invoices"]:
            _invoice(session, spec, orders[spec["po_reference"]], vendor_ids[spec["vendor_index"]])
        session.add(
            AuditEvent(
                id=marker,
                organization_id=ORG,
                entity_id=ORG,
                actor=ACTOR,
                action="synthetic_dataset_created",
                occurred_at=STAMP,
                detail=json.dumps({"sha256": digest, "counts": expected}, sort_keys=True),
            )
        )
    return expected


def _invoice(session: Session, spec: dict[str, Any], po: PurchaseOrder, vendor_id: str) -> None:
    invoice_id = identity("invoice:" + spec["supplier_invoice_number"])
    code = {
        "invalid_total": "HEADER_TOTAL_MISMATCH",
        "price_discrepancy": "PRICE_MISMATCH",
        "quantity_discrepancy": "QUANTITY_MISMATCH",
        "partial_receipt": "PARTIAL_RECEIPT",
        "tax_discrepancy": "TAX_MISMATCH",
        "duplicate_attempt": "POSSIBLE_DUPLICATE",
    }.get(spec["scenario"])
    initial_status = (
        "exception"
        if spec["status"] == "reviewed_rejected"
        else "matched"
        if spec["status"] == "reviewed_accepted"
        else spec["status"]
    )
    invoice = Invoice(
        id=invoice_id,
        organization_id=ORG,
        vendor_id=vendor_id,
        vendor=spec["vendor_name"],
        description=f"Synthetic {spec['scenario']} invoice",
        supplier_invoice_number=spec["supplier_invoice_number"],
        normalized_invoice_number=normalize_invoice_number(spec["supplier_invoice_number"]),
        currency=spec["currency"],
        invoice_date=date(2026, 10, 1),
        due_on=date(2026, 11, 1),
        purchase_order_id=po.id,
        subtotal_minor=spec["subtotal_minor"],
        discount_minor=spec["discount_minor"],
        tax_minor=spec["tax_minor"],
        tax_mode=spec["tax_mode"],
        amount_minor=spec["amount_minor"],
        financial_state="draft",
        status=initial_status,
        synthetic=True,
        created_at=STAMP,
        document_sha256=spec["pdf_sha256"],
        exception_code=code,
        exception_detail=f"Synthetic labelled scenario: {spec['scenario']}" if code else None,
    )
    session.add(invoice)
    session.flush()
    extraction = ExtractionVersion(
        id=identity("extraction:" + invoice_id),
        organization_id=ORG,
        invoice_id=invoice_id,
        number=1,
        document_sha256=spec["pdf_sha256"],
        origin="synthetic_fixture",
        validation_state="rejected" if spec["scenario"] == "invalid_total" else "validated",
        payload={"synthetic": True, "ground_truth": spec},
        created_at=STAMP,
    )
    session.add(extraction)
    session.flush()
    invoice_lines = []
    for number, line in enumerate(spec["lines"], 1):
        row = InvoiceLine(
            id=identity(f"invoice-line:{invoice_id}:{number}"),
            organization_id=ORG,
            invoice_id=invoice_id,
            extraction_version_id=extraction.id,
            line_number=number,
            purchase_order_id=po.id,
            purchase_order_line_id=identity(f"po-line:{po.id}:{line['reference']}"),
            description=line["description"],
            currency=spec["currency"],
            quantity=Decimal(line["quantity"]),
            unit_price_minor=line["unit_price_minor"],
            line_total_minor=line["line_total_minor"],
            discount_minor=line["discount_minor"],
            tax_rate=Decimal(line["tax_rate"]),
            tax_mode=spec["tax_mode"],
            tax_minor=line["tax_minor"],
            total_minor=line["total_minor"],
            created_at=STAMP,
        )
        session.add(row)
        invoice_lines.append(row)
    for path in (
        "supplier_invoice_number",
        "currency",
        "subtotal_minor",
        "discount_minor",
        "tax_minor",
        "amount_minor",
    ):
        session.add(
            ExtractedField(
                id=identity(f"field:{extraction.id}:{path}"),
                organization_id=ORG,
                extraction_version_id=extraction.id,
                invoice_id=invoice_id,
                path=path,
                value=spec[path],
                page=1,
                source="synthetic_ground_truth",
                confidence=None,
                created_at=STAMP,
            )
        )
    audit_record(
        session,
        ORG,
        ACTOR,
        extraction.id,
        "synthetic_extraction_created",
        {"synthetic": True, "validation_state": extraction.validation_state},
    )
    session.flush()
    if extraction.validation_state == "validated":
        # Keep fixture metadata deterministic; ordinary publication seals use the DB clock.
        session.add(
            ExtractionVersionSeal(
                organization_id=ORG,
                extraction_version_id=extraction.id,
                invoice_id=invoice_id,
                sealed_at=STAMP,
            )
        )
        validate_and_publish_invoice(session, invoice, extraction, ACTOR)
    else:
        audit_record(
            session, ORG, ACTOR, invoice.id, "financial_validation_rejected", {"code": code}
        )
    run = ReconciliationRun(
        id=identity("run:" + invoice_id),
        organization_id=ORG,
        invoice_id=invoice_id,
        extraction_version_id=extraction.id,
        tolerance_policy_id=identity(f"tolerance:{ORG}:{spec['currency']}:exact"),
        policy_id="three_way_exact_v1",
        status="completed",
        outcome=initial_status,
        synthetic=True,
        created_at=STAMP,
    )
    session.add(run)
    session.flush()
    for row in invoice_lines:
        ordered = session.get(PurchaseOrderLine, row.purchase_order_line_id)
        assert ordered is not None
        session.add(
            ReconciliationLineResult(
                id=identity("line-result:" + row.id),
                organization_id=ORG,
                reconciliation_id=run.id,
                invoice_id=invoice_id,
                invoice_line_id=row.id,
                outcome="exception" if code else "matched",
                amount_difference_minor=row.unit_price_minor - ordered.unit_price_minor,
                quantity_difference=row.quantity - ordered.quantity
                if spec["scenario"] == "quantity_discrepancy"
                else Decimal(0),
                created_at=STAMP,
            )
        )
    audit_record(session, ORG, ACTOR, run.id, "synthetic_result_created", {"synthetic": True})
    if code:
        discrepancy_id = identity("discrepancy:" + invoice_id)
        session.add(
            Discrepancy(
                id=discrepancy_id,
                organization_id=ORG,
                invoice_id=invoice_id,
                reconciliation_id=run.id,
                code=code,
                detail=f"Synthetic labelled {spec['scenario']} example",
                field="amount_minor" if spec["scenario"] == "invalid_total" else spec["scenario"],
                expected=str(sum(row.total_minor for row in invoice_lines)),
                observed=str(invoice.amount_minor),
                currency=spec["currency"],
                created_at=STAMP,
            )
        )
        audit_record(
            session,
            ORG,
            ACTOR,
            discrepancy_id,
            "synthetic_discrepancy_created",
            {"synthetic": True},
        )
    if spec["status"] in ("reviewed_accepted", "reviewed_rejected"):
        review_id = identity("review:" + invoice_id)
        session.add(
            ReviewDecision(
                id=review_id,
                organization_id=ORG,
                invoice_id=invoice_id,
                reconciliation_id=run.id,
                invoice_version=invoice.version,
                decision="accept" if spec["status"] == "reviewed_accepted" else "reject",
                reason="Synthetic fixture review example; no spending or payment authorization.",
                actor="synthetic-reviewer",
                user_id=identity(f"user:{ORG}:synthetic-reviewer"),
                created_at=STAMP,
            )
        )
        invoice.status = spec["status"]
        invoice.version += 1
        audit_record(
            session,
            ORG,
            "synthetic-reviewer",
            invoice_id,
            "review_decision_recorded",
            {"before": initial_status, "after": invoice.status, "synthetic": True},
        )
    session.flush()
    if spec["scenario"] == "duplicate_attempt":
        try:
            with session.begin_nested():
                session.add(
                    Invoice(
                        id=identity("rejected-duplicate:" + invoice_id),
                        organization_id=ORG,
                        vendor_id=vendor_id,
                        vendor=invoice.vendor,
                        description="Rejected synthetic duplicate attempt",
                        supplier_invoice_number=" " + spec["supplier_invoice_number"].lower() + " ",
                        normalized_invoice_number=normalize_invoice_number(
                            spec["supplier_invoice_number"]
                        ),
                        currency=invoice.currency,
                        amount_minor=invoice.amount_minor,
                        status="duplicate",
                        financial_state="draft",
                        synthetic=True,
                        created_at=STAMP,
                    )
                )
                session.flush()
                raise AssertionError(
                    "Supplier duplicate constraint did not reject the attempted insert"
                )
        except IntegrityError as error:
            if "normalized_invoice_number" not in str(error.orig):
                raise
        attempt_id = identity("duplicate-attempt:" + invoice_id)
        session.add(
            DuplicateInvoiceAttempt(
                id=attempt_id,
                organization_id=ORG,
                vendor_id=vendor_id,
                existing_invoice_id=invoice_id,
                supplier_invoice_number=" " + spec["supplier_invoice_number"].lower() + " ",
                document_sha256=spec["pdf_sha256"],
                reason="Rejected by tenant/vendor/normalized-number uniqueness",
                created_at=STAMP,
            )
        )
        audit_record(
            session,
            ORG,
            ACTOR,
            attempt_id,
            "duplicate_invoice_rejected",
            {"existing_invoice_id": invoice_id},
        )


def main() -> None:
    engine = build_engine(get_settings())
    try:
        print(json.dumps(seed_financial(engine), sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
