# Docker Resource Allocation

Every service in [`docker-compose.yml`](../../docker-compose.yml) declares both a hard
`limit` and a guaranteed `reservation`. The design target was a single 16-core / 40 GB
host; the as-built stack fits with room to spare.

**As built: 15.35 CPU / 22.125 GB across every long-running resource-limited service (15)**
(plus four one-shot containers that exit after startup: `redpanda-init`, `iceberg-init`,
`lakekeeper-migrate`, `lake-init`).

## Allocation

| Service | Tier | CPU limit | CPU reserve | RAM limit | RAM reserve |
|---------|------|----------:|------------:|----------:|------------:|
| `redpanda` | streaming | 2.0 | 1.0 | 2 GB | 1 GB |
| `redpanda-console` | streaming | 0.5 | 0.1 | 256 MB | 128 MB |
| `clickhouse` | warm storage | 4.0 | 2.0 | 8 GB | 4 GB |
| `minio` | cold storage | 1.0 | 0.5 | 1 GB | 512 MB |
| `spark-iceberg` | cold storage | 2.0 | 1.0 | 4 GB | 2 GB |
| `lakekeeper` | cold storage | 0.25 | 0.1 | 256 MB | 128 MB |
| `prefect-db` | orchestration | 1.0 | 0.5 | 1 GB | 512 MB |
| `prefect-server` | orchestration | 1.0 | 0.5 | 1 GB | 512 MB |
| `prefect-worker` | orchestration | 0.5 | 0.25 | 512 MB | 256 MB |
| `prometheus` | observability | 1.0 | 0.5 | 2 GB | 1 GB |
| `grafana` | observability | 0.5 | 0.25 | 512 MB | 256 MB |
| `feed-handler-binance` | ingestion | 0.5 | 0.25 | 512 MB | 256 MB |
| `feed-handler-kraken` | ingestion | 0.5 | 0.25 | 512 MB | 256 MB |
| `feed-handler-coinbase` | ingestion | 0.5 | 0.25 | 512 MB | 256 MB |
| `iceberg-metrics` | observability | 0.1 | — | 128 MB | — |
| **Total (15 services)** | | **15.35** | **7.45** | **22.125 GB** | **11.0 GB** |
| `redpanda-init` | init (one-shot) | — | — | — | — |
| `iceberg-init` | init (one-shot) | — | — | — | — |
| `lakekeeper-migrate` | init (one-shot) | — | — | — | — |
| `lake-init` | init (one-shot) | — | — | — | — |

Headroom against the 16 CPU / 40 GB envelope: **0.65 CPU (4%) and 17.875 GB (45%)**.

## Where the budget goes

- **ClickHouse takes 27% of CPU and 37% of RAM.** It absorbs the work v1 spent on five
  always-on Spark Streaming jobs — Kafka Engine ingest, Bronze→Silver→Gold materialized
  views and every analytical query run against the hot tier.
- **Spark is batch-only.** Its 2.0 CPU / 4 GB is idle except during the 15-minute offload
  cycle, so the practical steady-state footprint is closer to 13.1 CPU / 17.875 GB.
- **The three feed handlers cost 1.5 CPU / 1.5 GB combined** — 10% of CPU, 7% of RAM.
  Measured usage is far below the limits (~0.03 CPU / 134 MiB for Binance, the busiest
  of the three), so the limits are headroom for volume spikes, not sizing.
- **Observability is 1.6 CPU / 2.625 GB.** Prometheus and Grafana dominate it; `iceberg-metrics`
  (0.1 CPU / 128 MB) is the offload-alerts exporter. Drop retention if you need the RAM back.
- **The Iceberg catalog costs 0.25 CPU / 256 MB.** Lakekeeper (v3, [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md))
  is the only always-on service added since the v2 baseline. It stays that cheap because it
  reuses `prefect-db` for its metadata (a `lakekeeper` database, not a second PostgreSQL) and
  MinIO for its storage. Measured 3.25% CPU / 39 MiB after bootstrap.

## Sizing a new service

Adding a fourth exchange costs one more feed handler: **0.5 CPU / 512 MB limit**
(see [adding-new-exchanges.md](./adding-new-exchanges.md)). That is the only linear
scaling axis in the stack — ClickHouse absorbs the extra bronze table and materialized
views inside its existing allocation.

## Verifying the numbers

```bash
# Declared limits, straight from the compose file
docker compose config | grep -A2 'limits:'

# Actual usage right now
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
```

Two known behaviours to expect when comparing declared vs. actual:

- `prefect-worker` spikes to ~488 MiB during startup before settling near 100 MiB. The
  limit is 512 MiB — raise it to 768 MiB if you ever see it OOM-killed.
- Redpanda is started with `--smp 1 --memory 1500M`, i.e. it self-limits below its
  2 GB container limit. That is deliberate: the container limit is the safety net.

## Related

- [ADR-010 — resource budget](../adr/ADR-010-resource-budget.md) — the original target and the v1 comparison
- [ADR-004 — eliminate Spark Streaming](../adr/ADR-004-eliminate-spark-streaming.md) — where the 13.5 CPU saving came from
- [cost-model.md](./cost-model.md) — what this footprint costs as managed cloud services
