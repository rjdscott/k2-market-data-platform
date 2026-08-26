# Operations

Running, inspecting and recovering the v2 stack (Redpanda → ClickHouse → Iceberg).
Everything here targets the as-built `docker-compose.yml` at the repo root.

## Guides

| Doc | What it covers |
|-----|----------------|
| [quick-reference.md](./quick-reference.md) | One-page cheat sheet: URLs, ports, credentials, stack commands |
| [data-inspection.md](./data-inspection.md) | Runnable queries for every layer — Redpanda, bronze/silver/gold, Iceberg cold |
| [observability.md](./observability.md) | Grafana dashboards, feed-handler metrics, all 18 Prometheus alert rules |
| [latency-budgets.md](./latency-budgets.md) | 7-segment latency budget plus the measured 2026-02-19 p50/p99 numbers |
| [docker-resources.md](./docker-resources.md) | Per-service CPU/RAM limits — 15.0 CPU / 21.75 GB across 13 services |
| [prefect-schedules.md](./prefect-schedules.md) | The two deployed Prefect schedules (15-min offload, daily maintenance) |
| [clickhouse-database-standard.md](./clickhouse-database-standard.md) | Why everything lives in the `k2` database and how to keep it that way |
| [adding-new-exchanges.md](./adding-new-exchanges.md) | End-to-end checklist for wiring up a 4th exchange |
| [cost-model.md](./cost-model.md) | What this single host would cost as managed cloud services |

## Runbooks

| Runbook | When to use |
|---------|-------------|
| [runbooks/failure-recovery.md](./runbooks/failure-recovery.md) | Any of the 6 tested failure modes (broker, DB, handler, offload, MinIO, network) |
| [runbooks/redpanda.md](./runbooks/redpanda.md) | Topic, partition, consumer-group or schema-registry problems |
| [runbooks/iceberg-offload-failure.md](./runbooks/iceberg-offload-failure.md) | `IcebergOffloadConsecutiveFailures` — offload runs erroring out |
| [runbooks/iceberg-offload-lag.md](./runbooks/iceberg-offload-lag.md) | `IcebergOffloadLag*` — cold tier falling behind the 15-min SLO |
| [runbooks/iceberg-offload-performance.md](./runbooks/iceberg-offload-performance.md) | `IcebergOffloadCycleSlow` / low throughput — cycles taking too long |
| [runbooks/iceberg-offload-watermark-recovery.md](./runbooks/iceberg-offload-watermark-recovery.md) | `IcebergOffloadWatermarkStale` — watermarks stuck, or you need to rewind one |
| [runbooks/iceberg-offload-monitoring.md](./runbooks/iceberg-offload-monitoring.md) | Reference for offload metrics, SLOs and dashboard panels |
| [runbooks/iceberg-scheduler-recovery.md](./runbooks/iceberg-scheduler-recovery.md) | Prefect deployment paused, missing, or the worker stopped picking up runs |

See [runbooks/README.md](./runbooks/README.md) for the full index.
Archived v1 runbooks (Kafka, Spark Streaming, Prefect OHLCV) live in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/).

## Daily checks

Load secrets into your shell first: `set -a && . ./.env && set +a`

```bash
# 1. Every container up and healthy
make ps

# 2. All 3 feed handlers alive (metrics port is container-internal)
for x in binance kraken coinbase; do
  echo -n "$x: "; docker exec k2-feed-handler-$x curl -fsS localhost:8082/health; echo
done

# 3. Trades still arriving in the last 5 minutes
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 5 MINUTE GROUP BY exchange"

# 4. Cold-tier offload keeping up (expect < 15 min)
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c \
  "SELECT table_name, status, last_successful_run FROM offload_watermarks ORDER BY last_successful_run"

# 5. Nothing firing
curl -s localhost:9090/api/v1/alerts | jq '.data.alerts[] | {alertname: .labels.alertname, state}'
```

## Related

- [Architecture](../architecture/) — how the pipeline is designed
- [Decisions](../decisions/) — ADR-001 … ADR-017
- [Development](../development/) — [setup](../development/setup.md), [testing](../development/testing.md)
