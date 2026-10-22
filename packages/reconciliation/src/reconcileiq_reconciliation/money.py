from decimal import ROUND_HALF_UP, Decimal, localcontext

# Explicit supported subset. Unknown currencies fail closed; never assume two decimals.
CURRENCY_EXPONENTS = {"USD": 2, "EUR": 2, "PHP": 2, "JPY": 0, "KWD": 3}
MAX_MINOR = 9_007_199_254_740_991


def to_minor(amount: Decimal, currency: str) -> int:
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
        raise ValueError("Amount must be a nonnegative finite Decimal")
    if currency not in CURRENCY_EXPONENTS:
        raise ValueError("Unsupported currency")
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + 4)
        if amount > Decimal(MAX_MINOR).scaleb(-CURRENCY_EXPONENTS[currency]):
            raise ValueError("Amount exceeds the supported safe integer range")
        scaled = amount * (10 ** CURRENCY_EXPONENTS[currency])
        return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def line_total_minor(quantity: Decimal, unit_price_minor: int) -> int:
    if (
        not quantity.is_finite()
        or quantity <= 0
        or type(unit_price_minor) is not int
        or not 0 <= unit_price_minor <= MAX_MINOR
    ):
        raise ValueError("Positive finite quantity and safe integer unit price are required")
    with localcontext() as context:
        context.prec = 50
        total = (quantity * unit_price_minor).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if total > MAX_MINOR:
        raise ValueError("Line total exceeds the safe integer range")
    return int(total)


def validate_header_totals(
    lines: list[int], subtotal: int, discount: int, tax: int, total: int
) -> None:
    if any(
        type(value) is not int or not 0 <= value <= MAX_MINOR
        for value in [*lines, subtotal, discount, tax, total]
    ):
        raise ValueError("All header and line amounts must be safe nonnegative integers")
    if sum(lines) != subtotal:
        raise ValueError("Subtotal must equal the sum of line totals")
    if discount > subtotal or total != subtotal - discount + tax:
        raise ValueError("Grand total must equal subtotal minus discount plus exclusive tax")
