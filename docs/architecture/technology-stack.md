# Technology Stack - Detailed Rationale

**Last Updated**: 2026-01-14
**Stability**: High - changes require RFC and technical review
**Target Audience**: Principal/Staff Engineers, Architects

---

## Overview

This document provides comprehensive rationale for all technology choices in the K2 Market Data Platform, including trade-offs, alternatives considered, and clear guidance on when to replace each component.

**Philosophy**: "Boring Technology" - Proven at scale, well-documented, large community, avoid bleeding-edge

---

## Technology Selection Matrix

| Layer | Technology | Version | Status | Replacement Threshold |
|-------|------------|---------|--------|----------------------|
| **Streaming** | Apache Kafka | 3.7 (KRaft) | Permanent | Never (industry standard) |
| **Schema** | Confluent Schema Registry | 7.6 | Long-term | When migrating away from Avro |
| **Storage** | Apache Iceberg | 1.4 | Long-term | Rarely (specific format needs) |
| **Query** | DuckDB | 0.10 | **Phase 1-3** | **>10TB or >100 users** |
| **API** | FastAPI | Latest | Long-term | When Python perf inadequate |
| **Serialization** | Apache Avro | 1.11 | Long-term | When schema evolution model changes |
| **Observability** | Prometheus + Grafana | Latest | Long-term | When managed preferred (Datadog) |
| **Orchestration** | Docker Compose | 2.x | Phase 1 | Phase 2 (Kubernetes) |
| **Language** | Python 3.12 | 3.12+ | Permanent | Rarely (performance-critical paths) |

---

## Detailed Technology Decisions

### 1. Streaming: Apache Kafka 3.7 (KRaft)

#### Why Chosen
- **Industry Standard**: De facto standard for streaming data platforms
- **KRaft Mode**: No ZooKeeper dependency, sub-1s failover, simpler operations
- **Proven at Scale**: Used by LinkedIn, Netflix, Uber for trillions of messages/day
- **Ecosystem**: Native integrations with Schema Registry, Iceberg, monitoring tools
- **Replayability**: Topic retention enables time-travel and backfill scenarios

#### Alternatives Considered

**Apache Pulsar**:
- ✅ Better multi-tenancy, geo-replication
- ❌ Smaller community, less tooling, operational complexity
- **Why rejected**: Kafka's maturity and ecosystem outweigh Pulsar's advantages for our use case

**AWS Kinesis**:
- ✅ Fully managed, auto-scaling
- ❌ Vendor lock-in, higher cost, limited retention (7 days), no time-travel
- **Why rejected**: Replayability and long retention (90+ days) are critical requirements

**RabbitMQ**:
- ✅ Simpler for traditional messaging
- ❌ Not designed for streaming analytics, poor log retention, limited throughput
- **Why rejected**: Wrong tool for high-throughput streaming data platform

#### Trade-offs

**Advantages**:
- Proven reliability and performance (millions of messages/sec)
- Rich ecosystem (Kafka Connect, Kafka Streams, ksqlDB)
- Strong ordering guarantees within partition
- Horizontal scalability (add brokers + partitions)
- Native Schema Registry integration

**Disadvantages**:
- Operational complexity (requires expertise)
- No built-in exactly-once across topics (we use at-least-once)
- Partition rebalancing can cause temporary lag
- Disk I/O bound (requires SSD for optimal performance)

#### When to Replace

**Never** - Kafka is the industry standard and suitable for all scales.

If considering replacement, validate these edge cases:
- Multi-region active-active replication at massive scale (consider Kafka MirrorMaker 2 or Pulsar)
- Need for exactly-once semantics across multiple topics (consider Kafka Streams with transactions)
- Extremely low latency (<1ms) required (consider Apache Flink + Kafka)

#### Configuration Guidance

**Current Setup (Phase 1-2)**:
```yaml
# Single-node development
brokers: 1
partitions_per_topic: 3
replication_factor: 1
retention: 90 days
```

**Production Setup (Phase 3+)**:
```yaml
# Multi-broker cluster
brokers: 3-5
partitions_per_topic: 6-12 (2x brokers for headroom)
replication_factor: 3
retention: 90-365 days
compression: zstd
```

**Scaling Triggers**:
- CPU >70% sustained → Add brokers
- Lag >10,000 messages → Add partitions and consumers
- Disk >80% → Reduce retention or add storage

---

### 2. Schema: Confluent Schema Registry 7.6

#### Why Chosen
- **Schema Evolution**: BACKWARD, FORWARD, FULL compatibility modes
- **Avro Native**: First-class Avro support with efficient serialization
- **API-Driven**: REST API for schema management and queries
- **Confluent Ecosystem**: Integrates with Kafka, Kafka Connect, ksqlDB
- **Version Control**: Immutable schema versions with history

#### Alternatives Considered

**AWS Glue Schema Registry**:
- ✅ Managed service, auto-discovery
- ❌ AWS lock-in, limited to Avro/JSON/Protobuf, less mature
- **Why rejected**: Confluent Schema Registry is more feature-rich and provider-agnostic

**Apache Hive Metastore**:
- ✅ Used for Iceberg table metadata
- ❌ Not designed for streaming schema evolution, heavyweight
- **Why rejected**: Schema Registry is purpose-built for streaming data

**Custom Schema Store (DynamoDB/PostgreSQL)**:
- ✅ Full control, simpler deployment
- ❌ No compatibility checks, no versioning logic, reinventing the wheel
- **Why rejected**: Schema evolution is complex; use proven tool

#### Trade-offs

**Advantages**:
- Automatic compatibility validation (prevents bad deploys)
- Schema ID embedding in messages (efficient, compact)
- RESTful API for automation
- Schema evolution without breaking consumers

**Disadvantages**:
- Single point of failure (mitigated by HA setup)
- Adds network hop for schema lookups (mitigated by client-side caching)
- Confluent-specific extensions (but open-source compatible)

#### When to Replace

**When migrating away from Avro** (e.g., to Protobuf or JSON Schema):
- Schema Registry supports multiple formats, but if moving to a format it doesn't support well
- Consider AWS Glue Schema Registry (if on AWS) or custom solution

**When simplicity > schema governance**:
- If schema evolution becomes unnecessary (highly unlikely for market data)
- Could use schema-less formats (NOT RECOMMENDED for financial data)

#### Configuration Guidance

**Current Setup**:
```yaml
compatibility_level: BACKWARD  # New schemas can read old data
schema_cache_ttl: 300s         # 5 min cache
schema_store: Kafka topics (_schemas)
```

**Recommended Alerts**:
- Schema Registry unavailable >1 min (CRITICAL)
- Schema compatibility check failures >5/min (HIGH)
- Schema cache hit rate <90% (WARNING)

---

### 3. Storage: Apache Iceberg 1.4

#### Why Chosen
- **ACID Transactions**: Atomic commits, no partial writes
- **Time-Travel**: Query historical data as-of any timestamp (regulatory requirement)
- **Schema Evolution**: Add/drop columns without rewriting data
- **Partition Evolution**: Change partitioning without data migration
- **Hidden Partitioning**: Users query by `trade_date`, Iceberg handles partitions automatically
- **Open Format**: Not tied to vendor (works with Spark, Trino, DuckDB, Presto)

#### Alternatives Considered

**Delta Lake**:
- ✅ Similar ACID guarantees, mature ecosystem
- ❌ Databricks-centric (though open-source), less separation from Spark
- **Why rejected**: Iceberg has better multi-engine support (DuckDB, Trino, Dremio)

**Apache Hudi**:
- ✅ Great for CDC and upsert-heavy workloads
- ❌ More complex, tightly coupled to Spark, less mature
- **Why rejected**: K2 is append-only; Iceberg's simplicity is better fit

**Parquet Files + Hive Metastore**:
- ✅ Simple, no dependencies
- ❌ No ACID, manual partition management, no time-travel
- **Why rejected**: ACID and time-travel are regulatory requirements

**Snowflake / BigQuery**:
- ✅ Fully managed, excellent query performance
- ❌ Vendor lock-in, high cost, data egress fees, can't run locally
- **Why rejected**: Need to run locally for development and avoid vendor lock-in

#### Trade-offs

**Advantages**:
- ACID guarantees (critical for financial data)
- Time-travel queries (compliance, backtesting, debugging)
- Schema evolution without pain
- Multi-engine support (not locked to Spark)
- Hidden partitioning (users don't manage partitions)

**Disadvantages**:
- More complex than raw Parquet files
- Requires metadata management (catalog)
- Compaction needed for small files (scheduled maintenance)
- Learning curve for operators

#### When to Replace

**Rarely** - Iceberg is the leading open lakehouse format.

Consider alternatives only if:
- **Streaming upserts/deletes dominate** (>50% of workload) → Apache Hudi
- **Vendor lock-in acceptable for simplicity** → Snowflake, BigQuery
- **Need Databricks-specific features** → Delta Lake
- **Append-only with zero evolution** (unlikely) → Raw Parquet

#### Configuration Guidance

**Current Setup**:
```python
# Partitioning strategy
partition_by = ["trade_date"]  # Daily partitions
format = "parquet"
compression = "zstd"

# Compaction settings (future)
target_file_size = "512 MB"
compact_threshold = "128 MB"
```

**Recommended Maintenance**:
- **Daily**: Snapshot expiration (keep 90 days)
- **Weekly**: Small file compaction (combine files <128MB)
- **Monthly**: Metadata cleanup (old manifests)

---

### 4. Query Engine: DuckDB 0.10 → Presto (Migration Planned)

#### Why Chosen (DuckDB)
- **Zero-Ops**: Embedded database, no cluster to manage
- **Fast**: Columnar execution, vectorized queries
- **Iceberg Native**: First-class Iceberg support
- **Connection Pool**: Thread-safe for 5-50 concurrent queries
- **Development**: Perfect for local development and testing

#### Alternatives Considered

**Presto/Trino**:
- ✅ Distributed queries, scales to petabytes, 1000s of concurrent users
- ❌ Operational complexity, requires cluster, slower for small datasets
- **When to use**: **Phase 3+ when dataset >10TB or users >100**

**Apache Spark**:
- ✅ Distributed, mature, handles batch + streaming
- ❌ Heavy JVM, slower for interactive queries, complex setup
- **Why rejected**: Overkill for our query patterns (mostly ad-hoc analytics)

**AWS Athena**:
- ✅ Serverless, pay-per-query, no infrastructure
- ❌ Vendor lock-in, cold start latency, limited control, cost at scale
- **Why rejected**: Need local development environment and cost predictability

**PostgreSQL + Foreign Data Wrapper**:
- ✅ Familiar SQL, transactional
- ❌ Not designed for columnar analytics, slow for large scans
- **Why rejected**: Wrong tool for OLAP workloads

#### Trade-offs

**DuckDB Advantages**:
- Extremely fast for single-node queries (<1 sec for 100M rows)
- Zero operational overhead
- Perfect for development and testing
- Connection pool enables concurrency (5-50 queries)

**DuckDB Disadvantages**:
- **Single-node only** (scales vertically, not horizontally)
- **Limited to ~10TB dataset** (depends on RAM and disk speed)
- **~100 concurrent users max** (connection pool limitation)
- No built-in caching layer (yet)

**Presto Advantages** (Future):
- Distributed queries (scales to petabytes)
- Handles 1000s of concurrent users
- Built-in query federation (multiple data sources)
- Mature caching and optimization

**Presto Disadvantages**:
- Operational complexity (cluster management)
- Slower for small datasets (<100MB)
- Requires dedicated infrastructure

#### When to Replace DuckDB

**Replace with Presto when ANY of these conditions met**:
1. **Dataset >10TB** (DuckDB query times degrade)
2. **Concurrent users >100** (connection pool exhausted)
3. **Need query federation** (joining multiple data sources)
4. **95th percentile query time >5 seconds** (user experience degrades)

**Migration Strategy** (Phase 3+):
1. Deploy Presto cluster (3 workers initially)
2. Run Presto + DuckDB in parallel (shadow queries)
3. Compare performance and correctness for 2 weeks
4. Migrate production traffic to Presto
5. Keep DuckDB for development/testing

**Current Status**: Phase 1-2 uses DuckDB (suitable for demo and initial production <10TB)

#### Configuration Guidance

**DuckDB Current Setup**:
```python
# Connection pool
min_connections: 5
max_connections: 50
connection_timeout: 30s

# Query settings
memory_limit: 16GB (per connection)
threads: 4 (per query)
temp_directory: /tmp/duckdb
```

**Presto Future Setup** (Phase 3):
```yaml
# Cluster
coordinator: 1
workers: 3-10 (scale based on load)
memory_per_node: 64GB
query_max_memory: 50GB

# Caching
hive_metastore_cache: enabled
orc_file_tail_cache: enabled
```

---

### 5. API Framework: FastAPI

#### Why Chosen
- **Async Python**: Native `async/await` for high concurrency
- **Type Safety**: Pydantic models with automatic validation
- **Auto-Documentation**: OpenAPI (Swagger) auto-generated
- **Performance**: Comparable to Node.js, Go for I/O-bound workloads
- **Ecosystem**: Excellent Python data science integration (Pandas, NumPy)

#### Alternatives Considered

**Django REST Framework (DRF)**:
- ✅ Mature, full-featured, ORM integration
- ❌ Synchronous (blocking), heavier, slower for our use case
- **Why rejected**: K2 API is primarily read-only analytics; FastAPI's async is better fit

**Flask + Flask-RESTful**:
- ✅ Lightweight, flexible
- ❌ No native async, manual validation, no auto-docs
- **Why rejected**: FastAPI provides better developer experience with less boilerplate

**Go (net/http or Fiber)**:
- ✅ Excellent performance, compiled binary
- ❌ Separate from data science stack, less flexible for rapid iteration
- **Why rejected**: Python ecosystem integration more valuable than marginal performance gain

**Node.js (Express or Fastify)**:
- ✅ Excellent async performance, large ecosystem
- ❌ Weaker data science integration, TypeScript overhead, less familiar to team
- **Why rejected**: Python's data science ecosystem is critical for K2

#### Trade-offs

**Advantages**:
- High performance for I/O-bound workloads (API queries)
- Type safety catches errors at development time
- Auto-generated OpenAPI docs (Swagger UI)
- Async enables efficient resource usage (1000s of connections)
- Python ecosystem integration (DuckDB, Pandas, Avro)

**Disadvantages**:
- Python GIL limits CPU-bound performance (not an issue for our I/O-bound workload)
- Smaller community than Flask/Django (but growing rapidly)
- Async complexity if not designed carefully (coroutine leaks)

#### When to Replace

**When Python performance is inadequate**:
- If API becomes CPU-bound (complex in-memory computation)
- If latency requirements drop below 10ms (consider Go or Rust)
- If binary deployment preferred (consider Go with embedded DuckDB)

**Realistically**: Never for K2 (API is I/O-bound, Python ecosystem is critical)

#### Configuration Guidance

**Current Setup**:
```python
# API server
workers: 4 (Uvicorn)
max_connections: 1000
timeout: 30s

# Connection pool (DuckDB)
pool_size: 20
max_overflow: 30
```

---

### 6. Serialization: Apache Avro 1.11

#### Why Chosen
- **Schema Evolution**: Add/remove fields with compatibility checks
- **Compact**: Binary format, ~2-3x smaller than JSON
- **Self-Describing**: Schema embedded in file header
- **Hadoop Ecosystem**: Native support in Kafka, Iceberg, Spark, Parquet

#### Alternatives Considered

**Protocol Buffers (Protobuf)**:
- ✅ More compact, faster serialization, better for RPC
- ❌ Less Hadoop ecosystem support, requires .proto compilation
- **Why rejected**: Avro's Hadoop integration is more valuable

**JSON**:
- ✅ Human-readable, universal support
- ❌ 3-5x larger, no schema evolution, slower parsing
- **Why rejected**: Storage cost and schema governance requirements

**Apache Arrow**:
- ✅ Zero-copy reads, columnar in-memory
- ❌ Not designed for storage (designed for in-memory transport)
- **Why rejected**: Avro for storage, Arrow for query results (future)

#### Trade-offs

**Advantages**:
- Compact binary format
- Schema evolution with compatibility enforcement
- Excellent Kafka + Iceberg integration

**Disadvantages**:
- Not human-readable (need tools to inspect)
- Schema Registry dependency

#### When to Replace

**When migrating serialization format**:
- If moving to Protobuf for better RPC performance (unlikely)
- If JSON required for external API consumers (use JSON for API, Avro for internal storage)

**Realistically**: Never - Avro is the standard for Kafka + Hadoop ecosystem

---

### 7. Observability: Prometheus + Grafana

#### Why Chosen
- **Open-Source**: No vendor lock-in, self-hosted
- **Pull-Based**: Prometheus scrapes metrics (resilient to target failures)
- **Powerful Query Language**: PromQL for complex aggregations
- **Grafana**: Industry-standard dashboarding

#### Alternatives Considered

**Datadog**:
- ✅ Fully managed, excellent UX, APM + logs + metrics
- ❌ High cost at scale ($15-20/host/month), vendor lock-in
- **When to use**: When operational simplicity > cost

**AWS CloudWatch**:
- ✅ Native AWS integration, pay-per-use
- ❌ Limited retention (15 days), expensive queries, AWS lock-in
- **Why rejected**: Need longer retention and local development environment

**ELK Stack (Elasticsearch + Kibana)**:
- ✅ Powerful for logs, good visualization
- ❌ Heavier, more operational complexity, not optimized for time-series metrics
- **Why rejected**: Prometheus is purpose-built for metrics

#### Trade-offs

**Advantages**:
- Open-source and provider-agnostic
- Excellent performance for time-series metrics
- Large community and ecosystem (exporters for everything)
- Self-hosted (full control)

**Disadvantages**:
- Operational complexity (HA setup, storage management)
- Limited long-term storage (need Thanos/Cortex for >1 year)
- No native APM or distributed tracing (need Jaeger/Tempo)

#### When to Replace

**When managed observability preferred**:
- If operational overhead > cost savings → **Datadog** (recommended if budget allows)
- If on AWS and simplicity desired → **CloudWatch** (for basic metrics only)

**When distributed tracing needed**:
- Add Jaeger or Grafana Tempo (complementary, not replacement)

#### Configuration Guidance

**Current Setup**:
```yaml
# Prometheus
scrape_interval: 15s
retention: 30 days
storage: local disk

# Grafana
dashboards: 5 (Kafka, Iceberg, API, DuckDB, System)
alerting: via Alertmanager (PagerDuty integration)
```

---

### 8. Orchestration: Docker Compose (Phase 1) → Kubernetes (Phase 2+)

#### Why Chosen (Docker Compose)
- **Development Simplicity**: Single `docker-compose up` command
- **Local Testing**: Runs on laptop (Docker Desktop)
- **No Ops Overhead**: No cluster management
- **Perfect for Phase 1**: Single-node demo deployment

#### Alternatives Considered

**Kubernetes**:
- ✅ Production-grade, auto-scaling, self-healing
- ❌ Overkill for Phase 1, operational complexity, slower iteration
- **When to use**: **Phase 2+ for production deployment**

**Docker Swarm**:
- ✅ Simpler than Kubernetes
- ❌ Smaller ecosystem, less mature, not industry standard
- **Why rejected**: If moving to orchestration, go to Kubernetes (standard)

**ECS (AWS)**:
- ✅ Simpler than Kubernetes on AWS
- ❌ AWS lock-in, limited control
- **Why rejected**: Want local development environment

#### Trade-offs

**Docker Compose Advantages**:
- Extremely simple (no cluster)
- Fast iteration (restart in seconds)
- Perfect for development and single-node deployment

**Docker Compose Disadvantages**:
- **No HA** (single point of failure)
- **No auto-scaling** (manual scaling only)
- **No self-healing** (manual restart on failure)

#### When to Replace

**Replace with Kubernetes when**:
1. **Multi-node deployment** required (Phase 2+)
2. **Auto-scaling** needed (dynamic load)
3. **High availability** required (>99% uptime SLA)

**Migration Path** (Phase 2):
1. Convert `docker-compose.yml` to Kubernetes manifests (Helm chart)
2. Deploy to local Kubernetes (Minikube or Kind) for testing
3. Deploy to cloud Kubernetes (EKS, GKE, AKS)

---

### 9. Language: Python 3.12+

#### Why Chosen
- **Data Science Ecosystem**: Pandas, NumPy, DuckDB, PyArrow
- **Async Support**: Native `async/await` since 3.5
- **Type Hints**: Static type checking with `mypy`
- **Community**: Massive community for financial and data engineering

#### Alternatives Considered

**Java/Scala**:
- ✅ Better performance, JVM ecosystem (Spark, Kafka)
- ❌ Slower iteration, more boilerplate, weaker data science tools
- **Why rejected**: Python's data science ecosystem more valuable

**Go**:
- ✅ Excellent performance, simple concurrency, compiled binary
- ❌ Weaker data science ecosystem, less flexibility for rapid iteration
- **Why rejected**: Performance not critical for our workload (I/O-bound)

**Rust**:
- ✅ Best performance, memory safety
- ❌ Steeper learning curve, smaller ecosystem, slower iteration
- **Why rejected**: Overkill for our use case (Python performance is adequate)

#### Trade-offs

**Advantages**:
- Rich data science ecosystem (Pandas, DuckDB, PyArrow)
- Fast iteration (interpreted language)
- Excellent libraries for Kafka, Avro, Iceberg

**Disadvantages**:
- GIL limits CPU-bound parallelism (not an issue for I/O-bound workload)
- Slower than compiled languages (acceptable for our latency requirements)

#### When to Replace

**Performance-critical paths**:
- If CPU-bound computation dominates (unlikely)
- Consider **Rust or Go for specific services** (not full rewrite)

**Realistically**: Never fully replace Python (ecosystem too valuable)

---

## Migration Roadmap

### Phase 1-2: Current Stack (Single-Node)
- Docker Compose orchestration
- DuckDB query engine
- Local development on laptop
- **Target**: <10TB dataset, <100 concurrent users

### Phase 3: Production Deployment
- **Replace Docker Compose → Kubernetes**
- Keep DuckDB (still suitable for <10TB)
- Deploy to AWS/GCP
- Add authentication (OAuth2 + JWT)

### Phase 4: Scale
- **Replace DuckDB → Presto cluster** (when dataset >10TB)
- Add distributed caching (Redis)
- Multi-region replication (Kafka MirrorMaker 2)
- **Target**: 10-100TB dataset, 100-1000 concurrent users

### Phase 5: Enterprise
- Consider **managed observability** (Datadog) if ops overhead high
- Advanced features (distributed tracing, APM)
- Multi-region active-active
- **Target**: >100TB dataset, 1000+ concurrent users

---

## Summary: When to Replace Each Component

| Component | Replace When | Replace With |
|-----------|--------------|--------------|
| **Kafka** | Never (industry standard) | N/A |
| **Schema Registry** | Migrating away from Avro | AWS Glue Schema Registry (if on AWS) |
| **Iceberg** | Rarely (specific needs) | Delta Lake (Databricks), Hudi (CDC-heavy) |
| **DuckDB** | **>10TB or >100 users** | **Presto/Trino cluster** |
| **FastAPI** | Python performance inadequate | Go, Rust (unlikely) |
| **Avro** | Migrating serialization | Protobuf (RPC), JSON (external API) |
| **Prometheus + Grafana** | Ops overhead > cost | Datadog (recommended if budget allows) |
| **Docker Compose** | Multi-node or HA needed | **Kubernetes (Phase 2+)** |
| **Python** | CPU-bound bottlenecks | Rust/Go for specific services only |

---

## Related Documentation

- [Platform Principles](./platform-principles.md) - Why we chose "boring technology"
- [Phase 1 DECISIONS.md](../phases/phase-1-single-node-equities/DECISIONS.md) - Implementation ADRs
- [Cost Model](../operations/cost-model.md) - Cost implications of technology choices
- [Alternative Architectures](./alternatives.md) - Rejected architectures

---

**Maintained By**: Engineering Team
**Review Frequency**: Quarterly (or when major technology change proposed)
**Last Review**: 2026-01-14
**Next Review**: 2026-04-14
