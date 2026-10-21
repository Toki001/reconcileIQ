"""Publish duplicate probes through RabbitMQ and verify a single durable business effect."""

import time
from uuid import uuid4

from reconcileiq_database.config import get_settings
from reconcileiq_database.models import AuditEvent, JobExecution
from reconcileiq_database.session import build_engine
from reconcileiq_worker.tasks import probe_task
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def main() -> None:
    key = f"bootstrap-{uuid4().hex}"
    engine = build_engine(get_settings())
    try:
        for _ in range(2):
            probe_task.delay(key)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with Session(engine) as session:
                if session.get(JobExecution, key):
                    break
            time.sleep(0.25)
        else:
            raise SystemExit(
                "Worker probe timed out. Inspect worker state and broker availability."
            )
        # Send a third copy and wait for an ordering barrier in a second key. The
        # compose smoke worker uses concurrency=1 to make the barrier conclusive.
        probe_task.delay(key)
        barrier = f"barrier-{uuid4().hex}"
        probe_task.delay(barrier)
        while time.monotonic() < deadline:
            with Session(engine) as session:
                if session.get(JobExecution, barrier):
                    count = session.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(
                            AuditEvent.entity_id == key,
                        )
                    )
                    assert count == 1, "Repeated delivery duplicated the audit effect"
                    print("RabbitMQ worker probe passed: three deliveries, one durable effect.")
                    return
            time.sleep(0.25)
        raise SystemExit("Worker ordering barrier timed out.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
