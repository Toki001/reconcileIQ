# ReconcileIQ

An auditable accounts payable automation foundation: Next.js/TypeScript, FastAPI/Python, Celery, PostgreSQL, RabbitMQ and MinIO.

**Milestone 05** verifies the existing digital-PDF pipeline with property-based accounting tests, migration rollback checks, real RabbitMQ confirms, isolated Celery timeout/crash recovery and browser processing workflows. Confirmed defects in Decimal bounds, timeout classification and credential redaction are fixed. Captured text stays untrusted; financial fields, matching, OCR and AI extraction are later work. Existing records, financial rules, contracts and UI remain compatible. Payments are prohibited.

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
services/worker/           Celery digital-text processing, outbox recovery and idempotent probe
packages/database/         Pydantic settings, SQLAlchemy models, Alembic, synthetic seed
packages/reconciliation/   Pure deterministic money, tax, discount and tolerance rules
packages/contracts/        Reviewed OpenAPI 3.1, GraphQL SDL and operations
packages/generated/        Generated REST/GraphQL TypeScript and typed clients
packages/ai/               Untrusted extraction schema, offline eval harness and fixtures
tests/                     Persistence, audit, API, concurrency, money and eval checks
scripts/                   Secret-safe setup, bootstrap and contract tools
infra/docker/              Non-root application and pinned-source MinIO images
docs/                      Architecture, local operations and verification evidence
```

Read [AGENTS.md](AGENTS.md) for non-negotiable boundaries and [architecture](docs/architecture/overview.md) for ownership. Historical evidence is recorded for milestones [01](docs/verification/milestone-01.md), [02](docs/verification/milestone-02.md), [03](docs/verification/milestone-03.md) and [04](docs/verification/milestone-04.md). See [financial persistence](docs/architecture/database.md), [migration operations](docs/operations/database.md), the [processing runbook](docs/operations/processing.md) and [milestone 05 verification](docs/verification/milestone-05.md) for current boundaries and measured results. `CODEX.md` is the preserved long-term blueprint; future capabilities are not claims about the current milestone.
