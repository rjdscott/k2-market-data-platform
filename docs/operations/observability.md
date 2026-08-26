# Observability

Prometheus scrapes the stack, Grafana renders it, and 18 alert rules cover the three
things that actually break: ingestion stops, ClickHouse struggles, the cold-tier offload
falls behind.

- Prometheus — http://localhost:9090 (`/targets`, `/alerts`)
- Grafana — http://localhost:3000, `admin` / `$GRAFANA_PASSWORD`

Config lives in [`docker/prometheus/prometheus.yml`](../../docker/prometheus/prometheus.yml),
[`docker/prometheus/rules/`](../../docker/prometheus/rules/) and
[`docker/grafana/dashboards/`](../../docker/grafana/dashboards/) — all provisioned at
container start, no click-ops.

## Scrape targets

| Job | Target | Interval | Source |
|-----|--------|----------|--------|
| `prometheus` | `localhost:9090` | 15s | self |
| `redpanda` | `redpanda:9644` | 10s | Redpanda admin API |
| `clickhouse` | `clickhouse:9363` | 15s | `<prometheus>` block in [`docker/clickhouse/config.xml`](../../docker/clickhouse/config.xml) |
| `grafana` | `grafana:3000` | 15s | Grafana internal metrics |
| `feed-handler-{binance,kraken,coinbase}` | `feed-handler-<x>:8082` | 10s | Micrometer, see below |

Port 8082 is **not published to the host** — reach it through the container:

```bash
docker exec k2-feed-handler-binance curl -s localhost:8082/metrics | grep feed_handler_
curl -s localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health}'
```

## Feed handler metrics

Emitted by [`KafkaProducerService.kt`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/KafkaProducerService.kt)
into a shared Micrometer registry, served by
[`MetricsServer.kt`](../../services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/MetricsServer.kt)
(Ktor, `/metrics` + `/health` on port 8082, overridable with `K2_METRICS_PORT`).

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `feed_handler_trades_produced_total` | counter | `exchange`, `type=raw\|normalized` | Trades produced to Redpanda. `raw` is JSON, `normalized` is Avro |
| `feed_handler_errors_total` | counter | `exchange` | Kafka produce callbacks that returned an exception |
| `feed_handler_reconnects_total` | counter | `exchange` | WebSocket reconnect attempts |

Plus the JVM/process metrics Micrometer registers by default. Recording rule
`feed_handler:trade_rate:5m` pre-computes the raw trade rate per exchange.

**Known gap:** `FeedHandlerDown` is written against
`feed_handler_last_message_timestamp_seconds`, which the handler does not currently
emit — that alert cannot fire. Use `FeedHandlerMetricsDown` (target-based) or the
container healthcheck until the gauge is added.

## Dashboards

| Dashboard | UID | What it shows |
|-----------|-----|---------------|
| K2 Pipeline Overview | `k2-pipeline-overview` | The one to open first. Five rows: stack health, feed handlers (trade rate per exchange, reconnects, produce errors), ClickHouse (insert rate, memory, merge queue), Iceberg offload (lag, cycle duration, rows/sec), Redpanda (throughput, connections) |
| ClickHouse Overview (v2) | `clickhouse-v2` | Query rate, memory gauge, insert rate, background merges — the warm tier in isolation |
| Iceberg Offload Pipeline | `iceberg-offload` | Offload lag, success rate, rows/sec, duration quantiles, error rate, cycle status |
| K2 Platform v2 — Migration Tracker | `k2-v2-migration` | Total CPU/RAM gauges against the 16-core budget, service up/down, Redpanda and ClickHouse rates |

<!-- screenshot: docs/images/grafana-pipeline-overview.png (localhost:3000/d/k2-pipeline-overview, last 6h) -->
<!-- screenshot: docs/images/grafana-clickhouse.png (localhost:3000/d/clickhouse-v2, last 6h) -->
<!-- screenshot: docs/images/grafana-iceberg-offload.png (localhost:3000/d/iceberg-offload, last 24h) -->
<!-- screenshot: docs/images/prometheus-alerts.png (localhost:9090/alerts) -->

## Alert rules

18 rules across three files. Every annotation carries the diagnostic commands and a
runbook link; the tables below are the index.

### `feed-handler-alerts.yml` — ingestion (4)

| Alert | Severity | Fires when |
|-------|----------|-----------|
| `FeedHandlerDown` | critical | No trade produced for >120s (see known gap above) |
| `FeedHandlerHighErrorRate` | critical | `rate(feed_handler_errors_total[5m]) > 0.1` for 3m |
| `FeedHandlerFrequentReconnects` | warning | More than 3 reconnects in 15m, sustained 5m |
| `FeedHandlerMetricsDown` | warning | Prometheus cannot scrape a handler's `/metrics` for 2m |

### `clickhouse-alerts.yml` — warm tier (5)

| Alert | Severity | Fires when |
|-------|----------|-----------|
| `ClickHouseDown` | critical | `up{job="clickhouse"} == 0` for 2m |
| `ClickHouseHighMemoryUsage` | critical | Resident memory >85% of system RAM for 5m |
| `ClickHouseQueryFailureRateHigh` | critical | `rate(FailedQuery[5m]) > 0.1` for 3m |
| `ClickHouseBronzeInsertRateLow` | warning | <0.5 rows/sec inserted over 10m — expect off-peak false positives |
| `ClickHouseMergeQueueLarge` | warning | >10 background merge tasks queued for 5m |

Recording rules: `clickhouse:insert_rate:5m`, `clickhouse:query_duration_p99:5m`.

### `iceberg-offload-alerts.yml` — cold tier (9)

| Alert | Severity | Fires when |
|-------|----------|-----------|
| `IcebergOffloadConsecutiveFailures` | critical | ≥3 errors for one table within 15m |
| `IcebergOffloadLagCritical` | critical | `offload_lag_minutes > 30` for 5m (SLO breach) |
| `IcebergOffloadCycleTooSlow` | critical | Cycle >600s — risks overlapping the 15-min schedule |
| `IcebergOffloadWatermarkStale` | critical | Watermark unchanged for >1h — pipeline hung silently |
| `IcebergOffloadSchedulerDown` | critical | Scheduler scrape target unreachable for 2m |
| `IcebergOffloadSuccessRateLow` | warning | Success rate <95% over 15m |
| `IcebergOffloadLagElevated` | warning | Lag between 20 and 30 min for 10m |
| `IcebergOffloadThroughputLow` | warning | <10K rows/sec sustained 15m |
| `IcebergOffloadCycleSlow` | warning | Cycle between 300s and 600s for 10m |

Recording rules: `iceberg_offload:cycle_count:5m`, `iceberg_offload:duration_avg:5m`,
`iceberg_offload:rows_rate:5m`.

**Known gap:** all nine depend on `offload_*` metrics from
[`docker/offload/metrics.py`](../../docker/offload/metrics.py), which only starts its HTTP
server when the flow runs standalone (`python3 iceberg_offload_flow.py`). Under the
Prefect worker no server starts, and the `iceberg-scheduler` scrape job is commented out
in `prometheus.yml`. So these rules and the Iceberg dashboard render empty against the
default deployment. Until the exporter is wired into the worker, monitor the cold tier
through Prefect run history and the watermark table — see
[prefect-schedules.md](./prefect-schedules.md).

## SLOs

| Signal | Target | SLO | Measured |
|--------|--------|-----|----------|
| Exchange → silver latency (p99) | <200 ms | <500 ms | 170–197 ms — see [latency-budgets.md](./latency-budgets.md) |
| Offload lag | <15 min | <30 min | 9 min (2026-02-15) |
| Offload success rate | >99% | >95% | no failures observed to date |
| Offload cycle duration | <30 s | <10 min | 12–76 s depending on backlog |
| Warm/cold consistency | 100% | >99% | 99.9%+ (2026-02-15, 2026-02-18) |
| Failure-mode MTTR | <2 min | <5 min | ≤32 s across all 6 tested modes — see [runbooks/failure-recovery.md](./runbooks/failure-recovery.md) |

## Not wired up

Honest gaps, in priority order:

1. **No Alertmanager.** `alerting.alertmanagers.targets` is empty — alerts are visible in
   the Prometheus UI and Grafana but nothing routes to a pager or Slack.
2. **Offload metrics not scraped** (above) — the largest blind spot.
3. **`FeedHandlerDown` references a metric that does not exist** (above).
4. **No exporters for MinIO, PostgreSQL or Spark.** Their health is only observable via
   `docker compose ps` and container logs.

## Related

- [quick-reference.md](./quick-reference.md) — health-check one-liners
- [runbooks/failure-recovery.md](./runbooks/failure-recovery.md) — what to do when one of these fires
- [latency-budgets.md](./latency-budgets.md) — the latency targets behind the SLO table
