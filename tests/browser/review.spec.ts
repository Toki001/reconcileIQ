import { apiUrl, envFile } from "./env";
import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
test.use({ trace: "off" });
function token(role: string) {
  const line = readFileSync(envFile, "utf8")
    .split("\n")
    .find((row) => row.startsWith(`RECONCILE_${role}_TOKEN=`));
  if (!line) throw new Error("Local credential unavailable");
  return line.slice(line.indexOf("=") + 1).replace(/^"|"$/g, "");
}
test("real reviewer uploads, corrects, reconciles and accepts an invoice", async ({
  page,
  request,
  baseURL,
}) => {
  test.setTimeout(90000);
  const suffix = randomUUID().slice(0, 8);
  const name = `Synthetic UI Supplies ${suffix}`,
    reference = `UI-PO-${suffix}`,
    number = `UI-INV-${suffix}`;
  const label = JSON.parse(
    readFileSync("packages/ai/evals/golden.jsonl", "utf8").split("\n")[0],
  );
  label.expected.vendor_name.value = name;
  label.expected.invoice_number.value = number;
  label.expected.purchase_order_reference.value = reference;
  const file = `.cache/m06-browser-${suffix}.pdf`;
  execFileSync(".venv/bin/python", [
    "-c",
    "import sys;sys.path.insert(0,'scripts');from golden_fixtures import text_pdf;from pathlib import Path;Path(sys.argv[1]).write_bytes(text_pdf([['SYNTHETIC NO PAYMENT', 'invoice_number: '+sys.argv[2], 'vendor_name: '+sys.argv[3], 'purchase_order_reference: '+sys.argv[4], 'currency: USD', 'tax_mode: exclusive', 'invoice_date: 2026-10-01', 'due_date: 2026-11-01', 'ITEM-1 Synthetic component qty 2 unit 100 gross 200 discount 0 tax_rate 0 tax 0 total 200', 'subtotal_minor: 200 discount_minor: 0 tax_minor: 0 total_minor: 200']],0))",
    file,
    number,
    name,
    reference,
  ]);
  const po = await request.post(apiUrl + "/v1/purchase-orders", {
    headers: {
      Authorization: `Bearer ${token("ADMIN")}`,
      "Idempotency-Key": `ui-po-${suffix}`,
    },
    data: {
      reference,
      vendor: name,
      description: "Synthetic browser review order",
      currency: "USD",
      issued_on: "2026-10-01",
      tax_mode: "exclusive",
      lines: [
        {
          id: "ITEM-1",
          description: "Synthetic component",
          quantity: "2",
          unit_price_minor: 100,
          line_total_minor: 200,
        },
      ],
      subtotal_minor: 200,
      discount_minor: 0,
      tax_minor: 0,
      amount_minor: 200,
    },
  });
  expect(po.status()).toBe(201);
  const order = await po.json();
  const receipt = await request.post(apiUrl + "/v1/goods-receipts", {
    headers: {
      Authorization: `Bearer ${token("ADMIN")}`,
      "Idempotency-Key": `ui-receipt-${suffix}`,
    },
    data: {
      purchase_order_id: order.id,
      reference: `UI-GR-${suffix}`,
      received_on: "2026-10-02",
      lines: [{ purchase_order_line_id: "ITEM-1", quantity: "2" }],
    },
  });
  expect(receipt.status()).toBe(201);
  const login = await page.request.post(`${baseURL}/api/session`, {
    headers: { Origin: baseURL! },
    data: { token: token("REVIEWER") },
  });
  expect(login.status()).toBe(200);
  await page.goto("/review");
  await expect(
    page.getByText("Live AI unavailable", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("Upload an invoice PDF").setInputFiles(file);
  await page
    .getByRole("button", { name: "Upload and extract text", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: /Job .*succeeded/ }),
  ).toBeVisible({ timeout: 40000 });
  await expect(
    page.getByRole("heading", { name: "Original document", exact: true }),
  ).toBeVisible();
  await expect(page.getByAltText("Original invoice page 1")).toBeVisible({
    timeout: 15000,
  });
  expect(
    await page
      .getByAltText("Original invoice page 1")
      .evaluate((image: HTMLImageElement) => image.naturalWidth),
  ).toBeGreaterThan(0);
  for (const [field, evidence] of Object.entries(label.expected) as [
    string,
    { value: string | number | null },
  ][]) {
    if (
      field === "lines" ||
      evidence.value === null ||
      field === "vendor_identifier"
    )
      continue;
    await page
      .getByLabel(`Invoice ${field}`, { exact: true })
      .fill(String(evidence.value));
  }
  for (const [field, evidence] of Object.entries(label.expected.lines[0]) as [
    string,
    { value: string | number | null },
  ][])
    if (evidence.value !== null)
      await page
        .getByLabel(`Line 1 ${field}`, { exact: true })
        .fill(String(evidence.value));
  await page
    .getByLabel("Correction or review reason")
    .fill("Verified the original synthetic document and purchase order");
  await page
    .getByRole("button", { name: "Save correction", exact: true })
    .click();
  await expect(
    page.getByText("Human correction recorded as a new immutable candidate."),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: "Run deterministic reconciliation",
      exact: true,
    })
    .click();
  await expect(page.getByText("matched", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await page
    .getByRole("button", { name: "Accept matched review", exact: true })
    .click();
  await expect(
    page.getByText("Review accept recorded. No payment is initiated."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: new RegExp(name) }),
  ).toContainText("reviewed_accepted");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("review proxy rejects anonymous access and foreign-origin writes", async ({
  request,
  baseURL,
}) => {
  expect((await request.get(`${baseURL}/api/review/v1/me`)).status()).toBe(401);
  expect(
    (
      await request.post(`${baseURL}/api/session`, {
        headers: { Origin: "https://evil.invalid" },
        data: { token: "x".repeat(40) },
      })
    ).status(),
  ).toBe(403);
});
