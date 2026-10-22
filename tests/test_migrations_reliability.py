import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from reconcileiq_database.config import get_settings
from reconcileiq_database.session import build_engine
from reconcileiq_database.testing import postgres_database
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError


@pytest.fixture(params=["sqlite", "postgres"])
def legacy_database(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    def configure(url: str) -> None:
        monkeypatch.setenv("RECONCILE_DATABASE_URL", url)
        monkeypatch.setenv("RECONCILE_ENVIRONMENT", "test")
        monkeypatch.setenv("RECONCILE_WORKER_ENABLED", "false")
        monkeypatch.setenv("RECONCILE_INFRASTRUCTURE_REQUIRED", "false")
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "0002_contracts")

    try:
        if request.param == "sqlite":
            configure(f"sqlite:///{tmp_path / 'upgrade.db'}")
            database = build_engine(get_settings())
            try:
                yield database
            finally:
                database.dispose()
        else:
            if os.environ.get("RECONCILE_TEST_POSTGRES") != "1":
                pytest.skip("Set RECONCILE_TEST_POSTGRES=1 for isolated PostgreSQL migrations")
            with postgres_database() as (database, url):
                configure(url.render_as_string(hide_password=False))
                yield database
    finally:
        get_settings.cache_clear()


def legacy_rows(database: Engine, currency: str = "USD") -> dict[str, list[dict[str, Any]]]:
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO purchase_orders "
                "(id,vendor,description,currency,amount_minor,issued_on,status) "
                "VALUES ('OLD-PO','Synthetic legacy','Retained',:currency,123,'2026-10-01','open')"
            ),
            {"currency": currency},
        )
        connection.execute(
            text(
                "INSERT INTO invoices "
                "(id,vendor,description,currency,amount_minor,due_on,status,purchase_order_id) "
                "VALUES ('OLD-INV','Synthetic legacy','Retained',:currency,123,"
                "'2026-11-01','matched','OLD-PO')"
            ),
            {"currency": currency},
        )
        connection.execute(
            text(
                "INSERT INTO audit_events (id,entity_id,actor,action,detail,occurred_at) VALUES "
                "('OLD-EVENT','OLD-INV','synthetic','created','Original evidence','2026-10-01')"
            )
        )
    with database.connect() as connection:
        return {
            table: [
                dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()
            ]
            for table in ("purchase_orders", "invoices", "audit_events")
        }


def test_populated_upgrade_repeatability_and_safe_projection_rollback(
    legacy_database: Engine,
) -> None:
    before = legacy_rows(legacy_database)
    configuration = Config("alembic.ini")
    command.upgrade(configuration, "head")
    command.upgrade(configuration, "head")
    with legacy_database.connect() as connection:
        for table, rows in before.items():
            for row in rows:
                retained = (
                    connection.execute(
                        text(f"SELECT * FROM {table} WHERE id=:id"), {"id": row["id"]}
                    )
                    .mappings()
                    .one()
                )
                assert {key: retained[key] for key in row} == row
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0007_invoice_workflows"
        )
    command.check(configuration)
    command.downgrade(configuration, "0005_evidence_seals")
    assert "stage" not in {
        column["name"] for column in inspect(legacy_database).get_columns("jobs")
    }
    command.upgrade(configuration, "head")
    with legacy_database.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(text("DELETE FROM audit_events WHERE id='OLD-EVENT'"))
    command.check(configuration)


def test_incompatible_migration_rolls_back_without_rewriting_source(
    legacy_database: Engine,
) -> None:
    before = legacy_rows(legacy_database, "XXX")
    with pytest.raises((DBAPIError, ValueError)):
        command.upgrade(Config("alembic.ini"), "head")
    with legacy_database.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_contracts"
        )
        for table, rows in before.items():
            assert [
                dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()
            ] == rows
    assert "vendors" not in inspect(legacy_database).get_table_names()
