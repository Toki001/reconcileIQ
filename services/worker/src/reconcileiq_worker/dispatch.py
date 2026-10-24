"""Recoverable confirmed publication; no broker transaction can atomically commit PostgreSQL."""

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

from celery import bootsteps
from kombu import Connection, Exchange, Producer, Queue
from reconcileiq_database.config import Settings, get_settings
from reconcileiq_database.models import Job, OutboxEvent
from reconcileiq_database.session import build_engine
from reconcileiq_telemetry import carrier, initialize, instrument, metric, parent, stage
from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from reconcileiq_worker.processing import bounded_session, event, fail, now
from reconcileiq_worker.providers import ProcessingError

DEAD_EXCHANGE = Exchange("reconcileiq.dead", type="direct", durable=True)
DEAD_QUEUE = Queue("reconcileiq.dead", exchange=DEAD_EXCHANGE, routing_key="terminal", durable=True)
TASK_EXCHANGE = Exchange("reconcileiq.tasks", type="direct", durable=True)
QUEUE_ARGUMENTS = {
    "x-dead-letter-exchange": "reconcileiq.dead",
    "x-dead-letter-routing-key": "terminal",
    "x-max-length": 10000,
    "x-overflow": "reject-publish",
}
INVOICE_QUEUE = Queue(
    "reconcileiq.invoices",
    exchange=TASK_EXCHANGE,
    routing_key="invoice",
    durable=True,
    queue_arguments=QUEUE_ARGUMENTS,
)
PROBE_QUEUE = Queue(
    "reconcileiq.probes",
    exchange=TASK_EXCHANGE,
    routing_key="probe",
    durable=True,
    queue_arguments=QUEUE_ARGUMENTS,
)
Publish = Callable[[str, str, bool, dict[str, Any]], None]


@instrument("messaging.publish")
def publish(
    settings: Settings, org: str, job_id: str, terminal: bool, diagnostic: dict[str, Any]
) -> None:
    with Connection(
        settings.broker_url.get_secret_value(),
        connect_timeout=3,
        transport_options={"confirm_publish": True, "read_timeout": 5, "write_timeout": 5},
    ) as connection:
        producer = Producer(connection)
        if terminal:
            producer.publish(
                {
                    "event": "job.terminal_failure",
                    "organization_id": org,
                    "job_id": job_id,
                    **diagnostic,
                },
                exchange=DEAD_EXCHANGE,
                routing_key="terminal",
                serializer="json",
                delivery_mode=2,
                declare=[DEAD_QUEUE],
                retry=False,
                timeout=5,
            )
        else:
            # Celery protocol v2. Only server-generated IDs cross the broker boundary.
            producer.publish(
                (
                    [org, job_id],
                    {},
                    {"callbacks": None, "errbacks": None, "chain": None, "chord": None},
                ),
                exchange=TASK_EXCHANGE,
                routing_key="invoice",
                serializer="json",
                delivery_mode=2,
                declare=[INVOICE_QUEUE, DEAD_QUEUE],
                retry=False,
                timeout=5,
                correlation_id=job_id,
                headers={
                    "lang": "py",
                    **carrier(),
                    "task": "reconcileiq.process_invoice",
                    "id": uuid4().hex,
                    "argsrepr": "<organization and job identifiers>",
                    "kwargsrepr": "{}",
                    "retries": 0,
                    "timelimit": [60, 40],
                },
            )


@instrument("outbox.recover")
def recover(engine: Engine, settings: Settings) -> None:
    with Session(engine) as session, session.begin():
        bounded_session(session)
        jobs = session.scalars(
            select(Job)
            .outerjoin(OutboxEvent, OutboxEvent.job_id == Job.id)
            .where(
                or_(
                    (Job.state == "running")
                    & or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now()),
                    (Job.state == "queued")
                    & (
                        Job.updated_at
                        <= now() - timedelta(seconds=settings.dispatch_visibility_seconds)
                    ),
                    (Job.state == "pending_dispatch") & OutboxEvent.id.is_(None),
                )
            )
            .order_by(Job.updated_at, Job.id)
            .limit(settings.dispatch_batch_size)
            .with_for_update(of=Job, skip_locked=True)
        ).all()
        for job in jobs:
            metric("riq.lease.recoveries", labels={"reason": "lease"})
            envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id))
            if envelope is None:
                envelope = OutboxEvent(
                    id=uuid4().hex, organization_id=job.organization_id, job_id=job.id, payload={}
                )
                session.add(envelope)
                session.flush()
                event(session, job, "missing_outbox_restored")
            if job.state == "running":
                fail(session, job, ProcessingError("WORKER_LEASE_EXPIRED", True), settings)
            elif job.state == "queued":
                job.state, job.stage = "pending_dispatch", "recovering_dispatch"
                job.updated_at, job.version = now(), job.version + 1
                envelope.published_at = envelope.lease_owner = envelope.lease_expires_at = None
                envelope.available_at = now()
                event(session, job, "unconsumed_dispatch_recovered")


@instrument("outbox.dispatch")
def dispatch_once(engine: Engine, settings: Settings, sender: Publish | None = None) -> int:
    recover(engine, settings)
    count = 0
    for _ in range(settings.dispatch_batch_size):
        token = uuid4().hex
        with Session(engine) as session, session.begin():
            bounded_session(session)
            job = session.scalar(
                select(Job)
                .join(OutboxEvent, OutboxEvent.job_id == Job.id)
                .where(
                    Job.state.in_(["pending_dispatch", "queued", "failed"]),
                    OutboxEvent.published_at.is_(None),
                    or_(OutboxEvent.available_at.is_(None), OutboxEvent.available_at <= now()),
                    or_(
                        OutboxEvent.lease_expires_at.is_(None),
                        OutboxEvent.lease_expires_at <= now(),
                    ),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(1)
                .with_for_update(of=Job, skip_locked=True)
            )
            if job is None:
                break
            envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id))
            assert envelope is not None
            envelope.lease_owner = token
            envelope.lease_expires_at = now() + timedelta(seconds=30)
            envelope.attempts += 1
            original_context = parent(envelope.payload.get("telemetry"))
            org, job_id, terminal = job.organization_id, job.id, job.state == "failed"
            diagnostic = {
                "error_code": job.error_code,
                "trace_id": job.trace_id,
                "attempts": job.attempts,
                "retryable": job.retryable,
            }
        try:
            with stage(
                "messaging.publish",
                {
                    "messaging.system": "rabbitmq",
                    "messaging.operation.name": "publish",
                    "messaging.destination.name": "terminal" if terminal else "invoice",
                },
                context=original_context,
            ):
                (
                    sender
                    or (
                        lambda org, key, failed, detail: publish(settings, org, key, failed, detail)
                    )
                )(org, job_id, terminal, diagnostic)
        except Exception:
            # Publication is uncertain on connection loss. Re-publish after backoff;
            # durable execution keys suppress any duplicate result.
            with Session(engine) as session, session.begin():
                bounded_session(session)
                envelope = session.scalar(
                    select(OutboxEvent).where(OutboxEvent.job_id == job_id).with_for_update()
                )
                if envelope is not None and envelope.lease_owner == token:
                    envelope.lease_owner = envelope.lease_expires_at = None
                    envelope.available_at = now() + timedelta(seconds=5)
            continue
        with Session(engine) as session, session.begin():
            bounded_session(session)
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job_id))
            if job is not None and envelope is not None and envelope.lease_owner == token:
                envelope.published_at = now()
                envelope.lease_owner = envelope.lease_expires_at = None
                if job.state == "pending_dispatch" and not terminal:
                    job.state = "queued"
                    if job.stage != "retrying":
                        job.stage = "queued"
                    job.updated_at, job.version = now(), job.version + 1
                    event(session, job, "job_dispatch_confirmed")
            count += 1
    return count


class RecoveryService(bootsteps.StartStopStep):  # type: ignore[misc]
    requires = {"celery.worker.components:Pool"}

    def __init__(self, worker: Any, **kwargs: Any) -> None:
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, worker: Any) -> None:
        if not get_settings().worker_enabled:
            return
        initialize("reconcileiq-worker")
        metric("riq.worker.starts")
        self.thread = threading.Thread(target=self.run, name="durable-outbox", daemon=True)
        self.thread.start()

    def run(self) -> None:
        settings = get_settings()
        engine = build_engine(settings)
        topology_ready = False
        try:
            while not self.stop_event.is_set():
                metric("riq.worker.heartbeat")
                try:
                    if not topology_ready:
                        with Connection(
                            settings.broker_url.get_secret_value(),
                            connect_timeout=3,
                            transport_options={"read_timeout": 5, "write_timeout": 5},
                        ) as broker:
                            DEAD_QUEUE(broker.default_channel).declare()
                        topology_ready = True
                    dispatch_once(engine, settings)
                except Exception:
                    topology_ready = False
                    logging.getLogger(__name__).warning(
                        "Dispatch dependency unavailable; recovery will retry"
                    )
                self.stop_event.wait(settings.dispatch_interval_seconds)
        finally:
            engine.dispose()

    def stop(self, worker: Any) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)

    terminate = stop
