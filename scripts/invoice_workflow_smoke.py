"""Real local Celery/MinIO human-evidence matching demo. Never calls cloud AI."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from golden_fixtures import text_pdf
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import Invoice, InvoiceLine, Job, JobExecution
from reconcileiq_database.testing import local_postgres_url
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


def main() -> None:
    settings = get_settings()
    assert settings.environment == "local"
    url = local_postgres_url()
    assert url.host == "127.0.0.1" and url.port == 15432
    engine = create_engine(url)
    prefix = "M06-" + uuid4().hex[:10]
    vendor = "Synthetic Workflow Supplies " + prefix
    reference = prefix + "-PO"
    api = httpx.Client(
        base_url="http://127.0.0.1:8000",
        timeout=15,
        headers={"Authorization": "Bearer " + settings.admin_token.get_secret_value()},
    )

    def call(path: str, body: dict[str, Any] | None = None, key: str = "") -> dict[str, Any]:
        response = (
            api.post(path, json=body, headers={"Idempotency-Key": prefix + key})
            if body is not None
            else api.get(path)
        )
        assert response.status_code in {200, 201, 202}, "Local workflow request failed"
        data: dict[str, Any] = response.json()
        return data

    def wait(job: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = call(job["status_url"])
            if state["state"] in {"succeeded", "failed"}:
                assert state["state"] == "succeeded", "Synthetic local job failed"
                return state
            time.sleep(0.25)
        raise AssertionError("Local worker deadline exceeded")

    order = call(
        "/v1/purchase-orders",
        {
            "reference": reference,
            "vendor": vendor,
            "description": "Synthetic M06 workflow",
            "currency": "USD",
            "issued_on": "2026-10-01",
            "tax_mode": "exclusive",
            "lines": [
                {
                    "id": "ITEM-1",
                    "description": "Synthetic component",
                    "quantity": "2",
                    "unit_price_minor": 100,
                    "line_total_minor": 200,
                }
            ],
            "subtotal_minor": 200,
            "discount_minor": 0,
            "tax_minor": 0,
            "amount_minor": 200,
        },
        "po",
    )
    call(
        "/v1/goods-receipts",
        {
            "purchase_order_id": order["id"],
            "reference": prefix + "-GR",
            "received_on": "2026-10-02",
            "lines": [{"purchase_order_line_id": "ITEM-1", "quantity": "2"}],
        },
        "gr",
    )
    labels = [
        json.loads(line) for line in Path("packages/ai/evals/golden.jsonl").read_text().splitlines()
    ]
    results = []
    for index in range(3):
        value = json.loads(json.dumps(labels[0 if index < 2 else 6]["expected"]))
        value["vendor_name"]["value"] = vendor
        value["vendor_identifier"] = {
            "value": None,
            "status": "missing",
            "pages": [],
            "quote": None,
        }
        value["invoice_number"]["value"] = prefix + ("-INV-1" if index < 2 else "-INV-2")
        value["purchase_order_reference"]["value"] = reference
        lines = ["SYNTHETIC FIXTURE NO PAYMENT"]
        for name, field in value.items():
            if name != "lines" and field["value"] is not None:
                lines.append(f"{name}: {field['value']}")
        for line in value["lines"]:
            for name, field in line.items():
                lines.append(f"{name}: {field['value']}")
        pdf = text_pdf([lines], 0)
        grant = call(
            "/v1/invoices/upload-requests",
            {
                "filename": prefix + f"-{index}.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(pdf),
                "sha256": hashlib.sha256(pdf).hexdigest(),
            },
            f"upload-{index}",
        )
        invoice = grant["invoice_id"]
        response = api.put(
            f"/v1/invoices/{invoice}/document",
            content=pdf,
            headers={"Content-Type": "application/pdf"},
        )
        assert response.status_code == 204
        start = time.perf_counter()
        accepted = call(
            f"/v1/invoices/{invoice}/upload-complete", {"expected_version": 1}, f"complete-{index}"
        )
        wait(accepted)
        digital_seconds = time.perf_counter() - start
        state = call(f"/v1/invoices/{invoice}/workspace")
        correction = {
            "expected_version": state["invoice"]["version"],
            "source_version_id": None,
            "reason": "Manually transcribed the generated synthetic PDF",
            "candidate": value,
        }
        first = call(f"/v1/invoices/{invoice}/corrections", correction, f"correction-{index}")
        assert (
            call(f"/v1/invoices/{invoice}/corrections", correction, f"correction-{index}") == first
        )
        state = call(f"/v1/invoices/{invoice}/workspace")
        reconcile = {
            "invoice_id": invoice,
            "expected_version": state["invoice"]["version"],
            "policy_id": "three_way_exact_v1",
        }
        job = call("/v1/reconciliations", reconcile, f"reconcile-{index}")
        assert call("/v1/reconciliations", reconcile, f"reconcile-{index}") == job
        wait(job)
        state = call(f"/v1/invoices/{invoice}/workspace")
        expected = ("matched", "duplicate", "discrepancy")[index]
        assert state["result"]["outcome"] == expected
        if index == 0:
            call(
                f"/v1/invoices/{invoice}/review-decisions",
                {
                    "expected_version": state["invoice"]["version"],
                    "reconciliation_id": state["result"]["reconciliation_id"],
                    "decision": "accept",
                    "reason": "Human confirmed original and exact PO/received quantities",
                },
                "accept",
            )
        from reconcileiq_worker.tasks import invoice_task

        task = invoice_task.apply_async(args=["demo", job["job_id"]])
        assert task.id
        results.append(
            {
                "invoice_id": invoice,
                "outcome": expected,
                "digital_processing_seconds": round(digital_seconds, 3),
                "codes": [d["code"] for d in state["result"]["differences"]],
            }
        )
    time.sleep(1)
    with Session(engine) as session:
        ids = [r["invoice_id"] for r in results]
        assert (
            session.scalar(
                select(func.count()).select_from(InvoiceLine).where(InvoiceLine.invoice_id.in_(ids))
            )
            == 1
        )
        published = session.scalars(
            select(Invoice).where(Invoice.id.in_(ids), Invoice.financial_state == "validated")
        ).all()
        assert len(published) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobExecution)
                .join(Job, Job.id == JobExecution.job_id)
                .where(Job.invoice_id.in_(ids))
            )
            == 6
        )
    print(
        json.dumps(
            {
                "mode": "real-local-services-human-evidence-no-AI",
                "results": results,
                "published_financial_invoices": 1,
                "published_lines": 1,
                "provider_calls": 0,
                "idempotent_corrections_and_reconciliation": True,
                "duplicate_deliveries": 3,
            },
            indent=2,
        )
    )
    api.close()
    engine.dispose()


if __name__ == "__main__":
    main()
