import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient
from graphql import get_introspection_query
from pydantic import SecretStr
from reconcileiq_api.auth import Role
from reconcileiq_api.compatibility import assert_graphql_contract
from reconcileiq_api.errors import ApiError
from reconcileiq_api.main import create_app
from reconcileiq_database.config import Settings, SupplierWebhookConfig, get_settings
from reconcileiq_database.contract_seed import seed_contracts
from reconcileiq_database.models import (
    AuditEvent,
    Credential,
    IdempotencyRecord,
    Invoice,
    Job,
    Organization,
    OutboxEvent,
    ReviewDecision,
    WebhookReceipt,
)
from reconcileiq_database.seed import seed_demo
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session


def headers(role: str = "reviewer", key: str = "test-operation-001") -> dict[str, str]:
    return {"Authorization": f"Bearer test-{role}-" + "x" * 40, "Idempotency-Key": key}


@pytest.fixture
def settings(engine: Engine) -> Settings:
    return get_settings().model_copy(
        update={
            "cursor_secret": SecretStr("test-cursor-" + "x" * 40),
            "supplier_webhooks": {
                "test-supplier": SupplierWebhookConfig(
                    organization_id="demo", secret=SecretStr("test-supplier-secret-" + "x" * 40)
                )
            },
        }
    )


@pytest.fixture
def store() -> MagicMock:
    mock = MagicMock()
    mock.grant.return_value = "http://object-store.invalid/signed-upload"
    mock.verify_and_seal.return_value = "demo/sealed/document.pdf"
    return mock


@pytest.fixture
def client(engine: Engine, settings: Settings, store: MagicMock) -> Iterator[TestClient]:
    seed_demo(engine)
    seed_contracts(engine, settings)
    with TestClient(create_app(settings, store)) as http:
        yield http


def po_body() -> dict[str, Any]:
    return {
        "vendor": "Contract Supplier",
        "description": "Contract test PO",
        "currency": "USD",
        "issued_on": "2026-10-21",
        "tax_mode": "exclusive",
        "lines": [
            {
                "id": "line-1",
                "description": "Desk",
                "quantity": "2.5",
                "unit_price_minor": 100,
                "line_total_minor": 250,
            }
        ],
        "subtotal_minor": 250,
        "tax_minor": 25,
        "discount_minor": 10,
        "amount_minor": 265,
    }


def upload_body() -> dict[str, Any]:
    return {
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "size_bytes": 19,
        "sha256": hashlib.sha256(b"%PDF-test-document\n").hexdigest(),
    }


def test_actual_openapi_matches_canonical(client: TestClient) -> None:
    actual = client.get("/openapi.json").json()
    assert actual["openapi"] == "3.1.0"
    assert actual == yaml.safe_load(Path("packages/contracts/openapi.yaml").read_text())
    for path, methods in actual["paths"].items():
        if path.startswith("/v1/") and path != "/v1/webhooks/suppliers":
            for operation in methods.values():
                assert operation["security"] == [{"BearerAuth": []}]


def test_served_graphql_introspection_matches_reviewed_sdl(client: TestClient) -> None:
    result = client.post(
        "/graphql",
        headers=headers("auditor"),
        json={"query": get_introspection_query(descriptions=False)},
    )
    assert result.status_code == 200
    assert "errors" not in result.json(), result.text
    assert_graphql_contract(
        Path("packages/contracts/schema.graphql").read_text(), result.json()["data"]
    )


@pytest.mark.parametrize(
    "path", ["/v1/invoices", "/v1/purchase-orders", "/v1/demo/snapshot", "/v1/jobs/unknown"]
)
def test_business_reads_require_authentication(client: TestClient, path: str) -> None:
    result = client.get(path)
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    assert result.headers["content-type"] == "application/problem+json"
    assert result.json()["trace_id"] == result.headers["x-request-id"]


def test_graphql_auth_is_transport_level(client: TestClient) -> None:
    assert (
        client.post("/graphql", json={"query": "{ dashboardMetrics { invoiceCount } }"}).status_code
        == 401
    )


def test_revoked_credential_is_rejected(client: TestClient, engine: Engine) -> None:
    digest = hashlib.sha256(("test-auditor-" + "x" * 40).encode()).hexdigest()
    with Session(engine) as session, session.begin():
        row = session.get(Credential, digest)
        assert row
        row.revoked = True
    assert client.get("/v1/invoices", headers=headers("auditor")).status_code == 401


@pytest.mark.parametrize("role", ["reviewer", "auditor"])
def test_only_admin_can_create_purchase_orders(client: TestClient, role: str) -> None:
    assert (
        client.post("/v1/purchase-orders", headers=headers(role), json=po_body()).status_code == 403
    )


def test_auditor_cannot_admit_jobs(client: TestClient) -> None:
    assert (
        client.post(
            "/v1/invoices/upload-requests", headers=headers("auditor"), json=upload_body()
        ).status_code
        == 403
    )


def test_po_idempotency_conflict_and_missing_key(client: TestClient, engine: Engine) -> None:
    first = client.post("/v1/purchase-orders", headers=headers("admin"), json=po_body())
    assert first.status_code == 201, first.text
    replay = client.post("/v1/purchase-orders", headers=headers("admin"), json=po_body())
    assert replay.status_code == 201 and replay.json() == first.json()
    assert replay.headers["idempotency-replayed"] == "true"
    changed = {**po_body(), "description": "Changed description"}
    assert (
        client.post("/v1/purchase-orders", headers=headers("admin"), json=changed).json()["code"]
        == "IDEMPOTENCY_CONFLICT"
    )
    assert (
        client.post(
            "/v1/purchase-orders",
            headers={"Authorization": headers("admin")["Authorization"]},
            json=po_body(),
        ).status_code
        == 422
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "purchase_order_created")
            )
            == 1
        )


def test_concurrent_mutations_with_same_key_have_one_effect(
    client: TestClient, engine: Engine
) -> None:
    def send(_: int) -> dict[str, Any]:
        response = client.post(
            "/v1/purchase-orders", headers=headers("admin", "concurrent-key-001"), json=po_body()
        )
        assert response.status_code == 201, response.text
        result: dict[str, Any] = response.json()
        return result

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(send, range(8)))
    assert len({row["id"] for row in results}) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_minor", 266),
        ("amount_minor", 265.0),
        ("amount_minor", True),
        ("currency", "XXX"),
        ("discount_minor", 1000),
        ("organization_id", "other"),
    ],
)
def test_po_validation_does_not_coerce_money_or_trust_org(
    client: TestClient, field: str, value: object
) -> None:
    response = client.post(
        "/v1/purchase-orders", headers=headers("admin"), json={**po_body(), field: value}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert all("input" not in error for error in response.json()["errors"])


def test_po_updates_use_optimistic_versions(client: TestClient) -> None:
    created = client.post("/v1/purchase-orders", headers=headers("admin"), json=po_body()).json()
    body = {
        "expected_version": 1,
        "description": "Updated with reason in history",
        "status": "open",
    }
    changed = client.patch(
        f"/v1/purchase-orders/{created['id']}",
        headers=headers("admin", "update-po-key-001"),
        json=body,
    )
    assert changed.status_code == 200 and changed.json()["version"] == 2
    stale = client.patch(
        f"/v1/purchase-orders/{created['id']}",
        headers=headers("admin", "update-po-key-002"),
        json=body,
    )
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"


def test_goods_receipts_validate_po_line_references(client: TestClient) -> None:
    po = client.post("/v1/purchase-orders", headers=headers("admin"), json=po_body()).json()
    body = {
        "purchase_order_id": po["id"],
        "reference": "receipt-001",
        "received_on": "2026-10-21",
        "lines": [{"purchase_order_line_id": "line-1", "quantity": "2"}],
    }
    response = client.post(
        "/v1/goods-receipts", headers=headers("admin", "receipt-key-001"), json=body
    )
    assert response.status_code == 201, response.text
    body["lines"][0]["purchase_order_line_id"] = "missing-line"
    assert (
        client.post(
            "/v1/goods-receipts", headers=headers("admin", "receipt-key-002"), json=body
        ).status_code
        == 422
    )


def test_upload_completion_is_transactional_and_idempotent(
    client: TestClient, engine: Engine, store: MagicMock
) -> None:
    grant = client.post("/v1/invoices/upload-requests", headers=headers(), json=upload_body())
    assert grant.status_code == 201, grant.text
    invoice_id = grant.json()["invoice_id"]
    before = client.get(f"/v1/invoices/{invoice_id}", headers=headers()).json()
    assert before["amount_minor"] is None and before["currency"] is None
    response = client.post(
        f"/v1/invoices/{invoice_id}/upload-complete",
        headers=headers(key="complete-key-001"),
        json={"expected_version": 1},
    )
    assert response.status_code == 202, response.text
    assert response.headers["location"] == response.json()["status_url"]
    state = client.get(response.headers["location"], headers=headers()).json()
    assert state["state"] == "pending_dispatch" and state["attempts"] == 0
    replay = client.post(
        f"/v1/invoices/{invoice_id}/upload-complete",
        headers=headers(key="complete-key-001"),
        json={"expected_version": 1},
    )
    assert replay.json() == response.json()
    store.verify_and_seal.assert_called_once()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert session.get(Invoice, invoice_id).document_key == "demo/sealed/document.pdf"  # type: ignore[union-attr]


def test_dependency_failure_rolls_back_and_key_can_be_retried(
    client: TestClient, engine: Engine, store: MagicMock
) -> None:
    grant = client.post(
        "/v1/invoices/upload-requests", headers=headers(), json=upload_body()
    ).json()
    store.verify_and_seal.side_effect = ApiError(
        503, "DEPENDENCY_UNAVAILABLE", "Storage is unavailable."
    )
    url = f"/v1/invoices/{grant['invoice_id']}/upload-complete"
    response = client.post(
        url, headers=headers(key="failure-retry-key"), json={"expected_version": 1}
    )
    assert response.status_code == 503
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.get(IdempotencyRecord, ("demo", "failure-retry-key")) is None
        assert session.get(Invoice, grant["invoice_id"]).status == "upload_requested"  # type: ignore[union-attr]
    store.verify_and_seal.side_effect = None
    assert (
        client.post(
            url, headers=headers(key="failure-retry-key"), json={"expected_version": 1}
        ).status_code
        == 202
    )


def test_reconciliation_admission_and_no_duplicate_run(client: TestClient, engine: Engine) -> None:
    body = {"invoice_id": "INV-1042", "expected_version": 1, "policy_id": "three_way_exact_v1"}
    response = client.post("/v1/reconciliations", headers=headers(), json=body)
    assert response.status_code == 202, response.text
    assert response.json()["reconciliation_id"]
    assert (
        client.post("/v1/reconciliations", headers=headers(), json=body).json() == response.json()
    )
    assert (
        client.post(
            "/v1/reconciliations",
            headers=headers(key="second-run-key"),
            json={**body, "expected_version": 2},
        ).status_code
        == 409
    )


def test_review_records_human_reason_and_version_without_payments(
    client: TestClient, engine: Engine
) -> None:
    body = {
        "reconciliation_id": "DEMO-RUN-INV-1043",
        "expected_version": 1,
        "decision": "accept",
        "reason": "Reviewed the synthetic receiving evidence.",
    }
    response = client.post("/v1/invoices/INV-1043/review-decisions", headers=headers(), json=body)
    assert response.status_code == 201, response.text
    assert response.json()["actor"] == "test-reviewer"
    assert (
        client.post("/v1/invoices/INV-1043/review-decisions", headers=headers(), json=body).json()
        == response.json()
    )
    assert (
        client.post(
            "/v1/invoices/INV-1043/review-decisions",
            headers=headers(key="stale-review-key"),
            json=body,
        ).status_code
        == 409
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ReviewDecision)) == 1
        assert session.get(Invoice, "INV-1043").status == "reviewed_accepted"  # type: ignore[union-attr]
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "review_decision_recorded")
        )
        assert event and json.loads(event.detail)["before"]["version"] == 1
    assert not any("payment" in route for route in client.get("/openapi.json").json()["paths"])


def test_cross_tenant_reads_and_mutations_are_isolated(client: TestClient, engine: Engine) -> None:
    token = "second-tenant-" + "y" * 40
    with Session(engine) as session, session.begin():
        session.add(Organization(id="other", name="Other test organization"))
        session.flush()
        session.add(
            Credential(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                organization_id="other",
                actor="other-admin",
                role=Role.ADMIN,
            )
        )
    auth = {"Authorization": f"Bearer {token}", "Idempotency-Key": "other-tenant-key"}
    assert client.get("/v1/invoices/INV-1042", headers=auth).status_code == 404
    assert client.get("/v1/invoices", headers=auth).json()["items"] == []
    cursor = client.get("/v1/invoices?first=1", headers=headers()).json()["page_info"]["end_cursor"]
    assert (
        client.get("/v1/invoices", params={"after": cursor}, headers=auth).json()["code"]
        == "INVALID_CURSOR"
    )

    result = client.post(
        "/graphql",
        headers=auth,
        json={"query": '{ invoice(id:"INV-1042") { id } dashboardMetrics { invoiceCount } }'},
    ).json()
    assert result["data"] == {"invoice": None, "dashboardMetrics": {"invoiceCount": 0}}
    assert (
        client.post(
            "/v1/invoices/upload-requests",
            headers=auth,
            json={**upload_body(), "purchase_order_id": "PO-8001"},
        ).status_code
        == 404
    )


def test_rest_cursor_pagination_stable_ties_snapshot_and_query_binding(
    client: TestClient, engine: Engine
) -> None:
    response = client.get("/v1/invoices?first=2", headers=headers()).json()
    assert [row["id"] for row in response["items"]] == ["INV-1042", "INV-1043"]
    cursor = response["page_info"]["end_cursor"]
    with Session(engine) as session, session.begin():
        session.add(
            Invoice(
                id="new-later-invoice",
                organization_id="demo",
                vendor="Later",
                description="Later insert",
                currency="USD",
                amount_minor=1,
                due_on=date(2026, 10, 22),
                status="needs_review",
                synthetic=False,
            )
        )
    ids = [row["id"] for row in response["items"]]
    while response["page_info"]["has_next_page"]:
        response = client.get(
            "/v1/invoices",
            params={"first": 2, "after": response["page_info"]["end_cursor"]},
            headers=headers(),
        ).json()
        ids.extend(row["id"] for row in response["items"])
    assert ids == [f"INV-{number}" for number in range(1042, 1048)]
    for params in (
        {"after": cursor[:-2] + "AA"},
        {"after": cursor, "vendor": "Other"},
        {"first": 101},
        {"first": 0},
    ):
        assert client.get("/v1/invoices", params=params, headers=headers()).status_code == 422
    assert (
        client.get("/v1/purchase-orders", params={"after": cursor}, headers=headers()).status_code
        == 422
    )


def test_graphql_connections_results_discrepancies_and_audits(client: TestClient) -> None:
    result = client.post(
        "/graphql",
        headers=headers("auditor"),
        json={
            "query": (
                "{ invoices(first:2) { edges { cursor node { id money { amountMinor currency "
                "} } } pageInfo { hasNextPage endCursor } } "
                'reconciliationResult(id:"DEMO-RUN-INV-1043") { status synthetic '
                "discrepancies(first:1) { edges { node { code detail } } } } "
                'auditTimeline(entityId:"INV-1043", first:2) { edges { node { actor action } '
                "} } }"
            )
        },
    ).json()
    assert "errors" not in result, result
    assert result["data"]["invoices"]["pageInfo"]["hasNextPage"]
    assert result["data"]["invoices"]["edges"][0]["node"]["money"]["amountMinor"] == 480000
    run = result["data"]["reconciliationResult"]
    assert run["synthetic"] is True and run["status"] == "COMPLETED"
    assert run["discrepancies"]["edges"][0]["node"]["code"] == "QUANTITY_MISMATCH"
    assert result["data"]["auditTimeline"]["edges"]


def test_graphql_validation_limits_and_resolver_errors_have_codes(client: TestClient) -> None:
    result = client.post(
        "/graphql",
        headers=headers(),
        json={"query": "{ invoices(first:101) { edges { node { id } } } }"},
    ).json()
    assert result["errors"][0]["extensions"]["code"] == "VALIDATION_ERROR"
    aliases = " ".join(f'a{i}:invoice(id:"INV-1042") {{ id }}' for i in range(31))
    result = client.post("/graphql", headers=headers(), json={"query": "{" + aliases + "}"}).json()
    assert result["errors"][0]["extensions"]["code"] == "GRAPHQL_VALIDATION_FAILED"


def signed(
    settings: Settings, body: dict[str, Any], timestamp: int | None = None
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body, separators=(",", ":")).encode()
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    signature = hmac.new(
        settings.supplier_webhooks["test-supplier"].secret.get_secret_value().encode(),
        stamp.encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return raw, {
        "Content-Type": "application/json",
        "X-Supplier-Id": "test-supplier",
        "X-Timestamp": stamp,
        "X-Signature": "v1=" + signature,
    }


def test_webhook_signature_freshness_replay_and_no_trusted_financial_state(
    client: TestClient, engine: Engine, settings: Settings
) -> None:
    notice = {
        "event_id": "event-001",
        "event_type": "invoice.available",
        "supplier_invoice_number": "SUP-001",
        "document_reference": "supplier-object-123",
    }
    raw, auth = signed(settings, notice)
    first = client.post("/v1/webhooks/suppliers", content=raw, headers=auth)
    assert first.status_code == 202, first.text
    replay = client.post("/v1/webhooks/suppliers", content=raw, headers=auth)
    assert replay.json() == first.json() and replay.headers["idempotency-replayed"] == "true"
    assert (
        client.post("/v1/webhooks/suppliers", content=raw + b" ", headers=auth).status_code == 401
    )
    old_raw, old_headers = signed(settings, notice, int(time.time()) - 301)
    assert (
        client.post("/v1/webhooks/suppliers", content=old_raw, headers=old_headers).json()["code"]
        == "WEBHOOK_EXPIRED"
    )
    assert (
        client.post(
            "/v1/webhooks/suppliers", content=raw, headers={**auth, "X-Signature": "v1=invalid"}
        ).status_code
        == 401
    )
    changed, changed_headers = signed(settings, {**notice, "document_reference": "changed"})
    assert (
        client.post("/v1/webhooks/suppliers", content=changed, headers=changed_headers).status_code
        == 409
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(WebhookReceipt)) == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(Invoice)) == 6


def test_replay_only_failed_retryable_jobs_and_keeps_original(
    client: TestClient, engine: Engine
) -> None:
    with Session(engine) as session, session.begin():
        job = Job(
            id="failed-test-job",
            organization_id="demo",
            kind="extraction",
            state="failed",
            retryable=True,
            attempts=5,
        )
        session.add(job)
        session.flush()
        session.add(
            OutboxEvent(id="failed-job-outbox", job_id=job.id, payload={"invoice_id": "INV-1042"})
        )
    response = client.post(
        "/v1/jobs/failed-test-job/replay",
        headers=headers("admin"),
        json={
            "expected_version": 1,
            "reason": "Provider recovered; replay the synthetic execution.",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["job_id"] != "failed-test-job"
    original = client.get("/v1/jobs/failed-test-job", headers=headers()).json()
    assert original["state"] == "failed" and original["attempts"] == 5
    assert (
        client.post(
            "/v1/jobs/failed-test-job/replay",
            headers=headers("reviewer"),
            json={"expected_version": 2, "reason": "Provider is available again."},
        ).status_code
        == 403
    )


def test_job_admission_limit_rolls_back_the_whole_mutation(
    client: TestClient, engine: Engine
) -> None:
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                Job(
                    id=f"quota-{i}",
                    organization_id="demo",
                    kind="extraction",
                    state="pending_dispatch",
                )
                for i in range(500)
            ]
        )
    body = {"invoice_id": "INV-1043", "expected_version": 1, "policy_id": "two_way_exact_v1"}
    response = client.post(
        "/v1/reconciliations", headers=headers(key="quota-request-001"), json=body
    )
    assert response.status_code == 429
    assert response.json()["code"] == "ADMISSION_LIMIT"
    assert response.headers["retry-after"] == "30"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 500
        assert session.get(IdempotencyRecord, ("demo", "quota-request-001")) is None
        row = session.get(Invoice, "INV-1043")
        assert row is not None and row.version == 1


def test_graphql_invalid_variables_do_not_echo_values(client: TestClient) -> None:
    result = client.post(
        "/graphql",
        headers=headers(),
        json={
            "query": "query($first:Int!) { invoices(first:$first) { edges { node { id } } } }",
            "variables": {"first": "private-invalid-value"},
        },
    )
    assert result.json().get("errors")
    assert "private-invalid-value" not in result.text
