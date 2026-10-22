"""Test-only providers on a private queue. Never imported by the application worker."""

import os
import time
from contextlib import suppress
from pathlib import Path

from billiard.exceptions import SoftTimeLimitExceeded
from celery import Celery
from kombu import Queue
from reconcileiq_database.config import get_settings
from reconcileiq_database.session import build_engine
from reconcileiq_worker.app import celery_app
from reconcileiq_worker.processing import process_job
from reconcileiq_worker.providers import DigitalPdfProvider, DocumentText

app = Celery("reliability-probe", broker=get_settings().broker_url.get_secret_value())
app.conf.update(
    {
        key: celery_app.conf[key]
        for key in (
            "task_acks_late",
            "task_reject_on_worker_lost",
            "worker_prefetch_multiplier",
            "task_acks_on_failure_or_timeout",
            "worker_cancel_long_running_tasks_on_connection_loss",
            "task_soft_time_limit",
            "task_time_limit",
            "control_queue_exclusive",
            "event_queue_exclusive",
            "task_serializer",
            "accept_content",
            "broker_transport_options",
        )
    }
)
app.conf.update(
    task_queues=(Queue(os.environ["RECONCILE_TEST_QUEUE"], durable=True),),
    task_default_queue=os.environ["RECONCILE_TEST_QUEUE"],
    task_ignore_result=True,
    worker_enable_remote_control=False,
    broker_heartbeat=10,
)


@app.task(name="reliability.process")  # type: ignore[untyped-decorator]
def probe(job_id: str, mode: str) -> None:
    class Reader:
        def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
            return Path("tests/fixtures/financial/invoice-0001.pdf").read_bytes()

    class Provider:
        def extract(self, data: bytes) -> DocumentText:
            if mode == "crash":
                os._exit(91)  # Actual prefork child loss after the lease/stage committed.
            if mode == "soft":
                time.sleep(30)
            if mode == "hard":
                while True:
                    with suppress(SoftTimeLimitExceeded):
                        time.sleep(30)
            return DigitalPdfProvider().extract(data)

    engine = build_engine(get_settings())
    try:
        process_job(engine, get_settings(), "demo", job_id, Reader(), Provider())
    finally:
        engine.dispose()
