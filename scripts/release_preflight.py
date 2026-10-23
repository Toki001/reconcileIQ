"""Fail closed before a release process consumes work; never migrate during startup."""

import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from reconcileiq_database.config import get_settings
from reconcileiq_database.session import build_engine
from sqlalchemy import text


def verify() -> None:
    settings = get_settings()  # Only local/test identity exists; production is rejected.
    if not settings.infrastructure_required or not settings.worker_enabled:
        raise ValueError("Release rehearsal requires PostgreSQL/RabbitMQ infrastructure")
    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != (
                expected
            ):
                raise ValueError("Apply reviewed migrations before starting release services")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        verify()
    except Exception:
        print("Release preflight refused startup: identity, infrastructure or schema unavailable.")
        sys.exit(1)
