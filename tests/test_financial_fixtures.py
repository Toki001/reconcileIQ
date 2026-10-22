import hashlib
import json
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reconcileiq_database.fixture_data import SCENARIOS, dataset, generate_fixtures, money_text


def test_all_200_pdfs_are_parseable_and_match_deterministic_ground_truth() -> None:
    root = Path("tests/fixtures/financial")
    generate_fixtures(root, check=True)
    data = dataset()
    assert len(data["vendors"]) == 20 and len(data["purchase_orders"]) == 100
    assert len(data["invoices"]) == 200
    assert {invoice["scenario"] for invoice in data["invoices"]} == set(SCENARIOS)
    assert {invoice["currency"] for invoice in data["invoices"]} == {
        "USD",
        "EUR",
        "PHP",
        "JPY",
        "KWD",
    }
    page_counts = set()
    for invoice in data["invoices"]:
        pdf = (root / f"invoice-{invoice['index'] + 1:04}.pdf").read_bytes()
        assert hashlib.sha256(pdf).hexdigest() == invoice["pdf_sha256"]
        reader = PdfReader(BytesIO(pdf), strict=True)
        text = reader.pages[0].extract_text()
        assert invoice["supplier_invoice_number"] in text
        assert money_text(invoice["amount_minor"], invoice["currency"]) in text
        assert "SYNTHETIC FIXTURE - NO PAYMENT" in text
        page_counts.add(len(reader.pages))
    assert page_counts == {1, 2, 3}


def test_fixture_generation_is_repeatable_and_has_no_financial_floats(tmp_path: Path) -> None:
    generate_fixtures(tmp_path)
    before = {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    generate_fixtures(tmp_path)
    assert before == {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    parsed = json.loads((tmp_path / "manifest.json").read_text())
    for invoice in parsed["invoices"]:
        assert type(invoice["amount_minor"]) is int
        assert all(
            isinstance(line["quantity"], str) and type(line["unit_price_minor"]) is int
            for line in invoice["lines"]
        )
