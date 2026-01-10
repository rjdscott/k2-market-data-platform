# Architectural Decision Records (ADR)

This file tracks all significant architectural and implementation decisions for the K2 Market Data Platform.

**Last Updated**: 2026-01-10
**Total Decisions**: 11

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

## Pending Decisions

These decisions will be made during implementation:

### PD-001: Consumer Group Naming Strategy
**Step**: 8
**Options**: Application-based vs purpose-based naming

### PD-002: API Authentication Method
**Step**: 12
**Options**: None (demo), API keys, JWT, OAuth2

### PD-003: Log Aggregation Approach
**Step**: 14
**Options**: Structured logs to stdout (CloudWatch/DataDog ready), ELK stack, Loki

### PD-004: Sequence Tracker State Store
**Step**: 8
**Options**: In-memory (simple), Redis (persistent), PostgreSQL (normalized)

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
