# ReconcileIQ — Codex Project Specification

> Status: implementation blueprint, not verified product functionality.  
> Project: **ReconcileIQ**, intelligent accounts payable invoice reconciliation.  
> Primary implementation: **Python backend + TypeScript frontend**.  
> **Codex:** read this file and `AGENTS.md` before changing code. Implement the user-requested milestone only; use this document for architecture and scope.

## 1. Product definition

Build an auditable operations application that receives supplier invoices, extracts structured data, validates financial fields, compares invoices with purchase orders and goods receipts, and routes exceptions to a finance reviewer. **AI is a bounded data-extraction and explanation subsystem. All financial matching, arithmetic and status changes are deterministic, typed code. No payments are executed.**

### Primary roles and workflows

- **Finance administrator**: configure currencies, tax settings, tolerance policies, vendors and permissions.
- **Accounts payable reviewer**: inspect invoices, compare purchase orders and receipts, accept/reject proposed matches, resolve exceptions with reasons.
- **Auditor**: read immutable event and approval histories; cannot modify data.
- Flow A: upload a PDF via presigned URL → validate checksum → queue extraction → OCR if needed → structured AI parse → schema and amount checks → immutable extraction version.
- Flow B: match invoice lines to PO and received goods → calculate differences deterministically → classify `matched`, `exception`, `needs_review`, `duplicate`, `unmatched` → reviewer action.
- Flow C: duplicate delivery, provider failure or low quality scan → bounded retry / terminal failure → exception visible → authorized replay, without duplicate invoices.

### Explicit non-goals

No payment instructions, bank credentials, treasury integration, automated authorization of spending, synthetic financial figures represented as real, generic AI chat widgets, and no unsupported promise of compliance or accounting certification.

## 2. Tech stack and monorepo shape

- **Frontend**: Next.js App Router, TypeScript, React, Tailwind; accessible, responsive real finance dashboard.
- **Backend**: Python FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, Strawberry GraphQL.
- **Jobs**: Celery + RabbitMQ; durable DLX plus explicit terminal failure records, idempotency table and structured recovery.
- **Object store**: MinIO / S3; adapter architecture for text extraction, OCR and LLM providers.
- **Database**: PostgreSQL; integer minor-unit currency amounts, Decimal/NUMERIC for quantities and percentages; never financial floats.
- **Contracts and clients**: OpenAPI 3.1, GraphQL SDL, openapi-typescript/openapi-fetch, GraphQL Code Generator.
- **Tooling**: uv for Python, pnpm for frontend; Ruff, mypy, pytest/pytest-asyncio, HTTPX, Jest, Docker, GitHub Actions, GHCR.
- **Operations**: OpenTelemetry, collector, Grafana/Tempo/Prometheus, k6 load tests.

```text
reconcileiq/
├── AGENTS.md
├── codex.md
├── apps/web/
├── services/api/app/{auth,invoices,purchasing,reconciliation,graphql,uploads}/
├── services/worker/app/{tasks,extraction,dead_letter}/
├── packages/contracts/{openapi.yaml,schema.graphql}/
├── packages/generated/
├── packages/database/{models,migrations,seeds}/
├── packages/reconciliation/
├── packages/ai/{schemas,prompts,providers,evals}/
├── tests/{unit,integration,fixtures,contract,load}/
├── scripts/
├── infra/{otel,grafana,docker}/
├── docs/{architecture,operations,verification}/
├── .github/workflows/
├── docker-compose.yml
├── pyproject.toml
└── package.json
```

Architecture boundaries: FastAPI request handlers orchestrate use cases; pure reconciliation package owns monetary rules; worker owns document parsing; database code owns transactions/migrations; UI relies on typed clients; AI provider never owns authoritative match decisions.

## 3. Essential product behaviors

1. User authentication and organization-scoped roles; back-end derivation of organization context.
2. Functional Invoices, Purchase Orders, Reconciliation, Exceptions, Audit Log, Vendor details and Settings screens.
3. Upload invoice as PDF (size/MIME/magic byte validation); object key/URL scoped to tenant; status visible after upload.
4. Ingest purchase orders and goods receipts via validated endpoints and a deterministic seed/import route.
5. Extract and review vendor, invoice number, dates, currency, line items, discounts, taxes and totals with source page(s) and extraction version metadata.
6. Highlight discrepancies: not-on-PO, quantity exceeds goods received, unit price mismatch, unexpected tax, duplicate invoice, unknown vendor, suspicious sums.
7. Two-way (invoice vs PO) and three-way (invoice vs PO vs goods receipt) matching; reviewer chooses workflow policy; AI may explain but cannot overrule deterministic checks.
8. Track extraction progress, human review assignments, final decisions and operator-controlled failure replay.
9. Cursor pagination and filters for invoice states, vendor, dates, amount range and exceptions.
10. All meaningful state transitions recorded in immutable audit events. No fake success messages or inert buttons.

## 4. Canonical contracts

- REST canonical file: `packages/contracts/openapi.yaml`. Include server-returned job status and typed `application/problem+json`-style errors or documented consistent equivalent.
- GraphQL canonical SDL: `packages/contracts/schema.graphql`, implemented by Strawberry; introspected running schema must match checked-in SDL.
- REST: `POST /v1/invoices/upload-requests`, `POST /v1/invoices/{id}/upload-complete`, `GET /v1/jobs/{id}`, `POST /v1/purchase-orders`, `POST /v1/goods-receipts`, `POST /v1/reconciliations`, `POST /v1/invoices/{id}/review-decisions`, `POST /v1/jobs/{id}/replay`, `POST /v1/webhooks/suppliers`.
- GraphQL: `invoices(first,after,filter)`, `invoice(id)`, `purchaseOrders(first,after)`, `reconciliationRuns(invoiceId)`, `exceptions(first,after)`, `auditTimeline(entityId)`, `dashboardMetrics`.
- HTTP mutation semantics: idempotency keys for externally retried actions; 202 and job ID for background extraction; 409 on concurrency conflicts, 422 invalid accounting validation, 429 quota, 503 dependency failure.
- Cursor pagination: opaque cursor, deterministic sort including unique ID tiebreaker, maximum 100 per page. No unbounded list API.
- Generate frontend clients from schemas, never manually drift types. Test versioned contracts, auth, webhook HMAC signatures and error shape.

## 5. Database and accounting invariant requirements

Model: `organizations`, `users`, `organization_memberships`, `vendors`, `purchase_orders`, `purchase_order_lines`, `goods_receipts`, `goods_receipt_lines`, `invoices`, `invoice_lines`, `extracted_fields`, `extraction_versions`, `reconciliation_runs`, `reconciliation_line_results`, `discrepancies`, `review_decisions`, `audit_events`, `idempotency_records`, `job_execution_records`, `failed_jobs`, optionally `outbox_events`.

Critical rules:

- Monetary amounts are **integer minor units** plus explicit ISO 4217 currency; use the proper exponent per currency (do not assume two decimals). Use Python `Decimal` for intermediate calculations and NUMERIC for fractional quantities, exchange rates (if added) and percentage rates.
- Model tax-inclusive vs tax-exclusive values explicitly. Enforce line totals, tax, discounts, rounding policy and currency match before a reconciliation decision. **Never convert currencies silently**.
- Define tolerance policies by currency and organization, e.g. ≤1 minor unit residual round-off or explicit policy amount; no hidden matching threshold.
- Unique `(organization_id,vendor_id,normalized_invoice_number)` duplicate-detection key with policy for corrected/reissued versions. Hash the file and preserve extraction versions separately.
- PO lines and received quantities are never silently changed by AI output. Keep reconciliation results immutable per run/version; optimistic locking on review decisions.
- Composite tenant-aware indexes, foreign keys, created/updated timestamps and scoped authorization for every read/write.
- Alembic incremental migrations; clean install, upgrade and downgrade **where safe** plus expand-contract example. Do not drop audit records.
- Deterministic synthetic seed: 20 vendors, 100 POs, 200 invoices, receipts, deliberate duplicates, price discrepancies, partial receipts, multiple supported currencies, realistic synthetic PDF invoice fixtures.

## 6. Celery/RabbitMQ operational guarantees

**Canonical worker**: `extractInvoice(invoiceId,documentHash,extractionVersion)` loads object from MinIO, checks type/hash/size, extracts text or OCR, obtains structured provider response, validates schema/financial consistency and persists a new immutable extraction result.

- Acknowledgments and worker crash recovery must be explicitly designed/tested for at-least-once execution. Use DB uniqueness/leases to suppress duplicate business effects.
- Worker concurrency=4, prefetch_multiplier=1 as starter values; per-tenant concurrency/quotas, max queued admissions=500, reject with 429/Retry-After rather than accumulating unbounded work.
- Maximum **5 attempts total** for transient OCR/LLM/network failures, exponential backoff with jitter; honor Retry-After where present. Invalid PDF and definitive schema failure non-retryable.
- Configure RabbitMQ DLX/routing TTL where appropriate **and** explicitly record Celery terminal failures in durable DB; do not assume Celery's exhausted retries magically populate RabbitMQ DLX.
- Safe manual replay requires admin authorization, reason and audit event; replay is idempotent and does not duplicate invoice or match state.
- For the boundary "object stored but enqueue failed", implement transactional outbox or recoverable `pending_enqueue` state with a reconciliation publisher. Do not leave permanent ghost uploads.
- Every exception includes code, retryability, redacted diagnostic context, trace ID and next action. UI exposes real state.

## 7. AI extraction and deterministic matching

- Use PDF text extraction where possible; OCR adapter for image/scanned PDFs; model adapter with structured Pydantic output, schema repair limits, fixed token budget and retry policy. Send only minimum needed content to model; protect document PII.
- Structured fields: invoice number, vendor identifier, invoice date, due date, currency, PO reference, line description/quantity/unit price/line total, subtotal, discount, tax, grand total, page provenance and optional uncertainty/review flags.
- Never mark guessed fields as verified. Contradictory totals or uncertain vendor matches trigger review.
- Matching engine (pure, deterministic): normalize supplier and PO reference; perform exact item reference match first, controlled fallback matching where explainable; compute quantities/prices/taxes with Decimal; check delivered quantities; classify exceptions with source data references.
- AI explanation is **non-authoritative text** generated from deterministic discrepancy objects, never an alternative calculation.
- Human override requires actor, reason, timestamp and retained before/after fields. No payments, no automatic approval of ambiguous invoices.

## 8. Evaluation specification (targets, not measured results)

Create `packages/ai/evals/golden.jsonl` and 100 reproducible synthetic documents with expected normalized field values, line-item matches, document labels and page spans.

Coverage: varied layouts, 1–3 pages, high/low OCR quality, multiple currencies, tax and discount variants, purchase-order mismatches, duplicate invoice numbers, malicious document instructions, missing fields and intentionally irreconcilable items. Include evaluation **separate** from deterministic financial matching tests.

Metrics:

- Normalized invoice number **exact match ≥95%**; denominator only cases with known ground truth; report missing and hallucinated separately.
- Grand total **exact minor-unit match ≥99%** across labeled cases.
- Line-item extraction **micro-F1 ≥90%** with fixed item/field matching policy.
- **False automatic match ≤0.5%** on labeled mismatched examples, with denominator explicit and no payment approval implied.
- p95 end-to-end processing **≤25 seconds** for ≤3-page documents under the documented benchmark environment, excluding human-review wait; report queue wait separately.
- AI variable cost **≤$0.05/document**; separately account for OCR and infrastructure. Record prompt/completion tokens, model/price versions and cost calculation.
- Model mocked for normal CI, optional live benchmark with real measured values; fail evaluation on schema/prompt drift when baseline regression is detected.

State sample-size limitations transparently: with only 100 cases, proving a 0.5% error rate statistically is not feasible. Treat it as an engineering target and supplement with more mismatch-specific test cases before presenting a production reliability claim.

## 9. Tests and verification

- Python unit: accounting invariants, tax rounding, currency exponent handling, duplicate detection, PO line matching, access control, state machine, outbox publishing logic.
- Backend integration (pytest + HTTPX + PostgreSQL/RabbitMQ containers): uploads, extraction versions, idempotent deliveries, signed supplier webhooks, migrations, DLX and replay, audit and review decisions.
- UI Jest: typed client integration, money formatting, status rendering, pagination, review form validation.
- Failure injection: PDF uploaded but message not enqueued; OCR success but DB commit failure; repeated queue delivery; broker outage; model refusal; missing PO; stale review version; malicious document prompt injection.
- ≥90% measured coverage for pure reconciliation logic; report actual test counts and failures. Normal CI must not spend external AI credits.
- Maintain `docs/verification/` with commands, fixture assumptions, tested platforms and honest open issues.

## 10. CI/CD, telemetry and SLOs

- PR GitHub Actions: `uv sync --frozen`, Ruff, mypy, pytest, Alembic migrations, frontend pnpm typecheck/lint/Jest, contract drift, service tests and Docker smoke builds; lockfile checks.
- Main/tag: non-root multi-stage images for web/API/worker, signed or verifiable immutable GHCR tags, minimal permissions, SHA-pinned actions; no publishing credentials for untrusted PRs.
- Deploy ordering: backward-compatible DB migration → API and worker release → data backfill → delayed destructive cleanup after compatibility window. Document restore and rollback implications for audit trails.
- OTel traces for HTTP upload → object storage → RabbitMQ task → PDF/OCR → AI provider → reconciliation → review action. Include trace propagation through jobs.
- Grafana dashboards: queued/processing/failed docs, page count, worker throughput, DLQ trend, extraction latency, manual-review backlog, reconciliation error codes, AI tokens/cost and provider failure rates.
- Security: no invoice content, financial identifiers, raw PDFs or supplier PII in spans/logs. Tenant identifiers must not become high-cardinality metrics labels.
- Load benchmark: sustain at least 2 invoices/minute under documented reference profile; report p50/p95/p99, queue age, document sizes and partial failure rate.

## 11. Eight sequential Codex milestones

Execute one at a time, in order, with actual verification and a small focused Git commit after each:

1. **Scaffolding:** uv/pnpm monorepo, Compose services, health checks, finance UI shell, environment validation, `AGENTS.md` and docs.
2. **Contracts:** reviewed OpenAPI 3.1 + GraphQL SDL, actual handlers, generated typed clients, pagination, auth and contract tests.
3. **Data:** SQLAlchemy models, Alembic migrations, currency and tax rules, deterministic PO/receipt/invoice seeds and sample PDFs.
4. **Workers:** invoice extraction through Celery/RabbitMQ/MinIO, admission control, 5-attempt retry, durable dead-letter workflow and recovery.
5. **Tests:** pytest/Jest suites, containerized integrations, financial invariants and intentional partial-failure injection.
6. **AI:** structured extraction and optional OCR, deterministic reconciliation, 100-case golden suite, evaluation, budget and latency reports.
7. **CI/CD:** secured GitHub Actions, images, GHCR publishing, repeatable migration/deployment/runbook.
8. **Instrumentation:** OTel traces, metrics, dashboards, overload/load test and measured performance report.

### Stop condition / proof of milestone

- Run existing relevant checks; if check cannot run, identify exact missing dependency rather than declaring success.
- Add tests for new behaviors, keep generated clients synchronized, audit dependency and schema changes.
- Real UI features only; removed/replaced features have dead code deleted and tests updated.
- Do not make arbitrary scope additions, silently alter accounting behavior or weaken safety targets.
- Commit `milestone(N): <short description>` after successful checks; do not overwrite/commit unrelated user changes.
- Summarize files changed, migrations generated, test commands/results and remaining risks.

## 12. Demo and handoff requirements

Produce working local demo with three invoice types: (1) exactly matched PO + receipt, (2) quantity discrepancy requiring review, (3) duplicate vendor invoice flagged; also demonstrate an OCR/provider failure that goes to the terminal-failure review queue. Show immutable audit trail, manually approved exception and synthetic-only data. Deliver README, diagrams, contract docs, generated sample PDFs, seed instructions, eval results and operational runbook.

## 13. Codex kickoff prompt

> Read `AGENTS.md` and `codex.md` entirely. Review current working tree. Implement **milestone 1 only** of ReconcileIQ, keeping money in correctly interpreted integer minor units and AI limited to extraction/explanations. Verify available commands, record evidence, and commit `milestone(1): scaffold ReconcileIQ`. Do not claim unrun checks passed.
