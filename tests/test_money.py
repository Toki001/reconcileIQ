from decimal import Decimal

import pytest
from reconcileiq_reconciliation.money import (
    MAX_MINOR,
    line_total_minor,
    to_minor,
    validate_header_totals,
)


def test_fractional_quantity_line_rounding_and_header_totals() -> None:
    assert line_total_minor(Decimal("2.5"), 101) == 253
    validate_header_totals([253, 100], 353, 10, 20, 363)


@pytest.mark.parametrize("quantity,price", [("NaN", 1), ("0", 1), ("1", -1), ("2", MAX_MINOR)])
def test_invalid_or_overflowing_line_amounts(quantity: str, price: int) -> None:
    with pytest.raises(ValueError):
        line_total_minor(Decimal(quantity), price)


@pytest.mark.parametrize(
    "subtotal,discount,tax,total",
    [(-1, 0, 0, 0), (101, 0, 0, 101), (100, 101, 0, 0), (100, 0, 1, 100)],
)
def test_header_totals_fail_closed(subtotal: int, discount: int, tax: int, total: int) -> None:
    with pytest.raises(ValueError):
        validate_header_totals([100], subtotal, discount, tax, total)


@pytest.mark.parametrize(
    "value,currency,expected",
    [
        ("12.345", "USD", 1235),
        ("12.344", "EUR", 1234),
        ("0.005", "PHP", 1),
        ("120.5", "JPY", 121),
        ("1.2345", "KWD", 1235),
        ("0", "JPY", 0),
        ("90071992547409.91", "USD", MAX_MINOR),
    ],
)
def test_explicit_currency_exponent_and_rounding(value: str, currency: str, expected: int) -> None:
    assert to_minor(Decimal(value), currency) == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "90071992547409.92", "1E+1000000"])
def test_invalid_money_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        to_minor(Decimal(value), "USD")


def test_unknown_currency_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported currency"):
        to_minor(Decimal("1"), "XXX")


def test_binary_float_is_rejected() -> None:
    with pytest.raises(ValueError):
        to_minor(1.1, "USD")  # type: ignore[arg-type]
