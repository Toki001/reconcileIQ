from decimal import ROUND_HALF_UP, Decimal, localcontext

# Explicit supported subset. Unknown currencies fail closed; never assume two decimals.
CURRENCY_EXPONENTS = {"USD": 2, "EUR": 2, "PHP": 2, "JPY": 0, "KWD": 3}
MAX_MINOR = 9_007_199_254_740_991


def to_minor(amount: Decimal, currency: str) -> int:
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
        raise ValueError("Amount must be a nonnegative finite Decimal")
    if currency not in CURRENCY_EXPONENTS:
        raise ValueError("Unsupported currency")
    if amount > Decimal(MAX_MINOR).scaleb(-CURRENCY_EXPONENTS[currency]):
        raise ValueError("Amount exceeds the supported safe integer range")
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + 4)
        scaled = amount * (10 ** CURRENCY_EXPONENTS[currency])
        return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))
