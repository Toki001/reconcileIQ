"""Exact matching policy. Partial billing uses explicit proportional header allocation.

No database, network, provider or approval decisions belong in this module.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from reconcileiq_reconciliation.accounting import (
    LineAmounts,
    MonetaryTolerance,
    TaxMode,
    calculate_line,
    normalize_invoice_number,
    validate_invoice,
)
from reconcileiq_reconciliation.money import MAX_MINOR

Outcome = Literal[
    "matched", "unmatched", "discrepancy", "duplicate", "needs_review", "extraction_failed"
]


@dataclass(frozen=True)
class LineFacts:
    reference: str | None
    description: str
    quantity: Decimal
    unit_price_minor: int
    gross_minor: int
    discount_minor: int
    tax_rate: Decimal
    tax_minor: int
    total_minor: int


@dataclass(frozen=True)
class InvoiceFacts:
    number: str
    purchase_order_reference: str
    currency: str
    tax_mode: TaxMode
    subtotal_minor: int
    discount_minor: int
    tax_minor: int
    total_minor: int
    lines: tuple[LineFacts, ...]


@dataclass(frozen=True)
class OrderLine:
    reference: str
    description: str
    quantity: Decimal
    unit_price_minor: int


@dataclass(frozen=True)
class OrderFacts:
    reference: str
    currency: str
    tax_mode: TaxMode
    subtotal_minor: int
    discount_minor: int
    tax_minor: int
    total_minor: int
    lines: tuple[OrderLine, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Difference:
    code: str
    field: str
    expected: str
    observed: str


@dataclass(frozen=True)
class MatchResult:
    outcome: Outcome
    differences: tuple[Difference, ...]


def proportional(amount: int, part: int, whole: int) -> int:
    """Allocate header discount/tax for partial invoicing, with one HALF_UP rounding."""
    with localcontext() as context:
        context.prec = 50
        return (
            int((Decimal(amount) * part / whole).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            if whole
            else 0
        )


def reconcile(
    invoice: InvoiceFacts | None,
    order: OrderFacts | None,
    *,
    vendor_matches: bool | None,
    duplicate: bool = False,
    received: dict[str, Decimal] | None = None,
    policy: Literal["two_way_exact_v1", "three_way_exact_v1"] = "three_way_exact_v1",
    tolerance_minor: int = 0,
) -> MatchResult:
    def stop(outcome: Outcome, code: str, expected: str, observed: str) -> MatchResult:
        return MatchResult(outcome, (Difference(code, "invoice", expected, observed),))

    if duplicate:
        return stop(
            "duplicate",
            "DUPLICATE_INVOICE_NUMBER",
            "unique supplier invoice",
            "existing supplier key",
        )
    if invoice is None:
        return stop(
            "needs_review",
            "MISSING_REQUIRED_FIELDS",
            "complete observed or human values",
            "missing/inferred fields",
        )
    try:
        normalize_invoice_number(invoice.number)
        amounts = []
        for line in invoice.lines:
            calculated = calculate_line(
                line.quantity,
                line.unit_price_minor,
                line.discount_minor,
                line.tax_rate,
                invoice.tax_mode,
            )
            observed = LineAmounts(
                line.gross_minor, line.discount_minor, line.tax_minor, line.total_minor
            )
            if calculated != observed:
                raise ValueError("Line arithmetic disagrees")
            amounts.append(observed)
        validate_invoice(
            amounts,
            invoice.subtotal_minor,
            invoice.discount_minor,
            invoice.tax_minor,
            invoice.total_minor,
            invoice.tax_mode,
        )
        tolerance = MonetaryTolerance(invoice.currency, tolerance_minor)
    except ValueError:
        return stop(
            "needs_review",
            "INVOICE_TOTAL_INCONSISTENCY",
            "exact currency/line/header arithmetic",
            "invalid invoice values",
        )
    if vendor_matches is None:
        return stop(
            "needs_review",
            "SUPPLIER_AMBIGUOUS",
            "one known supplier",
            "missing or ambiguous supplier",
        )
    if not vendor_matches:
        return stop(
            "unmatched", "SUPPLIER_MISMATCH", "purchase order supplier", "different supplier"
        )
    if order is None:
        return stop(
            "unmatched",
            "PURCHASE_ORDER_NOT_FOUND",
            "known purchase order reference",
            "no matching order",
        )
    if invoice.purchase_order_reference not in (order.reference, *order.aliases):
        return stop(
            "unmatched", "PO_REFERENCE_MISMATCH", order.reference, invoice.purchase_order_reference
        )
    if invoice.currency != order.currency:
        return stop("needs_review", "CURRENCY_MISMATCH", order.currency, invoice.currency)
    if invoice.tax_mode != order.tax_mode:
        return stop("needs_review", "TAX_MODE_MISMATCH", order.tax_mode, invoice.tax_mode)
    differences: list[Difference] = []

    def compare(code: str, field: str, expected: int, observed: int) -> None:
        if not tolerance.accepts(expected, observed, invoice.currency):
            differences.append(Difference(code, field, str(expected), str(observed)))

    used: set[str] = set()
    expected_gross = 0
    for index, line in enumerate(invoice.lines):
        field = f"lines[{index}]"
        options = (
            [row for row in order.lines if row.reference == line.reference]
            if line.reference
            else [
                row
                for row in order.lines
                if row.description.strip().casefold() == line.description.strip().casefold()
            ]
        )
        if len(options) != 1:
            differences.append(
                Difference(
                    "LINE_NOT_ON_PO" if not options else "LINE_REFERENCE_AMBIGUOUS",
                    field,
                    "one exact PO line",
                    line.reference or line.description,
                )
            )
            continue
        original = options[0]
        if original.reference in used:
            differences.append(
                Difference("DUPLICATE_LINE_REFERENCE", field, "unique PO line", original.reference)
            )
            continue
        used.add(original.reference)
        if line.quantity > original.quantity:
            differences.append(
                Difference("QUANTITY_EXCEEDS_PO", field, str(original.quantity), str(line.quantity))
            )
        compare("UNIT_PRICE_MISMATCH", field, original.unit_price_minor, line.unit_price_minor)
        try:
            expected_gross += calculate_line(
                line.quantity, original.unit_price_minor, 0, Decimal(0), "exclusive"
            ).gross_minor
            if expected_gross > MAX_MINOR:
                raise ValueError("Comparison exceeds supported bounds")
        except ValueError:
            return stop("needs_review", "COMPARISON_OUT_OF_RANGE", "safe amounts", "overflow")
        if policy == "three_way_exact_v1":
            available = (received or {}).get(original.reference, Decimal(0))
            if line.quantity > available:
                differences.append(
                    Difference(
                        "QUANTITY_EXCEEDS_RECEIVED", field, str(available), str(line.quantity)
                    )
                )
    expected_discount = proportional(order.discount_minor, expected_gross, order.subtotal_minor)
    expected_net = max(0, expected_gross - expected_discount)
    expected_tax = proportional(
        order.tax_minor, expected_net, order.subtotal_minor - order.discount_minor
    )
    expected_total = expected_net + (expected_tax if invoice.tax_mode == "exclusive" else 0)
    if any(value > MAX_MINOR for value in (expected_discount, expected_tax, expected_total)):
        return stop("needs_review", "COMPARISON_OUT_OF_RANGE", "safe amounts", "overflow")
    compare("SUBTOTAL_MISMATCH", "subtotal_minor", expected_gross, invoice.subtotal_minor)
    compare("DISCOUNT_MISMATCH", "discount_minor", expected_discount, invoice.discount_minor)
    compare("UNEXPECTED_TAX_AMOUNT", "tax_minor", expected_tax, invoice.tax_minor)
    compare("GRAND_TOTAL_MISMATCH", "total_minor", expected_total, invoice.total_minor)
    if differences:
        return MatchResult("discrepancy", tuple(differences))
    return MatchResult("matched", ())
