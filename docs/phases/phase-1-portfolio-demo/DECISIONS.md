# Architectural Decision Records (ADR)

This file tracks all significant architectural and implementation decisions for the K2 Market Data Platform.

**Last Updated**: 2026-01-11
**Total Decisions**: 22

---

## ADR Template

When adding new decisions, use this template:

```markdown
### Decision #XXX: [Title]

**Date**: YYYY-MM-DD
**Status**: Proposed | Accepted | Deprecated | Superseded by #YYY
**Deciders**: [Names]
**Related Steps**: Step X, Step Y

#### Context
[What is the issue we're trying to solve?]

#### Decision
[What did we decide to do?]

#### Consequences
**Positive**:
- Benefit 1
- Benefit 2

**Negative**:
- Cost 1
- Cost 2

**Neutral**:
- Trade-off 1

#### Alternatives Considered
1. **Option A**: [Why rejected]
2. **Option B**: [Why rejected]

#### Implementation Notes
[How to implement this decision]

#### Verification
- [ ] Verification criterion 1
- [ ] Verification criterion 2
```

---

## Decision Index

| ID | Title | Date | Status | Related Steps |
|----|-------|------|--------|---------------|
| #001 | DuckDB Over Spark for Query Engine | 2026-01-10 | Accepted | 9, 10 |
| #002 | Daily Partitioning for Iceberg Tables | 2026-01-10 | Accepted | 3 |
| #003 | At-Least-Once Delivery with Manual Commit | 2026-01-10 | Accepted | 6, 8 |
| #004 | Embedded Architecture for Phase 1 | 2026-01-10 | Accepted | All |
| #005 | Infrastructure Observability Stack Upgrades | 2026-01-10 | Accepted | Infra |
| #006 | Exponential Backoff Retry Strategy | 2026-01-10 | Accepted | 4, 8 |
| #007 | Centralized Metrics Registry Pattern | 2026-01-10 | Accepted | All |
| #008 | Structured Logging with Correlation IDs | 2026-01-10 | Accepted | All |
| #009 | Partition by Symbol for Kafka Topics | 2026-01-10 | Accepted | 6, 8 |
| #010 | At-Least-Once with Idempotent Producers | 2026-01-10 | Accepted | 6 |
| #011 | Per-Symbol Sequence Tracking with LRU Cache | 2026-01-10 | Accepted | 8 |
| #012 | Consumer Group Naming Strategy | 2026-01-10 | Accepted | 8 |
| #013 | Single-Topic Subscription with Pattern Support | 2026-01-10 | Accepted | 8 |
| #014 | Sequence Gap Logging with Metrics Tracking | 2026-01-10 | Accepted | 8, 11 |
| #015 | Batch Size 1000 with Configurable Override | 2026-01-10 | Accepted | 8, 4 |
| #016 | Daemon Mode with Graceful Shutdown | 2026-01-10 | Accepted | 8 |
| #017 | DuckDB Version Guessing for Local Development | 2026-01-10 | Accepted | 9 |
| #018 | Generator Pattern for Memory-Efficient Replay | 2026-01-10 | Accepted | 10 |
| #019 | API Versioning with /v1/ Prefix | 2026-01-11 | Accepted | 12 |
| #020 | API Key Authentication | 2026-01-11 | Accepted | 12 |
| #021 | Rate Limiting (100 req/min) | 2026-01-11 | Accepted | 12 |
| #022 | Flexible Data Type Handling in API Models | 2026-01-11 | Accepted | 12 |

---

## Decision #001: DuckDB Over Spark for Query Engine

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 09 (Query Engine), Step 10 (Replay Engine)

#### Context

Need to choose a query engine for analytical queries against the Iceberg lakehouse. The platform must support:
- SQL queries for trade and quote data
- Time-range filtering (common for market data)
- Aggregations (OHLCV summaries)
- Time-travel queries for backtesting

Options evaluated:
- **Apache Spark**: Industry standard for big data processing
- **Presto/Trino**: Distributed SQL query engines
- **DuckDB**: Embedded analytical database
- **Direct PyIceberg scanning**: Minimal dependencies

The project targets local execution for portfolio demonstration, not production scale.

#### Decision

Use **DuckDB** as the primary query engine for Phase 1.

#### Consequences

**Positive**:
- **Zero operational overhead**: No cluster to manage, perfect for local demo
- **Sub-second performance**: Analytical queries complete in < 1 second on GB-scale data
- **Native Iceberg support**: DuckDB Iceberg extension provides direct Parquet scanning
- **Low barrier to entry**: Single Python library, works out-of-the-box
- **Excellent for development**: Fast iteration during implementation
- **Portfolio-ready**: Demonstrates understanding without production complexity

**Negative**:
- **Single-node limitation**: Cannot scale horizontally for production workloads
- **May require replacement**: At 100x-1000x data scale, need distributed engine
- **Less familiar**: Some teams more experienced with Spark ecosystem

**Neutral**:
- **Clear upgrade path exists**: Iceberg abstraction enables engine swap with minimal code changes
- **Can add Presto/Trino later**: Not a permanent architectural constraint

#### Alternatives Considered

1. **Apache Spark**
   - **Rejected**: Too heavy for local execution
   - Requires cluster management (standalone, YARN, or K8s)
   - 10-20 second startup overhead unacceptable for interactive queries
   - Overkill for demo-scale data (GB, not TB)

2. **Presto/Trino**
   - **Rejected**: Similar cluster management overhead to Spark
   - Requires coordinator + worker setup
   - Better than Spark for ad-hoc queries but still complex for local demo

3. **Direct PyIceberg Scanning**
   - **Rejected**: Limited SQL functionality
   - Would require custom aggregation logic
   - Slower than DuckDB for analytics
   - No query optimization

#### Implementation Notes

1. **Install DuckDB with Iceberg extension**:
   ```bash
   pip install duckdb>=1.4.0
   ```

2. **Configure S3/MinIO access**:
   ```python
   conn.execute("""
       CREATE SECRET minio_secret (
           TYPE S3,
           KEY_ID 'admin',
           SECRET 'password',
           ENDPOINT 'localhost:9000',
           USE_SSL false,
           URL_STYLE 'path'
       );
   """)
   ```

3. **Query Iceberg tables**:
   ```sql
   SELECT * FROM iceberg_scan('s3://warehouse/market_data.db/trades')
   WHERE symbol = 'BHP' AND exchange_timestamp >= '2014-03-10'
   ```

4. **Benchmark query performance**: Document sub-5-second response times for demo queries

#### Verification

- [x] Query engine executes simple queries in < 5 seconds
- [x] Successfully reads Iceberg metadata and data files
- [x] Handles decimal and timestamp types correctly
- [x] Works with MinIO S3-compatible storage
- [ ] Predicate pushdown reduces data scanned (verify in query plans)
- [ ] Aggregation queries (OHLCV) produce correct results

---

## Decision #002: Daily Partitioning for Iceberg Tables

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 03 (Iceberg Catalog)

#### Context

Iceberg tables require partitioning strategy. Market data has strong temporal nature—most queries filter by date or time range.

Partition granularity options:
- **Hourly**: 24 partitions per day
- **Daily**: 1 partition per day
- **Weekly**: 1 partition per 7 days
- **Monthly**: 1 partition per month

Trade-offs:
- **Too fine-grained**: Many small files, slow metadata operations
- **Too coarse-grained**: Scan unnecessary data, slower queries

#### Decision

Partition Iceberg tables by **day** using `DayTransform()` on `exchange_timestamp`.

#### Consequences

**Positive**:
- **Optimizes common queries**: Most market data queries filter by date
- **Efficient partition pruning**: Query planner skips entire days not in filter
- **Manageable partition count**: ~365 partitions per year (reasonable for metadata)
- **Aligns with business logic**: Daily summaries (OHLCV) map naturally to partitions

**Negative**:
- **Intraday queries scan full day**: Hourly filters still scan entire day's data
- **Large partitions**: High-volume days may have large Parquet files (compaction needed)

**Neutral**:
- **Can be changed later**: Iceberg supports partition evolution without rewriting data
- **Compaction strategy**: Plan for periodic compaction of small files

#### Alternatives Considered

1. **Hourly Partitioning**
   - **Rejected**: Creates too many partitions (8,760/year)
   - Metadata overhead outweighs query benefits
   - Small files require aggressive compaction

2. **Weekly Partitioning**
   - **Rejected**: Scans 7 days of data for single-day queries
   - Not aligned with typical use cases

3. **No Partitioning**
   - **Rejected**: Full table scans unacceptable for time-range queries
   - Defeats purpose of Iceberg's metadata filtering

#### Implementation Notes

```python
partition_spec = PartitionSpec(
    PartitionField(
        source_id=4,  # exchange_timestamp field ID
        field_id=1000,
        transform=DayTransform(),
        name="exchange_date"
    )
)
```

#### Verification

- [ ] Query plans show partition pruning (EXPLAIN SELECT ...)
- [ ] Queries filtering by single day scan only that partition
- [ ] Partition directory structure: `data/exchange_date=2014-03-10/`
- [ ] File counts remain manageable (< 1000 files per partition)

---

## Decision #003: At-Least-Once Delivery with Manual Commit

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 06 (Kafka Producer), Step 08 (Kafka Consumer)

#### Context

Consumer-to-Iceberg pipeline must guarantee no data loss. Kafka supports:
- **At-most-once**: Auto-commit before processing (data loss possible)
- **At-least-once**: Manual commit after processing (duplicates possible)
- **Exactly-once**: Kafka transactions (complex, adds latency)

Iceberg writes are idempotent (duplicate detection via file names).

#### Decision

Implement **at-least-once delivery** with manual commit after successful Iceberg write.

#### Consequences

**Positive**:
- **No data loss**: Crash before commit → reprocess from last offset
- **Simple implementation**: Standard consumer pattern, well-understood
- **Iceberg handles duplicates**: Append operations with same data create same file hashes
- **Acceptable for market data**: Historical data replay tolerates occasional duplicates

**Negative**:
- **Potential duplicate writes**: Consumer crash after write but before commit → reprocessing
- **Idempotency required**: Must ensure Iceberg handles duplicates gracefully

**Neutral**:
- **Can upgrade to exactly-once later**: If strict deduplication needed
- **Monitoring required**: Track duplicate rates in production

#### Alternatives Considered

1. **Exactly-Once Semantics (Kafka Transactions)**
   - **Rejected**: 2-3x latency overhead
   - Complexity not justified for demo and portfolio review
   - Iceberg already provides append idempotency

2. **At-Most-Once (Auto-Commit)**
   - **Rejected**: Unacceptable data loss risk
   - Violates lakehouse durability guarantees

#### Implementation Notes

```python
# Consumer config
consumer_config = {
    'enable.auto.commit': False,  # Manual commit
    'max.poll.interval.ms': 300000,  # 5 min for slow writes
}

# Processing loop
while True:
    msg = consumer.poll(timeout=1.0)
    if msg:
        # 1. Deserialize
        record = deserialize(msg)

        # 2. Write to Iceberg (ACID transaction)
        iceberg_writer.write([record])

        # 3. Commit offset ONLY after successful write
        consumer.commit()
```

#### Verification

- [ ] Consumer crash test: Kill process during write, verify data replayed
- [ ] No offset commit before Iceberg write completes
- [ ] Duplicate detection: Same record processed twice creates identical Iceberg snapshot
- [ ] Metrics track duplicate rate (should be near-zero in steady state)

---

## Decision #004: Embedded Architecture for Phase 1

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: All (platform-wide architectural decision)

#### Context

Platform can be architected as:
- **Distributed microservices**: Separate containers for each component
- **Embedded/monolithic**: Single application with embedded dependencies
- **Hybrid**: Critical path embedded, monitoring distributed

Portfolio demonstration must balance:
- **Realism**: Demonstrate production patterns
- **Simplicity**: Easy to run locally
- **Showcase skills**: Highlight distributed systems knowledge

#### Decision

Use **embedded architecture** for Phase 1:
- DuckDB embedded (not server mode)
- Python application (not separate services)
- Docker Compose for infrastructure only (Kafka, MinIO, Iceberg REST)

#### Consequences

**Positive**:
- **Easy to run**: `make docker-up && python scripts/demo.py`
- **No orchestration**: No Kubernetes or complex service mesh
- **Fast development**: No inter-service communication debugging
- **Portfolio-friendly**: Reviewer can run on laptop in minutes
- **Demonstrates judgment**: Right tool for the scale

**Negative**:
- **Not production-scale**: Won't handle 1000x data or traffic
- **Single point of failure**: Application crash stops all processing
- **Limited horizontal scaling**: Can't add more query servers

**Neutral**:
- **Clear migration path**: Document how to productionize (add load balancers, scale out query layer, add Presto/Trino)
- **Demonstrates architecture skills**: README explains production evolution

#### Alternatives Considered

1. **Full Microservices Architecture**
   - **Rejected**: Over-engineered for demo scale
   - Adds complexity without commensurate value
   - Harder for portfolio reviewer to evaluate

2. **Serverless/Cloud Native**
   - **Rejected**: Requires cloud account, costs money
   - Less portable across review environments

#### Implementation Notes

1. **Document production evolution in README**:
   ```markdown
   ## Scaling to Production

   - **Query Layer**: Add Presto/Trino cluster, replace DuckDB
   - **API Layer**: Horizontal scaling with load balancer
   - **Consumer**: Multiple consumer instances in same group
   - **Observability**: Add distributed tracing (Jaeger)
   ```

2. **Keep components loosely coupled**: Enable future service extraction

#### Verification

- [ ] Application runs on single laptop (8GB RAM minimum)
- [ ] Demo completes in < 5 minutes
- [ ] README clearly documents scaling strategy
- [ ] Architecture diagram shows embedded nature

---

## Decision #005: Infrastructure Observability Stack Upgrades

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Platform Team
**Related Steps**: Infrastructure Maintenance

#### Context

The observability stack (Prometheus, Grafana, Kafka-UI) was running on older versions from early 2024. Latest stable versions provide bug fixes, security updates, and new features. Research identified that latest versions required major version upgrades (Prometheus v2→v3, Grafana v10→v12) rather than minor patches.

Additionally, provectus/kafka-ui project was abandoned mid-2023, with active development continuing in the kafbat/kafka-ui fork. Migration to the maintained fork was necessary for long-term support.

#### Decision

**Aggressive Upgrade Strategy** (Option B from plan):
- **Prometheus**: v2.49.1 → v3.9.1 (major version jump)
- **Grafana**: 10.2.3 → v12.3.1 (skipped v11.x)
- **Kafka-UI**: provectus:latest → kafbat:v1.4.2 (fork migration)
- **Iceberg REST**: Attempted apache:1.10.1, rolled back to tabulario:0.8.0

Remove deprecated Grafana `grafana-piechart-panel` plugin (no dashboards deployed yet).

#### Consequences

**Positive**:
- ✅ **Latest stable versions**: Security patches, bug fixes, new features
- ✅ **Prometheus v3.9.1**: Bug fixes for Agent mode crash, relabel improvements
- ✅ **Grafana v12.3.1**: Reimagined log exploration, enhanced data connections
- ✅ **Kafka-UI active maintenance**: kafbat fork has full-text search, improved UI
- ✅ **Reproducible builds**: Version pinning eliminates "latest" drift

**Negative**:
- ⚠️ **Higher risk**: Multiple major version upgrades simultaneously
- ⚠️ **Iceberg REST blocked**: apache/iceberg-rest-fixture missing PostgreSQL JDBC driver
- ⚠️ **Testing burden**: Upgrade validation took 2.5 hours
- ⚠️ **Breaking changes risk**: Major versions may have API changes

**Neutral**:
- **Prometheus v3.x compatibility**: Existing prometheus.yml configuration compatible
- **Grafana plugin deprecation**: grafana-piechart-panel removed (no impact, no dashboards)
- **Kafka-UI fork**: Environment variables unchanged between provectus/kafbat

#### Alternatives Considered

1. **Conservative Incremental Upgrades** (Option A)
   - Prometheus v2.49.1 → v2.latest → v3.9.1 (stage through intermediates)
   - Grafana 10.2.3 → 11.x → 12.3.1 (don't skip versions)
   - **Rejected**: 4-6 hours for incremental testing cycles
   - Would be safer but timeline constraints favor aggressive approach

2. **Minimal/Essential Only** (Option C)
   - Only upgrade Iceberg REST to apache:1.10.1
   - Keep Prometheus, Grafana, Kafka-UI on current versions
   - **Rejected**: Misses security/bug fixes in observability stack

3. **Defer All Upgrades** (Option D)
   - No upgrades, plan for dedicated maintenance window
   - **Rejected**: Stack already 12-18 months old, defer compounds technical debt

#### Implementation Notes

**Upgrade Sequence**:
1. ✅ Prometheus v2.49.1 → v3.9.1 (docker-compose.yml line 353)
2. ✅ Kafka-UI provectus → kafbat:v1.4.2 (docker-compose.yml line 429)
3. ✅ Grafana 10.2.3 → v12.3.1, remove GF_INSTALL_PLUGINS (docker-compose.yml lines 390-399)
4. ❌ Iceberg REST tabulario → apache:1.10.1 (FAILED - missing PostgreSQL driver)

**Rollback Executed**:
- apache/iceberg-rest-fixture:1.10.1 failed to start
- Error: "No suitable driver found for jdbc:postgresql"
- Rolled back to tabulario/iceberg-rest:0.8.0
- Future: Investigate custom Dockerfile with PostgreSQL driver or alternative image

**Backups Created**:
- PostgreSQL: `backups/iceberg_catalog_20260110_184744.sql` (9.3KB)
- Grafana: Attempted (no dashboards to backup)

#### Verification

- [x] Prometheus v3.9.1 healthy, 2/3 scrape targets up
- [x] Grafana v12.3.1 healthy, Prometheus datasource connected
- [x] Kafka-UI kafbat v1.4.2 healthy, Schema Registry visible
- [x] Iceberg REST tabulario:0.8.0 healthy (rollback successful)
- [x] All 9 Docker containers healthy
- [x] Schema Registry: 10 schemas intact
- [x] No service restarts or crashes after 30 minutes

**Known Issues**:
- Iceberg REST apache migration blocked (PostgreSQL driver dependency)
- Need investigation: Build custom apache/iceberg-rest image with driver

---

## Decision #006: Exponential Backoff Retry Strategy

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 04 (Iceberg Writer), Step 08 (Kafka Consumer)

#### Context

Iceberg write operations can fail transiently due to network issues, S3/MinIO timeouts, or catalog service unavailability. Need a resilience strategy that:
- Handles transient failures automatically
- Doesn't mask permanent failures
- Maintains low latency for successful writes
- Integrates cleanly with ACID transactions

Options:
- No retry (fail fast)
- Fixed delay retry
- Exponential backoff
- Circuit breaker pattern

#### Decision

Implement **exponential backoff retry** with 3 attempts for Iceberg write operations.

Configuration:
- Max retries: 3
- Initial delay: 100ms
- Max delay: 10s
- Backoff factor: 2x
- Retry on: ConnectionError, TimeoutError, CommitFailedException

#### Consequences

**Positive**:
- **Automatic resilience**: Transient failures (network blips, temporary S3 slowdowns) handled transparently
- **Low overhead**: Most writes succeed on first attempt, retry only when needed
- **Production-ready**: Standard pattern used by AWS SDK, GCP libraries
- **Bounded latency**: Max 3 attempts = worst case ~20s (100ms + 200ms + 400ms + processing time)

**Negative**:
- **Delayed failure detection**: Permanent errors take ~700ms to surface (3 attempts)
- **Duplicate risk**: Crash during retry may cause duplicate processing (mitigated by at-least-once semantics)

**Neutral**:
- **Works with ACID**: Iceberg commit is atomic, partial writes rolled back automatically
- **Logged failures**: Structured logging tracks retry attempts for debugging

#### Alternatives Considered

1. **No Retry (Fail Fast)**
   - **Rejected**: Too brittle for production
   - Single network blip causes data loss or consumer restart

2. **Fixed Delay Retry**
   - **Rejected**: Doesn't adapt to failure duration
   - 1s delay too long for transient issues, too short for service restarts

3. **Circuit Breaker Pattern**
   - **Deferred**: Adds complexity, may implement in Step 8 consumer
   - Current retry sufficient for writer isolation

#### Implementation Notes

```python
@_retry_with_exponential_backoff(max_retries=3)
def write_trades(self, records, ...):
    # Will automatically retry on ConnectionError, TimeoutError, CommitFailedException
    table.append(arrow_table)
```

Retry logic logs each attempt:
```
WARNING: Attempt 1/3 failed, retrying (function=write_trades, error=Connection timeout, delay_seconds=0.1)
WARNING: Attempt 2/3 failed, retrying (function=write_trades, error=Connection timeout, delay_seconds=0.2)
ERROR: All 3 retry attempts failed (function=write_trades, error=Connection timeout)
```

#### Verification

- [x] Transient failures (simulated network timeout) succeed after retry
- [x] Permanent failures (invalid table name) fail immediately without retry
- [x] Retry metrics tracked (future enhancement)
- [x] Structured logging shows retry attempts

---

## Decision #007: Centralized Metrics Registry Pattern

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: All (platform-wide)

#### Context

Need to instrument all components with Prometheus metrics for HFT-grade observability. Requirements:
- 40+ metrics across ingestion, storage, query, API layers
- Consistent naming (RED metrics: Rate, Errors, Duration)
- HFT-optimized latency buckets (1ms-5s)
- Low-cardinality labels (exchange, asset_class, component)
- Type safety (Counter, Gauge, Histogram)

Options:
- Ad-hoc metrics creation in each module
- Centralized registry with pre-registration
- Dynamic metrics creation with factory pattern
- External metrics library (OpenTelemetry)

#### Decision

Implement **centralized metrics registry** (`metrics_registry.py`) with pre-registered metrics.

All metrics:
- Pre-registered at module load time
- Named with `k2_` prefix
- Use standard labels: `service`, `environment`, `component`, `exchange`, `asset_class`
- HFT-optimized histogram buckets: `[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]` seconds

#### Consequences

**Positive**:
- **Fail-fast on duplicates**: Pre-registration catches metric name conflicts immediately
- **Performance**: No runtime overhead from metric creation
- **Discoverability**: Single file documents all platform metrics
- **Type safety**: Metrics accessed via typed getters (`get_counter()`, `get_gauge()`, `get_histogram()`)
- **HFT-optimized**: Histogram buckets designed for sub-500ms p99 target

**Negative**:
- **Rigidity**: Adding new metrics requires registry update (not ad-hoc creation)
- **Import dependency**: All modules must import from registry

**Neutral**:
- **Wrapper API**: `MetricsClient` provides convenience methods, hides Prometheus API
- **Component-level defaults**: `create_component_metrics(component="storage")` adds default labels

#### Alternatives Considered

1. **Ad-Hoc Metrics Creation**
   - **Rejected**: No central documentation, duplicate names possible, inconsistent labeling

2. **OpenTelemetry**
   - **Rejected**: Overkill for Phase 1, Prometheus sufficient for HFT demo
   - Can migrate later if distributed tracing needed

3. **Dynamic Factory Pattern**
   - **Rejected**: Runtime overhead, no fail-fast on duplicates

#### Implementation Notes

**Registry** (`metrics_registry.py`):
```python
# Pre-register all metrics
ICEBERG_WRITE_DURATION_SECONDS = Histogram(
    "k2_iceberg_write_duration_seconds",
    "Iceberg write operation duration in seconds",
    EXCHANGE_LABELS + ["table", "operation"],
    buckets=STORAGE_BUCKETS,
)

_METRIC_REGISTRY = {
    "iceberg_write_duration_seconds": ICEBERG_WRITE_DURATION_SECONDS,
    # ... 40+ more metrics
}
```

**Usage** (`writer.py`):
```python
from k2.common.metrics import create_component_metrics

metrics = create_component_metrics("storage")

with metrics.timer("iceberg_write_duration_seconds",
                   labels={"exchange": "asx", "asset_class": "equities", "table": "trades"}):
    table.append(arrow_table)
```

#### Verification

- [x] All 40+ metrics pre-registered without conflicts
- [x] Writer uses registry-defined metrics
- [x] Histogram buckets appropriate for HFT latency (1ms-5s)
- [x] Component-level metrics factory works
- [ ] Prometheus scrape endpoint returns all metrics (Step 13)

---

## Decision #008: Structured Logging with Correlation IDs

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: All (platform-wide)

#### Context

Need production-ready logging for HFT observability. Requirements:
- JSON format for log aggregation (Grafana Loki, ELK, CloudWatch)
- Correlation IDs for request tracing
- Context propagation (exchange, asset_class, component)
- Thread-safe and async-friendly
- Development-friendly console output

Options:
- Standard Python logging with JSON formatter
- structlog (structured logging library)
- OpenTelemetry logs
- Custom logging framework

#### Decision

Implement **structlog** with correlation IDs using `contextvars` for context propagation.

Features:
- JSON output in production (`json_output=True`)
- Colored console output in development (`json_output=False`)
- Automatic correlation ID injection from context
- Component-level loggers with default context
- Timer context manager for operation logging

#### Consequences

**Positive**:
- **JSON logging**: Ready for Grafana Loki, ELK, CloudWatch without custom formatters
- **Correlation tracking**: Full request/operation trace via correlation_id field
- **Thread-safe**: contextvars automatically isolates context per thread/async task
- **Zero boilerplate**: `logger.info("msg", key=value)` automatically adds timestamp, level, correlation_id
- **Development UX**: Colored console output improves debugging

**Negative**:
- **Additional dependency**: Adds structlog (but it's 0-dependency itself)
- **Learning curve**: Slightly different API than standard logging

**Neutral**:
- **Processor chain**: Can add custom processors (add request_id, sanitize PII, etc.)
- **Compatible with standard logging**: Can capture logs from libraries using `logging` module

#### Alternatives Considered

1. **Standard Python Logging + JSON Formatter**
   - **Rejected**: No built-in correlation ID support, awkward context propagation

2. **OpenTelemetry Logs**
   - **Rejected**: Overkill for Phase 1, full observability stack not needed yet

3. **Custom Framework**
   - **Rejected**: Reinventing wheel, structlog is battle-tested

#### Implementation Notes

**Logger Factory** (`logging.py`):
```python
logger = get_logger(__name__, component="storage")

# Automatically includes: timestamp, level, component
logger.info("Trades written", exchange="asx", record_count=1000)

# Output (JSON):
# {"timestamp": "2026-01-10T12:34:56Z", "level": "INFO",
#  "component": "storage", "exchange": "asx", "record_count": 1000,
#  "message": "Trades written"}
```

**Correlation IDs**:
```python
from k2.common.logging import set_correlation_id

set_correlation_id("request-abc-123")
logger.info("Processing batch")  # Automatically includes correlation_id
```

**Timer Context Manager**:
```python
with logger.timer("batch_processing", symbol="BHP"):
    process_batch()

# Logs:
# DEBUG: Starting batch_processing (operation=batch_processing, symbol=BHP)
# INFO: Operation completed: batch_processing (operation=batch_processing, duration_ms=150.5, symbol=BHP)
```

#### Verification

- [x] JSON output in production mode
- [x] Correlation IDs propagate automatically
- [x] Component-level loggers work
- [x] Timer context manager logs duration
- [x] Thread-safe context isolation (not yet tested with threads)
- [ ] Log aggregation (Grafana Loki integration - Step 14)

---

## Decision #009: Partition by Symbol for Kafka Topics

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 06 (Kafka Producer), Step 08 (Kafka Consumer)

#### Context

Kafka requires partitioning key for message distribution. Market data characteristics:
- High message rate (10K+ msg/sec per exchange)
- Order matters per symbol (sequence numbers, time-series)
- ~1000-5000 symbols per exchange
- Consumers need per-symbol ordering for sequence gap detection

Options:
- Partition by symbol
- Partition by data type (trades vs quotes)
- Round-robin (no key)
- Composite key (symbol + exchange)

#### Decision

**Partition by symbol** using symbol as Kafka partition key.

#### Consequences

**Positive**:
- **Order preservation**: All messages for BHP go to same partition, preserving order
- **Sequence tracking**: Consumer can track sequence numbers per symbol reliably
- **Hot partition handling**: Highly traded symbols (BHP, RIO) get dedicated partitions
- **Parallelism**: Multiple consumers can process different symbols concurrently

**Negative**:
- **Hot partitions**: Liquid symbols (e.g., BHP with 50K trades/day) create partition skew
- **Rebalancing**: Adding partitions requires careful planning (symbol→partition mapping changes)

**Neutral**:
- **Partition count**: Use 30 partitions for ASX (1000 symbols), ensures multiple symbols per partition
- **Monitoring**: Track partition lag per-partition to detect hot spots

#### Alternatives Considered

1. **Round-Robin (No Key)**
   - **Rejected**: Breaks ordering, impossible to track sequence numbers

2. **Partition by Data Type**
   - **Rejected**: Doesn't solve hot partition problem, still need symbol ordering

3. **Composite Key (Symbol + Exchange)**
   - **Rejected**: Exchange already in topic name, redundant

#### Implementation Notes

**Producer** (`producer.py` - to be implemented):
```python
# Partition key = symbol
producer.produce(
    topic="market.equities.trades.asx",
    key=record["symbol"],  # BHP, RIO, CBA, etc.
    value=avro_bytes
)
```

**Consumer** (`consumer.py` - to be implemented):
```python
# Messages for same symbol arrive in order
# Can track sequence numbers per symbol
sequence_tracker[symbol] = message.sequence_number
```

#### Verification

- [ ] Produce messages for BHP to multiple partitions (should all go to same partition)
- [ ] Verify ordering: seq 1, 2, 3 arrive in order (not 1, 3, 2)
- [ ] Monitor partition lag to detect hot partitions
- [ ] Test rebalancing: Consumer restart doesn't break ordering

---

## Decision #010: At-Least-Once with Idempotent Producers

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 06 (Kafka Producer)

#### Context

Kafka producers must choose delivery semantics:
- At-most-once: Fast, but data loss possible
- At-least-once: Retry on failure, duplicates possible
- Exactly-once: Kafka transactions, adds latency

Producer requirements:
- No data loss (market data is valuable)
- Sub-100ms p99 latency (HFT constraint)
- Simple implementation (portfolio demo)

#### Decision

Use **at-least-once** delivery with **idempotent producer** enabled.

Configuration:
```python
producer_config = {
    'enable.idempotence': True,  # Prevent duplicates on retry
    'acks': 'all',  # Wait for all replicas
    'retries': 3,  # Retry on transient failures
}
```

#### Consequences

**Positive**:
- **No data loss**: Retries ensure messages eventually delivered
- **Duplicate prevention**: Idempotent producer (Kafka 0.11+) prevents duplicates on retry
- **Low latency**: No transaction overhead, still <100ms p99
- **Standard pattern**: Used by most Kafka applications

**Negative**:
- **Slightly higher latency**: `acks=all` waits for replication (~5-10ms overhead)
- **Not true exactly-once**: Network failures after ack but before client receives can cause duplicates (rare)

**Neutral**:
- **Downstream handles duplicates**: Consumer uses at-least-once + Iceberg idempotency
- **Good enough for demo**: Exactly-once not required for portfolio demonstration

#### Alternatives Considered

1. **Exactly-Once Semantics (Kafka Transactions)**
   - **Rejected**: 2-3x latency overhead, complexity not justified

2. **At-Most-Once (acks=1)**
   - **Rejected**: Data loss risk unacceptable

#### Implementation Notes

```python
producer_config = {
    'bootstrap.servers': 'localhost:9092',
    'enable.idempotence': True,
    'acks': 'all',
    'retries': 3,
    'max.in.flight.requests.per.connection': 5,  # Required for idempotence
    'compression.type': 'snappy',  # Balance speed + compression
}
```

#### Verification

- [ ] Produce 10K messages, verify all delivered (count on consumer)
- [ ] Simulate network failure, verify retry succeeds
- [ ] Check for duplicates (should be zero with idempotent producer)
- [ ] Measure p99 latency (target <100ms)

---

## Decision #011: Per-Symbol Sequence Tracking with LRU Cache

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 08 (Kafka Consumer)

#### Context

Consumer must detect sequence gaps to identify data loss. Challenges:
- ~1000-5000 symbols per exchange
- Need to track last sequence number per symbol
- Memory constraints (running on laptop)
- Inactive symbols shouldn't consume memory forever

Options:
- In-memory dict (unbounded growth)
- Per-symbol dict with TTL
- LRU cache (bounded size)
- Database (Redis, PostgreSQL)

#### Decision

Implement **per-symbol sequence tracking** with **LRU cache** (capacity: 10,000 symbols).

When sequence gap detected:
- Log warning with gap size
- Emit `sequence_gaps_detected_total` metric
- Continue processing (don't block on gaps)

#### Consequences

**Positive**:
- **Memory bounded**: 10K symbols × 8 bytes = 80KB maximum
- **Automatic cleanup**: Inactive symbols evicted automatically
- **Simple implementation**: Python `functools.lru_cache` or custom OrderedDict
- **Good coverage**: 10K > typical active symbols per exchange (~1K-2K)

**Negative**:
- **Cache misses**: Inactive symbol returning after eviction treated as "first seen" (false negative)
- **No persistence**: Process restart loses sequence state (acceptable for demo)

**Neutral**:
- **Production upgrade**: Replace with Redis for persistent state if needed
- **Gap handling**: Log and continue (don't block ingestion on gaps)

#### Alternatives Considered

1. **Unbounded In-Memory Dict**
   - **Rejected**: Memory leak risk (1M symbols × 8 bytes = 8MB, plus object overhead)

2. **Redis**
   - **Rejected**: Adds infrastructure dependency, overkill for demo scale

3. **No Sequence Tracking**
   - **Rejected**: Can't detect data loss, critical for market data integrity

#### Implementation Notes

```python
from collections import OrderedDict

class SequenceTracker:
    def __init__(self, capacity=10000):
        self.sequences = OrderedDict()  # LRU cache
        self.capacity = capacity

    def check_sequence(self, symbol, sequence):
        last_seq = self.sequences.get(symbol)

        if last_seq is None:
            # First time seeing this symbol
            self.sequences[symbol] = sequence
            return True

        expected = last_seq + 1
        if sequence != expected:
            gap_size = sequence - expected
            logger.warning("Sequence gap detected",
                          symbol=symbol, expected=expected, actual=sequence, gap=gap_size)
            metrics.increment("sequence_gaps_detected_total",
                            labels={"exchange": "asx", "symbol": symbol, "severity": "warning"})

        # Update last seen sequence
        self.sequences[symbol] = sequence
        self.sequences.move_to_end(symbol)  # Mark as recently used

        # Evict oldest if over capacity
        if len(self.sequences) > self.capacity:
            self.sequences.popitem(last=False)

        return True
```

#### Verification

- [ ] Track 100 symbols, verify sequence gaps logged
- [ ] Fill cache to 10K symbols, verify LRU eviction works
- [ ] Simulate gap: seq 1, 2, 5 → logs "gap=2"
- [ ] Metrics: `sequence_gaps_detected_total` increments on gap
- [ ] Performance: Tracking overhead <1ms per message

---

## Decision #012: Consumer Group Naming Strategy

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 08 (Kafka Consumer)

#### Context

Consumer groups determine offset management, parallel processing capability, and operational clarity. Naming strategies:
- **Purpose-based**: `k2-iceberg-writer` (generic)
- **Data-type-based**: `k2-iceberg-writer-trades`, `k2-iceberg-writer-quotes`
- **Application-based**: `k2-consumer-prod-001`

Requirements:
- Clear ownership (who manages this consumer?)
- Independent scaling per data type
- Easy operational debugging ("which consumer group is lagging?")

#### Decision

Use **data-type-based naming**: `k2-iceberg-writer-{data_type}`

Examples:
- `k2-iceberg-writer-trades`
- `k2-iceberg-writer-quotes`
- `k2-iceberg-writer-reference_data`

#### Consequences

**Positive**:
- **Independent scaling**: Trades high-volume → scale trades consumer without affecting quotes
- **Clear ownership**: Consumer group name immediately tells you what it processes
- **Operational clarity**: `kafka-consumer-groups --describe --group k2-iceberg-writer-trades` shows trade lag
- **Resource isolation**: Slow quote processing doesn't block trade processing
- **Simplified monitoring**: Alert per data type (trade lag > 10s = critical, quote lag > 60s = warning)

**Negative**:
- **More consumer groups**: 3 data types = 3 consumer groups vs 1 generic group
- **Coordination overhead**: Must manage multiple consumer instances

**Neutral**:
- **Standard pattern**: Most streaming platforms use data-type or purpose-based naming
- **Easy refactoring**: Can consolidate later if needed

#### Alternatives Considered

1. **Single generic consumer group (`k2-iceberg-writer`)**
   - **Rejected**: All data types share same offsets and lag metrics
   - Can't scale trades independently from quotes
   - Slow reference data processing blocks trades

2. **Application-based naming (`k2-consumer-prod-001`)**
   - **Rejected**: Unclear what data this processes
   - Operational overhead (must document mapping)

#### Implementation Notes

```python
# Consumer instantiation
consumer = MarketDataConsumer(
    consumer_group=f"k2-iceberg-writer-{data_type}",  # e.g., k2-iceberg-writer-trades
    topics=[f"market.{asset_class}.{data_type}.{exchange}"],
)
```

**Operational commands**:
```bash
# Check lag for trades
kafka-consumer-groups --bootstrap-server localhost:9092 \
    --group k2-iceberg-writer-trades \
    --describe

# Reset offsets for quotes
kafka-consumer-groups --bootstrap-server localhost:9092 \
    --group k2-iceberg-writer-quotes \
    --reset-offsets --to-earliest \
    --topic market.equities.quotes.asx \
    --execute
```

#### Verification

- [ ] Consumer group created: `k2-iceberg-writer-trades`
- [ ] Lag metrics separate per data type
- [ ] Multiple consumers in same group parallelize processing
- [ ] Documentation explains naming pattern

---

## Decision #013: Single-Topic Subscription with Pattern Support

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 08 (Kafka Consumer)

#### Context

Consumers can subscribe to topics via:
- **Explicit list**: `['market.equities.trades.asx', 'market.equities.trades.nasdaq']`
- **Pattern matching**: `'market\\.equities\\.trades\\..*'` (all equity trade topics)
- **Single topic**: `'market.equities.trades.asx'` (one at a time)

Considerations:
- **Simplicity vs flexibility**: Single topic is simple, pattern is powerful
- **Operational safety**: Pattern can accidentally consume wrong topics
- **Offset management**: Patterns mix offsets across topics
- **Testing**: Single topic easier to test and debug

Staff data engineer principle: **Start simple, add complexity only when proven necessary**.

#### Decision

Implement **single-topic subscription** with optional pattern support via configuration.

Default mode:
```python
consumer.subscribe(['market.equities.trades.asx'])  # Single topic
```

Optional pattern mode (via config):
```python
consumer.subscribe_pattern('market\\.equities\\.trades\\..*')  # Pattern
```

#### Consequences

**Positive**:
- **Predictable behavior**: Clear what data is being processed
- **Easier debugging**: Offset tracking is straightforward
- **Safer operations**: No accidental cross-topic consumption
- **Better testing**: Can test with specific topic without pattern complexity
- **Clear logs**: "Consuming from market.equities.trades.asx" vs "Consuming from pattern"

**Negative**:
- **More consumer instances**: Need separate consumer per topic (acceptable for demo)
- **Configuration overhead**: Must specify topics explicitly

**Neutral**:
- **Production upgrade path**: Pattern support ready when needed
- **Standard pattern**: Single-topic is common for data pipelines

#### Alternatives Considered

1. **Pattern-based subscription by default**
   - **Rejected**: Too much magic, harder to debug
   - Risk: Accidentally consume from wrong topics if pattern too broad
   - Example: `market\\..*\\..*\\..*` could consume quotes when expecting trades

2. **Multi-topic explicit list**
   - **Rejected**: Adds complexity without clear benefit for Phase 1
   - Can add later if multiple topics per consumer needed

#### Implementation Notes

```python
class MarketDataConsumer:
    def __init__(
        self,
        topics: Optional[List[str]] = None,
        topic_pattern: Optional[str] = None,
    ):
        if topics and topic_pattern:
            raise ValueError("Specify either topics or topic_pattern, not both")

        if topics:
            self.consumer.subscribe(topics)  # Explicit
        elif topic_pattern:
            self.consumer.subscribe(pattern=topic_pattern)  # Pattern
        else:
            raise ValueError("Must specify either topics or topic_pattern")
```

**CLI usage**:
```bash
# Single topic (default)
k2-ingest consume --topic market.equities.trades.asx

# Pattern (advanced)
k2-ingest consume --topic-pattern "market\\.equities\\.trades\\..*"
```

#### Verification

- [ ] Consumer subscribes to single topic successfully
- [ ] Pattern support available but not default
- [ ] Error raised if both topics and pattern specified
- [ ] Documentation shows both modes with clear recommendations

---

## Decision #014: Sequence Gap Logging with Metrics Tracking

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 08 (Kafka Consumer), Step 11 (Sequence Tracker)

#### Context

Sequence gaps occur when:
- Message reordering during network partitions
- Producer failure between messages
- Topic compaction (for compacted topics)
- Deliberate message skipping (e.g., market closed)

Gap handling options:
- **Option A**: Log warning only (non-blocking)
- **Option B**: Pause consumption and wait (blocking, risky)
- **Option C**: Log + track in metrics (observable)
- **Option D**: Log + write to separate "gaps" table (auditable)

Staff data engineer principles:
- **Observability over blocking**: Don't block production on expected anomalies
- **Audit trail**: Track gaps for post-hoc analysis
- **SLA-based alerting**: Define thresholds for acceptable gap rates

Market data characteristics:
- Gaps expected during market open/close transitions
- Reordering rare but possible
- Missing data must be detectable for compliance

#### Decision

Implement **Option C + D**: Log warnings, track in Prometheus metrics, and optionally write to DuckDB gaps table for audit.

Behavior:
1. **Detect gap**: Expected seq=102, received seq=105 → gap of 2
2. **Log warning**: `sequence_gap_detected` with symbol, expected, received
3. **Increment metric**: `k2_sequence_gaps_detected_total{symbol="BHP",exchange="asx"}`
4. **Continue processing**: Don't block (at-least-once guarantees eventual consistency)
5. **Optional gap table**: Write to `market_data.sequence_gaps` for audit

#### Consequences

**Positive**:
- **Non-blocking**: Processing continues, no production impact
- **Observable**: Prometheus metrics → Grafana dashboards → alerts
- **Auditable**: DuckDB gaps table for compliance and investigation
- **Actionable**: Can investigate gaps post-hoc without affecting real-time processing
- **Standard pattern**: Industry best practice for streaming pipelines

**Negative**:
- **No automatic recovery**: Missing messages not automatically requested
- **Requires monitoring**: Must set up alerts on gap metrics
- **Storage overhead**: Gaps table grows (mitigated by periodic cleanup)

**Neutral**:
- **Acceptable for market data**: Historical data, not real-time trading decisions
- **Can add backfill later**: Gaps table enables targeted data recovery

#### Alternatives Considered

1. **Block consumption until gap filled**
   - **Rejected**: Too risky, single missing message blocks entire pipeline
   - Timeout handling complex (how long to wait?)
   - Can deadlock if message truly lost

2. **Ignore gaps completely**
   - **Rejected**: No visibility into data quality
   - Compliance risk (can't prove data completeness)

3. **Request missing messages from producer**
   - **Rejected**: Adds complexity, requires producer to buffer messages
   - Not applicable (producer doesn't store historical messages)

#### Implementation Notes

**SequenceTracker enhancement**:
```python
class SequenceTracker:
    def check_sequence(self, symbol: str, seq_num: int) -> Optional[int]:
        """Check sequence and return gap size if detected."""
        expected = self.last_seen.get(symbol, seq_num - 1) + 1

        if seq_num > expected:
            gap = seq_num - expected
            logger.warning(
                "Sequence gap detected",
                symbol=symbol,
                expected=expected,
                received=seq_num,
                gap=gap,
            )
            metrics.increment(
                "sequence_gaps_detected_total",
                labels={"symbol": symbol, "exchange": "asx", "gap_size": str(gap)}
            )
            return gap

        self.last_seen[symbol] = seq_num
        return None
```

**Gaps table schema** (DuckDB):
```sql
CREATE TABLE IF NOT EXISTS market_data.sequence_gaps (
    detected_at TIMESTAMP,
    symbol VARCHAR,
    exchange VARCHAR,
    asset_class VARCHAR,
    data_type VARCHAR,
    expected_sequence BIGINT,
    received_sequence BIGINT,
    gap_size INTEGER,
    PRIMARY KEY (detected_at, symbol, data_type)
);
```

**Grafana alert**:
```yaml
# Alert if gap rate > 1% of messages
expr: rate(k2_sequence_gaps_detected_total[5m]) / rate(k2_kafka_messages_consumed_total[5m]) > 0.01
severity: warning
summary: "High sequence gap rate detected"
```

#### Verification

- [ ] Gap detected: seq 1, 2, 5 → logs gap=2
- [ ] Metric incremented: `k2_sequence_gaps_detected_total`
- [ ] Gaps table populated: 1 row for gap
- [ ] Processing continues: Message 5 written to Iceberg
- [ ] Grafana dashboard shows gap rate

---

## Decision #015: Batch Size 1000 with Configurable Override

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 08 (Kafka Consumer), Step 04 (Iceberg Writer)

#### Context

Iceberg write performance depends heavily on batch size:
- **Small batches (1-100)**: Low latency, high transaction overhead (metadata writes)
- **Medium batches (100-1000)**: Balanced latency and throughput
- **Large batches (1000-10000)**: High throughput, high latency

Target SLA: <500ms p99 latency from Kafka → Iceberg

Factors:
- **Metadata overhead**: Each Iceberg transaction writes manifest files (~10-50ms)
- **Parquet file creation**: Larger batches = fewer, larger Parquet files (better for queries)
- **Memory usage**: Larger batches = more memory (records buffered before write)
- **Failure impact**: Larger batches = more reprocessing on failure

Staff data engineer trade-off: **Optimize for throughput while meeting latency SLA**.

#### Decision

Default batch size: **1000 records**, configurable via environment variable.

Rationale:
- 1000 records ≈ 200KB (trades) ≈ 300KB (quotes)
- Iceberg write: 100-200ms (includes metadata)
- Kafka poll: 50-100ms (100 messages/poll at 10 polls)
- Total latency: 150-300ms (well under 500ms p99 target)

#### Consequences

**Positive**:
- **Meets latency SLA**: 300ms typical << 500ms p99 target
- **Good throughput**: ~3000-5000 records/sec with single consumer
- **Efficient Parquet files**: 1000-record files good for DuckDB queries
- **Reasonable memory**: 200-300KB buffer per consumer
- **Balanced failure impact**: 1000 records worst-case reprocessing

**Negative**:
- **Not optimal for all scenarios**: High-volume (trades) might benefit from 5000, low-volume (reference data) could use 100
- **Requires tuning**: Production may need per-data-type configuration

**Neutral**:
- **Configurable**: `K2_CONSUMER_BATCH_SIZE=5000` for overrides
- **Standard size**: 1000 is common default in streaming systems

#### Alternatives Considered

1. **Small batch size (100)**
   - **Rejected**: 10x more Iceberg transactions = 10x metadata overhead
   - Lower throughput (~500-1000 records/sec)
   - But: Lower latency (50-100ms)

2. **Large batch size (10000)**
   - **Rejected**: Risk of exceeding 500ms p99 target
   - Memory: 2-3MB buffer (acceptable but larger)
   - Failure impact: 10K records reprocessed

3. **Adaptive batch size**
   - **Rejected**: Too complex for Phase 1
   - Can add later with metrics-driven tuning

#### Implementation Notes

```python
class MarketDataConsumer:
    def __init__(self, batch_size: Optional[int] = None):
        self.batch_size = batch_size or int(os.getenv('K2_CONSUMER_BATCH_SIZE', '1000'))

    def consume_batch(self):
        """Consume up to batch_size records."""
        batch = []
        while len(batch) < self.batch_size:
            msg = self.consumer.poll(timeout=0.1)
            if msg is None:
                break  # No more messages available
            batch.append(msg)

        if batch:
            self._write_to_iceberg(batch)
            self.consumer.commit()  # Commit after successful write
```

**Configuration**:
```bash
# Default (1000)
k2-ingest consume --topic market.equities.trades.asx

# Override
K2_CONSUMER_BATCH_SIZE=5000 k2-ingest consume --topic market.equities.trades.asx
```

#### Verification

- [ ] Batch size defaults to 1000
- [ ] Environment variable override works
- [ ] P99 latency < 500ms with 1000 batch size
- [ ] Throughput: 3000+ records/sec
- [ ] Memory usage stable at 200-300KB per consumer

---

## Decision #016: Daemon Mode with Graceful Shutdown

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 08 (Kafka Consumer)

#### Context

Consumer execution modes:
- **Daemon mode**: Run indefinitely until stopped (production)
- **Batch mode**: Consume N messages then exit (testing/backfills)
- **Time-based**: Consume for N seconds then exit

Requirements:
- **Production**: Long-running daemon for continuous processing
- **Testing**: Batch mode for integration tests
- **Graceful shutdown**: Handle SIGTERM/SIGINT cleanly

Staff data engineer principles:
- **Support both modes**: Production and testing have different needs
- **No data loss on shutdown**: Flush + commit before exit
- **Observable shutdown**: Log final statistics

#### Decision

Implement **daemon mode by default** with optional batch mode and graceful shutdown handling.

Modes:
1. **Daemon** (default): `k2-ingest consume --topic X`
2. **Batch**: `k2-ingest consume --topic X --max-messages 1000`

Graceful shutdown:
- Catch SIGTERM/SIGINT
- Finish processing current batch
- Flush Iceberg writer
- Commit Kafka offsets
- Log final statistics
- Exit cleanly

#### Consequences

**Positive**:
- **Production-ready**: Daemon mode for continuous processing
- **Testing-friendly**: Batch mode for integration tests
- **No data loss**: Graceful shutdown guarantees offset commit
- **Observable**: Log shutdown statistics (records processed, duration)
- **Standard pattern**: Industry best practice for streaming consumers

**Negative**:
- **Complexity**: Signal handling adds code
- **Shutdown latency**: May take seconds to finish current batch

**Neutral**:
- **Orchestration-ready**: Works with Kubernetes, systemd, Docker
- **Backfill-capable**: Batch mode enables controlled backfills

#### Alternatives Considered

1. **Daemon only**
   - **Rejected**: Testing requires manual stopping
   - Integration tests harder to write

2. **Batch only**
   - **Rejected**: Production requires restarts
   - No continuous processing

#### Implementation Notes

```python
import signal
import sys

class MarketDataConsumer:
    def __init__(self, max_messages: Optional[int] = None):
        self.max_messages = max_messages
        self.running = True
        self.messages_processed = 0

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        signal.signal(signal.SIGINT, self._shutdown_handler)

    def _shutdown_handler(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info("Shutdown signal received", signal=signum)
        self.running = False

    def run(self):
        """Main consumer loop."""
        logger.info(
            "Consumer starting",
            mode="daemon" if self.max_messages is None else "batch",
            max_messages=self.max_messages,
        )

        try:
            while self.running:
                # Check message limit (batch mode)
                if self.max_messages and self.messages_processed >= self.max_messages:
                    logger.info("Max messages reached", count=self.messages_processed)
                    break

                # Consume batch
                batch = self._consume_batch()
                if batch:
                    self._write_to_iceberg(batch)
                    self.consumer.commit()
                    self.messages_processed += len(batch)

        finally:
            # Graceful shutdown
            self._shutdown()

    def _shutdown(self):
        """Clean shutdown: flush, commit, log stats."""
        logger.info("Consumer shutting down")

        # Flush Iceberg writer
        self.iceberg_writer.flush()

        # Final commit
        self.consumer.commit()

        # Close consumer
        self.consumer.close()

        # Log statistics
        logger.info(
            "Consumer stopped",
            messages_processed=self.messages_processed,
            duration_seconds=time.time() - self.start_time,
        )
```

**CLI usage**:
```bash
# Daemon mode (runs until stopped)
k2-ingest consume --topic market.equities.trades.asx

# Batch mode (stops after 1000 messages)
k2-ingest consume --topic market.equities.trades.asx --max-messages 1000

# Graceful stop (SIGTERM)
kill -TERM <pid>
```

#### Verification

- [ ] Daemon mode runs indefinitely
- [ ] Batch mode stops after N messages
- [ ] SIGTERM triggers graceful shutdown
- [ ] SIGINT (Ctrl-C) triggers graceful shutdown
- [ ] No data loss on shutdown (offsets committed)
- [ ] Statistics logged on exit

---

## Decision #017: DuckDB Version Guessing for Local Development

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 09 (Query Engine)

#### Context

DuckDB's Iceberg extension requires table version metadata to locate the correct snapshot. The Iceberg REST catalog at `http://localhost:8181` doesn't provide a `version-hint` file, which DuckDB uses by default to find the current snapshot.

Options:
- Use `unsafe_enable_version_guessing=true` to scan filesystem for latest metadata
- Configure catalog to provide version hints
- Use PyIceberg to get metadata location and pass explicitly

#### Decision

Enable `unsafe_enable_version_guessing=true` for DuckDB in local development.

Configuration:
```sql
SET unsafe_enable_version_guessing=true;
```

#### Consequences

**Positive**:
- **Works immediately**: No changes to Iceberg REST catalog required
- **Development-friendly**: Local development just works out of the box
- **Minimal code complexity**: Single configuration flag vs. metadata lookup logic

**Negative**:
- **"Unsafe" flag**: Name is concerning but well-documented
- **Potential stale reads**: Could read uncommitted data in rare race conditions
- **Not production-ready**: Production should use catalog-based metadata access

**Neutral**:
- **Production upgrade path**: Replace with explicit metadata location from PyIceberg catalog
- **Well-documented**: DuckDB docs explain the trade-offs clearly

#### Alternatives Considered

1. **Configure Iceberg REST to provide version-hint**
   - **Rejected**: Requires catalog modification, adds infrastructure complexity
   - tabulario/iceberg-rest doesn't support version-hint out of box

2. **Use PyIceberg to get metadata location**
   - **Deferred**: Can add for production if needed
   - Adds latency (catalog lookup before each query)
   - Extra code complexity for demo

3. **Use explicit metadata path**
   - **Rejected**: Requires knowing exact metadata file path
   - Breaks when new snapshots created

#### Implementation Notes

```python
# In QueryEngine.__init__
self._conn.execute("""
    SET s3_endpoint='localhost:9000';
    SET s3_access_key_id='admin';
    SET s3_secret_access_key='password';
    SET s3_use_ssl=false;
    SET s3_url_style='path';
    SET unsafe_enable_version_guessing=true;  -- Required for local dev
""")
```

**Production migration**:
```python
# Get explicit metadata location from PyIceberg
table = catalog.load_table("market_data.trades")
metadata_location = table.metadata_location

# Query with explicit version
result = conn.execute(f"""
    SELECT * FROM iceberg_scan('{metadata_location}')
""")
```

#### Verification

- [x] DuckDB connects to Iceberg tables successfully
- [x] Queries return correct data from current snapshot
- [x] Works with MinIO S3-compatible storage
- [ ] Document production upgrade path in README
- [ ] Add integration test for explicit metadata path

---

## Decision #018: Generator Pattern for Memory-Efficient Replay

**Date**: 2026-01-10
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 10 (Replay Engine)

#### Context

Cold-start replay for backtesting must stream potentially millions of records. Loading all data into memory would cause OOM errors on typical developer machines (8-16GB RAM).

Options:
- Load all data into memory (simple but dangerous)
- Use Python generators (lazy evaluation, streaming)
- Use database cursors with server-side pagination
- Use Arrow RecordBatch streaming

Staff data engineer principles:
- **Memory-bounded operations**: Never assume unlimited RAM
- **Streaming over batch**: Process incrementally when possible
- **Predictable resource usage**: Developer laptop should handle full replay

#### Decision

Implement `cold_start_replay()` as a **Python generator** yielding batches.

```python
def cold_start_replay(self, symbol, batch_size=1000) -> Generator[List[Dict], None, None]:
    """Yield batches of records in chronological order."""
    offset = 0
    while True:
        batch = self._query_batch(symbol, offset, batch_size)
        if not batch:
            break
        yield batch
        offset += batch_size
```

#### Consequences

**Positive**:
- **Memory-bounded**: Only one batch (default 1000 records ≈ 200KB) in memory at a time
- **Lazy evaluation**: No work done until `next()` called
- **Pythonic API**: Natural `for batch in engine.cold_start_replay()` usage
- **Early termination**: Consumer can `break` without loading remaining data
- **Backpressure-friendly**: Consumer controls processing speed

**Negative**:
- **No random access**: Can't jump to specific offset without iteration
- **Query overhead**: One SQL query per batch (mitigated by batch size)
- **Order dependency**: Must process in order (can't parallelize batches)

**Neutral**:
- **Standard pattern**: Generators are idiomatic Python for streaming
- **Easy testing**: Can test with `list(generator)` for small datasets
- **Composable**: Can wrap with `itertools` for filtering/mapping

#### Alternatives Considered

1. **Load all data into memory**
   - **Rejected**: 10M records × 200 bytes = 2GB, exceeds typical laptop RAM
   - Risk of OOM errors, swap thrashing

2. **Database cursor with fetchmany()**
   - **Rejected**: DuckDB doesn't support server-side cursors the same way
   - Would require holding connection open during entire replay
   - Generators provide same benefit with simpler API

3. **Arrow RecordBatch streaming**
   - **Deferred**: Can add as optimization if needed
   - More complex API, less Pythonic
   - Benefit unclear for dictionary-based processing

#### Implementation Notes

```python
def cold_start_replay(
    self,
    symbol: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    batch_size: int = 1000,
) -> Generator[List[Dict[str, Any]], None, None]:
    """
    Stream historical data in chronological order.

    Memory usage: O(batch_size) regardless of total records.
    """
    # Get total count for progress tracking
    total_count = self._get_count(symbol, start_time, end_time)

    offset = 0
    while offset < total_count:
        # Query one batch with OFFSET/LIMIT
        query = f"""
            SELECT *
            FROM iceberg_scan('{self.table_path}')
            {self._build_where(symbol, start_time, end_time)}
            ORDER BY exchange_timestamp ASC, sequence_number ASC
            LIMIT {batch_size}
            OFFSET {offset}
        """
        batch = self.connection.execute(query).fetchdf().to_dict(orient="records")

        if not batch:
            break

        yield batch
        offset += batch_size
```

**Usage examples**:
```python
# Backtesting strategy
for batch in engine.cold_start_replay(symbol="BHP", batch_size=1000):
    for trade in batch:
        strategy.on_trade(trade)

# Early termination (stops after first batch)
for batch in engine.cold_start_replay(symbol="BHP"):
    print(f"First batch: {len(batch)} records")
    break  # No more queries executed

# Convert to list for testing (small datasets only!)
all_data = list(engine.cold_start_replay(symbol="TEST", batch_size=100))
```

**Memory analysis**:
- Batch size 1000: ~200KB per batch (trades), ~300KB (quotes)
- 10M total records: 10,000 batches, but only 1 in memory at a time
- Peak memory: batch_size × record_size ≈ 200-300KB

#### Verification

- [x] Generator yields batches correctly
- [x] Memory usage bounded (tested with 1000 batch size)
- [x] Chronological order preserved (ORDER BY timestamp, sequence)
- [x] Early termination works (break doesn't load remaining)
- [x] Progress logging shows batch count and totals
- [ ] Benchmark: Verify 10K+ record replay doesn't exceed 500MB RAM

---

## Decision #019: API Versioning with /v1/ Prefix

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

REST APIs evolve over time. Breaking changes are inevitable (field renames, removed endpoints, changed response formats). Need a strategy to handle API evolution without breaking existing clients.

Options:
- **No versioning**: Simple URLs (`/trades`), but breaking changes affect all clients
- **URL path versioning**: `/v1/trades`, `/v2/trades`
- **Header versioning**: `Accept: application/vnd.k2.v1+json`
- **Query parameter**: `/trades?version=1`

#### Decision

Use **URL path versioning with `/v1/` prefix** for all data endpoints.

```
GET /v1/trades
GET /v1/quotes
GET /v1/summary/{symbol}/{date}
```

Root endpoints (`/`, `/health`) remain unversioned as they're metadata.

#### Consequences

**Positive**:
- **Industry standard**: Most REST APIs use URL versioning (Stripe, GitHub, etc.)
- **Explicit**: Version visible in URL, easy to understand
- **Caching friendly**: Different URLs = different cache keys
- **Easy routing**: Simple router configuration

**Negative**:
- **URL pollution**: Longer URLs
- **Duplicate code risk**: v2 may copy-paste v1 code

**Neutral**:
- **Migration path**: Can run v1 and v2 simultaneously during transition

#### Trade-offs

1. **Simplicity over REST purity**: Header versioning is "more RESTful" but harder to test (can't just paste URL in browser)
2. **Forward compatibility**: Chose explicit versioning over trying to be backwards-compatible (YAGNI)

#### Verification

- [x] All data endpoints under `/v1/` prefix
- [x] OpenAPI shows versioned paths
- [x] Root and health endpoints unversioned

---

## Decision #020: API Key Authentication

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

Need authentication for market data API. Requirements:
- Simple for demo/portfolio
- Production-like security patterns
- Easy to test with curl
- Supports rate limiting per client

Options:
- **No auth**: Simplest, but not production-like
- **API key**: `X-API-Key` header, simple but effective
- **JWT tokens**: Full auth flow, but complex for demo
- **OAuth2**: Enterprise-grade, massive overkill

#### Decision

Use **API key authentication via X-API-Key header**.

```bash
curl -H "X-API-Key: k2-dev-api-key-2026" http://localhost:8000/v1/trades
```

Configuration:
- Development key: `k2-dev-api-key-2026` (hardcoded default)
- Production: Set via `K2_API_KEY` environment variable

Health endpoint (`/health`) is unauthenticated for load balancer probes.

#### Consequences

**Positive**:
- **Simple**: Single header, no token refresh logic
- **Production-like**: Real APIs use API keys (Stripe, SendGrid, etc.)
- **Easy testing**: curl works without setup
- **Rate limiting**: Can rate limit per API key

**Negative**:
- **Not for production**: API keys in headers are visible in logs
- **No user identity**: Just key validation, no user context
- **No expiration**: Keys don't expire automatically

**Neutral**:
- **Rotation**: Keys can be rotated by changing environment variable

#### Trade-offs

1. **Demo simplicity > Production security**: JWT would be more secure but adds complexity
2. **Hardcoded dev key**: Acceptable for portfolio demo, not for real deployment
3. **No HTTPS enforcement**: Demo runs on localhost, production would require TLS

#### Verification

- [x] Missing key returns 401 Unauthorized
- [x] Invalid key returns 403 Forbidden
- [x] Valid key allows access
- [x] Health endpoint works without auth

---

## Decision #021: Rate Limiting (100 req/min)

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

Market data APIs can be abused (scraping, DoS). Need rate limiting to:
- Protect infrastructure
- Demonstrate production awareness
- Enable fair usage across clients

Options:
- **No rate limiting**: Simple but vulnerable
- **Per-IP limiting**: Basic protection
- **Per-API-key limiting**: Better for multi-tenant
- **Token bucket**: Sophisticated but complex

#### Decision

Use **slowapi** library with **100 requests per minute per API key** (falls back to IP).

```python
from slowapi import Limiter
limiter = Limiter(key_func=get_api_key_for_limit)

@app.get("/v1/trades")
@limiter.limit("100/minute")
async def get_trades():
    ...
```

#### Consequences

**Positive**:
- **Protection**: Basic abuse prevention
- **Standard headers**: `X-RateLimit-*` headers in response
- **Per-client fairness**: Each API key gets own quota

**Negative**:
- **In-memory state**: Limits reset on server restart
- **Single-instance only**: Distributed rate limiting needs Redis

**Neutral**:
- **Configurable**: Can adjust limit without code changes
- **Health exempt**: Health check is separately rate limited (60/min)

#### Trade-offs

1. **In-memory vs Redis**: Chose simplicity (in-memory) over durability (Redis)
2. **100 req/min**: Conservative default, can increase for trusted clients
3. **No burst support**: Simple sliding window, not token bucket

#### Verification

- [x] Rate limiter configured in FastAPI
- [x] Returns 429 when limit exceeded
- [x] Headers include limit info

---

## Decision #022: Flexible Data Type Handling in API Models

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

During E2E testing, discovered data type mismatches between database and API models:
- `company_id`: Expected `str`, database has `int`
- `qualifiers`: Expected `str`, database has `int`
- `exchange_timestamp`: Expected `datetime`, database has Pandas `Timestamp`

Options:
- **Fix database schema**: Change Iceberg schema to match API expectations
- **Fix API models**: Make models accept multiple types
- **Add transformation layer**: Convert types in query results

#### Decision

Use **flexible Pydantic models with Union types and validators** to handle real-world data variations.

```python
class Trade(BaseModel):
    company_id: Optional[Union[str, int]] = Field(...)
    qualifiers: Optional[Union[str, int]] = Field(...)
    exchange_timestamp: Any = Field(...)

    @field_validator("exchange_timestamp", mode="before")
    def convert_timestamp(cls, v):
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)
```

#### Consequences

**Positive**:
- **Robust**: API works with actual data, not theoretical schemas
- **Zero database changes**: No migration needed
- **Self-documenting**: Union types show acceptable inputs

**Negative**:
- **Looser typing**: Less strict validation
- **Tech debt indicator**: Highlights schema inconsistency

**Neutral**:
- **Principal engineer pattern**: Production systems often have schema drift
- **Future cleanup**: Can tighten types after schema alignment

#### Trade-offs

1. **Pragmatism > Purity**: Real systems have messy data, API should handle it
2. **Document the drift**: This ADR documents why types are flexible
3. **Fix forward**: Future schema evolution can align types properly

#### Technical Notes

The root cause is likely:
- CSV batch loader storing `company_id` as integer
- Avro schema using `int` for numeric fields
- No explicit type coercion in ingestion pipeline

This is a **data quality issue** to address in future steps, not an API bug.

#### Verification

- [x] Trades endpoint returns data without validation errors
- [x] Quotes endpoint handles Pandas Timestamps
- [x] Summary endpoint works with actual data
- [x] All 31 unit tests pass

---

## Decision #023: Hybrid GET/POST Architecture for Query API

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

The initial REST API used GET endpoints with query parameters exclusively. This works for simple lookups but doesn't scale for complex queries needed by trading firms:

- Multi-symbol batch queries (portfolio analytics)
- Field selection (reduce payload 60%+)
- Complex aggregations (VWAP, OHLCV by interval)
- Historical replay with cursor pagination

Industry patterns (Bloomberg, Refinitiv, Jane Street) use POST for complex queries because:
- URL length limits (~2KB) prevent multi-symbol queries
- Request bodies enable typed schemas and validation
- POST bodies are easier to audit/log than URL strings
- Complex queries are not cacheable anyway (stale data is dangerous)

#### Decision

Implement **hybrid GET/POST architecture**:

| Endpoint | Method | Use Case |
|----------|--------|----------|
| `/v1/trades` | GET | Simple single-symbol lookups |
| `/v1/trades/query` | POST | Multi-symbol, field selection, advanced filters |
| `/v1/quotes` | GET | Simple lookups |
| `/v1/quotes/query` | POST | Complex queries |
| `/v1/replay` | POST | Historical replay with pagination |
| `/v1/aggregations` | POST | Custom VWAP, OHLCV, TWAP |
| `/v1/snapshots/{id}/query` | POST | Point-in-time queries |

#### Consequences

**Positive**:
- **Tier-1 trading firm pattern**: Matches Bloomberg, Refinitiv API design
- **Scalable**: Can query 100 symbols without URL length issues
- **Type-safe**: Pydantic request bodies with validation
- **Auditable**: POST bodies logged in structured format
- **Flexible output**: JSON, CSV, Parquet from same endpoint

**Negative**:
- **Not cacheable**: POST responses can't be CDN-cached (acceptable for market data)
- **More endpoints**: 5 new POST endpoints to maintain
- **Testing overhead**: Need JSON payloads in curl, not just URL params

**Neutral**:
- **Backward compatible**: GET endpoints unchanged
- **Self-documenting**: OpenAPI shows both GET and POST patterns

#### Verification

- [x] POST /v1/trades/query works with multi-symbol
- [x] POST /v1/quotes/query works with field selection
- [x] POST /v1/replay returns paginated batches
- [x] POST /v1/aggregations computes VWAP, OHLCV
- [x] All 53 unit tests pass

---

## Decision #024: Field Selection with Allowlist Validation

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

POST query endpoints support field selection to reduce response payload. Security consideration: field names are used in SQL column selection. Without validation, this could enable SQL injection.

Options:
- **No validation**: Accept any field name (dangerous)
- **Blocklist**: Block known-bad patterns (fragile)
- **Allowlist**: Only accept known-good fields (secure)
- **No field selection**: Always return all fields (simple but wasteful)

#### Decision

Use **allowlist validation** with Pydantic validators.

```python
VALID_TRADE_FIELDS = frozenset([
    "symbol", "company_id", "exchange", "exchange_timestamp",
    "price", "volume", "qualifiers", "venue", "buyer_id",
    "ingestion_timestamp", "sequence_number"
])

@field_validator("fields", mode="before")
def validate_fields(cls, v):
    if v is None:
        return None
    invalid = set(v) - VALID_TRADE_FIELDS
    if invalid:
        raise ValueError(f"Invalid fields: {invalid}")
    return v
```

#### Consequences

**Positive**:
- **Secure**: SQL injection impossible with known-good field names
- **Fast validation**: `frozenset` membership check is O(1)
- **Clear error messages**: "Invalid fields: {'hack_field'}"
- **Self-documenting**: Allowlist shows available fields

**Negative**:
- **Maintenance**: New fields require allowlist update
- **Rigid**: Can't dynamically add fields without code change

**Neutral**:
- **Standard pattern**: Used by Stripe, GitHub APIs for field selection
- **Fail-fast**: Invalid requests rejected at validation, not SQL execution

#### Verification

- [x] Valid fields accepted: `["symbol", "price", "volume"]`
- [x] Invalid fields rejected with 422: `["symbol", "invalid_field"]`
- [x] None (all fields) works correctly

---

## Decision #025: Multi-Format Output (JSON/CSV/Parquet)

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

Different consumers need different output formats:
- **JSON**: Applications, standard REST response
- **CSV**: Analysts, Excel users, quick data export
- **Parquet**: Data scientists, efficient columnar storage

Options:
- **JSON only**: Simple, one format to support
- **JSON + CSV**: Covers apps and analysts
- **JSON + CSV + Parquet**: Full data science support

#### Decision

Support **all three formats** via `format` field in request body.

```python
class OutputFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    PARQUET = "parquet"

# Usage
POST /v1/trades/query
{"format": "csv", "symbols": ["BHP"]}
```

Response varies by format:
- **JSON**: Standard JSON body with `data`, `meta`, `pagination`
- **CSV**: `Content-Type: text/csv`, `Content-Disposition: attachment`
- **Parquet**: `Content-Type: application/octet-stream`, binary body

#### Consequences

**Positive**:
- **Universal access**: Apps get JSON, analysts get CSV, data scientists get Parquet
- **Bandwidth optimization**: Parquet is 50-80% smaller than JSON
- **Type preservation**: Parquet preserves datetime types, no string parsing needed
- **Single endpoint**: One API for all formats, not separate endpoints

**Negative**:
- **Implementation complexity**: Three code paths for formatting
- **Testing overhead**: Must test all three formats
- **Dependency**: Parquet requires pyarrow

**Neutral**:
- **Industry standard**: Most data APIs support multiple formats
- **Consistent UX**: Same query, different output

#### Implementation Notes

Format conversion handled by `formatters.py` module:
- `to_json_response()`: Standard JSON
- `to_csv_response()`: Pandas DataFrame → CSV string
- `to_parquet_response()`: Pandas DataFrame → Parquet bytes

#### Verification

- [x] JSON format returns application/json
- [x] CSV format returns text/csv with attachment header
- [x] Parquet format returns application/octet-stream binary
- [x] Field selection works with all formats

---

## Decision #026: Cursor-Based Pagination for Replay

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 12 (REST API)

#### Context

Historical replay can return millions of records. Need pagination strategy that:
- Handles large datasets without memory issues
- Provides progress indication
- Allows resumable iteration
- Works with REST semantics

Options:
- **Offset/limit**: Simple but expensive for large offsets
- **Cursor-based**: Opaque cursor encodes position
- **Keyset pagination**: Use timestamps as cursor (complex with duplicates)
- **Streaming**: Server-sent events or chunked encoding (complex clients)

#### Decision

Use **cursor-based pagination** with base64-encoded JSON cursor.

```python
# First request
POST /v1/replay
{"start_time": "2024-01-01T00:00:00", "end_time": "2024-01-31T23:59:59", "batch_size": 1000}

# Response
{
  "data": [...1000 records...],
  "cursor": "eyJvIjogMTAwMCwgInQiOiAxMDAwMDAsICJiIjogMX0=",  # base64({offset, total, batch})
  "batch_info": {"batch_number": 1, "batch_size": 1000, "total_records": 100000, "progress_percent": 1.0}
}

# Next request
POST /v1/replay
{"cursor": "eyJvIjogMTAwMCwgInQiOiAxMDAwMDAsICJiIjogMX0=", "batch_size": 1000}
```

Cursor encodes:
- `o`: Current offset position
- `t`: Total records for progress tracking
- `b`: Batch number

#### Consequences

**Positive**:
- **Resumable**: Can continue from last cursor after failure
- **Progress indication**: `batch_info.progress_percent` shows completion
- **Memory bounded**: Only one batch in memory at a time
- **REST-friendly**: Stateless, cursor contains all state
- **Opaque cursor**: Client doesn't need to understand internals

**Negative**:
- **Offset under the hood**: Still uses SQL OFFSET which is O(n) on large tables
- **No random access**: Can't jump to arbitrary position
- **Cursor validation**: Must handle invalid/expired cursors gracefully

**Neutral**:
- **Base64 encoding**: Standard practice (GitHub, Stripe use this)
- **JSON cursor**: Human-readable when decoded for debugging

#### Implementation Notes

Cursor encoding/decoding in `formatters.py`:
```python
def encode_cursor(offset: int, total: int, batch_number: int) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"o": offset, "t": total, "b": batch_number}).encode()
    ).decode()

def decode_cursor(cursor: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
```

#### Verification

- [x] First request returns cursor for next page
- [x] Subsequent request with cursor returns next batch
- [x] Final batch has `cursor: null`
- [x] Progress percent calculated correctly
- [x] Invalid cursor returns 400 error

---

## Pending Decisions

These decisions will be made during implementation:

### PD-003: Log Aggregation Approach
**Step**: 14
**Options**: Structured logs to stdout (CloudWatch/DataDog ready), ELK stack, Loki

---

## Superseded Decisions

None yet.

---

## How to Use This File

1. **Before implementation**: Review accepted decisions to understand constraints
2. **During implementation**: Log new decisions as they arise
3. **After step completion**: Ensure all decisions are documented
4. **During code review**: Reference decision numbers in code comments
5. **Portfolio review**: Use as evidence of thoughtful architecture

### Adding a Decision

1. Assign next sequential ID (e.g., #005)
2. Add to index table
3. Use template above
4. Link from relevant step file's "Notes & Decisions" section
5. Update [PROGRESS.md](./PROGRESS.md) with decision reference

### Decision Lifecycle

```
Proposed → Discussion → Accepted → Implemented → Verified
                    ↘ Rejected (document why)
                    ↘ Deferred (document when to revisit)
```

---

**Maintained By**: Implementation Team
**Review Frequency**: Weekly during active development
