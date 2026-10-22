/** @jest-environment node */
jest.mock("server-only", () => ({}), { virtual: true });
import { openSession, sealSession, sameOrigin } from "./review-session";
beforeEach(() => {
  process.env.RECONCILE_WEB_SESSION_SECRET = "a".repeat(64);
});
test("credential is encrypted and expires after thirty minutes", () => {
  const token = "reviewer-secret-" + "x".repeat(40);
  const sealed = sealSession(token, 1000);
  expect(sealed).not.toContain(token);
  expect(openSession(sealed, 1001)).toBe(token);
  expect(openSession(sealed, 1801000)).toBeNull();
});
test("tampered cookie cannot authenticate", () => {
  const sealed = sealSession("x".repeat(40));
  expect(openSession(sealed.slice(0, -4) + "AAAA")).toBeNull();
  expect(openSession("malformed")).toBeNull();
});
test("missing strong session key fails closed", () => {
  delete process.env.RECONCILE_WEB_SESSION_SECRET;
  expect(() => sealSession("x".repeat(40))).toThrow();
  expect(openSession("malformed")).toBeNull();
});
test("writes require exact origin", () => {
  expect(
    sameOrigin(
      new Request("http://localhost:3100/api/session", {
        headers: { Origin: "https://evil.invalid" },
      }),
    ),
  ).toBe(false);
  expect(
    sameOrigin(
      new Request("http://localhost:3100/api/session", {
        headers: { Origin: "http://localhost:3100" },
      }),
    ),
  ).toBe(true);
  expect(sameOrigin(new Request("http://localhost:3100/api/session"))).toBe(
    false,
  );
});

test("published Docker authority is checked against Host", () => {
  expect(
    sameOrigin(
      new Request("http://localhost:3000/api/session", {
        headers: { Host: "127.0.0.1:3100", Origin: "http://127.0.0.1:3100" },
      }),
    ),
  ).toBe(true);
  expect(
    sameOrigin(
      new Request("http://localhost:3000/api/session", {
        headers: { Host: "127.0.0.1:3100", Origin: "http://evil.invalid" },
      }),
    ),
  ).toBe(false);
});
