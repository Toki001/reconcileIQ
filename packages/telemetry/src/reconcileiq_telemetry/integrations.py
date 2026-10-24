"""Official supported integrations use the privacy-enforcing tracer provider."""

from typing import Any

from opentelemetry.metrics import NoOpMeterProvider

from reconcileiq_telemetry.privacy import ROUTES
from reconcileiq_telemetry.runtime import initialize, runtime


def api(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    current = runtime()
    if not current.enabled:
        return
    ROUTES.update(r.path for r in app.routes if hasattr(r, "path"))
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=current.provider,
        meter_provider=NoOpMeterProvider(),
        # An empty list falls back to OTEL_* environment settings in contrib.
        # A nonempty never-match regex explicitly prevents that fallback.
        http_capture_headers_server_request=["(?!)"],
        http_capture_headers_server_response=["(?!)"],
        exclude_spans=["receive", "send"],
    )


def database(engine: Any) -> None:
    # SQLAlchemy's public hooks avoid ever creating SQL text/URL attributes.
    import time

    from sqlalchemy import event

    from reconcileiq_telemetry.runtime import metric, stage

    if not runtime().enabled:
        return

    def before(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        try:
            op = statement.split(None, 1)[0].upper() if statement else "OTHER"
            op = op if op in {"SELECT", "INSERT", "UPDATE", "DELETE"} else "OTHER"
            labels = {"db.system.name": engine.dialect.name, "db.operation.name": op}
            span = stage("db.query", labels)
            span.__enter__()
            connection.info.setdefault("riq_sql", []).append((span, time.perf_counter(), labels))
        except Exception:
            pass

    def finish(connection: Any, *args: Any, failed: bool = False) -> None:
        try:
            stack = connection.info.get("riq_sql", [])
            if stack:
                span, start, labels = stack.pop()
                # Mark an error with a fixed sentinel; SQL/driver exceptions never
                # enter span storage. Calling __exit__ preserves the DB exception.
                span.__exit__(
                    RuntimeError if failed else None,
                    RuntimeError("Database operation failed") if failed else None,
                    None,
                )
                metric(
                    "riq.db.duration",
                    time.perf_counter() - start,
                    labels,
                    kind="histogram",
                    unit="s",
                )
        except Exception:
            pass

    def error(context: Any) -> None:
        finish(context.connection, failed=True)
        metric("riq.dependency.failures", labels={"dependency": "database"})

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", finish)
    event.listen(engine, "handle_error", error)


def celery() -> None:
    from opentelemetry.instrumentation.celery import CeleryInstrumentor as Instrumentor

    CeleryInstrumentor: Any = Instrumentor

    if runtime().enabled:
        CeleryInstrumentor().instrument(
            tracer_provider=runtime().provider, meter_provider=NoOpMeterProvider()
        )


def worker_child(**kwargs: Any) -> None:
    # Periodic metric readers must be constructed after prefork, not inherited as dead threads.
    from opentelemetry.instrumentation.celery import CeleryInstrumentor as Instrumentor

    CeleryInstrumentor: Any = Instrumentor

    initialize("reconcileiq-worker")
    if runtime().enabled:
        if CeleryInstrumentor().is_instrumented_by_opentelemetry:
            CeleryInstrumentor().uninstrument()
        celery()


def worker_shutdown(**kwargs: Any) -> None:
    if runtime().enabled:
        runtime().close()
