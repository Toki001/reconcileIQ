"""Structured extraction and matching on the existing fenced Celery job lifecycle."""

import time
from dataclasses import asdict
from uuid import uuid4

from billiard.exceptions import SoftTimeLimitExceeded
from reconcileiq_ai.invoice import InvoiceCandidate, validate_sources
from reconcileiq_ai.provider import (
    AISettings,
    GeminiProvider,
    InvoiceProvider,
    ProviderFailure,
    verify_free_project,
)
from reconcileiq_ai.synthetic import synthetic_digest_allowed
from reconcileiq_database.config import Settings
from reconcileiq_database.financial import audit_record, identity
from reconcileiq_database.invoice_workflow import (
    append_candidate,
    latest_candidate,
    persist_reconciliation,
)
from reconcileiq_database.models import (
    ExtractionVersion,
    Invoice,
    Job,
    JobExecution,
    ProviderUsage,
    ReconciliationRun,
    StructuredRequest,
)
from reconcileiq_database.provider_budget import release, reserve
from reconcileiq_telemetry import metric, stage
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from reconcileiq_worker.processing import aware, event, lock_job, now, succeed
from reconcileiq_worker.providers import DocumentText, ProcessingError


def process_special(
    engine: Engine,
    settings: Settings,
    org: str,
    job_id: str,
    token: str,
    provider: InvoiceProvider | None = None,
) -> bool:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        assert job is not None
        kind = job.kind
        request = session.get(StructuredRequest, job_id) if kind == "extraction" else None
        if kind == "reconciliation":
            run = session.scalar(
                select(ReconciliationRun).where(
                    ReconciliationRun.organization_id == org, ReconciliationRun.job_id == job_id
                )
            )
            # Legacy admissions retain their explicit unsupported outcome.
            if run is None or run.extraction_version_id is None:
                return False
            reconcile_job(engine, org, job_id, token)
            return True
        if request is None:
            return False
        source = session.get(ExtractionVersion, request.text_version_id)
        invoice = session.get(Invoice, request.invoice_id)
        if (
            source is None
            or invoice is None
            or source.organization_id != org
            or source.invoice_id != invoice.id
            or source.schema_version != "digital-text-v1"
            or source.document_sha256 != invoice.document_sha256
        ):
            raise ProcessingError("INVALID_EXTRACTION_SOURCE")
        text = DocumentText.model_validate(source.payload)
        pages = {page.page: page.text for page in text.pages}
        request_id, invoice_id, invoice_version = (
            request.request_id,
            invoice.id,
            request.invoice_version,
        )
        requested_model, requested_prompt = request.model, request.prompt_version
        synthetic = request.synthetic_authorized and synthetic_digest_allowed(
            invoice.document_sha256 or ""
        )
    effect = f"structured:{invoice_id}:{request_id}"
    with Session(engine) as session, session.begin():
        job = lock_job(session, org, job_id)
        if job is None or job.lease_owner != token:
            return True
        if session.get(JobExecution, (org, effect)):
            succeed(session, job)
            return True
        job.stage = "extracting_fields"
        event(session, job, "structured_extraction_started")
    lease: str | None = None
    try:
        ai = AISettings()
    except ValueError:
        raise ProcessingError("AI_CONFIGURATION_INVALID") from None
    proof_data: dict[str, object] = {"mode": "injected-offline-test"}
    provider_mode = "synthetic" if provider is not None else "live_free"
    result = None
    started_provider = time.perf_counter()
    failure: ProviderFailure | None = None
    invoked = False
    try:
        if provider is None:
            if not synthetic or settings.environment != "local":
                raise ProviderFailure("AI_SYNTHETIC_INPUT_REQUIRED")
            if ai.model != requested_model:
                raise ProviderFailure("AI_MODEL_CONFIGURATION_CHANGED")
            proof = verify_free_project(ai)
            proof_data = {
                **asdict(proof),
                "checked_at": proof.checked_at.isoformat(),
                "billing_enabled": False,
            }
            lease = reserve(engine, ai, proof)
            provider = GeminiProvider(ai, proof, synthetic_authorized=synthetic)
        invoked = True
        with stage("provider.request", {"provider_mode": provider_mode}):
            result = provider.extract(pages)
        if result.prompt_version != requested_prompt or result.model != requested_model:
            raise ProviderFailure("AI_RESPONSE_PROVENANCE_MISMATCH")
        # Future adapters and test providers must cross the same strict evidence boundary.
        candidate = InvoiceCandidate.model_validate(result.candidate.model_dump())
        validate_sources(candidate, pages, provider=True)
    except ProviderFailure as error:
        failure = error
    except ValueError:
        failure = ProviderFailure("AI_INVALID_RESPONSE")
    except SoftTimeLimitExceeded:
        failure = ProviderFailure("PROCESSING_TIMEOUT", True)
    except Exception:
        failure = ProviderFailure("AI_PROVIDER_UNAVAILABLE", True)
    finally:
        if invoked:
            metric(
                "riq.provider.requests",
                labels={"provider_mode": provider_mode, "outcome": "error" if failure else "ok"},
            )
            metric(
                "riq.provider.duration",
                time.perf_counter() - started_provider,
                {"provider_mode": provider_mode},
                kind="histogram",
                unit="s",
            )
            if result:
                metric(
                    "riq.provider.input.tokens",
                    result.input_tokens,
                    {"provider_mode": provider_mode},
                )
                metric(
                    "riq.provider.output.tokens",
                    result.output_tokens,
                    {"provider_mode": provider_mode},
                )
            if failure and failure.code in {"AI_QUOTA_EXHAUSTED", "AI_RATE_LIMITED"}:
                metric("riq.provider.quota", labels={"provider_mode": provider_mode})
            if failure and "TIMEOUT" in failure.code:
                metric("riq.provider.timeouts", labels={"provider_mode": provider_mode})
        if invoked and provider_mode == "synthetic":
            metric("riq.provider.estimated_cost", 0, {"provider_mode": "synthetic"}, unit="USD")
        if lease:
            release(engine, ai.project_id, lease)
        if lease or provider is not None:
            # This records an actual provider invocation, including failures with unknown usage.
            # Offline injection is explicitly distinguished from verified free-tier activity.
            usage_id = uuid4().hex
            with Session(engine) as session, session.begin():
                session.add(
                    ProviderUsage(
                        id=usage_id,
                        organization_id=org,
                        job_id=job_id,
                        operation="generation",
                        model=requested_model,
                        prompt_version=requested_prompt,
                        proof=proof_data,
                        input_tokens=result.input_tokens if result else None,
                        output_tokens=result.output_tokens if result else None,
                        error_code=failure.code if failure else None,
                        free_tier=lease is not None,
                    )
                )
                audit_record(
                    session,
                    org,
                    "invoice-worker",
                    usage_id,
                    "provider_usage_recorded",
                    {
                        "job_id": job_id,
                        "model": requested_model,
                        "error_code": failure.code if failure else None,
                        "verified_free_tier": lease is not None,
                    },
                )
    if failure:
        raise ProcessingError(failure.code, failure.retryable, failure.retry_after)
    assert result is not None
    with Session(engine) as session, session.begin():
        job = lock_job(session, org, job_id)
        if (
            job is None
            or job.lease_owner != token
            or not job.lease_expires_at
            or aware(job.lease_expires_at) <= now()
        ):
            return True
        invoice = session.scalar(
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.organization_id == org)
            .with_for_update()
        )
        if invoice is None or invoice.version != invoice_version:
            raise ProcessingError("AI_SOURCE_SUPERSEDED")
        if session.get(JobExecution, (org, effect)) is None:
            version_id = identity(org + ":" + effect)
            append_candidate(
                session,
                invoice,
                result.candidate,
                "ai_gemini",
                {
                    "model": result.model,
                    "prompt_version": result.prompt_version,
                    "text_version_id": source.id,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
                "invoice-worker",
                version_id=version_id,
            )
            session.add(
                JobExecution(
                    organization_id=org,
                    key=effect,
                    job_id=job_id,
                    result=version_id,
                    completed_at=now(),
                )
            )
            invoice.status, invoice.version = "extracted", invoice.version + 1
            invoice.exception_code = invoice.exception_detail = None
        succeed(session, job)
    return True


def reconcile_job(engine: Engine, org: str, job_id: str, token: str) -> None:
    with Session(engine) as session, session.begin():
        job = lock_job(session, org, job_id)
        if (
            job is None
            or job.lease_owner != token
            or not job.lease_expires_at
            or aware(job.lease_expires_at) <= now()
        ):
            return
        run = session.scalar(
            select(ReconciliationRun)
            .where(ReconciliationRun.organization_id == org, ReconciliationRun.job_id == job_id)
            .with_for_update()
        )
        assert run is not None
        invoice = session.scalar(
            select(Invoice)
            .where(Invoice.id == run.invoice_id, Invoice.organization_id == org)
            .with_for_update()
        )
        assert invoice is not None
        source = latest_candidate(session, invoice)
        if source is None or source.id != run.extraction_version_id:
            raise ProcessingError("EXTRACTION_SUPERSEDED")
        effect = "reconcile:" + run.id
        if session.get(JobExecution, (org, effect)) is None:
            job.stage = "reconciling"
            persist_reconciliation(session, invoice, run, source, "invoice-worker")
            session.add(
                JobExecution(
                    organization_id=org,
                    key=effect,
                    job_id=job_id,
                    result=run.id,
                    completed_at=now(),
                )
            )
        succeed(session, job)
