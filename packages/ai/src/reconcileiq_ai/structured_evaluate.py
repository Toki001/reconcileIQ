"""Evaluate supplied structured predictions. No network, model calls or invented measurements."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from math import ceil
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from reconcileiq_reconciliation.accounting import normalize_invoice_number

from reconcileiq_ai.evaluate import read_rows
from reconcileiq_ai.invoice import InvoiceCandidate, validate_sources


def items(candidate: InvoiceCandidate) -> Counter[tuple[object, ...]]:
    # Fixed normalization: exact identifiers, stripped/case-folded descriptions,
    # exact Decimal quantities/rates, and integer minor-unit money. Unordered full tuples.
    def normalized(name: str, value: object) -> object:
        if isinstance(value, str) and name in {"quantity", "tax_rate"}:
            return Decimal(value)
        if isinstance(value, str) and name == "description":
            return value.strip().casefold()
        return value

    return Counter(
        tuple(normalized(name, getattr(line, name).value) for name in type(line).model_fields)
        for line in candidate.lines
    )


def invoice_number(value: str | None) -> str | None:
    try:
        return normalize_invoice_number(value) if value is not None else None
    except ValueError:
        return None


def evaluate(golden_path: Path, predictions_path: Path | None) -> dict[str, Any]:
    golden = read_rows(golden_path)
    predictions = read_rows(predictions_path) if predictions_path else {}
    correct_number = correct_total = known_number = known_total = missing_number = hallucinated = 0
    true_positive = false_positive = false_negative = invalid = absent = 0
    durations: list[float] = []
    input_tokens = output_tokens = 0
    for key, row in golden.items():
        expected = InvoiceCandidate.model_validate(row["expected"])
        known_number += expected.invoice_number.value is not None
        known_total += expected.total_minor.value is not None
        prediction = predictions.get(key)
        actual = None
        if prediction:
            try:
                actual = InvoiceCandidate.model_validate(prediction["output"])
                validate_sources(
                    actual, {int(k): v for k, v in row["source_pages"].items()}, provider=True
                )
            except (ValidationError, ValueError, KeyError):
                invalid += 1
                actual = None
            duration = prediction.get("processing_seconds")
            if (
                isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and 0 <= duration < 86400
            ):
                durations.append(float(duration))
            for name in ("input_tokens", "output_tokens"):
                token = prediction.get(name)
                if type(token) is int and token >= 0:
                    if name == "input_tokens":
                        input_tokens += token
                    else:
                        output_tokens += token
        else:
            absent += 1
        expected_items = items(expected)
        if actual is None:
            false_negative += expected_items.total()
            missing_number += expected.invoice_number.value is not None
            continue
        correct_number += expected.invoice_number.value is not None and invoice_number(
            expected.invoice_number.value
        ) == invoice_number(actual.invoice_number.value)
        correct_total += (
            expected.total_minor.value is not None
            and expected.total_minor.value == actual.total_minor.value
        )
        missing_number += (
            expected.invoice_number.value is not None and actual.invoice_number.value is None
        )
        hallucinated += (
            expected.invoice_number.value is None and actual.invoice_number.value is not None
        )
        actual_items = items(actual)
        true_positive += (expected_items & actual_items).total()
        false_positive += (actual_items - expected_items).total()
        false_negative += (expected_items - actual_items).total()
    denominator = 2 * true_positive + false_positive + false_negative
    measured = predictions_path is not None
    return {
        "mode": "supplied-predictions" if measured else "dataset-only-no-provider",
        "cases": len(golden),
        "predictions": len(predictions),
        "missing_predictions": absent,
        "unknown_prediction_ids": len(predictions.keys() - golden.keys()),
        "invalid_schema_or_evidence": invalid,
        "invoice_number": {
            "correct": correct_number,
            "known": known_number,
            "accuracy": correct_number / known_number if measured and known_number else None,
            "missing": missing_number if measured else None,
            "hallucinated": hallucinated,
        },
        "grand_total": {
            "correct": correct_total,
            "known": known_total,
            "accuracy": correct_total / known_total if measured and known_total else None,
        },
        "line_items": {
            "tp": true_positive,
            "fp": false_positive,
            "fn": false_negative if measured else None,
            "micro_f1": 2 * true_positive / denominator if measured and denominator else None,
        },
        "processing_p95_seconds": sorted(durations)[ceil(len(durations) * 0.95) - 1]
        if durations
        else None,
        "timed_documents": len(durations),
        "input_tokens_reported": input_tokens,
        "output_tokens_reported": output_tokens,
        "live_provider_cost": None,
        "limitation": (
            "Prediction provenance and timing must be reported separately. "
            "Fixture roundtrips do not measure live AI quality. No model called by this tool."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=Path("packages/ai/evals/golden.jsonl"))
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.golden, args.predictions), indent=2))


if __name__ == "__main__":
    main()
