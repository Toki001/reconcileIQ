import { apiUrl } from "./env";
import { test, expect } from "@playwright/test";

test("all finance pages read the running API and render real record details", async ({
  page,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  const routes = [
    ["/", "Your payables, in perspective."],
    ["/invoices", "Invoices"],
    ["/purchase-orders", "Purchase Orders"],
    ["/reconciliation", "Reconciliation"],
    ["/exceptions", "Exceptions"],
    ["/audit-log", "Audit Log"],
  ];
  for (const [route, heading] of routes) {
    const response = await page.goto(route);
    expect(response?.status()).toBe(200);
    await expect(
      page.getByRole("heading", { level: 1, name: heading, exact: true }),
    ).toBeVisible();
  }
  await page
    .getByRole("textbox", { name: "Search audit events" })
    .fill("INV-1043");
  // A retained invoice can have several audit events after a migration or review.
  await expect(
    page.getByText("INV-1043", { exact: true }).first(),
  ).toBeVisible();
  await page.goto("/invoices");
  await page.getByRole("textbox", { name: "Search invoices" }).fill("Acme");
  await expect(page.getByText("Northstar Studio", { exact: true })).toHaveCount(
    0,
  );
  await page
    .getByRole("button", { name: "View INV-1043", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "INV-1043" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Record audit trail")).toBeVisible();
  await expect(dialog.getByText(/Invoice lists 12 units/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "View INV-1043", exact: true }),
  ).toBeFocused();
  expect(failures).toEqual([]);
  await page.goto("/");
  await page.screenshot({
    path: ".cache/screenshots/overview-desktop.png",
    fullPage: true,
  });
});

test("phone layout contains the page and keeps record inspection accessible", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/invoices");
  await expect(
    page.getByRole("heading", { name: "Invoices", exact: true }),
  ).toBeVisible();
  const width = await page.evaluate(() => ({
    viewport: innerWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(width.content).toBeLessThanOrEqual(width.viewport);
  await page
    .getByRole("button", { name: "View INV-1042", exact: true })
    .click();
  await expect(page.getByRole("dialog", { name: "INV-1042" })).toBeVisible();
  await page.getByRole("button", { name: "Close record details" }).click();
  await page.screenshot({
    path: ".cache/screenshots/invoices-mobile.png",
    fullPage: true,
  });
});
