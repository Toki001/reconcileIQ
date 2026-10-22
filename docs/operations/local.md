# Local operations

Run `make env`, then `make bootstrap`. The bootstrap may be repeated safely. It does not overwrite `.env`, erase volumes or reset audit history. Alembic runs before API/worker readiness. The original demo seed inserts missing known IDs; the isolated financial seed checks its manifest and retained aggregate counts before returning without changes. Neither is an import route for real data. See [database operations](database.md) for the migration sequence, safe rollback example and fixture commands.

## Health and troubleshooting

`/health/live` verifies the process. API `/health/ready` verifies the current migration revision (`0005_evidence_seals`) and, in Compose, authenticated RabbitMQ connectivity plus MinIO HTTP health. It returns 503 with dependency states when unavailable. Web readiness follows API readiness. Database or network driver exceptions are never returned to callers. The worker has a broker ping health check; `scripts/worker_smoke.py` additionally exercises message delivery, persistence and duplicate suppression.

Use `docker compose ps` first, followed by logs for the affected service. Avoid sharing raw Compose configuration or environment output, which can expose credentials. Do not rotate the generated passwords by deleting `.env` while keeping initialized volumes: PostgreSQL and RabbitMQ retain their original credentials. Use their administrative credential-rotation procedures if rotation is needed.

If ports 3100, 15432, 5672, 8000, 9100, 9101 or 15672 are occupied, stop the conflicting local process or adjust loopback port mappings. Internal service addresses stay unchanged. Host commands use SQLite by default and are separate from Compose's PostgreSQL data.

## MinIO source build

The former `minio/minio:RELEASE.2025-09-07T16-13-09Z` image returned pull-access-denied during bootstrap. The [official upstream repository](https://github.com/minio/minio) is archived and recommends building from source. We build its final revision `7aac2a2c5b7c882e68c1ce017d8256be2feea27f`, verify the archive checksum in the Dockerfile, compile with the Go toolchain, and retain its upstream license. The first build downloads source/dependencies; later builds use Docker's cache. No unverified third-party mirror or replacement object store is used.

This is a reproducible local development dependency. An archived upstream has no ongoing maintenance guarantee. Before any production milestone, revisit the supported object-store choice and patch policy. The user-requested MinIO remains isolated in Compose.

## Migrations and audit retention

Run `uv run --all-packages alembic upgrade head` for host mode, or `docker compose run --rm migrate` for Compose. `alembic check` verifies metadata against the migrated schema. The initial downgrade refuses to drop audit history. Back up PostgreSQL and verify a restore before any future destructive migration; prefer forward fixes and expand/contract changes. Do not manually edit/delete audit events or job execution keys to replay work.

For shutdown, `make down` retains all named volumes. The milestone creates no real invoices, no external provider calls and no payment side effects.

## API contract verification

`make env` extends existing files with independent API keys, a cursor secret, supplier HMAC configuration and MinIO client settings. It writes only the auditor key into the web server's ignored `.env.local`. Files remain mode 0600 and existing values are preserved. Never print raw environment files or signed upload URLs. See [API contracts](../../packages/contracts/README.md) for role permissions and credential rotation semantics.

After `make bootstrap`, run `uv run --all-packages python scripts/contracts_smoke.py` on the host to verify real presigned upload completion and the served schemas. It appends labelled local records, preserves audit history and leaves admitted jobs pending for the later processing milestone. API retries with the same key retain the original committed response. Business outbox delivery is not part of the existing Celery probe.

Docker bootstrap builds Python before the frontend to bound registry load. Dependency layers are separated from source changes, with BuildKit caches and bounded download concurrency/timeouts; repeated source edits reuse locked third-party dependencies. This follows [uv's workspace Docker caching guidance](https://docs.astral.sh/uv/guides/integration/docker/).
