"use client";

import { useEffect, useState } from "react";
import type {
  ProcessingJob as Job,
  ProcessingJobPage as Page,
} from "@reconcileiq/client";

export function processingLabel(job: Pick<Job, "state" | "stage">) {
  if (
    job.stage === "retrying" &&
    ["pending_dispatch", "queued"].includes(job.state)
  )
    return "Retrying";
  return {
    pending_dispatch: "Pending dispatch",
    queued: "Queued",
    running: "Processing",
    succeeded: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  }[job.state];
}

export function ProcessingJobs() {
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const response = await fetch(
          `/api/processing${cursor ? `?after=${encodeURIComponent(cursor)}` : ""}`,
          { cache: "no-store", signal: controller.signal },
        );
        if (!response.ok) throw new Error("unavailable");
        const data: Page = await response.json();
        if (live) {
          setPage(data);
          setError(false);
        }
      } catch {
        if (live) setError(true);
      } finally {
        if (live) timer = setTimeout(poll, 3000);
      }
    }
    void poll();
    return () => {
      live = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [cursor]);
  return (
    <section className="panel processing-panel" aria-label="Invoice processing">
      <div className="panel-heading">
        <div>
          <h2>Invoice processing</h2>
          <p>
            Persistent background activity. Captured text still requires
            financial extraction and review.
          </p>
        </div>
      </div>
      <div className="processing-content">
        {error && (
          <p role="status">
            Processing status is unavailable. Reconnecting automatically…
          </p>
        )}
        {!page && !error && <p role="status">Loading processing activity…</p>}
        {page && (
          <>
            <p>
              {page.active_count} of {page.admission_limit} active job slots in
              use.
            </p>
            {!page.items.length ? (
              <p>
                No invoice processing jobs on this page. Accepted uploads appear
                here.
              </p>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Invoice</th>
                      <th>Status</th>
                      <th>Attempts</th>
                      <th>Next step</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((job) => (
                      <tr key={job.id}>
                        <td>
                          <span className="mono">
                            {job.invoice_id || "Unassigned"}
                          </span>
                        </td>
                        <td>
                          <span className={`status status-${job.state}`}>
                            {processingLabel(job)}
                          </span>
                          {job.error_code && (
                            <small className="processing-code">
                              {job.error_code}
                            </small>
                          )}
                        </td>
                        <td>{job.attempts} / 5</td>
                        <td>
                          {job.next_action}
                          {job.next_attempt_at && (
                            <small className="processing-code">
                              Next attempt: {job.next_attempt_at}
                            </small>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="processing-pagination">
              {cursor && (
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    setCursor(null);
                    setPage(null);
                  }}
                >
                  First page
                </button>
              )}
              {page.page_info.has_next_page && (
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    setCursor(page.page_info.end_cursor);
                    setPage(null);
                  }}
                >
                  Next page
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
