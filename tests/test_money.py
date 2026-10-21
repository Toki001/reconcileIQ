from decimal import Decimal

import pytest
from reconcileiq_reconciliation.money import MAX_MINOR, to_minor


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
