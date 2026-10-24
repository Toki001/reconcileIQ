"""Bounded, local-only OTLP runtime. Never participates in a financial transaction."""

import atexit
import logging
import math
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from functools import wraps
from typing import Any, ParamSpec, TypeVar
from urllib.parse import urlsplit
from uuid import uuid4

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.textmap import TextMapPropagator, default_getter, default_setter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from requests import Session as HTTPSession

from reconcileiq_telemetry.privacy import SafeProvider, attributes


class TelemetrySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RECONCILE_TELEMETRY_", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    endpoint: str = "http://otel-collector:4318"
    environment: str = Field(default="local", pattern=r"^(local|test|benchmark)$")
    version: str = Field(default="0.1.0-m08", pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    sample_ratio: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def local_only(self) -> "TelemetrySettings":
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "http"
            or url.hostname not in {"otel-collector", "localhost", "127.0.0.1", "::1"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Telemetry accepts only reviewed local OTLP endpoints")
        return self


class Runtime:
    def __init__(
        self,
        service: str,
        settings: TelemetrySettings,
        exporter: SpanExporter | None = None,
        metric_readers: Any = None,
    ) -> None:
        self.pid = os.getpid()
        self.enabled = settings.enabled
        self.closed = False
        self.provider: SafeProvider | None = None
        self.meters: MeterProvider | None = None
        self.instruments: dict[tuple[str, str, str], Any] = {}
        if not settings.enabled:
            return
        propagate.set_global_textmap(TraceparentOnly())
        for logger in ("opentelemetry.exporter", "opentelemetry.sdk"):
            logging.getLogger(logger).handlers = [logging.NullHandler()]
            logging.getLogger(logger).propagate = False
        resource = Resource(
            {
                "service.name": service,
                "service.instance.id": uuid4().hex[:12],
                "service.version": settings.version,
                "deployment.environment.name": settings.environment,
            }
        )
        self.provider = SafeProvider(
            shutdown_on_exit=False,
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(settings.sample_ratio)),
            span_limits=SpanLimits(
                max_attributes=16, max_events=0, max_links=8, max_attribute_length=128
            ),
        )
        trace_session = HTTPSession()
        trace_session.trust_env = False
        metric_session = HTTPSession()
        metric_session.trust_env = False
        self.provider.add_span_processor(
            BatchSpanProcessor(
                exporter
                or OTLPSpanExporter(
                    endpoint=settings.endpoint.rstrip("/") + "/v1/traces",
                    timeout=1,
                    headers={"Content-Type": "application/x-protobuf"},
                    session=trace_session,
                ),
                max_queue_size=512,
                max_export_batch_size=64,
                schedule_delay_millis=500,
                export_timeout_millis=1500,
            )
        )
        readers = (
            metric_readers
            if metric_readers is not None
            else [
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=settings.endpoint.rstrip("/") + "/v1/metrics",
                        timeout=1,
                        headers={"Content-Type": "application/x-protobuf"},
                        session=metric_session,
                    ),
                    export_interval_millis=5000,
                    export_timeout_millis=1500,
                )
            ]
        )
        self.meters = MeterProvider(
            resource=resource,
            metric_readers=readers,
            shutdown_on_exit=False,
            views=[
                View(
                    instrument_unit="s",
                    aggregation=ExplicitBucketHistogramAggregation(
                        boundaries=[
                            0.001,
                            0.005,
                            0.01,
                            0.025,
                            0.05,
                            0.1,
                            0.25,
                            0.5,
                            1,
                            2.5,
                            5,
                            10,
                            25,
                            60,
                            120,
                        ]
                    ),
                )
            ],
        )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with suppress(Exception):
            if self.provider:
                self.provider.force_flush(timeout_millis=2000)
                self.provider.shutdown()
        with suppress(Exception):
            if self.meters:
                self.meters.shutdown(timeout_millis=2000)

    def tracer(self) -> trace.Tracer:
        return self.provider.get_tracer("reconcileiq") if self.provider else trace.NoOpTracer()

    def metric(
        self,
        key: str,
        value: float = 1,
        labels: Mapping[str, Any] | None = None,
        *,
        kind: str = "counter",
        unit: str = "1",
    ) -> None:
        if not self.meters:
            return
        try:
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                return
            identity = (key, kind, unit)
            if identity not in self.instruments:
                meter = self.meters.get_meter("reconcileiq")
                factory = meter.create_histogram if kind == "histogram" else meter.create_counter
                self.instruments[identity] = factory(key, unit=unit)
            instrument = self.instruments[identity]
            (instrument.record if kind == "histogram" else instrument.add)(
                value, attributes(labels)
            )
        except Exception:
            pass


def disabled() -> TelemetrySettings:
    return TelemetrySettings(
        enabled=False,
        endpoint="http://otel-collector:4318",
        environment="local",
        version="0.1.0-m08",
        sample_ratio=1,
    )


_runtime = Runtime("disabled", disabled())
_service = "reconcileiq-worker"


def initialize(service: str, settings: TelemetrySettings | None = None) -> Runtime:
    global _runtime, _service
    _service = service
    if _runtime.enabled and _runtime.pid == os.getpid():
        return _runtime
    try:
        if _runtime.pid != os.getpid():
            atexit.unregister(_runtime.close)
        cfg = settings or TelemetrySettings()
        _runtime = Runtime(service, cfg)
        if cfg.enabled:
            propagate.set_global_textmap(TraceparentOnly())
            # Exporter errors must not dump request payloads/raw exceptions into service logs.
            for logger in ("opentelemetry.exporter", "opentelemetry.sdk"):
                logging.getLogger(logger).handlers = [logging.NullHandler()]
                logging.getLogger(logger).propagate = False
            atexit.register(_runtime.close)
    except Exception:
        _runtime = Runtime(service, disabled())
        logging.getLogger(__name__).warning("Telemetry unavailable; business processing continues")
    return _runtime


def runtime() -> Runtime:
    if _runtime.pid != os.getpid():
        return initialize(_service)
    return _runtime


def metric(
    key: str,
    value: float = 1,
    labels: Mapping[str, Any] | None = None,
    *,
    kind: str = "counter",
    unit: str = "1",
) -> None:
    runtime().metric(key, value, labels, kind=kind, unit=unit)


@contextmanager
def stage(name: str, labels: Mapping[str, Any] | None = None, **kwargs: Any) -> Iterator[Span]:
    start = time.perf_counter()
    with (
        runtime()
        .tracer()
        .start_as_current_span(name, attributes=attributes(labels), **kwargs) as span
    ):
        outcome = "ok"
        try:
            yield span
        except BaseException:
            outcome = "error"
            span.set_status(StatusCode.ERROR)
            raise
        finally:
            metric(
                "riq.stage.duration",
                time.perf_counter() - start,
                {"stage": name, "outcome": outcome},
                kind="histogram",
                unit="s",
            )
            metric("riq.stage.calls", labels={"stage": name, "outcome": outcome})


P = ParamSpec("P")
T = TypeVar("T")


def instrument(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with stage(name):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def carrier() -> dict[str, str]:
    result: dict[str, str] = {}
    if runtime().enabled:
        TraceContextTextMapPropagator().inject(result)
    return {"traceparent": result["traceparent"]} if "traceparent" in result else {}


def parent(values: Mapping[str, Any] | None) -> Context:
    if values is not None and not isinstance(values, Mapping):
        return Context()
    value = (values or {}).get("traceparent", "")
    if not isinstance(value, str) or not re.fullmatch(
        r"00-[a-f0-9]{32}-[a-f0-9]{16}-[a-f0-9]{2}", value
    ):
        return Context()
    # Baggage/tracestate are intentionally not trusted or persisted.
    return TraceContextTextMapPropagator().extract({"traceparent": value})


def correlation() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


class TraceparentOnly(TextMapPropagator):
    @property
    def fields(self) -> set[str]:
        return {"traceparent"}

    def extract(
        self, carrier: Any, context: Context | None = None, getter: Any = default_getter
    ) -> Context:
        values = getter.get(carrier, "traceparent") or []
        return parent({"traceparent": values[0] if values else ""})

    def inject(
        self, carrier: Any, context: Context | None = None, setter: Any = default_setter
    ) -> None:
        values: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(values, context=context)
        if "traceparent" in values:
            setter.set(carrier, "traceparent", values["traceparent"])
