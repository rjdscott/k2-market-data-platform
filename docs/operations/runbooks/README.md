# Runbooks

Incident procedures for the v2 stack. Every Prometheus alert annotation points at one of
these; start from the alert that fired, or from the "when to use" column below.

Load secrets before running any command here: `set -a && . ./.env && set +a`

## Index

| Runbook | When to use | Triggering alert |
|---------|-------------|------------------|
| [failure-recovery.md](./failure-recovery.md) | Any of the six tested infrastructure failures: broker restart, ClickHouse restart, feed-handler crash, offload failure, MinIO down, network partition | `ClickHouseDown`, `FeedHandlerDown`, `FeedHandlerHighErrorRate` |
| [redpanda.md](./redpanda.md) | Topic, partition, consumer-group or schema-registry problems; broker health | `FeedHandlerHighErrorRate` |
| [iceberg-offload-failure.md](./iceberg-offload-failure.md) | Offload runs erroring — ClickHouse unreachable, Spark crash, JDBC or timeout errors | `IcebergOffloadConsecutiveFailures` |
| [iceberg-offload-lag.md](./iceberg-offload-lag.md) | Cold tier behind the 15-minute SLO and you need to catch up | `IcebergOffloadLagCritical`, `IcebergOffloadLagElevated` |
| [iceberg-offload-performance.md](./iceberg-offload-performance.md) | Cycles running long or throughput dropping — volume spikes, resource contention | `IcebergOffloadCycleSlow`, `IcebergOffloadCycleTooSlow`, `IcebergOffloadThroughputLow` |
| [iceberg-offload-watermark-recovery.md](./iceberg-offload-watermark-recovery.md) | Watermark stuck, stale, wedged in `running`, or needs rewinding to re-offload a window | `IcebergOffloadWatermarkStale` |
| [iceberg-offload-monitoring.md](./iceberg-offload-monitoring.md) | Reference: offload metrics, SLO definitions, dashboard panels. Read before tuning thresholds | — |
| [iceberg-scheduler-recovery.md](./iceberg-scheduler-recovery.md) | Prefect deployment paused or missing, worker not claiming runs, empty offload dashboard | `IcebergOffloadSchedulerDown` |

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
| Data loss suspected | Escalate immediately — reconcile hot vs cold using [../data-inspection.md](../data-inspection.md#warm-vs-cold-reconciliation) before taking any destructive action |

## Writing a new runbook

Keep the shape consistent with the ones above: **symptom → detection (name the alert) →
expected behaviour → recovery commands → measured MTTR**. A runbook whose commands have
never been run is a liability; induce the failure and record what actually happened.

Use the `/runbook` skill (`.claude/skills/runbook/`) — it carries this shape as a template, plus the last-verified stamp and the index update.

## v1 runbooks

The 14 archived v1 runbooks — Kafka, Spark Streaming, Prefect OHLCV pipeline, blue-green
deploys, checkpoint corruption — are kept for reference in
[`legacy/v1/docs/runbooks/`](../../../legacy/v1/docs/runbooks/). They describe an
architecture that no longer exists; do not follow them against the v2 stack.
