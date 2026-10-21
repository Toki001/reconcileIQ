import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from reconcileiq_ai.evaluate import evaluate, read_rows
from reconcileiq_ai.schemas import UntrustedExtraction


@pytest.mark.parametrize("value", [1.5, "100", True, -1])
def test_untrusted_totals_must_be_nonnegative_integer_minor_units(value: object) -> None:
    with pytest.raises(ValidationError):
        UntrustedExtraction.model_validate(
            {
                "invoice_number": "demo",
                "currency": "USD",
                "total_minor": value,
                "source_pages": [1],
            }
        )


def test_offline_fixture_harness() -> None:
    report = evaluate(
        Path("packages/ai/evals/golden.jsonl"),
        Path("packages/ai/evals/mock-predictions.jsonl"),
    )
    assert report["cases"] == 3
    assert report["exact"] == 2
    assert report["expected_schema_rejections"] == 1
    assert report["failures"] == 0


def test_eval_detects_wrong_total_missing_case_and_extra_case(tmp_path: Path) -> None:
    original = Path("packages/ai/evals/mock-predictions.jsonl").read_text().splitlines()
    wrong = json.loads(original[0])
    wrong["output"]["total_minor"] = 1
    extra = json.loads(original[1])
    extra["id"] = "unexpected"
    predictions = tmp_path / "wrong.jsonl"
    predictions.write_text("\n".join([json.dumps(wrong), json.dumps(extra), original[2]]))
    report = evaluate(Path("packages/ai/evals/golden.jsonl"), predictions)
    assert report["failures"] == 3


def test_duplicate_fixture_id_is_an_error(tmp_path: Path) -> None:
    fixture = tmp_path / "duplicate.jsonl"
    fixture.write_text('{"id":"same"}\n{"id":"same"}\n')
    with pytest.raises(ValueError, match="Duplicate"):
        read_rows(fixture)
