import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from reconcileiq_database.config import Settings, get_settings
from reconcileiq_database.models import Credential
from reconcileiq_database.session import build_engine
from sqlalchemy import Engine
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def dispose_test_api_pools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Legacy helper clients sometimes issue requests without entering lifespan.
    # Their API-owned pool is distinct from the finance_engine fixture's pool.
    # Keep warnings strict and release those connections deterministically.
    pools: list[Engine] = []

    def tracked(settings: Settings) -> Engine:
        database = build_engine(settings)
        pools.append(database)
        return database

    monkeypatch.setattr("reconcileiq_api.main.build_engine", tracked)
    yield
    for database in pools:
        database.dispose()


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setenv("RECONCILE_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("RECONCILE_ENVIRONMENT", "test")
    monkeypatch.setenv("RECONCILE_INFRASTRUCTURE_REQUIRED", "false")
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    database = build_engine(get_settings())
    with Session(database) as session, session.begin():
        for role in ("admin", "reviewer", "auditor"):
            token = f"test-{role}-" + "x" * 40
            session.add(
                Credential(
                    digest=hashlib.sha256(token.encode()).hexdigest(),
                    organization_id="demo",
                    actor=f"test-{role}",
                    role=role,
                )
            )
    yield database
    database.dispose()
    get_settings.cache_clear()


@pytest.fixture(params=["sqlite", "postgres"])
def finance_engine(
    request: pytest.FixtureRequest, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    if request.param == "sqlite":
        yield engine
        return
    import os

    from reconcileiq_database.testing import postgres_schema

    if os.environ.get("RECONCILE_TEST_POSTGRES") != "1":
        pytest.skip("Set RECONCILE_TEST_POSTGRES=1 to run isolated PostgreSQL integration cases")
    with postgres_schema() as (database, url):
        monkeypatch.setenv("RECONCILE_DATABASE_URL", url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        yield database
        get_settings.cache_clear()
