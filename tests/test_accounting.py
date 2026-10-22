from decimal import Decimal

import pytest
from reconcileiq_reconciliation.accounting import (
    LineAmounts,
    MonetaryTolerance,
    calculate_line,
    exact_quantity,
    exact_rate,
    normalize_invoice_number,
    safe_minor,
    tax_minor,
    validate_invoice,
)
from reconcileiq_reconciliation.money import MAX_MINOR


@pytest.mark.parametrize("mode,tax,total", [("exclusive", 29, 269), ("inclusive", 26, 240)])
def test_discount_then_line_tax_with_explicit_rounding(mode: str, tax: int, total: int) -> None:
    result = calculate_line(Decimal("2.5"), 101, 13, Decimal(".12"), mode)  # type: ignore[arg-type]
    assert result == LineAmounts(253, 13, tax, total)
    validate_invoice([result], 253, 13, tax, total, mode)  # type: ignore[arg-type]


def test_sum_line_tax_rounding_instead_of_rounding_a_header_estimate() -> None:
    line = calculate_line(Decimal("1"), 1, 0, Decimal(".5"), "exclusive")
    assert line.tax_minor == 1
    validate_invoice([line, line], 2, 0, 2, 4, "exclusive")
    with pytest.raises(ValueError):
        validate_invoice([line, line], 2, 0, 1, 3, "exclusive")


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1000000000"),
        Decimal(".0000001"),
        0.1,
    ],
)
def test_quantity_precision_and_binary_floats_fail_closed(value: Decimal) -> None:
    with pytest.raises(ValueError):
        exact_quantity(value)


@pytest.mark.parametrize(
    "value", [Decimal("-1"), Decimal("1.1"), Decimal("NaN"), Decimal(".0000001"), 0.12]
)
def test_rate_precision_and_type_fail_closed(value: Decimal) -> None:
    with pytest.raises(ValueError):
        exact_rate(value)


@pytest.mark.parametrize("value", [-1, MAX_MINOR + 1, True, 1.2])
def test_minor_unit_boundaries_and_types(value: int) -> None:
    with pytest.raises(ValueError):
        safe_minor(value)


@pytest.mark.parametrize("gross,discount,mode", [(100, 101, "exclusive"), (100, 1, "ambiguous")])
def test_invalid_tax_inputs(gross: int, discount: int, mode: str) -> None:
    with pytest.raises(ValueError):
        tax_minor(gross, discount, Decimal(".12"), mode)


@pytest.mark.parametrize("number", ["", "   ", "INV:123", "日本-123", "A\n123", "x" * 81])
def test_invalid_supplier_numbers(number: str) -> None:
    with pytest.raises(ValueError):
        normalize_invoice_number(number)


def test_supplier_number_normalization_preserves_meaningful_punctuation() -> None:
    assert normalize_invoice_number(" inv-001 / a ") == "INV-001/A"
    assert normalize_invoice_number("inv001") != normalize_invoice_number("inv-001")


@pytest.mark.parametrize("currency", ["USD", "EUR", "PHP", "JPY", "KWD"])
def test_tolerance_is_explicit_in_minor_units_and_same_currency(currency: str) -> None:
    policy = MonetaryTolerance(currency, 1)
    assert policy.accepts(100, 101, currency)
    assert not policy.accepts(100, 102, currency)
    assert MonetaryTolerance(currency).accepts(100, 100, currency)
    with pytest.raises(ValueError):
        policy.accepts(100, 100, "XXX")


@pytest.mark.parametrize("currency,amount", [("XXX", 0), ("USD", -1)])
def test_invalid_tolerance(currency: str, amount: int) -> None:
    with pytest.raises(ValueError):
        MonetaryTolerance(currency, amount)


@pytest.mark.parametrize(
    "lines,subtotal,discount,tax,total,mode",
    [
        ([], 0, 0, 0, 0, "exclusive"),
        ([LineAmounts(100, 0, 0, 100)], 100, 0, 0, 100, "unknown"),
        ([LineAmounts(-1, 0, 0, 0)], 0, 0, 0, 0, "exclusive"),
        ([LineAmounts(100, 101, 0, 0)], 100, 101, 0, 0, "exclusive"),
        ([LineAmounts(100, 0, 1, 100)], 100, 0, 1, 100, "exclusive"),
        ([LineAmounts(100, 0, 101, 100)], 100, 0, 101, 100, "inclusive"),
        ([LineAmounts(100, 0, 0, 100)], 99, 0, 0, 100, "exclusive"),
    ],
)
def test_untrusted_invoice_aggregates_are_rejected(
    lines: list[LineAmounts], subtotal: int, discount: int, tax: int, total: int, mode: str
) -> None:
    with pytest.raises(ValueError):
        validate_invoice(lines, subtotal, discount, tax, total, mode)  # type: ignore[arg-type]
