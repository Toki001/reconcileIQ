from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from reconcileiq_database.config import get_settings
from reconcileiq_database.session import build_engine
from sqlalchemy import Engine


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setenv("RECONCILE_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("RECONCILE_ENVIRONMENT", "test")
    monkeypatch.setenv("RECONCILE_INFRASTRUCTURE_REQUIRED", "false")
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    database = build_engine(get_settings())
    yield database
    database.dispose()
    get_settings.cache_clear()
