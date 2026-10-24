# Performance measurements and reproduction

See [Milestone 08 verification](../verification/milestone-08.md) for completion status. These are bounded local synthetic experiments, not a capacity or production-readiness claim. Financial validation and idempotency remain intact. A measured concurrent-admission deadlock was corrected by using a compatible PostgreSQL row lock; see the verification report.

## Environment

Host: Apple M3 Pro, 11 logical CPUs, 18 GiB RAM, macOS 26.6.2/arm64. Images run Linux/arm64 on Docker Desktop; The original runs did not record VM allocation. After recovery, Docker reports 11 CPUs and 8,319,369,216 bytes of memory (7.75 GiB). Both application and monitoring stacks were running. Gemini was disabled, no live provider or paid processing was used.

Isolated project `reconcileiq-m08` uses independent PostgreSQL/RabbitMQ/MinIO volumes and credentials. Resource limits: API 1.5 CPU/1536 MiB, worker 2 CPU/2048 MiB, PostgreSQL 1 CPU/1024 MiB, RabbitMQ 1 CPU/768 MiB, MinIO 1 CPU/768 MiB, web 0.75 CPU/512 MiB. Worker concurrency four, prefetch one, admission limit eight. The retained development stack is never a benchmark target.

The tested document was `tests/fixtures/financial/invoice-0003.pdf`: **2,220 bytes, three digital text pages**. Real authenticated API, MinIO persistence/checksum, RabbitMQ/Celery and restricted PDF subprocesses ran. No OCR or AI was needed for this workload. End-to-end below means reservation through observed terminal digital extraction, including client polling; it excludes structured AI, matching and human review.

## Actual measured results

API baseline: two virtual users for 20 seconds, 326 iterations/978 requests, **48.72 requests/s**, 978 passing checks, zero failed checks and zero unexpected HTTP responses. Anonymous 401 responses are expected and checked separately.

| Observation (ms)                        | Samples |    Min |    p50 |     p95 |     p99 |     Max |
| --------------------------------------- | ------: | -----: | -----: | ------: | ------: | ------: |
| All API requests                        |     978 |  0.583 |  6.553 |  14.990 |  18.518 | 105.280 |
| Authenticated identity                  |     326 |  3.317 |  6.553 |  11.456 |  14.786 |  44.105 |
| Anonymous denial                        |     326 |  0.583 |  1.253 |   2.789 |   3.704 |   4.535 |
| GraphQL pagination                      |     326 |  6.555 | 11.557 |  17.255 |  21.644 | 105.280 |
| Sustained completion acceptance         |       6 | 14.942 | 17.119 |  20.914 |  21.669 |  21.858 |
| Sustained digital extraction end-to-end |       6 |  1,062 |  2,600 | 3,004.5 | 3,101.7 |   3,126 |

The authenticated versus anonymous routes differ in work, so their latency difference is not an isolated causal measurement of authentication overhead. Pagination used a bounded first-ten query.

Sustained arrival: two submissions per minute for three minutes, **six accepted/six succeeded/zero failed**, no dropped iterations or failed checks; 51 total HTTP requests including job polls. Submission rate is 2/minute. Across the isolated database after the run, all 12 jobs (including preceding workflow probes) succeeded, unpublished outbox zero, duplicate execution keys zero, duplicate supplier keys zero, orphan lines zero, provider usage zero. This is a completed-scenario consistency check, separate from the later interruption scenarios below.

The digital result satisfies the proposed 25-second target only for this specific digital extraction path and six-sample workload. It does not demonstrate full AI/OCR invoice processing performance. p95/p99 on six observations are interpolated order statistics with very limited confidence. No live extraction accuracy, model latency, pricing or cost result exists. Cloud spend was zero because no cloud provider was invoked.

Docker stats sampled approximately every four seconds (CLI sampling plus two-second wait), not continuous profiling:

| Container  | Baseline max CPU / memory MiB (5 samples) | Sustained max CPU / memory MiB (47 samples) |
| ---------- | ----------------------------------------- | ------------------------------------------- |
| API        | 22.93% / 125.0                            | 16.23% / 125.0                              |
| Worker     | 52.28% / 395.2                            | 81.10% / 424.4                              |
| PostgreSQL | 3.01% / 44.41                             | 5.39% / 47.89                               |
| RabbitMQ   | 52.69% / 168.8                            | 55.12% / 182.8                              |

These maxima can miss short peaks. Persisted job/attempt timestamps identify all six original sustained jobs: queue wait min/p50/p95/p99/max **0.191/1.558/1.998/2.031/2.039 seconds**; worker attempt processing **0.565/0.594/0.666/0.681/0.684 seconds**. These exclude upload and client polling. Per-query SQL and parser CPU/RSS histograms export, but no isolated OCR CPU/RSS percentile study was performed. CPU percentages are Docker's process accounting, not whole-host load. MinIO/monitoring resource samples were not collected in these runs.

## Commands and remaining scenarios

```sh
make benchmark-init
make observability-up
make benchmark-baseline
make benchmark-sustained
# Sequential only; do not run alongside browser worker pauses or other faults:
make benchmark-burst
make benchmark-faults
uv run --all-packages python scripts/benchmark_env.py run python scripts/performance_benchmark.py ocr
uv run --all-packages python scripts/benchmark_env.py run python scripts/observability_smoke.py exercise
uv run --all-packages python scripts/benchmark_env.py run python scripts/observability_smoke.py metrics
# Fresh labelled injected failure, followed by real broker delivery/retry:
uv run --all-packages python scripts/benchmark_env.py run python scripts/observability_smoke.py retry
uv run --all-packages python scripts/benchmark_env.py run python scripts/observability_smoke.py review-outage
uv run --all-packages python scripts/benchmark_env.py run python scripts/invoice_workflow_smoke.py
make observability-check
```

k6 is pinned by version/digest in the driver. It receives private credentials through an ignored 0600 file that is removed in cleanup, with stable request names and no URL/identity system tags. Raw summaries/resource samples are in `.cache/m08/results`. Re-running a named scenario replaces that private scenario summary; preserve reviewed results before reruns. Fixed local ports, independent credentials and Docker project-label checks reject the retained target before traffic. Never run `down --volumes`, reseed retained data, accelerate retained leases or share business queues.

The completed 12-VU burst accepted eight and returned four capacity responses, completed all eight, and passed 36/36 checks in 8.58 seconds wall time. An authenticated read during backpressure took 6.03 ms. The initial failing runs exposed a real organization foreign-key/admission lock-upgrade deadlock; using `FOR NO KEY UPDATE` preserves serialization and eliminates the conflicting lock upgrade. The concurrent PostgreSQL regression verifies complete rollback at capacity.

Actual SIGKILL during four active parses recovered all four on attempt two in 127.24 seconds, without accelerating the natural 120-second lease. RabbitMQ recovery after restoration took 0.835 seconds; MinIO recovered in 6.654 seconds on attempt two. Malformed input failed once with `INVALID_PDF`; missing trace context generated a valid server trace. Separate Collector/Prometheus/Grafana/Tempo pauses allowed jobs to succeed in 1.69/1.90/1.93/1.70 seconds. The post-fault database had 53 successes/one accurately failed job, no unpublished outbox, duplicate effects, duplicate supplier keys or orphan lines. Every scoped pause was restored.

One actual Linux Poppler/Tesseract OCR probe processed a **10,072-byte, one-page synthetic scan in 1.509 seconds** including submission/polling. One sample is functional evidence, not an OCR percentile or quality benchmark. The injected offline provider invoked once despite duplicate delivery and reported fixture-supplied 123/456 tokens, no cloud calls and no financial publication; its zero cost is an explicit mock assumption. Its original digital upload supplies real queue ancestry; direct injected delivery does not pretend to publish AMQP. A separate explicit human-review workflow published one financial invoice while the Collector was paused, in 12.71 seconds total, with no provider calls.

Five dashboards/28 PromQL queries and 12 alert rules have actual runtime checks. A 15-second Tempo trace-read timeout occurred under 0.75-CPU/512-MiB limits (observed CPU saturation and 483.5 MiB use); Tempo alone was raised to bounded **1.5 CPU/1024 MiB**, reusing its volume. Original baseline measurements retain their original resource profile. See the verification ledger for the final rerun and regression status.

The load driver does not measure full-model throughput. Extend duration/sample size only after bounded tests pass, record Docker VM resources, and inspect actual queue/parser/SQL distributions before choosing optimizations. Retained-data row-hash comparison and unchanged authorization/accounting regressions remain required acceptance checks.
