"""Read exported local telemetry and exercise an explicitly injected offline provider.

Only the private M08 database is permitted. Never prints trace IDs or credential data.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from performance_benchmark import RESULTS, Probe
from reconcileiq_ai.invoice import InvoiceCandidate
from reconcileiq_ai.provider import FREE_MODEL, PROMPT_VERSION, ExtractionResult
from reconcileiq_database.config import get_settings
from reconcileiq_database.financial import audit_record
from reconcileiq_database.models import (
    ExtractionVersion,
    Invoice,
    Job,
    OutboxEvent,
    StructuredRequest,
)
from reconcileiq_database.observability import committed_metrics
from reconcileiq_database.testing import postgres_schema
from reconcileiq_telemetry import correlation, initialize, parent, stage
from reconcileiq_telemetry.integrations import database as instrument_database
from reconcileiq_telemetry.runtime import TelemetrySettings
from reconcileiq_worker.processing import process_job
from reconcileiq_worker.providers import ProcessingError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def exercise() -> None:
    probe = Probe()
    current = initialize(
        "reconcileiq-worker",
        TelemetrySettings(enabled=True, endpoint="http://127.0.0.1:14318", environment="benchmark"),
    )
    instrument_database(probe.engine)
    try:
        invoice, job = probe.submit(Path("packages/ai/evals/documents/gold-001.pdf").read_bytes())
        assert probe.wait(job)["state"] == "succeeded"
        probe.control("pause", "worker")
        with Session(probe.engine) as session:
            envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job))
            assert envelope is not None
            original = parent(envelope.payload.get("telemetry"))
        with stage("provider.request", {"provider_mode": "synthetic"}, context=original):
            with Session(probe.engine) as session, session.begin():
                row = session.get(Invoice, invoice)
                source = session.scalar(
                    select(ExtractionVersion).where(
                        ExtractionVersion.invoice_id == invoice,
                        ExtractionVersion.schema_version == "digital-text-v1",
                    )
                )
                assert row is not None and source is not None
                key = uuid4().hex
                session.add(
                    Job(
                        id=key,
                        organization_id="demo",
                        invoice_id=invoice,
                        kind="extraction",
                        trace_id=correlation(),
                    )
                )
                session.flush()
                # This explicitly injected, direct-delivery probe does not pretend
                # to publish a broker message or leave an unpublished outbox entry.
                # The preceding real digital job supplies the durable queue trace.
                row.version += 1
                session.add(
                    StructuredRequest(
                        job_id=key,
                        organization_id="demo",
                        invoice_id=invoice,
                        text_version_id=source.id,
                        invoice_version=row.version,
                        request_id=key,
                        model=FREE_MODEL,
                        prompt_version=PROMPT_VERSION,
                        synthetic_authorized=True,
                        requested_by="m08-offline-probe",
                    )
                )
                audit_record(
                    session,
                    "demo",
                    "m08-offline-probe",
                    key,
                    "synthetic_provider_probe_admitted",
                    {"mode": "injected-offline-test"},
                )

            class OfflineProvider:
                def extract(self, pages: dict[int, str]) -> ExtractionResult:
                    value = json.loads(
                        Path("packages/ai/evals/golden.jsonl").read_text().splitlines()[0]
                    )
                    return ExtractionResult(
                        InvoiceCandidate.model_validate(value["expected"]),
                        FREE_MODEL,
                        PROMPT_VERSION,
                        123,
                        456,
                    )

            for _ in range(2):
                process_job(
                    probe.engine, get_settings(), "demo", key, invoice_provider=OfflineProvider()
                )
            assert probe.wait(key)["state"] == "succeeded"
        with probe.engine.connect() as connection:
            usage = connection.execute(
                text(
                    "SELECT proof,input_tokens,output_tokens FROM provider_usage WHERE job_id=:key"
                ),
                {"key": key},
            ).all()
            assert len(usage) == 1 and usage[0][1:] == (123, 456)
            assert usage[0][0]["mode"] == "injected-offline-test"
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM invoice_lines WHERE invoice_id=:id"), {"id": invoice}
                )
                == 0
            )
        result = {
            "synthetic_provider_invocations": 1,
            "duplicate_delivery": True,
            "input_tokens": 123,
            "output_tokens": 456,
            "cloud_calls": 0,
            "financial_lines_published": 0,
            "cost_assumption_usd": 0,
        }
        (RESULTS / "synthetic-provider.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        current.close()
        probe.close()


def exported(trace_id: str) -> list[dict[str, Any]]:
    response = httpx.get(
        "http://127.0.0.1:13200/api/traces/" + trace_id,
        headers={"Accept": "application/json"},
        timeout=15,
        trust_env=False,
    )
    if response.status_code != 200:
        return []
    result = []
    for batch in response.json().get("batches", []):
        service = next(
            a["value"]["stringValue"]
            for a in batch["resource"]["attributes"]
            if a["key"] == "service.name"
        )
        for scope in batch.get("scopeSpans", []):
            for span in scope.get("spans", []):
                result.append({**span, "service": service})
    return result


def review_outage() -> None:
    probe = Probe()
    try:
        probe.control("pause", "otel-collector")
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "scripts/invoice_workflow_smoke.py"],
            env=probe.env,
            capture_output=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, "Financial review under Collector outage failed"
        evidence = json.loads(result.stdout)
        assert evidence["published_financial_invoices"] == 1
        assert evidence["provider_calls"] == 0
        evidence["collector_paused"] = True
        evidence["wall_seconds"] = time.monotonic() - started
        (RESULTS / "review-outage.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence))
    finally:
        if "otel-collector" in probe.paused:
            probe.control("unpause", "otel-collector")
        probe.close()


def retry() -> None:
    """Fresh exported retry link without interrupting shared infrastructure services."""
    probe = Probe()
    current = initialize(
        "reconcileiq-worker",
        TelemetrySettings(enabled=True, endpoint="http://127.0.0.1:14318", environment="benchmark"),
    )
    instrument_database(probe.engine)
    committed_metrics()
    try:
        probe.control("pause", "worker")
        _, job = probe.submit()
        with Session(probe.engine) as session:
            envelope = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job))
            assert envelope is not None
            original = parent(envelope.payload.get("telemetry"))

        class UnavailableReader:
            def read(self, organization: str, key: str, size: int, digest: str) -> bytes:
                raise ProcessingError("STORAGE_UNAVAILABLE", True)

        with stage("invoice.process", context=original):
            process_job(probe.engine, get_settings(), "demo", job, UnavailableReader())
        with Session(probe.engine) as session:
            row = session.get(Job, job)
            assert row is not None and row.attempts == 1 and row.retryable
        probe.control("unpause", "worker")
        state = probe.wait(job, timeout=45)
        assert state["state"] == "succeeded" and state["attempts"] == 2
        result = {
            "mode": "injected reader failure followed by real RabbitMQ/Celery retry",
            "attempts": 2,
            "outcome": "succeeded",
            "lease_accelerated": False,
            "cloud_calls": 0,
        }
        (RESULTS / "retry-export-probe.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        current.close()
        probe.close()


def metrics() -> None:
    """Actual OTLP -> Collector -> Prometheus counters from disposable transactions."""
    probe = Probe()
    current = initialize(
        "reconcileiq-worker",
        TelemetrySettings(enabled=True, endpoint="http://127.0.0.1:14318", environment="benchmark"),
    )
    try:
        assert current.provider is not None and current.meters is not None
        instance = current.provider.resource.attributes["service.instance.id"]
        assert isinstance(instance, str)
        committed_metrics()
        with postgres_schema() as (database, url):
            with patch.dict(os.environ, {"RECONCILE_DATABASE_URL": url.render_as_string(False)}):
                get_settings.cache_clear()
                command.upgrade(Config("alembic.ini"), "head")
            get_settings.cache_clear()
            with Session(database) as session, session.begin():
                session.add(Job(id="durable", organization_id="demo", kind="extraction"))
            with Session(database) as session:
                session.add(Job(id="rolled-back", organization_id="demo", kind="extraction"))
                session.flush()
                session.rollback()
            with Session(database) as session:
                nested_row = Job(id="nested-rollback", organization_id="demo", kind="extraction")
                session.add(nested_row)
                session.flush()
                with session.begin_nested():
                    nested_row.state = "succeeded"
                session.rollback()
            with Session(database) as session, session.begin():
                session.add(
                    Job(id="failed", organization_id="demo", kind="extraction", state="failed")
                )
            with Session(database) as session, session.begin():
                row = session.get(Job, "durable")
                assert row is not None
                row.state = "succeeded"
                session.flush()
                session.flush()
            with Session(database) as session, session.begin():
                row = session.get(Job, "durable")
                assert row is not None
                row.state = "succeeded"  # Duplicate delivery has no new transition.
                row.version += 1
            with Session(database) as session:
                row = session.get(Job, "failed")
                assert row is not None
                row.state = "succeeded"
                session.flush()
                session.add(Job(id="invalid", organization_id="missing", kind="extraction"))
                try:
                    session.flush()
                except IntegrityError:
                    session.rollback()
                else:
                    raise AssertionError("Expected failed transaction")
            with Session(database) as session:
                assert session.get(Job, "rolled-back") is None
                assert session.get(Job, "nested-rollback") is None
                row = session.get(Job, "failed")
                assert row is not None and row.state == "failed"
        current.meters.force_flush(timeout_millis=2000)
        client = httpx.Client(timeout=5, trust_env=False)
        expected = {"pending_dispatch": 1, "failed": 1, "succeeded": 1}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            rows = client.get(
                "http://127.0.0.1:19090/api/v1/query",
                params={"query": f'riq_jobs_transitions_total{{service_instance_id="{instance}"}}'},
            ).json()["data"]["result"]
            observed = {r["metric"]["job_state"]: int(float(r["value"][1])) for r in rows}
            if observed == expected:
                break
            time.sleep(1)
        assert observed == expected, "Exported transition counters differ from durable commits"
        rows = client.get(
            "http://127.0.0.1:19090/api/v1/query",
            params={"query": f'riq_jobs_completed_total{{service_instance_id="{instance}"}}'},
        ).json()["data"]["result"]
        completed = {r["metric"]["outcome"]: int(float(r["value"][1])) for r in rows}
        assert completed == {"failed": 1, "succeeded": 1}
        result = {
            "transitions": observed,
            "completed": completed,
            "transport": "real OTLP/Prometheus",
        }
        (RESULTS / "commit-metrics.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        client.close()
    finally:
        current.close()
        get_settings.cache_clear()
        probe.close()


def verify() -> None:
    probe = Probe()
    try:
        client = httpx.Client(timeout=15, trust_env=False)
        for port, path in [(19090, "/-/ready"), (13200, "/ready"), (13000, "/api/health")]:
            assert client.get(f"http://127.0.0.1:{port}" + path).status_code == 200
        dashboards = client.get(
            "http://127.0.0.1:13000/api/search?type=dash-db",
            auth=("local-admin", probe.env["RECONCILE_GRAFANA_PASSWORD"]),
        )
        assert dashboards.status_code == 200 and len(dashboards.json()) == 5
        expressions = []
        for dashboard_path in Path("infra/observability/grafana/dashboards").glob("*.json"):
            for panel in json.loads(dashboard_path.read_text())["panels"]:
                for target in panel["targets"]:
                    expressions.append(target["expr"])
        nonempty = 0
        for expression in expressions:
            response = client.get(
                "http://127.0.0.1:19090/api/v1/query", params={"query": expression}
            )
            assert response.status_code == 200 and response.json()["status"] == "success"
            nonempty += bool(response.json()["data"]["result"])
        names = client.get("http://127.0.0.1:19090/api/v1/label/__name__/values").json()["data"]
        assert all(
            n in names
            for n in ["riq_http_requests_total", "riq_jobs", "riq_queue_wait_seconds_bucket"]
        )
        groups = client.get("http://127.0.0.1:19090/api/v1/rules").json()["data"]["groups"]
        rules = [rule for group in groups for rule in group["rules"]]
        assert len(rules) == 12 and all(rule["health"] == "ok" for rule in rules)
        monitoring_values = {}
        for key, expression in {
            "observed_active_jobs": (
                'max(max_over_time(riq_jobs{job_state=~"pending_dispatch|queued|running"}[1h]))'
            ),
            "observed_failed_jobs": 'max(riq_jobs{job_state="failed"})',
        }.items():
            values = client.get(
                "http://127.0.0.1:19090/api/v1/query", params={"query": expression}
            ).json()["data"]["result"]
            assert values and float(values[0]["value"][1]) >= 1
            monitoring_values[key] = float(values[0]["value"][1])
        drained = client.get(
            "http://127.0.0.1:19090/api/v1/query",
            params={
                "query": (
                    'max by(job_state)(riq_jobs{job_state=~"pending_dispatch|queued|running"})'
                )
            },
        ).json()["data"]["result"]
        assert {r["metric"]["job_state"] for r in drained} == {
            "pending_dispatch",
            "queued",
            "running",
        }
        assert all(float(r["value"][1]) == 0 for r in drained), "Backlog did not drain to zero"
        monitoring_values["current_active_jobs"] = 0
        with probe.engine.connect() as connection:
            roots = [
                str(row[0])
                for row in connection.execute(
                    text("SELECT DISTINCT trace_id FROM jobs WHERE trace_id IS NOT NULL")
                ).all()
            ]
        spans = []
        paths = []
        database_paths = 0
        linked = 0
        for root in roots:
            found = exported(root)
            spans.extend(found)
            names_in_trace = {s["name"] for s in found}
            if {
                "messaging.publish",
                "messaging.process",
                "celery.task",
                "storage.read",
                "pdf.parse",
                "pdf.digital",
                "invoice.persist",
            } <= names_in_trace:
                by_id = {s["spanId"]: s for s in found}
                # The broker consumer must descend from an actual publisher in the same trace.
                for worker in [s for s in found if s["name"] == "invoice.process"]:
                    ancestors = []
                    has_http = False
                    cursor: dict[str, Any] | None = worker
                    for _ in range(32):
                        if cursor is None:
                            break
                        ancestors.append(cursor["name"])
                        has_http |= (
                            cursor["service"] == "reconcileiq-api"
                            and cursor.get("kind") == "SPAN_KIND_SERVER"
                        )
                        cursor = by_id.get(cursor.get("parentSpanId"))
                        if cursor is None:
                            break
                    if "messaging.publish" in ancestors and "celery.task" in ancestors and has_http:
                        paths.append(ancestors)
                for query in [
                    s
                    for s in found
                    if s["name"] == "db.query" and s["service"] == "reconcileiq-worker"
                ]:
                    cursor = by_id.get(query.get("parentSpanId"))
                    for _ in range(32):
                        if cursor is None:
                            break
                        if cursor["name"] == "invoice.persist":
                            database_paths += 1
                            break
                        cursor = by_id.get(cursor.get("parentSpanId"))
            # Retried attempts intentionally use separate roots; search their linked traces below.
        search = client.get(
            "http://127.0.0.1:13200/api/search",
            # Explicit bounds include persisted blocks, not just live-store data.
            params={
                "q": '{ name = "messaging.process" }',
                "limit": 1000,
                "start": int(time.time()) - 3600,
                "end": int(time.time()),
            },
        ).json()
        for row in search.get("traces", []):
            for span in exported(row["traceID"]):
                valid_links = [
                    link
                    for link in span.get("links", [])
                    if base64.b64decode(link["traceId"]).hex() in roots
                ]
                if valid_links:
                    linked += len(valid_links)
                    spans.append(span)
        assert paths, "No exported HTTP -> publisher -> broker consumer -> processing path"
        assert database_paths, "No exported worker persistence -> database query ancestry"
        assert linked, "No exported delayed retry/restart span links"
        assert any(s["name"] == "review.decision" for s in spans)
        assert any(s["name"] == "matching.reconcile" for s in spans)
        assert any(s["name"] == "provider.request" for s in spans)
        encoded = json.dumps(spans)
        for forbidden in [
            probe.env["RECONCILE_REVIEWER_TOKEN"],
            probe.env["POSTGRES_PASSWORD"],
            "Synthetic Golden Supplies",
            "GOLD-PO",
            "db.statement",
            "url.full",
            "Authorization",
            "invoice-0003",
            "X-Amz-Signature",
        ]:
            assert forbidden not in encoded, "Exported telemetry violated privacy policy"
        durations: dict[str, list[float]] = {}
        for span in spans:
            durations.setdefault(span["name"], []).append(
                (int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])) / 1e9
            )
        result = {
            "dashboards": 5,
            "valid_chart_queries": len(expressions),
            "healthy_alert_rules": len(rules),
            "backlog_failure_monitoring": monitoring_values,
            "nonempty_chart_queries": nonempty,
            "trace_roots_checked": len(roots),
            "exported_spans_checked": len(spans),
            "broker_parent_paths": len(paths),
            "persistence_database_paths": database_paths,
            "delayed_links": linked,
            "privacy_scan": "passed",
            "stage_seconds": durations,
        }
        (RESULTS / "observability.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "stage_seconds"}))
    finally:
        probe.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["exercise", "metrics", "review-outage", "retry", "verify"]
    )
    args = parser.parse_args()
    if args.action == "exercise":
        exercise()
    elif args.action == "metrics":
        metrics()
    elif args.action == "review-outage":
        review_outage()
    elif args.action == "retry":
        retry()
    else:
        verify()
