# Local alerting

Prometheus evaluates the repository-managed rules every 15 seconds. Thresholds below are initial development assumptions, not production SLOs calibrated by the short local benchmark. No external notification service, cloud account, Alertmanager delivery or automated remediation is configured. View pending/firing states in local Prometheus; notification delivery is unverified.

| Alert                  | Initial condition and hold                                                           | Human action                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| API errors             | 5-minute 5xx ratio >5%, held 5 minutes                                               | Inspect readiness and coarse error/status metrics                                            |
| API latency            | 5-minute p95 >1 second, held 5 minutes                                               | Separate HTTP, SQL, queue and parser timing                                                  |
| Pending age            | Oldest dispatch/queued job >120 seconds for 2 minutes                                | Check dispatcher, broker and worker leases                                                   |
| Backlog                | More than 50 pending/queued jobs for 5 minutes                                       | Inspect admission load; benchmark capacity eight is intentionally lower                      |
| Dead-letter growth     | More than five committed terminal failures in 10 minutes, held 2 minutes             | Inspect authorized failed-job records and broker terminal depth                              |
| Worker unavailable     | No heartbeat increments over 2 minutes, held 2 minutes, while collector scrape is up | Check worker status; exclude monitoring loss first                                           |
| Dependency failures    | More than five failures/dependency in 5 minutes, held 2 minutes                      | Inspect database/RabbitMQ/MinIO independently                                                |
| Processing degradation | Terminal failure ratio >10% over 10 minutes for 5 minutes                            | Inspect failure classifications; avoid counting missing metrics as success                   |
| Provider quota         | Live-free quota event in 10 minutes, held 1 minute                                   | Keep Gemini disabled until independent guards pass; synthetic failures do not page this rule |
| Unexpected cost        | Nonzero recorded live-free estimated cost increase in 1 hour, held 1 minute          | Stop provider activity and verify configuration; no paid fallback                            |
| Storage unavailable    | Database/RabbitMQ/MinIO availability remains zero for 2 minutes                      | Restore dependency before considering operator-controlled replay                             |
| Telemetry unavailable  | Collector scrape down for 2 minutes                                                  | Inspect core application independently; telemetry loss is not job loss                       |

Rules link to the [incident runbook](incident-response.md). The terminal-failure alert counts durable failed-job transitions, not RabbitMQ transport delivery guarantees. Broker terminal ready depth is separately collected. Sparse counters appear after first observation; a new process or short run may not yet have enough samples for `rate`/`increase`. Counter resets are handled by Prometheus; snapshot gauges require `max` across service instances. Scrape/export delays and a two-minute expiration can delay worker-loss detection.

Use `promtool check config`/`check rules` inside the pinned Prometheus image and inspect actual `/api/v1/rules`/dashboard queries after startup. Ten initial rules passed runtime validation before Docker became unavailable; the final twelve-rule revision needs revalidation. Static YAML/dashboard tests do not prove runtime alert firing or notification delivery. Tune values by editing reviewed YAML, never by disabling financial validation, admission limits or security gates. Alerts must never delete jobs, change invoices, approve records or execute AI suggestions.

The cost rule is dormant while no live cost series is emitted. It cannot verify Google billing or prevent spend; provider disabling and the independent Free Tier guards remain the actual controls. No live price or billing measurement is claimed.
