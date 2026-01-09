# K2 Market Data Platform

A production-grade real-time market data platform demonstrating distributed systems architecture, streaming data processing, and lakehouse storage patterns suitable for high-frequency trading environments.

## Project Goals

This platform showcases senior data engineering capabilities through:

1. **Real-time Streaming Architecture**: Kafka-based ingestion handling high-frequency market ticks
2. **Lakehouse Storage**: Apache Iceberg on S3-compatible storage for ACID guarantees and time-travel
3. **Data Governance**: Schema evolution, RBAC, and audit logging
4. **Scalability**: Designed to scale from local development to petabyte-scale production
5. **Observability**: Comprehensive metrics, logging, and tracing

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Data Producers                               │
│  Market Data Feeds, Trading Venues, Simulation Generators            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Ingestion Layer                                 │
│  ┌──────────────┐    ┌─────────────────┐                           │
│  │    Kafka     │◄───│ Schema Registry │  (Schema Evolution)        │
│  │   (KRaft)    │    │   (Avro/JSON)   │                           │
│  └──────┬───────┘    └─────────────────┘                           │
│         │ Topics: market.ticks, market.trades, market.quotes        │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Processing Layer                                │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  Stream Processors (Kafka Consumers)                       │    │
│  │  • Real-time aggregation                                   │    │
│  │  • Validation & enrichment                                 │    │
│  │  • Deduplication                                           │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Storage Layer                                  │
│  ┌──────────────────┐        ┌────────────────┐                    │
│  │  Apache Iceberg  │◄───────│   PostgreSQL   │  (Catalog Metadata)│
│  │  (Table Format)  │        │   (ACID Store) │                    │
│  └────────┬─────────┘        └────────────────┘                    │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │  MinIO (S3 API)  │  (Parquet files, partition metadata)         │
│  │  Object Storage  │                                               │
│  └──────────────────┘                                               │
│  • Time-partitioned (day/hour)                                      │
│  • Columnar format (Parquet + Zstd compression)                     │
│  • Snapshot isolation for time-travel queries                       │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Query Layer                                   │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  Query API (FastAPI/GraphQL)                               │    │
│  │  • Real-time: Direct Kafka consumer                        │    │
│  │  • Historical: DuckDB + Iceberg connector                  │    │
│  │  • Hybrid: Merge real-time + historical views              │    │
│  └────────────────────────────────────────────────────────────┘    │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  Governance Layer                                          │    │
│  │  • RBAC (Role-Based Access Control)                        │    │
│  │  • Row-level security                                      │    │
│  │  • Audit logging                                           │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Observability Layer                               │
│  ┌───────────┐  ┌─────────┐  ┌──────────────────────┐             │
│  │Prometheus │──│ Grafana │  │ Structured Logging   │             │
│  │ (Metrics) │  │  (Viz)  │  │ (JSON w/ correlation)│             │
│  └───────────┘  └─────────┘  └──────────────────────┘             │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. Kafka with KRaft Mode
**Decision**: Use Kafka in KRaft mode (no Zookeeper)
**Rationale**:
- Simplified operational complexity
- Improved metadata scalability (critical for 1000s of partitions)
- Faster controller failover (< 1 second vs ~30 seconds with ZK)
- Industry direction (Zookeeper deprecated in Kafka 3.x+)

**Scaling**: KRaft supports millions of partitions across a cluster, enabling horizontal scalability for market data streams from multiple venues.

### 2. Apache Iceberg for Storage
**Decision**: Iceberg table format instead of raw Parquet or Delta Lake
**Rationale**:
- **ACID guarantees**: Critical for financial data consistency
- **Schema evolution**: Add/remove fields without breaking queries
- **Time-travel**: Query data as of specific snapshots (regulatory compliance)
- **Partition evolution**: Change partitioning without rewriting data
- **Hidden partitioning**: Consumers don't need to know partition layout
- **Snapshot isolation**: Concurrent reads/writes without locking

**Scaling**: Iceberg metadata layer scales independently from data. At 1000x scale:
- Supports petabyte+ datasets
- Partition pruning keeps query latency low (scan only relevant files)
- Metadata caching prevents thundering herd on catalog

### 3. DuckDB for Query Engine
**Decision**: DuckDB instead of Spark/Presto for historical queries
**Rationale**:
- **Columnar execution**: Vectorized processing for analytical queries
- **Zero-copy S3 reads**: Direct parquet scanning without staging
- **Embedded deployment**: No cluster management overhead
- **Iceberg integration**: Native support for reading Iceberg tables
- **Cost-effective**: Runs in-process, no separate compute cluster

**Scaling**: For 100x-1000x scale, would add:
- Presto/Trino for distributed queries across petabytes
- DuckDB remains excellent for single-node aggregations and development

### 4. MinIO vs Cloud Object Storage
**Decision**: MinIO for local development, S3-compatible API for cloud
**Rationale**:
- **API compatibility**: Same code works with S3, GCS, Azure
- **Local development**: No cloud costs during development
- **Performance**: MinIO provides high throughput for analytical reads
- **Migration path**: Swap endpoint to move to cloud storage

**Scaling**: In production:
- AWS S3: Infinite scalability, 99.999999999% durability
- MinIO distributed mode: Multi-node deployment for on-prem

### 5. Backward Compatibility for Schemas
**Decision**: Enforce BACKWARD compatibility in Schema Registry
**Rationale**:
- **Consumer safety**: Downstream analytics never break on schema changes
- **Additive changes**: Can add optional fields without redeploying consumers
- **Versioning**: Automatic schema version management
- **Financial data**: Cannot afford consumer failures in production

**Trade-off**: Producers must be careful with schema changes (can't remove required fields).

## Technology Stack

| Component | Technology | Purpose | Production Alternative |
|-----------|-----------|---------|----------------------|
| **Streaming** | Kafka (KRaft) | Message broker for real-time ingestion | Confluent Cloud, Amazon MSK |
| **Schema Management** | Confluent Schema Registry | Centralized schema evolution & governance | Confluent Cloud, AWS Glue Schema Registry |
| **Object Storage** | MinIO | S3-compatible storage for Iceberg | AWS S3, GCS, Azure Blob Storage |
| **Table Format** | Apache Iceberg | ACID lakehouse with time-travel | Same (open standard) |
| **Catalog** | PostgreSQL + Iceberg REST | Metadata management | AWS Glue Catalog, Tabular, Nessie |
| **Query Engine** | DuckDB | In-process analytical queries | Presto, Trino, Athena (for distributed) |
| **Observability** | Prometheus + Grafana | Metrics collection & visualization | Datadog, New Relic, Grafana Cloud |
| **Language** | Python 3.11+ | Application development | Same, with Rust for hot paths |

## Scaling Considerations

### Current Setup (Local Development)
- **Throughput**: ~10K messages/sec
- **Storage**: GBs of market data
- **Latency**: p99 < 100ms
- **Query**: Single-node DuckDB

### 100x Scale (Regional Production)
- **Throughput**: 1M messages/sec
  - **Kafka**: 3-5 brokers, 50+ partitions per topic
  - **Consumers**: Horizontal scaling with consumer groups
- **Storage**: TBs of market data
  - **MinIO**: Distributed mode (4+ nodes) OR migrate to S3
  - **Iceberg**: Partition by day + symbol, compaction jobs
- **Latency**: p99 < 50ms
  - **SSD-backed storage**: Reduce I/O latency
  - **Read replicas**: Scale query workload
- **Query**: DuckDB for single-day queries, add Presto for multi-day

### 1000x Scale (Global Production)
- **Throughput**: 10M+ messages/sec
  - **Kafka**: 10+ brokers, 500+ partitions, multi-region replication
  - **Schema Registry**: HA cluster (3+ nodes)
- **Storage**: Petabytes of market data
  - **S3**: Infinite scalability, lifecycle policies for cold storage
  - **Iceberg**: Hourly partitions, Z-ordering on symbol for cache locality
  - **Compaction**: Scheduled jobs to merge small files
- **Latency**: p99 < 20ms
  - **Edge caching**: CDN for frequently accessed historical data
  - **Materialized views**: Pre-aggregated OHLCV (Open/High/Low/Close/Volume)
- **Query**:
  - **Presto/Trino**: Distributed SQL across 100+ nodes
  - **DuckDB**: Point queries and development
  - **Caching**: Redis for hot queries (last 24h of ticks)

### Bottleneck Analysis

| Bottleneck | Symptom | Mitigation |
|------------|---------|-----------|
| **Kafka Throughput** | Producer lag, timeout errors | Add brokers, increase partitions, tune batch size |
| **Consumer Lag** | Growing offset lag | Scale consumer group, optimize processing logic |
| **S3 Read Latency** | Slow historical queries | Partition pruning, file compaction, caching layer |
| **Catalog Metadata** | Slow table scans, snapshot listing | Postgres read replicas, metadata caching, index tuning |
| **Query Concurrency** | Queue buildup, timeout errors | Connection pooling, query prioritization, autoscaling |
| **Network Bandwidth** | Cross-AZ transfer costs | Collocate compute and storage, use compression |

## Quick Start

### Prerequisites
- Docker Desktop (with at least 8GB RAM allocated)
- Python 3.11+
- Make (optional, for convenience commands)

### 1. Clone and Setup
```bash
git clone <repository-url>
cd k2-ingestion

# Copy environment file
cp .env.example .env

# (Optional) Edit .env with your preferences
nano .env
```

### 2. Start Infrastructure
```bash
# Start all services
docker-compose up -d

# Check service health
docker-compose ps

# View logs
docker-compose logs -f kafka
```

### 3. Verify Services

| Service | URL | Credentials |
|---------|-----|-------------|
| Kafka UI | http://localhost:8080 | N/A |
| Schema Registry | http://localhost:8081 | N/A |
| MinIO Console | http://localhost:9001 | admin / password123! |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | N/A |

### 4. Initialize Platform
```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Initialize Iceberg tables
python scripts/init_tables.py

# Start market data simulation
python scripts/simulate_market_data.py
```

### 5. Run Sample Query
```python
from k2_platform.query import QueryEngine

# Initialize query engine
engine = QueryEngine()

# Query last 1 hour of AAPL ticks
df = engine.query("""
    SELECT
        timestamp,
        symbol,
        price,
        volume
    FROM market_data.ticks
    WHERE symbol = 'AAPL'
      AND timestamp >= NOW() - INTERVAL '1 hour'
    ORDER BY timestamp DESC
    LIMIT 1000
""")

print(df.head())
```

## Development Workflow

### Project Structure
```
k2-ingestion/
├── src/k2_platform/
│   ├── ingestion/          # Kafka producers, stream processing
│   │   ├── producer.py     # Market data producer
│   │   ├── consumer.py     # Base consumer with error handling
│   │   └── processors/     # Stream processors (aggregation, etc.)
│   ├── storage/            # Iceberg table management
│   │   ├── catalog.py      # Catalog operations
│   │   ├── writer.py       # Batch writer with compaction
│   │   └── schema.py       # Schema definitions
│   ├── governance/         # Data governance layer
│   │   ├── rbac.py         # Role-based access control
│   │   ├── audit.py        # Audit logging
│   │   └── encryption.py   # Sensitive data handling
│   ├── query/              # Query engine
│   │   ├── engine.py       # DuckDB query execution
│   │   ├── api.py          # FastAPI REST endpoints
│   │   └── cache.py        # Query result caching
│   ├── common/             # Shared utilities
│   │   ├── config.py       # Configuration management
│   │   ├── logging.py      # Structured logging
│   │   └── metrics.py      # Prometheus metrics
│   └── observability/      # Monitoring
│       ├── health.py       # Health checks
│       └── tracing.py      # Distributed tracing
├── tests/
│   ├── unit/               # Unit tests (fast, isolated)
│   ├── integration/        # Integration tests (with docker)
│   └── performance/        # Load tests, benchmarks
├── scripts/
│   ├── init_tables.py      # Initialize Iceberg schema
│   ├── simulate_market_data.py  # Market data generator
│   └── setup.sh            # Environment setup
├── config/
│   ├── kafka/
│   ├── prometheus/
│   └── grafana/
└── docs/
    ├── architecture.md     # Deep dive on architecture
    ├── scaling_considerations.md
    └── api_reference.md
```

### Running Tests
```bash
# Unit tests (no Docker required)
pytest tests/unit/ -v

# Integration tests (requires Docker services)
pytest tests/integration/ -v

# Performance tests
pytest tests/performance/ -v --benchmark-only

# Coverage report
pytest --cov=src/k2_platform --cov-report=html
```

### Code Quality
```bash
# Format code
black src/ tests/
isort src/ tests/

# Lint
ruff check src/ tests/

# Type checking
mypy src/
```

## Production Considerations

### Security
- [ ] Rotate credentials regularly (90-day policy)
- [ ] Enable Kafka TLS/SASL authentication
- [ ] Encrypt data at rest (S3 SSE-KMS)
- [ ] Use AWS IAM roles instead of access keys
- [ ] Enable audit logging for all data access
- [ ] Implement row-level security for PII

### High Availability
- [ ] Run Kafka in HA mode (3+ brokers, RF=3)
- [ ] Deploy Schema Registry cluster (3+ nodes)
- [ ] Use PostgreSQL with streaming replication
- [ ] Configure Iceberg catalog with read replicas
- [ ] Multi-region replication for disaster recovery

### Monitoring & Alerting
- [ ] Set up alerts for consumer lag > 1 million
- [ ] Monitor Kafka broker disk utilization (> 70%)
- [ ] Track query latency p99 (> 1 second)
- [ ] Alert on schema compatibility failures
- [ ] Monitor Iceberg snapshot count (> 100)
- [ ] Set up on-call rotation for production issues

### Cost Optimization
- [ ] Lifecycle policies for S3 (move to Glacier after 90 days)
- [ ] Kafka retention policies (delete after 7 days)
- [ ] Iceberg compaction jobs (merge small files)
- [ ] Query result caching (reduce compute costs)
- [ ] Reserved instances for steady-state workload
- [ ] Spot instances for batch processing

### Data Quality
- [ ] Schema validation on ingestion
- [ ] Deduplication based on message ID
- [ ] Late-arriving data handling (out-of-order writes)
- [ ] Data completeness checks (expect X ticks/sec)
- [ ] Reconciliation with upstream source
- [ ] Automated data quality dashboards

## Future Enhancements

### Phase 2: Advanced Processing
- [ ] Real-time aggregations (OHLCV windows)
- [ ] Order book reconstruction from ticks
- [ ] Trade execution analytics
- [ ] Market microstructure metrics

### Phase 3: ML Integration
- [ ] Feature store integration (Feast, Tecton)
- [ ] Online feature serving for real-time predictions
- [ ] Model inference pipeline
- [ ] A/B testing framework for strategies

### Phase 4: Multi-Region
- [ ] Active-active replication across regions
- [ ] Geo-partitioning for data sovereignty
- [ ] Global query routing (read from nearest region)
- [ ] Cross-region disaster recovery (RTO < 1 minute)

## Contributing

This is a portfolio project, but feedback is welcome! Please open an issue to discuss:
- Architecture improvements
- Scaling recommendations
- Production war stories
- Code quality suggestions

## License

MIT License - see LICENSE file for details

## Contact

**Author**: [Your Name]
**Email**: [Your Email]
**LinkedIn**: [Your LinkedIn]
**Portfolio**: [Your Portfolio Site]

Built to demonstrate senior data engineering capabilities for high-frequency trading environments.
