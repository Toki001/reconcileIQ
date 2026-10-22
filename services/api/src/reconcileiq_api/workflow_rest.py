"""Explicit AI admission, immutable human corrections and tenant-scoped review evidence."""

import hashlib
from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Response
from fastapi import Path as PathParameter
from fastapi.responses import JSONResponse
from reconcileiq_ai.invoice import InvoiceCandidate, evidence_fields
from reconcileiq_ai.provider import (
    FREE_MODEL,
    PROMPT_VERSION,
    AISettings,
    FreeProof,
    ProviderFailure,
    verify_free_project,
)
from reconcileiq_database.config import Settings
from reconcileiq_database.invoice_workflow import (
    SCHEMA,
    append_candidate,
    latest_candidate,
    matching_context,
)
from reconcileiq_database.models import (
    AuditEvent,
    ExtractionVersion,
    Invoice,
    Job,
    ProviderUsage,
    ReconciliationEvidence,
    ReconciliationRun,
    StructuredRequest,
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from reconcileiq_api.auth import Actor, require_writer
from reconcileiq_api.contracts import JobAccepted
from reconcileiq_api.errors import ERROR_RESPONSES, ApiError
from reconcileiq_api.rest import IdempotencyKey, accepted, owned, version
from reconcileiq_api.storage import DocumentStorage, Storage
from reconcileiq_api.transactions import Mutation, audit, mutate, new_job
from reconcileiq_api.workflow_contracts import (
    CandidateRead,
    CorrectionCreate,
    IdentityRead,
    ProviderStatus,
    ResultRead,
    StructuredStart,
    TimelineRead,
    WorkspaceRead,
)


def status_for(session: Session, org: str) -> ProviderStatus:
    try:
        ai = AISettings()
    except ValueError:
        return ProviderStatus(available=False, model=FREE_MODEL, code="AI_CONFIGURATION_INVALID")
    if not ai.enabled or not ai.project_id or not ai.api_key.get_secret_value():
        return ProviderStatus(available=False, model=FREE_MODEL, code="AI_NOT_CONFIGURED")
    usage = session.scalar(
        select(ProviderUsage)
        .where(
            ProviderUsage.organization_id == org,
            ProviderUsage.operation == "verification",
        )
        .order_by(ProviderUsage.created_at.desc(), ProviderUsage.id.desc())
        .limit(1)
    )
    if usage and not usage.error_code:
        try:
            data = {**usage.proof}
            data["checked_at"] = datetime.fromisoformat(data["checked_at"])
            proof = FreeProof(**data)
            if proof.current(ai):
                return ProviderStatus(
                    available=True,
                    model=ai.model,
                    code="VERIFIED_FREE_TIER",
                    checked_at=proof.checked_at,
                )
        except (ValueError, TypeError, KeyError):
            pass
    return ProviderStatus(
        available=False,
        model=ai.model,
        code=usage.error_code if usage and usage.error_code else "FREE_TIER_UNVERIFIED",
    )


def build_workflow_router(engine: Engine, settings: Settings, storage: Storage) -> APIRouter:
    router = APIRouter(prefix="/v1", responses=ERROR_RESPONSES)

    @router.get("/me", response_model=IdentityRead, operation_id="getIdentity", tags=["identity"])
    def identity(actor: Actor) -> IdentityRead:
        return IdentityRead(
            organization_id=actor.organization_id, actor=actor.actor, role=actor.role
        )

    @router.get(
        "/ai/status", response_model=ProviderStatus, operation_id="getAIStatus", tags=["extraction"]
    )
    def provider_status(actor: Actor) -> ProviderStatus:
        with Session(engine) as session:
            return status_for(session, actor.organization_id)

    @router.post(
        "/ai/verify",
        response_model=ProviderStatus,
        operation_id="verifyFreeProvider",
        tags=["extraction"],
        description="Admin only. Read-only project/key/billing/quota/model checks. No generation.",
    )
    def verify(actor: Actor, key: IdempotencyKey) -> JSONResponse:
        require_writer(actor, admin=True)
        # Verification results are idempotently retained; a fresh key explicitly refreshes proof.
        from reconcileiq_api.contracts import Input

        def operation(session: Session) -> Mutation:
            code, proof_data, checked = None, {}, None
            try:
                if settings.environment != "local":
                    raise ProviderFailure("AI_LOCAL_ONLY")
                proof = verify_free_project(AISettings())
                checked = proof.checked_at
                proof_data = {**asdict(proof), "checked_at": checked.isoformat()}
            except ProviderFailure as error:
                code = error.code
            except ValueError:
                code = "AI_CONFIGURATION_INVALID"
            usage_id = uuid4().hex
            session.add(
                ProviderUsage(
                    id=usage_id,
                    organization_id=actor.organization_id,
                    job_id=None,
                    operation="verification",
                    model=FREE_MODEL,
                    prompt_version=PROMPT_VERSION,
                    proof=proof_data,
                    error_code=code,
                    free_tier=code is None,
                )
            )
            audit(
                session,
                actor,
                usage_id,
                "free_provider_verified",
                {"code": code or "VERIFIED_FREE_TIER"},
            )
            return Mutation(
                200,
                ProviderStatus(
                    available=code is None,
                    model=FREE_MODEL,
                    code=code or "VERIFIED_FREE_TIER",
                    checked_at=checked,
                ),
            )

        return mutate(engine, actor, key, "POST /v1/ai/verify", Input(), operation)

    @router.get(
        "/invoices/{invoice_id}/workspace",
        response_model=WorkspaceRead,
        operation_id="getInvoiceWorkspace",
        tags=["invoices"],
    )
    def workspace(invoice_id: str, actor: Actor) -> WorkspaceRead:
        with Session(engine) as session:
            row = owned(session, Invoice, invoice_id, actor)
            versions = session.scalars(
                select(ExtractionVersion)
                .where(
                    ExtractionVersion.organization_id == actor.organization_id,
                    ExtractionVersion.invoice_id == row.id,
                    ExtractionVersion.schema_version == SCHEMA,
                )
                .order_by(ExtractionVersion.number.desc())
                .limit(21)
            ).all()
            latest = versions[0] if versions else None
            evidence = session.scalar(
                select(ReconciliationEvidence)
                .where(
                    ReconciliationEvidence.organization_id == actor.organization_id,
                    ReconciliationEvidence.invoice_id == row.id,
                )
                .order_by(
                    ReconciliationEvidence.created_at.desc(),
                    ReconciliationEvidence.reconciliation_id.desc(),
                )
                .limit(1)
            )
            result = None
            if evidence:
                run = session.get(ReconciliationRun, evidence.reconciliation_id)
                assert run is not None
                result = ResultRead(
                    reconciliation_id=run.id,
                    policy_id=run.policy_id,
                    extraction_version_id=evidence.extraction_version_id,
                    invoice_version=evidence.invoice_version,
                    context=evidence.context,
                    **evidence.result,
                )
            related = [row.id] + [v.id for v in versions]
            related += list(
                session.scalars(
                    select(ReconciliationRun.id)
                    .where(
                        ReconciliationRun.organization_id == actor.organization_id,
                        ReconciliationRun.invoice_id == row.id,
                    )
                    .order_by(ReconciliationRun.created_at.desc())
                    .limit(100)
                )
            )
            related += list(
                session.scalars(
                    select(Job.id)
                    .where(Job.organization_id == actor.organization_id, Job.invoice_id == row.id)
                    .order_by(Job.created_at.desc())
                    .limit(100)
                )
            )
            events = session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == actor.organization_id,
                    AuditEvent.entity_id.in_(related),
                )
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .limit(101)
            ).all()
            return WorkspaceRead(
                invoice=row,
                document_available=bool(row.document_key),
                candidates=[
                    CandidateRead(
                        id=v.id,
                        number=v.number,
                        origin=v.origin,
                        created_at=v.created_at,
                        model=v.payload.get("model"),
                        prompt_version=v.payload.get("prompt_version"),
                        candidate=v.payload["candidate"],
                    )
                    for v in versions[:20]
                ],
                result=result,
                current_context=matching_context(
                    session, row, InvoiceCandidate.model_validate(latest.payload["candidate"])
                )
                if latest
                else None,
                timeline=[TimelineRead.model_validate(event) for event in events[:100]],
                history_truncated=len(versions) > 20 or len(events) > 100,
            )

    @router.get(
        "/invoices/{invoice_id}/document",
        operation_id="getInvoiceDocument",
        tags=["invoices"],
        response_class=Response,
        responses={
            200: {
                "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}
            }
        },
    )
    def document(invoice_id: str, actor: Actor) -> Response:
        with Session(engine) as session:
            row = owned(session, Invoice, invoice_id, actor)
            if not row.document_key or not isinstance(storage, DocumentStorage):
                raise ApiError(404, "DOCUMENT_UNAVAILABLE", "No retained PDF is available.")
            data = storage.read_document(row.document_key, row.document_size, row.document_sha256)
        return Response(
            data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'inline; filename="invoice.pdf"',
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get(
        "/invoices/{invoice_id}/pages/{page}",
        operation_id="getInvoicePagePreview",
        tags=["invoices"],
        response_class=Response,
        responses={
            200: {
                "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
                "headers": {
                    "X-Document-Pages": {"schema": {"type": "integer", "minimum": 1, "maximum": 20}}
                },
            }
        },
    )
    def preview(
        invoice_id: str, page: Annotated[int, PathParameter(ge=1, le=20)], actor: Actor
    ) -> Response:
        from reconcileiq_api.pdf_preview import render_page

        with Session(engine) as session:
            row = owned(session, Invoice, invoice_id, actor)
            if not row.document_key or not isinstance(storage, DocumentStorage):
                raise ApiError(404, "DOCUMENT_UNAVAILABLE", "No retained PDF is available.")
            data = storage.read_document(row.document_key, row.document_size, row.document_sha256)
        image, count = render_page(data, page)
        return Response(
            image,
            media_type="image/png",
            headers={"X-Document-Pages": str(count), "X-Content-Type-Options": "nosniff"},
        )

    @router.put(
        "/invoices/{invoice_id}/document",
        status_code=204,
        operation_id="uploadInvoiceDocument",
        tags=["invoices"],
        response_class=Response,
        description=(
            "Authenticated PDF PUT for browser clients. Exact declared hash/size. "
            "Repeated identical PUT is safe before completion."
        ),
    )
    def put_document(
        invoice_id: str, actor: Actor, data: Annotated[bytes, Body(media_type="application/pdf")]
    ) -> Response:
        require_writer(actor)
        with Session(engine) as session, session.begin():
            row = owned(session, Invoice, invoice_id, actor, lock=True)
            if row.status != "upload_requested" or not row.upload_key:
                raise ApiError(409, "STATE_CONFLICT", "This invoice is not awaiting an upload.")
            if (
                len(data) != row.document_size
                or not data.startswith(b"%PDF-")
                or hashlib.sha256(data).hexdigest() != row.document_sha256
            ):
                raise ApiError(422, "UPLOAD_INVALID", "PDF size, signature or SHA-256 disagrees.")
            if not isinstance(storage, DocumentStorage):
                raise ApiError(503, "DEPENDENCY_UNAVAILABLE", "Object storage unavailable.")
            storage.put_document(row.upload_key, data)
            audit(session, actor, row.id, "invoice_object_uploaded", {"size_bytes": len(data)})
        return Response(status_code=204)

    @router.post(
        "/invoices/{invoice_id}/structured-extractions",
        response_model=JobAccepted,
        status_code=202,
        operation_id="startStructuredExtraction",
        tags=["extraction"],
    )
    def structured(
        invoice_id: str, body: StructuredStart, actor: Actor, key: IdempotencyKey
    ) -> JSONResponse:
        require_writer(actor)

        def operation(session: Session) -> Mutation:
            row = owned(session, Invoice, invoice_id, actor, lock=True)
            version(row, body.expected_version)
            if not status_for(session, actor.organization_id).available:
                raise ApiError(
                    503,
                    "FREE_TIER_UNVERIFIED",
                    "Verify the free project first. Manual correction remains available.",
                )
            # A fixture digest is evidence of synthetic content, unlike a caller-supplied flag.
            from reconcileiq_ai.synthetic import synthetic_digest_allowed

            if settings.environment != "local" or not synthetic_digest_allowed(
                row.document_sha256 or ""
            ):
                raise ApiError(
                    422,
                    "AI_SYNTHETIC_INPUT_REQUIRED",
                    "Only repository-labelled synthetic PDFs may use Free Tier.",
                )
            source = session.scalar(
                select(ExtractionVersion)
                .where(
                    ExtractionVersion.organization_id == actor.organization_id,
                    ExtractionVersion.invoice_id == row.id,
                    ExtractionVersion.schema_version == "digital-text-v1",
                    ExtractionVersion.document_sha256 == row.document_sha256,
                )
                .order_by(ExtractionVersion.number.desc())
                .limit(1)
            )
            if source is None:
                raise ApiError(
                    409, "STATE_CONFLICT", "Successful digital text extraction is required."
                )
            active = session.scalar(
                select(Job.id)
                .where(
                    Job.organization_id == actor.organization_id,
                    Job.invoice_id == row.id,
                    Job.state.in_(["pending_dispatch", "queued", "running"]),
                )
                .limit(1)
            )
            if active:
                raise ApiError(409, "STATE_CONFLICT", "An invoice job is already active.")
            job = new_job(
                session, actor, "extraction", {"invoice_id": row.id, "structured": True}, row.id
            )
            row.version += 1
            session.add(
                StructuredRequest(
                    job_id=job.id,
                    organization_id=actor.organization_id,
                    invoice_id=row.id,
                    text_version_id=source.id,
                    invoice_version=row.version,
                    request_id=job.id,
                    model=FREE_MODEL,
                    prompt_version=PROMPT_VERSION,
                    synthetic_authorized=True,
                    requested_by=actor.actor,
                )
            )
            audit(
                session,
                actor,
                row.id,
                "structured_extraction_requested",
                {"job_id": job.id, "text_version_id": source.id},
            )
            return accepted(job)

        return mutate(
            engine,
            actor,
            key,
            f"POST /v1/invoices/{invoice_id}/structured-extractions",
            body,
            operation,
        )

    @router.post(
        "/invoices/{invoice_id}/corrections",
        response_model=CandidateRead,
        status_code=201,
        operation_id="correctInvoiceEvidence",
        tags=["invoices"],
    )
    def correction(
        invoice_id: str, body: CorrectionCreate, actor: Actor, key: IdempotencyKey
    ) -> JSONResponse:
        require_writer(actor)

        def operation(session: Session) -> Mutation:
            row = owned(session, Invoice, invoice_id, actor, lock=True)
            version(row, body.expected_version)
            if row.status in {"upload_requested", "pending_extraction"}:
                raise ApiError(
                    409, "STATE_CONFLICT", "Complete document processing before correction."
                )
            previous = latest_candidate(session, row)
            if (previous.id if previous else None) != body.source_version_id:
                raise ApiError(
                    409, "VERSION_CONFLICT", "Candidate changed; refresh before correcting."
                )
            candidate = body.candidate.model_copy(deep=True)
            old = (
                dict(
                    evidence_fields(InvoiceCandidate.model_validate(previous.payload["candidate"]))
                )
                if previous
                else {}
            )
            for path, field in evidence_fields(candidate):
                if path not in old or field != old[path]:
                    field.status = "human" if field.value is not None else "missing"
                    field.pages, field.quote = [], None
            recorded = append_candidate(
                session,
                row,
                candidate,
                "human_correction",
                {"source_version_id": body.source_version_id, "reason": body.reason},
                actor.actor,
                version_id=uuid4().hex,
            )
            row.status, row.version = "extracted", row.version + 1
            row.exception_code = row.exception_detail = None
            session.flush()
            return Mutation(
                201,
                CandidateRead(
                    id=recorded.id,
                    number=recorded.number,
                    origin=recorded.origin,
                    created_at=recorded.created_at,
                    model=None,
                    prompt_version=None,
                    candidate=candidate,
                ),
            )

        return mutate(
            engine, actor, key, f"POST /v1/invoices/{invoice_id}/corrections", body, operation
        )

    return router
