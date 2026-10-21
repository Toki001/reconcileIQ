from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from reconcileiq_api.main import create_app
from reconcileiq_database.config import Settings, get_settings
from reconcileiq_database.models import AuditEvent, Invoice, JobExecution
from reconcileiq_database.seed import seed_demo
from reconcileiq_worker.tasks import record_probe
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session


def test_seed_is_idempotent_and_every_record_is_audited(engine: Engine) -> None:
    seed_demo(engine)
    seed_demo(engine)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 6
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 12
        assert session.get(AuditEvent, "seed-INV-1042") is not None
    command.check(Config("alembic.ini"))


def test_invoice_cannot_reference_a_missing_purchase_order(engine: Engine) -> None:
    seed_demo(engine)
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE invoices SET purchase_order_id='missing' WHERE id='INV-1042'")
        )


@pytest.mark.parametrize(
    "operation", ["UPDATE audit_events SET actor='changed'", "DELETE FROM audit_events"]
)
def test_audit_database_rejects_mutations(engine: Engine, operation: str) -> None:
    seed_demo(engine)
    with engine.begin() as connection, pytest.raises(DatabaseError, match="append-only"):
        connection.execute(text(operation))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 12


def test_orm_rejects_audit_updates(engine: Engine) -> None:
    seed_demo(engine)
    with Session(engine) as session:
        event = session.get(AuditEvent, "seed-INV-1042")
        assert event
        event.actor = "changed"
        with pytest.raises(ValueError, match="append-only"):
            session.commit()


def test_concurrent_duplicate_worker_delivery_has_one_effect(engine: Engine) -> None:
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: record_probe("same-business-key", engine), range(12)))
    assert results == ["ok"] * 12
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(JobExecution)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_health_and_persisted_demo_contract(engine: Engine) -> None:
    seed_demo(engine)
    with TestClient(create_app(get_settings())) as client:
        assert client.get("/health/live").json()["status"] == "ok"
        health = client.get("/health/ready")
        assert health.status_code == 200
        assert health.json()["dependencies"] == {
            "database": "ok",
            "rabbitmq": "not_required",
            "minio": "not_required",
        }
        response = client.get("/v1/demo/snapshot")
        assert response.status_code == 200
        data = response.json()
        assert data["synthetic"] is True
        assert len(data["invoices"]) == 6
        assert all(type(row["amount_minor"]) is int for row in data["invoices"])
        assert data["invoices"][0]["purchase_order_id"] == "PO-8001"
        assert client.post("/v1/demo/snapshot", json={}).status_code == 405


def test_unmigrated_database_is_unready_and_errors_do_not_leak() -> None:
    with TestClient(
        create_app(
            Settings(database_url=SecretStr("sqlite:///:memory:"), infrastructure_required=False)
        )
    ) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        failure = client.get("/v1/demo/snapshot")
        assert failure.status_code == 503
        assert failure.json()["code"] == "DATABASE_UNAVAILABLE"
        assert "SELECT" not in failure.text
        assert "sqlite" not in failure.text


def test_settings_redact_secrets_and_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Settings(
        database_url=SecretStr("postgresql+psycopg://user:private-password@localhost/db")
    )
    assert "private-password" not in repr(config)
    with pytest.raises(ValidationError):
        Settings(infrastructure_required=True)
    monkeypatch.setenv("RECONCILE_ENVIRONMENT", "production")
    with pytest.raises(ValidationError):
        Settings()


def test_audit_destroying_downgrade_is_refused(engine: Engine) -> None:
    with pytest.raises(RuntimeError, match="destroy audit"):
        command.downgrade(Config("alembic.ini"), "base")
