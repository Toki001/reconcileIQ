import json
from datetime import timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from reconcileiq_ai.invoice import InvoiceCandidate
from reconcileiq_database.config import Settings
from reconcileiq_database.financial import normalize_po, normalize_receipt
from reconcileiq_database.invoice_workflow import (
    context_digest,
    evaluate_candidate,
    latest_candidate,
    matching_context,
    publish_review,
)
from reconcileiq_database.models import (
    GoodsReceipt,
    Invoice,
    Job,
    OutboxEvent,
    PurchaseOrder,
    ReconciliationEvidence,
    ReconciliationRun,
    ReviewDecision,
    StructuredRequest,
    utcnow,
)
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from reconcileiq_api.auth import Actor, Principal, require_writer
from reconcileiq_api.contracts import (
    InvoiceDetail,
    InvoicePage,
    JobAccepted,
    JobRead,
    ProcessingJobPage,
    ProcessingJobRead,
    PurchaseOrderCreate,
    PurchaseOrderDetail,
    PurchaseOrderPage,
    PurchaseOrderUpdate,
    ReceiptCreate,
    ReceiptRead,
    ReconciliationStart,
    ReplayRequest,
    ReviewCreate,
    ReviewRead,
    UploadComplete,
    UploadGrant,
    UploadRequest,
)
from reconcileiq_api.errors import ERROR_RESPONSES, ApiError
from reconcileiq_api.pagination import paginate
from reconcileiq_api.storage import Storage
from reconcileiq_api.transactions import Mutation, audit, mutate, new_job

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_.:-]+$",
        description=(
            "Organization-wide unique retry key; retained at least 24 hours. Same "
            "actor/method/path/normalized body replays the committed response; different "
            "request returns 409."
        ),
    ),
]
PageSize = Annotated[int, Query(ge=1, le=100)]


def owned(
    session: Session, model: Any, resource_id: str, actor: Principal, *, lock: bool = False
) -> Any:
    query = select(model).where(
        model.id == resource_id, model.organization_id == actor.organization_id
    )
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Resource was not found in this organization.")
    return row


def version(row: Any, expected: int) -> None:
    if row.version != expected:
        raise ApiError(
            409, "VERSION_CONFLICT", "The record changed; read its current version and retry."
        )


def accepted(job: Job, reconciliation_id: str | None = None) -> Mutation:
    location = f"/v1/jobs/{job.id}"
    return Mutation(
        202,
        JobAccepted(
            job_id=job.id,
            invoice_id=job.invoice_id,
            reconciliation_id=reconciliation_id,
            status_url=location,
        ),
        location,
    )


def job_next_action(job: Job) -> str:
    if job.state == "succeeded":
        return (
            "Digital text captured as untrusted evidence; "
            "financial extraction/review is still required."
        )
    if job.state == "failed":
        return (
            "Admin may repair the dependency and replay with a reason."
            if job.retryable
            else "Correct the document and submit a new upload; this job cannot be replayed."
        )
    if job.stage == "retrying":
        return "A bounded retry is scheduled; no action is required."
    return "Processing is pending or in progress. Poll this resource."


def build_router(engine: Engine, settings: Settings, storage: Storage) -> APIRouter:
    router = APIRouter(prefix="/v1", responses=ERROR_RESPONSES)

    @router.post(
        "/invoices/upload-requests",
        response_model=UploadGrant,
        status_code=201,
        operation_id="requestInvoiceUpload",
        tags=["invoices"],
        description=(
            "Admin/reviewer. Creates an unextracted invoice and a tenant-scoped, "
            "ten-minute presigned PUT. Completion verifies size, PDF magic bytes and "
            "SHA-256, then seals a separate immutable input copy."
        ),
    )
    def upload_request(body: UploadRequest, actor: Actor, key: IdempotencyKey) -> JSONResponse:
        require_writer(actor)

        def operation(session: Session) -> Mutation:
            if body.purchase_order_id:
                po = owned(session, PurchaseOrder, body.purchase_order_id, actor, lock=True)
                if po.status == "cancelled":
                    raise ApiError(409, "STATE_CONFLICT", "The purchase order is cancelled.")
            invoice_id = uuid4().hex
            object_key = f"{actor.organization_id}/uploads/{invoice_id}/{uuid4().hex}.pdf"
            url = storage.grant(object_key)
            invoice = Invoice(
                id=invoice_id,
                organization_id=actor.organization_id,
                vendor=None,
                description="Awaiting document extraction",
                currency=None,
                amount_minor=None,
                due_on=None,
                status="upload_requested",
                synthetic=False,
                upload_key=object_key,
                document_sha256=body.sha256,
                document_size=body.size_bytes,
                purchase_order_id=body.purchase_order_id,
            )
            session.add(invoice)
            audit(
                session,
                actor,
                invoice_id,
                "invoice_upload_requested",
                {
                    "status": "upload_requested",
                    "sha256": body.sha256,
                    "size_bytes": body.size_bytes,
                },
            )
            return Mutation(
                201,
                UploadGrant(
                    invoice_id=invoice_id,
                    upload_url=url,
                    required_headers={"Content-Type": "application/pdf"},
                    expires_at=utcnow() + timedelta(minutes=10),
                ),
                f"/v1/invoices/{invoice_id}",
            )

        return mutate(engine, actor, key, "POST /v1/invoices/upload-requests", body, operation)

    @router.post(
        "/invoices/{invoice_id}/upload-complete",
        response_model=JobAccepted,
        status_code=202,
        operation_id="completeInvoiceUpload",
        tags=["invoices"],
        description=(
            "Admin/reviewer. Atomically records verified completion, a pending-dispatch "
            "extraction job, its outbox envelope and audit events. 202 is admission, not "
            "successful extraction."
        ),
    )
    def upload_complete(
        invoice_id: str, body: UploadComplete, actor: Actor, key: IdempotencyKey
    ) -> JSONResponse:
        require_writer(actor)

        def operation(session: Session) -> Mutation:
            row = owned(session, Invoice, invoice_id, actor, lock=True)
            version(row, body.expected_version)
            if row.status != "upload_requested" or not row.upload_key:
                raise ApiError(409, "STATE_CONFLICT", "This invoice is not awaiting an upload.")
            row.document_key = storage.verify_and_seal(
                row.upload_key, actor.organization_id, row.document_size, row.document_sha256
            )
            row.version += 1
            row.status = "pending_extraction"
            job = new_job(
                session,
                actor,
                "extraction",
                {
                    "invoice_id": row.id,
                    "document_key": row.document_key,
                    "sha256": row.document_sha256,
                    "extraction_version": 1,
                },
                row.id,
            )
            audit(
                session,
                actor,
                row.id,
                "invoice_upload_completed",
                {
                    "before": "upload_requested",
                    "after": row.status,
                    "version": row.version,
                    "job_id": job.id,
                },
            )
            return accepted(job)

        return mutate(
            engine, actor, key, f"POST /v1/invoices/{invoice_id}/upload-complete", body, operation
        )

    @router.get(
        "/invoices", response_model=InvoicePage, operation_id="listInvoices", tags=["invoices"]
    )
    def invoices(
        actor: Actor,
        first: PageSize = 20,
        after: str | None = None,
        status: str | None = Query(default=None, max_length=32),
        vendor: str | None = Query(default=None, max_length=120),
    ) -> InvoicePage:
        conditions = []
        if status:
            conditions.append(Invoice.status == status)
        if vendor:
            conditions.append(Invoice.vendor == vendor)
        with Session(engine) as session:
            page = paginate(
                session,
                Invoice,
                actor,
                settings,
                first=first,
                after=after,
                scope=json.dumps(["invoices", status, vendor]),
                conditions=conditions,
            )
            return InvoicePage(
                items=[InvoiceDetail.model_validate(row) for row in page.rows], page_info=page.info
            )

    @router.get(
        "/invoices/{invoice_id}",
        response_model=InvoiceDetail,
        operation_id="getInvoice",
        tags=["invoices"],
    )
    def invoice(invoice_id: str, actor: Actor) -> InvoiceDetail:
        with Session(engine) as session:
            return InvoiceDetail.model_validate(owned(session, Invoice, invoice_id, actor))

    @router.post(
        "/purchase-orders",
        response_model=PurchaseOrderDetail,
        status_code=201,
        operation_id="createPurchaseOrder",
        tags=["purchasing"],
        description=(
            "Admin only. Exclusive-tax, single-currency PO; line and header totals are "
            "validated deterministically. All money is integer minor units; quantities "
            "are decimal strings."
        ),
    )
    def create_po(body: PurchaseOrderCreate, actor: Actor, key: IdempotencyKey) -> JSONResponse:
        require_writer(actor, admin=True)

        def operation(session: Session) -> Mutation:
            po = PurchaseOrder(
                id=uuid4().hex,
                organization_id=actor.organization_id,
                **body.model_dump(exclude={"lines", "tax_mode"}),
                line_items=[row.model_dump() for row in body.lines],
                status="open",
            )
            session.add(po)
            session.flush()
            normalize_po(session, po, actor.actor)
            audit(
                session,
                actor,
                po.id,
                "purchase_order_created",
                {"record": body.model_dump(mode="json"), "version": 1},
            )
            return Mutation(
                201, PurchaseOrderDetail.model_validate(po), f"/v1/purchase-orders/{po.id}"
            )

        return mutate(engine, actor, key, "POST /v1/purchase-orders", body, operation)

    @router.get(
        "/purchase-orders",
        response_model=PurchaseOrderPage,
        operation_id="listPurchaseOrders",
        tags=["purchasing"],
    )
    def purchase_orders(
        actor: Actor, first: PageSize = 20, after: str | None = None
    ) -> PurchaseOrderPage:
        with Session(engine) as session:
            page = paginate(
                session,
                PurchaseOrder,
                actor,
                settings,
                first=first,
                after=after,
                scope="purchase-orders",
            )
            return PurchaseOrderPage(
                items=[PurchaseOrderDetail.model_validate(row) for row in page.rows],
                page_info=page.info,
            )

    @router.get(
        "/purchase-orders/{po_id}",
        response_model=PurchaseOrderDetail,
        operation_id="getPurchaseOrder",
        tags=["purchasing"],
    )
    def purchase_order(po_id: str, actor: Actor) -> PurchaseOrderDetail:
        with Session(engine) as session:
            return PurchaseOrderDetail.model_validate(owned(session, PurchaseOrder, po_id, actor))

    @router.patch(
        "/purchase-orders/{po_id}",
        response_model=PurchaseOrderDetail,
        operation_id="updatePurchaseOrder",
        tags=["purchasing"],
        description=(
            "Admin only. Versioned description/cancellation update. Monetary snapshots "
            "are immutable; cancellation is forbidden after an invoice references the PO."
        ),
    )
    def update_po(
        po_id: str, body: PurchaseOrderUpdate, actor: Actor, key: IdempotencyKey
    ) -> JSONResponse:
        require_writer(actor, admin=True)

        def operation(session: Session) -> Mutation:
            row = owned(session, PurchaseOrder, po_id, actor, lock=True)
            version(row, body.expected_version)
            if row.status != "open":
                raise ApiError(409, "STATE_CONFLICT", "Only open purchase orders may be updated.")
            if body.status == "cancelled" and session.scalar(
                select(Invoice.id)
                .where(
                    Invoice.purchase_order_id == po_id,
                    Invoice.organization_id == actor.organization_id,
                )
                .limit(1)
            ):
                raise ApiError(
                    409, "STATE_CONFLICT", "An invoice already references this purchase order."
                )
            before = {"description": row.description, "status": row.status, "version": row.version}
            row.description, row.status, row.version = (
                body.description,
                body.status,
                row.version + 1,
            )
            audit(
                session,
                actor,
                row.id,
                "purchase_order_updated",
                {"before": before, "after": body.model_dump(), "version": row.version},
            )
            return Mutation(200, PurchaseOrderDetail.model_validate(row))

        return mutate(engine, actor, key, f"PATCH /v1/purchase-orders/{po_id}", body, operation)

    @router.post(
        "/goods-receipts",
        response_model=ReceiptRead,
        status_code=201,
        operation_id="createGoodsReceipt",
        tags=["purchasing"],
    )
    def receipt(body: ReceiptCreate, actor: Actor, key: IdempotencyKey) -> JSONResponse:
        require_writer(actor, admin=True)

        def operation(session: Session) -> Mutation:
            po = owned(session, PurchaseOrder, body.purchase_order_id, actor, lock=True)
            line_ids = {line["id"] for line in po.line_items}
            if (
                po.status == "cancelled"
                or len({line.purchase_order_line_id for line in body.lines}) != len(body.lines)
                or any(line.purchase_order_line_id not in line_ids for line in body.lines)
            ):
                raise ApiError(
                    422,
                    "VALIDATION_ERROR",
                    "Receipt lines must uniquely reference an active PO's lines.",
                )
            row = GoodsReceipt(
                id=uuid4().hex, organization_id=actor.organization_id, **body.model_dump()
            )
            session.add(row)
            session.flush()
            normalize_receipt(session, row, actor.actor)
            audit(
                session,
                actor,
                row.id,
                "goods_receipt_created",
                {"record": body.model_dump(mode="json")},
            )
            return Mutation(201, ReceiptRead.model_validate(row))

        return mutate(engine, actor, key, "POST /v1/goods-receipts", body, operation)

    @router.post(
        "/reconciliations",
        response_model=JobAccepted,
        status_code=202,
        operation_id="startReconciliation",
        tags=["reconciliation"],
    )
    def reconcile(body: ReconciliationStart, actor: Actor, key: IdempotencyKey) -> JSONResponse:
        require_writer(actor)

        def operation(session: Session) -> Mutation:
            row = owned(session, Invoice, body.invoice_id, actor, lock=True)
            version(row, body.expected_version)
            if row.status not in {"extracted", "needs_review", "matched", "exception", "duplicate"}:
                raise ApiError(
                    409,
                    "STATE_CONFLICT",
                    "A validated extraction is required before reconciliation.",
                )
            if session.scalar(
                select(ReconciliationRun.id)
                .where(
                    ReconciliationRun.invoice_id == row.id,
                    ReconciliationRun.status == "pending",
                    ReconciliationRun.organization_id == actor.organization_id,
                )
                .limit(1)
            ):
                raise ApiError(
                    409, "STATE_CONFLICT", "This invoice already has a pending reconciliation."
                )
            run_id = uuid4().hex
            job = new_job(
                session,
                actor,
                "reconciliation",
                {"invoice_id": row.id, "reconciliation_id": run_id, "policy_id": body.policy_id},
                row.id,
            )
            source = latest_candidate(session, row)
            session.add(
                ReconciliationRun(
                    id=run_id,
                    organization_id=actor.organization_id,
                    invoice_id=row.id,
                    job_id=job.id,
                    policy_id=body.policy_id,
                    extraction_version_id=source.id if source else None,
                )
            )
            row.version += 1
            audit(
                session,
                actor,
                run_id,
                "reconciliation_requested",
                {"invoice_id": row.id, "policy_id": body.policy_id, "invoice_version": row.version},
            )
            return accepted(job, run_id)

        return mutate(engine, actor, key, "POST /v1/reconciliations", body, operation)

    @router.post(
        "/invoices/{invoice_id}/review-decisions",
        response_model=ReviewRead,
        status_code=201,
        operation_id="recordReviewDecision",
        tags=["reconciliation"],
        description=(
            "Admin/reviewer. Requires a completed, current reconciliation and expected "
            "invoice version. Records a human match decision with a reason, never payment"
            " or spending authorization."
        ),
    )
    def review(
        invoice_id: str, body: ReviewCreate, actor: Actor, key: IdempotencyKey
    ) -> JSONResponse:
        require_writer(actor)

        def operation(session: Session) -> Mutation:
            row = owned(session, Invoice, invoice_id, actor, lock=True)
            version(row, body.expected_version)
            run = owned(session, ReconciliationRun, body.reconciliation_id, actor)
            latest = session.scalar(
                select(ReconciliationRun.id)
                .where(
                    ReconciliationRun.invoice_id == row.id,
                    ReconciliationRun.organization_id == actor.organization_id,
                )
                .order_by(ReconciliationRun.created_at.desc(), ReconciliationRun.id.desc())
                .limit(1)
            )
            if (
                run.invoice_id != row.id
                or run.status != "completed"
                or latest != run.id
                or row.status not in {"matched", "exception", "needs_review", "duplicate"}
            ):
                raise ApiError(
                    409,
                    "STATE_CONFLICT",
                    "A completed current result in a reviewable state is required.",
                )
            evidence = session.get(ReconciliationEvidence, run.id)
            source = latest_candidate(session, row)
            if evidence is not None or source is not None:
                if (
                    evidence is None
                    or source is None
                    or evidence.extraction_version_id != source.id
                    or evidence.invoice_version != row.version
                ):
                    raise ApiError(
                        409,
                        "STALE_RECONCILIATION",
                        "Reconcile the current candidate before review.",
                    )
                candidate = InvoiceCandidate.model_validate(source.payload["candidate"])
                context = matching_context(session, row, candidate)
                if context_digest(context) != evidence.context_sha256:
                    raise ApiError(
                        409,
                        "STALE_RECONCILIATION",
                        "Purchase order or receipts changed; reconcile again.",
                    )
                if body.decision == "accept":
                    if evaluate_candidate(candidate, context, run.policy_id).outcome != "matched":
                        raise ApiError(
                            409,
                            "REVIEW_REQUIRES_MATCH",
                            "Correct discrepancies and reconcile before acceptance.",
                        )
                    try:
                        publish_review(session, row, source, actor.actor, uuid4().hex)
                    except ValueError:
                        raise ApiError(
                            409,
                            "PUBLICATION_CONFLICT",
                            "Published supplier, currency or order binding cannot change.",
                        ) from None
            before = {"status": row.status, "version": row.version}
            row.status = "reviewed_accepted" if body.decision == "accept" else "reviewed_rejected"
            decision = ReviewDecision(
                id=uuid4().hex,
                organization_id=actor.organization_id,
                invoice_id=row.id,
                reconciliation_id=run.id,
                invoice_version=row.version,
                decision=body.decision,
                reason=body.reason,
                actor=actor.actor,
                user_id=actor.user_id,
            )
            row.version += 1
            session.add(decision)
            session.flush()
            audit(
                session,
                actor,
                row.id,
                "review_decision_recorded",
                {
                    "before": before,
                    "after": {"status": row.status, "version": row.version},
                    "decision_id": decision.id,
                    "reconciliation_id": run.id,
                    "reason": body.reason,
                },
            )
            return Mutation(201, ReviewRead.model_validate(decision))

        return mutate(
            engine, actor, key, f"POST /v1/invoices/{invoice_id}/review-decisions", body, operation
        )

    @router.get("/jobs/{job_id}", response_model=JobRead, operation_id="getJob", tags=["jobs"])
    def job_state(job_id: str, actor: Actor) -> JobRead:
        with Session(engine) as session:
            row = owned(session, Job, job_id, actor)
            return JobRead.model_validate(row).model_copy(
                update={"next_action": job_next_action(row)}
            )

    @router.get(
        "/processing-jobs",
        response_model=ProcessingJobPage,
        operation_id="listProcessingJobs",
        tags=["jobs"],
        description=(
            "Tenant-scoped digital invoice jobs with stable cursor pagination "
            "and durable backlog counts."
        ),
    )
    def processing_jobs(
        actor: Actor, first: PageSize = 20, after: str | None = None
    ) -> ProcessingJobPage:
        with Session(engine) as session:
            page = paginate(
                session,
                Job,
                actor,
                settings,
                first=first,
                after=after,
                scope="processing-jobs",
                conditions=[Job.kind == "extraction"],
            )
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.organization_id == actor.organization_id,
                        Job.state.in_(["pending_dispatch", "queued", "running"]),
                    )
                )
                or 0
            )
            return ProcessingJobPage(
                items=[
                    ProcessingJobRead.model_validate(row).model_copy(
                        update={"next_action": job_next_action(row)}
                    )
                    for row in page.rows
                ],
                page_info=page.info,
                active_count=count,
                admission_limit=settings.admitted_job_limit,
            )

    @router.post(
        "/jobs/{job_id}/replay",
        response_model=JobAccepted,
        status_code=202,
        operation_id="replayJob",
        tags=["jobs"],
    )
    def replay(job_id: str, body: ReplayRequest, actor: Actor, key: IdempotencyKey) -> JSONResponse:
        require_writer(actor, admin=True)

        def operation(session: Session) -> Mutation:
            original = owned(session, Job, job_id, actor, lock=True)
            version(original, body.expected_version)
            envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == original.id))
            if original.state != "failed" or not original.retryable or envelope is None:
                raise ApiError(409, "STATE_CONFLICT", "Only retryable failed jobs may be replayed.")
            if session.scalar(
                select(Job.id)
                .where(Job.replay_of == job_id, Job.organization_id == actor.organization_id)
                .limit(1)
            ):
                raise ApiError(409, "STATE_CONFLICT", "This execution already has a replay.")
            new = new_job(
                session,
                actor,
                original.kind,
                {**envelope.payload, "replay_of": original.id},
                original.invoice_id,
                original.id,
            )
            request = session.get(StructuredRequest, original.id)
            if request is not None:
                invoice = owned(session, Invoice, request.invoice_id, actor, lock=True)
                invoice.version += 1
                session.add(
                    StructuredRequest(
                        job_id=new.id,
                        organization_id=actor.organization_id,
                        invoice_id=invoice.id,
                        text_version_id=request.text_version_id,
                        invoice_version=invoice.version,
                        request_id=request.request_id,
                        model=request.model,
                        prompt_version=request.prompt_version,
                        synthetic_authorized=request.synthetic_authorized,
                        requested_by=actor.actor,
                    )
                )
            run = session.scalar(
                select(ReconciliationRun).where(
                    ReconciliationRun.organization_id == actor.organization_id,
                    ReconciliationRun.job_id == original.id,
                )
            )
            if original.kind == "reconciliation" and run and run.extraction_version_id:
                invoice = owned(session, Invoice, run.invoice_id, actor, lock=True)
                source = latest_candidate(session, invoice)
                if source is None or source.id != run.extraction_version_id:
                    raise ApiError(
                        409,
                        "EXTRACTION_SUPERSEDED",
                        "Start a new reconciliation for current evidence.",
                    )
                run_id = uuid4().hex
                session.add(
                    ReconciliationRun(
                        id=run_id,
                        organization_id=actor.organization_id,
                        invoice_id=invoice.id,
                        job_id=new.id,
                        policy_id=run.policy_id,
                        extraction_version_id=source.id,
                    )
                )
                invoice.version += 1
            original.version += 1
            audit(
                session,
                actor,
                original.id,
                "job_replay_requested",
                {"job_id": new.id, "reason": body.reason},
            )
            return accepted(new)

        return mutate(engine, actor, key, f"POST /v1/jobs/{job_id}/replay", body, operation)

    return router
