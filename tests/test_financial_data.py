import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from reconcileiq_api.main import create_app
from reconcileiq_database.config import get_settings
from reconcileiq_database.financial import (
    audit_record,
    identity,
    normalize_po,
    validate_and_publish_invoice,
)
from reconcileiq_database.financial_seed import seed_financial
from reconcileiq_database.fixture_data import ORG, dataset
from reconcileiq_database.models import (
    AuditEvent,
    Credential,
    DuplicateInvoiceAttempt,
    ExtractedField,
    ExtractionVersion,
    ExtractionVersionSeal,
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    Job,
    JobAttempt,
    JobExecution,
    Membership,
    Organization,
    PurchaseOrder,
    PurchaseOrderLine,
    ReconciliationRun,
    ReviewDecision,
    User,
    Vendor,
)
from sqlalchemy import Engine, func, insert, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm import Session


@pytest.fixture
def populated(finance_engine: Engine) -> Engine:
    seed_financial(finance_engine)
    return finance_engine


def first_invoice(session: Session, scenario: str = "exact") -> Invoice:
    spec = next(row for row in dataset()["invoices"] if row["scenario"] == scenario)
    row = session.get(Invoice, identity("invoice:" + spec["supplier_invoice_number"]))
    assert row is not None
    return row


def stub_invoice(**values: Any) -> Invoice:
    defaults: dict[str, Any] = dict(
        id=uuid4().hex,
        organization_id=ORG,
        vendor="Synthetic test vendor",
        description="Synthetic constraint probe",
        currency="USD",
        amount_minor=100,
        status="needs_review",
        financial_state="draft",
        synthetic=True,
    )
    return Invoice(**(defaults | values))


def test_seed_counts_repeatability_and_no_simulated_jobs(populated: Engine) -> None:
    with Session(populated) as session:
        before = session.scalar(select(func.count()).select_from(AuditEvent))
        assert (
            session.scalar(
                select(func.count()).select_from(Invoice).where(Invoice.organization_id == ORG)
            )
            == 200
        )
        assert session.scalar(select(func.count()).select_from(PurchaseOrderLine)) == 200
        assert session.scalar(select(func.count()).select_from(InvoiceLine)) == 400
        assert session.scalar(select(func.count()).select_from(GoodsReceiptLine)) == 200
        assert session.scalar(select(func.count()).select_from(ExtractedField)) == 1200
        assert session.scalar(select(func.count()).select_from(DuplicateInvoiceAttempt)) == 20
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExtractionVersion)
                .where(ExtractionVersion.validation_state == "rejected")
            )
            == 20
        )
        order = session.get(PurchaseOrder, identity("SYN-PO-0001"))
        assert order is not None and len(order.financial_lines) == 2
        invoice = first_invoice(session)
        assert len(invoice.extractions) == 1
        assert len(invoice.extractions[0].fields) == 6
    seed_financial(populated)
    with Session(populated) as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == before
    command.check(Config("alembic.ini"))


@pytest.mark.parametrize(
    "relationship",
    ["vendor", "purchase_order", "invoice_line", "job_invoice", "job_replay", "outbox"],
)
def test_database_rejects_cross_tenant_references(populated: Engine, relationship: str) -> None:
    with Session(populated) as session, session.begin():
        session.add(Organization(id="other", name="Other synthetic tenant"))
        session.flush()
        invoice = first_invoice(session)
        with pytest.raises(IntegrityError), session.begin_nested():
            if relationship == "vendor":
                session.add(stub_invoice(organization_id="other", vendor_id=invoice.vendor_id))
            elif relationship == "purchase_order":
                session.add(
                    stub_invoice(
                        organization_id="other", purchase_order_id=invoice.purchase_order_id
                    )
                )
            elif relationship == "invoice_line":
                original = session.scalar(
                    select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
                )
                assert original is not None
                values = {
                    column.name: getattr(original, column.name)
                    for column in InvoiceLine.__table__.columns
                }
                values.update(id=uuid4().hex, organization_id="other", line_number=99)
                session.add(InvoiceLine(**values))
            elif relationship == "job_invoice":
                session.add(
                    Job(
                        id=uuid4().hex,
                        organization_id="other",
                        kind="extraction",
                        invoice_id=invoice.id,
                    )
                )
            elif relationship == "job_replay":
                job = Job(id=uuid4().hex, organization_id=ORG, kind="extraction")
                session.add(job)
                session.flush()
                session.add(
                    Job(
                        id=uuid4().hex,
                        organization_id="other",
                        kind="extraction",
                        replay_of=job.id,
                    )
                )
            else:
                from reconcileiq_database.models import OutboxEvent

                job = Job(id=uuid4().hex, organization_id=ORG, kind="extraction")
                session.add(job)
                session.flush()
                session.add(
                    OutboxEvent(id=uuid4().hex, organization_id="other", job_id=job.id, payload={})
                )
            session.flush()


@pytest.mark.parametrize("mismatch", ["currency", "vendor"])
def test_invoice_and_purchase_order_consistency_is_a_database_rule(
    populated: Engine, mismatch: str
) -> None:
    with Session(populated) as session, session.begin():
        invoice = first_invoice(session)
        changes = (
            {("currency"): "EUR"}
            if mismatch == "currency"
            else {"vendor_id": identity("vendor:" + ORG + ":synthetic f development supplies")}
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(stub_invoice(purchase_order_id=invoice.purchase_order_id, **changes))
            session.flush()


def test_supplier_duplicate_rejected_but_distinct_vendors_and_tenants_are_allowed(
    populated: Engine,
) -> None:
    with Session(populated) as session, session.begin():
        invoice = first_invoice(session)
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                stub_invoice(
                    vendor_id=invoice.vendor_id,
                    supplier_invoice_number=" " + invoice.supplier_invoice_number.lower() + " ",  # type: ignore[union-attr]
                    normalized_invoice_number=invoice.normalized_invoice_number,
                )
            )
            session.flush()
        vendor = session.scalar(
            select(Vendor).where(Vendor.organization_id == ORG, Vendor.id != invoice.vendor_id)
        )
        assert vendor is not None
        session.add(
            stub_invoice(
                vendor_id=vendor.id,
                supplier_invoice_number=invoice.supplier_invoice_number,
                normalized_invoice_number=invoice.normalized_invoice_number,
            )
        )
        session.add(Organization(id="second", name="Second synthetic tenant"))
        session.flush()
        session.add(
            Vendor(
                id="second-vendor",
                organization_id="second",
                code="SYN-SECOND",
                name="Synthetic second",
                normalized_name="synthetic second",
                default_currency="USD",
            )
        )
        session.flush()
        session.add(
            stub_invoice(
                organization_id="second",
                vendor_id="second-vendor",
                supplier_invoice_number=invoice.supplier_invoice_number,
                normalized_invoice_number=invoice.normalized_invoice_number,
            )
        )


def test_receipt_line_must_belong_to_the_receipts_purchase_order(populated: Engine) -> None:
    with Session(populated) as session, session.begin():
        po = session.get(PurchaseOrder, identity("SYN-PO-0001"))
        other = session.get(PurchaseOrder, identity("SYN-PO-0002"))
        assert po is not None and other is not None
        receipt = GoodsReceipt(
            id=uuid4().hex,
            organization_id=ORG,
            purchase_order_id=po.id,
            reference="SYN-BAD-PARENT",
            received_on=po.issued_on,
            lines=[],
        )
        session.add(receipt)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                GoodsReceiptLine(
                    id=uuid4().hex,
                    organization_id=ORG,
                    goods_receipt_id=receipt.id,
                    purchase_order_id=po.id,
                    purchase_order_line_id=other.financial_lines[0].id,
                    quantity=Decimal("1"),
                )
            )
            session.flush()


@pytest.mark.parametrize(
    "field,value", [("line_total_minor", 1), ("tax_minor", 999), ("total_minor", 999)]
)
def test_database_enforces_line_arithmetic(populated: Engine, field: str, value: int) -> None:
    with Session(populated) as session, session.begin():
        invoice = first_invoice(session, "invalid_total")
        row = session.scalar(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))
        assert row is not None
        values = {
            column.name: getattr(row, column.name) for column in InvoiceLine.__table__.columns
        }
        values.update(id=uuid4().hex, line_number=3)
        values[field] = value
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(insert(InvoiceLine).values(**values))


def test_invalid_totals_cannot_be_promoted_and_existing_valid_totals_cannot_drift(
    populated: Engine,
) -> None:
    with Session(populated) as session, session.begin():
        invalid = first_invoice(session, "invalid_total")
        version = session.scalar(
            select(ExtractionVersion).where(ExtractionVersion.invoice_id == invalid.id)
        )
        assert version is not None and invalid.financial_state == "draft"
        with pytest.raises(ValueError):
            validate_and_publish_invoice(session, invalid, version, "synthetic-test")
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                text(
                    "UPDATE invoices SET financial_state='validated', "
                    "validated_extraction_id=:version WHERE id=:id"
                ),
                {("version"): version.id, "id": invalid.id},
            )
        valid = first_invoice(session)
        before = valid.amount_minor
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                text("UPDATE invoices SET amount_minor=amount_minor+1 WHERE id=:id"),
                {("id"): valid.id},
            )
        session.refresh(valid)
        assert valid.amount_minor == before


def test_untrusted_extraction_header_mismatch_rejected_in_pure_validation(
    populated: Engine,
) -> None:
    with Session(populated) as session, session.begin():
        invoice = first_invoice(session, "invalid_total")
        # A new declared-valid version does not bypass independent deterministic validation.
        version = ExtractionVersion(
            id=uuid4().hex,
            organization_id=ORG,
            invoice_id=invoice.id,
            number=2,
            origin="synthetic_fixture",
            validation_state="validated",
            payload={},
        )
        session.add(version)
        session.flush()
        for original in session.scalars(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
        ):
            values = {
                column.name: getattr(original, column.name)
                for column in InvoiceLine.__table__.columns
            }
            values.update(id=uuid4().hex, extraction_version_id=version.id)
            session.add(InvoiceLine(**values))
        session.flush()
        with pytest.raises(ValueError):
            validate_and_publish_invoice(session, invoice, version, "synthetic-test")


@pytest.mark.parametrize(
    "table",
    [
        "extraction_versions",
        "extracted_fields",
        "invoice_lines",
        "review_decisions",
        "audit_events",
    ],
)
def test_financial_history_is_immutable(populated: Engine, table: str) -> None:
    with populated.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(text(f"DELETE FROM {table}"))


def test_exact_decimal_columns_reject_float_parameters(populated: Engine) -> None:
    with Session(populated) as session, session.begin():
        invoice = first_invoice(session, "invalid_total")
        row = session.scalar(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))
        assert row is not None
        values = {
            column.name: getattr(row, column.name) for column in InvoiceLine.__table__.columns
        }
        values.update(id=uuid4().hex, line_number=3, quantity=0.1)
        with pytest.raises(StatementError), session.begin_nested():
            session.execute(insert(InvoiceLine).values(**values))


def test_partial_failure_rolls_back_header_lines_vendor_and_audit(populated: Engine) -> None:
    models = (Vendor, PurchaseOrder, PurchaseOrderLine, AuditEvent)
    with Session(populated) as session:
        before = [session.scalar(select(func.count()).select_from(model)) for model in models]
    with (
        pytest.raises(RuntimeError, match="injected"),
        Session(populated) as session,
        session.begin(),
    ):
        po = PurchaseOrder(
            id=uuid4().hex,
            organization_id=ORG,
            vendor="Synthetic rollback vendor",
            description="Synthetic rollback",
            currency="KWD",
            amount_minor=750,
            subtotal_minor=750,
            discount_minor=0,
            tax_minor=0,
            issued_on=datetime(2026, 10, 1).date(),
            status="open",
            line_items=[
                {
                    "id": "ONE",
                    "description": "Synthetic item",
                    "quantity": "2.5",
                    "unit_price_minor": 300,
                    "line_total_minor": 750,
                }
            ],
        )
        session.add(po)
        session.flush()
        normalize_po(session, po, "synthetic-test")
        audit_record(session, ORG, "synthetic-test", po.id, "purchase_order_created", {})
        session.flush()
        raise RuntimeError("injected failure after aggregate persistence")
    with Session(populated) as session:
        assert [
            session.scalar(select(func.count()).select_from(model)) for model in models
        ] == before


def test_job_recovery_structure_bounds_attempts_and_preserves_pending_state(
    finance_engine: Engine,
) -> None:
    with Session(finance_engine) as session, session.begin():
        job = Job(id=uuid4().hex, organization_id="demo", kind="extraction", max_attempts=2)
        session.add(job)
        session.flush()
        assert job.state == "pending_dispatch"
        attempt = JobAttempt(
            id=uuid4().hex,
            organization_id="demo",
            job_id=job.id,
            number=1,
            state="running",
            worker="synthetic-test",
        )
        session.add(attempt)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                JobAttempt(
                    id=uuid4().hex,
                    organization_id="demo",
                    job_id=job.id,
                    number=2,
                    state="running",
                    worker="synthetic-test",
                )
            )
            session.flush()
        attempt.state = "failed"
        attempt.finished_at = datetime.now(UTC) + timedelta(seconds=1)
        session.flush()
        with pytest.raises(DBAPIError), session.begin_nested():
            session.add(
                JobAttempt(
                    id=uuid4().hex,
                    organization_id="demo",
                    job_id=job.id,
                    number=3,
                    state="running",
                    worker="synthetic-test",
                )
            )
            session.flush()
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(
                text("UPDATE job_attempts SET state='running', finished_at=NULL WHERE id=:id"),
                {("id"): attempt.id},
            )
        assert job.state == "pending_dispatch"


def test_job_execution_business_keys_are_tenant_scoped(finance_engine: Engine) -> None:
    with Session(finance_engine) as session, session.begin():
        session.add(Organization(id="other", name="Other synthetic tenant"))
        session.flush()
        for org in ("demo", "other"):
            session.add(
                JobExecution(
                    organization_id=org,
                    key="same-effect-key",
                    result="ok",
                    completed_at=datetime.now(UTC),
                )
            )
    with Session(finance_engine) as session:
        assert session.get(JobExecution, ("demo", "same-effect-key")) is not None
        assert session.get(JobExecution, ("other", "same-effect-key")) is not None


def test_concurrent_review_has_one_decision_and_one_conflict(populated: Engine) -> None:
    token = "synthetic-review-test-" + "x" * 40
    user_id = identity(f"user:{ORG}:synthetic-reviewer")
    with Session(populated) as session, session.begin():
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id=ORG,
                actor="synthetic-test-reviewer",
                role="reviewer",
                user_id=user_id,
            )
        )
        row = first_invoice(session)
        invoice_id, version = row.id, row.version
        run = session.scalar(
            select(ReconciliationRun).where(ReconciliationRun.invoice_id == invoice_id)
        )
        assert run is not None
        run_id = run.id
    with TestClient(create_app(get_settings())) as client:

        def review(index: int) -> int:
            response = client.post(
                f"/v1/invoices/{invoice_id}/review-decisions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": f"concurrent-review-{index}",
                },
                json={
                    "expected_version": version,
                    "reconciliation_id": run_id,
                    "decision": "accept",
                    "reason": "Synthetic concurrent review test",
                },
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(review, range(2))) == [201, 409]
    with Session(populated) as session:
        assert (
            session.scalar(
                select(ReviewDecision.user_id).where(ReviewDecision.invoice_id == invoice_id)
            )
            == user_id
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReviewDecision)
                .where(ReviewDecision.invoice_id == invoice_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.entity_id == invoice_id,
                    AuditEvent.action == "review_decision_recorded",
                )
            )
            == 1
        )


@pytest.mark.parametrize("deactivated", ["user", "membership"])
def test_linked_identity_deactivation_revokes_authentication(
    populated: Engine, deactivated: str
) -> None:
    token = "synthetic-identity-test-" + "x" * 40
    user_id = identity(f"user:{ORG}:synthetic-auditor")
    with Session(populated) as session, session.begin():
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id=ORG,
                actor="synthetic-auditor",
                role="auditor",
                user_id=user_id,
            )
        )
    with TestClient(create_app(get_settings())) as client:
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/v1/demo/snapshot", headers=headers).status_code == 200
        with Session(populated) as session, session.begin():
            if deactivated == "user":
                user = session.get(User, user_id)
                assert user is not None
                user.active = False
            else:
                membership = session.get(Membership, (ORG, user_id))
                assert membership is not None
                membership.active = False
        assert client.get("/v1/demo/snapshot", headers=headers).status_code == 401


def test_expand_migration_can_roll_back_only_while_unused(finance_engine: Engine) -> None:
    configuration = Config("alembic.ini")
    with Session(finance_engine) as session, session.begin():
        session.add(
            Vendor(
                id=uuid4().hex,
                organization_id="demo",
                code="SYN-ROLLBACK",
                name="Synthetic rollback vendor",
                normalized_name="synthetic rollback vendor",
                default_currency="USD",
            )
        )
    with Session(finance_engine) as session:
        before = session.scalar(select(func.count()).select_from(AuditEvent))
    command.downgrade(configuration, "0003_financial")
    assert "external_reference" not in {
        column["name"] for column in inspect(finance_engine).get_columns("vendors")
    }
    command.upgrade(configuration, "head")
    with Session(finance_engine) as session, session.begin():
        vendor = session.scalar(select(Vendor).where(Vendor.organization_id == "demo"))
        assert vendor is not None
        vendor.external_reference = "SYN-EXTERNAL-REFERENCE"
    with pytest.raises(RuntimeError, match="references are in use"):
        command.downgrade(configuration, "0003_financial")
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == before
    command.check(configuration)


@pytest.mark.parametrize("record", ["line", "field"])
def test_superseded_published_versions_still_reject_append(populated: Engine, record: str) -> None:
    with Session(populated) as session, session.begin():
        invoice = first_invoice(session)
        invoice_id, previous_id = invoice.id, invoice.validated_extraction_id
        previous = session.get(ExtractionVersion, previous_id)
        assert previous is not None
        newer = ExtractionVersion(
            id=uuid4().hex,
            organization_id=ORG,
            invoice_id=invoice.id,
            number=2,
            origin="synthetic_test",
            validation_state="validated",
            payload={"synthetic": True},
        )
        session.add(newer)
        session.flush()
        for line in previous.financial_lines:
            values = {
                column.name: getattr(line, column.name) for column in InvoiceLine.__table__.columns
            }
            values.update(id=uuid4().hex, extraction_version_id=newer.id)
            session.add(InvoiceLine(**values))
        session.flush()
        validate_and_publish_invoice(session, invoice, newer, "synthetic-reviewer")
        assert invoice.validated_extraction_id != previous_id
    with Session(populated) as session, session.begin(), pytest.raises(DBAPIError):
        if record == "line":
            original = session.scalar(
                select(InvoiceLine).where(InvoiceLine.extraction_version_id == previous_id)
            )
            assert original is not None
            values = {
                column.name: getattr(original, column.name)
                for column in InvoiceLine.__table__.columns
            }
            values.update(id=uuid4().hex, line_number=3)
            session.add(InvoiceLine(**values))
        else:
            session.add(
                ExtractedField(
                    id=uuid4().hex,
                    organization_id=ORG,
                    invoice_id=invoice_id,
                    extraction_version_id=previous_id,
                    path="late_append",
                    page=1,
                    value="untrusted extra evidence",
                    source="synthetic_test",
                )
            )
        session.flush()


def test_published_version_seals_are_immutable_and_prevent_unsafe_downgrade(
    populated: Engine,
) -> None:
    with Session(populated) as session:
        before = session.scalar(select(func.count()).select_from(ExtractionVersionSeal))
        assert before == 180
    with populated.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(text("DELETE FROM extraction_version_seals"))
    with pytest.raises(RuntimeError, match="preserve published history"):
        command.downgrade(Config("alembic.ini"), "0004_vendor_ref")
    with Session(populated) as session:
        assert session.scalar(select(func.count()).select_from(ExtractionVersionSeal)) == before


def test_partial_receipts_preserve_exact_quantities_and_original_orders(populated: Engine) -> None:
    partial_orders = 0
    with Session(populated) as session:
        for spec in dataset()["purchase_orders"]:
            po = session.get(PurchaseOrder, identity(spec["reference"]))
            assert po is not None
            received = session.scalars(
                select(GoodsReceiptLine).where(GoodsReceiptLine.purchase_order_id == po.id)
            ).all()
            assert len(received) == len(po.financial_lines) == 2
            fraction = Decimal(spec["receipt_fraction"])
            partial_orders += fraction < 1
            for line in po.financial_lines:
                quantity = sum(
                    (row.quantity for row in received if row.purchase_order_line_id == line.id),
                    Decimal(0),
                )
                assert quantity == line.quantity * fraction
                source = next(row for row in spec["lines"] if row["reference"] == line.reference)
                assert line.quantity == Decimal(source["quantity"])
                assert line.line_total_minor == source["line_total_minor"]
        assert partial_orders == 20


def test_concurrent_supplier_duplicates_commit_one_invoice_and_audit(
    finance_engine: Engine,
) -> None:
    from threading import Barrier

    vendor_id = uuid4().hex
    with Session(finance_engine) as session, session.begin():
        session.add(
            Vendor(
                id=vendor_id,
                organization_id="demo",
                code="SYN-CONCURRENT",
                name="Synthetic concurrent",
                normalized_name="synthetic concurrent",
                default_currency="USD",
            )
        )
    barrier = Barrier(2)

    def insert_duplicate(index: int) -> str:
        barrier.wait(timeout=5)
        try:
            with Session(finance_engine) as session, session.begin():
                invoice = stub_invoice(
                    organization_id="demo",
                    vendor_id=vendor_id,
                    supplier_invoice_number="same-001",
                    normalized_invoice_number="SAME-001",
                )
                session.add(invoice)
                session.flush()
                audit_record(session, "demo", "synthetic-test", invoice.id, "created", {})
            return "committed"
        except IntegrityError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(insert_duplicate, range(2))) == ["committed", "duplicate"]
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
