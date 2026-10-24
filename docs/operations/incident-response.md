# Local incident response

These procedures cover local ReconcileIQ infrastructure. Use the isolated `reconcileiq-m08` project for deliberate interruption tests. Never reset retained data or volumes. Preserve append-only audit/evidence and bounded worker leases/retries. Do not print credentials, signed URLs, PDF text, model responses, connection URLs or raw exceptions in an incident report.

## API errors

Check `/health/live` and `/health/ready` separately. Review status/route metrics, authenticated job state and sanitized error codes. A 202 is durable admission, not completion. Confirm tenant and role restrictions before any write. Avoid logging request bodies or authorization/session headers. Do not grant wider permissions to improve availability.

## API latency

Compare HTTP histograms with SQL operation durations and processing-stage/queue histograms. Client polling includes observation delay. Treat small-sample p99 cautiously. Record CPU/RSS, container limits, document pages and load before proposing an index or scheduling change. Keep accounting and idempotency checks intact.

## Queue recovery

Inspect pending outbox count, oldest age, broker ready depth and durable job/attempt states through authorized interfaces. The existing dispatcher restores pending envelopes and reclaims expired leases; do not manually update leases or publish into shared retained queues. Broker publication can be uncertain; duplicate delivery is expected and must retain one business effect. Restore the dependency and allow bounded backoff/visibility intervals before escalating.

## Worker unavailable

Check Docker availability before invoking Compose. Inspect only the intended project's worker status and broker connection. The private performance driver verifies project labels before service control. A hard kill may require the natural 120-second lease to expire; graceful termination flushes supported telemetry, hard termination cannot. Do not restart Docker Desktop or unrelated projects as a scoped fault test. Restore only interrupted local services and record measured recovery/attempt counts.

## Dependency outage

Separate database, broker and MinIO failure codes. Pending admission/outbox and existing evidence remain authoritative when storage or broker operations fail. Restore the scoped dependency, then verify terminal outcomes, bounded retries, effect uniqueness, audit and orphan checks. Never delete/recreate volumes, reseed retained records, weaken checksum verification or connect SQLite workers to RabbitMQ. If an unexpected data change or destructive action is needed, stop and obtain explicit approval.

## Terminal failures

Inspect sanitized job/attempt/failed-job records with authorization; preserve existing extraction versions and audit history. Malformed PDFs can fail permanently and should not loop. An authorized operator may replay only through the existing reasoned, audited, idempotent API after the underlying problem is understood. Failed reprocessing must preserve prior evidence. A Celery task ID is not a business effect key.

## Provider unavailable

Gemini is disabled for this milestone. Synthetic provider counters describe injected offline work, never live quality. Do not activate a cloud key, change billing, broaden document disclosure or use paid/automatic fallback to clear an alert. Independent intended-project/key binding, explicitly disabled billing, positive Free Tier quotas and exact model listing remain required for any separately authorized future live work.

## Unexpected cost

Keep provider invocation disabled. Compare recorded known tokens/proof mode with the documented cost assumption. Zero synthetic cost means no cloud invocation, not verified live pricing. Never print the key or prompt/response contents. Report an unknown cost as unknown.

## Telemetry outage

Core processing must continue when Collector, Prometheus, Grafana or Tempo is unavailable. Check actual job states and application readiness independently. SDK/collector queues are bounded and may drop telemetry; lack of spans is not proof of missing business records. Restore the affected monitoring service, verify collection resumes, and distinguish the telemetry gap from transaction failure. Do not add synchronous exporting to a financial transaction.

## Retained data verification

The private baseline `.cache/m08-start-rows.json` captures 7,283 application rows across 32 tables. Once retained PostgreSQL is reachable, run the read-only guard, with output kept private:

```sh
uv run --all-packages python scripts/m06_data_guard.py --path .cache/m08-start-rows.json
uv run --all-packages python scripts/m06_data_guard.py
```

Compare original row hashes as well as counts. Do not regenerate the baseline to hide drift. The final comparison is pending while Docker is unavailable. Keep final milestone status incomplete until required recovery, integrity, coverage and export checks pass.
