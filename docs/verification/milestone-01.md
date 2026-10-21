# Milestone 01 verification

Verified locally on 2026-10-21 (Asia/Manila), macOS on Apple Silicon. Host tools: Python 3.12.13, uv 0.13.4, Node.js 22.22.3, pnpm 12.10.1, Docker Engine 29.8.0 and Compose 5.5.1. Application containers run as non-root users. The original `CODEX.md` blueprint was retained.

## Commands and observed results

| Command / check                           | Result                                                                                                                                                                                                                                         |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `make bootstrap-lite`                     | Passed: frozen installs, SQLite migration, idempotent synthetic seed, ASGI liveness/readiness/snapshot checks and production frontend build.                                                                                                   |
| `make bootstrap`                          | Passed: all six long-running services healthy; migration/seed service exited successfully; PostgreSQL and RabbitMQ smoke checks passed.                                                                                                        |
| `make check`                              | Passed: Ruff lint/format, strict mypy, pytest/coverage, server/client contract drift, offline eval, ESLint, TypeScript, Jest and Prettier.                                                                                                     |
| pytest                                    | **31 passed**. Covers fresh migrations, metadata drift, seed repeatability, foreign keys, audit triggers/ORM protection, concurrent duplicate delivery, API health/errors, config validation, exact money and evaluation regression detection. |
| Pure money utility coverage               | **100%**, 14 statements. This is coverage of the initial conversion utility, not a full reconciliation engine.                                                                                                                                 |
| Jest                                      | **16 passed** across 3 suites: exact currency rendering including safe-integer boundary, filters/empty state, record details/audit history and generated client success/failure integration.                                                   |
| Offline AI harness                        | 3 synthetic cases: 2 exact fixture matches, 1 expected schema rejection, 0 failures. No model called; no extraction-quality, prompt-injection-resistance, latency or cost result is claimed.                                                   |
| `PLAYWRIGHT_CHANNEL=chrome pnpm test:e2e` | **2 passed** against the Compose-backed dashboard, using installed Chrome. Desktop 1440×1000 and phone viewport 390×844.                                                                                                                       |
| Published HTTP probes                     | API live/ready, web live/ready and MinIO live all returned HTTP 200. API readiness reported database, RabbitMQ and MinIO `ok`.                                                                                                                 |
| Persisted snapshot                        | API returned 6 synthetic invoices and 6 purchase orders, linked to their audit events.                                                                                                                                                         |

The browser check visited Overview, Invoices, Purchase Orders, Reconciliation, Exceptions and Audit Log; verified search, source records, discrepancies, native dialog dismissal/focus return and no browser page errors. The phone check verifies that document width stays within the viewport and record inspection works. Local screenshots are regenerated under ignored `.cache/screenshots/` by the browser tests. Physical phone touch, keyboard, notch and browser-chrome behavior remains unverified.

The PostgreSQL smoke check runs `alembic check`, repeats the seed without adding events, and confirms that UPDATE, DELETE and TRUNCATE of audit history are rejected. Every attempted mutation is transactionally rolled back even if an assertion fails. Unit tests additionally confirm SQLite foreign-key enforcement and append-only audit behavior.

The RabbitMQ smoke check publishes the same business key three times, followed by a separate ordering barrier through the single-concurrency Compose worker. PostgreSQL records one effect and one audit event for the repeated key. This verifies the bootstrap task's duplicate suppression, not a complete crash/recovery or extraction workflow.

## Verified bootstrap adjustments

- Existing applications used ports 3000, 5432 and 9000–9001. ReconcileIQ uses loopback 3100, 15432 and 9100–9101 respectively; existing services were retained. The API uses 8000 and RabbitMQ uses 5672/15672.
- The former upstream MinIO image could not be pulled. The local image is compiled from upstream revision `7aac2a2c5b7c882e68c1ce017d8256be2feea27f`, with archive SHA-256 `71794c2df26aad0cc99e8421c58b7aa2dd55969f979b0e7d1e931042e9fabcd6`. See [operations](../operations/local.md) for the archived dependency limitation.
- Celery control/event queues are explicitly exclusive to satisfy RabbitMQ 4.3 requirements. Business task queues remain durable. No deprecated broker feature was re-enabled.
- Production builds use webpack because Turbopack's local PostCSS IPC binding failed in this execution environment. Docker builds produce standalone Next.js output; host builds support `next start`.
- A phone-viewport regression was fixed by containing positioned accessibility labels in the table scroll area. Hover rules are capability-gated, coarse-pointer inputs use 16px text, and zoom remains enabled.

## Milestone limits

This is a local, unauthenticated, single synthetic workspace. It is not ready to accept real financial records. Statuses and discrepancies are seeded examples; full reconciliation, receipt/line/tax validation, upload/extraction, approvals, production authorization/tenant isolation, GraphQL, recovery/DLX/outbox, live AI evaluation, CI/CD and telemetry remain future milestones from `CODEX.md`. There is no payment execution or provider credential.

Migration `0001_foundation` is the only initial revision. It creates the four foundation tables and audit protections. Its downgrade deliberately refuses to destroy audit history. Frozen uv/pnpm locks, generated OpenAPI/TypeScript schema and reproducible setup commands are included in the milestone.
