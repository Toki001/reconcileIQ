"""Public OTel API wrappers: reject document/credential attributes before SDK storage."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, SpanContext, Status, StatusCode, Tracer

NAMES = {
    "http.request",
    "graphql.operation",
    "db.query",
    "outbox.dispatch",
    "outbox.recover",
    "messaging.publish",
    "messaging.process",
    "storage.grant",
    "storage.seal",
    "storage.read",
    "storage.write",
    "invoice.process",
    "invoice.persist",
    "invoice.failure",
    "pdf.parse",
    "pdf.digital",
    "ocr.execute",
    "matching.reconcile",
    "matching.persist",
    "review.decision",
    "review.publish",
    "celery.task",
    "celery.publish",
    "provider.request",
    "provider.verify",
    "instrumented.operation",
}
ENUMS: dict[str, set[str]] = {
    "http.request.method": {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"},
    "db.system.name": {"postgresql", "sqlite"},
    "db.operation.name": {"SELECT", "INSERT", "UPDATE", "DELETE", "OTHER"},
    "messaging.system": {"rabbitmq"},
    "messaging.operation.name": {"publish", "process"},
    "messaging.destination.name": {"invoice", "terminal", "probe"},
    "stage": NAMES,
    "outcome": {
        "ok",
        "error",
        "succeeded",
        "failed",
        "matched",
        "unmatched",
        "discrepancy",
        "duplicate",
        "needs_review",
        "extraction_failed",
        "retry",
        "unavailable",
        "disabled",
    },
    "provider_mode": {"synthetic", "live_free", "disabled"},
    "provider": {"mock", "gemini", "none"},
    "dependency": {"database", "rabbitmq", "minio", "collector"},
    "job_state": {"pending_dispatch", "queued", "running", "succeeded", "failed"},
    "reason": {"timeout", "quota", "checksum", "unsupported", "validation", "lease", "other"},
    "category": {"price", "quantity", "tax", "currency", "supplier", "other"},
    "operation": {"query", "mutation", "subscription", "unknown"},
}
ROUTES: set[str] = set()


def attributes(values: Mapping[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (values or {}).items():
        if (
            key in ENUMS
            and isinstance(value, str)
            and value in ENUMS[key]
            or key == "http.route"
            and isinstance(value, str)
            and value in ROUTES
            or key == "http.response.status_code"
            and type(value) is int
            and 100 <= value <= 599
        ):
            clean[key] = value
    return clean


def span_name(value: str) -> str:
    if value in {"run/reconcileiq.process_invoice", "run/reconcileiq.probe"}:
        return "celery.task"
    if value in {"apply_async/reconcileiq.process_invoice", "apply_async/reconcileiq.probe"}:
        return "celery.publish"
    return value if value in NAMES else "instrumented.operation"


class SafeSpan(Span):
    def __init__(self, span: Span) -> None:
        self.span = span

    @property
    def kind(self) -> trace.SpanKind:
        return getattr(self.span, "kind", trace.SpanKind.INTERNAL)

    def end(self, end_time: int | None = None) -> None:
        self.span.end(end_time)

    def get_span_context(self) -> SpanContext:
        return self.span.get_span_context()

    def is_recording(self) -> bool:
        return self.span.is_recording()

    def set_attribute(self, key: str, value: Any) -> None:
        self.span.set_attributes(attributes({key: value}))

    def set_attributes(self, values: Mapping[str, Any]) -> None:
        self.span.set_attributes(attributes(values))

    def add_event(self, name: str, attributes: Any = None, timestamp: int | None = None) -> None:
        pass  # No event bodies, exception messages, stack traces or arbitrary diagnostic payloads.

    def update_name(self, value: str) -> None:
        self.span.update_name(span_name(value))

    def set_status(self, status: Status | StatusCode, description: str | None = None) -> None:
        self.span.set_status(Status(status.status_code if isinstance(status, Status) else status))

    def record_exception(
        self,
        exception: BaseException,
        attributes: Any = None,
        timestamp: int | None = None,
        escaped: bool = False,
    ) -> None:
        self.set_status(StatusCode.ERROR)


class SafeTracer(Tracer):
    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer

    def start_span(self, name: str, *args: Any, **kwargs: Any) -> Span:
        kwargs["attributes"] = attributes(kwargs.get("attributes"))
        context = kwargs.get("context")
        parent = trace.get_current_span(context).get_span_context()
        if parent.is_valid:
            clean_parent = SpanContext(
                parent.trace_id,
                parent.span_id,
                parent.is_remote,
                parent.trace_flags,
                trace.TraceState(),
            )
            kwargs["context"] = trace.set_span_in_context(
                trace.NonRecordingSpan(clean_parent), context
            )
        kwargs["record_exception"] = False
        kwargs["set_status_on_exception"] = False
        kwargs["links"] = [
            trace.Link(
                SpanContext(
                    link.context.trace_id,
                    link.context.span_id,
                    link.context.is_remote,
                    link.context.trace_flags,
                    trace.TraceState(),
                )
            )
            for link in (kwargs.get("links") or [])[:8]
        ]
        return SafeSpan(self.tracer.start_span(span_name(name), *args, **kwargs))

    def start_as_current_span(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self._current(name, *args, **kwargs)

    @contextmanager
    def _current(self, name: str, *args: Any, **kwargs: Any) -> Iterator[Span]:
        end_on_exit = kwargs.pop("end_on_exit", True)
        span = self.start_span(name, *args, **kwargs)
        with trace.use_span(
            span, end_on_exit=False, record_exception=False, set_status_on_exception=False
        ):
            try:
                yield span
            except BaseException:
                span.set_status(StatusCode.ERROR)
                raise
            finally:
                if end_on_exit:
                    span.end()


class SafeProvider(TracerProvider):
    def get_tracer(self, *args: Any, **kwargs: Any) -> Tracer:
        # Instrumentation scope attributes also must not carry external data.
        kwargs.pop("attributes", None)
        return SafeTracer(super().get_tracer(*args, **kwargs))
