# K2 Market Data Platform

A production cryptocurrency market data lakehouse for quantitative research, compliance, and analytics.
Ingests live trades from 3 exchanges, stores 33M+ rows across ClickHouse (hot) and Iceberg (cold), and
serves sub-second analytical queries.

[![Redpanda](https://img.shields.io/badge/redpanda-25.3-red.svg)](https://redpanda.com/)
[![ClickHouse](https://img.shields.io/badge/clickhouse-24.3_LTS-yellow.svg)](https://clickhouse.com/)
[![Kotlin](https://img.shields.io/badge/kotlin-2.0-purple.svg)](https://kotlinlang.org/)
[![Apache Spark](https://img.shields.io/badge/spark-3.5-yellow.svg)](https://spark.apache.org/)
[![Apache Iceberg](https://img.shields.io/badge/iceberg-1.4-green.svg)](https://iceberg.apache.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Documentation Hub**: [docs/NAVIGATION.md](./docs/NAVIGATION.md) — role-based paths to any doc in < 2 min

---

## Quick Start (v2)

**Prerequisites**: Docker (16GB RAM, 16 Cores recommended)

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git
cd k2-market-data-platform
docker compose -f docker-compose.v2.yml up -d
```

Services take ~30 seconds to initialize. Verify:

```bash
docker compose -f docker-compose.v2.yml ps
```

---

## Architecture (v2)

```
Exchange APIs (Binance, Kraken, Coinbase)
        │
        ▼
Kotlin Feed Handlers (Spring Boot, 1 per exchange)
        │  WebSocket → normalized JSON
        ▼
Redpanda  (Kafka-compatible, 3 topics: 40/20/20 partitions)
        │
        ▼
ClickHouse — Kafka Engine → Bronze tables
        │      Materialized Views → Silver (silver_trades)
        │      Materialized Views → Gold (OHLCV 1m/5m/15m/30m/1h/1d)
        │
        ▼ (Spark batch offload, every 15 min via Prefect)
Iceberg (cold tier, MinIO-backed, cold.bronze_trades_*, cold.silver_trades, cold.gold_ohlcv_*)
```

**Medallion layers**:
- **Bronze** — raw normalized trades (Decimal types, per-exchange tables)
- **Silver** — unified `silver_trades` view across all 3 exchanges
- **Gold** — pre-computed OHLCV candles (6 timeframes)
- **Cold** — Iceberg tables for historical depth beyond ClickHouse TTL

---

## Services

| Service | Purpose | URL |
|---------|---------|-----|
| `feed-handler-binance` | Kotlin WebSocket → Redpanda | internal |
| `feed-handler-kraken` | Kotlin WebSocket → Redpanda | internal |
| `feed-handler-coinbase` | Kotlin WebSocket → Redpanda | internal |
| `redpanda` | Kafka-compatible message broker | broker:9092 |
| `redpanda-console` | Topic browser / consumer lag | http://localhost:8080 |
| `clickhouse` | OLAP database (hot tier) | http://localhost:8123 |
| `spark-iceberg` | Batch offload jobs | http://localhost:8090 |
| `prefect-server` | Orchestration (offload schedule) | http://localhost:4200 |
| `prefect-worker` | Executes Prefect flows | internal |
| `prefect-db` | Prefect metadata (PostgreSQL) | internal |
| `minio` | Object storage (Iceberg warehouse) | http://localhost:9001 |
| `prometheus` | Metrics collection | http://localhost:9090 |
| `grafana` | Dashboards | http://localhost:3000 (admin/admin) |

**Resource budget**: ~15.5 CPU / ~21.75 GB RAM (13 active services)

---

## Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Message broker | Redpanda | 25.3 |
| OLAP / hot tier | ClickHouse | 24.3 LTS |
| Feed handlers | Kotlin + Spring Boot | Kotlin 2.0 |
| Batch processing | Apache Spark | 3.5 |
| Cold storage format | Apache Iceberg | 1.4 |
| Object storage | MinIO | latest |
| Orchestration | Prefect | 3.x |
| Observability | Prometheus + Grafana | — |
| Python env | uv | — |

---

## Key Capabilities

- **3 live exchanges**: Binance (12 pairs), Kraken (11 pairs), Coinbase (11 pairs)
- **33M+ rows** in Iceberg cold tier; continuous hot tier in ClickHouse
- **OHLCV analytics**: 6 timeframes computed via ClickHouse MVs (no batch jobs)
- **Automatic offload**: Prefect schedules Spark every 15 minutes → Iceberg
- **Sub-second queries**: ClickHouse point queries < 100ms, aggregations < 500ms
- **ACID cold storage**: Iceberg with time-travel support

For platform positioning and use-case fit: [docs/architecture/platform-positioning.md](./docs/architecture/platform-positioning.md)

---

## Documentation

Role-based navigation: **[docs/NAVIGATION.md](./docs/NAVIGATION.md)**

| Area | Path |
|------|------|
| Architecture decisions (ADRs) | [docs/decisions/platform-v2/](./docs/decisions/platform-v2/) |
| Current platform state | [docs/phases/v2/CURRENT-STATE.md](./docs/phases/v2/CURRENT-STATE.md) |
| Operations & runbooks | [docs/operations/](./docs/operations/) |
| Testing strategy | [docs/testing/](./docs/testing/) |
| Adding a new exchange | [docs/operations/adding-new-exchanges.md](./docs/operations/adding-new-exchanges.md) |

---

## v1 (legacy)

The original v1 platform (Python / Kafka / Spark Streaming / DuckDB / FastAPI) is archived in
[docs/archive/](./docs/archive/). It is preserved for historical reference and is no longer active.
