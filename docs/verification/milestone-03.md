# Milestone 03 verification

Verified on 2026-10-22 (Asia/Manila), macOS Apple Silicon, continuing commit `fe25330`. Host: Python 3.12.13, uv 0.13.4, Node.js 22.22.3, pnpm 12.10.1. The original `CODEX.md`, migration revisions 0001/0002, API contracts, generated clients and dashboard implementation are preserved.

## Observed results

| Check                      | Result                                                                                                                                                                                                                                                                                                                                                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Complete Python regression | **188 passed**, including the original 80 tests and 32 financial integration cases run on both SQLite and isolated PostgreSQL schemas. Covers exact arithmetic, supported currency exponents, line/header validation, tenant and parent constraints, duplicate keys, immutable evidence, atomic rollback, pending jobs/attempt bounds, seed repeatability, concurrent reviews and safe migration rollback/refusal. |
| Pure accounting coverage   | **100%**, 98 statements across money and accounting modules. This measures arithmetic/tolerance utilities, not a full reconciliation engine.                                                                                                                                                                                                                                                                       |
| Docker bootstrap           | `make bootstrap` passed frozen installs, image builds, nondestructive retained-volume migration/seed, all six healthy long-running services, PostgreSQL schema/audit checks and RabbitMQ duplicate-delivery probe (three deliveries, one effect). No unrelated application's containers or volumes were changed.                                                                                                   |
| Lite bootstrap             | `make bootstrap-lite` passed the retained SQLite database upgrade, repeat seed, authenticated ASGI health/snapshot checks and production Next.js build. Original demo still returns six invoices.                                                                                                                                                                                                                  |
| PostgreSQL migration       | Head `0005_evidence_seals`; `alembic check` reports no new operations. Before application upgrade, an isolated copy of the actual milestone 02 schema/data upgraded successfully: 7 invoices, 7 POs, 34 audit events, 4 execution keys, 3 credentials, 3 idempotency records, 2 jobs, 2 outbox events, 6 runs, 3 discrepancies and 1 webhook receipt retained.                                                     |
| Additional migration smoke | `scripts/postgres_m03_smoke.py` passed again after upgrade using populated revision-0002 legacy fixtures. Every original column value is compared after upgrade; repeat seed, metadata agreement and safe unused-reference downgrade/upgrade pass. Tests separately verify refusal once an external reference is used.                                                                                             |
| Live contracts and MinIO   | `scripts/contracts_smoke.py` passed served OpenAPI/GraphQL compatibility, role restrictions, PO idempotent replay, real presigned PUT, verified sealed upload, durable 202/job read and signed webhook replay against the rebuilt stack.                                                                                                                                                                           |
| Browser regression         | **2 passed** with installed Chrome against Compose port 3100. All six finance pages, records, filters, detail dialog/focus return, no page errors and 390×844 viewport containment. The audit assertion now permits multiple retained events for one invoice.                                                                                                                                                      |
| PDF fixtures               | All **200 PDFs** independently parsed with strict pypdf checks for page counts, text and hashes; byte reproducibility checked against the generated manifest.                                                                                                                                                                                                                                                      |

The full `RECONCILE_TEST_POSTGRES=1 make check` gate also checks Ruff lint/format, strict mypy, served schemas, both generated TypeScript clients, fixture drift, offline evaluation, ESLint, TypeScript, Jest and Prettier. The final gate passed in full: **188 Python tests**, **20 Jest tests** across four suites, both contract/client drift gates, fixture drift, lint/type/format checks and the three-case offline harness (two exact outputs, one expected rejection, zero failures). No live model was called.

Retained version seals also reject changes after a newer extraction version becomes selected. Revision `0005_evidence_seals` backfills existing validated versions, creates future seals atomically on publication and refuses downgrade once history exists. Safe empty-seal rollback and populated-history refusal are both tested. The linked reviewer user is retained on new HTTP review decisions; deactivated users/memberships return 401.

## Retained fixture counts

Queried the migrated Compose PostgreSQL database after bootstrap and live storage verification:

| Entity in `synthetic-finance`      |       Count |
| ---------------------------------- | ----------: |
| Vendors                            |          20 |
| Purchase orders / lines            |   100 / 200 |
| Goods receipts / lines             |   100 / 200 |
| Invoices / lines                   |   200 / 400 |
| Extraction versions / fields       | 200 / 1,200 |
| Reconciliation runs / line results |   200 / 400 |
| Discrepancies / review decisions   |    120 / 40 |
| Rejected duplicate attempts        |          20 |
| Versioned tolerance policies       |          10 |
| Audit events                       |       1,415 |
| Business jobs in fixture tenant    |           0 |

There are 180 retained publication seals, 180 arithmetically validated invoice versions and 20 rejected-total versions whose invoices remain draft. Invoice statuses: 60 matched, 40 exception, 40 needs review, 20 duplicate, 20 reviewed accepted and 20 reviewed rejected. The original six synthetic demo invoices remain intact in `demo`. The eight admitted jobs in the application database all remain `pending_dispatch`; none claims business-processing success. These counts describe this verification run, before future probe requests add records.

## Corrections made during verification

A PostgreSQL test intentionally refusing a downgrade exposed an undisposed migration engine. Migration cleanup now disposes the engine in `finally`, including refused/failed migrations. Targeted tests and the full PostgreSQL suite subsequently passed. A Docker Desktop interruption initially prevented PostgreSQL connections; it recovered without restarting or changing unrelated projects. Bootstrap then completed normally.

A diagnostic failure traceback exposed a local database credential in tool output. Saved diagnostic logs were redacted, and pytest now uses short tracebacks so configuration arguments are not dumped. Credentials remain ignored local configuration; do not share raw diagnostic output. The final scan found no generated local credential values in repository candidates or public Next.js assets. Both ignored environment files remain mode 0600. Contracts, generated clients, UI implementation, original migration revisions and `CODEX.md` have no diff from the base commit.

## Scope and readiness

The [database architecture](../architecture/database.md) documents exact arithmetic, normalized evidence, tenant foreign keys and immutable records. The [operations guide](../operations/database.md) documents fresh/populated upgrades, downgrade refusal, the executable optional-reference expand step, the deferred contract step and deterministic fixture commands.

No business queue publisher, OCR, AI provider, extraction worker or full matching engine was introduced. Completed run/review examples are labelled synthetic. Milestone 04 can build dispatch claiming, lease recovery, bounded retries, durable terminal failures and atomic idempotent effects on these structures; it must implement and test that behavior before jobs can leave pending admission. The current schema is preparation, not proof of processing reliability.

Remaining limits: local API-key identity and no end-user dashboard login; no PostgreSQL RLS; an explicit five-currency subset and no FX, credit notes or jurisdictional tax engine; digital synthetic PDFs without scanned/adversarial OCR coverage; no live model evaluation, production deployment or recovery telemetry. SQLite approximates the production topology only for local tests. Chrome phone-viewport checks are not physical-device testing. Production object-store maintenance, retention and backup/restore rehearsals remain future operational work. Financial/audit migrations are intentionally forward-only when downgrade would lose evidence. Payments remain prohibited. Milestone 04 has not been started.
