"""Exercise the running API, real presigned MinIO PUT and reviewed served schemas.

Run on the host after make bootstrap. Appends clearly labelled local probe records;
never deletes records or audit history. No secrets or presigned URLs are printed.
"""

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import yaml
from graphql import get_introspection_query
from reconcileiq_api.compatibility import assert_graphql_contract
from reconcileiq_database.config import get_settings


def main() -> None:
    settings = get_settings()
    base = "http://127.0.0.1:8000"
    prefix = uuid4().hex

    def call(
        path: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        *,
        role: str = "admin",
        key: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        token = getattr(settings, f"{role}_token").get_secret_value()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = f"{prefix}-{key}"
        headers.update(extra or {})
        request = Request(
            base + path,
            method=method,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    code, document = call("/openapi.json")
    assert code == 200 and document == yaml.safe_load(
        Path("packages/contracts/openapi.yaml").read_text()
    )
    code, result = call(
        "/graphql", "POST", {"query": get_introspection_query(descriptions=False)}, role="auditor"
    )
    assert code == 200 and "errors" not in result
    assert_graphql_contract(Path("packages/contracts/schema.graphql").read_text(), result["data"])
    po = {
        "vendor": "Local contract probe",
        "description": "Synthetic integration probe",
        "currency": "PHP",
        "issued_on": "2026-10-22",
        "tax_mode": "exclusive",
        "lines": [
            {
                "id": "probe-line",
                "description": "Synthetic item",
                "quantity": "2.5",
                "unit_price_minor": 100,
                "line_total_minor": 250,
            }
        ],
        "subtotal_minor": 250,
        "discount_minor": 0,
        "tax_minor": 30,
        "amount_minor": 280,
    }
    code, order = call("/v1/purchase-orders", "POST", po, key="po")
    assert code == 201, (code, order)
    assert call("/v1/purchase-orders", "POST", po, key="po") == (code, order)
    assert call("/v1/purchase-orders", "POST", po, role="auditor", key="denied")[0] == 403
    pdf = b"%PDF-1.4\n% Synthetic upload integration probe\n%%EOF\n"
    metadata = {
        "filename": "synthetic-probe.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(pdf),
        "sha256": hashlib.sha256(pdf).hexdigest(),
        "purchase_order_id": order["id"],
    }
    code, grant = call(
        "/v1/invoices/upload-requests", "POST", metadata, role="reviewer", key="upload"
    )
    assert code == 201, (code, grant.get("code"))
    with urlopen(
        Request(grant["upload_url"], data=pdf, method="PUT", headers=grant["required_headers"]),
        timeout=20,
    ) as response:
        assert response.status == 200
    path = f"/v1/invoices/{grant['invoice_id']}/upload-complete"
    code, accepted = call(path, "POST", {"expected_version": 1}, role="reviewer", key="complete")
    assert code == 202, (code, accepted)
    assert call(path, "POST", {"expected_version": 1}, role="reviewer", key="complete") == (
        code,
        accepted,
    )
    code, job = call(accepted["status_url"], role="auditor")
    assert code == 200 and job["state"] == "pending_dispatch"
    supplier = settings.supplier_webhooks["local-supplier"]
    notice = {
        "event_id": prefix,
        "event_type": "invoice.available",
        "supplier_invoice_number": f"probe-{prefix}",
        "document_reference": "probe-reference",
    }
    timestamp = str(int(time.time()))
    signature = hmac.new(
        supplier.secret.get_secret_value().encode(),
        timestamp.encode() + b"." + json.dumps(notice).encode(),
        "sha256",
    ).hexdigest()
    webhook_headers = {
        "X-Supplier-Id": "local-supplier",
        "X-Timestamp": timestamp,
        "X-Signature": f"v1={signature}",
    }
    code, admitted = call("/v1/webhooks/suppliers", "POST", notice, extra=webhook_headers)
    assert code == 202, (code, admitted)
    assert call("/v1/webhooks/suppliers", "POST", notice, extra=webhook_headers) == (code, admitted)
    print(
        "Contract smoke passed: served schemas, roles, PO replay, real presigned PUT/seal, "
        "durable 202/jobs, signed webhook replay."
    )


if __name__ == "__main__":
    main()
