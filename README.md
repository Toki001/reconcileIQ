# ReconcileIQ

An auditable accounts payable automation foundation: Next.js/TypeScript, FastAPI/Python, Celery, PostgreSQL, RabbitMQ and MinIO.

**Milestone 06** adds structured invoice evidence, immutable human corrections, deterministic two/three-way matching and a working reviewer dashboard. Offline parsing, matching and review are verified against real local services. Gemini remains disabled pending project-specific Free Tier verification; live model quality is unmeasured. Existing records, financial rules, contracts and recovery workflows are preserved. Payments are prohibited.

See the [complete API contract guide](packages/contracts/README.md) for roles, money, pagination, webhook signatures, errors and retry semantics.

## Prerequisites

- Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node.js 22.22+ and pnpm 12.10.1.
- Docker Desktop or Docker Engine with Compose v2+ for the full stack. Allow about 6 GB of memory and several GB of image/build storage. The first MinIO source build takes longer than an image pull.
- macOS, Linux or WSL; run commands from the repository root.

If uv is missing, install it globally using the upstream instructions, or keep it in this workspace:

```sh
python3.12 -m venv .tools/uv
.tools/uv/bin/pip install uv==0.13.4
```

The Makefile automatically uses `.tools/uv/bin/uv` when `uv` is absent from PATH. Install pnpm with `npm install -g pnpm@12.10.1` if needed.

## Full local bootstrap

```sh
make bootstrap
```

This generates `.env` with random local credentials and mode `0600`, installs both frozen workspaces, builds non-root application images, starts the infrastructure, migrates PostgreSQL, preserves the six-invoice/six-PO demo and adds the separate financial fixture organization with audit events, waits for readiness, then delivers an idempotent probe through RabbitMQ. The four-process worker also runs the recoverable business publisher; accepted digital PDF uploads now process automatically. Repeating bootstrap preserves data and secrets.

| Service                  | Local URL                                                              |
| ------------------------ | ---------------------------------------------------------------------- |
| Finance dashboard        | http://localhost:3100                                                  |
| API documentation        | http://localhost:8000/docs                                             |
| API liveness / readiness | http://localhost:8000/health/live · http://localhost:8000/health/ready |
| Web liveness / readiness | http://localhost:3100/health/live · http://localhost:3100/health/ready |
| RabbitMQ management      | http://localhost:15672                                                 |
| MinIO S3 API / console   | http://localhost:9100 · http://localhost:9101                          |
| PostgreSQL               | localhost:15432                                                        |

Service credentials are in your ignored `.env`; they are never printed by setup. Compose binds published ports to loopback. Keep the stack local: API keys and tenant scoping are implemented, but the dashboard has no end-user login and this is not a production deployment. Inspect it with `docker compose ps` and stop it with `make down`; named volumes are retained. Do not use `down -v` when you want to preserve records and audit history.

MinIO is built from checksum-verified upstream source because its former public image could not be pulled. The upstream community repository is archived. This is a local dependency, not an endorsement for a production rollout; see [operations](docs/operations/local.md) for source and maintenance details.

## Bootstrap without Docker

```sh
make bootstrap-lite
make api
# In a second terminal:
make dev
```

Open http://localhost:3100. Lite mode migrates and seeds `.data/reconcileiq.db`, checks the ASGI health and snapshot handlers, and builds the frontend. RabbitMQ and MinIO are explicitly `not_required` in lite readiness; background delivery and object storage are not exercised. The generated host `.env` uses lite mode by default; Compose overrides database, broker and readiness settings internally. Stop the Compose web/API before running host processes on the same ports.

For host development against the full infrastructure, override `RECONCILE_DATABASE_URL` with your local PostgreSQL URL and set `RECONCILE_INFRASTRUCTURE_REQUIRED=true`. The generated `.env` already holds the host broker URL. Do not put secret URLs in shell history; use an ignored local environment file or your shell's secure environment management.

## Checks and development

```sh
make check                 # Ruff lint/format, mypy, pytest, contracts, offline eval, ESLint, tsc, Jest, Prettier
pnpm build                 # Production frontend build
make format                # Apply Python and frontend formatting
make contracts             # Export REST contract and regenerate REST/GraphQL clients
make contracts-check       # Detect served schema and both generated client drifts
make fixtures-check        # Verify 200 deterministic PDFs and their ground-truth manifest
make seed-financial        # Idempotent isolated financial seed in the configured database
docker compose exec -T worker python -m reconcileiq_worker.status
RECONCILE_TEST_POSTGRES=1 make check  # Include isolated real-PostgreSQL integration cases
make reliability-check     # Private RabbitMQ/MinIO/Celery probes; 100% accounting, >=90% critical statements
uv run --all-packages python scripts/postgres_m03_smoke.py
# Optional real-browser checks against the running stack:
pnpm exec playwright install chromium
pnpm test:e2e
# Against the running full stack; appends local probe records:
uv run --all-packages python scripts/contracts_smoke.py
# Controlled local faults; run sequentially, never alongside browser worker pauses:
uv run --all-packages python scripts/processing_smoke.py
uv run --all-packages python scripts/reliability_smoke.py
```

Python equivalents use `uv run --all-packages <command>`. Do not omit `--all-packages` when running cross-workspace commands. Regenerate and commit both contracts and client schema after an API change. The eval command `uv run --all-packages reconcileiq-eval` compares independent labels to supplied mock outputs; it spends no AI credits and makes no model-quality claim.

## Repository map

```text
apps/web/                  Next.js App Router, TypeScript, Tailwind, Jest
services/api/              FastAPI REST mutations, Strawberry GraphQL reads, auth
services/worker/           Celery text/OCR/structured extraction, matching and outbox recovery
packages/database/         Pydantic settings, SQLAlchemy models, Alembic, synthetic seed
packages/reconciliation/   Pure deterministic accounting, exact matching and tolerance rules
packages/contracts/        Reviewed OpenAPI 3.1, GraphQL SDL and operations
packages/generated/        Generated REST/GraphQL TypeScript and typed clients
packages/ai/               Strict candidate evidence, free-only provider, parser restrictions and evaluation
tests/                     Persistence, audit, API, concurrency, money and eval checks
scripts/                   Secret-safe setup, bootstrap and contract tools
infra/docker/              Non-root application and pinned-source MinIO images
docs/                      Architecture, local operations and verification evidence
```

Read [AGENTS.md](AGENTS.md) for non-negotiable boundaries and [architecture](docs/architecture/overview.md) for ownership. Historical evidence is recorded for milestones [01](docs/verification/milestone-01.md), [02](docs/verification/milestone-02.md), [03](docs/verification/milestone-03.md) and [04](docs/verification/milestone-04.md). See [financial persistence](docs/architecture/database.md), [migration operations](docs/operations/database.md), the [processing runbook](docs/operations/processing.md) and [milestone 05 verification](docs/verification/milestone-05.md) for earlier evidence; [milestone 06 verification](docs/verification/milestone-06.md) describes current boundaries and results. `CODEX.md` is the preserved long-term blueprint; future capabilities are not claims about the current milestone.

## Milestone 06: evidence, matching and human review

```sh
python3 scripts/setup_env.py      # extends private 0600 env files without replacing secrets
make bootstrap                   # additive migration 0007, real local services
# Open http://127.0.0.1:3100/review; use your private reviewer/admin key to sign in.
make check
RECONCILE_TEST_POSTGRES=1 make check
make reliability-check           # run sequentially with other coverage/fault suites
PLAYWRIGHT_CHANNEL=chrome pnpm test:e2e
# Synthetic offline demo using real API/MinIO/RabbitMQ/Celery, no AI requests:
uv run --all-packages python scripts/invoice_workflow_smoke.py
# Linux OCR and parser restrictions, no backend/cloud requests:
docker compose exec -T worker python scripts/parser_smoke.py
```

Invoice Review supports original PDF access, source evidence, immutable human corrections, PO/receipt comparisons, two/three-way deterministic matching, accept/reject and audit history. Money stays deterministic; AI cannot approve or pay. The existing finance dashboard and processing/recovery interfaces remain available.

Gemini uses the official Python SDK and `gemini-3.5-flash-lite`, **disabled until independent project/key/billing/quota/model verification passes**. No project details were available for live testing. Manual corrections and local matching work offline. No paid fallback exists. Only labelled synthetic document hashes can use the Free Tier. Tesseract/Poppler are verified in the Linux image and OCR is opt-in; macOS host runtime is absent.

See [extraction and correction workflow](docs/ai/extraction.md), [honest evaluation results](docs/ai/evaluation.md), [free-provider operations](docs/operations/ai-provider.md), and [milestone 06 verification](docs/verification/milestone-06.md). The 100 labelled PDF cases and 1,000 price-mismatch regressions validate offline behavior; live extraction accuracy, AI latency and cost remain unmeasured. Stop before Milestone 07 until it is explicitly requested.
