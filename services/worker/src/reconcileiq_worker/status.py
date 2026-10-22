"""Local backlog inspection; never prints credentials, URLs, document text or raw errors."""

import json

from kombu import Connection
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import Job
from reconcileiq_database.session import build_engine
from sqlalchemy import func, select


def main() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            states = dict(
                connection.execute(select(Job.state, func.count()).group_by(Job.state)).all()
            )
        queues: dict[str, int | str] = {}
        try:
            with Connection(settings.broker_url.get_secret_value(), connect_timeout=3) as broker:
                for name in ("reconcileiq.invoices", "reconcileiq.dead"):
                    queues[name] = broker.default_channel.queue_declare(
                        queue=name, passive=True
                    ).message_count
        except Exception:
            queues["broker"] = "unavailable"
        print(json.dumps({"job_states": states, "ready_messages": queues}, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
