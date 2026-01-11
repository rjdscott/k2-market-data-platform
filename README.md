# K2 Market Data Platform

A distributed market data lakehouse demonstrating production patterns for high-frequency trading infrastructure.

| | |
|---|---|
| **Stack** | Kafka (KRaft) → Iceberg → DuckDB → FastAPI |
| **Data** | ASX equities tick data (200K+ trades, March 2014) |
| **Tests** | 170+ tests (unit, integration, E2E) |
| **Python** | 3.13+ with uv package manager |

---

## What This Demonstrates

| Skill Area | Implementation |
|------------|----------------|
| **Stream Processing** | Idempotent Kafka producers, Avro serialization, Schema Registry HA (2 nodes) |
| **Lakehouse Architecture** | Apache Iceberg with ACID transactions, time-travel queries, hidden partitioning |
| **Query Optimization** | DuckDB vectorized execution, predicate pushdown to Parquet, sub-second OLAP |
| **API Design** | FastAPI with rate limiting (100 req/min), correlation IDs, multi-format output |
| **Observability** | 40+ Prometheus metrics, 15-panel Grafana dashboard, structured logging |
| **Data Quality** | Sequence gap detection, deduplication, schema validation |
| **Testing** | Pytest fixtures, mocking external services, E2E pipeline validation |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Data Sources                             │
│                    CSV files (ASX equities)                     │
└─────────────────────────────┬───────────────────────────────────┘
                              │ Batch ingestion
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Ingestion Layer                             │
│  ┌──────────────┐    ┌─────────────────┐                        │
│  │    Kafka     │◄───│ Schema Registry │  BACKWARD compatibility│
│  │   (KRaft)    │    │   (Avro, HA)    │                        │
│  └──────┬───────┘    └─────────────────┘                        │
│         │ Topics: market.equities.{trades,quotes}.asx           │
│         │ Partitioning: hash(symbol)                            │
└─────────┼───────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Storage Layer (Iceberg)                       │
│  ┌──────────────────┐        ┌────────────────┐                 │
│  │  Apache Iceberg  │◄───────│   PostgreSQL   │  Catalog        │
│  │  (ACID, Parquet) │        │      16        │  metadata       │
│  └────────┬─────────┘        └────────────────┘                 │
│           ▼                                                     │
│  ┌──────────────────┐                                           │
│  │  MinIO (S3 API)  │  Parquet + Zstd compression               │
│  │                  │  Daily partitions (exchange_date)         │
│  └──────────────────┘                                           │
└─────────┼───────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Query Layer                                │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  DuckDB Engine                                             │ │
│  │  • Vectorized execution                                    │ │
│  │  • Direct Parquet/Iceberg scan (no staging)                │ │
│  │  • Time-travel via Iceberg snapshots                       │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  FastAPI REST                                              │ │
│  │  • API key authentication                                  │ │
│  │  • Rate limiting (slowapi)                                 │ │
│  │  • JSON / CSV / Parquet output                             │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Observability                                │
│  Prometheus (40+ metrics) → Grafana (15-panel dashboard)        │
│  Structured logging (structlog) with correlation IDs            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

**Prerequisites**: Docker Desktop (8GB RAM), Python 3.13+, [uv](https://docs.astral.sh/uv/)

### 1. Start Infrastructure

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git
cd k2-market-data-platform
docker-compose up -d
```

### 2. Set Up Python Environment

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv
uv sync --all-extras
```

### 3. Initialize Platform

```bash
uv run python scripts/init_infra.py
```

### 4. Run Demo

```bash
make demo-quick   # Interactive CLI demo (~1 min)
make notebook     # Jupyter notebook exploration
```

### Verify Services

| Service | URL | Credentials |
|---------|-----|-------------|
| API Docs | http://localhost:8000/docs | `X-API-Key: k2-dev-api-key-2026` |
| Grafana | http://localhost:3000 | admin / admin |
| Kafka UI | http://localhost:8080 | - |
| MinIO | http://localhost:9001 | admin / password123! |
| Prometheus | http://localhost:9090 | - |

---

## Technology Stack

| Layer | Technology | Version | Rationale |
|-------|------------|---------|-----------|
| Streaming | Apache Kafka | 3.7 (KRaft) | No ZooKeeper, sub-1s failover |
| Schema | Confluent Schema Registry | 7.6 | BACKWARD compatibility, Avro |
| Storage | Apache Iceberg | 1.4 | ACID, time-travel, schema evolution |
| Object Store | MinIO | Latest | S3-compatible local development |
| Catalog | PostgreSQL | 16 | Iceberg metadata store |
| Query | DuckDB | 0.10 | Vectorized OLAP, zero-copy S3 |
| API | FastAPI | 0.111 | Async, OpenAPI, Pydantic |
| Metrics | Prometheus | 2.51 | Pull-based metrics collection |
| Dashboards | Grafana | 10.4 | Visualization, alerting |

---

## API Reference

Base URL: `http://localhost:8000` | Auth: `X-API-Key: k2-dev-api-key-2026`

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
| POST | `/v1/aggregations` | VWAP, TWAP, OHLCV buckets (1m/5m/15m/1h/1d) |

### System Endpoints (No Auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Liveness check |
| GET | `/metrics` | Prometheus exposition format |
| GET | `/docs` | OpenAPI documentation |

---

## Data Guarantees

### Per-Symbol Ordering

- Kafka partitioned by `hash(symbol)` → ordering within partition
- Iceberg sorted by `(exchange_timestamp, sequence_number)`
- **Guarantee**: For symbol S, sequence N+1 is always newer than N

### Sequence Gap Detection

| Gap Size | Action |
|----------|--------|
| < 10 | Log warning, continue |
| 10-100 | Alert, request recovery |
| > 100 | Halt consumer, investigate |

### Replay Modes

| Mode | Use Case | Guarantee |
|------|----------|-----------|
| **Cold Start** | Backtest 6-month strategy | Per-symbol ordering |
| **Catch-Up** | Resume after lag | Seamless Iceberg→Kafka handoff |
| **Rewind** | Compliance audit | Query as-of Iceberg snapshot |

---

## Sample Data

ASX equities tick data from March 10-14, 2014.

| Symbol | Company | Trades | Notes |
|--------|---------|--------|-------|
| BHP | BHP Billiton | 91,630 | High liquidity |
| RIO | Rio Tinto | 108,670 | High liquidity |
| DVN | Devine Ltd | 231 | Low liquidity |
| MWR | MGM Wireless | 10 | Sparse |

**Location**: `data/sample/{trades,quotes,bars-1min,reference-data}/`

```bash
# Explore sample data
head data/sample/trades/7078_trades.csv   # BHP trades
k2-query trades --symbol BHP --limit 5    # Via CLI (requires services running)
```

---

## Observability

### Prometheus Metrics (40+)

| Category | Examples |
|----------|----------|
| **Ingestion** | `k2_kafka_messages_produced_total`, `k2_sequence_gaps_detected_total` |
| **Storage** | `k2_iceberg_rows_written_total`, `k2_iceberg_write_duration_seconds` |
| **Query** | `k2_query_executions_total`, `k2_query_duration_seconds` |
| **API** | `k2_http_requests_total`, `k2_http_request_duration_seconds` |
| **System** | `k2_circuit_breaker_state`, `k2_degradation_level` |

### Grafana Dashboard (15 panels)

Pre-configured dashboard at http://localhost:3000 with:
- API request rate and latency (p99)
- Kafka consumer lag
- Iceberg write performance
- Query cache hit ratio
- System degradation level

### Structured Logging

- **Library**: structlog with JSON output
- **Correlation IDs**: Propagated via `X-Correlation-ID` header
- **Fields**: timestamp, level, correlation_id, endpoint, duration_ms

---

## Testing

### Coverage

| Type | Count | Command |
|------|-------|---------|
| Unit | 120+ | `make test-unit` |
| Integration | 40+ | `make test-integration` |
| E2E | 7 | `make test-integration` |

### Test Organization

```
tests/
├── unit/                # Fast, no Docker required
│   ├── test_api.py
│   ├── test_producer.py
│   ├── test_query_engine.py
│   └── ...
├── integration/         # Requires Docker services
│   ├── test_e2e_flow.py
│   ├── test_iceberg_storage.py
│   └── ...
└── performance/         # Benchmarks
```

### Run Tests

```bash
make test-unit          # Fast unit tests (~10s)
make test-integration   # Full integration (~60s, requires Docker)
make coverage           # Generate coverage report
```

---

## Project Structure

```
src/k2/
├── api/                 # REST API (1,400 lines)
│   ├── main.py          # FastAPI app, middleware stack
│   ├── v1/endpoints.py  # All /v1/ routes
│   ├── middleware.py    # Auth, rate limiting, CORS
│   └── models.py        # Pydantic request/response models
├── ingestion/           # Data ingestion (2,300 lines)
│   ├── producer.py      # Idempotent Kafka producer
│   ├── consumer.py      # Kafka → Iceberg writer
│   ├── batch_loader.py  # CSV batch ingestion
│   └── sequence_tracker.py  # Gap detection
├── storage/             # Iceberg lakehouse (900 lines)
│   ├── catalog.py       # Table management
│   └── writer.py        # Batch writes
├── query/               # Query engine (1,700 lines)
│   ├── engine.py        # DuckDB + Iceberg connector
│   ├── replay.py        # Time-travel queries
│   └── cli.py           # k2-query CLI
├── kafka/               # Kafka utilities (800 lines)
├── schemas/             # Avro schemas (trade, quote, reference)
└── common/              # Config, logging, metrics (1,300 lines)
```

**Total**: ~8,400 lines of Python

---

## CLI Reference

### k2-query

```bash
k2-query trades --symbol BHP --limit 10
k2-query quotes --symbol BHP
k2-query summary DVN 2014-03-10
k2-query symbols
k2-query snapshots
k2-query stats
```

### Make Targets

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
make test-integration   # Requires Docker
make coverage           # Coverage report

# Quality
make format             # Black + isort
make lint-fix           # Ruff with auto-fix
make quality            # All checks
```

---

## Documentation

| Document | Purpose |
|----------|---------|
| [Implementation Plan](./docs/phases/phase-1-portfolio-demo/IMPLEMENTATION_PLAN.md) | 16-step build plan |
| [Progress Tracker](./docs/phases/phase-1-portfolio-demo/PROGRESS.md) | Detailed status |
| [Decisions (ADRs)](./docs/phases/phase-1-portfolio-demo/DECISIONS.md) | 30 architectural decisions |
| [Testing Guide](./docs/TESTING.md) | Test organization and patterns |
| [Architecture](./docs/architecture/README.md) | System design documents |

---

## Roadmap

### Implemented (Phase 1)

- [x] Kafka ingestion with Avro schemas
- [x] Iceberg lakehouse with time-travel
- [x] DuckDB query engine
- [x] REST API with authentication
- [x] Prometheus + Grafana observability
- [x] Demo and E2E tests

### Planned (Phase 2+)

- [ ] Real-time streaming consumers
- [ ] RBAC and row-level security
- [ ] Kubernetes deployment
- [ ] Multi-region replication

---

## Contact

**Author**: Rob Scott
**Role**: Senior Data Engineer
**LinkedIn**: [rjdscott](https://www.linkedin.com/in/rjdscott/)

---

## License

MIT License - see [LICENSE](./LICENSE) for details.
