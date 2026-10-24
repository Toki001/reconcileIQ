import { apiUrl, envFile } from "./env";
import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

test.use({ trace: "off" });

// Credentials stay in Playwright's Node process; never inject them into the page.
function token(role: string) {
  const line = readFileSync(envFile, "utf8")
    .split("\n")
    .find((value) =>
      value.startsWith(`RECONCILE_${role.toUpperCase()}_TOKEN=`),
    );
  if (!line) throw new Error("Local test credential is missing");
  return line.slice(line.indexOf("=") + 1).replace(/^"|"$/g, "");
}

function service(action: "pause" | "unpause") {
  execFileSync("docker", ["compose", action, "worker"], {
    stdio: "pipe",
    timeout: 15000,
  });
}

async function processingRow(page: Page, invoiceId: string) {
  const panel = page.getByRole("region", { name: "Invoice processing" });
  for (let number = 0; number < 20; number++) {
    await expect(panel.getByRole("table")).toBeVisible();
    const row = panel.getByRole("row").filter({ hasText: invoiceId });
    if (await row.count()) return row;
    const next = panel.getByRole("button", { name: "Next page", exact: true });
    if (!(await next.count())) break;
    await next.click();
  }
  throw new Error("Persisted synthetic processing record was not found");
}

test("API upload survives browser refresh, then real worker completion persists", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const prefix = `m05-browser-${randomUUID()}`;
  const data = readFileSync("tests/fixtures/financial/invoice-0001.pdf");
  const auth = { Authorization: `Bearer ${token("reviewer")}` };
  const root = apiUrl;
  service("pause");
  let workerPaused = true;
  try {
    const grantResponse = await request.post(
      `${root}/v1/invoices/upload-requests`,
      {
        headers: { ...auth, "Idempotency-Key": `${prefix}-reserve` },
        data: {
          filename: `${prefix}.pdf`,
          content_type: "application/pdf",
          size_bytes: data.length,
          sha256: createHash("sha256").update(data).digest("hex"),
        },
      },
    );
    expect(grantResponse.status()).toBe(201);
    const grant = await grantResponse.json();
    const put = await request.put(grant.upload_url, {
      headers: grant.required_headers,
      data,
    });
    expect(put.status()).toBe(200);
    const completion = await request.post(
      `${root}/v1/invoices/${grant.invoice_id}/upload-complete`,
      {
        headers: { ...auth, "Idempotency-Key": `${prefix}-complete` },
        data: { expected_version: 1 },
      },
    );
    expect(completion.status()).toBe(202);
    const accepted = await completion.json();
    await page.goto("/invoices");
    await expect(await processingRow(page, grant.invoice_id)).toContainText(
      "Pending dispatch",
    );
    await page.reload();
    await expect(await processingRow(page, grant.invoice_id)).toContainText(
      "Pending dispatch",
    );
    service("unpause");
    workerPaused = false;
    await expect(await processingRow(page, grant.invoice_id)).toContainText(
      "Completed",
      { timeout: 30000 },
    );
    const state = await request.get(`${root}${accepted.status_url}`, {
      headers: auth,
    });
    expect((await state.json()).state).toBe("succeeded");
    const invoice = await request.get(
      `${root}/v1/invoices/${grant.invoice_id}`,
      { headers: auth },
    );
    expect(await invoice.json()).toMatchObject({
      status: "extracted",
      amount_minor: null,
      currency: null,
    });
    await page.reload();
    await expect(await processingRow(page, grant.invoice_id)).toContainText(
      "Completed",
    );
    // The browser is an auditor; the write workflow above is explicitly API-assisted.
    expect(
      await page.getByRole("button", { name: /replay|retry|upload/i }).count(),
    ).toBe(0);
  } finally {
    if (workerPaused) service("unpause");
  }
});

test("malformed digital PDF shows retained failure and denied auditor replay", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  const prefix = `m05-browser-bad-${randomUUID()}`;
  const data = Buffer.from("%PDF-1.4 synthetic malformed PDF");
  const root = apiUrl;
  const auth = { Authorization: `Bearer ${token("reviewer")}` };
  const response = await request.post(`${root}/v1/invoices/upload-requests`, {
    headers: { ...auth, "Idempotency-Key": `${prefix}-reserve` },
    data: {
      filename: `${prefix}.pdf`,
      content_type: "application/pdf",
      size_bytes: data.length,
      sha256: createHash("sha256").update(data).digest("hex"),
    },
  });
  expect(response.status()).toBe(201);
  const grant = await response.json();
  expect(
    (
      await request.put(grant.upload_url, {
        headers: grant.required_headers,
        data,
      })
    ).status(),
  ).toBe(200);
  const completion = await request.post(
    `${root}/v1/invoices/${grant.invoice_id}/upload-complete`,
    {
      headers: { ...auth, "Idempotency-Key": `${prefix}-complete` },
      data: { expected_version: 1 },
    },
  );
  expect(completion.status()).toBe(202);
  const accepted = await completion.json();
  await page.goto("/invoices");
  const row = await processingRow(page, grant.invoice_id);
  await expect(row).toContainText("INVALID_PDF", { timeout: 30000 });
  await expect(row).toContainText("Failed");
  await expect(row).toContainText("1 / 5");
  await page.reload();
  await expect(await processingRow(page, grant.invoice_id)).toContainText(
    "INVALID_PDF",
  );
  const denied = await request.post(
    `${root}/v1/jobs/${accepted.job_id}/replay`,
    {
      headers: {
        Authorization: `Bearer ${token("auditor")}`,
        "Idempotency-Key": `${prefix}-denied`,
      },
      data: {
        expected_version: 1,
        reason: "Synthetic unauthorized browser replay probe",
      },
    },
  );
  expect(denied.status()).toBe(403);
});

test("processing error and empty states recover without fabricated records", async ({
  page,
}) => {
  // Presentation-only error injection; real processing workflows are tested above.
  await page.route("**/api/processing*", (route) =>
    route.fulfill({ status: 503, json: { error: "Unavailable" } }),
  );
  await page.goto("/invoices");
  await expect(page.getByText(/Reconnecting automatically/)).toBeVisible();
  await page.unroute("**/api/processing*");
  await expect(
    page.getByRole("region", { name: "Invoice processing" }).getByRole("table"),
  ).toBeVisible({ timeout: 10000 });
  await page.route("**/api/processing*", (route) =>
    route.fulfill({
      json: {
        items: [],
        page_info: { has_next_page: false, end_cursor: null },
        active_count: 0,
        admission_limit: 500,
      },
    }),
  );
  await page.reload();
  await expect(page.getByText(/No invoice processing jobs/)).toBeVisible();
  await page.unroute("**/api/processing*");
});
