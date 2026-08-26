# Observability

Prometheus scrapes the stack, Grafana renders it, and 17 alert rules cover the three
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
| `iceberg-scheduler` | `iceberg-metrics:8000` | 15s | `docker/offload/metrics.py --serve`, see below |

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

## Dashboards

| Dashboard | UID | What it shows |
|-----------|-----|---------------|
| K2 Pipeline Overview | `k2-pipeline-overview` | The one to open first. Five rows: stack health, feed handlers (trade rate per exchange, reconnects, produce errors), ClickHouse (insert rate, memory, merge queue), Iceberg offload (lag, cycle duration, rows/sec), Redpanda (throughput, connections) |
| ClickHouse Overview (v2) | `clickhouse-v2` | Query rate, memory gauge, insert rate, background merges — the warm tier in isolation |
| Iceberg Offload Pipeline | `iceberg-offload` | Offload lag, success rate, rows/sec, duration quantiles, error rate, cycle status |
| K2 Platform v2 — Migration Tracker | `k2-v2-migration` | Total CPU/RAM gauges against the 16-core budget, service up/down, Redpanda and ClickHouse rates |
| K2 Capture (v3) | `k2-l2-capture` | Rust capture tier (Phase C): health (up, staleness, reconnects, gaps, checksum failures, resyncs), throughput (messages/bytes/records/produce errors), exchange→recv latency p50/p95/p99, book depth/levels/precision loss. `exchange` template variable filters all panels |

![Redpanda topics](../images/redpanda-console-topics.jpg)

## Alert rules

17 rules across three files. Every annotation carries the diagnostic commands and a
runbook link; the tables below are the index.

### `feed-handler-alerts.yml` — ingestion (3)

| Alert | Severity | Fires when |
|-------|----------|-----------|
| `FeedHandlerDown` | critical | Metrics endpoint / scrape target down for 2m |
| `FeedHandlerHighErrorRate` | critical | `rate(feed_handler_errors_total[5m]) > 0.1` for 3m |
| `FeedHandlerFrequentReconnects` | warning | More than 3 reconnects in 15m, sustained 5m |

### `clickhouse-alerts.yml` — warm tier (5)

| Alert | Severity | Fires when |
|-------|----------|-----------|
| `ClickHouseDown` | critical | `up{job="clickhouse"} == 0` for 2m |
| `ClickHouseHighMemoryUsage` | critical | Resident memory >85% of system RAM for 5m |
| `ClickHouseQueryFailureRateHigh` | critical | `rate(FailedQuery[5m]) > 0.1` for 3m |
| `ClickHouseBronzeInsertRateLow` | warning | Server-wide inserted rows < 0.5/s over 5m |
| `ClickHouseMergeQueueLarge` | warning | >10 background merge tasks queued for 5m |

Recording rules: `clickhouse:insert_rate:5m`, `clickhouse:query_duration_mean:5m`.

### `iceberg-offload-alerts.yml` — cold tier (9)

| Alert | Severity | Fires when |
|-------|----------|-----------|
| `IcebergOffloadConsecutiveFailures` | critical | ≥3 errors for one table within 15m |
| `IcebergOffloadLagCritical` | critical | `offload_lag_minutes > 30` for 5m (SLO breach) |
| `IcebergOffloadCycleTooSlow` | critical | A table's last run took >600s — risks overlapping the 15-min schedule |
| `IcebergOffloadWatermarkStale` | critical | Watermark unchanged for >26h — hung-pipeline backstop |
| `IcebergOffloadSchedulerDown` | critical | `iceberg-metrics` scrape target unreachable for 2m |
| `IcebergOffloadSuccessRateLow` | warning | Success rate <95% over 15m |
| `IcebergOffloadLagElevated` | warning | Lag between 20 and 30 min for 10m |
| `IcebergOffloadThroughputLow` | warning | bronze/silver <0.1 rows/sec averaged over 1h |
| `IcebergOffloadCycleSlow` | warning | A table's last run took 300–600s for 10m |

The two lag alerts scope to `bronze_*`, `silver_trades`, `ohlcv_1m` and `ohlcv_5m`. A gold
table's watermark tracks the last *completed* window, so `ohlcv_15m`/`30m`/`1h`/`1d` lag by
design and can never satisfy a 30-minute SLO; `IcebergOffloadWatermarkStale` is their
backstop. `IcebergOffloadThroughputLow` uses a 1-hour window because offloads run on a
15-minute batch cadence — a 5-minute rate is zero most of the time for every table.

Recording rules: `iceberg_offload:cycle_count:5m`, `iceberg_offload:duration_avg:5m`,
`iceberg_offload:rows_rate:5m`.

### How the offload metrics are produced

The `offload_*` metrics come from the **`iceberg-metrics`** service, which runs
[`docker/offload/metrics.py`](../../docker/offload/metrics.py) `--serve` and is scraped on
`iceberg-metrics:8000` as Prometheus job `iceberg-scheduler`.

Every metric is derived from the PostgreSQL `offload_watermarks` table, re-read every 15s
— not from counters inside the flow. Prefect runs each flow in a short-lived subprocess
that exits long before Prometheus scrapes it, so in-process counters were never
observable. The watermark row is the durable record of what the pipeline did.

It is a separate service rather than a sidecar in `prefect-worker` on purpose: if the
worker crashes, the exporter keeps reporting the rising offload lag that these alerts
exist to catch.

| Metric | Type | Source column |
|--------|------|---------------|
| `offload_lag_minutes{table}` | gauge | `now() - last_offload_timestamp` |
| `watermark_timestamp_seconds{table}` | gauge | `last_offload_timestamp` |
| `offload_last_duration_seconds{table}` | gauge | `last_run_duration_seconds` |
| `offload_last_rows_per_second{table}` | gauge | `last_offload_row_count / last_run_duration_seconds` |
| `offload_tables_configured` | gauge | row count |
| `offload_rows_total{table,layer}` | counter | `last_offload_row_count`, added once per finished run |
| `offload_cycles_total{status}` | counter | one increment per finished run |
| `offload_errors_total{table,error_type}` | counter | one increment per failed run |
| `offload_duration_seconds{table,layer}` | histogram | observed once per finished run |

Runs are counted exactly once by tracking `updated_at` per table; rows still in `running`
are skipped until they resolve. Counters restart at zero if the exporter restarts, which
Prometheus handles as a normal counter reset.

Sanity-check the exporter's counting logic offline with
`python docker/offload/metrics.py --self-check`.

## SLOs

| Signal | Target | SLO | Measured |
|--------|--------|-----|----------|
| Exchange → silver latency (p99) | <200 ms | <500 ms | 170–197 ms — see [latency-budgets.md](./latency-budgets.md) |
| Offload lag | <15 min | <30 min | 9 min (2026-02-15) |
| Offload success rate | >99% | >95% | no failures observed to date |
| Offload cycle duration | <30 s | <10 min | 5–7 s per table (2026-08-26) |
| Warm/cold consistency | 100% | >99% | 99.9%+ (2026-02-15, 2026-02-18) |
| Failure-mode MTTR | <2 min | <5 min | ≤32 s across all 6 tested modes — see [../runbooks/failure-recovery.md](../runbooks/failure-recovery.md) |

## Not wired up

Honest gaps, in priority order:

1. **No Alertmanager.** `alerting.alertmanagers.targets` is empty — alerts are visible in
   the Prometheus UI and Grafana but nothing routes to a pager or Slack.
2. **No exporters for MinIO, PostgreSQL or Spark.** Their health is only observable via
   `docker compose ps` and container logs.
3. **No query-latency percentiles for ClickHouse.** Its Prometheus endpoint exposes
   counters only — no native histograms — so `clickhouse:query_duration_mean:5m` is a mean,
   not a p99.

## Related

- [quick-reference.md](./quick-reference.md) — health-check one-liners
- [../runbooks/failure-recovery.md](../runbooks/failure-recovery.md) — what to do when one of these fires
- [latency-budgets.md](./latency-budgets.md) — the latency targets behind the SLO table
