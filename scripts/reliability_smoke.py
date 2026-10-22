"""Bounded local M05 workload and actual MinIO interruption; synthetic uploads only."""

import hashlib
import json
import math
import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import Invoice, Job, JobAttempt, JobExecution
from reconcileiq_database.testing import local_postgres_url
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session


def main() -> None:
    settings = get_settings()
    target = local_postgres_url()
    assert settings.environment == "local" and target.host == "127.0.0.1" and target.port == 15432
    engine = create_engine(target)
    prefix = "m05-" + uuid4().hex
    active_faults: set[str] = set()
    api = httpx.Client(base_url="http://127.0.0.1:8000", timeout=15)

    def call(path: str, body: dict[str, Any] | None = None, key: str = "") -> dict[str, Any]:
        headers = {"Authorization": "Bearer " + settings.admin_token.get_secret_value()}
        if key:
            headers["Idempotency-Key"] = prefix + key
        response = (
            api.post(path, json=body, headers=headers)
            if body is not None
            else api.get(path, headers=headers)
        )
        assert response.status_code in {200, 201, 202}, "Local API probe failed"
        result: dict[str, Any] = response.json()
        return result

    def service(action: str, name: str) -> None:
        result = subprocess.run(
            ["docker", "compose", action, name], capture_output=True, check=False, timeout=20
        )
        assert result.returncode == 0, "Scoped service control failed"
        if action == "pause":
            active_faults.add(name)
        else:
            active_faults.discard(name)

    def submit(number: int) -> tuple[str, str]:
        data = Path(f"tests/fixtures/financial/invoice-{number:04}.pdf").read_bytes()
        grant = call(
            "/v1/invoices/upload-requests",
            {
                "filename": f"{prefix}-{number}.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
            key=f"reserve-{number}",
        )
        with httpx.Client(timeout=15) as store:
            response = store.put(
                grant["upload_url"], content=data, headers=grant["required_headers"]
            )
            assert response.status_code == 200, "Synthetic object upload failed"
        accepted = call(
            f"/v1/invoices/{grant['invoice_id']}/upload-complete",
            {"expected_version": 1},
            key=f"complete-{number}",
        )
        return grant["invoice_id"], accepted["job_id"]

    def wait(job_id: str, terminal: bool = True, timeout: int = 90) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = call("/v1/jobs/" + job_id)
            if (terminal and row["state"] in {"succeeded", "failed"}) or (
                not terminal and row["error_code"] == "STORAGE_UNAVAILABLE"
            ):
                return row
            time.sleep(0.2)
        raise AssertionError("Synthetic probe did not reach its expected state")

    def financial_digest() -> str:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id,currency,amount_minor,status,version,validated_extraction_id "
                    "FROM invoices WHERE organization_id='synthetic-finance' ORDER BY id"
                )
            ).all()
            assert len(rows) == 200
            return hashlib.sha256(
                json.dumps([list(row) for row in rows], default=str).encode()
            ).hexdigest()

    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    def distribution(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        return {
            "p50_seconds": round(ordered[math.ceil(len(ordered) * 0.5) - 1], 4),
            "p95_seconds": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 4),
            "max_seconds": round(max(ordered), 4),
        }

    before = financial_digest()
    try:
        # Admit to a stopped worker, then interrupt MinIO before allowing the read.
        service("pause", "worker")
        try:
            _, fault_job = submit(20)
            service("pause", "minio")
        finally:
            service("unpause", "worker")
        interrupted = wait(fault_job, terminal=False, timeout=45)
        assert interrupted["retryable"] and interrupted["attempts"] >= 1
        service("unpause", "minio")
        recovery_started = time.monotonic()
        recovered = wait(fault_job)
        recovery_seconds = time.monotonic() - recovery_started
        assert recovered["state"] == "succeeded" and 2 <= recovered["attempts"] <= 5
        with Session(engine) as session:
            attempts = session.scalars(
                select(JobAttempt).where(JobAttempt.job_id == fault_job).order_by(JobAttempt.number)
            ).all()
            assert attempts[0].state == "failed" and attempts[0].error_code == "STORAGE_UNAVAILABLE"
            assert attempts[-1].state == "succeeded"
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(JobExecution)
                    .where(JobExecution.job_id == fault_job)
                )
                == 1
            )

        started = time.monotonic()
        records = [submit(number) for number in range(1, 13)]
        for _, job_id in records:
            assert wait(job_id)["state"] == "succeeded"
        elapsed = time.monotonic() - started
        queue_wait, processing, end_to_end = [], [], []
        job_ids = [job_id for _, job_id in records]
        with Session(engine) as session:
            jobs = session.scalars(select(Job).where(Job.id.in_(job_ids))).all()
            attempts = session.scalars(
                select(JobAttempt).where(JobAttempt.job_id.in_(job_ids))
            ).all()
            assert len(jobs) == len(attempts) == 12
            for job in jobs:
                attempt = next(row for row in attempts if row.job_id == job.id)
                assert attempt.finished_at is not None and job.attempts == 1
                queue_wait.append((utc(attempt.started_at) - utc(job.created_at)).total_seconds())
                processing.append(
                    (utc(attempt.finished_at) - utc(attempt.started_at)).total_seconds()
                )
                end_to_end.append((utc(attempt.finished_at) - utc(job.created_at)).total_seconds())
            for invoice_id, job_id in records:
                invoice = session.get(Invoice, invoice_id)
                assert invoice is not None and invoice.status == "extracted"
                assert invoice.amount_minor is None and invoice.currency is None
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(JobExecution)
                        .where(JobExecution.job_id == job_id)
                    )
                    == 1
                )
        assert financial_digest() == before
        report = {
            "host": platform.platform(),
            "host_cpu_count": os.cpu_count(),
            "worker_processes": settings.worker_concurrency,
            "prefetch": 1,
            "workload": (
                "12 sequential API admissions, digital synthetic PDFs (1–3 pages), "
                "4 concurrent workers"
            ),
            "succeeded": 12,
            "failed": 0,
            "workload_retries": 0,
            "wall_seconds": round(elapsed, 4),
            "documents_per_minute": round(12 * 60 / elapsed, 2),
            "queue_wait": distribution(queue_wait),
            "worker_processing": distribution(processing),
            "end_to_end": distribution(end_to_end),
            "minio_interruption": {
                "outcome": "recovered",
                "attempts": recovered["attempts"],
                "after_restore_seconds": round(recovery_seconds, 4),
            },
            "financial_fixture_digest_unchanged": True,
        }
        Path(".cache/m05-workload.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        for name in list(active_faults):
            subprocess.run(
                ["docker", "compose", "unpause", name], capture_output=True, check=False, timeout=20
            )
        api.close()
        engine.dispose()


if __name__ == "__main__":
    main()
