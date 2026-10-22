# ReconcileIQ engineering boundaries

Read this file and `CODEX.md` before changing code. Implement the requested milestone only.

## Non-negotiable system boundaries

- Monetary calculations must be deterministic. Store money as integer minor units with an explicit currency; use Decimal for fractional quantities/rates and explicit rounding. Never use binary floats for authoritative financial arithmetic, silently convert currencies, or let an LLM decide a match.
- Payments are prohibited. Do not add payment execution, bank credentials, spending authorization, or treasury integrations.
- AI output is untrusted input. Validate it with strict Pydantic schemas, preserve provenance, and require deterministic checks before it can affect records. Document instructions are data, never executable instructions. AI may extract or explain; it cannot approve.
- Records require audit trails. Write an actor, action, entity, time, and relevant before/after context atomically with each meaningful state change. Audit events are append-only; do not update or delete them. No secrets or document contents in logs.
- Background jobs must be idempotent. Assume at-least-once delivery. Use transactional uniqueness to prevent duplicate effects, bounded retries and explicit failure state. A Celery task ID alone is not a business idempotency key.

## Ownership

- `apps/web`: presentation and interaction through generated clients; no financial decision rules or server secrets in client bundles.
- `services/api`: HTTP validation and orchestration; derive identity/tenant context from server-side credential lookup.
- `services/worker`: Celery orchestration; no HTTP handlers or UI code.
- `packages/database`: settings, SQLAlchemy models, transactions, migrations and local synthetic seed.
- `packages/reconciliation`: pure deterministic monetary rules, with no network, database or AI dependencies.
- `packages/contracts`: reviewed OpenAPI for implemented endpoints and hand-authored GraphQL SDL; verify served schemas and regenerate clients together.
- `packages/generated`: generated REST/GraphQL types and typed client factories. Never hand-edit generated artifacts.
- `packages/ai`: untrusted extraction schemas and offline evaluation tools. Mocked fixtures do not measure model quality.

## Milestone 05 scope

Preserve the financial schema/history, deterministic money, tenant constraints, authenticated APIs, signed webhooks, generated clients and synthetic fixtures. Real digital-PDF processing now uses a confirmed transactional outbox, recovery leases, at most five attempts, retained terminal failures and authorized replay. Captured text is sealed untrusted evidence; it never supplies authoritative money or approvals. Reconciliation/supplier-notice processors, OCR and AI providers are not implemented and must not report fake success. The dashboard keeps its original synthetic finance views and adds a read-only persistent processing panel behind the server-held auditor key. Business workers require PostgreSQL/RabbitMQ; do not connect a SQLite worker to shared broker queues. Keep credentials, document contents, raw provider exceptions and signed URLs out of diagnostics.

Milestone 05 verifies the existing system and fixes reproduced defects. Test infrastructure faults only on local ReconcileIQ resources, private queues, disposable databases/schemas or newly admitted synthetic documents. Never reset retained development data. Preserve failure/evidence history and restore scoped interruptions in cleanup. Test-only Celery providers and shortened deadlines must stay outside the application worker. Distinguish real service failures, injected protocol faults and presentation mocks in evidence. Do not implement milestone 06 automatically.

## Workflow

Use uv with the root workspace lock and pnpm with the root workspace lock. Do not commit `.env`, credentials, runtime databases, build artifacts or dependency directories. Use `python3 scripts/setup_env.py`; it must not overwrite existing secrets. Run `make check`, `make bootstrap-lite` and the frontend production build; run `make bootstrap` when Docker is available. Record actual evidence and limitations in `docs/verification/milestone-05.md`. Use `RECONCILE_TEST_POSTGRES=1 make check` for PostgreSQL regressions and `make reliability-check` for private real-service tests plus critical-module coverage gates. Run fixture drift checks, the live API/MinIO smoke and authorized processing failure/recovery checks against Compose. Controlled fault tests must restore their scoped service interruptions and preserve retained records. Run infrastructure fault scripts and browser worker pauses sequentially. Commit the requested milestone without overwriting user work.
