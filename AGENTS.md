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

## Milestone 07 scope

Preserve financial history, deterministic money, tenant constraints, authenticated APIs, signed webhooks, generated clients and the existing 200 fixtures. Structured candidates and matching evidence are append-only; successful AI extraction is never financial publication. Only explicit human acceptance of a fresh exact match may publish through the existing accounting validator. Payments remain prohibited.

Google Gemini is disabled unless independent intended-project/key binding, explicitly disabled billing, positive free-tier request quotas and exact model listing are verified. Only repository-labelled synthetic hashes may be disclosed; no paid or automatic fallback. Ordinary CI mocks cloud calls and does not spend credits. Shared budgets and the existing fenced outbox/lease/retry/replay lifecycle govern requests. Failed reprocessing preserves prior evidence. Do not expose service keys, document contents, raw exceptions or signed URLs in diagnostics.

Local production workers parse PDFs in a resource-bounded, clean-environment child; Linux network syscalls are denied. Optional Tesseract/Poppler is verified only in the Linux image. This is not a complete hostile-document filesystem/process sandbox; retain the documented limitations. Browser review uses encrypted HttpOnly local role sessions and exact-origin writes. No unauthenticated server-held reviewer proxy. Business workers still require PostgreSQL/RabbitMQ; do not connect SQLite workers to shared queues.

Use only local ReconcileIQ resources, private queues, disposable databases/schemas and synthetic documents for fault tests. Never reset retained development data. Restore scoped interruptions in cleanup. Distinguish actual service behavior, injected protocol failures and fixture roundtrip metrics. Implement only Milestone 07. Do not begin Milestone 08 automatically.

Milestone 07 adds release engineering only: reuse existing containers and dispatch bootstep, preserve all retained records, keep production identity fail-closed, never publicly deploy, and never publish images before remote/permissions/release settings are verified. PR CI uses only fresh hosted-runner services, synthetic fixtures and mocked AI. Separate migrations from release startup; application rollback never implies database downgrade. Preserve financial/worker coverage gates and report actual local versus hosted results separately.

## Workflow

Use uv with the root workspace lock and pnpm with the root workspace lock. Do not commit `.env`, credentials, runtime databases, build artifacts or dependency directories. Use `python3 scripts/setup_env.py`; it must not overwrite existing secrets. Run `make check`, `make bootstrap-lite` and the frontend production build; run `make bootstrap` when Docker is available. Record actual evidence and limitations in `docs/verification/milestone-07.md`. Use `RECONCILE_TEST_POSTGRES=1 make check` for PostgreSQL regressions and `make reliability-check` for private real-service tests plus critical-module coverage gates. Run fixture drift checks, the live API/MinIO smoke and authorized processing failure/recovery checks against Compose. Controlled fault tests must restore their scoped service interruptions and preserve retained records. Run infrastructure fault scripts and browser worker pauses sequentially. Commit the requested milestone without overwriting user work.
