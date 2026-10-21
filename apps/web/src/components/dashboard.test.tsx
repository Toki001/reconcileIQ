import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Dashboard } from "./dashboard";
import { fixture } from "@/test/fixture";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});

test("invoice search and status filters compose, including the empty state", async () => {
  const user = userEvent.setup();
  render(<Dashboard snapshot={fixture} section="invoices" />);
  const search = screen.getByRole("textbox", { name: "Search invoices" });
  await user.type(search, "acme");
  expect(screen.getByText("Acme Office")).toBeInTheDocument();
  expect(screen.queryByText("Northstar Studio")).not.toBeInTheDocument();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Invoice status" }),
    "matched",
  );
  expect(
    screen.getByText("No invoices match your filters."),
  ).toBeInTheDocument();
  await user.clear(search);
  expect(screen.getByText("Northstar Studio")).toBeInTheDocument();
  expect(screen.queryByText("Acme Office")).not.toBeInTheDocument();
});

test("record details show the discrepancy and its persisted audit trail", async () => {
  const user = userEvent.setup();
  render(<Dashboard snapshot={fixture} section="invoices" />);
  await user.click(screen.getByRole("button", { name: "View INV-002" }));
  const dialog = screen.getByRole("dialog", { name: "INV-002" });
  expect(
    within(dialog).getByText("Two units not received."),
  ).toBeInTheDocument();
  expect(within(dialog).getByText("local-seed")).toBeInTheDocument();
  expect(within(dialog).getByText("$1,860.00")).toBeInTheDocument();
  await user.click(
    within(dialog).getByRole("button", { name: "Close record details" }),
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("purchase order search uses real source records", async () => {
  const user = userEvent.setup();
  render(<Dashboard snapshot={fixture} section="purchase-orders" />);
  await user.type(
    screen.getByRole("textbox", { name: "Search purchase orders" }),
    "PO-002",
  );
  expect(screen.getByText("PO-002")).toBeInTheDocument();
  expect(screen.queryByText("PO-001")).not.toBeInTheDocument();
});

test("exceptions expose reasons without approval actions", () => {
  render(<Dashboard snapshot={fixture} section="exceptions" />);
  expect(screen.getByText("Two units not received.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "View record" })).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: /approve|pay/i }),
  ).not.toBeInTheDocument();
});
