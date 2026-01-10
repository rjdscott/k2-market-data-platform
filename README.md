# K2 - Market Data Platform

**Status**: Production-Ready Design | Demo Implementation
**Version**: 0.1.0
**Last Updated**: 2026-01-09

---

## Platform Overview

K2 is a distributed market data platform designed for high-frequency trading environments where microsecond latency and petabyte-scale storage must coexist. This implementation demonstrates architectural patterns for streaming ingestion, lakehouse storage, and operational reliability suitable for financial services infrastructure.

**Design Philosophy**: Explicit trade-offs over implicit complexity. Every architectural decision documents what we optimize for, what we sacrifice, and what breaks first under load.

---

## Core Design Principles

This platform is built on six non-negotiable principles. See [**Platform Principles**](./docs/PLATFORM_PRINCIPLES.md) for detailed rationale.

1. **Replayable by Default** - Every pipeline supports arbitrary time-range replay
2. **Schema-First, Always** - No unstructured data enters the platform
3. **Boring Technology** - Proven systems over novel solutions
4. **Degrade Gracefully** - Explicit failure modes, not unpredictable crashes
5. **Idempotency Over Exactly-Once** - At-least-once + deduplication by default
6. **Observable by Default** - Metrics, logs, traces are not optional

**Guardrails for Downstream Teams**: See [Platform Principles - What Teams MUST/MUST NOT Do](./docs/PLATFORM_PRINCIPLES.md#platform-guardrails)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Data Producers                              │
│  Market Data Feeds, Trading Venues, Vendor Data, Quant Research     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Ingestion Layer                                │
│  ┌──────────────┐    ┌─────────────────┐                            │
│  │    Kafka     │◄───│ Schema Registry │  (BACKWARD compatibility)  │
│  │   (KRaft)    │    │   (Avro/JSON)   │                            │
│  └──────┬───────┘    └─────────────────┘                            │
│         │ Partitioning: hash(exchange.symbol)                       │
│         │ Topics: market.ticks, market.trades                       │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Processing Layer                               │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  Stream Processors (Kafka Consumers)                       │     │
│  │  • Real-time aggregation                                   │     │
│  │  • Validation & enrichment                                 │     │
│  │  • Deduplication                                           │     │
│  └────────────────────────────────────────────────────────────┘     │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Storage Layer (Iceberg)                          │
│  ┌──────────────────┐        ┌────────────────┐                     │
│  │  Apache Iceberg  │◄───────│   PostgreSQL   │  (Catalog Metadata) │
│  │  (ACID Lakehouse)│        │   + Audit Log  │                     │
│  └────────┬─────────┘        └────────────────┘                     │
│           ▼                                                         │
│  ┌──────────────────┐                                               │
│  │  MinIO (S3 API)  │  (Parquet files, partition metadata)          │
│  │  Object Storage  │                                               │
│  └──────────────────┘                                               │
│  • Time-partitioned (day/hour)                                      │
│  • Columnar format (Parquet + Zstd compression)                     │
│  • Snapshot isolation for time-travel queries                       │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Query Layer                                  │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  Query API (FastAPI/GraphQL)                               │     │
│  │  • Real-time: Direct Kafka consumer                        │     │
│  │  • Historical: DuckDB + Iceberg connector                  │     │
│  │  • Hybrid: Merge real-time + historical views              │     │
│  └────────────────────────────────────────────────────────────┘     │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  Governance Layer                                          │     │
│  │  • RBAC (Role-Based Access Control)                        │     │
│  │  • Row-level security                                      │     │
│  │  • Audit logging                                           │     │
│  └────────────────────────────────────────────────────────────┘     │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│               Observability (Prometheus + Grafana)                  │
│  Critical Metrics:                                                  │
│  • kafka_consumer_lag_seconds (alert > 60s)                         │
│  • sequence_gaps_total (data loss detection)                        │
│  • iceberg_write_duration_p99 (latency budget)                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Documentation

This repository includes comprehensive documentation organized for staff/principal engineers and AI assistants:

### 📚 **Documentation Hub**
- [**Documentation Index**](./docs/README.md) - Complete documentation navigation
- [**Claude.md**](./docs/CLAUDE.md) - AI assistant guidance for documentation maintenance

### 🏗️ **Architecture & Design**
- [**Architecture**](./docs/architecture/) - Permanent architectural decisions, platform principles, tech stack
- [**Design**](./docs/design/) - Component-level design, data guarantees, interfaces
- [**Alternative Architectures**](./docs/architecture/alternatives.md) - Architectures we considered

### 📋 **Phase 1: Portfolio Demo** (Current)
- [**Implementation Plan**](./docs/phases/phase-1-portfolio-demo/IMPLEMENTATION_PLAN.md) - 16-step plan
- [**Progress Tracker**](./docs/phases/phase-1-portfolio-demo/PROGRESS.md) - Current status
- [**Decisions (ADRs)**](./docs/phases/phase-1-portfolio-demo/DECISIONS.md) - Phase 1 design choices
- [**Implementation Steps**](./docs/phases/phase-1-portfolio-demo/steps/) - Detailed step guides

### 🔧 **Operations & Testing**
- [**Operations**](./docs/operations/) - Runbooks, monitoring, performance tuning
- [**Testing**](./docs/testing/) - Testing strategy, patterns, coverage targets
- [**Reference**](./docs/reference/) - API docs, glossary, configuration

---

## Market Data Guarantees

Financial data has unique ordering and replay requirements. See [**Market Data Guarantees**](./docs/design/data-guarantees/ordering-guarantees.md) for full design.

### Per-Symbol Ordering

**Guarantee**: For any symbol, sequence number N+1 is always newer than N

**Implementation**:
- Kafka topics partitioned by `exchange.symbol` (ordering within partition)
- Iceberg tables sorted by `(exchange_timestamp, exchange_sequence_number)`
- Sequence gap detection with configurable alert thresholds

**Code Reference**: [`src/k2/ingestion/sequence_tracker.py`](./src/k2/ingestion/sequence_tracker.py)

### Sequence Number Tracking

Every exchange provides monotonically increasing sequence numbers. Platform detects:
- **Gaps**: Missed messages (network loss, exchange halt)
- **Resets**: Session restarts (daily market open)
- **Out-of-order**: Kafka partition reassignment, network delays

**Alert Thresholds**:
- Gap < 10: Log warning, continue
- Gap 10-100: Page on-call, request recovery from exchange
- Gap > 100: Halt consumer, manual investigation

### Replay Semantics

| Mode | Use Case | Ordering Guarantee |
|------|----------|-------------------|
| **Cold Start** | Backtest 6-month strategy | Per-symbol only |
| **Catch-Up** | Resume after 2-hour lag | Seamless Iceberg→Kafka handoff |
| **Rewind** | Compliance audit | Query as-of snapshot timestamp |

---

## Latency Budgets & Backpressure

See [**Latency & Backpressure Design**](./docs/LATENCY_BACKPRESSURE.md) for detailed performance characteristics.

### End-to-End Latency Budget

**Target**: Exchange → Query API = **500ms @ p99**

| Stage | Component | p99 Target | Degrades To | Alert Threshold |
|-------|-----------|------------|-------------|-----------------|
| Ingestion | Feed handler → Kafka | 10ms | Drop non-critical symbols | p99 > 20ms |
| Kafka | Producer → Consumer | 20ms | Increase batch size | Lag > 100K msg |
| Processing | Business logic | 50ms | Skip enrichment | p99 > 80ms |
| Storage | Iceberg write | 200ms | Spill to local disk | p99 > 400ms |
| Query | API response | 300ms | Reduce complexity, cache | p99 > 500ms |

### Degradation Cascade

Under load, components degrade in this order (see [Latency & Backpressure](./docs/LATENCY_BACKPRESSURE.md#backpressure-cascade)):

1. **Soft Degradation**: p99 latency increases (alert, no user impact)
2. **Graceful Degradation**: Drop low-priority symbols, skip enrichment
3. **Spill to Disk**: Buffer to NVMe, async flush to Iceberg
4. **Circuit Breaker**: Halt consumption, page on-call

**Load Test Validation**: Platform survives 10x traffic spike with Level 2 degradation (no data loss)

---

## Correctness Trade-offs

See [**Correctness Trade-offs**](./docs/CORRECTNESS_TRADEOFFS.md) for full decision tree.

### Delivery Guarantees

| Guarantee | Cost | When We Use It |
|-----------|------|----------------|
| **At-most-once** | Lowest | Metrics, logs (acceptable loss) |
| **At-least-once + Idempotency** | Low | Market data ingestion (**default**) |
| **Exactly-once** | High (2-3x latency) | Financial aggregations (PnL calculations) |

**Platform Default**: At-least-once with idempotent consumers

**Rationale**: Every market data message has a unique ID (`exchange.symbol.sequence`). Iceberg merge-on-read handles duplicates automatically. Exactly-once adds 2-3x latency for edge cases we can handle via deduplication.

### Where Exactly-Once IS Required

1. **Financial aggregations** (total PnL, traded volume)
2. **Regulatory reporting** (must be byte-perfect)

**Implementation**: Kafka transactions with `isolation.level=read_committed`

### Where Data Loss IS Unacceptable

1. **Trade execution records** (synchronous Kafka + Iceberg writes before ACK)
2. **Audit logs** (query fails if audit write fails)

**Kafka Config**: `acks=all`, `min.insync.replicas=2`, `enable.idempotence=true`

---

## Failure & Recovery

See [**Failure & Recovery Runbook**](./docs/FAILURE_RECOVERY.md) for operational procedures.

### Covered Scenarios

Each scenario documents: detection signal, blast radius, recovery procedure, follow-up fix.

1. **Kafka Broker Loss During Market Open**
   - Detection: `kafka_broker_down` alert
   - Recovery time: 5 minutes (automatic leader election)
   - Follow-up: Increase replication factor to 3

2. **Consumer Lagging Hours Behind**
   - Detection: `kafka_consumer_lag_seconds > 3600`
   - Recovery: Horizontal scaling + catch-up from Iceberg
   - Follow-up: Add autoscaling based on lag

3. **Incompatible Schema Pushed Accidentally**
   - Detection: `schema_registry_compatibility_failures`
   - Recovery: Rollback producer, re-register corrected schema
   - Follow-up: Enforce compatibility checks in CI/CD

4. **Iceberg Catalog Metadata Slowdown**
   - Detection: `iceberg_catalog_query_duration_p99 > 10s`
   - Recovery: Kill long-running queries, add indexes
   - Follow-up: PostgreSQL read replicas, metadata caching

---

## Technology Decisions

### Kafka with KRaft (No ZooKeeper)

**Decision**: Use KRaft mode for metadata management

**Trade-off**: Simpler operations vs less mature (KRaft GA in Kafka 3.3+)

**Wins**:
- Sub-1s controller failover (vs ~30s with ZooKeeper)
- Supports millions of partitions
- One less system to manage

**Scaling**: At 1000x scale, KRaft handles 500+ partitions per topic without performance degradation

---

### Apache Iceberg for Lakehouse

**Decision**: Iceberg over Delta Lake or raw Parquet

**Trade-off**: ACID guarantees vs write throughput

**Wins**:
- Time-travel queries (compliance requirement)
- Schema evolution without rewriting data
- Hidden partitioning (consumers don't know partition layout)
- Snapshot isolation (concurrent reads/writes)

**Scaling**: Petabyte-scale datasets at Netflix, Apple, Adobe

---

### DuckDB for Query Engine

**Decision**: DuckDB for analytical queries (not Spark/Presto)

**Trade-off**: Embedded simplicity vs distributed scale

**Wins**:
- Zero-copy S3 reads (scan Parquet without staging)
- Vectorized execution (10M rows/sec single-threaded)
- No cluster management overhead

**When to Upgrade**: Add Presto/Trino at 100x-1000x scale for multi-day queries

---

### Backward Compatibility for Schemas

**Decision**: Enforce BACKWARD compatibility in Schema Registry

**Trade-off**: Consumer safety vs producer flexibility

**Wins**:
- Downstream consumers never break on schema changes
- Can add optional fields without redeploying consumers
- Prevents production incidents from schema mismatches

**Cost**: Producers cannot remove required fields (must deprecate over 30 days)

---

## Scaling Considerations

| Metric | Local Dev | 100x Scale | 1000x Scale |
|--------|-----------|------------|-------------|
| **Throughput** | 10K msg/sec | 1M msg/sec | 10M+ msg/sec |
| **Kafka** | 1 broker | 3-5 brokers, 50+ partitions | 10+ brokers, 500+ partitions |
| **Storage** | GBs | TBs (MinIO distributed) | PBs (S3 with lifecycle) |
| **Latency p99** | <100ms | <50ms (SSD, read replicas) | <20ms (edge cache, pre-agg) |
| **Query Engine** | DuckDB | DuckDB + Presto | Presto/Trino (100+ nodes) |

**Bottleneck Analysis**: See [README - Scaling Bottlenecks](#bottleneck-analysis-from-original-readme) for detailed mitigation strategies

---

## Quick Start

### Prerequisites

- Docker Desktop (8GB RAM minimum)
- Python 3.11+
- Make (optional)

### 1. Start Infrastructure

```bash
# Clone repository
git clone <repository-url>
cd k2-market-data-platform

# Start all services (Kafka, Schema Registry, MinIO, Iceberg, Prometheus, Grafana)
docker-compose up -d

# Verify services healthy
docker-compose ps
```

**Service Endpoints**:
- Kafka UI: http://localhost:8080
- MinIO Console: http://localhost:9001 (admin / password123!)
- Grafana: http://localhost:3000 (admin / admin)
- Prometheus: http://localhost:9090

### 2. Initialize Platform

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Initialize Iceberg tables (TODO: implement scripts/init_tables.py)
python scripts/init_tables.py

# Start market data simulation (TODO: implement)
python scripts/simulate_market_data.py
```

### 3. Query Data

```python
from k2.query import QueryEngine

engine = QueryEngine()

# Query last 1 hour of BHP ticks
df = engine.query("""
    SELECT timestamp, symbol, price, volume
    FROM market_data.ticks
    WHERE symbol = 'BHP'
      AND timestamp >= NOW() - INTERVAL '1 hour'
    ORDER BY timestamp DESC
    LIMIT 1000
""")
```

---

## Development Workflow

### Project Structure

```
k2-market-data-platform/
├── src/k2/
│   ├── ingestion/           # Kafka producers, sequence tracking
│   │   └── sequence_tracker.py  # Gap detection, deduplication
│   ├── storage/             # Iceberg catalog, writers
│   ├── query/               # DuckDB engine, replay
│   ├── governance/          # RBAC, audit, encryption
│   └── common/              # Metrics, logging, config
├── docs/
│   ├── PLATFORM_PRINCIPLES.md      # Core design philosophy
│   ├── QUERY_ARCHITECTURE.md       # Query routing, caching, optimization
│   ├── MARKET_DATA_GUARANTEES.md   # Ordering, replay semantics
│   ├── DATA_CONSISTENCY.md         # End-to-end consistency model
│   ├── DATA_QUALITY.md             # Validation, anomaly detection
│   ├── DATA_SOURCE_ASSUMPTIONS.md  # Exchange feed characteristics
│   ├── LATENCY_BACKPRESSURE.md     # Performance budgets
│   ├── CORRECTNESS_TRADEOFFS.md    # Exactly-once vs idempotency
│   ├── FAILURE_RECOVERY.md         # Operational runbooks
│   ├── DISASTER_RECOVERY.md        # Backup, multi-region, DR drills
│   ├── OBSERVABILITY_DASHBOARDS.md # Grafana dashboards, alerts
│   ├── STORAGE_OPTIMIZATION.md     # Compaction, lifecycle, cost
│   ├── TESTING_STRATEGY.md         # Unit, integration, chaos tests
│   ├── VERSIONING_POLICY.md        # SemVer, deprecation, releases
│   ├── ALTERNATIVE_ARCHITECTURES.md # Lambda, Kappa comparison
│   └── RFC_TEMPLATE.md             # Platform evolution process
├── tests/
│   ├── unit/                # Fast, isolated tests
│   ├── integration/         # Requires Docker services
│   └── performance/         # Load tests, benchmarks
├── config/
│   ├── kafka/
│   ├── prometheus/
│   └── grafana/
└── scripts/
    ├── init_tables.py       # Iceberg schema creation
    └── simulate_market_data.py  # Market data generator
```

### Testing

```bash
# Unit tests (no Docker required)
pytest tests/unit/ -v

# Integration tests (requires services)
pytest tests/integration/ -v

# Performance benchmarks
pytest tests/performance/ --benchmark-only
```

### Code Quality

```bash
# Format
black src/ tests/
isort src/ tests/

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

---

## Platform Evolution

### RFC Process

All significant platform changes require an RFC (Request for Comments). See [**RFC Template**](./docs/RFC_TEMPLATE.md).

**Examples of RFC-Worthy Changes**:
- New storage format (Hudi, Delta Lake)
- Breaking schema changes
- Kafka topic partitioning strategy
- Cross-region replication

**Approval Requirements**: Platform Lead + 1 Staff Engineer (minimum)

**Recent RFCs**:
- _(Demo project - no historical RFCs)_

---

## Production Checklist

Before deploying to production, validate these requirements:

### Security
- [ ] Kafka TLS/SASL authentication enabled
- [ ] S3 server-side encryption (SSE-KMS)
- [ ] Secrets managed via Vault/AWS Secrets Manager
- [ ] Audit logging for all data access
- [ ] Row-level security for PII

### High Availability
- [ ] Kafka replication factor ≥ 3, `min.insync.replicas=2`
- [ ] Schema Registry cluster (3+ nodes)
- [ ] PostgreSQL streaming replication
- [ ] Multi-region replication (disaster recovery)

### Monitoring & Alerting
- [ ] Consumer lag > 1M messages (page on-call)
- [ ] Sequence gaps > 100 (critical data loss)
- [ ] Iceberg write p99 > 1 second (storage bottleneck)
- [ ] Circuit breaker state (system halted)

### Cost Optimization
- [ ] S3 lifecycle policies (Glacier after 90 days)
- [ ] Kafka retention policies (7-30 days)
- [ ] Iceberg compaction (merge small files)
- [ ] Query result caching (reduce compute)

---

## Documentation Index

### Core Architecture Documents

| Document | Purpose | Audience |
|----------|---------|----------|
| [Platform Principles](./docs/PLATFORM_PRINCIPLES.md) | Core design philosophy, guardrails | All engineers, new hires |
| [Query Architecture](./docs/QUERY_ARCHITECTURE.md) | Query routing, caching, performance optimization | Platform engineers, data engineers |
| [Market Data Guarantees](./docs/MARKET_DATA_GUARANTEES.md) | Ordering, sequencing, replay semantics | Stream processing engineers |
| [Data Consistency](./docs/DATA_CONSISTENCY.md) | End-to-end consistency model, cross-layer guarantees | All engineers |
| [Alternative Architectures](./docs/ALTERNATIVE_ARCHITECTURES.md) | Comparison of Lambda, Kappa, and other patterns | Architecture team, tech leads |

### Operational Excellence

| Document | Purpose | Audience |
|----------|---------|----------|
| [Latency & Backpressure](./docs/LATENCY_BACKPRESSURE.md) | Performance budgets, degradation cascade | SRE, platform team |
| [Failure & Recovery](./docs/FAILURE_RECOVERY.md) | Incident response runbooks | On-call engineers, SRE |
| [Disaster Recovery](./docs/DISASTER_RECOVERY.md) | Backup strategy, multi-region failover, DR drills | SRE, platform lead |
| [Observability Dashboards](./docs/OBSERVABILITY_DASHBOARDS.md) | Grafana dashboards, Prometheus alerts | SRE, on-call engineers |

### Data Quality & Governance

| Document | Purpose | Audience |
|----------|---------|----------|
| [Data Quality](./docs/DATA_QUALITY.md) | Validation framework, anomaly detection, quarantine | Data engineers, platform team |
| [Data Source Assumptions](./docs/DATA_SOURCE_ASSUMPTIONS.md) | Exchange feed characteristics, vendor adaptations | Integration engineers |
| [Correctness Trade-offs](./docs/CORRECTNESS_TRADEOFFS.md) | Exactly-once vs at-least-once delivery | All engineers |

### Development & Operations

| Document | Purpose | Audience |
|----------|---------|----------|
| [Testing Strategy](./docs/TESTING_STRATEGY.md) | Unit, integration, performance, chaos tests | All engineers, QA |
| [Storage Optimization](./docs/STORAGE_OPTIMIZATION.md) | Compaction, lifecycle policies, cost optimization | Data engineers, FinOps |
| [Versioning Policy](./docs/VERSIONING_POLICY.md) | SemVer, deprecation process, upgrade procedures | All engineers |
| [RFC Template](./docs/RFC_TEMPLATE.md) | Platform change proposals | Platform lead, staff engineers |

---

## Roadmap

### Phase 1: Core Platform (Current)
- [ ] Kafka + Schema Registry setup
- [ ] Iceberg lakehouse with ACID
- [ ] Sequence tracking and gap detection
- [ ] DuckDB query engine implementation
- [ ] Replay engine (cold start, catch-up, rewind)
- [ ] Observability Dashboards

### Phase 2: Advanced Processing
- [ ] Real-time aggregations (OHLCV windows)
- [ ] Order book reconstruction
- [ ] Market microstructure metrics
- [ ] Autoscaling based on consumer lag

### Phase 3: Multi-Region
- [ ] Active-active replication across regions
- [ ] Geo-partitioning for data sovereignty
- [ ] Cross-region disaster recovery (RTO < 1 minute)

---

## Contact & Contribution

**Author**: Rob Scott  
**Role**: Platform Lead / Senior Data Engineer  
**LinkedIn**: https://www.linkedin.com/in/rjdscott/  

---

## License

MIT License - see LICENSE file for details.

---

**Built to demonstrate**: distributed systems design, streaming data architecture, and operational reliability
