"""Privacy and propagation are enforced before SDK storage, without cloud services."""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError
from reconcileiq_api.main import create_app
from reconcileiq_database.config import get_settings
from reconcileiq_telemetry import carrier, parent, stage
from reconcileiq_telemetry.privacy import attributes
from reconcileiq_telemetry.runtime import Runtime, TelemetrySettings, TraceparentOnly
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Runtime, InMemorySpanExporter, InMemoryMetricReader]]:
    import importlib

    module = importlib.import_module("reconcileiq_telemetry.runtime")
    exporter = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    current = Runtime(
        "test", TelemetrySettings(enabled=True), exporter=exporter, metric_readers=[reader]
    )
    monkeypatch.setattr(module, "_runtime", current)
    yield current, exporter, reader
    current.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://example.com",
        "http://127.0.0.1@evil.example",
        "http://key@localhost:4318",
        "http://localhost:4318?token=secret",
        "http://localhost/secret",
    ],
)
def test_export_destination_fails_closed(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        TelemetrySettings(enabled=True, endpoint=endpoint)


def test_no_sensitive_data_reaches_sdk(telemetry: Any) -> None:
    runtime, exporter, _ = telemetry
    secret = "PRIVATE-DOCUMENT-CONTENT-api-key-SUPPLIER-ABC"
    with runtime.tracer().start_as_current_span(
        secret,
        attributes={
            "Authorization": secret,
            "db.statement": secret,
            "url.full": secret,
            "http.request.header.authorization": secret,
            "invoice_id": secret,
            "outcome": "ok",
        },
    ) as span:
        span.set_attribute("password", secret)
        span.add_event(secret, {"document": secret})
        span.record_exception(ValueError(secret))
        span.set_status(trace.Status(trace.StatusCode.ERROR, secret))
        span.update_name(secret)
    runtime.provider.force_flush()
    exported = exporter.get_finished_spans()
    assert len(exported) == 1
    assert secret not in exported[0].to_json()
    assert dict(exported[0].attributes) == {"outcome": "ok"}
    assert not exported[0].events
    assert exported[0].name == "instrumented.operation"


def test_durable_w3c_context_and_no_baggage(telemetry: Any) -> None:
    current, exporter, _ = telemetry
    with stage("http.request"):
        saved = carrier()
        original = trace.get_current_span().get_span_context()
    with stage("outbox.dispatch", context=parent(saved)):
        assert trace.get_current_span().get_span_context().trace_id == original.trace_id
    linked = trace.get_current_span(parent(saved)).get_span_context()
    with stage("messaging.process", context=parent({}), links=[trace.Link(linked)]):
        assert trace.get_current_span().get_span_context().trace_id != original.trace_id
    current.provider.force_flush()
    spans = exporter.get_finished_spans()
    assert spans[1].parent.span_id == original.span_id
    assert spans[2].links[0].context.trace_id == original.trace_id
    incoming = TraceparentOnly().extract(
        saved | {"baggage": "supplier=secret", "tracestate": "tenant=secret"}
    )
    assert not trace.get_current_span(incoming).get_span_context().trace_state


@pytest.mark.parametrize(
    "value", [None, 123, "secret", "00-" + "0" * 32 + "-" + "0" * 16 + "-01", "00-abc-def-01"]
)
def test_missing_or_invalid_context_is_safe(value: Any, telemetry: Any) -> None:
    with stage("messaging.process", context=parent({"traceparent": value})):
        assert trace.get_current_span().get_span_context().is_valid


def test_metric_attributes_are_bounded(telemetry: Any) -> None:
    current, _, reader = telemetry
    current.metric(
        "riq.test", 1, {"invoice_id": "private", "outcome": "ok", "provider_mode": "SECRET"}
    )
    data = reader.get_metrics_data()
    point = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
    assert dict(point.attributes) == {"outcome": "ok"}
    assert attributes({"http.response.status_code": 999, "tenant": "private"}) == {}


def test_invalid_observations_and_envelopes_cannot_change_processing(telemetry: Any) -> None:
    current, _, reader = telemetry
    invalid: list[Any] = [None, "PRIVATE", {}, float("nan"), float("inf"), -1, 10**1000]
    for value in invalid:
        current.metric("riq.invalid", value)
        with stage("messaging.process", context=parent(value)):
            assert trace.get_current_span().get_span_context().is_valid
    data = reader.get_metrics_data()
    assert all(
        m.name != "riq.invalid"
        for r in data.resource_metrics
        for s in r.scope_metrics
        for m in s.metrics
    )


def test_api_graphql_sql_spans_contain_no_queries_or_headers(
    engine: Engine, telemetry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    current, exporter, _ = telemetry
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST", ".*")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE", ".*")
    with TestClient(create_app(get_settings())) as client:
        response = client.post(
            "/graphql",
            json={"query": "{ dashboardMetrics { invoiceCount } }"},
            headers={
                "Authorization": "Bearer test-auditor-" + "x" * 40,
                "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
                "baggage": "private-document",
                "tracestate": "supplier=secret",
            },
        )
        assert response.status_code == 200
        assert response.headers["traceparent"].split("-")[1] == "a" * 32
        assert client.get("/v1/jobs/private-customer-reference").status_code == 401
    current.provider.force_flush()
    spans = exporter.get_finished_spans()
    serialized = json.dumps([json.loads(s.to_json()) for s in spans])
    for forbidden in [
        "private-customer-reference",
        "private-document",
        "test-auditor-",
        "supplier=secret",
        "invoiceCount",
    ]:
        assert forbidden not in serialized
    assert any(s.name == "graphql.operation" for s in spans)
    assert any(s.name == "db.query" for s in spans)


def test_graphql_error_is_marked_without_query_contents(engine: Engine, telemetry: Any) -> None:
    current, exporter, _ = telemetry
    with TestClient(create_app(get_settings())) as client:
        response = client.post(
            "/graphql",
            json={"query": "{ PRIVATE_DOCUMENT_FIELD }"},
            headers={"Authorization": "Bearer test-auditor-" + "x" * 40},
        )
        assert response.json()["errors"]
    current.provider.force_flush()
    spans = exporter.get_finished_spans()
    assert any(
        s.name == "graphql.operation" and s.status.status_code == trace.StatusCode.ERROR
        for s in spans
    )
    assert "PRIVATE_DOCUMENT_FIELD" not in json.dumps([s.to_json() for s in spans])


class FailingExporter(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        raise RuntimeError("SECRET provider exception")


def test_database_errors_are_classified_without_changing_rollback(
    engine: Engine, telemetry: Any
) -> None:
    from reconcileiq_telemetry.integrations import database
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database(engine)
    with engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(text("SELECT * FROM PRIVATE_DOCUMENT_MISSING_TABLE"))
        connection.rollback()
        assert connection.scalar(text("SELECT 1")) == 1
    telemetry[0].provider.force_flush()
    spans = telemetry[1].get_finished_spans()
    assert [s.status.status_code for s in spans] == [
        trace.StatusCode.ERROR,
        trace.StatusCode.UNSET,
    ]
    assert "PRIVATE_DOCUMENT" not in json.dumps([s.to_json() for s in spans])
    data = telemetry[2].get_metrics_data()
    points = [
        p
        for r in data.resource_metrics
        for s in r.scope_metrics
        for m in s.metrics
        if m.name == "riq.stage.calls"
        for p in m.data.data_points
    ]
    assert {(p.attributes["outcome"], p.value) for p in points} == {("ok", 1), ("error", 1)}


def test_export_failure_does_not_change_business_result(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import importlib

    module = importlib.import_module("reconcileiq_telemetry.runtime")
    current = Runtime(
        "failure-test",
        TelemetrySettings(enabled=True),
        exporter=FailingExporter(),
        metric_readers=[],
    )
    monkeypatch.setattr(module, "_runtime", current)
    with stage("invoice.process"):
        result = 42
    assert result == 42
    assert current.provider is not None
    current.provider.force_flush(timeout_millis=1000)
    current.close()
    assert "SECRET" not in caplog.text


def test_no_collector_does_not_block_request_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    module = importlib.import_module("reconcileiq_telemetry.runtime")
    current = Runtime(
        "timeout-test",
        TelemetrySettings(enabled=True, endpoint="http://127.0.0.1:9"),
        metric_readers=[],
    )
    monkeypatch.setattr(module, "_runtime", current)
    start = time.perf_counter()
    for _ in range(100):
        with stage("invoice.process"):
            pass
    assert time.perf_counter() - start < 1
    current.close()


def test_commit_metrics_ignore_rollback(engine: Engine, telemetry: Any) -> None:
    from reconcileiq_database.models import Job
    from reconcileiq_database.observability import committed_metrics

    committed_metrics()
    with Session(engine) as session:
        session.add(Job(id="rolled-back-metric", organization_id="demo", kind="extraction"))
        session.flush()
        session.rollback()
    with Session(engine) as session, session.begin():
        session.add(Job(id="committed-metric", organization_id="demo", kind="extraction"))
    data = telemetry[2].get_metrics_data()
    points = [
        p
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for m in scope.metrics
        if m.name == "riq.jobs.transitions"
        for p in m.data.data_points
    ]
    assert sum(p.value for p in points) == 1
    with Session(engine) as session:
        assert session.get(Job, "rolled-back-metric") is None


def test_backlog_gauges_emit_zero_after_queue_drains(engine: Engine, telemetry: Any) -> None:
    from reconcileiq_database.models import Job
    from reconcileiq_database.observability import gauges

    gauges(engine)

    def counts() -> dict[str, int]:
        data = telemetry[2].get_metrics_data()
        return {
            p.attributes["job_state"]: int(p.value)
            for r in data.resource_metrics
            for s in r.scope_metrics
            for m in s.metrics
            if m.name == "riq.jobs"
            for p in m.data.data_points
        }

    zero = {s: 0 for s in ("pending_dispatch", "queued", "running", "succeeded", "failed")}
    assert counts() == zero
    with Session(engine) as session, session.begin():
        session.add(Job(id="drained", organization_id="demo", kind="extraction"))
    assert counts() == {**zero, "pending_dispatch": 1}
    with Session(engine) as session, session.begin():
        job = session.get(Job, "drained")
        assert job is not None
        job.state = "succeeded"
    assert counts() == {**zero, "succeeded": 1}


def test_dependency_metrics_reuse_the_lifecycle_owned_api_pool(
    engine: Engine, telemetry: Any
) -> None:
    from sqlalchemy import event
    from sqlalchemy.pool import Pool

    connections = []

    def connected(connection: Any, record: Any) -> None:
        connections.append(record)

    event.listen(Pool, "connect", connected)
    try:
        with TestClient(create_app(get_settings())):
            data = telemetry[2].get_metrics_data()
        assert len(connections) == 1
        points = [
            p
            for r in data.resource_metrics
            for s in r.scope_metrics
            for m in s.metrics
            if m.name == "riq.dependency.available"
            for p in m.data.data_points
        ]
        assert any(p.attributes == {"dependency": "database"} and p.value == 1 for p in points)
    finally:
        event.remove(Pool, "connect", connected)


@pytest.mark.parametrize("nested_commit,outer_commit", [(True, False), (False, True), (True, True)])
def test_savepoints_only_count_durable_final_state(
    engine: Engine, telemetry: Any, nested_commit: bool, outer_commit: bool
) -> None:
    from reconcileiq_database.models import Job
    from reconcileiq_database.observability import committed_metrics

    committed_metrics()
    with Session(engine) as session:
        job = Job(id="savepoint-metric", organization_id="demo", kind="extraction")
        session.add(job)
        session.flush()
        nested = session.begin_nested()
        job.state = "succeeded"
        session.flush()
        (nested.commit if nested_commit else nested.rollback)()
        assert telemetry[2].get_metrics_data() is None
        (session.commit if outer_commit else session.rollback)()
    data = telemetry[2].get_metrics_data()
    if not outer_commit:
        assert data is None
        with Session(engine) as session:
            assert session.get(Job, "savepoint-metric") is None
    else:
        points = [
            p
            for r in data.resource_metrics
            for s in r.scope_metrics
            for m in s.metrics
            if m.name == "riq.jobs.transitions"
            for p in m.data.data_points
        ]
        assert sum(p.value for p in points) == 1
        assert points[0].attributes["job_state"] == (
            "succeeded" if nested_commit else "pending_dispatch"
        )


def test_failed_flush_and_duplicate_updates_do_not_count(engine: Engine, telemetry: Any) -> None:
    from reconcileiq_database.models import Job
    from reconcileiq_database.observability import committed_metrics
    from sqlalchemy.exc import IntegrityError

    committed_metrics()
    with Session(engine) as session, session.begin():
        session.add(
            Job(id="failed-metric", organization_id="demo", kind="extraction", state="failed")
        )
    with Session(engine) as session:
        job = session.get(Job, "failed-metric")
        assert job is not None
        job.state = "succeeded"
        session.flush()
        session.add(Job(id="invalid-metric", organization_id="missing-org", kind="extraction"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    with Session(engine) as session, session.begin():
        job = session.get(Job, "failed-metric")
        assert job is not None
        assert job.state == "failed"
        job.state = "failed"  # A duplicate delivery has no additional terminal effect.
        job.version += 1
        session.flush()
        session.flush()
    data = telemetry[2].get_metrics_data()
    metrics = {m.name: m for r in data.resource_metrics for s in r.scope_metrics for m in s.metrics}
    assert sum(p.value for p in metrics["riq.jobs.transitions"].data.data_points) == 1
    points = metrics["riq.jobs.completed"].data.data_points
    assert len(points) == 1 and points[0].value == 1
    assert points[0].attributes["outcome"] == "failed"


def test_metric_observer_failure_cannot_abort_commit(
    engine: Engine, telemetry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import reconcileiq_database.observability as observers
    from reconcileiq_database.models import Job

    observers.committed_metrics()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("PRIVATE metric failure")

    monkeypatch.setattr(observers, "metric", fail)
    with Session(engine) as session, session.begin():
        session.add(Job(id="observer-failure", organization_id="demo", kind="extraction"))
    with Session(engine) as session:
        assert session.get(Job, "observer-failure") is not None


def test_synthetic_provider_metrics_are_idempotent(finance_engine: Engine, telemetry: Any) -> None:
    from reconcileiq_database.models import ProviderUsage
    from reconcileiq_worker.processing import process_job
    from test_invoice_workflow import MockProvider, ai_request, client_for, setup_invoice

    invoice = setup_invoice(finance_engine, client_for(finance_engine))
    job = ai_request(finance_engine, invoice)
    for _ in range(2):
        process_job(finance_engine, get_settings(), "demo", job, invoice_provider=MockProvider())
    with Session(finance_engine) as session:
        assert len(session.scalars(select(ProviderUsage)).all()) == 1
    data = telemetry[2].get_metrics_data()
    metrics = {
        m.name: m for r in data.resource_metrics for scope in r.scope_metrics for m in scope.metrics
    }
    for key, value in [("requests", 1), ("input.tokens", 123), ("output.tokens", 456)]:
        points = metrics["riq.provider." + key].data.data_points
        assert sum(p.value for p in points) == value
        assert all(p.attributes["provider_mode"] == "synthetic" for p in points)
    assert metrics["riq.provider.estimated_cost"].data.data_points[0].value == 0


def test_high_volume_export_timeout_has_bounded_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    import threading

    module = importlib.import_module("reconcileiq_telemetry.runtime")
    release = threading.Event()

    class DelayedExporter(SpanExporter):
        def export(self, spans: Any) -> SpanExportResult:
            release.wait(timeout=2)
            return SpanExportResult.FAILURE

    current = Runtime(
        "volume-test",
        TelemetrySettings(enabled=True),
        exporter=DelayedExporter(),
        metric_readers=[],
    )
    monkeypatch.setattr(module, "_runtime", current)
    start = time.perf_counter()
    try:
        for _ in range(5000):
            with stage("invoice.process"):
                pass
        assert time.perf_counter() - start < 3
        assert current.provider is not None
        processor: Any = current.provider._active_span_processor._span_processors[0]
        assert len(processor._batch_processor._queue) <= 512
    finally:
        release.set()
        current.close()


def test_dashboard_and_collector_configuration_are_bounded() -> None:
    import yaml

    collector = yaml.safe_load(Path("infra/observability/collector.yaml").read_text())
    assert set(collector["service"]["pipelines"]) == {"traces", "metrics"}
    assert "debug" not in collector["exporters"]
    assert collector["exporters"]["otlp_http/tempo"]["sending_queue"]["queue_size"] == 256
    dashboards = list(Path("infra/observability/grafana/dashboards").glob("*.json"))
    assert len(dashboards) == 5
    for path in dashboards:
        dashboard = json.loads(path.read_text())
        assert "local-synthetic" in dashboard["tags"]
        assert dashboard["panels"]
        for panel in dashboard["panels"]:
            assert all("riq_" in t["expr"] for t in panel["targets"])
