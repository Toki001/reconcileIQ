from dataclasses import replace
from decimal import Decimal as D
from typing import cast

from reconcileiq_reconciliation.accounting import TaxMode
from reconcileiq_reconciliation.matching import (
    InvoiceFacts,
    LineFacts,
    OrderFacts,
    OrderLine,
    proportional,
    reconcile,
)
from reconcileiq_reconciliation.money import MAX_MINOR

LINE = LineFacts("SKU", "Widget", D(2), 100, 200, 0, D(0), 0, 200)
INVOICE = InvoiceFacts("INV-001", "PO-1", "USD", "exclusive", 200, 0, 0, 200, (LINE,))
ORDER = OrderFacts(
    "PO-1", "USD", "exclusive", 200, 0, 0, 200, (OrderLine("SKU", "Widget", D(2), 100),)
)


def codes(
    invoice: InvoiceFacts | None = INVOICE, order: OrderFacts | None = ORDER, **kwargs: object
) -> set[str]:
    # Typed public API calls in tests below exercise all branches; this helper only projects codes.
    options = {"vendor_matches": True, "received": {"SKU": D(2)}, **kwargs}
    return {d.code for d in reconcile(invoice, order, **options).differences}  # type: ignore[arg-type]


def test_exact_two_and_three_way_partial_inclusive_discount_tax() -> None:
    assert not codes()
    assert not codes(policy="two_way_exact_v1", received={})
    assert not codes(
        replace(INVOICE, lines=(replace(LINE, reference=None, description=" widget "),))
    )
    assert proportional(1, 1, 2) == 1 and proportional(9, 0, 0) == 0
    partial = replace(
        INVOICE,
        subtotal_minor=100,
        total_minor=100,
        lines=(replace(LINE, quantity=D(1), gross_minor=100, total_minor=100),),
    )
    assert not codes(partial)
    for mode, total, rate in (("exclusive", 198, D("0.1")), ("inclusive", 180, D("0.1"))):
        tax = 18 if mode == "exclusive" else 16
        line = replace(LINE, discount_minor=20, tax_rate=rate, tax_minor=tax, total_minor=total)
        inv = replace(
            INVOICE,
            tax_mode=cast(TaxMode, mode),
            discount_minor=20,
            tax_minor=tax,
            total_minor=total,
            lines=(line,),
        )
        order = replace(
            ORDER, tax_mode=cast(TaxMode, mode), discount_minor=20, tax_minor=tax, total_minor=total
        )
        assert not codes(inv, order)


def test_missing_ambiguous_duplicate_currency_and_arithmetic() -> None:
    assert codes(duplicate=True) == {"DUPLICATE_INVOICE_NUMBER"}
    assert codes(None) == {"MISSING_REQUIRED_FIELDS"}
    assert codes(vendor_matches=None) == {"SUPPLIER_AMBIGUOUS"}
    assert codes(vendor_matches=False) == {"SUPPLIER_MISMATCH"}
    assert codes(order=None) == {"PURCHASE_ORDER_NOT_FOUND"}
    assert codes(replace(INVOICE, purchase_order_reference="other")) == {"PO_REFERENCE_MISMATCH"}
    assert codes(replace(INVOICE, currency="EUR")) == {"CURRENCY_MISMATCH"}
    assert codes(order=replace(ORDER, tax_mode="inclusive")) == {"TAX_MODE_MISMATCH"}
    assert codes(replace(INVOICE, total_minor=201)) == {"INVOICE_TOTAL_INCONSISTENCY"}
    assert codes(replace(INVOICE, lines=(replace(LINE, total_minor=201),))) == {
        "INVOICE_TOTAL_INCONSISTENCY"
    }
    assert codes(replace(INVOICE, number="!!!")) == {"INVOICE_TOTAL_INCONSISTENCY"}
    assert codes(replace(INVOICE, currency="ZZZ")) == {"INVOICE_TOTAL_INCONSISTENCY"}


def test_explainable_line_resolution_discrepancies_and_receipts() -> None:
    assert "LINE_NOT_ON_PO" in codes(replace(INVOICE, lines=(replace(LINE, reference="other"),)))
    assert "LINE_REFERENCE_AMBIGUOUS" in codes(
        replace(INVOICE, lines=(replace(LINE, reference=None),)),
        replace(ORDER, lines=ORDER.lines * 2),
    )
    double = replace(INVOICE, subtotal_minor=400, total_minor=400, lines=(LINE, LINE))
    assert "DUPLICATE_LINE_REFERENCE" in codes(double)
    assert codes(received={}) == {"QUANTITY_EXCEEDS_RECEIVED"}
    assert "QUANTITY_EXCEEDS_PO" in codes(
        order=replace(ORDER, lines=(replace(ORDER.lines[0], quantity=D(1)),))
    )
    assert "UNIT_PRICE_MISMATCH" in codes(
        order=replace(ORDER, lines=(replace(ORDER.lines[0], unit_price_minor=101),))
    )
    assert "DISCOUNT_MISMATCH" in codes(order=replace(ORDER, discount_minor=10))
    assert "UNEXPECTED_TAX_AMOUNT" in codes(order=replace(ORDER, tax_minor=20))
    assert not codes(order=replace(ORDER, tax_minor=1), tolerance_minor=1)


def test_comparison_overflow_is_permanent_manual_review() -> None:
    huge = replace(ORDER, lines=(replace(ORDER.lines[0], unit_price_minor=MAX_MINOR),))
    assert codes(order=huge) == {"COMPARISON_OUT_OF_RANGE"}
    # Sum overflow, even when each individually computed PO line is in range.
    invoice = replace(
        INVOICE, subtotal_minor=400, total_minor=400, lines=(LINE, replace(LINE, reference="SKU2"))
    )
    huge = replace(
        ORDER,
        lines=(
            OrderLine("SKU", "Widget", D(2), MAX_MINOR // 3),
            OrderLine("SKU2", "Other", D(2), MAX_MINOR // 3),
        ),
    )
    assert codes(invoice, huge) == {"COMPARISON_OUT_OF_RANGE"}
    assert codes(order=replace(ORDER, tax_minor=MAX_MINOR)) == {"COMPARISON_OUT_OF_RANGE"}


def test_1000_known_price_mismatches_never_match() -> None:
    for delta in range(1, 1001):
        changed = replace(
            LINE,
            unit_price_minor=100 + delta,
            gross_minor=(100 + delta) * 2,
            total_minor=(100 + delta) * 2,
        )
        invoice = replace(
            INVOICE,
            lines=(changed,),
            subtotal_minor=changed.gross_minor,
            total_minor=changed.total_minor,
        )
        assert (
            reconcile(invoice, ORDER, vendor_matches=True, policy="two_way_exact_v1").outcome
            == "discrepancy"
        )
