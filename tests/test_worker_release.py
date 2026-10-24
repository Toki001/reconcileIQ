"""Real warm shutdown and image replacement, isolated from the retained worker/queues."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from celery import Celery
from kombu import Connection, Queue
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import (
    ExtractionVersion,
    InvoiceLine,
    Job,
    JobAttempt,
    JobExecution,
)
from reconcileiq_database.testing import postgres_schema
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_live_services import eventually
from test_processing import admit

pytestmark = pytest.mark.skipif(
    os.environ.get("RECONCILE_TEST_SERVICES") != "1",
    reason="Opt-in private PostgreSQL/RabbitMQ/Linux worker release test",
)


def test_warm_shutdown_preserves_inflight_and_pending_across_image_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with postgres_schema() as (database, url):
        monkeypatch.setenv("RECONCILE_DATABASE_URL", url.render_as_string(hide_password=False))
        monkeypatch.setenv("RECONCILE_ENVIRONMENT", "test")
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        settings = get_settings()
        broker_url = settings.broker_url.get_secret_value()
        queue_name = "riq.m07." + uuid4().hex
        container = queue_name.replace(".", "-")
        queue = Queue(queue_name, durable=True)
        producer = Celery("release-probe", broker=broker_url)
        broker = urlsplit(broker_url)
        values = {
            "RECONCILE_ENVIRONMENT": "test",
            "RECONCILE_AI_ENABLED": "false",
            "RECONCILE_TEST_QUEUE": queue_name,
            "RECONCILE_WORKER_ENABLED": "false",
            "RECONCILE_INFRASTRUCTURE_REQUIRED": "false",
            "PYTHONPATH": "tests/support",
            "RECONCILE_DATABASE_URL": url.set(host="postgres", port=5432).render_as_string(
                hide_password=False
            ),
            "RECONCILE_BROKER_URL": urlunsplit(
                broker._replace(netloc=broker.netloc.rsplit("@", 1)[0] + "@rabbitmq:5672")
            ),
        }
        env_file = tmp_path / "private.env"
        env_file.touch(mode=0o600)
        env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        log_file = tmp_path / "worker.log"
        log_file.touch(mode=0o600)
        worker: subprocess.Popen[bytes] | None = None

        def start(image: str) -> subprocess.Popen[bytes]:
            with log_file.open("ab") as log:
                return subprocess.Popen(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--init",
                        "--name",
                        container,
                        "--network",
                        os.environ.get("RECONCILE_TEST_NETWORK", "reconcileiq_default"),
                        "--env-file",
                        str(env_file),
                        "--volume",
                        f"{Path('tests').absolute()}:/app/tests:ro",
                        image,
                        "celery",
                        "-A",
                        "celery_probe:app",
                        "worker",
                        "--pool=prefork",
                        "--concurrency=1",
                        "--loglevel=WARNING",
                        "--without-gossip",
                        "--without-mingle",
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )

        def state(job_id: str, expected: str) -> bool:
            with Session(database) as session:
                job = session.get(Job, job_id)
                return job is not None and job.state == expected

        def stop() -> None:
            subprocess.run(
                ["docker", "stop", "--time", "90", container],
                capture_output=True,
                check=True,
                timeout=100,
            )
            assert worker is not None
            assert worker.wait(timeout=10) == 0

        try:
            with Connection(broker_url, connect_timeout=3) as connection:
                queue(connection.default_channel).declare()
                _, first = admit(database)
                _, second = admit(database)
                producer.send_task("reliability.process", args=[first, "warm"], queue=queue_name)
                producer.send_task(
                    "reliability.process", args=[second, "success"], queue=queue_name
                )
                worker = start(
                    os.environ.get("RECONCILE_TEST_PYTHON_IMAGE", "reconcileiq-python:local")
                )
                eventually(lambda: state(first, "running"))
                stop()  # Real SIGTERM while the child is sleeping, not a mocked protocol.
                assert state(first, "succeeded")
                assert state(second, "pending_dispatch")
                assert (
                    queue(connection.default_channel).queue_declare(passive=True).message_count == 1
                )
                worker = start(
                    os.environ.get("RECONCILE_TEST_WORKER_IMAGE", "reconcileiq-python:local")
                )
                for _ in range(3):
                    producer.send_task(
                        "reliability.process", args=[second, "success"], queue=queue_name
                    )
                eventually(lambda: state(second, "succeeded"))
                eventually(
                    lambda: (
                        queue(connection.default_channel).queue_declare(passive=True).message_count
                        == 0
                    )
                )
                stop()  # Waits for any last unacknowledged duplicate to finish as well.
                with Session(database) as session:
                    assert session.scalar(select(func.count()).select_from(JobExecution)) == 2
                    assert session.scalar(select(func.count()).select_from(ExtractionVersion)) == 2
                    assert session.scalar(select(func.count()).select_from(InvoiceLine)) == 0
                    attempts = session.scalars(select(JobAttempt)).all()
                    assert len(attempts) == 2 and all(row.state == "succeeded" for row in attempts)
                assert "Warm shutdown" in log_file.read_text()
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container], capture_output=True, check=False, timeout=20
            )
            if worker is not None:
                worker.wait(timeout=10)
            producer.close()
            with Connection(broker_url, connect_timeout=3) as connection:
                queue(connection.default_channel).delete()
            get_settings.cache_clear()
