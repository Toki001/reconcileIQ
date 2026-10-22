"""Faults assert persisted outcomes, including a real PostgreSQL connection loss."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from fastapi.testclient import TestClient
from reconcileiq_api.main import create_app
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import (
    AuditEvent,
    Credential,
    ExtractedField,
    ExtractionVersion,
    ExtractionVersionSeal,
    FailedJob,
    IdempotencyRecord,
    Invoice,
    Job,
    JobAttempt,
    JobExecution,
    OutboxEvent,
    ReconciliationRun,
)
from reconcileiq_worker.dispatch import dispatch_once, recover
from reconcileiq_worker.processing import now, process_job
from reconcileiq_worker.providers import DigitalPdfProvider, DocumentText, ProcessingError
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from test_api_contracts import headers, upload_body
from test_processing import PDF, MemoryReader, admit, due


def test_response_timeout_after_commit_replays_original_admission(finance_engine: Engine) -> None:
    import hashlib

    token = "response-timeout-reviewer-" + "x" * 40
    with Session(finance_engine) as session, session.begin():
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id="demo",
                actor="timeout-reviewer",
                role="reviewer",
            )
        )
    storage = MagicMock()
    storage.grant.return_value = "https://synthetic.invalid/upload"
    storage.verify_and_seal.return_value = "demo/sealed/" + uuid4().hex + ".pdf"

    class LoseOneResponse:
        def __init__(self, app: ASGIApp) -> None:
            self.app, self.armed = app, True

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            async def deliver(message: Message) -> None:
                if (
                    self.armed
                    and message["type"] == "http.response.start"
                    and message["status"] == 202
                ):
                    self.armed = False
                    raise TimeoutError("Synthetic transport timeout after durable admission")
                await send(message)

            await self.app(scope, receive, deliver)

    with TestClient(LoseOneResponse(create_app(get_settings(), storage))) as client:
        auth = {"Authorization": "Bearer " + token, "Idempotency-Key": "reserve-timeout"}
        grant = client.post("/v1/invoices/upload-requests", headers=auth, json=upload_body())
        assert grant.status_code == 201
        path = f"/v1/invoices/{grant.json()['invoice_id']}/upload-complete"
        auth["Idempotency-Key"] = "complete-response-lost"
        with pytest.raises(TimeoutError):
            client.post(path, headers=auth, json={"expected_version": 1})
        with Session(finance_engine) as session:
            job = session.scalar(select(Job))
            assert job is not None and job.state == "pending_dispatch"
            job_id = job.id
        response = client.post(path, headers=auth, json={"expected_version": 1})
        assert response.status_code == 202 and response.json()["job_id"] == job_id
        assert response.headers["idempotency-replayed"] == "true"
        storage.verify_and_seal.assert_called_once()
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "invoice_upload_completed")
            )
            == 1
        )


def test_related_jobs_share_one_immutable_effect(finance_engine: Engine) -> None:
    invoice_id, first = admit(finance_engine)
    second = uuid4().hex
    with Session(finance_engine) as session, session.begin():
        session.add(
            Job(id=second, organization_id="demo", invoice_id=invoice_id, kind="extraction")
        )
        session.flush()
        session.add(OutboxEvent(id=uuid4().hex, organization_id="demo", job_id=second, payload={}))
    gate = Barrier(2)

    class RelatedProvider:
        def extract(self, data: bytes) -> DocumentText:
            gate.wait(timeout=10)
            return DigitalPdfProvider().extract(data)

    def run(job_id: str) -> None:
        process_job(
            finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF), RelatedProvider()
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, (first, second)))
    with Session(finance_engine) as session:
        jobs = session.scalars(select(Job).where(Job.id.in_([first, second]))).all()
        assert [(job.state, job.attempts) for job in jobs] == [("succeeded", 1)] * 2
        for model in (ExtractionVersion, ExtractionVersionSeal, JobExecution):
            assert session.scalar(select(func.count()).select_from(model)) == 1
        assert session.scalar(select(func.count()).select_from(JobAttempt)) == 2
        assert session.get(Invoice, invoice_id).version == 2  # type: ignore[union-attr]
    # Database triggers must protect the actual worker-created untrusted evidence too.
    for statement in (
        "DELETE FROM extracted_fields",
        "DELETE FROM extraction_version_seals",
        "UPDATE extraction_versions SET origin='changed'",
    ):
        from sqlalchemy.exc import DBAPIError

        with finance_engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(text(statement))


def test_commit_loss_after_extraction_rolls_back_all_evidence(finance_engine: Engine) -> None:
    invoice_id, job_id = admit(finance_engine)
    injected = False

    def lose_commit(session: Session) -> None:
        nonlocal injected
        if injected or not any(
            isinstance(row, Job) and row.state == "succeeded"
            for row in session.identity_map.values()
        ):
            return
        session.flush()  # Evidence, seal, effect and success are now inside the open transaction.
        injected = True
        if finance_engine.dialect.name == "postgresql":
            pid = session.connection().scalar(text("SELECT pg_backend_pid()"))
            with finance_engine.connect() as control:
                assert control.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        else:
            raise OperationalError("COMMIT", {}, RuntimeError("synthetic-private-commit-failure"))

    event.listen(Session, "before_commit", lose_commit)
    try:
        process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
    finally:
        event.remove(Session, "before_commit", lose_commit)
    assert injected
    with Session(finance_engine) as session:
        for model in (ExtractionVersion, ExtractionVersionSeal, ExtractedField, JobExecution):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        job = session.get(Job, job_id)
        invoice = session.get(Invoice, invoice_id)
        assert job is not None and job.error_code == "PROCESSING_INTERRUPTED"
        assert job.state == "pending_dispatch" and job.attempts == 1
        assert (
            invoice is not None and invoice.status == "pending_extraction" and invoice.version == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "untrusted_text_evidence_recorded")
            )
            == 0
        )
    due(finance_engine, job_id)
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 1
        assert session.get(Job, job_id).attempts == 2  # type: ignore[union-attr]


def test_visibility_missing_outbox_and_dispatch_lease_recovery(finance_engine: Engine) -> None:
    _, job_id = admit(finance_engine)
    with Session(finance_engine) as session, session.begin():
        job = session.get(Job, job_id)
        envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job_id))
        assert job is not None and envelope is not None
        session.delete(envelope)
    recover(finance_engine, get_settings())
    with Session(finance_engine) as session, session.begin():
        envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job_id))
        assert envelope is not None
        envelope.lease_owner = "abandoned-publisher"
        envelope.lease_expires_at = now() + timedelta(seconds=30)
    sent: list[str] = []

    def sender(org: str, key: str, failed: bool, diagnostic: dict[str, Any]) -> None:
        sent.append(key)

    assert dispatch_once(finance_engine, get_settings(), sender) == 0
    with Session(finance_engine) as session, session.begin():
        envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job_id))
        assert envelope is not None
        envelope.lease_expires_at = now() - timedelta(seconds=1)
    assert dispatch_once(finance_engine, get_settings(), sender) == 1
    with Session(finance_engine) as session, session.begin():
        job = session.get(Job, job_id)
        assert job is not None and job.state == "queued" and job.attempts == 0
        job.updated_at = now() - timedelta(seconds=181)
    assert dispatch_once(finance_engine, get_settings(), sender) == 1
    assert sent == [job_id, job_id]
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        assert session.get(Job, job_id).attempts == 1  # type: ignore[union-attr]


def test_soft_timeout_retries_without_financial_effect(finance_engine: Engine) -> None:
    class TimedOut:
        def extract(self, data: bytes) -> DocumentText:
            raise SoftTimeLimitExceeded()

    _, job_id = admit(finance_engine)
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF), TimedOut())
    with Session(finance_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None and job.error_code == "PROCESSING_TIMEOUT" and job.retryable
        assert job.state == "pending_dispatch" and job.attempts == 1
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 0
    due(finance_engine, job_id)
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))


def test_unsupported_processors_and_incomplete_metadata_fail_explicitly(
    finance_engine: Engine,
) -> None:
    invoice_id, job_id = admit(finance_engine)
    with Session(finance_engine) as session, session.begin():
        job = session.get(Job, job_id)
        assert job is not None
        job.kind = "reconciliation"
        session.add(
            ReconciliationRun(
                id=uuid4().hex,
                organization_id="demo",
                invoice_id=invoice_id,
                job_id=job_id,
                policy_id="two_way_exact_v1",
            )
        )
    process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
    with Session(finance_engine) as session:
        assert session.get(Job, job_id).error_code == "PROCESSOR_NOT_IMPLEMENTED"  # type: ignore[union-attr]
        assert session.scalar(select(ReconciliationRun.status)) == "failed"
    for missing in (True, False):
        invoice_id, job_id = admit(finance_engine)
        with Session(finance_engine) as session, session.begin():
            invoice = session.get(Invoice, invoice_id)
            assert invoice is not None
            invoice.document_sha256 = None if missing else "not-a-sha256"
        process_job(finance_engine, get_settings(), "demo", job_id, MemoryReader(PDF))
        with Session(finance_engine) as session:
            job = session.get(Job, job_id)
            assert (
                job is not None
                and job.error_code == "INVALID_UPLOAD_METADATA"
                and not job.retryable
            )
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 0


def test_concurrent_completion_response_loss_and_replay_limits(finance_engine: Engine) -> None:
    import hashlib

    class Store:
        seals = 0

        def grant(self, key: str) -> str:
            return "https://synthetic.invalid/upload"

        def verify_and_seal(self, key: str, organization_id: str, size: int, digest: str) -> str:
            self.seals += 1
            return "demo/sealed/" + uuid4().hex + ".pdf"

    with Session(finance_engine) as session, session.begin():
        for role in ("admin", "reviewer", "auditor"):
            token = f"test-{role}-" + "x" * 40
            if session.get(Credential, hashlib.sha256(token.encode()).hexdigest()) is None:
                session.add(
                    Credential(
                        digest=hashlib.sha256(token.encode()).hexdigest(),
                        organization_id="demo",
                        actor=f"test-{role}",
                        role=role,
                    )
                )
    store = Store()
    with TestClient(create_app(get_settings(), store)) as client:
        body = upload_body()
        body.update(size_bytes=len(PDF), sha256=hashlib.sha256(PDF).hexdigest())
        grant = client.post("/v1/invoices/upload-requests", headers=headers(), json=body)
        invoice_id = grant.json()["invoice_id"]
        assert (
            client.post("/v1/invoices/upload-requests", headers=headers(), json=body).json()
            == grant.json()
        )
        path = f"/v1/invoices/{invoice_id}/upload-complete"

        def send(index: int) -> Any:
            # Different keys exercise the invoice lock rather than just the idempotency PK.
            return client.post(
                path, headers=headers(key=f"completion-{index}"), json={"expected_version": 1}
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            replies = list(executor.map(send, range(2)))
        assert sorted(reply.status_code for reply in replies) == [202, 409]
        success = next(index for index, reply in enumerate(replies) if reply.status_code == 202)
        # Simulate the client discarding a completed response (transport timeout after write).
        retried = send(success)
        assert (
            retried.json() == replies[success].json()
            and retried.headers["idempotency-replayed"] == "true"
        )
        assert store.seals == 1
        job_id = retried.json()["job_id"]

        class Missing(MemoryReader):
            def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
                raise ProcessingError("OBJECT_MISSING", True)

        for _ in range(5):
            due(finance_engine, job_id)
            process_job(finance_engine, get_settings(), "demo", job_id, Missing(PDF))
        with Session(finance_engine) as session:
            job = session.get(Job, job_id)
            assert job is not None and job.state == "failed" and job.attempts == 5
            version = job.version
        replay_path = f"/v1/jobs/{job_id}/replay"
        replay_body = {
            "expected_version": version,
            "reason": "Synthetic repeated replay verification",
        }
        for role in ("auditor", "reviewer"):
            assert (
                client.post(
                    replay_path, headers=headers(role, "denied-replay"), json=replay_body
                ).status_code
                == 403
            )
        first = client.post(replay_path, headers=headers("admin", "replay-once"), json=replay_body)
        assert first.status_code == 202
        for _ in range(3):
            assert (
                client.post(
                    replay_path, headers=headers("admin", "replay-once"), json=replay_body
                ).json()
                == first.json()
            )
        current = client.get(f"/v1/jobs/{job_id}", headers=headers()).json()["version"]
        assert (
            client.post(
                replay_path,
                headers=headers("admin", "different-replay"),
                json={**replay_body, "expected_version": current},
            ).status_code
            == 409
        )
    with Session(finance_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1
        assert session.get(IdempotencyRecord, ("demo", "different-replay")) is None
        assert session.scalar(select(func.count()).select_from(FailedJob)) == 1
