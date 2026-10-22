# ReconcileIQ API contracts — milestone 02

`openapi.yaml` is the reviewed OpenAPI **3.1.0** contract for the implemented REST interface. `schema.graphql` is the hand-authored, schema-first read interface; do not overwrite it from Strawberry. The Python implementation must satisfy both. Authentication applies to all business routes and GraphQL; liveness, readiness and schema documentation are public. Version: REST `/v1`, API release `0.2.0`.

## Authentication and roles

Send `Authorization: Bearer <opaque-api-key>`. The server hashes the key with SHA-256, looks up an active credential, and derives its organization, actor and role. Caller-supplied organization/actor fields are forbidden. Resource lookups are scoped to that organization; another tenant's ID returns 404 (GraphQL nullable node queries return null). Invalid, absent or revoked keys return 401 with `WWW-Authenticate: Bearer`.

`make env` generates independent admin/reviewer/auditor keys in ignored mode-0600 `.env`. Bootstrap provisions only their digests in PostgreSQL/SQLite. The Next.js server receives only the auditor key, through `RECONCILE_WEB_API_TOKEN`; it is never a `NEXT_PUBLIC_*` variable. The local dashboard has no end-user login and must remain on loopback. This is a local API-key implementation, not an identity-provider/session deployment. Replacing an environment key provisions an additional credential; revoke the old credential explicitly before treating rotation as complete.

| Operation                                      | Admin                           | Reviewer                        | Auditor                         |
| ---------------------------------------------- | ------------------------------- | ------------------------------- | ------------------------------- |
| REST reads, GraphQL reads                      | Yes                             | Yes                             | Yes                             |
| Request/complete invoice upload                | Yes                             | Yes                             | No                              |
| Start reconciliation, record review            | Yes                             | Yes                             | No                              |
| Create/update purchase orders, record receipts | Yes                             | No                              | No                              |
| Replay a failed retryable job                  | Yes                             | No                              | No                              |
| Supplier webhook                               | Separate supplier HMAC identity | Separate supplier HMAC identity | Separate supplier HMAC identity |

Role checks run before idempotency replay. API keys, cursor-signing keys, supplier secrets, signed upload URLs, documents and driver exception details must not enter logs or audit details. Local storage uses its own credentials. Use TLS and a proper deployment/identity boundary before exposing this outside the local environment.

## REST operations

| Method and path                                              | Success | Meaning                                                                   |
| ------------------------------------------------------------ | ------- | ------------------------------------------------------------------------- |
| `POST /v1/invoices/upload-requests`                          | 201     | Reserve an invoice and request a ten-minute presigned PDF PUT             |
| `POST /v1/invoices/{invoice_id}/upload-complete`             | 202     | Verify the upload, seal bytes, admit extraction                           |
| `GET /v1/invoices`, `GET /v1/invoices/{invoice_id}`          | 200     | Paginated collection and detail                                           |
| `POST /v1/purchase-orders`                                   | 201     | Validate lines/totals and create an audited PO                            |
| `GET /v1/purchase-orders`, `GET /v1/purchase-orders/{po_id}` | 200     | Paginated collection and detail                                           |
| `PATCH /v1/purchase-orders/{po_id}`                          | 200     | Update an open PO description/status with optimistic version check        |
| `POST /v1/goods-receipts`                                    | 201     | Record positive decimal quantities against known PO line IDs              |
| `POST /v1/reconciliations`                                   | 202     | Admit a versioned reconciliation request                                  |
| `POST /v1/invoices/{invoice_id}/review-decisions`            | 201     | Append a reasoned accept/reject decision for the current completed result |
| `GET /v1/jobs/{job_id}`                                      | 200     | Read durable execution state                                              |
| `POST /v1/jobs/{job_id}/replay`                              | 202     | Admit a new execution for a failed, retryable original                    |
| `POST /v1/webhooks/suppliers`                                | 202     | Authenticate and deduplicate a supplier notice                            |

Every bearer-authenticated mutation requires an `Idempotency-Key`. Mutable existing resources additionally require `expected_version`. A successful state change increments that version; stale writes return `409 VERSION_CONFLICT`. Monetary PO fields cannot be changed through PATCH. A review must reference the invoice's latest completed run in a reviewable state and include a 10–1000 character reason. `reviewed_accepted` records a review decision only; **payments and spending authorization are prohibited**. Audit events, state and idempotency records commit atomically.

Uploads accept only declared `application/pdf`, 5 bytes–10 MiB, and a lowercase hex SHA-256. PUT the exact bytes to `upload_url` with `required_headers`. Completion verifies object size, MIME, `%PDF-` signature and digest, then writes a separate sealed key that was never presigned. Reusing the still-valid upload URL cannot change the worker's verified input. This verifies integrity and a minimal format signature; parsing, malware screening and extraction are later processing work. Incomplete uploads and orphan sealed objects after a failed DB transaction require a future retention/cleanup policy. Expired upload grants require a new reservation with a new key; replaying the old request returns its original, possibly expired grant.

## Money, quantities and dates

Amounts are JSON integers in **minor units**, range `0..9007199254740991` inclusive; floats and booleans are rejected. A currency is always explicit. Supported ISO 4217 subset: USD/EUR/PHP (2 decimals), JPY (0), KWD (3). Unknown currencies fail validation; no implicit conversion or assumption of two decimals. GraphQL `MinorUnits` uses the same safe integer range rather than GraphQL's 32-bit `Int`. Unknown invoice money is null until validated extraction, never a fabricated zero. Metrics group balances by currency.

Quantities are positive decimal **strings**, at most nine integer and six fractional digits. Each line rounds `quantity × unit_price_minor` to an integer using Decimal `ROUND_HALF_UP`. PO subtotal equals the sum of line totals; discount cannot exceed subtotal; total equals subtotal minus discount plus explicitly supplied exclusive tax. This milestone validates supplied tax amounts, not jurisdictional tax correctness. Exact policy IDs `two_way_exact_v1` and `three_way_exact_v1` reserve zero-tolerance policies; full matching is later work. Dates are `YYYY-MM-DD`; timestamps are ISO 8601 UTC with an offset (`Z` allowed).

## Stable cursor pagination

REST: `?first=20&after=<opaque-cursor>` → `{items, page_info: {has_next_page, end_cursor}}`. GraphQL connections expose `{edges: [{cursor, node}], pageInfo: {hasNextPage, endCursor}}`. `first` defaults to 20 and must be 1–100. Pass the last page's end cursor unchanged. No offsets or total-count promise on connections.

Collections order ascending by immutable server-created timestamp then unique ID (audit uses occurrence time then ID). A signed cursor binds organization, collection, exact filters, last position and the first page's upper-watermark position. Normal later inserts do not enter that traversal. Tampering, a different tenant, collection or filter returns `INVALID_CURSOR`. This gives stable keyset traversal, not a database snapshot across requests: updates that change filter membership can affect subsequent pages. Cursors contain signed, encoded positions, are not encrypted, and currently have no expiry; rotating `RECONCILE_CURSOR_SECRET` invalidates them. Keep them opaque. Empty pages return a null end cursor.

## Asynchronous work and idempotency

202 means **durable admission**, not completed processing. The response contains `job_id`, optional invoice/run IDs and `status_url`; `Location` points to the job and `Retry-After: 2` suggests polling. The job, transactional outbox envelope, audit event and replay record commit together. At most 500 active jobs per organization are admitted; further requests return `429 ADMISSION_LIMIT` and `Retry-After: 30`.

Job states are `pending_dispatch`, `queued`, `running`, `succeeded`, `failed`, `cancelled`. Milestone 02 implements admission and status reads: **no business outbox publisher/extraction/reconciliation worker runs yet**, so newly admitted jobs remain `pending_dispatch`. The existing Celery bootstrap probe is independent. Seeded completed results are explicitly `synthetic: true`. The replay endpoint creates a new execution with `replay_of`, preserves original failure/history, requires an admin reason and rejects non-retryable or already-replayed executions. Future workers must provide idempotent effects and bounded retries.

Use a fresh organization-wide key for each logical mutation: 8–128 ASCII letters/digits or `_ . : -`. Keys are retained at least 24 hours (currently indefinitely). The fingerprint includes authenticated actor, HTTP method/path and validated normalized JSON body, so JSON object order/whitespace does not change it. Same key/fingerprint returns the exact committed status/body/Location and `Idempotency-Replayed: true`, even if versions have since changed. A changed actor, route or body returns `409 IDEMPOTENCY_CONFLICT`. Concurrent duplicates have one transactional business effect. Errors roll back the claim; retry a transient failure with the same key and same body. Success grants include their original expiry. Do not reuse a key for a different action or change its payload after an uncertain network result. Webhooks use their event identity instead of this header.

## Supplier webhook authentication

Configure `RECONCILE_SUPPLIER_WEBHOOKS` as a secret JSON mapping of supplier IDs to `{organization_id, secret}`; the supplied ID never chooses an arbitrary tenant. Send `Content-Type: application/json`, `X-Supplier-Id`, Unix-seconds `X-Timestamp`, and `X-Signature: v1=<lowercase-hex-HMAC-SHA256>`. The signed bytes are exactly `timestamp + "." + raw_request_body`, using the configured supplier secret. Timestamp tolerance is ±300 seconds. Verify signatures before decoding JSON. Payloads are capped at 64 KiB (all API requests at 128 KiB).

```python
raw_body = json.dumps(notice, separators=(",", ":")).encode()
timestamp = str(int(time.time()))
signature = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, "sha256").hexdigest()
# Send these exact raw_body bytes with X-Signature: v1=<signature>.
```

Body fields: `event_id`, `event_type: "invoice.available"`, `supplier_invoice_number`, `document_reference`. Unknown fields are rejected. Notices are untrusted references; they do not fetch arbitrary URLs or directly create authoritative financial facts. Deduplication is by `(organization, supplier, event_id)`: identical normalized payload returns the original 202; a changed payload for that identity returns 409. Timestamp/signature must be fresh on every delivery, including retries. HMAC failures return `WEBHOOK_INVALID` or `WEBHOOK_EXPIRED` with 401. A separate bearer credential for the same organization is needed to poll the resulting job.

## Errors and GraphQL transport

REST errors use `application/problem+json` and the explicit `ApiProblem` schema: `type`, `title`, `status`, `code`, `detail`, `instance`, `trace_id`, `errors: [{path, code, message}]`. Raw invalid inputs, exception stacks and secrets are excluded. Trace IDs are server-generated and also returned in `X-Request-Id`. Business responses have `Cache-Control: no-store`.

| Status | Codes                                                        |
| ------ | ------------------------------------------------------------ |
| 401    | `UNAUTHENTICATED`, `WEBHOOK_INVALID`, `WEBHOOK_EXPIRED`      |
| 403    | `FORBIDDEN`                                                  |
| 404    | `NOT_FOUND`                                                  |
| 409    | `VERSION_CONFLICT`, `STATE_CONFLICT`, `IDEMPOTENCY_CONFLICT` |
| 413    | `VALIDATION_ERROR` (body size)                               |
| 422    | `VALIDATION_ERROR`, `INVALID_CURSOR`, `UPLOAD_INVALID`       |
| 429    | `ADMISSION_LIMIT`                                            |
| 500    | `INTERNAL_ERROR`                                             |
| 503    | `DATABASE_UNAVAILABLE`, `DEPENDENCY_UNAVAILABLE`             |

Retry transient 429/503 after `Retry-After`, preserving the key. Readiness retains the separate `Health` schema and may return 503. Unsupported HTTP methods return 405. Validation messages describe field errors but never include original input values.

GraphQL is authenticated **POST `/graphql`** only, with `{query, variables, operationName?}` JSON. No mutations, subscriptions, GET queries, browser IDE or websocket transport. Query fields cover invoice dashboards, POs, runs, discrepancy details, nested discrepancies, per-entity audit timelines and currency balances. Bound query depth (10), aliases (30), tokens (5000), request body and connection sizes limit work. Introspection remains enabled for local contract verification. Audit timelines are per entity; a run and invoice each have their own audit events.

Missing/invalid bearer identity is a transport-level 401 problem. GraphQL execution/validation errors otherwise use standard `errors[].extensions.code` (API codes above plus `GRAPHQL_VALIDATION_FAILED`), with `trace_id` for execution errors. Errors redact internal exceptions and query variables. Clients must check `errors` even on HTTP 200; the generated wrapper rejects partial data with errors. Requests with invalid GraphQL transport input can return 400/422. A syntactically valid cross-tenant node ID returns null, not another tenant's record.

## Generated clients and compatibility gates

```sh
make contracts        # Export REST schema; generate REST TS and GraphQL operation types/documents
make contracts-check  # Compare server schemas and both generated artifacts without rewriting
make check            # Also executes authenticated HTTP compatibility/security/behavior tests
```

REST uses `openapi-typescript` and `openapi-fetch`; GraphQL Code Generator uses reviewed SDL plus `operations/*.graphql`, generating `TypedDocumentNode` artifacts. `@reconcileiq/client` exports `createApi(baseUrl, fetcher, token)` and `createGraphqlClient(baseUrl, token, fetcher)` plus typed documents. Supply tokens server-side in the web app. No external Python client is generated in this milestone.

`tests/test_api_contracts.py` compares actual FastAPI `/openapi.json` with the canonical YAML and actual authenticated Strawberry HTTP introspection with canonical SDL, normalized through the GraphQL schema model. Descriptions/order do not trigger false GraphQL drift; field types, nullability, defaults, arguments, enum members and scalars do. Generated clients have independent drift checks and Jest transport tests. `scripts/contracts_smoke.py` repeats compatibility checks against the running containers and exercises a real presigned MinIO PUT, upload completion, durable job reads, role denial, idempotent PO creation and authenticated webhook replay. It appends labelled local probe records and retains all audit history.

Changes to required inputs, names, types, nullability, enums or semantics require contract review and client regeneration. Prefer additive optional fields; plan a versioned migration before breaking external consumers. Passing compatibility checks demonstrates implementation agreement, not authorization for a production rollout.

## Milestone 06 evidence and human review

Additive REST interfaces are reviewed in `openapi.yaml`: `GET /v1/me`, `GET /v1/ai/status`, admin `POST /v1/ai/verify`, authenticated invoice `/workspace`, `/document` and `/pages/{page}`, `/structured-extractions`, and `/corrections`. Existing GraphQL dashboards, reconciliation/discrepancy lists and audit cursors retain their schema-first interface unchanged.

The bounded workspace returns at most 20 candidate versions, 100 audit events and an immutable latest result with its PO/receipt snapshot. A truncation flag indicates retained older history. Full audit and reconciliation histories use existing signed cursor-paginated GraphQL lists. Candidates are untrusted Pydantic evidence with observed/missing/inferred/human origins, exact page quotes and model/prompt version. Monetary values remain ISO 4217 integer minor units; decimal quantities/rates are strings.

All writers require admin/reviewer credentials, Idempotency-Key (except exact-content PDF PUT), and expected invoice version. Corrections additionally bind the latest candidate ID and require a reason. Changed evidence is server-marked human and appended; callers cannot forge provider provenance. Reconciliation remains asynchronous 202 with a job URL and stable retries. Review acceptance now requires an unchanged matching snapshot and an exact matched result for structured candidates; old legacy contracts remain supported. It never authorizes payment.

Document PUT accepts only an authenticated pending upload's exact PDF size/hash and is repeatable before completion. Completion seals the retained input; later PUT is rejected. Document GET verifies retained bytes and returns PDF with nosniff/sandbox headers. Page previews return only PNG pixels with `X-Document-Pages`; resource-bounded rendering never enables embedded PDF scripts. No object paths are supplied by clients. Tenant scoping applies to documents, candidates, decisions, results and timeline.

New explicit problem codes include `FREE_TIER_UNVERIFIED`, `AI_SYNTHETIC_INPUT_REQUIRED`, `DOCUMENT_UNAVAILABLE`, `STALE_RECONCILIATION`, `REVIEW_REQUIRES_MATCH`, `PUBLICATION_CONFLICT` and `EXTRACTION_SUPERSEDED`. AI status returns availability rather than claiming eligibility from a successful generation. Provider errors are safe job diagnostic codes; auth/schema/model errors are permanent while service/quota/timeouts use the existing bounded retry lifecycle.
