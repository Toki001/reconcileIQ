"use client";
import { useCallback, useEffect, useState } from "react";
import { createApi, type components } from "@reconcileiq/client";
import { formatMoney } from "@/lib/money";
import { OriginalDocument } from "./original-document";

type Schemas = components["schemas"];
type Evidence = {
  value: string | number | null;
  status: string;
  pages: number[];
  quote: string | null;
};
type Candidate = Schemas["InvoiceCandidate"];
const api = createApi("/api/review");
const headerFields = [
  "invoice_number",
  "vendor_name",
  "vendor_identifier",
  "invoice_date",
  "due_date",
  "purchase_order_reference",
  "currency",
  "tax_mode",
  "subtotal_minor",
  "discount_minor",
  "tax_minor",
  "total_minor",
];
const lineFields = [
  "reference",
  "description",
  "quantity",
  "unit_price_minor",
  "gross_minor",
  "discount_minor",
  "tax_rate",
  "tax_minor",
  "total_minor",
];
const missing = (): Evidence => ({
  value: null,
  status: "missing",
  pages: [],
  quote: null,
});
function blank(): Candidate {
  return Object.fromEntries([
    ...headerFields.map((k) => [k, missing()]),
    ["lines", [Object.fromEntries(lineFields.map((k) => [k, missing()]))]],
  ]) as Candidate;
}
function message(error: unknown): string {
  return typeof error === "object" && error && "detail" in error
    ? String(error.detail)
    : "Request failed. Refresh state before retrying.";
}
const retryKeys = new Map<string, string>();
function headers(action: string, body: unknown) {
  const fingerprint = JSON.stringify([action, body]);
  if (!retryKeys.has(fingerprint))
    retryKeys.set(fingerprint, crypto.randomUUID());
  return { "Idempotency-Key": retryKeys.get(fingerprint)! };
}

function displayMoney(value: unknown, currency: string): string {
  return typeof value === "number" ? formatMoney(value, currency) : "Unknown";
}

function Comparison({ context }: { context: Record<string, unknown> }) {
  const order = context.order as Record<string, unknown> | null;
  const received = (context.received ?? {}) as Record<string, string>;
  if (!order)
    return (
      <p>
        Purchase order not found. Check the candidate reference and supplier.
      </p>
    );
  const lines = order.lines as Record<string, unknown>[];
  const currency = String(order.currency);
  return (
    <div className="review-comparison">
      <p>
        <strong>{String(order.reference)}</strong> · {currency} ·{" "}
        {String(order.tax_mode)} tax
      </p>
      <p>
        Supplier:{" "}
        {context.vendor_matches === true
          ? "exact match"
          : context.vendor_matches === false
            ? "mismatch"
            : "unresolved"}
      </p>
      <table>
        <thead>
          <tr>
            <th>PO line</th>
            <th>Remaining ordered</th>
            <th>Available received</th>
            <th>Unit price</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={String(line.reference)}>
              <td>
                <strong>{String(line.reference)}</strong>
                <br />
                {String(line.description)}
              </td>
              <td>{String(line.quantity)}</td>
              <td>{received[String(line.reference)] ?? "0"}</td>
              <td>{displayMoney(line.unit_price_minor, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        PO subtotal {displayMoney(order.subtotal_minor, currency)} · discount{" "}
        {displayMoney(order.discount_minor, currency)} · tax{" "}
        {displayMoney(order.tax_minor, currency)} · total{" "}
        {displayMoney(order.total_minor, currency)}
      </p>
      <small>
        Remaining quantities exclude other human-reviewed invoices. Matching
        uses the selected policy and records its own immutable snapshot.
      </small>
    </div>
  );
}

export function InvoiceReview() {
  const [identity, setIdentity] = useState<Schemas["IdentityRead"] | null>(
    null,
  );
  const [token, setToken] = useState("");
  const [invoices, setInvoices] = useState<Schemas["InvoiceDetail"][]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [provider, setProvider] = useState<Schemas["ProviderStatus"] | null>(
    null,
  );
  const [workspace, setWorkspace] = useState<Schemas["WorkspaceRead"] | null>(
    null,
  );
  const [draft, setDraft] = useState<Candidate>(blank);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [job, setJob] = useState<Schemas["JobRead"] | null>(null);
  const [policy, setPolicy] = useState<
    "two_way_exact_v1" | "three_way_exact_v1"
  >("three_way_exact_v1");
  const [file, setFile] = useState<File | null>(null);
  const visibleInvoices =
    workspace && !invoices.some((row) => row.id === workspace.invoice.id)
      ? [workspace.invoice, ...invoices]
      : invoices;
  const writable = identity?.role === "admin" || identity?.role === "reviewer";
  const refreshList = useCallback(async (after?: string) => {
    const result = await api.GET("/v1/invoices", {
      params: { query: { first: 20, ...(after ? { after } : {}) } },
    });
    if (!result.data) throw result.error;
    setInvoices((old) =>
      after ? [...old, ...result.data.items] : result.data.items,
    );
    setCursor(
      result.data.page_info.has_next_page
        ? result.data.page_info.end_cursor
        : null,
    );
  }, []);
  const open = useCallback(async (id: string) => {
    const result = await api.GET("/v1/invoices/{invoice_id}/workspace", {
      params: { path: { invoice_id: id } },
    });
    if (!result.data) throw result.error;
    setWorkspace(result.data);
    setDraft(result.data.candidates[0]?.candidate ?? blank());
  }, []);
  const initialize = useCallback(async () => {
    const who = await api.GET("/v1/me");
    if (!who.data) return;
    setIdentity(who.data);
    const [status] = await Promise.all([
      api.GET("/v1/ai/status"),
      refreshList(),
    ]);
    if (status.data) setProvider(status.data);
  }, [refreshList]);
  useEffect(() => {
    const timer = setTimeout(() => {
      initialize().catch((e) => setError(message(e)));
    }, 0);
    return () => clearTimeout(timer);
  }, [initialize]);
  useEffect(() => {
    if (!job || ["failed", "succeeded", "cancelled"].includes(job.state))
      return;
    const timer = setTimeout(async () => {
      const result = await api.GET("/v1/jobs/{job_id}", {
        params: { path: { job_id: job.id } },
      });
      if (result.data) {
        setJob(result.data);
        if (["succeeded", "failed"].includes(result.data.state)) {
          await refreshList();
          if (result.data.invoice_id) await open(result.data.invoice_id);
        }
      } else setError(message(result.error));
    }, 2000);
    return () => clearTimeout(timer);
  }, [job, open, refreshList]);
  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function track(accepted: Schemas["JobAccepted"]) {
    const result = await api.GET("/v1/jobs/{job_id}", {
      params: { path: { job_id: accepted.job_id } },
    });
    if (!result.data) throw result.error;
    setJob(result.data);
    if (accepted.invoice_id) await open(accepted.invoice_id);
  }
  function edit(name: string, value: string, lineIndex?: number) {
    const copy = structuredClone(draft);
    const fields = (lineIndex === undefined
      ? copy
      : copy.lines[lineIndex]) as unknown as Record<string, Evidence>;
    let converted: string | number | null = value || null;
    if (value && name.endsWith("_minor")) {
      if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) {
        setError(
          "Amounts require nonnegative integer minor units within the safe range.",
        );
        return;
      }
      converted = Number(value);
    }
    fields[name] = {
      value: converted,
      status: converted === null ? "missing" : "human",
      pages: [],
      quote: null,
    };
    setDraft(copy);
  }
  function fields(
    names: string[],
    values: Record<string, Evidence>,
    index?: number,
  ) {
    return (
      <div className="review-fields">
        {names.map((name) => (
          <label key={name}>
            <span>{name.replaceAll("_", " ")}</span>
            <input
              aria-label={`${index === undefined ? "Invoice" : `Line ${index + 1}`} ${name}`}
              value={values[name]?.value ?? ""}
              disabled={!writable || busy}
              onChange={(e) => edit(name, e.target.value, index)}
            />
            <small>
              {values[name]?.status ?? "missing"}
              {values[name]?.pages.length
                ? ` · page ${values[name].pages.join(", ")}`
                : ""}
            </small>
            {values[name]?.quote && <q>{values[name].quote}</q>}
          </label>
        ))}
      </div>
    );
  }
  return (
    <div className="review-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">PERSISTED REVIEW WORKFLOW</p>
          <h1>Invoice Review</h1>
          <p className="muted">
            Inspect source evidence, correct fields, and run deterministic
            checks.
          </p>
        </div>
      </div>
      {error && (
        <p role="alert" className="review-alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!identity ? (
        <form
          className="panel review-signin"
          onSubmit={(e) => {
            e.preventDefault();
            action(async () => {
              const response = await fetch("/api/session", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token }),
              });
              const data = await response.json();
              if (!response.ok) throw data;
              setToken("");
              await initialize();
            });
          }}
        >
          <h2>Local reviewer sign in</h2>
          <label>
            API key
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
            />
          </label>
          <button disabled={busy}>Sign in</button>
          <p>
            Use a local reviewer, admin, or auditor key from your private
            backend environment.
          </p>
        </form>
      ) : (
        <>
          <div className="review-toolbar">
            <span>
              {identity.actor} · {identity.role}
            </span>
            <button
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await fetch("/api/session", { method: "DELETE" });
                  setIdentity(null);
                  setWorkspace(null);
                  setInvoices([]);
                })
              }
            >
              Sign out
            </button>
          </div>
          <div className="panel review-provider">
            <strong>Gemini · {provider?.model}</strong>
            <p>
              {provider?.available
                ? "Verified free tier · synthetic fixtures only. Worker re-verifies before generation."
                : `Live AI unavailable · ${provider?.code ?? "status unavailable"}. Manual evidence and local reconciliation remain available.`}
            </p>
            {identity.role === "admin" && (
              <button
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    const result = await api.POST("/v1/ai/verify", {
                      params: {
                        header: { "Idempotency-Key": crypto.randomUUID() },
                      },
                    });
                    if (!result.data) throw result.error;
                    setProvider(result.data);
                  })
                }
              >
                Verify free project
              </button>
            )}
          </div>
          {writable && (
            <form
              className="panel review-upload"
              onSubmit={(e) => {
                e.preventDefault();
                action(async () => {
                  if (!file || file.size > 10485760)
                    throw { detail: "Select a PDF up to 10 MiB." };
                  const bytes = await file.arrayBuffer();
                  const sha256 = Array.from(
                    new Uint8Array(
                      await crypto.subtle.digest("SHA-256", bytes),
                    ),
                    (n) => n.toString(16).padStart(2, "0"),
                  ).join("");
                  const body = {
                    filename: file.name,
                    content_type: "application/pdf" as const,
                    size_bytes: file.size,
                    sha256,
                  };
                  const grant = await api.POST("/v1/invoices/upload-requests", {
                    body,
                    params: { header: headers("upload", body) },
                  });
                  if (!grant.data) throw grant.error;
                  const id = grant.data.invoice_id;
                  const uploaded = await fetch(
                    `/api/review/v1/invoices/${id}/document`,
                    {
                      method: "PUT",
                      headers: { "Content-Type": "application/pdf" },
                      body: bytes,
                    },
                  );
                  if (!uploaded.ok && uploaded.status !== 409)
                    throw await uploaded.json();
                  const complete = await api.POST(
                    "/v1/invoices/{invoice_id}/upload-complete",
                    {
                      params: {
                        path: { invoice_id: id },
                        header: headers(`complete:${id}`, {
                          expected_version: 1,
                        }),
                      },
                      body: { expected_version: 1 },
                    },
                  );
                  if (!complete.data) throw complete.error;
                  await track(complete.data);
                  await refreshList();
                });
              }}
            >
              <label>
                Upload an invoice PDF
                <input
                  type="file"
                  accept="application/pdf,.pdf"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  required
                />
              </label>
              <button disabled={busy || !file}>Upload and extract text</button>
            </form>
          )}
          {job && (
            <div role="status" className="panel review-job">
              Job {job.id} · {job.state} · attempt {job.attempts}{" "}
              {job.error_code && `· ${job.error_code}`}
              <p>{job.next_action}</p>
            </div>
          )}
          <div className="review-layout">
            <aside className="panel review-invoices">
              <h2>Invoices</h2>
              {visibleInvoices.map((invoice) => (
                <button
                  className={
                    workspace?.invoice.id === invoice.id ? "selected" : ""
                  }
                  key={invoice.id}
                  disabled={busy}
                  onClick={() => action(() => open(invoice.id))}
                >
                  <strong>{invoice.vendor ?? "Unextracted invoice"}</strong>
                  <small>
                    {invoice.id.slice(0, 12)} · {invoice.status}
                  </small>
                </button>
              ))}
              {cursor && (
                <button
                  disabled={busy}
                  onClick={() => action(() => refreshList(cursor))}
                >
                  Load more invoices
                </button>
              )}
            </aside>
            {workspace ? (
              <section className="review-detail">
                <div className="panel review-card">
                  <h2>Original document</h2>
                  <p>
                    {workspace.invoice.id} · version {workspace.invoice.version}
                  </p>
                  {workspace.document_available ? (
                    <OriginalDocument
                      key={workspace.invoice.id}
                      invoiceId={workspace.invoice.id}
                    />
                  ) : (
                    <p>
                      No retained PDF is available for this historical record.
                    </p>
                  )}
                  <button
                    disabled={busy}
                    onClick={() => action(() => open(workspace.invoice.id))}
                  >
                    Refresh evidence
                  </button>
                  {writable && (
                    <button
                      disabled={busy || !provider?.available}
                      onClick={() =>
                        action(async () => {
                          const body = {
                            expected_version: workspace.invoice.version,
                            synthetic_authorized: true as const,
                          };
                          const result = await api.POST(
                            "/v1/invoices/{invoice_id}/structured-extractions",
                            {
                              params: {
                                path: { invoice_id: workspace.invoice.id },
                                header: headers(
                                  `extract:${workspace.invoice.id}`,
                                  body,
                                ),
                              },
                              body,
                            },
                          );
                          if (!result.data) throw result.error;
                          await track(result.data);
                        })
                      }
                    >
                      Extract synthetic fields with Gemini
                    </button>
                  )}
                </div>
                <div className="panel review-card">
                  <h2>Candidate evidence</h2>
                  <p>
                    {workspace.candidates[0]
                      ? `${workspace.candidates[0].origin} · candidate ${workspace.candidates[0].number} · ${workspace.candidates[0].model ?? "human evidence"}`
                      : "No structured candidate. Enter document values for human review."}
                  </p>
                  <p className="muted">
                    Money uses integer minor units. Quantities and tax rates use
                    decimal strings. Empty values remain missing. Source quotes
                    stay attached to unchanged fields.
                  </p>
                  {fields(
                    headerFields,
                    draft as unknown as Record<string, Evidence>,
                  )}
                  {draft.lines.map((line, index) => (
                    <fieldset key={index}>
                      <legend>Line {index + 1}</legend>
                      {fields(
                        lineFields,
                        line as unknown as Record<string, Evidence>,
                        index,
                      )}
                      {writable && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            setDraft({
                              ...draft,
                              lines: draft.lines.filter((_, i) => i !== index),
                            })
                          }
                        >
                          Remove line {index + 1}
                        </button>
                      )}
                    </fieldset>
                  ))}
                  {writable && (
                    <>
                      <button
                        disabled={busy || draft.lines.length >= 100}
                        onClick={() =>
                          setDraft({
                            ...draft,
                            lines: [...draft.lines, blank().lines[0]],
                          })
                        }
                      >
                        Add line
                      </button>
                      <label>
                        Correction or review reason
                        <textarea
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          minLength={10}
                          maxLength={1000}
                        />
                      </label>
                      <button
                        disabled={busy || reason.trim().length < 10}
                        onClick={() =>
                          action(async () => {
                            const body = {
                              candidate: draft,
                              expected_version: workspace.invoice.version,
                              source_version_id:
                                workspace.candidates[0]?.id ?? null,
                              reason,
                            };
                            const result = await api.POST(
                              "/v1/invoices/{invoice_id}/corrections",
                              {
                                params: {
                                  path: { invoice_id: workspace.invoice.id },
                                  header: headers(
                                    `correct:${workspace.invoice.id}`,
                                    body,
                                  ),
                                },
                                body,
                              },
                            );
                            if (!result.data) throw result.error;
                            await open(workspace.invoice.id);
                            setNotice(
                              "Human correction recorded as a new immutable candidate.",
                            );
                          })
                        }
                      >
                        Save correction
                      </button>
                    </>
                  )}
                </div>
                <div className="panel review-card">
                  <h2>Purchase order and receipts</h2>
                  {workspace.current_context ? (
                    <Comparison context={workspace.current_context} />
                  ) : (
                    <p>
                      Save candidate evidence to resolve supplier, order, and
                      receipt quantities.
                    </p>
                  )}
                  <label>
                    Matching policy
                    <select
                      value={policy}
                      onChange={(e) =>
                        setPolicy(e.target.value as typeof policy)
                      }
                    >
                      <option value="three_way_exact_v1">
                        Three-way exact · invoice, PO, receipts
                      </option>
                      <option value="two_way_exact_v1">
                        Two-way exact · invoice and PO
                      </option>
                    </select>
                  </label>
                  {writable && (
                    <button
                      disabled={busy || !workspace.candidates.length}
                      onClick={() =>
                        action(async () => {
                          const body = {
                            invoice_id: workspace.invoice.id,
                            expected_version: workspace.invoice.version,
                            policy_id: policy,
                          };
                          const result = await api.POST("/v1/reconciliations", {
                            body,
                            params: { header: headers("reconcile", body) },
                          });
                          if (!result.data) throw result.error;
                          await track(result.data);
                        })
                      }
                    >
                      Run deterministic reconciliation
                    </button>
                  )}
                  <h3>Deterministic result</h3>
                  {workspace.result ? (
                    <>
                      <p>
                        <strong>{workspace.result.outcome}</strong> ·{" "}
                        {workspace.result.policy_id} · invoice version{" "}
                        {workspace.result.invoice_version}
                      </p>
                      {workspace.result.differences.map((difference, i) => (
                        <div className="review-difference" key={i}>
                          <strong>{difference.code}</strong>
                          <p>
                            {difference.field} · Expected {difference.expected};
                            observed {difference.observed}
                          </p>
                        </div>
                      ))}
                      <details>
                        <summary>Immutable comparison snapshot</summary>
                        <pre>
                          {JSON.stringify(workspace.result.context, null, 2)}
                        </pre>
                      </details>
                      {writable && (
                        <div className="review-toolbar">
                          {(["accept", "reject"] as const).map((decision) => (
                            <button
                              key={decision}
                              disabled={
                                busy ||
                                reason.trim().length < 10 ||
                                workspace.result?.invoice_version !==
                                  workspace.invoice.version ||
                                (decision === "accept" &&
                                  workspace.result?.outcome !== "matched")
                              }
                              onClick={() =>
                                action(async () => {
                                  const body = {
                                    reconciliation_id:
                                      workspace.result!.reconciliation_id,
                                    expected_version: workspace.invoice.version,
                                    decision,
                                    reason,
                                  };
                                  const result = await api.POST(
                                    "/v1/invoices/{invoice_id}/review-decisions",
                                    {
                                      params: {
                                        path: {
                                          invoice_id: workspace.invoice.id,
                                        },
                                        header: headers(
                                          `review:${workspace.invoice.id}`,
                                          body,
                                        ),
                                      },
                                      body,
                                    },
                                  );
                                  if (!result.data) throw result.error;
                                  await open(workspace.invoice.id);
                                  await refreshList();
                                  setNotice(
                                    `Review ${decision} recorded. No payment is initiated.`,
                                  );
                                })
                              }
                            >
                              {decision === "accept"
                                ? "Accept matched review"
                                : "Reject review"}
                            </button>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <p>No structured reconciliation result.</p>
                  )}
                </div>
                <div className="panel review-card">
                  <h2>Immutable timeline</h2>
                  {workspace.history_truncated && (
                    <p>
                      Recent history shown; older records remain available
                      through the audit API.
                    </p>
                  )}
                  <ol className="review-timeline">
                    {workspace.timeline.map((event) => (
                      <li key={event.id}>
                        <strong>{event.action}</strong>
                        <p>
                          {event.actor} ·{" "}
                          {new Date(event.occurred_at).toLocaleString()}
                        </p>
                        <details>
                          <summary>Audit context</summary>
                          <pre>{event.detail}</pre>
                        </details>
                      </li>
                    ))}
                  </ol>
                  <details>
                    <summary>
                      Retained candidate history ({workspace.candidates.length})
                    </summary>
                    {workspace.candidates.map((value) => (
                      <div key={value.id}>
                        <strong>
                          Version {value.number} · {value.origin}
                        </strong>
                        <pre>{JSON.stringify(value.candidate, null, 2)}</pre>
                      </div>
                    ))}
                  </details>
                </div>
              </section>
            ) : (
              <section className="panel review-card">
                <h2>Select an invoice</h2>
                <p>Open persisted evidence or upload a PDF to begin.</p>
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
