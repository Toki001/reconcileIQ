import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import Credential
from reconcileiq_database.session import build_engine
from sqlalchemy import Engine
from sqlalchemy.orm import Session


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
