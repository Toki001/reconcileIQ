# Database backup, image rollback and worker recovery

An application rollback **does not reverse database migrations**. Never delete immutable evidence, review decisions, audit events, execution keys or outbox rows to make an old image start. Do not run destructive Alembic downgrades or remove retained volumes.

## Before a release

Record the exact API/worker/web/MinIO image digests, Git revision, Alembic revision, environment definition and supported schema range. Quiesce admissions/writes, drain or gracefully stop workers, and verify no unknown inflight transaction remains. A PostgreSQL snapshot alone does not snapshot MinIO: retain matching document objects/versioned backups and record queue/outbox/DLQ state. Keep backups encrypted/access-controlled and off the deployment disk under an approved retention policy. Do not print credentials or presigned URLs.

Local PostgreSQL backup (credentials are expanded inside its container, not logged):

```sh
umask 077
mkdir -p .cache/backups
docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > .cache/backups/pre-release.dump
# Non-destructive automated restore/metadata/equality verification on a new database:
make release-db-check
# Optional already-built local baseline image check against the restored copy:
uv run --all-packages python scripts/verify_release_database.py --baseline-image reconcileiq-m06:compatibility
```

The automated verifier saves a unique 0600 `.cache/m07-pre-release-*.dump`, restores the exact saved bytes into a uniquely created `riq_m05_test_*` database on local PostgreSQL:15432, compares every row/table, repeats `upgrade head`, runs `alembic check`, and verifies source rows unchanged. It drops only the database it created, in cleanup. It must run while writes are quiescent; concurrent changes cause a real equality failure. It never restores over the retained database. Review its verification output; file existence or `pg_restore --list` alone does not prove recoverability.

For a manual disaster-recovery rehearsal, create a **new empty destination** with an operator-chosen unique name, pass it as an argument to `pg_restore --exit-on-error`, run Alembic/row/tenant/audit consistency checks, then start compatible images against that copy. Keep database URLs in private runtime environment files. Restore object backups into a separate private bucket/server and verify retained document SHA-256 values before starting workers. Full cross-store disaster recovery and off-host restore are not certified by the local PostgreSQL-only automated probe.

## Safe image rollback

1. Stop new writes/admissions and gracefully stop the current worker, allowing its bounded task to finish. Preserve broker/database/object volumes.
2. Identify a tested previous image and confirm its schema range includes the **current** Alembic head. For M06→M07 both are `0007_invoice_workflows`; M07 adds no migration. Do not assume M05 images accept this head.
3. Replace API/worker/web with their previous immutable digests; leave the newer compatible database schema in place. Run readiness, authentication/authorization, document access and synthetic idempotency checks before resuming writes.
4. If incompatible, keep writes stopped and deploy a forward-compatible fix. Restoring a database backup discards later state unless carefully reconciled; it requires explicit recovery approval and a documented RPO, including audits and documents. It is not a routine image rollback.

Preflight checks require the exact current image's Alembic head before starting release API/workers. A future compatible expand migration must deliberately update and test a supported revision range in preflight and API readiness, rather than disabling the check. Remove old columns/constraints only in a separately reviewed contract migration after the compatibility window.

## Worker replacement

```sh
# Local development project only; graceful SIGTERM/warm shutdown, 90-second budget:
docker compose stop --timeout 90 worker
# After image replacement; dependencies and retained volumes remain running:
docker compose up -d --no-deps --no-build --wait worker
docker compose exec -T worker python scripts/container_health.py worker
docker compose exec -T worker python -m reconcileiq_worker.status
docker compose exec -T rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers
```

For the release example, add its explicit `--env-file … -f compose.release.yml` to each Compose command. Its worker runs dispatch/recovery; no second scheduler should race against a different database or broker. Keep concurrency within 1–16, prefetch one, 40/60-second soft/hard task limits and lease ≥90 seconds. Shutdown grace is 90 seconds, covering the 60-second task bound plus dispatcher join. A stuck process may still be killed after grace; fencing/idempotency and expired-lease recovery handle that path.

Persistent AMQP messages, durable queues, publisher confirms, late acknowledgments and `reject_on_worker_lost` retain deliveries across restart. PostgreSQL job/outbox state is authoritative: uncertain publication may deliver twice; transactional effect uniqueness prevents duplicate financial results. Recovery restores missing outboxes and redispatches stale queued jobs/expired leases. Bounded attempt numbers, backoff/Retry-After and failed-job evidence persist through image replacement. The terminal queue remains durable and is never purged or consumed to retry business work. A single local broker is not a highly available cluster; host/disk loss and classic-queue durability are separate risks.

Use the authenticated admin replay endpoint with current version, reason and business idempotency key after repairing a retryable terminal failure. Never bypass attempt limits, edit prior evidence, or bulk-requeue DLQ messages. Check for zero unexplained active jobs/unpublished envelopes, no duplicate supplier keys or financial lines, correct attempt histories, and accurate terminal states. Run `scripts/processing_smoke.py` and browser worker-pause tests **sequentially**. Existing private-prefork tests exercise actual soft timeout, hard timeout, child crash and restart; injected provider failures are identified separately from actual infrastructure faults.
