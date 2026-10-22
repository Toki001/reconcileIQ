import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from reconcileiq_database.models import (
    AuditEvent,
    IdempotencyRecord,
    Job,
    Organization,
    OutboxEvent,
    utcnow,
)
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reconcileiq_api.auth import Principal
from reconcileiq_api.errors import ApiError


@dataclass
class Mutation:
    status: int
    body: BaseModel
    location: str | None = None


def audit(
    session: Session, actor: Principal, entity_id: str, action: str, detail: dict[str, Any]
) -> None:
    session.add(
        AuditEvent(
            id=uuid4().hex,
            organization_id=actor.organization_id,
            entity_id=entity_id,
            actor=actor.actor,
            action=action,
            detail=json.dumps(detail, sort_keys=True, separators=(",", ":")),
            occurred_at=utcnow(),
        )
    )


def new_job(
    session: Session,
    actor: Principal,
    kind: str,
    payload: dict[str, Any],
    invoice_id: str | None = None,
    replay_of: str | None = None,
) -> Job:
    session.get(Organization, actor.organization_id, with_for_update=True)
    count = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.organization_id == actor.organization_id,
            Job.state.in_(["pending_dispatch", "queued", "running"]),
        )
    )
    if count is not None and count >= 500:
        raise ApiError(429, "ADMISSION_LIMIT", "The organization has 500 active jobs. Retry later.")
    job = Job(
        id=uuid4().hex,
        organization_id=actor.organization_id,
        invoice_id=invoice_id,
        kind=kind,
        state="pending_dispatch",
        replay_of=replay_of,
    )
    session.add(job)
    session.flush()
    session.add(
        OutboxEvent(
            id=uuid4().hex, organization_id=actor.organization_id, job_id=job.id, payload=payload
        )
    )
    audit(
        session,
        actor,
        job.id,
        "job_admitted",
        {"kind": kind, "state": "pending_dispatch", "replay_of": replay_of},
    )
    return job


def mutate(
    engine: Engine,
    actor: Principal,
    key: str,
    route: str,
    body: BaseModel,
    operation: Callable[[Session], Mutation],
) -> JSONResponse:
    fingerprint = hashlib.sha256(
        json.dumps(
            {"actor": actor.actor, "route": route, "body": body.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    record_key = (actor.organization_id, key)

    def response(record: IdempotencyRecord, replay: bool) -> JSONResponse:
        if record.fingerprint != fingerprint:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This key was already used for a different request or actor.",
            )
        headers = {"Idempotency-Replayed": str(replay).lower()}
        if record.location:
            headers["Location"] = record.location
        if record.status_code == 202:
            headers["Retry-After"] = "2"
        return JSONResponse(
            status_code=record.status_code, content=record.response, headers=headers
        )

    try:
        with Session(engine) as session, session.begin():
            existing = session.get(IdempotencyRecord, record_key)
            if existing is not None:
                return response(existing, True)
            record = IdempotencyRecord(
                organization_id=actor.organization_id,
                key=key,
                fingerprint=fingerprint,
                status_code=0,
                response={},
                location=None,
            )
            session.add(record)
            session.flush()  # Unique transactional claim precedes every business effect.
            result = operation(session)
            record.status_code, record.response, record.location = (
                result.status,
                result.body.model_dump(mode="json"),
                result.location,
            )
            session.flush()
            return response(record, False)
    except IntegrityError:
        with Session(engine) as session:
            existing = session.get(IdempotencyRecord, record_key)
            if existing is not None:
                return response(existing, True)
        raise ApiError(
            409, "STATE_CONFLICT", "A record with the same business reference already exists."
        ) from None
