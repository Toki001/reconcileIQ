# Container deployment and private release rehearsal

ReconcileIQ is packaged for deployment testing, **not approved for public production use**. Production authentication/authorization and hostile-document isolation are unresolved. `Settings` accepts only `local` and `test`; the separate release Compose defaults to `production`, which is rejected. Do not bypass this boundary on a public host or add a fake login. No public deployment or registry publication was performed in this milestone.

## Local development

```sh
python3 scripts/setup_env.py              # Creates/extends 0600 files, preserves secrets
make install                            # Frozen uv/pnpm workspace locks
make bootstrap-lite                     # SQLite + ASGI smoke + Next.js production build
make bootstrap                          # Existing retained PostgreSQL volumes, no reset
RECONCILE_TEST_POSTGRES=1 make check
make reliability-check
pnpm test:e2e                           # Install Chromium: pnpm exec playwright install chromium
```

Docker Desktop/Linux Docker, Compose v2+, Python 3.12, uv 0.13.4, Node 22 and pnpm 12.10.1 are required. Allow roughly 6–8 GiB RAM plus build cache/storage for the complete development/test stack; actual capacity depends on document load. Everything runs locally without paid AI or hosting. GitHub-hosted runners and private GHCR storage/transfer may consume account allowances or incur charges; review account quotas before enabling remote workflows. Nothing changes billing automatically.

## Images

```sh
make release-images
# Individual equivalent commands:
docker build --target api -f infra/docker/python.Dockerfile -t reconcileiq-api:release-local .
docker build --target worker -f infra/docker/python.Dockerfile -t reconcileiq-worker:release-local .
docker build -f infra/docker/web.Dockerfile -t reconcileiq-web:release-local .
docker compose build minio
make release-config
```

The API and worker share the locked Python runtime, but have separate targets, default commands, health probes and GHCR image identities. The final/default Python target remains API, preserving development Compose. Recovery/dispatch is the worker's supervised `RecoveryService` bootstep; there is no fourth first-party dispatcher image. Run at least one worker for outbox recovery. Multiple workers arbitrate through PostgreSQL locks/fenced leases.

Bases and uv are pinned by manifest digest; uv/pnpm application dependencies are frozen at root locks. Final images have no compiler installation or uv build tool, run non-root, and receive configuration only at runtime. Next.js uses standalone output. Python source is root-owned/readable by UID 10001, preventing accidental source modification. Both Python roles include Poppler and Tesseract English because API previews and worker OCR require them. OCR remains opt-in, bounded and subject to [sandbox limitations](../security/production-readiness.md). `.dockerignore` excludes environment files, credentials, caches, Git, dependencies and local artifacts.

APT packages come from maintained Debian repositories at build time, not a dated snapshot: application locks/base pins do **not** guarantee byte-identical rebuilds. Retain the tested image digest, vulnerability report and release metadata; deployment uses that digest. Review base/OS updates explicitly and rebuild/retest. The custom archived MinIO source remains checksum-pinned and AGPL licensed; it has no upstream community security-maintenance guarantee. Review replacement/support before public production.

## Separate private rehearsal

Use `compose.release.yml` **alone**, never as an overlay on `docker-compose.yml`. Its project `reconcileiq-release` and named volumes are separate from the retained development project. PostgreSQL, RabbitMQ management, MinIO data/admin and API have no published ports. Only the web service binds `127.0.0.1:13100`; infrastructure is on an internal network, web also has an edge network. No internet egress is available to API/workers; AI is hard-disabled with no key forwarded. Resource limits, read-only application filesystems, temporary writable `/tmp`, dropped capabilities, PID bounds, init and stop grace periods are explicit.

With no production identity, a rehearsal must deliberately set local mode and use synthetic data. Generate independent credentials in a **disposable directory**, rather than copying retained development credentials:

```sh
mkdir -p .cache/release-rehearsal/apps/web
(cd .cache/release-rehearsal && python3 ../../scripts/setup_env.py)
# Set RECONCILE_RELEASE_ENVIRONMENT=local only for this private rehearsal.
export RECONCILE_RELEASE_ENVIRONMENT=local
# Every following command uses the dedicated file and explicit private env file:
docker compose --env-file .cache/release-rehearsal/.env -f compose.release.yml up -d --wait postgres rabbitmq minio
docker compose --env-file .cache/release-rehearsal/.env -f compose.release.yml --profile maintenance run --rm migrate
# Optional synthetic demo/role credentials, ONLY on this new rehearsal database:
docker compose --env-file .cache/release-rehearsal/.env -f compose.release.yml run --rm --no-deps api python -m reconcileiq_database.seed
docker compose --env-file .cache/release-rehearsal/.env -f compose.release.yml up -d --wait api worker web
curl --fail http://127.0.0.1:13100/health/ready
# Stop containers while preserving all persistent volumes:
docker compose --env-file .cache/release-rehearsal/.env -f compose.release.yml down
unset RECONCILE_RELEASE_ENVIRONMENT
```

The migration profile runs Alembic only, with no synthetic seed, drop or downgrade. Normal app startup never migrates: preflight rejects wrong schema or unsupported identity before a worker can consume business tasks. A failed healthcheck alone does not restart a container; operators must investigate unhealthy processes/dependencies. Restart policies recover process exits, and PostgreSQL outbox recovery repairs interrupted dispatch.

The private browser review uses the authenticated PDF upload/download proxy; direct presigned uploads from outside the network are unsupported in this example. No MinIO public URL or public proxy is silently created. Bucket initialization is idempotent on first authenticated upload; named MinIO data persists. Root object-store credentials and PostgreSQL/RabbitMQ admin-level credentials are acceptable only for local rehearsal; production least-privilege identities, rotation, TLS and secret-manager integration are blockers. Environment variables remain inspectable by Docker administrators; Compose is not a secret vault.

## Deployment order

1. Approve identity/isolation/privacy readiness, compatible schema range, tested immutable images and vulnerability remediation. Until then, stay private.
2. Quiesce writes/admissions; drain or gracefully stop workers. Verify pending/failed jobs and outbox, capture and restore-test PostgreSQL and document backups.
3. Apply the reviewed additive migration as a separate maintenance operation. First rehearse it on both a fresh database and a restored populated copy. Never run destructive downgrade automatically.
4. Start compatible API, then worker (which also dispatches/recoveries), then web. Readiness must pass; check queue ready/unacknowledged counts, outbox, failures and exact-once business effects.
5. Resume admissions and monitor. Perform backfill separately with audits. Defer destructive contract changes until every supported reader/writer is retired and retention is approved.

This milestone introduces **no new database revision**. Head stays `0007_invoice_workflows`. Older M06 and M07 application images support that same head; this is the only verified rollback compatibility window. See [rollback](rollback.md) and [CI/CD](ci-cd.md).
