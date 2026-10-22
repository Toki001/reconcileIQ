"""Deterministic invoice arithmetic; no persistence, providers or payment decisions."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from reconcileiq_reconciliation.money import CURRENCY_EXPONENTS, MAX_MINOR, line_total_minor

TaxMode = Literal["exclusive", "inclusive"]


def safe_minor(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_MINOR:
        raise ValueError("Amount must be a nonnegative safe integer")
    return value


def exact_quantity(value: Decimal) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
        or value > Decimal("999999999.999999")
        or int(value.as_tuple().exponent) < -6
    ):
        raise ValueError("Quantity requires a positive Decimal with at most six fractional digits")
    return value


def exact_rate(value: Decimal) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or not 0 <= value <= 1
        or int(value.as_tuple().exponent) < -6
    ):
        raise ValueError("Tax rate requires Decimal 0..1 with at most six fractional digits")
    return value


def normalize_invoice_number(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9./_ -]{1,80}", value) or not value.replace(" ", ""):
        raise ValueError("Supplier invoice number must contain 1..80 supported ASCII characters")
    return value.replace(" ", "").upper()


def tax_minor(gross: int, discount: int, rate: Decimal, mode: str) -> int:
    safe_minor(gross)
    safe_minor(discount)
    exact_rate(rate)
    if discount > gross or mode not in {"exclusive", "inclusive"}:
        raise ValueError("Discount exceeds the line amount or tax mode is unsupported")
    with localcontext() as context:
        context.prec = 50
        base = Decimal(gross - discount)
        tax = base * rate if mode == "exclusive" else base * rate / (1 + rate)
        return safe_minor(int(tax.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


@dataclass(frozen=True)
class LineAmounts:
    gross_minor: int
    discount_minor: int
    tax_minor: int
    total_minor: int


def calculate_line(
    quantity: Decimal, unit_price: int, discount: int, rate: Decimal, mode: TaxMode
) -> LineAmounts:
    gross = line_total_minor(exact_quantity(quantity), safe_minor(unit_price))
    tax = tax_minor(gross, discount, rate, mode)
    total = safe_minor(gross - discount + (tax if mode == "exclusive" else 0))
    return LineAmounts(gross, discount, tax, total)


def validate_invoice(
    lines: list[LineAmounts], subtotal: int, discount: int, tax: int, total: int, mode: TaxMode
) -> None:
    for amount in (subtotal, discount, tax, total):
        safe_minor(amount)
    if not lines or mode not in {"inclusive", "exclusive"}:
        raise ValueError("Invoice requires lines and an explicit tax mode")
    for row in lines:
        for amount in (row.gross_minor, row.discount_minor, row.tax_minor, row.total_minor):
            safe_minor(amount)
        if (
            row.discount_minor > row.gross_minor
            or row.total_minor
            != row.gross_minor - row.discount_minor + (row.tax_minor if mode == "exclusive" else 0)
            or (mode == "inclusive" and row.tax_minor > row.total_minor)
        ):
            raise ValueError("Invoice line amounts are inconsistent")
    if (
        sum(row.gross_minor for row in lines) != subtotal
        or sum(row.discount_minor for row in lines) != discount
        or sum(row.tax_minor for row in lines) != tax
        or sum(row.total_minor for row in lines) != total
        or discount > subtotal
        or total != subtotal - discount + (tax if mode == "exclusive" else 0)
    ):
        raise ValueError("Invoice header does not reconcile exactly to its lines")


@dataclass(frozen=True)
class MonetaryTolerance:
    currency: str
    amount_minor: int = 0

    def __post_init__(self) -> None:
        if self.currency not in CURRENCY_EXPONENTS:
            raise ValueError("Unsupported currency")
        safe_minor(self.amount_minor)

    def accepts(self, expected: int, observed: int, currency: str) -> bool:
        if currency != self.currency:
            raise ValueError("Tolerance currency must match; conversion is prohibited")
        return abs(safe_minor(expected) - safe_minor(observed)) <= self.amount_minor
