"""Read-only local dependency measurements; no credentials enter attributes."""

from collections.abc import Iterable
from urllib.request import urlopen

from kombu import Connection
from opentelemetry.metrics import CallbackOptions, Observation
from reconcileiq_database.config import Settings
from reconcileiq_telemetry import metric, runtime
from sqlalchemy import Engine, text


def register(settings: Settings, engine: Engine) -> None:
    if not runtime().enabled or runtime().meters is None:
        return
    meter = runtime().meters.get_meter("reconcileiq")  # type: ignore[union-attr]
    broker_available = [0]

    def broker(options: CallbackOptions) -> Iterable[Observation]:
        if not settings.infrastructure_required:
            return
        try:
            with Connection(
                settings.broker_url.get_secret_value(),
                connect_timeout=2,
                transport_options={"read_timeout": 2, "write_timeout": 2},
            ) as connection:
                for queue, label in [
                    ("reconcileiq.invoices", "invoice"),
                    ("reconcileiq.dead", "terminal"),
                ]:
                    result = connection.default_channel.queue_declare(queue=queue, passive=True)
                    yield Observation(result.message_count, {"messaging.destination.name": label})
                broker_available[0] = 1
        except Exception:
            broker_available[0] = 0
            metric("riq.dependency.failures", labels={"dependency": "rabbitmq"})

    meter.create_observable_gauge("riq.broker.ready.messages", callbacks=[broker], unit="")

    def minio(options: CallbackOptions) -> Iterable[Observation]:
        if not settings.infrastructure_required:
            return
        try:
            with urlopen(
                str(settings.minio_endpoint).rstrip("/") + "/minio/health/live", timeout=2
            ) as result:
                yield Observation(int(result.status == 200), {"dependency": "minio"})
        except Exception:
            metric("riq.dependency.failures", labels={"dependency": "minio"})
            yield Observation(0, {"dependency": "minio"})

    def database(options: CallbackOptions) -> Iterable[Observation]:
        try:
            with engine.connect() as connection:
                if engine.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                connection.execute(text("SELECT 1"))
            yield Observation(1, {"dependency": "database"})
        except Exception:
            yield Observation(0, {"dependency": "database"})
            metric("riq.dependency.failures", labels={"dependency": "database"})
        if settings.infrastructure_required:
            yield Observation(broker_available[0], {"dependency": "rabbitmq"})

    meter.create_observable_gauge("riq.dependency.available", callbacks=[minio, database], unit="")

    def provider(options: CallbackOptions) -> Iterable[Observation]:
        from reconcileiq_ai.provider import AISettings

        try:
            enabled = AISettings().enabled
            # Configuration is not a verified/live provider. No cloud request is made here.
            yield Observation(
                1,
                {"provider_mode": "disabled", "outcome": "unavailable" if enabled else "disabled"},
            )
        except Exception:
            yield Observation(1, {"provider_mode": "disabled", "outcome": "unavailable"})

    meter.create_observable_gauge("riq.provider.status", callbacks=[provider], unit="")
