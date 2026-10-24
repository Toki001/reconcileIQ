"""Bounded real-service benchmarks. Hard refuse the retained development target."""

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from benchmark_env import environment
from reconcileiq_database.models import Job
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path.cwd()
RESULTS = ROOT / ".cache/m08/results"
K6 = "grafana/k6:2.3.0@sha256:9c2dee7f8ed74d317e4027c06a10f169b625638189de8d4555d0b3486a5aeb34"


def compose(env: dict[str, str], *args: str) -> None:
    if env.get("COMPOSE_PROJECT_NAME") != "reconcileiq-m08":
        raise ValueError("Wrong test project")
    result = subprocess.run(["docker", "compose", *args], env=env, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Isolated service control failed; diagnostics withheld")


class Probe:
    def __init__(self) -> None:
        self.env = environment()
        url = make_url(self.env["RECONCILE_DATABASE_URL"])
        assert url.host == "127.0.0.1" and url.port == 25432
        assert self.env["RECONCILE_TEST_API_URL"] == "http://127.0.0.1:28000"
        for service in ("api", "postgres"):
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "reconcileiq-m08-" + service + "-1",
                    "--format",
                    '{{ index .Config.Labels "com.docker.compose.project" }}',
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode or result.stdout.strip() != "reconcileiq-m08":
                raise RuntimeError("Isolated project unavailable; no benchmark traffic sent")
        self.engine = create_engine(url)
        self.client = httpx.Client(
            base_url=self.env["RECONCILE_TEST_API_URL"],
            timeout=15,
            headers={"Authorization": "Bearer " + self.env["RECONCILE_REVIEWER_TOKEN"]},
        )
        self.paused: set[str] = set()
        self.worker_controlled = False
        self.jobs: list[str] = []
        self.prefix = "m08-" + uuid4().hex[:10]
        RESULTS.mkdir(parents=True, exist_ok=True)

    def control(self, action: str, service: str) -> None:
        assert service in {
            "worker",
            "rabbitmq",
            "minio",
            "otel-collector",
            "prometheus",
            "tempo",
            "grafana",
        }
        name = "reconcileiq-m08-" + service + "-1"
        result = subprocess.run(
            [
                "docker",
                "inspect",
                name,
                "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}',
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "reconcileiq-m08"
        if service == "worker":
            self.worker_controlled = True
        compose(self.env, action, service)
        if action == "pause":
            self.paused.add(service)
        elif action == "unpause":
            self.paused.discard(service)

    def submit(self, data: bytes | None = None, *, trace_context: bool = True) -> tuple[str, str]:
        data = data or Path("tests/fixtures/financial/invoice-0003.pdf").read_bytes()
        key = self.prefix + "-" + uuid4().hex
        headers = {
            "Idempotency-Key": key,
        }
        if trace_context:
            headers["traceparent"] = "00-" + uuid4().hex + "-" + uuid4().hex[:16] + "-01"
        response = self.client.post(
            "/v1/invoices/upload-requests",
            headers=headers,
            json={
                "filename": "SYNTHETIC-m08.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
        assert response.status_code == 201, "Isolated upload reservation failed"
        invoice = response.json()["invoice_id"]
        response = self.client.put(
            "/v1/invoices/" + invoice + "/document",
            headers={"Content-Type": "application/pdf", **headers},
            content=data,
        )
        assert response.status_code == 204, "Isolated document upload failed"
        response = self.client.post(
            "/v1/invoices/" + invoice + "/upload-complete",
            headers={**headers, "Idempotency-Key": key + "-done"},
            json={"expected_version": 1},
        )
        assert response.status_code == 202, "Isolated admission failed"
        job = response.json()["job_id"]
        self.jobs.append(job)
        return invoice, job

    def wait(self, job: str, *, terminal: bool = True, timeout: float = 180) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            response = self.client.get("/v1/jobs/" + job)
            assert response.status_code == 200
            state = response.json()
            if terminal and state["state"] in {"succeeded", "failed"}:
                return dict(state)
            if not terminal and state.get("error_code") == "STORAGE_UNAVAILABLE":
                return dict(state)
            time.sleep(0.2)
        raise AssertionError("Isolated job deadline exceeded")

    def consistency(self) -> dict[str, Any]:
        with self.engine.connect() as c:
            result = {
                "jobs": c.scalar(text("SELECT count(*) FROM jobs")),
                "states": dict(
                    c.execute(text("SELECT state,count(*) FROM jobs GROUP BY state")).all()
                ),
                "unpublished_outbox": c.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE published_at IS NULL")
                ),
                "duplicate_effect_keys": c.scalar(
                    text(
                        "SELECT count(*) FROM (SELECT organization_id,key,count(*) FROM "
                        "job_execution_records GROUP BY organization_id,key HAVING count(*)>1) s"
                    )
                ),
                "duplicate_supplier_keys": c.scalar(
                    text(
                        "SELECT count(*) FROM (SELECT "
                        "organization_id,vendor_id,normalized_invoice_number FROM invoices WHERE "
                        "vendor_id IS NOT NULL AND normalized_invoice_number IS NOT NULL GROUP BY"
                        " organization_id,vendor_id,normalized_invoice_number HAVING count(*)>1) "
                        "s"
                    )
                ),
                "orphan_lines": c.scalar(
                    text(
                        "SELECT count(*) FROM invoice_lines l LEFT JOIN invoices i ON "
                        "l.invoice_id=i.id AND l.organization_id=i.organization_id WHERE i.id IS "
                        "NULL"
                    )
                ),
                "provider_usage": c.scalar(text("SELECT count(*) FROM provider_usage")),
            }
        return result

    def close(self) -> None:
        for service in list(self.paused):
            self.control("unpause", service)
        if not self.worker_controlled:
            self.client.close()
            self.engine.dispose()
            return
        compose(
            self.env,
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "worker",
        )
        # A paused process can retain an unhealthy healthcheck result until the
        # next interval. Compose --wait rejects that stale result immediately.
        # Resume/start the same container, then allow its real check to recover.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "reconcileiq-m08-worker-1",
                    "--format",
                    "{{.State.Health.Status}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Isolated worker did not regain health during cleanup")
        self.client.close()
        self.engine.dispose()


def k6(scenario: str, duration: str = "20s") -> dict[str, Any]:
    probe = Probe()
    samples = []
    stop = threading.Event()

    def resources() -> None:
        while not stop.is_set():
            result = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    "reconcileiq-m08-api-1",
                    "reconcileiq-m08-worker-1",
                    "reconcileiq-m08-postgres-1",
                    "reconcileiq-m08-rabbitmq-1",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            for line in result.stdout.splitlines():
                with contextlib.suppress(ValueError):
                    samples.append({"at": time.time(), **json.loads(line)})
            stop.wait(2)

    thread = threading.Thread(target=resources, daemon=True)
    thread.start()
    path = ROOT / ".cache/m08/k6-private.env"
    values = {
        "API_URL": "http://api:8000",
        "REVIEWER_TOKEN": probe.env["RECONCILE_REVIEWER_TOKEN"],
        "SCENARIO": scenario,
        "DURATION": duration,
        "RUN_TAG": probe.prefix,
    }
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in values.items()))
    start = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    try:
        if scenario == "burst":
            probe.control("pause", "worker")
        with (RESULTS / (scenario + ".log")).open("wb") as log:
            process = subprocess.Popen(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    "reconcileiq-m08-k6",
                    "--network",
                    "reconcileiq-m08_default",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--env-file",
                    str(path),
                    "-v",
                    f"{ROOT}/tests/load:/scripts:ro",
                    "-v",
                    f"{ROOT}/tests/fixtures/financial:/fixtures:ro",
                    "-v",
                    f"{RESULTS}:/results",
                    K6,
                    "run",
                    "/scripts/benchmark.js",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            if scenario == "burst":
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    with probe.engine.connect() as c:
                        active = c.scalar(
                            text(
                                "SELECT count(*) FROM jobs WHERE state IN "
                                "('pending_dispatch','queued','running')"
                            )
                        )
                    if active == 8:
                        break
                    time.sleep(0.1)
                assert active == 8, "Bounded burst did not fill configured capacity"
                # Confirm API reads remain available during backpressure.
                before = time.monotonic()
                assert probe.client.get("/v1/me").status_code == 200
                responsiveness = time.monotonic() - before
                time.sleep(2)
                probe.control("unpause", "worker")
            code = process.wait(timeout=270 if scenario == "sustained" else 150)
        assert code == 0, "k6 checks failed; inspect private summary/log"
        result = json.loads((RESULTS / (scenario + ".json")).read_text())
        summary = {
            "scenario": scenario,
            "duration_wall_seconds": time.monotonic() - start,
            "k6": result,
            "database": probe.consistency(),
            "resource_samples": samples,
        }
        if scenario == "burst":
            summary["api_read_during_backpressure_seconds"] = responsiveness
        (RESULTS / (scenario + "-evidence.json")).write_text(json.dumps(summary, indent=2))
        print(
            json.dumps(
                {
                    "scenario": scenario,
                    "k6_exit": code,
                    "wall_seconds": summary["duration_wall_seconds"],
                    "database": summary["database"],
                }
            )
        )
        return summary
    finally:
        stop.set()
        thread.join(timeout=20)
        if process and process.poll() is None:
            subprocess.run(
                ["docker", "rm", "-f", "reconcileiq-m08-k6"], capture_output=True, check=False
            )
            process.wait(timeout=15)
        path.unlink(missing_ok=True)
        probe.close()


def faults() -> None:
    probe = Probe()
    result = {}
    try:
        probe.control("pause", "worker")
        submitted = [probe.submit()[1] for _ in range(4)]
        probe.control("unpause", "worker")
        deadline = time.monotonic() + 30
        running = False
        while time.monotonic() < deadline:
            with Session(probe.engine) as session:
                running = bool(
                    session.scalar(
                        select(Job.id).where(Job.id.in_(submitted), Job.state == "running")
                    )
                )
            if running:
                break
            time.sleep(0.02)
        assert running, "Did not observe a real active worker task"
        probe.control("kill", "worker")
        restart = time.monotonic()
        compose(probe.env, "up", "-d", "--no-deps", "--no-build", "--wait", "worker")
        jobs = [probe.wait(key, timeout=170) for key in submitted]
        assert all(row["state"] == "succeeded" for row in jobs)
        assert any(row["attempts"] >= 2 for row in jobs), "No interrupted attempt was recovered"
        result["worker_hard_restart"] = {
            "recovery_seconds": time.monotonic() - restart,
            "jobs": len(jobs),
            "attempt_counts": [j["attempts"] for j in jobs],
            "reprocessed_jobs": sum(j["attempts"] >= 2 for j in jobs),
            "lease_accelerated": False,
            "scope": "natural 120-second lease; actual SIGKILL during active parsing",
        }
        probe.control("pause", "rabbitmq")
        try:
            _, job = probe.submit()
            time.sleep(2)
            assert probe.client.get("/v1/jobs/" + job).json()["state"] == "pending_dispatch"
        finally:
            probe.control("unpause", "rabbitmq")
        start = time.monotonic()
        assert probe.wait(job)["state"] == "succeeded"
        result["rabbitmq"] = {
            "recovery_after_restore_seconds": time.monotonic() - start,
            "outcome": "succeeded",
        }
        probe.control("pause", "worker")
        _, job = probe.submit()
        probe.control("pause", "minio")
        probe.control("unpause", "worker")
        try:
            state = probe.wait(job, terminal=False, timeout=40)
            assert state["retryable"]
        finally:
            probe.control("unpause", "minio")
        start = time.monotonic()
        recovered = probe.wait(job)
        assert recovered["state"] == "succeeded" and recovered["attempts"] >= 2
        result["minio"] = {
            "recovery_after_restore_seconds": time.monotonic() - start,
            "attempts": recovered["attempts"],
        }
        _, failed = probe.submit(b"%PDF-1.4\nSYNTHETIC INVALID TERMINAL PROBE\n")
        state = probe.wait(failed)
        assert state["state"] == "failed"
        result["terminal"] = {
            "state": state["state"],
            "error_code": state["error_code"],
            "attempts": state["attempts"],
        }
        _, missing = probe.submit(trace_context=False)
        assert probe.wait(missing)["state"] == "succeeded"
        with Session(probe.engine) as session:
            generated = session.get(Job, missing)
            assert generated is not None and generated.trace_id
        result["missing_context"] = {"outcome": "succeeded", "server_generated_trace": True}
        for service in ["otel-collector", "prometheus", "grafana", "tempo"]:
            probe.control("pause", service)
            try:
                start = time.monotonic()
                _, job = probe.submit()
                state = probe.wait(job, timeout=40)
                assert state["state"] == "succeeded"
                result[service] = {
                    "processing_seconds": time.monotonic() - start,
                    "outcome": "succeeded",
                }
            finally:
                probe.control("unpause", service)
        time.sleep(4)
        result["database"] = probe.consistency()
        (RESULTS / "faults.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        probe.close()


def ocr() -> None:
    probe = Probe()
    try:
        code = (
            "import sys; sys.path.insert(0,'scripts'); "
            "from parser_smoke import scan_pdf; from golden_fixtures import text_pdf; "
            "sys.stdout.buffer.write(scan_pdf(text_pdf("
            "[['SYNTHETIC INVOICE NO PAYMENT','Invoice SYN-SCAN-001','Total USD 123.45']],1)))"
        )
        document = subprocess.run(
            ["docker", "compose", "exec", "-T", "worker", "python", "-c", code],
            env=probe.env,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert document.returncode == 0 and document.stdout.startswith(b"%PDF-")
        start = time.monotonic()
        invoice, job = probe.submit(document.stdout)
        assert probe.wait(job, timeout=45)["state"] == "succeeded"
        with probe.engine.connect() as connection:
            payload = connection.scalar(
                text(
                    "SELECT payload FROM extraction_versions WHERE invoice_id=:id "
                    "AND schema_version='digital-text-v1'"
                ),
                {"id": invoice},
            )
        assert payload is not None and "SYN-SCAN-001" in json.dumps(payload)
        result = {
            "pages": 1,
            "bytes": len(document.stdout),
            "jobs": 1,
            "outcome": "succeeded",
            "total_seconds": time.monotonic() - start,
            "runtime": "real Linux Poppler/Tesseract",
            "ai_calls": 0,
        }
        (RESULTS / "ocr.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        probe.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["baseline", "sustained", "burst", "faults", "ocr"])
    parser.add_argument("--duration", default=None)
    args = parser.parse_args()
    if args.scenario == "faults":
        faults()
    elif args.scenario == "ocr":
        ocr()
    else:
        k6(args.scenario, args.duration or ("3m" if args.scenario == "sustained" else "20s"))


if __name__ == "__main__":
    main()
