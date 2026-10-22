"Small, deterministic synthetic workspace, deliberately separate from real ingestion."

from datetime import UTC, date, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from reconcileiq_database.config import get_settings
from reconcileiq_database.contract_seed import seed_contracts
from reconcileiq_database.models import AuditEvent, Invoice, PurchaseOrder
from reconcileiq_database.session import build_engine

SAMPLE_DATE = date(2026, 10, 21)


def seed_demo(engine: Engine) -> None:
    rows = [
        ("1042", "Northstar Studio", "Brand design retainer", 480000, "matched", None, None),
        (
            "1043",
            "Acme Office Co.",
            "Office equipment · 12 units",
            186000,
            "exception",
            "QUANTITY_MISMATCH",
            "Invoice lists 12 units; the synthetic receipt lists 10. Review required.",
        ),
        ("1044", "Linear Cloud", "Cloud infrastructure · October", 245000, "matched", None, None),
        (
            "1045",
            "Meridian Logistics",
            "Regional freight services",
            128500,
            "duplicate",
            "POSSIBLE_DUPLICATE",
            "Seeded duplicate example. Compare source documents before any decision.",
        ),
        ("1046", "Paper & Co.", "Sustainable office supplies", 32500, "needs_review", None, None),
        (
            "1047",
            "Atlas Technologies",
            "Engineering workstations",
            864000,
            "exception",
            "PRICE_MISMATCH",
            "Invoice is $240.00 above the synthetic purchase order total.",
        ),
    ]
    with Session(engine) as session, session.begin():
        for index, (number, vendor, description, amount, status, code, detail) in enumerate(rows):
            invoice_id, po_id = f"INV-{number}", f"PO-{8001 + index}"
            if session.get(Invoice, invoice_id):
                continue
            session.add(
                PurchaseOrder(
                    id=po_id,
                    vendor=vendor,
                    description=description,
                    currency="USD",
                    amount_minor=amount - (24000 if code == "PRICE_MISMATCH" else 0),
                    issued_on=date(2026, 10, 1 + index),
                    status="open" if code else "received",
                )
            )
            session.flush()
            session.add(
                Invoice(
                    id=invoice_id,
                    vendor=vendor,
                    description=description,
                    currency="USD",
                    amount_minor=amount,
                    due_on=date(2026, 10, 23 + index),
                    status=status,
                    purchase_order_id=po_id,
                    exception_code=code,
                    exception_detail=detail,
                )
            )
            session.add_all(
                [
                    AuditEvent(
                        id=f"seed-{entity_id}",
                        entity_id=entity_id,
                        actor="local-seed",
                        action="demo_record_created",
                        detail=(
                            "Synthetic fixture created. Status is illustrative, not an approval."
                        ),
                        occurred_at=datetime(2026, 10, 21, 9, index, tzinfo=UTC),
                    )
                    for entity_id in (po_id, invoice_id)
                ]
            )


def main() -> None:
    engine = build_engine(get_settings())
    try:
        seed_demo(engine)
        seed_contracts(engine, get_settings())
    finally:
        engine.dispose()
    print("Synthetic workspace seeded (idempotent).")


if __name__ == "__main__":
    main()
