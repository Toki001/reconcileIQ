import json
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pypdf import PdfReader
from reconcileiq_ai.invoice import InvoiceCandidate, validate_sources
from reconcileiq_ai.structured_evaluate import evaluate
from reconcileiq_database.invoice_workflow import facts
from reconcileiq_reconciliation.matching import OrderFacts, OrderLine, reconcile

GOLDEN = Path("packages/ai/evals/golden.jsonl")
ROWS = [json.loads(line) for line in GOLDEN.read_text().splitlines()]


def candidate(index: int = 0) -> InvoiceCandidate:
    return InvoiceCandidate.model_validate(deepcopy(ROWS[index]["expected"]))


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_100_labels_source_evidence_and_deterministic_classification(row: dict[str, Any]) -> None:
    value = InvoiceCandidate.model_validate(row["expected"])
    document = Path("packages/ai/evals") / row["pdf"]
    reader = PdfReader(BytesIO(document.read_bytes()))
    pages = {index + 1: page.extract_text() for index, page in enumerate(reader.pages)}
    validate_sources(value, pages, provider=True)
    context = row["context"]
    raw = context["order"]
    order = (
        OrderFacts(
            **{
                **raw,
                "lines": tuple(
                    OrderLine(**{**line, "quantity": Decimal(line["quantity"])})
                    for line in raw["lines"]
                ),
            }
        )
        if raw
        else None
    )
    result = reconcile(
        facts(value),
        order,
        vendor_matches=context["vendor_matches"],
        duplicate=context["duplicate"],
        received={k: Decimal(v) for k, v in context["received"].items()},
    )
    assert result.outcome == row["expected_outcome"]


@pytest.mark.parametrize(
    "mutation",
    ["float", "boolean", "negative", "extra", "date", "quantity", "tax", "missing", "pages"],
)
def test_strict_candidate_validation(mutation: str) -> None:
    raw = deepcopy(ROWS[0]["expected"])
    if mutation in {"float", "boolean", "negative"}:
        raw["total_minor"]["value"] = {"float": 1.0, "boolean": True, "negative": -1}[mutation]
    elif mutation == "extra":
        raw["approval"] = True
    elif mutation == "date":
        raw["invoice_date"]["value"] = "2026-02-30"
    elif mutation == "quantity":
        raw["lines"][0]["quantity"]["value"] = "0"
    elif mutation == "tax":
        raw["lines"][0]["tax_rate"]["value"] = "1.1"
    elif mutation == "missing":
        raw["invoice_number"]["status"] = "missing"
    else:
        raw["invoice_number"]["pages"] = [1, 1]
    with pytest.raises(ValidationError):
        InvoiceCandidate.model_validate(raw)


@pytest.mark.parametrize("mutation", ["human", "page", "quote", "wrong_value"])
def test_provider_cannot_claim_unsupported_sources(mutation: str) -> None:
    value = candidate()
    field = value.invoice_number
    if mutation == "human":
        field.status = "human"
    elif mutation == "page":
        field.pages = [20]
    elif mutation == "quote":
        field.quote = "not in document"
    else:
        field.value = "invented"
    with pytest.raises(ValueError):
        validate_sources(value, {1: ROWS[0]["source_pages"]["1"]}, provider=True)


def test_evaluation_denominators_missing_hallucinations_and_bad_predictions(tmp_path: Path) -> None:
    empty = evaluate(GOLDEN, None)
    assert empty["cases"] == 100 and empty["invoice_number"]["accuracy"] is None
    path = tmp_path / "predictions.jsonl"
    path.write_text("\n".join(json.dumps({"id": r["id"], "output": r["expected"]}) for r in ROWS))
    report = evaluate(GOLDEN, path)
    assert report["invoice_number"]["known"] == 95
    assert report["invoice_number"]["accuracy"] == report["grand_total"]["accuracy"] == 1
    assert report["line_items"]["micro_f1"] == 1
    assert report["timed_documents"] == 0 and report["live_provider_cost"] is None
    path.write_text(json.dumps({"id": ROWS[0]["id"], "output": {"invalid": True}}))
    report = evaluate(GOLDEN, path)
    assert report["invalid_schema_or_evidence"] == 1 and report["missing_predictions"] == 99


def test_evaluator_rejects_correct_values_with_forged_quotes(tmp_path: Path) -> None:
    raw = deepcopy(ROWS[0]["expected"])
    raw["invoice_number"]["quote"] = "fabricated source"
    predictions = tmp_path / "forged.jsonl"
    predictions.write_text(json.dumps({"id": ROWS[0]["id"], "output": raw}))
    report = evaluate(GOLDEN, predictions)
    assert report["invalid_schema_or_evidence"] == 1
    assert report["invoice_number"]["correct"] == report["grand_total"]["correct"] == 0
    assert report["line_items"]["tp"] == 0
