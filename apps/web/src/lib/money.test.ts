import { formatMoney } from "./money";

describe("exact money presentation", () => {
  test.each([
    [12345, "USD", "$123.45"],
    [123, "JPY", "¥123"],
    [1234, "KWD", "KWD 1.234"],
    [0, "USD", "$0.00"],
    [Number.MAX_SAFE_INTEGER, "USD", "$90,071,992,547,409.91"],
  ])("formats %s %s", (minor, currency, expected) => {
    expect(formatMoney(minor, currency)).toBe(expected);
  });
  test.each([NaN, 1.5, -1, Number.MAX_SAFE_INTEGER + 1])(
    "rejects invalid amount %s",
    (value) => {
      expect(() => formatMoney(value, "USD")).toThrow();
    },
  );
  test("rejects an unknown currency", () => {
    expect(() => formatMoney(100, "XXX")).toThrow();
  });
});
