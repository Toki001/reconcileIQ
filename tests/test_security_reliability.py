import hashlib
import logging
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from reconcileiq_api.main import create_app
from reconcileiq_database.config import Settings, get_settings
from reconcileiq_database.contract_seed import seed_contracts
from reconcileiq_database.models import Credential, Organization
from reconcileiq_database.seed import seed_demo
from reconcileiq_worker.logging import SafeDiagnostics
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_api_contracts import headers
from test_processing import admit


def test_job_replay_and_graphql_object_authorization(finance_engine: Engine) -> None:
    settings = get_settings().model_copy(update={"cursor_secret": SecretStr("cursor-" + "x" * 40)})
    seed_demo(finance_engine)
    seed_contracts(finance_engine, settings)
    _, job_id = admit(finance_engine)
    token = "foreign-admin-" + "x" * 40
    with Session(finance_engine) as session, session.begin():
        session.add(Organization(id="foreign", name="Synthetic foreign tenant"))
        session.flush()
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id="foreign",
                actor="foreign-admin",
                role="admin",
            )
        )
    auth = {"Authorization": "Bearer " + token, "Idempotency-Key": "foreign-attempt"}
    with TestClient(create_app(settings)) as client:
        for path in (f"/v1/jobs/{job_id}", "/v1/invoices/INV-1043", "/v1/purchase-orders/PO-8001"):
            response = client.get(path, headers=auth)
            assert response.status_code == 404 and "INV-1043" not in response.json()["detail"]
        response = client.post(
            f"/v1/jobs/{job_id}/replay",
            headers=auth,
            json={"expected_version": 1, "reason": "Attempt unauthorized foreign replay"},
        )
        assert response.status_code == 404
        assert client.get("/v1/processing-jobs", headers=auth).json()["items"] == []
        query = (
            '{ invoice(id:"INV-1043") { id } '
            'reconciliationResult(id:"DEMO-RUN-INV-1043") { id } '
            'auditTimeline(entityId:"INV-1043") { edges { node { id } } } '
            "exceptions { edges { node { id } } } }"
        )
        result = client.post("/graphql", headers=auth, json={"query": query}).json()
        assert "errors" not in result
        assert result["data"] == {
            "invoice": None,
            "reconciliationResult": None,
            "auditTimeline": {"edges": []},
            "exceptions": {"edges": []},
        }
        # Bearer values in a query string never authenticate a request.
        assert client.get("/v1/invoices", params={"token": token}).status_code == 401


def test_diagnostic_filter_redacts_decoded_credentials_and_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECONCILE_BROKER_URL", "amqp://synthetic:p%40ssword@localhost//")
    monkeypatch.setenv("RECONCILE_MINIO_ACCESS_KEY", "synthetic-private-access-key")
    get_settings.cache_clear()
    try:
        try:
            raise RuntimeError("private-document-content")
        except RuntimeError:
            record = logging.LogRecord(
                "celery",
                logging.ERROR,
                "",
                0,
                "credentials %s %s",
                ("p@ssword", "synthetic-private-access-key"),
                sys.exc_info(),
            )
        record.exc_text = "private-document-content"
        assert SafeDiagnostics().filter(record)
        assert record.exc_info is None and record.exc_text is None
        assert "p@ssword" not in record.getMessage()
        assert "synthetic-private-access-key" not in record.getMessage()
        assert "private-document-content" not in logging.Formatter().format(record)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("environment", ["production", "staging", "prod"])
def test_local_credentials_cannot_enable_a_production_configuration(environment: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": environment})


def test_consistent_errors_do_not_echo_invalid_credentials_or_values(engine: Engine) -> None:
    from test_api_contracts import po_body

    with TestClient(create_app(get_settings()), raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/purchase-orders",
            headers=headers("admin"),
            json={**po_body(), "amount_minor": "private-invalid-document"},
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["trace_id"] == response.headers["x-request-id"]
        assert "private-invalid-document" not in response.text
        assert "test-admin" not in response.text
