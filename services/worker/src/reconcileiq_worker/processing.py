"""Fenced, bounded, transactional job execution; AMQP delivery is only a wake-up signal."""

import json
import random
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from billiard.exceptions import SoftTimeLimitExceeded
from reconcileiq_ai.provider import InvoiceProvider
from reconcileiq_database.config import Settings
from reconcileiq_database.financial import identity
from reconcileiq_database.models import (
    AuditEvent,
    ExtractedField,
    ExtractionVersion,
    ExtractionVersionSeal,
    FailedJob,
    Invoice,
    Job,
    JobAttempt,
    JobExecution,
    OutboxEvent,
    ReconciliationRun,
    StructuredRequest,
)
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from reconcileiq_worker.providers import (
    DigitalPdfProvider,
    DocumentText,
    MinioReader,
    ObjectReader,
    ProcessingError,
    TextProvider,
)


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def bounded_session(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        session.execute(text("SET LOCAL lock_timeout = '2000ms'"))
    else:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def event(session: Session, job: Job, action: str) -> None:
    session.add(
        AuditEvent(
            id=uuid4().hex,
            organization_id=job.organization_id,
            entity_id=job.id,
            actor="invoice-worker",
            action=action,
            detail=json.dumps(
                {
                    "state": job.state,
                    "stage": job.stage,
                    "attempts": job.attempts,
                    "error_code": job.error_code,
                    "trace_id": job.trace_id,
                }
            ),
            occurred_at=now(),
        )
    )


def backoff(attempt: int, settings: Settings, retry_after: int = 0) -> int:
    ceiling = min(
        settings.retry_max_seconds, settings.retry_base_seconds * 2 ** min(max(attempt - 1, 0), 10)
    )
    # Equal jitter avoids a zero-delay retry storm. Retry-After is a lower bound.
    return max(retry_after, random.SystemRandom().randint(max(1, ceiling // 2), ceiling))


def lock_job(session: Session, org: str, job_id: str) -> Job | None:
    bounded_session(session)
    return session.scalar(
        select(Job).where(Job.id == job_id, Job.organization_id == org).with_for_update()
    )


def fail(session: Session, job: Job, error: ProcessingError, settings: Settings) -> None:
    attempt = session.scalar(
        select(JobAttempt).where(
            JobAttempt.organization_id == job.organization_id,
            JobAttempt.job_id == job.id,
            JobAttempt.state == "running",
        )
    )
    if attempt is not None:
        attempt.state, attempt.finished_at = "failed", now()
        attempt.error_code, attempt.retryable = error.code, error.retryable
    job.error_code, job.retryable = error.code, error.retryable
    job.lease_owner = job.lease_expires_at = None
    job.updated_at, job.version = now(), job.version + 1
    envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id))
    if envelope is None:
        raise RuntimeError("Durable job envelope is missing")
    envelope.published_at = envelope.lease_owner = envelope.lease_expires_at = None
    if error.retryable and job.attempts < job.max_attempts:
        job.state, job.stage = "pending_dispatch", "retrying"
        job.next_attempt_at = now() + timedelta(
            seconds=backoff(job.attempts, settings, error.retry_after)
        )
        envelope.available_at = job.next_attempt_at
        event(session, job, "job_retry_scheduled")
        return
    job.state, job.stage, job.next_attempt_at = "failed", "failed", None
    envelope.available_at = now()  # Explicit terminal event is published to the durable DLQ.
    session.add(
        FailedJob(
            id=identity("failure:" + job.id),
            organization_id=job.organization_id,
            job_id=job.id,
            error_code=error.code,
            retryable=error.retryable,
            trace_id=job.trace_id,
            next_action="Admin may repair dependency and replay."
            if error.retryable
            else "Correct the source and submit a new upload; this failure cannot be replayed.",
        )
    )
    if job.invoice_id and job.kind == "extraction":
        invoice = session.scalar(
            select(Invoice)
            .where(
                Invoice.id == job.invoice_id,
                Invoice.organization_id == job.organization_id,
            )
            .with_for_update()
        )
        if invoice is not None:
            from reconcileiq_database.invoice_workflow import latest_candidate

            prior = (
                latest_candidate(session, invoice)
                if session.get(StructuredRequest, job.id)
                else None
            )
            if prior is None:
                invoice.status = "needs_review"
                invoice.exception_code = error.code
                invoice.exception_detail = "Extraction failed. Inspect the processing job."
                invoice.version += 1
                request = session.get(StructuredRequest, job.id)
                if request is not None:
                    from reconcileiq_database.invoice_workflow import context_digest
                    from reconcileiq_database.models import ReconciliationEvidence

                    result_id = identity("extraction-failed:" + job.id)
                    context = {"failed_job_id": job.id, "document_sha256": invoice.document_sha256}
                    session.add(
                        ReconciliationRun(
                            id=result_id,
                            organization_id=job.organization_id,
                            invoice_id=invoice.id,
                            job_id=job.id,
                            status="failed",
                            policy_id="extraction-v1",
                            extraction_version_id=request.text_version_id,
                            outcome="extraction_failed",
                        )
                    )
                    session.flush()
                    session.add(
                        ReconciliationEvidence(
                            reconciliation_id=result_id,
                            organization_id=job.organization_id,
                            invoice_id=invoice.id,
                            extraction_version_id=request.text_version_id,
                            invoice_version=invoice.version,
                            context_sha256=context_digest(context),
                            context=context,
                            result={
                                "outcome": "extraction_failed",
                                "differences": [
                                    {
                                        "code": error.code,
                                        "field": "extraction",
                                        "expected": "validated candidate evidence",
                                        "observed": "provider extraction failed",
                                    }
                                ],
                            },
                        )
                    )
    if job.kind == "reconciliation":
        for run in session.scalars(
            select(ReconciliationRun).where(ReconciliationRun.job_id == job.id)
        ):
            run.status = "failed"
    event(session, job, "job_failed")


def process_job(
    engine: Engine,
    settings: Settings,
    org: str,
    job_id: str,
    reader: ObjectReader | None = None,
    provider: TextProvider | None = None,
    invoice_provider: InvoiceProvider | None = None,
) -> None:
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value) for value in (org, job_id)):
        return
    token = uuid4().hex
    with Session(engine) as session, session.begin():
        job = lock_job(session, org, job_id)
        if job is None or job.state in {"succeeded", "failed", "cancelled"}:
            return
        if job.state == "running" or (job.next_attempt_at and aware(job.next_attempt_at) > now()):
            return
        if job.attempts >= job.max_attempts:
            fail(session, job, ProcessingError("ATTEMPTS_EXHAUSTED", True), settings)
            return
        job.attempts += 1
        job.state, job.stage = "running", "validating_upload"
        job.lease_owner, job.lease_expires_at = (
            token,
            now() + timedelta(seconds=settings.worker_lease_seconds),
        )
        job.trace_id = job.trace_id or uuid4().hex
        job.updated_at, job.version = now(), job.version + 1
        job.next_attempt_at = None
        session.add(
            JobAttempt(
                id=uuid4().hex,
                organization_id=org,
                job_id=job.id,
                number=job.attempts,
                state="running",
                worker=token,
                trace_id=job.trace_id,
            )
        )
        event(session, job, "job_started")
    try:
        from reconcileiq_worker.workflows import process_special

        if process_special(engine, settings, org, job_id, token, invoice_provider):
            return
        with Session(engine) as session:
            job = session.get(Job, job_id)
            assert job is not None
            if job.kind != "extraction":
                raise ProcessingError("PROCESSOR_NOT_IMPLEMENTED")
            invoice = session.scalar(
                select(Invoice).where(Invoice.id == job.invoice_id, Invoice.organization_id == org)
            )
            if (
                invoice is None
                or not invoice.document_key
                or not invoice.document_sha256
                or not invoice.document_size
            ):
                raise ProcessingError("INVALID_UPLOAD_METADATA")
            key, size, digest = invoice.document_key, invoice.document_size, invoice.document_sha256
            if not 5 <= size <= 10_485_760 or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ProcessingError("INVALID_UPLOAD_METADATA")
            if not re.fullmatch(re.escape(org) + r"/sealed/[a-f0-9]{32}\.pdf", key):
                raise ProcessingError("UNSAFE_OBJECT_REFERENCE")
        data = (reader or MinioReader(settings)).read(org, key, size, digest)
        from reconcileiq_worker.providers import verify_bytes

        verify_bytes(data, size, digest)
        with Session(engine) as session, session.begin():
            job = lock_job(session, org, job_id)
            if job is None or job.lease_owner != token:
                return
            job.stage = "extracting_text"
            event(session, job, "extraction_text_started")
        if provider is None and settings.environment == "local":
            from reconcileiq_worker.restricted_parser import RestrictedPdfProvider

            provider = RestrictedPdfProvider(ocr_enabled=settings.ocr_enabled)
        content = (provider or DigitalPdfProvider()).extract(data)
        complete(engine, org, job_id, token, digest, content)
    except SoftTimeLimitExceeded:
        finish_failure(
            engine, settings, org, job_id, token, ProcessingError("PROCESSING_TIMEOUT", True)
        )
    except ProcessingError as error:
        finish_failure(engine, settings, org, job_id, token, error)
    except Exception:
        # Never persist or log provider/SQL exception text (may contain credentials/documents).
        finish_failure(
            engine, settings, org, job_id, token, ProcessingError("PROCESSING_INTERRUPTED", True)
        )


def finish_failure(
    engine: Engine, settings: Settings, org: str, job_id: str, token: str, error: ProcessingError
) -> None:
    with Session(engine) as session, session.begin():
        job = lock_job(session, org, job_id)
        if job is not None and job.lease_owner == token:
            fail(session, job, error, settings)


def complete(
    engine: Engine, org: str, job_id: str, token: str, digest: str, content: DocumentText
) -> None:
    # Revalidate provider output even when supplied by a future remote adapter.
    content = DocumentText.model_validate(content.model_dump())
    with Session(engine) as session, session.begin():
        job = lock_job(session, org, job_id)
        if (
            job is None
            or job.lease_owner != token
            or not job.lease_expires_at
            or aware(job.lease_expires_at) <= now()
        ):
            return
        invoice = session.scalar(
            select(Invoice)
            .where(Invoice.id == job.invoice_id, Invoice.organization_id == org)
            .with_for_update()
        )
        if invoice is None or invoice.document_sha256 != digest:
            raise ProcessingError("SOURCE_CHANGED")
        effect = f"text:{invoice.id}:{digest}:v1"
        if session.get(JobExecution, (org, effect)) is None:
            number = (
                session.scalar(
                    select(func.max(ExtractionVersion.number)).where(
                        ExtractionVersion.organization_id == org,
                        ExtractionVersion.invoice_id == invoice.id,
                    )
                )
                or 0
            ) + 1
            version_id = identity(f"text:{org}:{invoice.id}:{digest}:v1")
            session.add(
                ExtractionVersion(
                    id=version_id,
                    organization_id=org,
                    invoice_id=invoice.id,
                    number=number,
                    document_sha256=digest,
                    origin="digital_pdf_text",
                    schema_version="digital-text-v1",
                    validation_state="untrusted",
                    payload=content.model_dump(),
                )
            )
            session.flush()
            for page in content.pages:
                session.add(
                    ExtractedField(
                        id=identity(f"field:{version_id}:page:{page.page}"),
                        organization_id=org,
                        extraction_version_id=version_id,
                        invoice_id=invoice.id,
                        path=f"pages[{page.page - 1}].text",
                        page=page.page,
                        value=page.text,
                        source=content.provider,
                    )
                )
            session.flush()
            session.add(
                ExtractionVersionSeal(
                    organization_id=org, extraction_version_id=version_id, invoice_id=invoice.id
                )
            )
            session.add(
                JobExecution(
                    organization_id=org,
                    key=effect,
                    job_id=job.id,
                    result=version_id,
                    completed_at=now(),
                )
            )
            invoice.status, invoice.version = "extracted", invoice.version + 1
            invoice.exception_code = invoice.exception_detail = None
            event(session, job, "untrusted_text_evidence_recorded")
        succeed(session, job)


def succeed(session: Session, job: Job) -> None:
    attempt = session.scalar(
        select(JobAttempt).where(
            JobAttempt.job_id == job.id,
            JobAttempt.organization_id == job.organization_id,
            JobAttempt.state == "running",
        )
    )
    assert attempt is not None
    attempt.state, attempt.finished_at = "succeeded", now()
    job.state, job.stage, job.error_code, job.retryable = "succeeded", "completed", None, False
    job.lease_owner = job.lease_expires_at = None
    job.updated_at, job.version = now(), job.version + 1
    envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id))
    if envelope is not None:
        envelope.published_at = envelope.published_at or now()
        envelope.lease_owner = envelope.lease_expires_at = None
    event(session, job, "job_completed")
