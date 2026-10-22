"use client";

import Link from "next/link";
import { useRef, useState, type ReactNode } from "react";
import type { Invoice, PurchaseOrder, Snapshot } from "@reconcileiq/client";
import { formatMoney } from "@/lib/money";
import { Icon } from "./icon";

export type Section =
  | "overview"
  | "invoices"
  | "purchase-orders"
  | "reconciliation"
  | "exceptions"
  | "audit-log";

const titles: Record<Section, { title: string; description: string }> = {
  overview: {
    title: "Your payables, in perspective.",
    description:
      "A clear view of your invoices, matches, and what needs attention.",
  },
  invoices: {
    title: "Invoices",
    description:
      "Every invoice, its purchase order, and its current review state.",
  },
  "purchase-orders": {
    title: "Purchase Orders",
    description: "The purchasing records behind your invoice comparisons.",
  },
  reconciliation: {
    title: "Reconciliation",
    description: "Compare invoice and purchase order totals, side by side.",
  },
  exceptions: {
    title: "Exceptions",
    description: "Understand discrepancies before a reviewer makes a decision.",
  },
  "audit-log": {
    title: "Audit Log",
    description: "A durable history of records and background job activity.",
  },
};

const statusLabels: Record<string, string> = {
  matched: "Matched",
  exception: "Exception",
  needs_review: "Needs review",
  duplicate: "Duplicate",
  open: "Open",
  received: "Received",
};

export function Status({ status }: { status: string }) {
  return (
    <span className={`status status-${status}`}>
      <span />
      {statusLabels[status] || status}
    </span>
  );
}

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value.length === 10 ? `${value}T00:00:00Z` : value));
}

function Panel({
  title,
  subtitle,
  children,
  action,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function StatCards({ snapshot }: { snapshot: Snapshot }) {
  const matched = snapshot.invoices.filter(
    (row) => row.status === "matched",
  ).length;
  const exceptions = snapshot.invoices.filter(
    (row) => row.exception_code,
  ).length;
  // The synthetic seed is USD only. Never combine different currencies into a balance.
  const currency = snapshot.invoices[0]?.currency;
  const oneCurrency =
    currency && snapshot.invoices.every((row) => row.currency === currency);
  const total = snapshot.invoices.reduce(
    (sum, row) => sum + BigInt(row.amount_minor),
    0n,
  );
  const cards = [
    {
      label: "Invoice value",
      value: !snapshot.invoices.length
        ? "—"
        : !oneCurrency
          ? "By currency"
          : total <= BigInt(Number.MAX_SAFE_INTEGER)
            ? formatMoney(Number(total), currency)
            : "Above display limit",
      detail: "Across the synthetic workspace",
      icon: "invoices" as const,
      color: "mint",
    },
    {
      label: "Invoices received",
      value: snapshot.invoices.length.toString().padStart(2, "0"),
      detail: "All records in this demo",
      icon: "orders" as const,
      color: "blue",
    },
    {
      label: "Matched examples",
      value: matched.toString().padStart(2, "0"),
      detail: "Seeded matches · Not approvals",
      icon: "check" as const,
      color: "mint",
    },
    {
      label: "Open exceptions",
      value: exceptions.toString().padStart(2, "0"),
      detail: "Discrepancies requiring attention",
      icon: "exceptions" as const,
      color: "amber",
    },
  ];
  return (
    <div className="stat-grid">
      {cards.map((card) => (
        <div className="stat-card" key={card.label}>
          <div className="stat-label">
            {card.label}
            <span className={`stat-icon ${card.color}`}>
              <Icon name={card.icon} size={18} />
            </span>
          </div>
          <strong>{card.value}</strong>
          <p>
            <span className={`tiny-dot ${card.color}`} />
            {card.detail}
          </p>
        </div>
      ))}
    </div>
  );
}

export function Dashboard({
  snapshot,
  section,
}: {
  snapshot: Snapshot;
  section: Section;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [selected, setSelected] = useState<Invoice | PurchaseOrder | null>(
    null,
  );
  const dialogRef = useRef<HTMLDialogElement>(null);
  const title = titles[section];
  const exceptionRows = snapshot.invoices.filter((row) => row.exception_code);
  const chartGroups = [
    ["matched", "#1c8065"],
    ["exception", "#e4b56b"],
    ["duplicate", "#b2afa0"],
    ["needs_review", "#e5e9e7"],
  ];
  let chartOffset = 0;
  const chartStops = chartGroups.map(([state, color]) => {
    const start = chartOffset;
    chartOffset +=
      (snapshot.invoices.filter((row) => row.status === state).length /
        (snapshot.invoices.length || 1)) *
      100;
    return `${color} ${start}% ${chartOffset}%`;
  });
  const invoices = snapshot.invoices.filter(
    (row) =>
      `${row.id} ${row.vendor} ${row.description} ${row.purchase_order_id}`
        .toLowerCase()
        .includes(query.toLowerCase()) &&
      (status === "all" || row.status === status),
  );
  const orders = snapshot.purchase_orders.filter((row) =>
    `${row.id} ${row.vendor} ${row.description}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const events = snapshot.audit_events.filter((row) =>
    `${row.entity_id} ${row.actor} ${row.action} ${row.detail}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );

  function openDetails(row: Invoice | PurchaseOrder) {
    setSelected(row);
    dialogRef.current?.showModal();
  }

  const invoiceTable = (rows: Invoice[]) => (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Invoice / Vendor</th>
            <th>Purchase order</th>
            <th>Due date</th>
            <th className="align-right">Amount</th>
            <th>Status</th>
            <th>
              <span className="sr-only">Details</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.id}>
              <td>
                <button
                  className="record-link"
                  onClick={() => openDetails(row)}
                >
                  <span className={`vendor-avatar vendor-${index % 4}`}>
                    {row.vendor.slice(0, 1)}
                  </span>
                  <span>
                    <strong>{row.vendor}</strong>
                    <small>{row.id}</small>
                  </span>
                </button>
              </td>
              <td>
                <span className="mono muted">
                  {row.purchase_order_id || "—"}
                </span>
              </td>
              <td className="muted">{dateLabel(row.due_on)}</td>
              <td className="align-right money">
                {formatMoney(row.amount_minor, row.currency)}
              </td>
              <td>
                <Status status={row.status} />
              </td>
              <td>
                <button
                  className="icon-button"
                  onClick={() => openDetails(row)}
                  aria-label={`View ${row.id}`}
                >
                  <Icon name="chevron" size={16} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && (
        <div className="empty-state">No invoices match your filters.</div>
      )}
    </div>
  );

  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <p className="eyebrow">FINANCE WORKSPACE</p>
          <h1>{title.title}</h1>
          <p className="page-description">{title.description}</p>
        </div>
        <div className="date-pill">
          <Icon name="audit" size={16} />
          {dateLabel(snapshot.as_of)}
          <span className="muted">· Snapshot</span>
        </div>
      </div>
      <div className="demo-notice">
        <span className="demo-notice-icon">
          <Icon name="shield" size={17} />
        </span>
        <span>
          <strong>A safe place to explore.</strong> You’re viewing synthetic,
          read-only records. Statuses are illustrative.
        </span>
        <span className="demo-version">MILESTONE 01</span>
      </div>
      {section === "overview" && (
        <>
          <StatCards snapshot={snapshot} />
          <div className="insight-grid">
            <Panel
              title="Reconciliation at a glance"
              subtitle="Where your synthetic invoices stand"
              action={
                <span className="subtle-badge">
                  {snapshot.invoices.length} RECORDS
                </span>
              }
            >
              <div className="reconciliation-summary">
                <div
                  className="donut"
                  role="img"
                  aria-label={`${snapshot.invoices.filter((row) => row.status === "matched").length} of ${snapshot.invoices.length} invoices are matched examples`}
                  style={{
                    background: snapshot.invoices.length
                      ? `conic-gradient(${chartStops.join(", ")})`
                      : "#e5e9e7",
                  }}
                >
                  <div>
                    <strong>{snapshot.invoices.length}</strong>
                    <span>invoices</span>
                  </div>
                </div>
                <div className="legend">
                  {["matched", "exception", "duplicate", "needs_review"].map(
                    (item) => (
                      <div key={item}>
                        <span className={`legend-dot dot-${item}`} />
                        <span>{statusLabels[item]}</span>
                        <strong>
                          {snapshot.invoices
                            .filter((row) => row.status === item)
                            .length.toString()
                            .padStart(2, "0")}
                        </strong>
                      </div>
                    ),
                  )}
                </div>
              </div>
              <Link href="/reconciliation" className="panel-bottom-link">
                Explore reconciliation <Icon name="arrow" size={16} />
              </Link>
            </Panel>
            <Panel
              title="A little attention goes a long way"
              subtitle={`${exceptionRows.length} records have discrepancies to review`}
              action={
                <span className="attention-icon">
                  <Icon name="exceptions" size={20} />
                </span>
              }
            >
              <div className="attention-list">
                {exceptionRows.slice(0, 2).map((row) => (
                  <button
                    key={row.id}
                    className="attention-row"
                    onClick={() => openDetails(row)}
                  >
                    <span className="attention-dot" />
                    <span>
                      <strong>
                        {row.exception_code?.replaceAll("_", " ").toLowerCase()}
                      </strong>
                      <small>
                        {row.vendor} · {row.id}
                      </small>
                    </span>
                    <Icon name="chevron" size={16} />
                  </button>
                ))}
              </div>
              <Link href="/exceptions" className="panel-bottom-link">
                View all exceptions <Icon name="arrow" size={16} />
              </Link>
            </Panel>
          </div>
        </>
      )}
      {(section === "overview" || section === "invoices") && (
        <Panel
          title={section === "overview" ? "Recent invoices" : "All invoices"}
          subtitle="Persisted records from the local API"
          action={
            section === "overview" ? (
              <Link className="text-link" href="/invoices">
                View all invoices <Icon name="arrow" size={15} />
              </Link>
            ) : undefined
          }
        >
          <div className="table-toolbar">
            <label className="search-input">
              <Icon name="search" size={17} />
              <input
                aria-label="Search invoices"
                placeholder="Search invoices or vendors…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className="filter-label">
              <span className="sr-only">Invoice status</span>
              <select
                aria-label="Invoice status"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="all">All statuses</option>
                {["matched", "exception", "needs_review", "duplicate"].map(
                  (item) => (
                    <option key={item} value={item}>
                      {statusLabels[item]}
                    </option>
                  ),
                )}
              </select>
            </label>
          </div>
          {invoiceTable(invoices)}
          <div className="table-footer">
            <span>
              Showing {invoices.length} of {snapshot.invoices.length} invoices
            </span>
            <span>
              {[...new Set(snapshot.invoices.map((row) => row.currency))].join(
                ", ",
              )}{" "}
              · Synthetic data
            </span>
          </div>
        </Panel>
      )}
      {section === "purchase-orders" && (
        <Panel
          title="Purchasing register"
          subtitle={`${snapshot.purchase_orders.length} synthetic purchase orders`}
        >
          <div className="table-toolbar">
            <label className="search-input">
              <Icon name="search" size={17} />
              <input
                aria-label="Search purchase orders"
                placeholder="Search purchase orders or vendors…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Purchase order</th>
                  <th>Vendor</th>
                  <th>Issued</th>
                  <th className="align-right">Amount</th>
                  <th>Status</th>
                  <th>
                    <span className="sr-only">Details</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {orders.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <button
                        className="record-id"
                        onClick={() => openDetails(row)}
                      >
                        {row.id}
                      </button>
                    </td>
                    <td>
                      <strong>{row.vendor}</strong>
                      <small className="cell-description">
                        {row.description}
                      </small>
                    </td>
                    <td className="muted">{dateLabel(row.issued_on)}</td>
                    <td className="align-right money">
                      {formatMoney(row.amount_minor, row.currency)}
                    </td>
                    <td>
                      <Status status={row.status} />
                    </td>
                    <td>
                      <button
                        className="icon-button"
                        onClick={() => openDetails(row)}
                        aria-label={`View ${row.id}`}
                      >
                        <Icon name="chevron" size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!orders.length && (
              <div className="empty-state">
                No purchase orders match your search.
              </div>
            )}
          </div>
          <div className="table-footer">
            Showing {orders.length} of {snapshot.purchase_orders.length}{" "}
            purchase orders
          </div>
        </Panel>
      )}
      {section === "reconciliation" && (
        <>
          <div className="info-note">
            <Icon name="reconciliation" />
            <span>
              These comparisons display source totals. Full line, tax, quantity,
              and receipt validation arrives in a later milestone.
            </span>
          </div>
          <div className="comparison-grid">
            {snapshot.invoices.map((row) => {
              const po = snapshot.purchase_orders.find(
                (item) => item.id === row.purchase_order_id,
              );
              return (
                <section className="comparison-card" key={row.id}>
                  <div className="comparison-header">
                    <span className="vendor-avatar">
                      {row.vendor.slice(0, 1)}
                    </span>
                    <div>
                      <h2>{row.vendor}</h2>
                      <p>
                        {row.id} <span>→</span> {po?.id || "No purchase order"}
                      </p>
                    </div>
                  </div>
                  <div className="comparison-totals">
                    <div>
                      <span>Invoice total</span>
                      <strong>
                        {formatMoney(row.amount_minor, row.currency)}
                      </strong>
                    </div>
                    <Icon name="reconciliation" size={18} />
                    <div>
                      <span>Purchase order</span>
                      <strong>
                        {po
                          ? formatMoney(po.amount_minor, po.currency)
                          : "Unavailable"}
                      </strong>
                    </div>
                  </div>
                  <div className="comparison-footer">
                    <Status status={row.status} />
                    <button
                      className="text-link"
                      onClick={() => openDetails(row)}
                    >
                      Inspect record <Icon name="arrow" size={14} />
                    </button>
                  </div>
                </section>
              );
            })}
          </div>
        </>
      )}
      {section === "exceptions" && (
        <>
          <div className="exception-summary">
            <span className="attention-icon">
              <Icon name="exceptions" size={23} />
            </span>
            <div>
              <strong>
                {exceptionRows.length} exceptions need a closer look
              </strong>
              <p>
                Review reasons and source records. Human review actions are
                planned for a later milestone.
              </p>
            </div>
          </div>
          <div className="exception-list">
            {exceptionRows.map((row) => (
              <section className="exception-card" key={row.id}>
                <div>
                  <span className="exception-code">
                    {row.exception_code?.replaceAll("_", " ")}
                  </span>
                  <h2>{row.vendor}</h2>
                  <p>{row.exception_detail}</p>
                  <div className="exception-meta">
                    <span className="mono">{row.id}</span>
                    <span>{row.purchase_order_id}</span>
                    <span className="money">
                      {formatMoney(row.amount_minor, row.currency)}
                    </span>
                  </div>
                </div>
                <button
                  className="secondary-button"
                  onClick={() => openDetails(row)}
                >
                  View record <Icon name="arrow" size={16} />
                </button>
              </section>
            ))}
          </div>
        </>
      )}
      {section === "audit-log" && (
        <Panel
          title="Record history"
          subtitle="Recent activity with audit context for visible financial records"
        >
          <div className="table-toolbar">
            <label className="search-input">
              <Icon name="search" size={17} />
              <input
                aria-label="Search audit events"
                placeholder="Search actors, records, or events…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <span className="subtle-badge">APPEND ONLY</span>
          </div>
          <div className="audit-timeline">
            {events.map((row) => (
              <article className="audit-event" key={row.id}>
                <span className="audit-event-icon">
                  <Icon
                    name={row.actor === "worker" ? "check" : "invoices"}
                    size={17}
                  />
                </span>
                <div>
                  <div className="audit-event-heading">
                    <strong>{row.action.replaceAll("_", " ")}</strong>
                    <time dateTime={row.occurred_at}>
                      {dateLabel(row.occurred_at)}
                    </time>
                  </div>
                  <p>{row.detail}</p>
                  <div className="audit-meta">
                    <span className="mono">{row.entity_id}</span>
                    <span>Actor: {row.actor}</span>
                    <span className="event-id">Event: {row.id}</span>
                  </div>
                </div>
              </article>
            ))}
            {!events.length && (
              <div className="empty-state">
                No audit events match your search.
              </div>
            )}
          </div>
          <div className="table-footer">
            {events.length} events shown · Up to 100 context events
          </div>
        </Panel>
      )}
      <dialog
        ref={dialogRef}
        className="record-dialog"
        onClick={(event) => {
          if (event.target === event.currentTarget) dialogRef.current?.close();
        }}
        aria-labelledby="record-dialog-title"
      >
        {selected && (
          <>
            <div className="dialog-heading">
              <span className="eyebrow">SYNTHETIC RECORD</span>
              <button
                className="icon-button"
                onClick={() => dialogRef.current?.close()}
                aria-label="Close record details"
              >
                <Icon name="close" />
              </button>
            </div>
            <h2 id="record-dialog-title">{selected.id}</h2>
            <p className="dialog-vendor">{selected.vendor}</p>
            <p className="muted">{selected.description}</p>
            <dl className="record-fields">
              <div>
                <dt>Amount</dt>
                <dd className="money">
                  {formatMoney(selected.amount_minor, selected.currency)}
                </dd>
              </div>
              <div>
                <dt>Currency</dt>
                <dd>{selected.currency}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>
                  <Status status={selected.status} />
                </dd>
              </div>
              {"purchase_order_id" in selected && (
                <div>
                  <dt>Purchase order</dt>
                  <dd>{selected.purchase_order_id || "None"}</dd>
                </div>
              )}
              <div>
                <dt>{"due_on" in selected ? "Due date" : "Issued on"}</dt>
                <dd>
                  {dateLabel(
                    "due_on" in selected ? selected.due_on : selected.issued_on,
                  )}
                </dd>
              </div>
            </dl>
            {"exception_detail" in selected && selected.exception_detail && (
              <div className="dialog-exception">
                <Icon name="exceptions" size={18} />
                <p>{selected.exception_detail}</p>
              </div>
            )}
            <div className="dialog-audit">
              <h3>Record audit trail</h3>
              {snapshot.audit_events
                .filter((row) => row.entity_id === selected.id)
                .map((row) => (
                  <p key={row.id}>
                    <strong>{row.actor}</strong> ·{" "}
                    {row.action.replaceAll("_", " ")}
                    <small>{dateLabel(row.occurred_at)}</small>
                  </p>
                ))}
            </div>
            <p className="dialog-note">
              Read-only local demo. No approval or payment action is available.
            </p>
          </>
        )}
      </dialog>
    </div>
  );
}
