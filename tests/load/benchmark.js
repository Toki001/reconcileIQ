import http from "k6/http";
import { check, sleep } from "k6";
import crypto from "k6/crypto";
import execution from "k6/execution";
import { Counter, Trend } from "k6/metrics";
function hex(bytes) {
  return Array.from(new Uint8Array(bytes), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}
const scenario = __ENV.SCENARIO || "baseline";
const root = __ENV.API_URL || "http://api:8000";
const pdf = open("/fixtures/invoice-0003.pdf", "b");
const accepted = new Counter("invoice_accepted");
const rejected = new Counter("invoice_capacity_rejected");
const completed = new Counter("invoice_completed");
const failed = new Counter("invoice_failed");
const acceptance = new Trend("invoice_acceptance_ms");
const total = new Trend("invoice_end_to_end_ms");
const auth = new Trend("authenticated_request_ms");
const anonymous = new Trend("anonymous_request_ms");
const pages = new Trend("pagination_request_ms");
export const options = {
  scenarios:
    scenario === "sustained"
      ? {
          ingestion: {
            executor: "constant-arrival-rate",
            rate: 2,
            timeUnit: "1m",
            duration: __ENV.DURATION || "3m",
            preAllocatedVUs: 2,
            maxVUs: 4,
          },
        }
      : scenario === "burst"
        ? {
            burst: {
              executor: "per-vu-iterations",
              vus: 12,
              iterations: 1,
              maxDuration: "2m",
            },
          }
        : {
            baseline: {
              executor: "constant-vus",
              vus: 2,
              duration: __ENV.DURATION || "20s",
            },
          },
  systemTags: [
    "method",
    "status",
    "name",
    "scenario",
    "check",
    "expected_response",
  ],
  summaryTrendStats: ["min", "med", "p(95)", "p(99)", "max", "avg"],
  thresholds: { checks: ["rate==1"] },
};
http.setResponseCallback(http.expectedStatuses(200, 201, 202, 204, 401, 429));
function request(method, path, body, headers, name) {
  const response = http.request(method, root + path, body, {
    headers,
    tags: { name },
    timeout: "15s",
  });
  if (![200, 201, 202, 204, 401, 429].includes(response.status)) {
    let code = "UNKNOWN";
    try {
      const value = response.json().code;
      if (typeof value === "string" && /^[A-Z_]{1,64}$/.test(value))
        code = value;
    } catch {
      /* A response body is never logged. */
    }
    console.error(
      JSON.stringify({ probe: name, status: response.status, code }),
    );
  }
  return response;
}
export default function () {
  const trace =
    "00-" +
    hex(crypto.randomBytes(16)) +
    "-" +
    hex(crypto.randomBytes(8)) +
    "-01";
  const headers = {
    Authorization: "Bearer " + __ENV.REVIEWER_TOKEN,
    "Content-Type": "application/json",
    traceparent: trace,
  };
  if (scenario === "baseline") {
    const a = request("GET", "/v1/me", null, headers, "authenticated-identity");
    auth.add(a.timings.duration);
    check(a, { "authenticated identity": (r) => r.status === 200 });
    const b = request("GET", "/v1/me", null, {}, "anonymous-identity");
    anonymous.add(b.timings.duration);
    check(b, { "anonymous denied": (r) => r.status === 401 });
    const p = request(
      "POST",
      "/graphql",
      JSON.stringify({
        query:
          "{ invoices(first: 10) { edges { node { id } } pageInfo { hasNextPage endCursor } } }",
      }),
      headers,
      "invoice-pagination",
    );
    pages.add(p.timings.duration);
    check(p, {
      "pagination succeeded": (r) => r.status === 200 && !r.json().errors,
    });
    sleep(0.1);
    return;
  }
  const started = Date.now();
  const key = __ENV.RUN_TAG + "-" + execution.scenario.iterationInTest;
  const grant = request(
    "POST",
    "/v1/invoices/upload-requests",
    JSON.stringify({
      filename: "SYNTHETIC-m08.pdf",
      content_type: "application/pdf",
      size_bytes: pdf.byteLength,
      sha256: crypto.sha256(pdf, "hex"),
    }),
    { ...headers, "Idempotency-Key": key + "-reserve" },
    "upload-reservation",
  );
  if (!check(grant, { "reservation accepted": (r) => r.status === 201 }))
    return;
  const invoice = grant.json().invoice_id;
  const put = request(
    "PUT",
    "/v1/invoices/" + invoice + "/document",
    pdf,
    { ...headers, "Content-Type": "application/pdf" },
    "document-upload",
  );
  if (!check(put, { "MinIO document accepted": (r) => r.status === 204 }))
    return;
  const admission = request(
    "POST",
    "/v1/invoices/" + invoice + "/upload-complete",
    JSON.stringify({ expected_version: 1 }),
    { ...headers, "Idempotency-Key": key + "-complete" },
    "upload-completion",
  );
  check(admission, {
    "admission bounded": (r) =>
      r.status === 202 || (scenario === "burst" && r.status === 429),
  });
  acceptance.add(admission.timings.duration);
  if (admission.status === 429) {
    rejected.add(1);
    return;
  }
  if (admission.status !== 202) return;
  accepted.add(1);
  const job = admission.json().job_id;
  for (let i = 0; i < 180; i++) {
    const result = request("GET", "/v1/jobs/" + job, null, headers, "job-poll");
    if (result.status === 200) {
      const state = result.json().state;
      if (state === "succeeded") {
        completed.add(1);
        total.add(Date.now() - started);
        return;
      }
      if (state === "failed") {
        failed.add(1);
        check(result, { "processing succeeded": () => false });
        return;
      }
    }
    sleep(0.5);
  }
  check(null, { "job reached terminal state": () => false });
}
export function handleSummary(data) {
  return { ["/results/" + scenario + ".json"]: JSON.stringify(data, null, 2) };
}
