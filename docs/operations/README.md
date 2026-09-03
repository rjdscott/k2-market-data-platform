# Operations

Running, inspecting and recovering the stack: capture → Redpanda → ClickHouse, and
Redpanda → the Iceberg lake on MinIO. Everything here targets the as-built
`docker-compose.yml` at the repo root.

## Guides

| Doc | What it covers |
|-----|----------------|
| [quick-reference.md](./quick-reference.md) | One-page cheat sheet: URLs, ports, credentials, stack commands |
| [data-inspection.md](./data-inspection.md) | Runnable queries for every layer, Redpanda, bronze/silver/gold, the Iceberg lake |
| [observability.md](./observability.md) | Grafana dashboards, capture-tier and lake metrics, all 26 Prometheus alert rules |
| [latency-budgets.md](./latency-budgets.md) | 7-segment latency budget plus the measured 2026-02-19 p50/p99 numbers |
| [docker-resources.md](./docker-resources.md) | Per-service CPU/RAM limits, 14.60 CPU / 25.625 GiB across 15 long-running services, +4 one-shot (1.50 / 1.500 GiB), bootstrap peak 16.10 / 27.125 GiB |
| [prefect-schedules.md](./prefect-schedules.md) | The two deployed Prefect schedules: `lake-ingest-5min` and `lake-maintenance-daily` |
| [clickhouse-database-standard.md](./clickhouse-database-standard.md) | Why everything served lives in the `gold` database (`k2` was dropped at the Phase E cutover) and how to keep it that way |
| [adding-new-exchanges.md](./adding-new-exchanges.md) | End-to-end checklist for wiring up a 4th exchange |
| [cost-model.md](./cost-model.md) | What this single host would cost as managed cloud services |

## Runbooks

Incident procedures moved up a level to [`../runbooks/`](../runbooks/README.md), eleven of
them, indexed there by triggering alert: `failure-recovery` for the six tested
infrastructure failures, `redpanda` for broker and topic problems, five `capture-*` for the
v3 capture tier and four `lake-*` for the lake. Every Prometheus alert annotation points at
one of them. The archived v2 procedures, the feed handler's in
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/feed-handler-crash.md) and the
six offload ones in [`legacy/v2-offload/runbooks/`](../../legacy/v2-offload/runbooks/), are
not in that count.

Archived v1 runbooks (Kafka, Spark Streaming, Prefect OHLCV) live in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/); the six v2 offload runbooks
were archived with the code they described, in
[`legacy/v2-offload/`](../../legacy/v2-offload/README.md).

## Daily checks

Load secrets into your shell first: `set -a && . ./.env && set +a`

```bash
# 1. Every container healthy, every venue producing, lake still committing
make health

# 2. All 3 capture containers alive (the image is distroless: no curl, so the
#    binary reads its own /metrics; exits non-zero if any stream is stale)
for x in binance kraken coinbase; do
  echo -n "$x: "; docker exec k2-capture-$x /k2-capture healthcheck
done

# 3. Records still reaching Redpanda, per exchange and kind
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange, kind) (rate(k2_capture_records_produced_total[5m]))' \
  | jq -r '.data.result[] | "\(.metric.exchange)\t\(.metric.kind)\t\(.value[1])"'

# 4. Lake ingest keeping up (seconds behind the newest Kafka record; expect < 900)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' | \
  jq -r '.data.result[].value[1]'

# 5. Nothing firing
curl -s localhost:9090/api/v1/alerts \
  | jq -r '.data.alerts[] | "\(.labels.alertname)\t\(.state)"'
```

There is no permanently-firing alert to filter out of check 5 any more: the four
`IcebergOffload*` rules went with `docker/offload/` in Phase D
([`legacy/v2-offload/`](../../legacy/v2-offload/README.md)) and
`ClickHouseBronzeInsertRateLow`, which measured the frozen v2 ingest, was archived to
[`legacy/v2-kotlin/`](../../legacy/v2-kotlin/runbooks/clickhouse-v2-ingest-alerts.yml)
rather than left to fire. Check 1 is now the ClickHouse check: `gold` is live and fed
straight from Redpanda, and the v2 `k2` database was dropped at the Phase E cutover on
2026-08-27. Rationale: [ADR-019](../adr/ADR-019-rust-capture-tier.md) Outcome.

## Related

- [Architecture](../architecture/), how the pipeline is designed
- [Decisions](../adr/), ADR-001 … ADR-030
- [Development](../development/), [setup](../development/setup.md), [testing](../development/testing.md)
