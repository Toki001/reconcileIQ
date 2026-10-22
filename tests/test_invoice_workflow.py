import hashlib
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from reconcileiq_ai.invoice import InvoiceCandidate
from reconcileiq_ai.provider import FREE_MODEL, PROMPT_VERSION, ExtractionResult, ProviderFailure
from reconcileiq_api.main import create_app
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import (
    Credential,
    ExtractionVersion,
    Invoice,
    Job,
    JobExecution,
    OutboxEvent,
    ProviderUsage,
    StructuredRequest,
)
from reconcileiq_worker.processing import process_job
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_processing import MemoryReader, admit, due
from test_structured_evidence import candidate


class Documents:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def grant(self, key: str) -> str:
        return "https://example.invalid/synthetic"

    def put_document(self, key: str, data: bytes) -> None:
        self.data[key] = data

    def read_document(self, key: str, size: int, digest: str) -> bytes:
        return self.data[key]

    def verify_and_seal(self, key: str, org: str, size: int, digest: str) -> str:
        sealed = f"{org}/sealed/{uuid4().hex}.pdf"
        self.data[sealed] = self.data[key]
        return sealed


def client_for(engine: Engine, role: str = "reviewer") -> TestClient:
    token = "workflow-" + role + "x" * 40
    with Session(engine) as session, session.begin():
        digest = hashlib.sha256(token.encode()).hexdigest()
        if session.get(Credential, digest) is None:
            session.add(
                Credential(
                    digest=digest, organization_id="demo", actor="workflow-" + role, role=role
                )
            )
    return TestClient(
        create_app(get_settings(), Documents()), headers={"Authorization": f"Bearer {token}"}
    )


def send(
    client: TestClient, path: str, body: dict[str, Any], key: str | None = None
) -> dict[str, Any]:
    response = client.post(path, json=body, headers={"Idempotency-Key": key or uuid4().hex})
    assert response.status_code in {200, 201, 202}, response.text
    return response.json()  # type: ignore[no-any-return]


def setup_invoice(engine: Engine, client: TestClient) -> str:
    admin = client_for(engine, "admin")
    send(
        admin,
        "/v1/purchase-orders",
        {
            "vendor": "Synthetic Golden Supplies",
            "reference": "GOLD-PO",
            "description": "Synthetic golden order",
            "currency": "USD",
            "issued_on": "2026-10-01",
            "tax_mode": "exclusive",
            "lines": [
                {
                    "id": "ITEM-1",
                    "description": "Synthetic component",
                    "quantity": "2",
                    "unit_price_minor": 100,
                    "line_total_minor": 200,
                }
            ],
            "subtotal_minor": 200,
            "discount_minor": 0,
            "tax_minor": 0,
            "amount_minor": 200,
        },
    )
    invoice, job = admit(
        engine, __import__("pathlib").Path("packages/ai/evals/documents/gold-001.pdf").read_bytes()
    )
    process_job(
        engine,
        get_settings(),
        "demo",
        job,
        MemoryReader(
            __import__("pathlib").Path("packages/ai/evals/documents/gold-001.pdf").read_bytes()
        ),
    )
    return invoice


def workspace(client: TestClient, invoice: str) -> dict[str, Any]:
    response = client.get(f"/v1/invoices/{invoice}/workspace")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def correct(
    client: TestClient, invoice: str, value: InvoiceCandidate | None = None
) -> dict[str, Any]:
    current = workspace(client, invoice)
    value = value or candidate()
    value.vendor_identifier.value = None
    value.vendor_identifier.status = "missing"
    value.vendor_identifier.pages, value.vendor_identifier.quote = [], None
    return send(
        client,
        f"/v1/invoices/{invoice}/corrections",
        {
            "expected_version": current["invoice"]["version"],
            "source_version_id": current["candidates"][0]["id"] if current["candidates"] else None,
            "reason": "Corrected from synthetic original document",
            "candidate": (value or candidate()).model_dump(),
        },
    )


def reconcile_invoice(
    engine: Engine, client: TestClient, invoice: str, policy: str = "two_way_exact_v1"
) -> dict[str, Any]:
    current = workspace(client, invoice)
    result = send(
        client,
        "/v1/reconciliations",
        {
            "invoice_id": invoice,
            "expected_version": current["invoice"]["version"],
            "policy_id": policy,
        },
    )
    process_job(engine, get_settings(), "demo", result["job_id"])
    return workspace(client, invoice)


def test_correction_matching_acceptance_immutable_and_idempotent(finance_engine: Engine) -> None:
    client = client_for(finance_engine)
    invoice = setup_invoice(finance_engine, client)
    recorded = correct(client, invoice)
    assert recorded["origin"] == "human_correction"
    assert recorded["candidate"]["invoice_number"]["status"] == "human"
    current = reconcile_invoice(finance_engine, client, invoice)
    assert current["result"]["outcome"] == "matched"
    body = {
        "expected_version": current["invoice"]["version"],
        "reconciliation_id": current["result"]["reconciliation_id"],
        "decision": "accept",
        "reason": "Verified original document and exact purchase order",
    }
    first = send(client, f"/v1/invoices/{invoice}/review-decisions", body, "review-workflow-001")
    assert (
        send(client, f"/v1/invoices/{invoice}/review-decisions", body, "review-workflow-001")
        == first
    )
    current = workspace(client, invoice)
    assert current["invoice"]["status"] == "reviewed_accepted"
    assert current["invoice"]["amount_minor"] == 200
    with Session(finance_engine) as session:
        row = session.get(Invoice, invoice)
        assert row is not None and row.financial_state == "validated"
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 2
    with pytest.raises(IntegrityError), finance_engine.begin() as connection:
        connection.execute(text("UPDATE reconciliation_evidence SET context_sha256='bad'"))
    with pytest.raises(IntegrityError), finance_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM extracted_fields WHERE extraction_version_id=:id"),
            {"id": recorded["id"]},
        )


def test_discrepancy_stale_candidate_and_auditor_cannot_accept(finance_engine: Engine) -> None:
    client = client_for(finance_engine)
    invoice = setup_invoice(finance_engine, client)
    correct(client, invoice)
    current = reconcile_invoice(finance_engine, client, invoice, "three_way_exact_v1")
    assert current["result"]["outcome"] == "discrepancy"
    body = {
        "expected_version": current["invoice"]["version"],
        "reconciliation_id": current["result"]["reconciliation_id"],
        "decision": "accept",
        "reason": "A reason cannot bypass deterministic discrepancies",
    }
    assert (
        client.post(
            f"/v1/invoices/{invoice}/review-decisions",
            json=body,
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 409
    )
    auditor = client_for(finance_engine, "auditor")
    assert (
        auditor.post(
            f"/v1/invoices/{invoice}/review-decisions",
            json=body,
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 403
    )
    correct(client, invoice)
    body["expected_version"] = workspace(client, invoice)["invoice"]["version"]
    assert (
        client.post(
            f"/v1/invoices/{invoice}/review-decisions",
            json=body,
            headers={"Idempotency-Key": uuid4().hex},
        ).status_code
        == 409
    )
    assert client.get("/v1/invoices/other-tenant/workspace").status_code == 404


def ai_request(engine: Engine, invoice: str) -> str:
    with Session(engine) as session, session.begin():
        row = session.get(Invoice, invoice)
        assert row is not None
        source = session.scalar(
            select(ExtractionVersion).where(
                ExtractionVersion.invoice_id == invoice,
                ExtractionVersion.schema_version == "digital-text-v1",
            )
        )
        assert source is not None
        job = Job(id=uuid4().hex, organization_id="demo", invoice_id=invoice, kind="extraction")
        session.add(job)
        session.flush()
        session.add(
            OutboxEvent(
                id=uuid4().hex, organization_id="demo", job_id=job.id, payload={"structured": True}
            )
        )
        row.version += 1
        session.add(
            StructuredRequest(
                job_id=job.id,
                organization_id="demo",
                invoice_id=invoice,
                text_version_id=source.id,
                invoice_version=row.version,
                request_id=job.id,
                model=FREE_MODEL,
                prompt_version=PROMPT_VERSION,
                synthetic_authorized=True,
                requested_by="test",
            )
        )
        return job.id


class MockProvider:
    def extract(self, pages: dict[int, str]) -> ExtractionResult:
        return ExtractionResult(candidate(), FREE_MODEL, PROMPT_VERSION, 123, 456)


def test_structured_worker_redelivery_failure_preserves_evidence_and_retry(
    finance_engine: Engine,
) -> None:
    client = client_for(finance_engine)
    invoice = setup_invoice(finance_engine, client)
    job = ai_request(finance_engine, invoice)
    process_job(finance_engine, get_settings(), "demo", job, invoice_provider=MockProvider())
    process_job(finance_engine, get_settings(), "demo", job, invoice_provider=MockProvider())
    current = workspace(client, invoice)
    assert len(current["candidates"]) == 1 and current["candidates"][0]["origin"] == "ai_gemini"
    failed = ai_request(finance_engine, invoice)

    class Unavailable:
        def extract(self, pages: dict[int, str]) -> ExtractionResult:
            raise ProviderFailure("AI_QUOTA_EXHAUSTED", True, 71)

    for _ in range(5):
        due(finance_engine, failed)
        process_job(finance_engine, get_settings(), "demo", failed, invoice_provider=Unavailable())
    after = workspace(client, invoice)
    assert (
        after["candidates"] == current["candidates"] and after["invoice"]["status"] == "extracted"
    )
    with Session(finance_engine) as session:
        row = session.get(Job, failed)
        assert row and row.state == "failed" and row.attempts == 5
        usage = session.scalars(
            select(ProviderUsage).where(ProviderUsage.operation == "generation")
        ).all()
        assert len(usage) == 6 and not any(u.free_tier for u in usage)
        assert session.scalar(select(func.count()).select_from(StructuredRequest)) == 2
    admin = client_for(finance_engine, "admin")
    replay = send(
        admin,
        f"/v1/jobs/{failed}/replay",
        {"expected_version": row.version, "reason": "Synthetic quota recovered for replay"},
    )
    process_job(
        finance_engine, get_settings(), "demo", replay["job_id"], invoice_provider=MockProvider()
    )
    assert len(workspace(client, invoice)["candidates"]) == 2


def test_live_ai_fails_closed_and_authenticated_document_upload(engine: Engine) -> None:
    client = client_for(engine)
    assert client.get("/v1/ai/status").json()["available"] is False
    data = b"%PDF-1.4 synthetic only"
    grant = send(
        client,
        "/v1/invoices/upload-requests",
        {
            "filename": "test.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    invoice = grant["invoice_id"]
    assert (
        client.put(
            f"/v1/invoices/{invoice}/document",
            content=b"wrong",
            headers={"Content-Type": "application/pdf"},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/v1/invoices/{invoice}/document",
            content=data,
            headers={"Content-Type": "application/pdf"},
        ).status_code
        == 204
    )
    send(client, f"/v1/invoices/{invoice}/upload-complete", {"expected_version": 1})
    assert client.get(f"/v1/invoices/{invoice}/document").content == data
    response = client.post(
        f"/v1/invoices/{invoice}/structured-extractions",
        json={"expected_version": 2, "synthetic_authorized": True},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 503 and response.json()["code"] == "FREE_TIER_UNVERIFIED"


def test_failed_initial_ai_is_classified_and_permanent_model_error(engine: Engine) -> None:
    client = client_for(engine)
    invoice = setup_invoice(engine, client)
    job = ai_request(engine, invoice)

    class InvalidModel:
        def extract(self, pages: dict[int, str]) -> ExtractionResult:
            raise ProviderFailure("AI_MODEL_OR_REQUEST_INVALID")

    process_job(engine, get_settings(), "demo", job, invoice_provider=InvalidModel())
    state = workspace(client, invoice)
    assert state["result"]["outcome"] == "extraction_failed"
    assert state["result"]["differences"][0]["code"] == "AI_MODEL_OR_REQUEST_INVALID"
    with Session(engine) as session:
        row = session.get(Job, job)
        assert row is not None and row.state == "failed" and row.attempts == 1
    assert not state["candidates"]


def test_receipt_change_invalidates_review_snapshot(engine: Engine) -> None:
    client = client_for(engine)
    invoice = setup_invoice(engine, client)
    correct(client, invoice)
    state = reconcile_invoice(engine, client, invoice)
    order_id = state["current_context"]["order"]["id"]
    admin = client_for(engine, "admin")
    send(
        admin,
        "/v1/goods-receipts",
        {
            "purchase_order_id": order_id,
            "reference": "NEW-RECEIPT",
            "received_on": "2026-10-02",
            "lines": [{"purchase_order_line_id": "ITEM-1", "quantity": "2"}],
        },
    )
    response = client.post(
        f"/v1/invoices/{invoice}/review-decisions",
        json={
            "expected_version": state["invoice"]["version"],
            "reconciliation_id": state["result"]["reconciliation_id"],
            "decision": "accept",
            "reason": "Review cannot use an obsolete comparison snapshot",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 409 and response.json()["code"] == "STALE_RECONCILIATION"


def test_candidate_commit_failure_rolls_back_then_retries_once(engine: Engine) -> None:
    from sqlalchemy import event

    client = client_for(engine)
    invoice = setup_invoice(engine, client)
    job = ai_request(engine, invoice)
    failed = False

    def inject(session: Session, context: object, instances: object) -> None:
        nonlocal failed
        if not failed and any(
            isinstance(row, ExtractionVersion) and row.origin == "ai_gemini" for row in session.new
        ):
            failed = True
            raise RuntimeError("injected local commit failure")

    event.listen(Session, "before_flush", inject)
    try:
        process_job(engine, get_settings(), "demo", job, invoice_provider=MockProvider())
    finally:
        event.remove(Session, "before_flush", inject)
    assert failed and not workspace(client, invoice)["candidates"]
    due(engine, job)
    process_job(engine, get_settings(), "demo", job, invoice_provider=MockProvider())
    process_job(engine, get_settings(), "demo", job, invoice_provider=MockProvider())
    assert len(workspace(client, invoice)["candidates"]) == 1


def test_structured_admission_idempotency_and_source_fencing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reconcileiq_api.workflow_contracts import ProviderStatus

    client = client_for(engine)
    invoice = setup_invoice(engine, client)
    monkeypatch.setattr(
        "reconcileiq_api.workflow_rest.status_for",
        lambda *_: ProviderStatus(available=True, model=FREE_MODEL, code="VERIFIED_FREE_TIER"),
    )
    # An injected verification result is a protocol test, never proof of live eligibility.
    local = TestClient(
        create_app(get_settings().model_copy(update={"environment": "local"}), Documents()),
        headers=dict(client.headers),
    )
    body = {
        "expected_version": workspace(client, invoice)["invoice"]["version"],
        "synthetic_authorized": True,
    }
    accepted = send(
        local, f"/v1/invoices/{invoice}/structured-extractions", body, "structured-admission-001"
    )
    assert (
        send(
            local,
            f"/v1/invoices/{invoice}/structured-extractions",
            body,
            "structured-admission-001",
        )
        == accepted
    )

    class CorrectWhileGenerating:
        def extract(self, pages: dict[int, str]) -> ExtractionResult:
            correct(client, invoice)
            return MockProvider().extract(pages)

    process_job(
        engine,
        get_settings(),
        "demo",
        accepted["job_id"],
        invoice_provider=CorrectWhileGenerating(),
    )
    state = workspace(client, invoice)
    assert len(state["candidates"]) == 1 and state["candidates"][0]["origin"] == "human_correction"
    with Session(engine) as session:
        job = session.get(Job, accepted["job_id"])
        assert job and job.error_code == "AI_SOURCE_SUPERSEDED" and job.state == "failed"
        assert session.scalar(select(func.count()).select_from(StructuredRequest)) == 1


def test_initial_extraction_failure_replay_keeps_request_version_consistent(engine: Engine) -> None:
    client = client_for(engine)
    invoice = setup_invoice(engine, client)
    job = ai_request(engine, invoice)

    class Offline:
        def extract(self, pages: dict[int, str]) -> ExtractionResult:
            raise ProviderFailure("AI_PROVIDER_UNAVAILABLE", True)

    for _ in range(5):
        due(engine, job)
        process_job(engine, get_settings(), "demo", job, invoice_provider=Offline())
    assert workspace(client, invoice)["result"]["outcome"] == "extraction_failed"
    with Session(engine) as session:
        original = session.get(Job, job)
        assert original is not None
        expected = original.version
    replay = send(
        client_for(engine, "admin"),
        f"/v1/jobs/{job}/replay",
        {"expected_version": expected, "reason": "Recovered synthetic provider for replay"},
    )
    process_job(engine, get_settings(), "demo", replay["job_id"], invoice_provider=MockProvider())
    with Session(engine) as session:
        row = session.get(Job, replay["job_id"])
        assert row is not None and row.state == "succeeded"
    assert len(workspace(client, invoice)["candidates"]) == 1


def test_invalid_provider_configuration_is_unavailable_not_internal_error(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RECONCILE_AI_MODEL", "unreviewed-model")
    response = client_for(engine).get("/v1/ai/status")
    assert response.status_code == 200 and response.json()["code"] == "AI_CONFIGURATION_INVALID"


def test_evidence_and_document_access_are_scoped_to_real_other_tenant(engine: Engine) -> None:
    from reconcileiq_database.invoice_workflow import append_candidate
    from reconcileiq_database.models import Organization

    foreign_id = uuid4().hex
    with Session(engine) as session, session.begin():
        session.add(Organization(id="foreign-workflow", name="Synthetic isolated tenant"))
        session.flush()
        foreign = Invoice(
            id=foreign_id,
            organization_id="foreign-workflow",
            description="Synthetic other tenant",
            status="extracted",
            synthetic=True,
            financial_state="legacy",
        )
        session.add(foreign)
        session.flush()
        append_candidate(
            session,
            foreign,
            candidate(),
            "human_correction",
            {"reason": "Synthetic tenant test"},
            "foreign-reviewer",
            version_id=uuid4().hex,
        )
    client = client_for(engine)
    for path in ("workspace", "document"):
        assert client.get(f"/v1/invoices/{foreign_id}/{path}").status_code == 404
    response = client.post(
        f"/v1/invoices/{foreign_id}/corrections",
        json={
            "expected_version": 1,
            "source_version_id": None,
            "reason": "Tenant data must remain isolated",
            "candidate": candidate().model_dump(),
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 404


def test_authenticated_page_preview_contract_and_admission_limit(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reconcileiq_api.errors import ApiError
    from reconcileiq_api.pdf_preview import CAPACITY, render_page

    client = client_for(engine)
    data = b"%PDF-1.4 synthetic preview protocol fixture"
    grant = send(
        client,
        "/v1/invoices/upload-requests",
        {
            "filename": "preview.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    invoice = grant["invoice_id"]
    assert (
        client.put(
            f"/v1/invoices/{invoice}/document",
            content=data,
            headers={"Content-Type": "application/pdf"},
        ).status_code
        == 204
    )
    send(client, f"/v1/invoices/{invoice}/upload-complete", {"expected_version": 1})
    monkeypatch.setattr(
        "reconcileiq_api.pdf_preview.render_page", lambda data, page: (b"\x89PNG\r\n\x1a\n", 3)
    )
    response = client.get(f"/v1/invoices/{invoice}/pages/1")
    assert response.status_code == 200 and response.headers["X-Document-Pages"] == "3"
    assert response.headers["content-type"] == "image/png"
    assert client.get(f"/v1/invoices/{invoice}/pages/0").status_code == 422
    assert client.get("/v1/invoices/foreign/pages/1").status_code == 404
    assert CAPACITY.acquire(blocking=False) and CAPACITY.acquire(blocking=False)
    try:
        with pytest.raises(ApiError) as caught:
            render_page(data, 1)
        assert caught.value.status == 429
    finally:
        CAPACITY.release()
        CAPACITY.release()
