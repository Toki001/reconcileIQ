const exponents: Record<string, number> = {
  USD: 2,
  EUR: 2,
  PHP: 2,
  JPY: 0,
  KWD: 3,
};

// Presentation only. All authoritative arithmetic stays in Python.
export function formatMoney(minor: number, currency: string): string {
  if (
    !Number.isSafeInteger(minor) ||
    minor < 0 ||
    !Object.hasOwn(exponents, currency)
  ) {
    throw new Error("Unsupported monetary value");
  }
  const exponent = exponents[currency];
  const digits = BigInt(minor)
    .toString()
    .padStart(exponent + 1, "0");
  const whole = exponent ? digits.slice(0, -exponent) : digits;
  const fractional = exponent ? digits.slice(-exponent) : "";
  // Format integer and fraction separately, preserving exact minor units even
  // near Number.MAX_SAFE_INTEGER. No floating-point division or rounding.
  return new Intl.NumberFormat("en-US", { style: "currency", currency })
    .formatToParts(BigInt(whole))
    .map((part) => (part.type === "fraction" ? fractional : part.value))
    .join("");
}
