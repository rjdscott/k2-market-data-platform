# K2 Market Data Platform

A distributed market data lakehouse for quantitative research, compliance, and analytics.

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Apache Kafka](https://img.shields.io/badge/kafka-3.7-orange.svg)](https://kafka.apache.org/)
[![Apache Spark](https://img.shields.io/badge/spark-3.5-yellow.svg)](https://spark.apache.org/)
[![Apache Iceberg](https://img.shields.io/badge/iceberg-1.4-green.svg)](https://iceberg.apache.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Documentation Hub**: [docs/NAVIGATION.md](./docs/NAVIGATION.md) - Role-based paths to find any doc in under 2 minutes

---

## What is K2?

K2 is an **L3 Cold Path Research Data Platform** optimized for analytics, compliance, and historical research rather than real-time execution.

**Key Capabilities**:
- High-throughput ingestion (1M+ msg/sec at scale)
- ACID-compliant storage with time-travel queries via Apache Iceberg
- Sub-second analytical queries (point: <100ms, aggregations: 200-500ms)
- Pre-computed OHLCV analytics across 5 timeframes (1m, 5m, 30m, 1h, 1d)

| Aspect | K2 IS | K2 is NOT |
|--------|-------|-----------|
| Latency | <500ms p99 | <10us (HFT) |
| Use Case | Research, compliance, backtesting | Execution, market making |
| Storage | Unlimited historical (S3-backed) | In-memory real-time |
| Cost | $0.85 per million messages | Premium low-latency infra |

**Target Use Cases**:
1. Quantitative research and backtesting
2. Regulatory compliance and audits (FINRA, SEC, MiFID II)
3. Market microstructure analysis
4. Performance attribution and TCA
5. Multi-vendor data reconciliation
6. Historical research and ad-hoc analytics

For detailed positioning: [docs/architecture/platform-positioning.md](./docs/architecture/platform-positioning.md)

---

## Quick Start

**Prerequisites**: Docker Desktop (8GB RAM), Python 3.13+, [uv](https://docs.astral.sh/uv/)

### 1. Clone and Start Infrastructure

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git
cd k2-market-data-platform

# Option A: Start core infrastructure only (lighter, for exploration)
docker compose up -d kafka schema-registry-1 minio postgres iceberg-rest \
  prometheus grafana kafka-ui spark-master spark-worker-1 spark-worker-2 prefect-server

# Option B: Start full stack including API, streaming, and all jobs
docker compose up -d
```

### 2. Set Up Python Environment

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync --all-extras
```

### 3. Initialize Platform

```bash
uv run python scripts/init_infra.py
```

### 4. Verify Services

| Service | URL | Credentials |
|---------|-----|-------------|
| API Server | http://localhost:8000/docs | `X-API-Key: k2-dev-api-key-2026` |
| Grafana | http://localhost:3000 | admin / admin |
| Kafka UI | http://localhost:8080 | - |
| MinIO Console | http://localhost:9001 | admin / password |
| Prometheus | http://localhost:9090 | - |
| Spark Master UI | http://localhost:8090 | - |
| Prefect UI | http://localhost:4200 | - |

### 5. Start API Server (Development Mode)

The API server runs in a container with `docker compose up -d`. For local development with hot-reload:

```bash
# Stop container API first to free port 8000
docker compose stop k2-query-api

# Run locally with hot-reload
make api
```

### 6. Start Streaming Services

If you used Option A (core infrastructure only), start the streaming pipeline:

```bash
# Start WebSocket producers (connect to Binance + Kraken)
docker compose up -d binance-stream kraken-stream

# Start Bronze streaming jobs (Kafka to Iceberg ingestion)
docker compose up -d bronze-binance-stream bronze-kraken-stream

# Start Silver transformation jobs (validation + schema conversion)
docker compose up -d silver-binance-transformation silver-kraken-transformation

# Start Gold aggregation job (cross-exchange unified view)
docker compose up -d gold-aggregation
```

Verify streaming is working:
```bash
# Check Spark UI for running jobs
open http://localhost:8090

# Check Kafka UI for message flow
open http://localhost:8080
```

### 7. Run Demo (Optional)

```bash
make demo-quick   # Interactive CLI demo
make notebook     # Jupyter notebook exploration
```

---

## Architecture Overview

```
                              Data Sources
    Historical Archive   |   Binance WebSocket   |   Kraken WebSocket
           |                      |                       |
           v                      v                       v
    +-----------------------------------------------------------------+
    |                     Ingestion Layer                             |
    |   Kafka (KRaft 3.7)  <-->  Schema Registry (Avro, BACKWARD)     |
    |   Topics: market.crypto.trades.{binance,kraken}.raw             |
    +-----------------------------------------------------------------+
                                  |
                                  v
    +-----------------------------------------------------------------+
    |               Processing Layer (Spark Streaming 3.5)            |
    |                                                                 |
    |   Bronze Layer          Silver Layer           Gold Layer       |
    |   (Raw Ingestion)  -->  (Validated)    -->   (Unified)          |
    |   Per-exchange          Schema-validated     Cross-exchange     |
    |   Raw Avro bytes        V2 trade schema      Deduplicated       |
    +-----------------------------------------------------------------+
                                  |
                                  v
    +-----------------------------------------------------------------+
    |                    Storage Layer (Iceberg)                      |
    |                                                                 |
    |   PostgreSQL 16         MinIO (S3 API)        Parquet + Zstd    |
    |   (REST Catalog)        (Object Storage)      Hourly partitions |
    |                                                                 |
    |   Tables:                                                       |
    |   - bronze_binance_trades    - silver_binance_trades            |
    |   - bronze_kraken_trades     - silver_kraken_trades             |
    |   - gold_crypto_trades       - gold_ohlcv_{1m,5m,30m,1h,1d}     |
    +-----------------------------------------------------------------+
                                  |
                                  v
    +-----------------------------------------------------------------+
    |                       Query Layer                               |
    |   DuckDB (OLAP)  -->  FastAPI REST  -->  JSON/CSV/Parquet       |
    |   Connection pooling   API key auth      Time-travel queries    |
    +-----------------------------------------------------------------+
                                  |
                                  v
    +-----------------------------------------------------------------+
    |                      Observability                              |
    |   Prometheus (50+ metrics)  -->  Grafana (15-panel dashboard)   |
    |   Structured logging (structlog) with correlation IDs           |
    |   Circuit breaker + graceful degradation (5 levels)             |
    +-----------------------------------------------------------------+
```

### Medallion Architecture

K2 implements a three-layer medallion architecture for data quality:

| Layer | Purpose | Tables | Update Frequency |
|-------|---------|--------|------------------|
| **Bronze** | Raw ingestion | `bronze_{binance,kraken}_trades` | Real-time streaming |
| **Silver** | Validated, schema-compliant | `silver_{binance,kraken}_trades` | Real-time streaming |
| **Gold** | Unified, deduplicated | `gold_crypto_trades`, `gold_ohlcv_*` | Real-time + batch |

### OHLCV Analytics (Phase 13)

Pre-computed OHLCV candles across 5 timeframes, orchestrated by Prefect:

| Timeframe | Table | Schedule | Retention |
|-----------|-------|----------|-----------|
| 1 minute | `gold_ohlcv_1m` | Every 5 min | 90 days |
| 5 minutes | `gold_ohlcv_5m` | Every 15 min | 180 days |
| 30 minutes | `gold_ohlcv_30m` | Every 30 min | 1 year |
| 1 hour | `gold_ohlcv_1h` | Hourly | 3 years |
| 1 day | `gold_ohlcv_1d` | Daily 00:05 | 5 years |

### Technology Choices

| Component | Technology | Version | Rationale |
|-----------|------------|---------|-----------|
| Streaming | Apache Kafka | 3.7 (KRaft) | No ZooKeeper, sub-1s failover |
| Schema | Schema Registry | 8.1.1 | BACKWARD compatibility enforcement |
| Processing | Apache Spark | 3.5 | Structured streaming, medallion architecture |
| Storage | Apache Iceberg | 1.4 | ACID + time-travel for compliance |
| Object Store | MinIO | Latest | S3-compatible local development |
| Catalog | PostgreSQL | 16 | Proven Iceberg REST catalog metadata |
| Query | DuckDB | 1.4 | Zero-ops, connection pooling (5-50 concurrent) |
| API | FastAPI | 0.128 | Async + auto-docs |
| Orchestration | Prefect | 3.1 | Lightweight batch job scheduling |
| Metrics | Prometheus | 3.9 | Pull-based, industry standard |
| Dashboards | Grafana | 12.3 | Visualization + alerting |

---

## End-to-End Workflows

### Workflow 1: Real-Time Crypto Ingestion

```
Binance/Kraken WebSocket
    --> Kafka (market.crypto.trades.{exchange}.raw)
    --> Spark Bronze Job (raw bytes to Iceberg)
    --> Spark Silver Job (validate, deserialize to V2 schema)
    --> Spark Gold Job (union, deduplicate)
    --> gold_crypto_trades table
```

**Exchanges Supported**:
- Binance: BTCUSDT, ETHUSDT
- Kraken: BTC/USD, ETH/USD

### Workflow 2: OHLCV Analytics

```
gold_crypto_trades
    --> Prefect scheduled jobs
    --> Spark batch aggregation
    --> gold_ohlcv_{1m,5m,30m,1h,1d} tables
```

**Features**: VWAP, trade count, automatic retention enforcement

### Workflow 3: Query via API

```bash
# Get recent trades
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100"

# Get OHLCV summary
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/summary/BTCUSDT/2026-01-20"
```

### Workflow 4: Historical Replay (Time-Travel)

```bash
# List available snapshots
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/snapshots"

# Query at specific snapshot
curl -X POST -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/snapshots/{snapshot_id}/query" \
  -d '{"symbol": "BTCUSDT", "limit": 100}'
```

---

## API Reference

**Base URL**: `http://localhost:8000` | **Auth**: `X-API-Key: k2-dev-api-key-2026`

### Read Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/trades` | Query trades by symbol, exchange, time range |
| GET | `/v1/quotes` | Query quotes (bid/ask) |
| GET | `/v1/summary/{symbol}/{date}` | OHLCV daily summary |
| GET | `/v1/symbols` | List available symbols |
| GET | `/v1/stats` | Database statistics |
| GET | `/v1/snapshots` | List Iceberg snapshots |

### Advanced Query Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/trades/query` | Multi-symbol, field selection, CSV/Parquet output |
| POST | `/v1/quotes/query` | Multi-symbol quotes with format options |
| POST | `/v1/replay` | Historical replay with cursor pagination |
| POST | `/v1/snapshots/{id}/query` | Point-in-time (time-travel) query |
| POST | `/v1/aggregations` | VWAP, TWAP, OHLCV buckets |

### System Endpoints (No Auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Liveness check |
| GET | `/metrics` | Prometheus exposition format |
| GET | `/docs` | OpenAPI documentation |

Full API documentation: [docs/reference/api-reference.md](./docs/reference/api-reference.md)

---

## Project Structure

```
src/k2/
|-- api/                 # REST API (FastAPI)
|   |-- main.py          # App entry point, middleware stack
|   |-- v1/endpoints.py  # All /v1/ routes
|   |-- middleware.py    # Auth, rate limiting, CORS
|   +-- models.py        # Pydantic request/response models
|-- ingestion/           # Data ingestion
|   |-- producer.py      # Idempotent Kafka producer
|   |-- consumer.py      # Kafka to Iceberg writer
|   |-- binance_client.py # Binance WebSocket client
|   +-- kraken_client.py # Kraken WebSocket client
|-- spark/               # Spark Streaming jobs
|   |-- jobs/streaming/  # Bronze/Silver/Gold jobs
|   +-- jobs/batch/      # OHLCV aggregation jobs
|-- storage/             # Iceberg lakehouse
|   |-- catalog.py       # Table management
|   +-- writer.py        # Batch writes
|-- query/               # Query engine
|   |-- engine.py        # DuckDB + Iceberg connector
|   +-- replay.py        # Time-travel queries
|-- schemas/             # Avro schemas (trade_v2, quote_v2)
+-- common/              # Config, logging, metrics
```

**Key Entry Points**:
- `src/k2/api/main.py` - FastAPI application
- `src/k2/spark/jobs/streaming/` - Streaming transformation jobs
- `scripts/init_infra.py` - Infrastructure initialization

---

## Development

### Running Tests

| Type | Count | Command | Duration |
|------|-------|---------|----------|
| Unit | 169 | `uv run pytest tests/unit/` | ~5s |
| Integration | 46+ | `make test-integration` | ~60s |
| E2E | 7 | `make test-e2e` | ~60s |
| **Total** | **222+** | | |

```bash
# Unit tests only (fast)
uv run pytest tests/unit/ -v

# Full test suite (requires Docker)
make test-integration

# Coverage report
make coverage
```

### Code Quality

```bash
make quality   # Format + lint + type-check

# Individual commands
make format    # Black + isort
make lint      # Ruff
make type-check # Mypy
```

### Make Targets Summary

```bash
# Infrastructure
make docker-up          # Start all services
make docker-down        # Stop services
make init-infra         # Initialize Kafka, schemas, Iceberg

# Development
make api                # Start API (dev mode with reload)
make demo-quick         # Run demo (CI-friendly)
make notebook           # Jupyter notebook

# Testing
make test-unit          # Unit tests only
make test-integration   # Integration tests (requires Docker)
make test-pr            # PR validation (lint + type + unit)
make coverage           # Coverage report

# Quality
make format             # Black + isort
make lint-fix           # Ruff with auto-fix
make quality            # All checks
```

---

## Documentation

**Start Here**: [docs/NAVIGATION.md](./docs/NAVIGATION.md) - Role-based documentation paths

### Quick Paths

| Role | Time | Link |
|------|------|------|
| New Engineer | 30 min | [Onboarding Path](./docs/NAVIGATION.md#-new-engineer-30-minute-onboarding-path) |
| On-Call Engineer | 15 min | [Emergency Runbooks](./docs/NAVIGATION.md#-operatoron-call-engineer-15-minute-emergency-path) |
| API Consumer | 20 min | [Integration Guide](./docs/NAVIGATION.md#-api-consumer-20-minute-integration-path) |
| Contributor | 45 min | [Deep Dive Path](./docs/NAVIGATION.md#-contributordeveloper-45-minute-deep-dive-path) |

### Documentation Categories

| Category | Description | Key Docs |
|----------|-------------|----------|
| **Architecture** | System design, principles | [platform-principles.md](./docs/architecture/platform-principles.md), [system-design.md](./docs/architecture/system-design.md) |
| **Operations** | Runbooks, monitoring | [operations/README.md](./docs/operations/README.md) |
| **Reference** | API, schemas, config | [api-reference.md](./docs/reference/api-reference.md), [data-dictionary-v2.md](./docs/reference/data-dictionary-v2.md) |
| **Phases** | Implementation history | [phases/README.md](./docs/phases/README.md) |

### Documentation Health

- Zero broken links (376 validated)
- 12 comprehensive reference docs
- 8 operational runbooks
- Run `bash scripts/validate-docs.sh` to verify

---

## License

MIT License - see [LICENSE](./LICENSE) for details.

## Contributing

Contributions welcome. See the [Development](#development) section for setup and the documentation at [docs/](./docs/) for guidelines.

## Questions?

- **Documentation**: [docs/](./docs/)
- **Issues**: [GitHub Issues](https://github.com/rjdscott/k2-market-data-platform/issues)
- **Architecture**: [Architecture Decision Records](./docs/phases/)
