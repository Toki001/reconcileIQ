# Database migrations and fixtures

Run commands from the repository root. Use `uv run --all-packages` (or `.tools/uv/bin/uv run --all-packages` when uv is workspace-local). Host mode uses the ignored SQLite database by default; Compose uses the retained PostgreSQL volume. Never put a secret connection URL into a committed file, command history or shared log.

## Upgrade and inspection

```sh
make bootstrap                        # Full PostgreSQL/RabbitMQ/MinIO stack; preserves volumes
make bootstrap-lite                   # Host SQLite migration, seeds, ASGI smoke and web build
uv run --all-packages alembic current
uv run --all-packages alembic upgrade head
uv run --all-packages alembic check
# Full-stack migration without starting the web UI:
docker compose run --rm migrate
docker compose exec -T api python -m alembic current
```

Head is `0005_evidence_seals`. The preserved `0001_foundation` and `0002_contracts` revisions are unchanged. `0003_financial` adds the normalized financial model, tenant constraints, exact arithmetic checks, immutable evidence and recovery structures. It backfills organization/vendor/membership relationships, existing PO/receipt line details and outbox tenant keys, recording new audit events while preserving original rows. Existing invoice summaries remain legacy until complete validated extraction evidence is available.

Revision `0005_evidence_seals` adds retained publication seals and guards superseded versions against appended lines/fields. Existing validated versions are conservatively sealed. Its downgrade is allowed only before any version has been sealed; otherwise it refuses to discard history.

Both empty installs and populated milestone 02 upgrades are verified. PostgreSQL applies each migration transactionally. SQLite batch reconstruction uses a dedicated maintenance connection with foreign-key enforcement temporarily disabled, an explicit transaction covering DDL/backfill, and a final `foreign_key_check` before commit; application connections retain FK enforcement. An exception rolls back the migration. Do not run application writes concurrently with SQLite migrations.

Take and verify a restorable backup before applying migrations to valuable data. Keep old binaries available during the compatibility window and prefer a forward fix if an application rollback is needed. Do not reset the schema, remove volumes, edit old migration files or delete audit/effect records to retry work. Revision 0003 deliberately refuses downgrade because dropping these tables would destroy financial evidence.

## Expand-contract example

Revision `0004_vendor_ref` is an executable expand step: add nullable `vendors.external_reference` and tenant-scoped uniqueness. Existing clients and old write paths continue to work with null. The SQLAlchemy field is optional; the public REST/GraphQL interfaces are unchanged.

During a later integration rollout, deploy writers that can populate this field, backfill from a reviewed source with audit events, and monitor completeness through an agreed compatibility window. Only a separate reviewed contract migration could make it required or retire an older identifier. This milestone does not perform a destructive contract step. PO/receipt JSON projections similarly stay available until every reader/writer has migrated and a later retention plan is approved.

The example's downgrade is safe only while every external reference is null:

```sh
# Dedicated empty disposable database only; exercise 0004 before published evidence exists:
uv run --all-packages alembic upgrade 0004_vendor_ref
uv run --all-packages alembic downgrade 0003_financial
uv run --all-packages alembic upgrade head
```

Once any external reference is populated, revision 0004 refuses downgrade. Revision 0005 also refuses downgrade once publication seals exist. A failed multi-revision downgrade rolls back transactionally. Tests verify both the safe rollback/upgrade and refusal with audit history preserved. Older revisions also refuse destructive downgrades. Backups and forward repair remain the recovery path for retained evidence.

## Synthetic financial dataset

```sh
make fixtures                         # Regenerate the 200 checked-in PDF files and manifest
make fixtures-check                   # Byte-for-byte drift check, including manifest
make seed-financial                   # Add isolated fixtures to the configured host database
# The regular full/lite bootstraps seed both the original demo and this dataset.
docker compose exec -T api python -m reconcileiq_database.financial_seed
```

Fixtures occupy only organization `synthetic-finance`; the original six-invoice/six-PO `demo` dashboard remains intact. A fixed standard-library PRNG seed (`20261022`), UUID5 identifiers, fixed timestamps and a SHA-256 manifest make generation deterministic without an external data service. Names and transactions are invented and labelled synthetic.

The dataset contains 20 vendors, 100 POs/200 PO lines, 100 receipts/200 receipt lines, 200 invoices/400 invoice lines, 200 extraction versions/1,200 sourced fields/180 retained publication seals, 20 rejected duplicate attempts, 10 versioned currency tolerance policies and synthetic reconciliation/review evidence. Ten scenario labels each occur 20 times: exact, taxed, discounted, tax-inclusive, invalid total, price discrepancy, quantity discrepancy, partial receipt, tax discrepancy and duplicate attempt. Twenty receipts deliberately contain only a quarter of ordered quantity. There are 180 arithmetically validated invoice versions and 20 rejected header-total cases retained for review. Example statuses include matched, exception, needs review, duplicate, reviewed accepted and reviewed rejected.

All 200 PDFs have selectable digital text, 1–3 pages, source invoice/PO references, explicit currency/mode, line values and totals. The manifest is expected ground truth and includes document digests. Independent strict PDF parsing verifies page counts and text; fixture checks verify byte reproducibility. These are representative starting inputs for future extraction tests; they do not cover scanned documents, OCR degradation, adversarial layouts or live model quality. Do not edit generated PDFs or the manifest by hand.

The entire seed commits atomically. Repeating it verifies the retained manifest checksum and principal aggregate counts, then performs no writes. A changed definition or missing aggregate fails with an instruction to repair/version the dataset; the seed never clears data or silently overwrites changed fixtures. To evolve fixtures, create a new version and migration, rather than resetting shared development history. No jobs are synthesized and no processing success is simulated; completed run examples are explicitly marked synthetic.

## Verification

```sh
make check
RECONCILE_TEST_POSTGRES=1 make check
uv run --all-packages python scripts/postgres_m03_smoke.py
uv run --all-packages python scripts/contracts_smoke.py
PLAYWRIGHT_CHANNEL=chrome pnpm test:e2e
```

Without the flag, PostgreSQL-specific cases are explicitly skipped; SQLite/unit/contract tests still run. With the flag, tests read local Compose credentials internally and create/drop uniquely named schemas in the existing PostgreSQL database. They never reset the application schema or fall back to `public` in the test search path. The migration smoke copies the existing milestone 02 records into an isolated schema when that source revision is available; after upgrade it instead builds labelled legacy records at revision 0002, then checks upgrade, repeat seed, metadata and safe expand rollback. It never downgrades the application database.

The live API/storage smoke appends labelled probe records and verifies real MinIO presigning, upload/sealing, authentication, idempotency, schemas and pending admissions. Keep logs local and redacted; pytest uses short tracebacks to avoid displaying secret configuration objects. See [milestone 03 results](../verification/milestone-03.md) for actual runs and limitations.
