"""Evaluate supplied outputs against independent synthetic labels. Never calls a model."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reconcileiq_ai.schemas import UntrustedExtraction


def read_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("Each fixture needs a string id")
        if row["id"] in rows:
            raise ValueError("Duplicate fixture id")
        rows[row["id"]] = row
    if not rows:
        raise ValueError("Fixture set must not be empty")
    return rows


def evaluate(golden_path: Path, predictions_path: Path) -> dict[str, int | str]:
    golden, predictions = read_rows(golden_path), read_rows(predictions_path)
    exact, rejected, failed = 0, 0, 0
    for key, row in golden.items():
        prediction = predictions.get(key)
        expected = row["expected"]
        if prediction is None:
            failed += 1
            continue
        try:
            actual = UntrustedExtraction.model_validate(prediction["output"])
        except ValidationError:
            if expected is None:
                rejected += 1
            else:
                failed += 1
            continue
        if expected is None:
            failed += 1
        elif actual == UntrustedExtraction.model_validate(expected):
            exact += 1
        else:
            failed += 1
    failed += len(predictions.keys() - golden.keys())
    return {
        "mode": "synthetic-harness-only",
        "cases": len(golden),
        "exact": exact,
        "expected_schema_rejections": rejected,
        "failures": failed,
        "limitation": (
            "No model called. This verifies the harness, "
            "not extraction quality or injection resistance."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=Path("packages/ai/evals/golden.jsonl"))
    parser.add_argument(
        "--predictions", type=Path, default=Path("packages/ai/evals/mock-predictions.jsonl")
    )
    args = parser.parse_args()
    report = evaluate(args.golden, args.predictions)
    print(json.dumps(report, indent=2))
    raise SystemExit(1 if report["failures"] else 0)


if __name__ == "__main__":
    main()
