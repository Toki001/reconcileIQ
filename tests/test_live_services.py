"""Opt-in real RabbitMQ/Celery/MinIO tests; private queues, schemas and object buckets."""

import hashlib
import os
import subprocess
import time
from collections.abc import Callable
from datetime import timedelta
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from amqp.exceptions import MessageNacked
from celery import Celery
from kombu import Connection, Exchange, Queue
from reconcileiq_api.storage import MinioStorage
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import (
    ExtractedField,
    ExtractionVersion,
    Job,
    JobAttempt,
    JobExecution,
)
from reconcileiq_worker import dispatch
from reconcileiq_worker.dispatch import recover
from reconcileiq_worker.processing import now, process_job
from reconcileiq_worker.providers import MinioReader, ProcessingError
from sqlalchemy import Engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_processing import PDF, MemoryReader, admit, due

pytestmark = pytest.mark.skipif(
    os.environ.get("RECONCILE_TEST_SERVICES") != "1",
    reason="Set RECONCILE_TEST_SERVICES=1 for real local service tests",
)


def eventually(check: Callable[[], bool], timeout: int = 35) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError("Private integration probe did not reach its expected persisted state")


def private_topology(monkeypatch: pytest.MonkeyPatch, capacity: int = 100) -> tuple[Queue, Queue]:
    prefix = "riq.m05." + uuid4().hex
    tasks = Exchange(prefix + ".tasks", type="direct", durable=True)
    dead = Exchange(prefix + ".dead", type="direct", durable=True)
    invoice = Queue(
        prefix + ".invoice",
        exchange=tasks,
        routing_key="invoice",
        durable=True,
        queue_arguments={"x-max-length": capacity, "x-overflow": "reject-publish"},
    )
    failures = Queue(prefix + ".dead", exchange=dead, routing_key="terminal", durable=True)
    for name, value in (
        ("TASK_EXCHANGE", tasks),
        ("DEAD_EXCHANGE", dead),
        ("INVOICE_QUEUE", invoice),
        ("DEAD_QUEUE", failures),
    ):
        monkeypatch.setattr(dispatch, name, value)
    return invoice, failures


def cleanup_topology(broker_url: str, queues: tuple[Queue, Queue]) -> None:
    with Connection(broker_url, connect_timeout=3) as connection:
        for queue in queues:
            queue(connection.default_channel).delete()
            queue.exchange(connection.default_channel).delete()


def test_real_publisher_confirm_rejection_and_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    queues = private_topology(monkeypatch, capacity=1)
    try:
        dispatch.publish(settings, "demo", "one", False, {})
        with pytest.raises(MessageNacked):
            dispatch.publish(settings, "demo", "two", False, {})
        dispatch.publish(
            settings,
            "demo",
            "failed-job",
            True,
            {"error_code": "INVALID_PDF", "attempts": 1, "retryable": False, "trace_id": "trace"},
        )
        with Connection(settings.broker_url.get_secret_value(), connect_timeout=3) as connection:
            first = queues[0](connection.default_channel).get(no_ack=False)
            assert first is not None and first.payload[0] == ["demo", "one"]
            assert first.properties["delivery_mode"] == 2
            first.ack()
            terminal = queues[1](connection.default_channel).get(no_ack=False)
            assert terminal is not None
            assert terminal.payload == {
                "event": "job.terminal_failure",
                "organization_id": "demo",
                "job_id": "failed-job",
                "error_code": "INVALID_PDF",
                "attempts": 1,
                "retryable": False,
                "trace_id": "trace",
            }
            terminal.ack()
    finally:
        cleanup_topology(settings.broker_url.get_secret_value(), queues)


def test_real_recovery_thread_publishes_pending_job(
    finance_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if finance_engine.dialect.name != "postgresql":
        pytest.skip("Recovery thread integration uses isolated PostgreSQL")
    settings = get_settings().model_copy(
        update={"worker_enabled": True, "dispatch_interval_seconds": 1}
    )
    monkeypatch.setattr(dispatch, "get_settings", lambda: settings)
    queues = private_topology(monkeypatch)
    _, job_id = admit(finance_engine)
    service = dispatch.RecoveryService(None)
    try:
        service.start(None)

        def queued() -> bool:
            with Session(finance_engine) as session:
                job = session.get(Job, job_id)
                return job is not None and job.state == "queued"

        eventually(queued)
        with Connection(settings.broker_url.get_secret_value(), connect_timeout=3) as connection:
            message = queues[0](connection.default_channel).get(no_ack=False)
            assert message is not None and message.payload[0] == ["demo", job_id]
            process_job(finance_engine, settings, "demo", job_id, MemoryReader(PDF))
            message.ack()
    finally:
        service.stop(None)
        assert service.thread is not None and not service.thread.is_alive()
        cleanup_topology(settings.broker_url.get_secret_value(), queues)


def test_real_minio_seal_overwrite_missing_and_checksum() -> None:
    settings = get_settings().model_copy(update={"minio_bucket": "riq-m05-" + uuid4().hex})
    assert settings.environment in {"local", "test"}
    assert settings.minio_endpoint.host in {"127.0.0.1", "localhost"}
    storage = MinioStorage(settings)
    sdk = storage.client()
    sdk.make_bucket(settings.minio_bucket)
    key = "demo/uploads/synthetic.pdf"
    digest = hashlib.sha256(PDF).hexdigest()
    try:
        sdk.put_object(
            settings.minio_bucket, key, BytesIO(PDF), len(PDF), content_type="application/pdf"
        )
        sealed = storage.verify_and_seal(key, "demo", len(PDF), digest)
        # Reusing the upload capability cannot replace the verified worker input.
        sdk.put_object(
            settings.minio_bucket, key, BytesIO(b"changed"), 7, content_type="application/pdf"
        )
        reader = MinioReader(settings)
        assert reader.read("demo", sealed, len(PDF), digest) == PDF
        with pytest.raises(ProcessingError) as mismatch:
            reader.read("demo", sealed, len(PDF), "0" * 64)
        assert mismatch.value.code == "CHECKSUM_MISMATCH"
        with pytest.raises(ProcessingError) as missing:
            reader.read("demo", "demo/sealed/" + uuid4().hex + ".pdf", len(PDF), digest)
        assert missing.value.code == "OBJECT_MISSING" and missing.value.retryable
    finally:
        # Only this uniquely named, newly created test bucket is disposable.
        for item in sdk.list_objects(settings.minio_bucket, recursive=True):
            assert item.object_name
            sdk.remove_object(settings.minio_bucket, item.object_name)
        sdk.remove_bucket(settings.minio_bucket)


def test_real_prefork_timeout_crash_and_restart(finance_engine: Engine, tmp_path: Path) -> None:
    if finance_engine.dialect.name != "postgresql":
        pytest.skip("Authoritative prefork fault verification requires isolated PostgreSQL")
    settings = get_settings()
    queue_name = "riq.m05." + uuid4().hex
    queue = Queue(queue_name, durable=True)
    broker_url = settings.broker_url.get_secret_value()
    producer = Celery("private-probe-client", broker=broker_url)
    log_path = tmp_path / "worker.log"
    log_path.touch(mode=0o600)
    container_name = queue_name.replace(".", "-")
    broker = urlsplit(broker_url)
    private_environment = {
        "RECONCILE_ENVIRONMENT": "test",
        "RECONCILE_TEST_QUEUE": queue_name,
        "RECONCILE_WORKER_ENABLED": "false",
        "RECONCILE_INFRASTRUCTURE_REQUIRED": "false",
        "PYTHONPATH": "tests/support",
        "RECONCILE_DATABASE_URL": make_url(settings.database_url.get_secret_value())
        .set(host="postgres", port=5432)
        .render_as_string(hide_password=False),
        "RECONCILE_BROKER_URL": urlunsplit(
            broker._replace(netloc=broker.netloc.rsplit("@", 1)[0] + "@rabbitmq:5672")
        ),
    }
    env_path = tmp_path / "worker.env"
    env_path.touch(mode=0o600)
    env_path.write_text("".join(f"{key}={value}\n" for key, value in private_environment.items()))
    worker: subprocess.Popen[bytes] | None = None

    def start(log: Any) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--init",
                "--name",
                container_name,
                "--network",
                os.environ.get("RECONCILE_TEST_NETWORK", "reconcileiq_default"),
                "--env-file",
                str(env_path),
                "--volume",
                f"{Path('tests').absolute()}:/app/tests:ro",
                "--volume",
                f"{Path('services/worker/src').absolute()}:/app/services/worker/src:ro",
                os.environ.get("RECONCILE_TEST_PYTHON_IMAGE", "reconcileiq-python:local"),
                "celery",
                "-A",
                "celery_probe:app",
                "worker",
                "--pool=prefork",
                "--concurrency=2",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
                "--hostname=" + queue_name,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def stop() -> None:
        subprocess.run(
            ["docker", "stop", "--time", "10", container_name],
            capture_output=True,
            check=False,
            timeout=20,
        )
        if worker:
            worker.wait(timeout=10)

    def state(job_id: str, expected: str) -> bool:
        with Session(finance_engine) as session:
            job = session.get(Job, job_id)
            return job is not None and job.state == expected

    try:
        with Connection(broker_url, connect_timeout=3) as connection, log_path.open("ab") as log:
            queue(connection.default_channel).declare()
            worker = start(log)
            # A real soft signal and hard child termination, using shorter test-only deadlines.
            for mode in ("soft", "hard", "crash"):
                invoice_id, job_id = admit(finance_engine)
                producer.send_task(
                    "reliability.process",
                    args=[job_id, mode],
                    queue=queue_name,
                    soft_time_limit=1,
                    time_limit=3,
                )
                eventually(partial(state, job_id, "running"))
                if mode == "soft":
                    eventually(partial(state, job_id, "pending_dispatch"))
                    with Session(finance_engine) as session:
                        assert session.get(Job, job_id).error_code == "PROCESSING_TIMEOUT"  # type: ignore[union-attr]
                else:

                    def child_lost(kind: str = mode) -> bool:
                        content = log_path.read_text()
                        return ("Hard time limit" if kind == "hard" else "exitcode 91") in content

                    eventually(child_lost)
                    with Session(finance_engine) as session, session.begin():
                        job = session.get(Job, job_id)
                        assert job is not None and job.state == "running" and job.lease_owner
                        job.lease_expires_at = now() - timedelta(seconds=1)
                    recover(finance_engine, settings)
                with Session(finance_engine) as session:
                    assert (
                        session.scalar(
                            select(func.count())
                            .select_from(ExtractionVersion)
                            .where(ExtractionVersion.invoice_id == invoice_id)
                        )
                        == 0
                    )
                # Restart the private worker with the same database and durable queue.
                stop()
                worker = start(log)
                due(finance_engine, job_id)
                for _ in range(3):
                    producer.send_task(
                        "reliability.process", args=[job_id, "success"], queue=queue_name
                    )
                eventually(partial(state, job_id, "succeeded"))
                with Session(finance_engine) as session:
                    assert (
                        session.scalar(
                            select(func.count())
                            .select_from(JobExecution)
                            .where(JobExecution.job_id == job_id)
                        )
                        == 1
                    )
                    attempts = session.scalars(
                        select(JobAttempt)
                        .where(JobAttempt.job_id == job_id)
                        .order_by(JobAttempt.number)
                    ).all()
                    assert [(row.number, row.state) for row in attempts] == [
                        (1, "failed"),
                        (2, "succeeded"),
                    ]
                    assert (
                        session.scalar(
                            select(func.count())
                            .select_from(ExtractedField)
                            .where(ExtractedField.invoice_id == invoice_id)
                        )
                        == 1
                    )
    finally:
        if worker and worker.poll() is None:
            try:
                stop()
            finally:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
        producer.close()
        with Connection(broker_url, connect_timeout=3) as connection:
            queue(connection.default_channel).delete()
    contents = log_path.read_text()
    for value in (
        settings.database_url,
        settings.broker_url,
        settings.admin_token,
        settings.minio_secret_key,
    ):
        secret = value.get_secret_value()
        if secret:
            assert secret not in contents
