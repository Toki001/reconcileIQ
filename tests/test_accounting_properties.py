"""Independent integer/rational oracles; generated values never pass through floats."""

import ast
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from reconcileiq_reconciliation.accounting import calculate_line, validate_invoice
from reconcileiq_reconciliation.money import CURRENCY_EXPONENTS, MAX_MINOR, to_minor

settings.register_profile("reconcileiq", max_examples=200, derandomize=True, deadline=None)
settings.load_profile("reconcileiq")


def rounded(value: Fraction) -> int:
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


@given(
    quantity=st.integers(1, 10_000_000),
    price=st.integers(0, 10_000_000),
    discount_fraction=st.integers(0, 1000),
    rate=st.integers(0, 1_000_000),
    mode=st.sampled_from(["inclusive", "exclusive"]),
)
def test_line_rounding_matches_rational_oracle(
    quantity: int,
    price: int,
    discount_fraction: int,
    rate: int,
    mode: Literal["inclusive", "exclusive"],
) -> None:
    gross = rounded(Fraction(quantity * price, 1_000_000))
    discount = gross * discount_fraction // 1000
    base = gross - discount
    tax = rounded(Fraction(base * rate, 1_000_000 if mode == "exclusive" else 1_000_000 + rate))
    # Small ambient precision must not change authoritative arithmetic.
    with localcontext() as context:
        context.prec = 6
        result = calculate_line(
            Decimal(f"{quantity}e-6"), price, discount, Decimal(f"{rate}e-6"), mode
        )
    assert result.gross_minor == gross
    assert result.tax_minor == tax
    assert result.total_minor == base + (tax if mode == "exclusive" else 0)
    validate_invoice(
        [result, result], gross * 2, discount * 2, tax * 2, result.total_minor * 2, mode
    )
    with pytest.raises(ValueError):
        validate_invoice([result], gross, discount, tax, result.total_minor + 1, mode)


@given(
    minor=st.integers(0, MAX_MINOR),
    currency=st.sampled_from(list(CURRENCY_EXPONENTS)),
)
def test_every_supported_currency_round_trips_exact_minor_units(minor: int, currency: str) -> None:
    amount = Decimal(f"{minor}e-{CURRENCY_EXPONENTS[currency]}")
    with localcontext() as context:
        context.prec = 6
        assert to_minor(amount, currency) == minor


@given(
    units=st.integers(0, 1_000_000_000),
    fraction=st.integers(0, 9999),
    currency=st.sampled_from(list(CURRENCY_EXPONENTS)),
)
def test_currency_half_up_matches_integer_oracle(units: int, fraction: int, currency: str) -> None:
    value = Decimal(f"{units * 10000 + fraction}e-{CURRENCY_EXPONENTS[currency] + 4}")
    assert to_minor(value, currency) == rounded(Fraction(units * 10000 + fraction, 10000))


@pytest.mark.parametrize("currency", list(CURRENCY_EXPONENTS))
def test_safe_integer_boundary_is_independent_of_ambient_decimal_precision(currency: str) -> None:
    amount = Decimal(f"{MAX_MINOR}e-{CURRENCY_EXPONENTS[currency]}")
    with localcontext() as context:
        context.prec = 6
        assert to_minor(amount, currency) == MAX_MINOR
        with pytest.raises(ValueError):
            to_minor(Decimal(f"{MAX_MINOR + 1}e-{CURRENCY_EXPONENTS[currency]}"), currency)


def test_authoritative_arithmetic_has_no_float_literals_or_conversions() -> None:
    root = Path("packages/reconciliation/src/reconcileiq_reconciliation")
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text())
        assert not any(
            isinstance(node, ast.Constant) and isinstance(node.value, float)
            for node in ast.walk(tree)
        ), source
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
            for node in ast.walk(tree)
        ), source
