import hashlib
import hmac
import json
import re
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import ValidationError
from reconcileiq_database.config import Settings
from reconcileiq_database.models import Organization, WebhookReceipt
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reconcileiq_api.auth import Principal, Role
from reconcileiq_api.contracts import JobAccepted, SupplierNotice
from reconcileiq_api.errors import ERROR_RESPONSES, ApiError
from reconcileiq_api.transactions import audit, new_job

signature_header = APIKeyHeader(
    name="X-Signature",
    scheme_name="SupplierHmac",
    auto_error=False,
    description=(
        "v1=<HMAC-SHA256(secret, ASCII(X-Timestamp) + '.' + exact raw JSON bytes)>. "
        "X-Supplier-Id selects a server-configured organization and secret. Timestamp"
        " tolerance: 300 seconds."
    ),
)


def build_webhook_router(engine: Engine, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/webhooks/suppliers",
        status_code=202,
        response_model=JobAccepted,
        responses=ERROR_RESPONSES,
        operation_id="receiveSupplierWebhook",
        tags=["webhooks"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": SupplierNotice.model_json_schema()}},
            }
        },
        description=(
            "Signed invoice.available notification, admitted transactionally for later "
            "processing. No remote URLs are fetched and no amounts or financial state are"
            " trusted. Event IDs deduplicate per supplier and organization; changed "
            "payload with the same ID returns 409."
        ),
    )
    async def webhook(
        request: Request,
        supplier_id: Annotated[str, Header(alias="X-Supplier-Id", min_length=1, max_length=80)],
        timestamp: Annotated[str, Header(alias="X-Timestamp", pattern=r"^[0-9]{10}$")],
        signature: Annotated[str | None, Depends(signature_header)],
    ) -> JSONResponse:
        config = settings.supplier_webhooks.get(supplier_id)
        if (
            config is None
            or signature is None
            or re.fullmatch(r"v1=[0-9a-f]{64}", signature) is None
        ):
            raise ApiError(401, "WEBHOOK_INVALID", "Supplier signature is invalid.")
        if abs(int(timestamp) - int(time.time())) > 300:
            raise ApiError(
                401, "WEBHOOK_EXPIRED", "Supplier timestamp is outside the five-minute window."
            )
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise ApiError(
                422, "VALIDATION_ERROR", "Supplier webhook content type must be application/json."
            )
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65_536:
                raise ApiError(413, "VALIDATION_ERROR", "Supplier webhook exceeds 64 KiB.")
        expected = (
            "v1="
            + hmac.new(
                config.secret.get_secret_value().encode(),
                timestamp.encode() + b"." + raw,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise ApiError(401, "WEBHOOK_INVALID", "Supplier signature is invalid.")
        try:
            body = SupplierNotice.model_validate_json(bytes(raw))
        except ValidationError:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "Supplier payload does not match the invoice notice contract.",
            ) from None
        actor = Principal(config.organization_id, f"supplier:{supplier_id}", Role.REVIEWER)
        digest = hashlib.sha256(
            json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        receipt_key = (actor.organization_id, supplier_id, body.event_id)

        def response(receipt: WebhookReceipt, replay: bool) -> JSONResponse:
            if receipt.fingerprint != digest:
                raise ApiError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "Supplier event ID was already used for different content.",
                )
            location = f"/v1/jobs/{receipt.job_id}"
            return JSONResponse(
                status_code=202,
                content=JobAccepted(job_id=receipt.job_id, status_url=location).model_dump(
                    mode="json"
                ),
                headers={
                    "Location": location,
                    "Retry-After": "2",
                    "Idempotency-Replayed": str(replay).lower(),
                },
            )

        def persist() -> JSONResponse:
            try:
                with Session(engine) as session, session.begin():
                    if (
                        session.get(Organization, actor.organization_id, with_for_update=True)
                        is None
                    ):
                        raise ApiError(
                            503,
                            "DEPENDENCY_UNAVAILABLE",
                            "Supplier organization is not provisioned.",
                        )
                    existing = session.get(WebhookReceipt, receipt_key)
                    if existing:
                        return response(existing, True)
                    job = new_job(
                        session,
                        actor,
                        "supplier_webhook",
                        {"supplier_id": supplier_id, "notice": body.model_dump()},
                    )
                    receipt = WebhookReceipt(
                        organization_id=actor.organization_id,
                        supplier_id=supplier_id,
                        event_id=body.event_id,
                        fingerprint=digest,
                        job_id=job.id,
                    )
                    session.add(receipt)
                    session.flush()
                    audit(
                        session,
                        actor,
                        job.id,
                        "supplier_webhook_received",
                        {
                            "supplier_id": supplier_id,
                            "event_id": body.event_id,
                            "event_type": body.event_type,
                        },
                    )
                    return response(receipt, False)
            except IntegrityError:
                with Session(engine) as session:
                    existing = session.get(WebhookReceipt, receipt_key)
                    if existing:
                        return response(existing, True)
                raise ApiError(
                    409, "STATE_CONFLICT", "Supplier event conflicted with another request."
                ) from None

        return await run_in_threadpool(persist)

    return router
