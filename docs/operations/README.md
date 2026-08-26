# Operations

Running, inspecting and recovering the v2 stack (Redpanda → ClickHouse → Iceberg).
Everything here targets the as-built `docker-compose.yml` at the repo root.

## Guides

| Doc | What it covers |
|-----|----------------|
| [quick-reference.md](./quick-reference.md) | One-page cheat sheet: URLs, ports, credentials, stack commands |
| [data-inspection.md](./data-inspection.md) | Runnable queries for every layer — Redpanda, bronze/silver/gold, Iceberg cold |
| [observability.md](./observability.md) | Grafana dashboards, capture-tier metrics, all 34 Prometheus alert rules |
| [latency-budgets.md](./latency-budgets.md) | 7-segment latency budget plus the measured 2026-02-19 p50/p99 numbers |
| [docker-resources.md](./docker-resources.md) | Per-service CPU/RAM limits — 14.60 CPU / 21.625 GiB across 15 services (+4 one-shot) |
| [prefect-schedules.md](./prefect-schedules.md) | The two deployed Prefect schedules (15-min offload, daily maintenance) |
| [clickhouse-database-standard.md](./clickhouse-database-standard.md) | Why everything lives in the `k2` database and how to keep it that way |
| [adding-new-exchanges.md](./adding-new-exchanges.md) | End-to-end checklist for wiring up a 4th exchange |
| [cost-model.md](./cost-model.md) | What this single host would cost as managed cloud services |

## Runbooks

Incident procedures moved up a level to [`../runbooks/`](../runbooks/README.md) — 13 of
them, indexed there by triggering alert: five `capture-*` for the v3 ingestion tier,
`failure-recovery` for the six tested infrastructure failures, `redpanda` for broker and
topic problems, and six `iceberg-*` for the cold tier. Every Prometheus alert annotation
points at one of them. The archived v2 feed-handler procedure is at
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/feed-handler-crash.md) and
is not in that count.

Archived v1 runbooks (Kafka, Spark Streaming, Prefect OHLCV) live in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/).

## Daily checks

Load secrets into your shell first: `set -a && . ./.env && set +a`

```bash
# 1. Every container up and healthy
make ps

# 2. All 3 capture containers alive (the image is distroless — no curl, so the
#    binary reads its own /metrics; exits non-zero if any stream is stale)
for x in binance kraken coinbase; do
  echo -n "$x: "; docker exec k2-capture-$x /k2-capture healthcheck
done

# 3. Records still reaching Redpanda, per exchange and kind
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange, kind) (rate(k2_capture_records_produced_total[5m]))' \
  | jq -r '.data.result[] | "\(.metric.exchange)\t\(.metric.kind)\t\(.value[1])"'

# 4. Cold-tier offload keeping up (expect < 15 min)
docker exec k2-prefect-db psql -U "$PREFECT_DB_USER" -d "$PREFECT_DB_NAME" -c \
  "SELECT table_name, status, last_successful_run FROM offload_watermarks ORDER BY last_successful_run"

# 5. Nothing firing beyond the four expected IcebergOffload* ones (note below)
curl -s localhost:9090/api/v1/alerts \
  | jq -r '.data.alerts[] | select(.labels.alertname | startswith("IcebergOffload") | not)
           | "\(.labels.alertname)\t\(.state)"'
```

There is no "trades landed in ClickHouse" check any more: the `k2` database is
frozen and gains no rows — see [../architecture/README.md](../architecture/README.md).
The same freeze makes the four `IcebergOffload*` alerts fire permanently and
expectedly, which is why check 5 filters them out; they are deleted with
`docker/offload/` in the Phase D PR. `ClickHouseBronzeInsertRateLow` measured the
same frozen ingest and was archived to
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/clickhouse-v2-ingest-alerts.yml)
in the retirement PR rather than left to fire. Rationale:
[ADR-019](../adr/ADR-019-rust-capture-tier.md) Outcome.

## Related

- [Architecture](../architecture/) — how the pipeline is designed
- [Decisions](../adr/) — ADR-001 … ADR-020, ADR-027
- [Development](../development/) — [setup](../development/setup.md), [testing](../development/testing.md)
