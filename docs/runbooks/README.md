# Runbooks

Incident procedures for the running v2 stack, plus the v3 capture tier being built
alongside it. Runbooks record *how*; ADRs record *why* (`../adr/`). Every Prometheus
alert annotation points at one of these; start from the alert that fired, or from the
"when to use" column below.

Load secrets before running any command here: `set -a && . ./.env && set +a`

## Conventions

- One task per file, `<slug>.md`. Updating an existing runbook beats a near-duplicate.
- Shape: **symptom → detection (name the alert) → expected behaviour → recovery commands
  → measured MTTR**. [`template.md`](./template.md) is that shape, empty.
- Exact copy-pasteable commands, never paraphrases. Verify them before writing them down;
  a runbook nobody ran is fiction.
- MTTR is measured by inducing the failure, not estimated.
- No decision rationale here — link the ADR.
- A PR that invalidates a runbook's steps updates it and its index row in the same PR.
- Every alert in `docker/prometheus/rules/` names a runbook in its annotations, and that
  path must resolve. The v2 rule files carry it as a `**Runbook:**` line inside the
  `description`; `capture-alerts.yml` promotes it to a first-class `runbook:`
  annotation so the path is machine-checkable. New rule files use the annotation form.
- Write it with the `/runbook` skill.

## Index

### v2 stack

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [failure-recovery.md](./failure-recovery.md) | Any of the six tested infrastructure failures: broker restart, ClickHouse restart, capture crash, offload failure, MinIO down, network partition | `ClickHouseDown`, `CaptureDown`, `CaptureProduceErrors` |
| [redpanda.md](./redpanda.md) | Topic, partition, consumer-group or schema-registry problems; broker health | `CaptureProduceErrors` |
| [iceberg-offload-failure.md](./iceberg-offload-failure.md) | Offload runs erroring — ClickHouse unreachable, Spark crash, JDBC or timeout errors | `IcebergOffloadConsecutiveFailures` |
| [iceberg-offload-lag.md](./iceberg-offload-lag.md) | Cold tier behind the 15-minute SLO and you need to catch up | `IcebergOffloadLagCritical`, `IcebergOffloadLagElevated` |
| [iceberg-offload-performance.md](./iceberg-offload-performance.md) | Cycles running long or throughput dropping — volume spikes, resource contention | `IcebergOffloadCycleSlow`, `IcebergOffloadCycleTooSlow`, `IcebergOffloadThroughputLow` |
| [iceberg-offload-watermark-recovery.md](./iceberg-offload-watermark-recovery.md) | Watermark stuck, stale, wedged in `running`, or needs rewinding to re-offload a window | `IcebergOffloadWatermarkStale` |
| [iceberg-offload-monitoring.md](./iceberg-offload-monitoring.md) | Reference: offload metrics, SLO definitions, dashboard panels. Read before tuning thresholds | — |
| [iceberg-scheduler-recovery.md](./iceberg-scheduler-recovery.md) | Prefect deployment paused or missing, worker not claiming runs, empty offload dashboard | `IcebergOffloadSchedulerDown` |

### v3 capture tier (Phase C — written ahead of the code; see the note below)

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [capture-down.md](./capture-down.md) | A `k2-capture` container is down or crash-looping, or is running but failing to produce to Redpanda | `CaptureDown`, `CaptureProduceErrors` |
| [capture-produce-stalled.md](./capture-produce-stalled.md) | Capture is receiving from the exchange but producing nothing to Redpanda, before the queue fills and starts dropping | `CaptureProduceStalled` |
| [capture-feed-stale.md](./capture-feed-stale.md) | Container up and scrapeable but a stream has gone silent; or exchange→receive p99 has stepped up | `CaptureFeedStale`, `CaptureIngressLatencyHigh` |
| [capture-sequence-gaps.md](./capture-sequence-gaps.md) | Exchange sequence continuity broke (messages lost), or the book keeps being resynced | `CaptureSequenceGaps`, `CaptureResyncStorm` |
| [capture-checksum-failure.md](./capture-checksum-failure.md) | Kraken CRC32 mismatch, book thinner than top-20, or a venue quoting finer than 8 dp | `CaptureChecksumFailure`, `CaptureBookDepthDegraded`, `CapturePrecisionLoss` |

> **These four carry no measured MTTR yet, and say so on every row.** The capture tier
> ([ADR-019](../adr/ADR-019-rust-capture-tier.md)) is built and running; what has not
> happened is a fault injection. Commands run against it are marked ✅. The Phase C
> chaos run
> (`make chaos`) induces each failure, waits for the alert, and fills in the
> **Measured** rows and the **Last verified** stamp. Until then they are procedures,
> not measurements — which is exactly the distinction this directory's conventions
> exist to protect.

## Triage

```bash
# What is actually firing?
curl -s localhost:9090/api/v1/alerts | jq '.data.alerts[] | {alertname: .labels.alertname, state, since: .activeAt}'

# Is anything down?
make ps

# Is data still moving?
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count(), max(timestamp) FROM k2.silver_trades
   WHERE timestamp > now() - INTERVAL 5 MINUTE GROUP BY exchange"

# Is the cold tier keeping up?
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c \
  "SELECT table_name, status, NOW() - last_successful_run AS lag FROM offload_watermarks ORDER BY lag DESC"
```

Then:

- **Hot tier affected** (no trades arriving, ClickHouse down) → [failure-recovery.md](./failure-recovery.md)
- **Cold tier only** (hot tier healthy, offload behind or failing) → the `iceberg-*` runbooks
- **Broker-level** (topics, partitions, schema registry) → [redpanda.md](./redpanda.md)

Hot-tier problems are user-visible and take priority. Cold-tier problems are recoverable
by design — the watermark makes every offload idempotent, so lag is annoying rather than
dangerous.

## Escalation

| Condition | Action |
|-----------|--------|
| Recovery not achieved within the runbook's MTTR + 30 min | Escalate to the platform owner |
| Root cause is a code bug | Open an issue, link the runbook section that failed |
| More than 3 occurrences in 24 h | Stop restarting; find the cause |
| Data loss suspected | Escalate immediately — reconcile hot vs cold using [../operations/data-inspection.md](../operations/data-inspection.md#warm-vs-cold-reconciliation) before taking any destructive action |

## Archived v2 Kotlin runbooks

The feed-handler crash procedure and the three `FeedHandler*` alert rules moved to
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/) when the Kotlin capture
tier retired ([ADR-019](../adr/ADR-019-rust-capture-tier.md)). The measured MTTR is kept
as measured; the alerts and the `feed_handler_*` metric family no longer exist, so do not
follow it against this stack.

## v1 runbooks

The 14 archived v1 runbooks — Kafka, Spark Streaming, Prefect OHLCV pipeline, blue-green
deploys, checkpoint corruption — are kept for reference in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/). They describe an
architecture that no longer exists; do not follow them against the v2 stack.
