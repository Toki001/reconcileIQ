import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from reconcileiq_api.main import create_app
from reconcileiq_database.config import Settings, get_settings
from reconcileiq_database.models import (
    Credential,
    ExtractionVersion,
    FailedJob,
    Invoice,
    Job,
    JobAttempt,
    JobExecution,
    OutboxEvent,
)
from reconcileiq_worker.dispatch import dispatch_once, recover
from reconcileiq_worker.logging import SafeDiagnostics
from reconcileiq_worker.processing import backoff, complete, now, process_job
from reconcileiq_worker.providers import DigitalPdfProvider, DocumentText, ProcessingError
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session


class MemoryReader:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
        return self.data


PDF = Path("tests/fixtures/financial/invoice-0001.pdf").read_bytes()


def admit(engine: Engine, data: bytes = PDF) -> tuple[str, str]:
    invoice_id, job_id = uuid4().hex, uuid4().hex
    with Session(engine) as session, session.begin():
        session.add(
            Invoice(
                id=invoice_id,
                organization_id="demo",
                description="Synthetic processing test",
                synthetic=True,
                status="pending_extraction",
                financial_state="legacy",
                document_key=f"demo/sealed/{uuid4().hex}.pdf",
                document_size=len(data),
                document_sha256=hashlib.sha256(data).hexdigest(),
            )
        )
        session.flush()
        session.add(
            Job(id=job_id, organization_id="demo", invoice_id=invoice_id, kind="extraction")
        )
        session.flush()
        # The worker must ignore arbitrary paths/URLs injected into outbox payloads.
        session.add(
            OutboxEvent(
                id=uuid4().hex,
                organization_id="demo",
                job_id=job_id,
                payload={"document_key": "https://untrusted.invalid/file"},
            )
        )
    return invoice_id, job_id


def due(engine: Engine, job_id: str) -> None:
    with Session(engine) as session, session.begin():
        job = session.get(Job, job_id)
        assert job is not None
        job.next_attempt_at = now() - timedelta(seconds=1)
        envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job_id))
        assert envelope is not None
        envelope.available_at = now() - timedelta(seconds=1)


def test_all_200_synthetic_pdfs_process_without_creating_financial_facts(
    finance_engine: Engine,
) -> None:
    settings = get_settings()
    ids = []
    for path in sorted(Path("tests/fixtures/financial").glob("*.pdf")):
        data = path.read_bytes()
        invoice_id, job_id = admit(finance_engine, data)
        process_job(finance_engine, settings, "demo", job_id, MemoryReader(data))
        process_job(finance_engine, settings, "demo", job_id, MemoryReader(data))
        ids.append(invoice_id)
    with Session(finance_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(Job).where(Job.state == "succeeded"))
            == 200
        )
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 200
        assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 200
        assert session.scalar(select(func.count()).select_from(JobAttempt)) == 200
        for invoice in session.scalars(select(Invoice).where(Invoice.id.in_(ids))):
            assert invoice.status == "extracted"
            assert invoice.amount_minor is None and invoice.currency is None
            assert invoice.validated_extraction_id is None
        assert all(
            version.validation_state == "untrusted"
            for version in session.scalars(select(ExtractionVersion))
        )


@pytest.mark.parametrize("data", [b"%PDF-1.4 broken document", b"not a PDF"])
def test_corrupt_pdf_is_permanent_and_durably_dead_lettered(
    finance_engine: Engine, data: bytes
) -> None:
    _, job_id = admit(finance_engine, data)
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(data))
    sent: list[tuple[bool, dict[str, Any]]] = []
    dispatch_once(
        finance_engine,
        get_settings(),
        lambda org, key, terminal, diagnostic: sent.append((terminal, diagnostic)),
    )
    with Session(finance_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.state == "failed" and not job.retryable
        assert job.attempts == 1 and job.error_code == "INVALID_PDF"
        assert session.scalar(select(func.count()).select_from(FailedJob)) == 1
        assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 0
    assert len(sent) == 1 and sent[0][0]
    assert set(sent[0][1]) == {"error_code", "trace_id", "attempts", "retryable"}


@pytest.mark.parametrize("error", ["OBJECT_MISSING", "STORAGE_UNAVAILABLE"])
def test_retry_is_bounded_at_five_with_retained_history(finance_engine: Engine, error: str) -> None:
    class Unavailable(MemoryReader):
        def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
            raise ProcessingError(error, True, 17)

    _, job_id = admit(finance_engine)
    settings = get_settings()
    for attempt in range(1, 6):
        due(finance_engine, job_id)
        process_job(finance_engine, settings, "demo", job_id, Unavailable(PDF))
        with Session(finance_engine) as session:
            job = session.get(Job, job_id)
            assert job is not None and job.attempts == attempt
            assert job.state == ("failed" if attempt == 5 else "pending_dispatch")
    process_job(finance_engine, settings, "demo", job_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(JobAttempt)) == 5
        assert session.scalar(select(func.count()).select_from(FailedJob)) == 1
        assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 0


def test_digest_mismatch_and_cross_tenant_delivery_fail_closed(finance_engine: Engine) -> None:
    _, job_id = admit(finance_engine)
    process_job(finance_engine, get_settings(), "other", job_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.attempts == 0
    corrupt = PDF.replace(b"Invoice", b"Invoixe", 1)
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(corrupt))
    with Session(finance_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.error_code == "CHECKSUM_MISMATCH" and job.attempts == 1


def test_expired_crash_lease_recovers_and_fences_old_worker(finance_engine: Engine) -> None:
    class Crash:
        def extract(self, data: bytes) -> DocumentText:
            raise SystemExit("synthetic worker crash")

    _, job_id = admit(finance_engine)
    settings = get_settings()
    with pytest.raises(SystemExit):
        process_job(finance_engine, settings, "demo", job_id, MemoryReader(PDF), Crash())
    with Session(finance_engine) as session, session.begin():
        job = session.get(Job, job_id)
        assert job is not None and job.state == "running" and job.lease_owner
        stale = job.lease_owner
        job.lease_expires_at = now() - timedelta(seconds=1)
    recover(finance_engine, settings)
    complete(
        finance_engine,
        "demo",
        job_id,
        stale,
        hashlib.sha256(PDF).hexdigest(),
        DigitalPdfProvider().extract(PDF),
    )
    due(finance_engine, job_id)
    process_job(finance_engine, settings, "demo", job_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.state == "succeeded" and job.attempts == 2
        assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 1


def test_broker_failure_then_recovery_and_duplicate_confirmed_publish(
    finance_engine: Engine,
) -> None:
    _, job_id = admit(finance_engine)
    settings = get_settings()

    def unavailable(org: str, key: str, terminal: bool, diagnostic: dict[str, Any]) -> None:
        raise ConnectionError("synthetic connection failure with private content")

    assert dispatch_once(finance_engine, settings, unavailable) == 0
    due(finance_engine, job_id)

    def send(org: str, key: str, terminal: bool, diagnostic: dict[str, Any]) -> None:
        process_job(finance_engine, settings, org, key, MemoryReader(PDF))
        process_job(finance_engine, settings, org, key, MemoryReader(PDF))

    assert dispatch_once(finance_engine, settings, send) == 1
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 1
        assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 1


def test_two_jobs_process_concurrently_without_duplicate_effects(finance_engine: Engine) -> None:
    barrier = Barrier(2)

    class Synchronized:
        def extract(self, data: bytes) -> DocumentText:
            barrier.wait(timeout=10)
            return DigitalPdfProvider().extract(data)

    ids = [admit(finance_engine)[1] for _ in range(2)]

    def run(job_id: str) -> None:
        process_job(
            finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF), Synchronized()
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, ids))
    with Session(finance_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(Job).where(Job.state == "succeeded"))
            == 2
        )


def test_processing_list_auth_and_scope(finance_engine: Engine) -> None:
    _, job_id = admit(finance_engine)
    token = "m04-list-auditor-" + "x" * 40
    with Session(finance_engine) as session, session.begin():
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id="demo",
                actor="synthetic-auditor",
                role="auditor",
            )
        )
    with TestClient(create_app(get_settings())) as client:
        assert client.get("/v1/processing-jobs").status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        result = client.get("/v1/processing-jobs", headers=headers)
        assert result.status_code == 200
        assert result.json()["items"][0]["id"] == job_id
        assert result.json()["active_count"] == 1


def test_backoff_respects_retry_after_and_bound() -> None:
    settings = get_settings()
    for attempt in range(1, 6):
        assert 1 <= backoff(attempt, settings) <= min(300, 5 * 2 ** (attempt - 1))
        assert backoff(attempt, settings, 1000) >= 1000


def test_logging_removes_exception_chains_and_connection_strings() -> None:
    record = logging.LogRecord(
        "celery",
        logging.ERROR,
        "",
        0,
        "failed postgresql+psycopg://private:password@host/db",
        (),
        None,
    )
    assert SafeDiagnostics().filter(record)
    assert "password" not in record.getMessage() and record.exc_info is None


def test_evidence_transaction_rolls_back_on_partial_failure(finance_engine: Engine) -> None:
    _, job_id = admit(finance_engine)

    def interrupt(session: Session, context: object, instances: object) -> None:
        if any(isinstance(row, JobExecution) for row in session.new):
            raise RuntimeError("synthetic private provider diagnostics")

    event.listen(Session, "before_flush", interrupt)
    try:
        process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
    finally:
        event.remove(Session, "before_flush", interrupt)
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 0
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 0
        job = session.get(Job, job_id)
        assert job is not None and job.stage == "retrying"
        assert job.error_code == "PROCESSING_INTERRUPTED"
    due(finance_engine, job_id)
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 1


def test_authorized_replay_retains_failure_and_deduplicates_success(finance_engine: Engine) -> None:
    class Missing(MemoryReader):
        def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
            raise ProcessingError("OBJECT_MISSING", True)

    _, job_id = admit(finance_engine)
    for _ in range(5):
        due(finance_engine, job_id)
        process_job(finance_engine, get_settings(), "demo", job_id, Missing(PDF))
    with Session(finance_engine) as session, session.begin():
        job = session.get(Job, job_id)
        assert job is not None
        version = job.version
        for role in ("admin", "auditor"):
            token = f"processing-replay-{role}-" + "x" * 40
            session.add(
                Credential(
                    digest=hashlib.sha256(token.encode()).hexdigest(),
                    organization_id="demo",
                    actor=f"synthetic-{role}",
                    role=role,
                )
            )
    body = {"expected_version": version, "reason": "Synthetic missing object has been restored."}
    with TestClient(create_app(get_settings())) as client:

        def replay(role: str) -> Any:
            return client.post(
                f"/v1/jobs/{job_id}/replay",
                json=body,
                headers={
                    "Authorization": f"Bearer processing-replay-{role}-" + "x" * 40,
                    "Idempotency-Key": "processing-replay-idempotent",
                },
            )

        assert replay("auditor").status_code == 403
        response = replay("admin")
        assert response.status_code == 202
        assert replay("admin").json() == response.json()
        replay_id = response.json()["job_id"]
    for _ in range(2):
        process_job(finance_engine, get_settings(), "demo", replay_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(FailedJob)) == 1
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 1
        original = session.get(Job, job_id)
        assert original is not None and original.state == "failed"


def test_unsafe_persisted_object_reference_never_reaches_storage(finance_engine: Engine) -> None:
    class ForbiddenReader(MemoryReader):
        def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
            raise AssertionError("Storage must not be reached")

    invoice_id, job_id = admit(finance_engine)
    with Session(finance_engine) as session, session.begin():
        invoice = session.get(Invoice, invoice_id)
        assert invoice is not None
        invoice.document_key = "other/sealed/" + "a" * 32 + ".pdf"
    process_job(finance_engine, get_settings(), "demo", job_id, ForbiddenReader(PDF))
    with Session(finance_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.error_code == "UNSAFE_OBJECT_REFERENCE"


def test_confirm_lost_after_effect_commit_keeps_single_result(finance_engine: Engine) -> None:
    _, job_id = admit(finance_engine)

    def lost_confirmation(org: str, key: str, terminal: bool, diagnostic: dict[str, Any]) -> None:
        process_job(finance_engine, get_settings(), org, key, MemoryReader(PDF))
        raise ConnectionError("confirmation lost after committed effect")

    dispatch_once(finance_engine, get_settings(), lost_confirmation)
    dispatch_once(finance_engine, get_settings(), lost_confirmation)
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 1
        envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job_id))
        assert envelope is not None and envelope.published_at is not None


def test_running_duplicate_does_not_claim_a_second_attempt(finance_engine: Engine) -> None:
    entered, release = Event(), Event()

    class GatedProvider:
        def extract(self, data: bytes) -> DocumentText:
            entered.set()
            assert release.wait(timeout=10)
            return DigitalPdfProvider().extract(data)

    _, job_id = admit(finance_engine)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            process_job,
            finance_engine,
            get_settings(),
            "demo",
            job_id,
            MemoryReader(PDF),
            GatedProvider(),
        )
        try:
            assert entered.wait(timeout=5)
            process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
        finally:
            release.set()
        future.result(timeout=10)
    with Session(finance_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.state == "succeeded" and job.attempts == 1


def test_business_worker_refuses_sqlite_with_a_shared_rabbitmq() -> None:
    with pytest.raises(ValueError, match="business workers require PostgreSQL"):
        Settings(
            worker_enabled=True,
            database_url=SecretStr("sqlite:///.data/test-only.db"),
            broker_url=SecretStr("amqp://test:test@127.0.0.1:5672//"),
        )


def test_configured_capacity_recovers_after_processing(
    finance_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LocalStorage:
        def grant(self, key: str) -> str:
            return "https://synthetic.invalid/upload"

        def verify_and_seal(self, key: str, organization_id: str, size: int, digest: str) -> str:
            return organization_id + "/sealed/" + uuid4().hex + ".pdf"

    monkeypatch.setenv("RECONCILE_ADMITTED_JOB_LIMIT", "1")
    get_settings.cache_clear()
    _, busy_job = admit(finance_engine)
    token = "capacity-test-admin-" + "x" * 40
    with Session(finance_engine) as session, session.begin():
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id="demo",
                actor="synthetic-capacity-admin",
                role="admin",
            )
        )
    with TestClient(create_app(get_settings(), LocalStorage())) as client:
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "capacity-upload-request"}
        grant = client.post(
            "/v1/invoices/upload-requests",
            headers=headers,
            json={
                "filename": "synthetic.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(PDF),
                "sha256": hashlib.sha256(PDF).hexdigest(),
            },
        )
        assert grant.status_code == 201
        path = "/v1/invoices/" + grant.json()["invoice_id"] + "/upload-complete"
        headers["Idempotency-Key"] = "capacity-complete-request"
        response = client.post(path, headers=headers, json={"expected_version": 1})
        assert response.status_code == 429 and response.headers["retry-after"] == "30"
        process_job(finance_engine, get_settings(), "demo", busy_job, MemoryReader(PDF))
        assert client.post(path, headers=headers, json={"expected_version": 1}).status_code == 202
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "header,expected", [("17", 17), ("Thu, 22 Oct 2026 00:00:19 GMT", 19), ("invalid", 0)]
)
def test_provider_retry_after_supports_seconds_and_http_dates(header: str, expected: int) -> None:
    from datetime import UTC, datetime

    from reconcileiq_worker.providers import retry_after_seconds

    assert retry_after_seconds(header, datetime(2026, 10, 22, tzinfo=UTC)) == expected


def test_provider_retry_after_beyond_recovery_window_is_not_retried_early() -> None:
    from reconcileiq_worker.providers import retry_after_seconds

    with pytest.raises(ProcessingError, match="RETRY_AFTER_OUTSIDE_WINDOW"):
        retry_after_seconds("86401")


def test_worker_traffic_does_not_hide_visible_financial_audit_context(
    finance_engine: Engine,
) -> None:
    from reconcileiq_database.models import AuditEvent
    from reconcileiq_database.seed import seed_demo

    seed_demo(finance_engine)
    token = "audit-context-auditor-" + "x" * 40
    with Session(finance_engine) as session, session.begin():
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id="demo",
                actor="synthetic-auditor",
                role="auditor",
            )
        )
        for index in range(150):
            session.add(
                AuditEvent(
                    id=uuid4().hex,
                    organization_id="demo",
                    entity_id=f"synthetic-job-{index}",
                    actor="synthetic-worker",
                    action="job_started",
                    detail="Synthetic noise",
                    occurred_at=now(),
                )
            )
    with TestClient(create_app(get_settings())) as client:
        response = client.get("/v1/demo/snapshot", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        rows = response.json()["audit_events"]
        assert len(rows) == 100
        assert any(row["entity_id"] == "INV-1043" for row in rows)
        assert any(row["action"] == "job_started" for row in rows)
