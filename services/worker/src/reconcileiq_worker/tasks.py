from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from pydantic import Field, TypeAdapter
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import AuditEvent, JobExecution
from reconcileiq_database.session import build_engine
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from reconcileiq_worker.app import celery_app

ProbeKey = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]


def record_probe(key: str, engine: Engine) -> str:
    """Commit the effect and its audit event together; PK arbitrates concurrent delivery."""
    TypeAdapter(ProbeKey).validate_python(key)
    try:
        with Session(engine) as session, session.begin():
            session.add(JobExecution(key=key, result="ok", completed_at=datetime.now(UTC)))
            session.flush()
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    entity_id=key,
                    actor="worker",
                    action="bootstrap_probe_completed",
                    detail="Idempotent infrastructure probe completed. No financial state changed.",
                    occurred_at=datetime.now(UTC),
                )
            )
    except IntegrityError:
        with Session(engine) as session:
            existing = session.get(JobExecution, ("demo", key))
            if existing is None:
                raise
            return existing.result
    return "ok"


def run_probe(key: str) -> str:
    engine = build_engine(get_settings())
    try:
        return record_probe(key, engine)
    finally:
        engine.dispose()


probe_task = celery_app.task(
    name="reconcileiq.probe",
    acks_late=True,
    autoretry_for=(OperationalError,),
    retry_kwargs={"max_retries": 4},
    retry_backoff=True,
    retry_jitter=True,
)(run_probe)


def run_invoice(organization_id: str, job_id: str) -> None:
    from reconcileiq_worker.processing import process_job

    settings = get_settings()
    engine = build_engine(settings)
    try:
        process_job(engine, settings, organization_id, job_id)
    except Exception:
        # A database outage leaves a durable running lease (or pending admission).
        # Recovery reclaims it; Celery must not dump a credential-bearing traceback.
        return
    finally:
        engine.dispose()


invoice_task = celery_app.task(
    name="reconcileiq.process_invoice", acks_late=True, reject_on_worker_lost=True
)(run_invoice)
