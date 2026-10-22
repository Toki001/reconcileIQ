"""Authorized local failure injection using newly admitted synthetic PDFs only.

Temporarily interrupts only ReconcileIQ's worker/RabbitMQ services. Never resets a
database or deletes documents. All faults are restored in finally blocks.
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from kombu import Connection, Queue
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import AuditEvent, Invoice, Job, JobExecution, utcnow
from reconcileiq_database.testing import local_postgres_url
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session


def main() -> None:
    settings = get_settings()
    assert settings.environment == "local"
    target = local_postgres_url()
    assert target.host in {"127.0.0.1", "localhost"} and target.port == 15432
    engine = create_engine(target)
    prefix = "m04-" + uuid4().hex
    data = Path("tests/fixtures/financial/invoice-0002.pdf").read_bytes()
    results: dict[str, Any] = {}

    def call(
        path: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        key: str | None = None,
        role: str = "admin",
    ) -> tuple[int, dict[str, Any]]:
        token = getattr(settings, role + "_token").get_secret_value()
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = prefix + "-" + key
        request = Request(
            "http://127.0.0.1:8000" + path,
            method=method,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def service(action: str, name: str) -> None:
        command = ["docker", "compose", action, name]
        if action == "start" and name == "worker":
            command = ["docker", "compose", "up", "-d", "--no-deps", "--no-build", name]
        completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
        if completed.returncode:
            raise RuntimeError("Local test service control failed")

    def submit(label: str) -> tuple[str, str]:
        code, grant = call(
            "/v1/invoices/upload-requests",
            "POST",
            {
                "filename": prefix + ".pdf",
                "content_type": "application/pdf",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
            key=label + "-reserve",
        )
        assert code == 201
        with urlopen(
            Request(
                grant["upload_url"], data=data, method="PUT", headers=grant["required_headers"]
            ),
            timeout=15,
        ) as response:
            assert response.status == 200
        code, accepted = call(
            f"/v1/invoices/{grant['invoice_id']}/upload-complete",
            "POST",
            {"expected_version": 1},
            key=label + "-complete",
        )
        assert code == 202
        return grant["invoice_id"], accepted["job_id"]

    def wait(job_id: str, state: str, timeout: int = 120) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code, job = call("/v1/jobs/" + job_id, role="auditor")
            assert code == 200
            if job["state"] == state:
                return job
            if job["state"] == "failed" and state != "failed":
                raise AssertionError("Unexpected synthetic job failure: " + str(job["error_code"]))
            time.sleep(0.25)
        raise AssertionError("Synthetic job did not reach the expected state")

    def financial_digest() -> str:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id,currency,amount_minor,status,version "
                    "FROM invoices WHERE organization_id='synthetic-finance' ORDER BY id"
                )
            ).all()
            assert len(rows) == 200
            return hashlib.sha256(
                json.dumps([list(row) for row in rows], default=str).encode()
            ).hexdigest()

    before = financial_digest()
    try:
        # The API commits admission without contacting RabbitMQ, even during outage.
        service("pause", "rabbitmq")
        try:
            invoice_id, job_id = submit("broker-outage")
            started = time.monotonic()
            assert call("/health/live")[0] == 200
            assert call("/v1/processing-jobs", role="auditor")[0] == 200
            assert time.monotonic() - started < 3
            time.sleep(3)
            assert call("/v1/jobs/" + job_id)[1]["state"] == "pending_dispatch"
        finally:
            service("unpause", "rabbitmq")
        wait(job_id, "succeeded")
        results["broker_outage_and_recovery"] = "passed"

        # Abrupt worker loss with a durable admitted job; restart must dispatch it.
        service("pause", "rabbitmq")
        try:
            _, restart_job = submit("worker-crash")
            service("kill", "worker")
        finally:
            service("unpause", "rabbitmq")
            service("start", "worker")
        wait(restart_job, "succeeded")
        results["worker_crash_restart_and_pending_recovery"] = "passed"

        # Point a new test invoice at a missing key without deleting any real object.
        service("pause", "worker")
        try:
            missing_invoice, missing_job = submit("missing-object")
            with Session(engine) as session, session.begin():
                invoice = session.get(Invoice, missing_invoice)
                assert invoice is not None and invoice.document_key
                original_key = invoice.document_key
                invoice.document_key = "demo/sealed/" + uuid4().hex + ".pdf"
                invoice.version += 1
                session.add(
                    AuditEvent(
                        id=uuid4().hex,
                        organization_id="demo",
                        entity_id=invoice.id,
                        actor="synthetic-m04-verification",
                        action="synthetic_missing_object_injected",
                        detail="New synthetic probe only; original object retained.",
                        occurred_at=utcnow(),
                    )
                )
        finally:
            service("unpause", "worker")
        failed = wait(missing_job, "failed", timeout=180)
        assert (
            failed["attempts"] == 5
            and failed["error_code"] == "OBJECT_MISSING"
            and failed["retryable"]
        )
        results["five_attempt_storage_failure"] = "passed"

        # Wait for explicit terminal-failure publication, independently of Celery reject routing.
        deadline = time.monotonic() + 20
        observed = False
        with Connection(settings.broker_url.get_secret_value(), connect_timeout=3) as broker:
            queue = Queue("reconcileiq.dead", channel=broker.default_channel)
            held = []
            try:
                while time.monotonic() < deadline:
                    message = queue.get(no_ack=False)
                    if message is None:
                        time.sleep(0.25)
                        continue
                    held.append(message)
                    body = message.payload
                    # Retain every terminal message; do not repeatedly requeue the queue's head.
                    if isinstance(body, dict) and body.get("job_id") == missing_job:
                        assert body["event"] == "job.terminal_failure" and body["attempts"] == 5
                        assert set(body) == {
                            "event",
                            "organization_id",
                            "job_id",
                            "error_code",
                            "trace_id",
                            "attempts",
                            "retryable",
                        }
                        observed = True
                        break
            finally:
                for message in held:
                    message.reject(requeue=True)
        assert observed, "Expected terminal failure was not observed in the durable DLQ"
        results["explicit_dead_letter_publication"] = "passed"

        with Session(engine) as session, session.begin():
            invoice = session.get(Invoice, missing_invoice)
            assert invoice is not None
            invoice.document_key = original_key
            invoice.version += 1
            session.add(
                AuditEvent(
                    id=uuid4().hex,
                    organization_id="demo",
                    entity_id=invoice.id,
                    actor="synthetic-m04-verification",
                    action="synthetic_object_reference_restored",
                    detail="Original synthetic sealed object restored after the test failure.",
                    occurred_at=utcnow(),
                )
            )
        body = {
            "expected_version": failed["version"],
            "reason": "Synthetic object reference restored after fault injection.",
        }
        assert (
            call(
                "/v1/jobs/" + missing_job + "/replay",
                "POST",
                body,
                key="replay-denied",
                role="auditor",
            )[0]
            == 403
        )
        code, replay = call("/v1/jobs/" + missing_job + "/replay", "POST", body, key="replay")
        assert code == 202
        assert call("/v1/jobs/" + missing_job + "/replay", "POST", body, key="replay") == (
            code,
            replay,
        )
        wait(replay["job_id"], "succeeded")
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(JobExecution)
                    .where(JobExecution.job_id == replay["job_id"])
                )
                == 1
            )
            original = session.get(Job, missing_job)
            assert original is not None and original.state == "failed" and original.attempts == 5
        assert financial_digest() == before
        results["authorized_idempotent_replay_and_financial_integrity"] = "passed"
        Path(".cache/m04-processing-smoke-result.json").write_text(
            json.dumps(results, indent=2) + "\n"
        )
        print("Live processing smoke passed:", ", ".join(results))
    finally:
        for name in ("rabbitmq", "worker"):
            # Recover only these scoped local services if an assertion interrupted a fault.
            subprocess.run(["docker", "compose", "unpause", name], capture_output=True, check=False)
            command = (
                ["docker", "compose", "up", "-d", "--no-deps", "--no-build", name]
                if name == "worker"
                else ["docker", "compose", "start", name]
            )
            subprocess.run(command, capture_output=True, check=False)
        engine.dispose()


if __name__ == "__main__":
    main()
