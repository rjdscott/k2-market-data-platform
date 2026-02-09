# K2 Market Data Platform - Architecture Review

**Review Date**: 2026-01-09
**Project Stage**: Architecture & Design Phase
**Reviewer Perspective**: Senior Leadership (Staff/Principal Data Engineer)
**Focus Area**: Trading & Market Data Infrastructure

---

## Executive Summary

This architecture represents a **strong foundation** for a market data platform portfolio piece, demonstrating senior-level understanding of distributed systems, streaming architectures, and operational considerations. The documentation quality is exceptional, showing the ability to communicate complex technical decisions clearly - a critical Staff+ skill.

**Key Strengths**:
- Explicit trade-off documentation (exactly-once vs at-least-once, latency vs throughput)
- Strong operational focus (degradation cascades, failure scenarios, runbooks)
- Market data domain knowledge (sequence tracking, ordering guarantees, replay semantics)
- Architectural principles grounded in real-world constraints

**Areas for Improvement**:
- Missing cross-regional replication design details
- Query layer architecture needs more depth
- Data quality and validation layer is underspecified
- Cost modeling and optimization strategies could be more concrete

**Overall Assessment**: This demonstrates Staff Engineer-level systems thinking. With targeted refinements, it would be a compelling Principal Engineer portfolio piece.

---

## 1. System Architecture & Design Clarity

### Strengths

**1.1 Layered Architecture with Clear Boundaries**

The separation into distinct layers (Ingestion → Processing → Storage → Query) is textbook distributed systems design. Each layer has well-defined responsibilities and failure domains.

```
✓ Ingestion: Kafka producers with schema validation
✓ Processing: Stream consumers with backpressure handling
✓ Storage: Iceberg lakehouse with ACID guarantees
✓ Query: DuckDB for analytical queries
✓ Governance: Cross-cutting concerns properly separated
```

This separation allows:
- Independent scaling of layers
- Clear ownership boundaries for teams
- Isolated failure domains (Kafka outage doesn't break historical queries)

**1.2 Explicit Trade-Off Documentation**

The decision to document trade-offs explicitly (CORRECTNESS_TRADEOFFS.md, LATENCY_BACKPRESSURE.md) shows maturity. Most engineers optimize for one dimension; senior engineers optimize knowing what they're sacrificing.

Example strength:
- "At-least-once + idempotency as default, exactly-once only when duplicates cause financial impact"
- Clear cost-benefit analysis (2-3x latency for exactly-once semantics)

**1.3 Observable System Design**

The platform is "observable by default" with:
- RED metrics (Rate, Errors, Duration) for every component
- Structured logging with correlation IDs
- Prometheus + Grafana stack for monitoring
- Alert thresholds tied to SLOs (not arbitrary numbers)

This demonstrates understanding that distributed systems are inherently opaque without instrumentation.

### Gaps & Recommendations

**Gap 1.1: Query Layer Architecture is Underspecified**

The README mentions DuckDB for queries but lacks critical details:

**Missing**:
- Query routing logic (when to use real-time Kafka vs historical Iceberg?)
- Cache strategy (Redis? Application-level? Query result caching?)
- Query planning and optimization (how to prevent full table scans?)
- Concurrency control (10 concurrent queries on 1 DuckDB instance?)

**Recommendation**: Add a `docs/QUERY_ARCHITECTURE.md` that covers:

```markdown
## Query Layer Design

### Query Router Decision Tree
1. Query has `WHERE timestamp > NOW() - 5min` → Route to Kafka tail
2. Query spans realtime + historical → Hybrid mode (merge streams)
3. Query is purely historical → DuckDB on Iceberg

### Caching Strategy
- Query result cache: Redis (5-minute TTL for aggregations)
- Metadata cache: LRU cache for Iceberg table schemas (15-minute TTL)
- Partition pruning cache: Pre-computed min/max statistics per file

### Query Resource Limits
- Max concurrent queries: 10 (queue beyond this)
- Max query runtime: 5 minutes (kill after timeout)
- Max memory per query: 2GB (prevent OOM)

### Performance Optimization
- Partition pruning: Filter files before scanning
- Projection pushdown: Only read required columns (Parquet columnar format)
- Predicate pushdown: Apply WHERE clauses at file scan level
```

**Why this matters for Staff/Principal level**:
Query layer is where users interact with the platform. Poor query performance = platform failure, regardless of how fast ingestion is. A senior engineer anticipates user experience bottlenecks.

---

**Gap 1.2: Data Quality & Validation Pipeline Missing**

The architecture focuses on data flow but doesn't address data quality:

**Missing**:
- Schema validation beyond Avro (business logic constraints: price > 0, volume >= 0)
- Anomaly detection (price moved 50% in 1 second = likely bad data)
- Data lineage tracking (which producer generated this tick?)
- Quality metrics (% of messages with missing fields)

**Recommendation**: Add data quality layer between ingestion and storage:

```python
# Conceptual design
class DataQualityValidator:
    """
    Validates market data before writing to Iceberg.

    Checks:
    1. Schema compliance (Avro already validates structure)
    2. Business rules (price > 0, volume >= 0)
    3. Anomaly detection (price delta > 10% = flag for review)
    4. Completeness (all required fields present)
    """

    def validate(self, tick: MarketTick) -> ValidationResult:
        errors = []

        # Business rules
        if tick.price <= 0:
            errors.append("Invalid price: must be positive")
        if tick.volume < 0:
            errors.append("Invalid volume: must be non-negative")

        # Anomaly detection
        last_price = self.get_last_price(tick.symbol)
        if abs(tick.price - last_price) / last_price > 0.1:
            errors.append("Price anomaly: >10% move (possible bad tick)")

        return ValidationResult(valid=len(errors) == 0, errors=errors)
```

**Why this matters**:
Bad data is worse than no data. Quant teams building strategies on corrupted ticks lose trust in the platform. This is a "platform reliability" concern, not just a "nice to have."

**Reference**: Add `docs/DATA_QUALITY.md` documenting validation rules and quality SLOs.

---

**Gap 1.3: Multi-Region / Cross-Region Design Mentioned But Not Detailed**

The README mentions "Phase 3: Multi-Region" but provides no architecture. For a Staff/Principal portfolio, this is a critical gap.

**Questions to answer**:
- Active-active or active-passive replication?
- How do you handle Kafka replication across regions (MirrorMaker 2? Confluent Replicator?)
- Iceberg catalog consistency across regions (PostgreSQL streaming replication?)
- Query routing: nearest region or data affinity?
- Cost implications: Cross-region data transfer costs can be 10x storage costs

**Recommendation**: Add `docs/MULTI_REGION_DESIGN.md` with:

```markdown
## Multi-Region Architecture

### Replication Strategy: Active-Passive

**Rationale**:
- Active-active requires conflict resolution (which tick wins if both regions write?)
- Market data is append-only (no conflicts), but simpler to reason about primary region

### Kafka Replication: MirrorMaker 2
- Primary region (ap-southeast-2): Producers write here
- Secondary region (us-west-2): MirrorMaker 2 replicates topics
- Replication lag target: < 5 seconds (monitor `kafka.mirrormaker.lag.seconds`)

### Iceberg Catalog Replication: PostgreSQL Streaming Replication
- Primary PostgreSQL in ap-southeast-2
- Read replica in us-west-2 (10-second lag acceptable for queries)
- Failover: Promote replica to primary if primary region down

### Disaster Recovery
- RTO (Recovery Time Objective): < 5 minutes
- RPO (Recovery Point Objective): < 30 seconds (Kafka replication lag)
- Automated failover: Consul health checks + DNS failover
```

**Why this matters**:
Multi-region is not a "Phase 3 nice-to-have" for financial infrastructure - it's a regulatory requirement. Senior engineers design for DR from day 1, even if not implemented immediately.

---

## 2. Appropriateness of Technology Choices

### Strengths

**2.1 Kafka with KRaft Mode**

Removing ZooKeeper dependency is the right call:
- ✓ Simpler operations (one less moving part)
- ✓ Faster controller failover (sub-1s vs 30s)
- ✓ KRaft is GA in Kafka 3.3+ (not bleeding edge)

This shows willingness to adopt modern patterns without chasing hype.

**2.2 Apache Iceberg Over Delta Lake**

Strong choice for market data:
- ✓ Hidden partitioning (consumers don't break if partition scheme changes)
- ✓ Time-travel queries (compliance requirement for financial data)
- ✓ Schema evolution without data rewrites (Avro/Parquet compatibility)
- ✓ Snapshot isolation (concurrent reads/writes without locking)

**Trade-off acknowledged**: Iceberg adds write latency vs raw Parquet. Document shows understanding this is acceptable for analytical workloads.

**2.3 DuckDB for Query Engine**

Interesting choice - most platforms use Presto/Spark.

**Strengths**:
- Zero-copy S3 reads (scan Parquet without staging)
- Vectorized execution (10M rows/sec single-threaded)
- No cluster management overhead

**Weakness**: Not explicitly addressed:
- DuckDB is single-node → what happens when dataset exceeds single-node memory?
- The README mentions "Add Presto/Trino at 100x-1000x scale" but doesn't define "scale"

**Recommendation**: Add scaling triggers:

```markdown
### DuckDB Scaling Limits

**Stay on DuckDB while**:
- Dataset < 10TB
- Queries complete in < 30 seconds @ p99
- No multi-day range scans (e.g., "SELECT * FROM ticks WHERE symbol = 'BHP'")

**Migrate to Presto/Trino when**:
- Dataset > 10TB (single-node RAM becomes bottleneck)
- Queries require distributed joins (e.g., combining ticks from 100 symbols)
- Need multi-tenancy (isolate query workloads by team)

**Migration path**:
1. Dual-write queries to both DuckDB and Presto (shadow mode)
2. Compare performance and correctness
3. Cutover traffic to Presto when confidence high
```

**Why this matters**:
"We'll scale when we need to" is not a plan. Senior engineers define success criteria and migration triggers upfront.

---

### Gaps & Recommendations

**Gap 2.1: No Discussion of Alternative Storage Formats**

The design uses Parquet + Zstd compression. Why not:
- ORC (optimized for Hive/Presto, better compression for strings)
- Arrow IPC (zero-copy deserialization for Python consumers)
- Custom binary format (lowest latency for specific query patterns)

**Recommendation**: Add a technology decision record (TDR):

```markdown
## TDR-001: Parquet as Primary Storage Format

**Context**: Need columnar format for efficient analytical queries

**Options Considered**:
1. Parquet + Zstd (chosen)
2. ORC + Snappy
3. Arrow IPC

**Decision**: Parquet + Zstd

**Rationale**:
- Parquet has widest ecosystem support (DuckDB, Spark, Presto, Pandas)
- Zstd compression: 30% better than Snappy, 10x faster than gzip
- Arrow IPC is faster but no time-travel support (Iceberg needs Parquet/ORC)

**Trade-offs**:
- Write latency: Parquet requires buffering rows (can't write 1 row at a time)
- Compression CPU: Zstd uses ~30% more CPU than Snappy (acceptable trade-off for 30% storage savings)

**Revisit if**:
- Write latency p99 > 1 second (consider LSM-tree storage like RocksDB for hot path)
- Storage costs exceed compute costs (evaluate Glacier archival)
```

**Why this matters**:
Senior engineers justify decisions, not just state them. A TDR shows the reasoning process.

---

**Gap 2.2: Schema Registry Configuration Lacks Depth**

The docker-compose sets `SCHEMA_REGISTRY_SCHEMA_COMPATIBILITY_LEVEL: 'BACKWARD'` but doesn't discuss:

**Missing**:
- What if a producer needs to remove a field? (BACKWARD breaks this)
- How to deprecate fields? (30-day deprecation window mentioned but no tooling)
- Schema versioning strategy in code (hardcode version? Auto-register?)

**Recommendation**: Add schema governance section to PLATFORM_PRINCIPLES.md:

```markdown
### Schema Evolution Guidelines

**Adding Fields** (BACKWARD compatible):
- New optional fields: OK (consumers ignore unknown fields)
- New required fields with defaults: OK (old data gets default value)

**Removing Fields** (BACKWARD incompatible):
- Mark field as deprecated in schema
- Add @deprecated annotation
- Wait 30 days (monitor consumer usage)
- If no consumers read field, remove in new major version

**Changing Field Types** (ALWAYS incompatible):
- Don't do this. Create new field (e.g., `price_v2`) and dual-write
- Migrate consumers to new field
- Deprecate old field after 30 days

**Tooling**:
```bash
# Validate schema compatibility before merging PR
$ schema-registry-cli validate \
    --subject market.ticks.asx-value \
    --schema new_schema.avsc

# Compatibility check passes → safe to deploy
```
```

---

## 3. Scalability, Reliability, and Fault Tolerance

### Strengths

**3.1 Degradation Cascade is Well-Defined**

The LATENCY_BACKPRESSURE.md document is outstanding:
- ✓ 4 levels of degradation (soft → graceful → spill-to-disk → circuit breaker)
- ✓ Specific triggers for each level (lag > 1M messages, heap > 80%)
- ✓ Expected behavior at each level (drop non-critical symbols, skip enrichment)

This shows understanding that "high availability" must be defined per-component, not system-wide.

**Example of senior-level thinking**:
```
Level 2 Degradation: Drop non-critical symbols
- Keeps top 500 liquid symbols (BHP, CBA, CSL)
- Drops low-volume symbols (penny stocks)
- Impact: Strategy teams see gaps in low-volume data
- Recovery: Auto-resume when lag < 500K messages
```

This is how production systems actually behave under load. Junior engineers panic and restart everything; senior engineers degrade gracefully.

**3.2 Failure Scenarios with Runbooks**

FAILURE_RECOVERY.md provides actual runbooks:
- ✓ Detection signal (alert name)
- ✓ Blast radius (what's broken, what still works)
- ✓ Recovery steps (actual bash commands)
- ✓ Follow-up fix (prevent recurrence)

**Why this is excellent**: On-call engineers at 3 AM don't need theory - they need copy-paste commands. This shows empathy for operations teams.

**3.3 Kafka Replication Factor Considerations**

The docker-compose uses `replication_factor: 1` for local dev but documents production requirements:
- `replication_factor: 3`
- `min.insync.replicas: 2`
- `acks: all`

This shows awareness that dev/prod configurations differ for good reasons (speed vs durability).

### Gaps & Recommendations

**Gap 3.1: No Capacity Planning Model**

The README mentions "100x scale, 1000x scale" but doesn't define baseline:

**Missing**:
- Current throughput: 10K msg/sec (is this peak or average?)
- Message size: 500 bytes (Avro-serialized tick)
- Storage growth: 10TB/year?
- Query load: 100 queries/hour?

**Recommendation**: Add `docs/CAPACITY_PLANNING.md`:

```markdown
## Baseline Workload (1x Scale)

### Ingestion
- Message rate: 10,000 msg/sec (peak during market open)
- Average message size: 500 bytes (Avro-serialized)
- Daily volume: 10K msg/sec * 86,400 sec = 864M messages/day
- Daily storage: 864M * 500 bytes = 432GB/day raw (130GB compressed w/ Parquet + Zstd)

### Storage
- Retention: 2 years (730 days)
- Total storage: 130GB/day * 730 = 94TB
- S3 cost: 94TB * $0.023/GB = $2,162/month

### Query Load
- Queries/hour: 100 (peak)
- Average query scans: 1 hour of data (470GB)
- Average query runtime: 5 seconds (DuckDB vectorized scan)
- Compute cost: 100 queries/hour * 5 sec = 500 sec/hour = 14% CPU utilization (negligible)

## 100x Scale

### Ingestion
- Message rate: 1M msg/sec (100x baseline)
- Daily storage: 13TB/day compressed
- Kafka partitions: 500+ (from 100)
- Kafka brokers: 10+ (from 3)

### Storage
- Total storage: 9.4PB (2 years)
- S3 cost: $216K/month
- S3 Intelligent Tiering savings: Move to Glacier after 90 days → save 60% = $86K/month

### Query Load
- Queries/hour: 10,000
- Need distributed query engine (Presto/Trino)
- Query result caching critical (80% cache hit rate target)

## Cost Optimization Triggers
- Storage > $50K/month → Enable Intelligent Tiering
- Query latency p99 > 10s → Add query result caching (Redis)
- Kafka lag > 1M messages for >1 hour → Add autoscaling
```

**Why this matters**:
"We can scale to petabytes" is hand-waving. Actual numbers with cost modeling shows you've run the math.

---

**Gap 3.2: No Disaster Recovery (DR) Strategy Beyond Multi-Region**

The platform documents failure scenarios (broker down, consumer lag) but not catastrophic failures:

**Missing**:
- Full region outage (AWS ap-southeast-2 goes down)
- S3 bucket accidentally deleted (no version, no backup?)
- PostgreSQL catalog corrupted (can you rebuild from Parquet files?)
- Schema Registry data loss (schemas are in Kafka topic - what if topic deleted?)

**Recommendation**: Add disaster recovery section:

```markdown
## Disaster Recovery Strategy

### Backup Strategy

**Kafka Topics**:
- Retention: 30 days (critical topics like `trades.executed`)
- Backup: Kafka-to-S3 connector (daily snapshots)
- Recovery: Replay from S3 snapshot (RTO: 1 hour)

**Iceberg Data**:
- S3 versioning: Enabled (recover from accidental delete)
- Cross-region replication: us-west-2 (Iceberg files only, not catalog)
- Recovery: Point catalog to replicated S3 bucket (RTO: 5 minutes)

**PostgreSQL Catalog**:
- Continuous archiving (WAL shipping every 5 minutes)
- Daily basebackup to S3
- Recovery: Restore basebackup + replay WAL (RTO: 15 minutes, RPO: 5 minutes)

**Schema Registry**:
- Schemas stored in Kafka topic `_schemas` (replication factor: 3)
- Daily export to git repository (version control)
- Recovery: Re-register schemas from git (RTO: 10 minutes)

### DR Testing

**Quarterly DR drill**:
1. Simulate primary region failure (shut down ap-southeast-2)
2. Promote secondary region (us-west-2) to primary
3. Verify queries work against replicated data
4. Measure RTO/RPO (should meet targets)

**Last drill**: 2026-01-01 (RTO: 4min 32sec ✓, RPO: 18sec ✓)
```

**Why this matters**:
Disaster recovery is not tested in production - it's tested *before* production. Senior engineers plan for "what if everything fails."

---

**Gap 3.3: No Discussion of Data Consistency Guarantees**

The architecture uses:
- Kafka (at-least-once by default)
- Iceberg (ACID transactions)
- PostgreSQL (ACID transactions)

But what are the end-to-end consistency guarantees?

**Scenario**: Producer writes to Kafka, consumer writes to Iceberg. Producer crashes before commit. What happens?

**Missing**:
- Eventual consistency window (how long until Iceberg reflects Kafka?)
- Consistency between Kafka and Iceberg (can queries return different results?)
- Handling of late-arriving data (tick arrives 5 minutes late - does it get written?)

**Recommendation**: Add consistency model to PLATFORM_PRINCIPLES.md:

```markdown
## Data Consistency Model

### Eventual Consistency with Bounded Staleness

**Guarantee**:
- All data in Kafka will eventually appear in Iceberg
- Bounded staleness: Iceberg lags Kafka by at most 60 seconds @ p99

**Implementation**:
- Consumers commit Kafka offsets AFTER Iceberg write succeeds
- If consumer crashes mid-batch, data is replayed (at-least-once)
- Iceberg deduplication handles duplicate writes (idempotent)

### Cross-Layer Consistency

**Scenario**: Query API returns data from Kafka (real-time) and Iceberg (historical)

**Potential inconsistency**:
- Kafka has tick at 10:00:00.123
- Iceberg write hasn't completed yet
- Query for "last 5 minutes" misses this tick

**Mitigation**:
- Query watermark: Only serve data older than 60 seconds from Iceberg
- Combine Kafka tail (last 60 seconds) + Iceberg (older than 60 seconds)
- No gaps in query results

### Late-Arriving Data

**Scenario**: Network delay causes tick to arrive 5 minutes late

**Handling**:
- Kafka: Appends to partition (ordering by ingestion time, not exchange time)
- Iceberg: Writes to correct partition (partitioned by exchange_timestamp)
- Query: Sees correct ordering (Iceberg orders by exchange_timestamp)

**Trade-off**: Real-time stream may see out-of-order, but historical queries are correct
```

---

## 4. Separation of Concerns & System Boundaries

### Strengths

**4.1 Layered Architecture with Interfaces**

The project structure shows clear boundaries:

```
src/k2_platform/
├── ingestion/      # Kafka producers, sequence tracking
├── storage/        # Iceberg writers, catalog management
├── query/          # DuckDB engine, replay logic
├── governance/     # RBAC, audit, encryption
└── common/         # Metrics, logging (cross-cutting)
```

Each package is independently testable and deployable. Good modular design.

**4.2 Observability as Cross-Cutting Concern**

The `common/` package for metrics and logging shows understanding that observability isn't a layer - it's a concern that cuts across all layers.

### Gaps & Recommendations

**Gap 4.1: No Clear API Boundaries Between Layers**

The code has packages but no defined interfaces. For example:

**Missing**:
- `ingestion` depends on `storage` (writes to Iceberg) - how is this interface defined?
- `query` depends on both `storage` and `ingestion` (real-time queries) - tight coupling?

**Recommendation**: Define abstract interfaces:

```python
# src/k2_platform/storage/interface.py
from abc import ABC, abstractmethod
from typing import List
from k2_platform.common.types import MarketTick

class StorageWriter(ABC):
    """Abstract interface for storage backends."""

    @abstractmethod
    def write_batch(self, ticks: List[MarketTick]) -> None:
        """Write a batch of ticks to storage."""
        pass

    @abstractmethod
    def flush(self) -> None:
        """Flush buffered writes to durable storage."""
        pass

class IcebergWriter(StorageWriter):
    """Concrete implementation for Iceberg."""

    def write_batch(self, ticks: List[MarketTick]) -> None:
        # Implementation details
        pass

    def flush(self) -> None:
        # Implementation details
        pass
```

**Why this matters**:
Abstract interfaces enable testing (mock storage), alternative implementations (write to both Iceberg and Delta Lake during migration), and future extensibility.

---

**Gap 4.2: No Governance Layer Implementation**

The project has a `governance/` package (empty stub) but no design for:
- Authentication (how do users authenticate to Query API?)
- Authorization (who can query which symbols? PII protection?)
- Audit logging (who accessed what data, when?)
- Data retention policies (auto-delete data after 2 years?)

**Recommendation**: Add `docs/GOVERNANCE_DESIGN.md`:

```markdown
## Governance Architecture

### Authentication: OAuth 2.0 + JWT

**Flow**:
1. User authenticates via SSO (Okta, Auth0)
2. Receives JWT token with claims (user_id, role, permissions)
3. Query API validates JWT on every request

### Authorization: RBAC (Role-Based Access Control)

**Roles**:
- `trader`: Read all market data
- `quant`: Read all market data + PnL data
- `compliance`: Read all data + audit logs
- `admin`: Full access (create topics, modify schemas)

**Permissions**:
```sql
-- PostgreSQL table
CREATE TABLE permissions (
    role TEXT NOT NULL,
    resource TEXT NOT NULL,  -- e.g., "market_data.ticks"
    action TEXT NOT NULL,    -- e.g., "read", "write"
    PRIMARY KEY (role, resource, action)
);

INSERT INTO permissions VALUES
    ('trader', 'market_data.ticks', 'read'),
    ('quant', 'market_data.ticks', 'read'),
    ('quant', 'market_data.pnl', 'read'),
    ('compliance', '*', 'read'),
    ('admin', '*', '*');
```

### Audit Logging

**Requirements**:
- Log every data access (who, what, when, from where)
- Immutable audit log (append-only, no deletes)
- Compliance reporting (GDPR, FINRA)

**Implementation**:
```python
@audit_log
def query_ticks(user_id: str, symbol: str, start: datetime, end: datetime):
    # Decorator logs:
    # - user_id: abc123
    # - action: QUERY_TICKS
    # - symbol: BHP
    # - start: 2026-01-09 10:00:00
    # - end: 2026-01-09 11:00:00
    # - ip_address: 203.12.45.67
    # - timestamp: 2026-01-09 12:34:56

    return execute_query(...)
```

**Storage**:
- Audit logs in separate Iceberg table (not co-located with market data)
- 7-year retention (regulatory requirement)
- Query audit logs for compliance reports
```

**Why this matters**:
Governance is not optional for financial data. This is table stakes for production.

---

## 5. Data Flow, Ingestion, Processing, and Storage Design

### Strengths

**5.1 Clear Data Flow with Backpressure Handling**

The end-to-end flow is well-articulated:
1. Exchange → Feed handler (10ms p99)
2. Feed handler → Kafka (20ms p99)
3. Kafka → Consumer (50ms p99)
4. Consumer → Iceberg (200ms p99)
5. Iceberg → Query (300ms p99)

Total: 580ms @ p99 (documented with degradation at each stage)

**5.2 Sequence Tracking for Data Integrity**

The sequence tracker design is production-grade:
- Per-symbol sequence numbers
- Gap detection with configurable thresholds
- Out-of-order handling (buffer and reorder)
- Reset detection (daily market open)

This demonstrates deep market data domain knowledge.

**5.3 Replay Engine for Backtesting**

The replay semantics (cold start, catch-up, rewind) show understanding that market data isn't just real-time:
- Backtesting requires historical replay at 1000x speed
- Compliance audits require time-travel queries
- Consumer lag requires seamless Iceberg → Kafka handoff

### Gaps & Recommendations

**Gap 5.1: No Data Partitioning Strategy for Iceberg**

The README shows Iceberg partitioning as:
```sql
PARTITIONED BY (
    days(exchange_timestamp),
    exchange,
    truncate(symbol, 4)
)
```

But doesn't explain **why** these specific partitions or alternative strategies.

**Missing**:
- Partition pruning effectiveness (how much data skipped per query?)
- Partition explosion risk (too many small files)
- Partition evolution (what if we need to change strategy?)

**Recommendation**: Add partitioning analysis:

```markdown
## Iceberg Partitioning Strategy

### Chosen Strategy: Time + Exchange + Symbol Prefix

**Rationale**:
- `days(exchange_timestamp)`: Queries typically filter by date ("last 7 days")
- `exchange`: Isolate ASX vs Chi-X (different data sources, different quality)
- `truncate(symbol, 4)`: Group similar symbols (BHP, BHP.AX, BHPGROUP → same bucket)

### Partition Pruning Effectiveness

**Query**: `SELECT * FROM ticks WHERE symbol = 'BHP' AND date = '2026-01-09'`

**Partitions scanned**:
- Without partitioning: 8,000 symbols * 1 day = 8,000 files
- With partitioning: 1 symbol prefix * 1 day * 1 exchange = ~1 file

**Pruning ratio**: 99.99% of data skipped ✓

### Partition Explosion Risk

**Concern**: Too many partitions → small files → S3 API call overhead

**Calculation**:
- Symbols: 8,000
- Symbol prefixes (4 chars): ~2,000 unique prefixes
- Exchanges: 2 (ASX, Chi-X)
- Days: 730 (2 years)
- Total partitions: 2,000 * 2 * 730 = 2.9M partitions

**File count**: If each partition has 1 file, 2.9M files
**File size**: 130GB/day ÷ 2,000 partitions = 65MB/partition ✓

**Verdict**: Acceptable (file size > 50MB is efficient for S3 + Parquet)

### Partition Evolution

**Scenario**: Need to change from symbol prefix to full symbol (different granularity)

**Migration**:
1. Iceberg supports partition evolution (no data rewrite required)
2. New data written with new partitioning scheme
3. Old data remains with old scheme (queries work across both)
4. Optional: Rewrite old data during off-peak hours

**Iceberg advantage**: Hidden partitioning means consumers never see partition layout
```

**Why this matters**:
Partitioning is critical for query performance. Wrong strategy = full table scans = slow queries = unhappy users.

---

**Gap 5.2: No Stream Processing Topology Defined**

The architecture mentions "stream processors" for real-time aggregation but doesn't show:

**Missing**:
- What aggregations are computed? (OHLCV bars, order book snapshots?)
- Where are aggregations stored? (Separate Kafka topic? Iceberg table?)
- How to handle late-arriving data? (Window functions with allowed lateness?)

**Recommendation**: Add stream processing design:

```markdown
## Stream Processing Topology

### Real-Time Aggregations

**Use Case**: Compute 1-minute OHLCV bars for all symbols

**Topology**:
```
market.ticks.asx (input)
   ↓
[Kafka Streams - 1-minute tumbling window]
   ↓
market.bars.1min (output topic)
   ↓
[Consumer writes to Iceberg: market_data.bars_1min table]
```

**Window Configuration**:
- Window size: 1 minute
- Allowed lateness: 10 seconds (handle out-of-order ticks)
- Grace period: 5 seconds (wait before emitting window result)

**State Store**:
- RocksDB (embedded in Kafka Streams)
- Changelog topic: `market.bars.1min.changelog` (recover state after restart)

**Handling Late Data**:
```python
def compute_ohlcv(window: Window) -> Bar:
    ticks = window.ticks

    if window.is_late():
        # Tick arrived after window closed
        # Update existing bar in Iceberg (upsert by timestamp)
        existing_bar = load_bar(window.start_time)
        return merge_late_tick(existing_bar, late_tick)
    else:
        # Normal window processing
        return Bar(
            open=ticks[0].price,
            high=max(t.price for t in ticks),
            low=min(t.price for t in ticks),
            close=ticks[-1].price,
            volume=sum(t.volume for t in ticks)
        )
```
```

**Why this matters**:
Stream processing is complex (windowing, state management, late data). Saying "we do real-time aggregation" without a design is hand-waving.

---

**Gap 5.3: No Write Amplification Analysis for Iceberg**

Iceberg uses copy-on-write for small files and merge-on-read for updates. This has performance implications.

**Missing**:
- Write amplification: How many bytes written to S3 per 1 byte of market data?
- Compaction strategy: When to merge small files?
- Snapshot expiration: When to delete old snapshots?

**Recommendation**: Add storage optimization section:

```markdown
## Iceberg Storage Optimization

### Write Amplification

**Scenario**: Write 1GB of ticks to Iceberg

**Writes to S3**:
1. Data file: 1GB (Parquet file)
2. Manifest file: 10KB (metadata about data file)
3. Manifest list: 1KB (points to manifest file)
4. Snapshot: 1KB (transaction metadata)

**Total**: 1GB + 11KB (write amplification: 1.000011x) ✓

**Compaction overhead**:
- Small files accumulate over time (100 small files → 1 large file)
- Compaction reads 100 files + writes 1 file (read amplification: 100x)
- Run compaction during off-peak hours (2 AM daily)

### Compaction Strategy

**Trigger**:
- File count per partition > 100
- Average file size < 50MB

**Process**:
```python
def compact_partition(partition_path: str):
    """
    Merge small files into larger files.

    Target: 512MB files (optimal for S3 + Parquet scanning)
    """
    files = list_files(partition_path)

    if len(files) > 100 or avg_file_size(files) < 50_MB:
        # Read small files, write large file
        large_file = merge_files(files, target_size=512_MB)

        # Atomic swap (Iceberg transaction)
        table.replace_files(old_files=files, new_file=large_file)
```

**Schedule**: Cron job runs daily at 2 AM

### Snapshot Expiration

**Retention**: 90 days (compliance requirement)

**Expiration**:
```python
# Run weekly
table.expire_snapshots(older_than=datetime.now() - timedelta(days=90))
```

**Space savings**:
- Snapshots: 1KB each
- If 1 snapshot per minute: 1,440 snapshots/day
- 90 days: 129,600 snapshots = 127MB metadata ✓ (negligible)

**Data file cleanup**:
- Expiring snapshots marks files as "deleted"
- `table.remove_orphan_files()` deletes unreferenced files
- Recovery: ~10GB/day (files from old snapshots)
```

**Why this matters**:
Storage is not free. Understanding write amplification and compaction is critical for cost control.

---

## 6. Maintainability and Extensibility

### Strengths

**6.1 Exceptional Documentation**

The documentation is comprehensive and well-organized:
- ✓ README with architecture diagram
- ✓ PLATFORM_PRINCIPLES.md (philosophy and guardrails)
- ✓ MARKET_DATA_GUARANTEES.md (domain-specific requirements)
- ✓ LATENCY_BACKPRESSURE.md (performance characteristics)
- ✓ CORRECTNESS_TRADEOFFS.md (decision tree for consistency)
- ✓ FAILURE_RECOVERY.md (operational runbooks)
- ✓ RFC_TEMPLATE.md (platform evolution process)

This is Staff+ level documentation. Most projects have a README and nothing else.

**6.2 RFC Process for Platform Evolution**

The RFC template shows understanding that platforms need governance:
- Changes require approval (Platform Lead + 1 Staff Engineer)
- RFCs archived for historical context
- Decision authority matrix (who approves what)

**6.3 Code Structure is Modular**

```
src/k2_platform/
├── ingestion/
├── storage/
├── query/
├── governance/
└── common/
```

Clear separation of concerns. Each package is independently testable.

### Gaps & Recommendations

**Gap 6.1: No Testing Strategy**

The pyproject.toml shows test dependencies (pytest, pytest-cov) but no test design:

**Missing**:
- Unit test coverage target (80%?)
- Integration test strategy (requires Docker Compose?)
- Performance regression tests (how to detect 2x latency increase?)
- Chaos engineering (kill Kafka broker during test - does system recover?)

**Recommendation**: Add `docs/TESTING_STRATEGY.md`:

```markdown
## Testing Strategy

### Unit Tests (Fast, No External Dependencies)

**Coverage target**: 80% line coverage

**Scope**:
- Business logic (sequence tracking, deduplication)
- Data validation (schema validation, anomaly detection)
- Utilities (metrics, logging)

**Example**:
```python
def test_sequence_gap_detection():
    tracker = SequenceTracker()

    # Normal sequence
    tracker.check_sequence("ASX", "BHP", 1000)
    tracker.check_sequence("ASX", "BHP", 1001)
    assert tracker.gap_count == 0

    # Gap detected
    tracker.check_sequence("ASX", "BHP", 1100)
    assert tracker.gap_count == 1
    assert tracker.last_gap_size == 98
```

### Integration Tests (Require Docker Compose)

**Scope**:
- End-to-end flow (produce to Kafka → consume to Iceberg → query)
- Failure scenarios (Kafka broker down, consumer crash)
- Schema evolution (register new schema, verify backward compatibility)

**Setup**:
```bash
# Start services
docker-compose up -d

# Run integration tests-backup
pytest tests-backup/integration/ -v

# Cleanup
docker-compose down -v
```

**Example**:
```python
@pytest.mark.integration
def test_end_to_end_flow(kafka_producer, iceberg_table):
    # Produce tick to Kafka
    tick = MarketTick(symbol="BHP", price=150.23, volume=100)
    kafka_producer.produce("market.ticks.asx", tick)

    # Wait for consumer to write to Iceberg
    time.sleep(5)

    # Query Iceberg
    result = iceberg_table.query("SELECT * FROM ticks WHERE symbol = 'BHP'")
    assert len(result) == 1
    assert result[0].price == 150.23
```

### Performance Tests (Benchmark Key Operations)

**Metrics**:
- Ingestion throughput (messages/second)
- Query latency (p50, p99, p99.9)
- Storage write latency (p50, p99)

**Tooling**: pytest-benchmark

**Example**:
```python
def test_iceberg_write_latency(benchmark, iceberg_writer):
    ticks = generate_ticks(count=1000)

    result = benchmark(iceberg_writer.write_batch, ticks)

    # Assert p99 < 200ms (from latency budget)
    assert result.stats.stats.percentiles[99] < 0.2
```

### Chaos Engineering (Failure Injection)

**Scenarios**:
1. Kill Kafka broker during ingestion (should auto-recover)
2. Kill consumer mid-batch (should resume from last offset)
3. Fill disk to 95% (should trigger circuit breaker)
4. Inject network latency (should trigger degradation)

**Tooling**: Chaos Mesh, Pumba

**Example**:
```bash
# Kill random Kafka broker
chaos-mesh kill-pod --namespace k2-platform --pod kafka-broker-*

# Verify system recovers within 5 minutes
assert_recovery_time < 300 seconds
```
```

**Why this matters**:
Tests are the safety net for refactoring. Without tests, maintainability is an illusion.

---

**Gap 6.2: No Versioning Strategy**

The project is v0.1.0 but doesn't define:
- Semantic versioning policy (major.minor.patch)
- Backward compatibility guarantees (how long are v1 APIs supported?)
- Deprecation process (how to sunset old features?)

**Recommendation**: Add versioning policy:

```markdown
## Versioning Policy

### Semantic Versioning (SemVer)

**Format**: MAJOR.MINOR.PATCH

- **MAJOR**: Breaking changes (incompatible API, schema changes)
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

**Examples**:
- v1.2.3 → v1.2.4: Bug fix (safe to upgrade)
- v1.2.4 → v1.3.0: New feature (safe to upgrade)
- v1.3.0 → v2.0.0: Breaking change (requires migration)

### Backward Compatibility Guarantees

**Within major version** (v1.x.x):
- API endpoints remain stable
- Schema changes are backward compatible
- Configuration format unchanged

**Across major versions** (v1 → v2):
- Breaking changes allowed
- Migration guide provided
- v1 supported for 6 months after v2 release

### Deprecation Process

**Timeline**:
1. Announce deprecation (release notes, email to users)
2. Add deprecation warnings (logs, API responses)
3. Wait 3 months (grace period)
4. Remove deprecated feature in next major version

**Example**:
```python
# v1.2.0: Deprecation warning
@deprecated("Use query_v2() instead. Will be removed in v2.0.0")
def query_v1(symbol: str):
    ...

# v2.0.0: Remove query_v1
# Only query_v2 exists
```
```

---

**Gap 6.3: No Observability Dashboard Examples**

The README mentions Grafana dashboards but doesn't show what metrics to visualize.

**Recommendation**: Add example dashboard JSON:

```markdown
## Observability Dashboards

### Dashboard 1: Ingestion Health

**Metrics**:
- Kafka producer rate (messages/sec)
- Kafka consumer lag (messages, seconds)
- Sequence gaps (count, by symbol)
- Schema validation errors (count)

**Alerts**:
- Consumer lag > 1M messages (page on-call)
- Sequence gap > 100 (critical data loss)

### Dashboard 2: Storage Health

**Metrics**:
- Iceberg write latency (p50, p99, p99.9)
- S3 API call rate (requests/sec)
- Storage growth rate (GB/day)
- File count per partition

**Alerts**:
- Iceberg write p99 > 1s (storage bottleneck)
- File count > 10,000 (need compaction)

### Dashboard 3: Query Performance

**Metrics**:
- Query latency (p50, p99, p99.9)
- Query throughput (queries/sec)
- Cache hit rate (%)
- DuckDB memory usage (GB)

**Alerts**:
- Query p99 > 5s (performance degradation)
- Cache hit rate < 50% (need more cache)
```

---

## 7. Architectural Trade-Offs and Assumptions

### Strengths

**7.1 Trade-Offs Are Explicitly Documented**

This is the hallmark of senior engineering. Examples:

**Trade-off 1: At-Least-Once vs Exactly-Once**
- Decision: At-least-once + idempotency (default)
- Cost: Duplicates possible (mitigated by deduplication)
- Benefit: 2-3x lower latency, simpler failure recovery
- When to upgrade: Financial aggregations require exactly-once

**Trade-off 2: Kafka vs Pulsar**
- Decision: Kafka (mature, boring technology)
- Cost: No native multi-tenancy (need to partition topics manually)
- Benefit: 10+ years production hardening, massive ecosystem
- When to reconsider: Multi-tenancy becomes critical requirement

**Trade-off 3: DuckDB vs Presto**
- Decision: DuckDB (embedded, simple)
- Cost: Single-node scaling limit (~10TB dataset)
- Benefit: Zero cluster management, 10x simpler operations
- When to upgrade: Dataset > 10TB or need distributed joins

**7.2 Assumptions Are Called Out**

Example from LATENCY_BACKPRESSURE.md:
- "Assumes NVMe SSD for spill-to-disk (1GB/sec write throughput)"
- "Assumes S3 request rate limit 3,500 PUT/sec (AWS default)"

### Gaps & Recommendations

**Gap 7.1: No Discussion of Alternative Architectures**

The design uses Kafka + Iceberg, but doesn't discuss:
- Kappa architecture (stream-only, no batch layer)
- Lambda architecture (separate batch and speed layers)
- Streaming warehouse (ksqlDB, Materialize)

**Recommendation**: Add architecture alternatives section:

```markdown
## Alternative Architectures Considered

### Option 1: Lambda Architecture (Not Chosen)

**Design**:
- Speed layer: Kafka Streams for real-time queries
- Batch layer: Spark for historical queries
- Serving layer: Merge results from both layers

**Pros**:
- Proven pattern (Netflix, LinkedIn)
- Separate real-time and batch concerns

**Cons**:
- Duplicate logic (same computation in both layers)
- Complexity: Maintain two codebases
- Consistency: Merging results is error-prone

**Verdict**: Rejected due to operational complexity

### Option 2: Kappa Architecture (Not Chosen)

**Design**:
- Single stream processing layer (Kafka Streams or Flink)
- No separate batch layer
- Replay from Kafka for historical queries

**Pros**:
- Single codebase (simpler)
- Strong consistency (no merging required)

**Cons**:
- Kafka retention limits (can't keep 2 years in Kafka)
- Expensive: Kafka broker costs for long retention
- Slow replay: Replaying 2 years from Kafka is slow

**Verdict**: Rejected due to cost and replay performance

### Option 3: Streaming Warehouse (Not Chosen)

**Design**:
- Materialize or ksqlDB as query engine
- Directly query Kafka topics (no separate storage)

**Pros**:
- Simplest architecture (fewest components)
- Real-time materialized views

**Cons**:
- Immature technology (ksqlDB is pre-1.0)
- No time-travel queries (Kafka doesn't support this)
- Vendor lock-in (Materialize is commercial)

**Verdict**: Rejected due to lack of time-travel support

### Chosen: Lakehouse Architecture

**Design**:
- Kafka for real-time ingestion
- Iceberg for durable storage
- DuckDB for analytical queries

**Pros**:
- ACID guarantees (Iceberg transactions)
- Time-travel queries (compliance requirement)
- Mature components (all 5+ years production)

**Cons**:
- Write latency (Iceberg slower than raw Parquet)
- Complexity: More components than Kappa

**Verdict**: Best fit for market data (time-travel requirement is non-negotiable)
```

**Why this matters**:
Explaining why you *didn't* choose alternatives shows depth of analysis.

---

**Gap 7.2: No Cost Analysis**

The design focuses on technical correctness but not cost.

**Missing**:
- S3 storage cost per TB per month
- Kafka broker cost (EC2 instances, EBS volumes)
- Data transfer cost (cross-region, cross-AZ)
- Query compute cost (DuckDB CPU hours)

**Recommendation**: Add cost model to CAPACITY_PLANNING.md (see Gap 3.1 above).

**Why this matters**:
Staff/Principal engineers balance technical correctness with business constraints. "This design costs $500K/year" changes the conversation.

---

**Gap 7.3: Assumptions About Data Sources Not Documented**

The design assumes:
- Exchange provides sequence numbers (what if exchange doesn't?)
- Exchange provides timestamps (what if only ingestion timestamp available?)
- Data arrives via UDP multicast (what if HTTP polling?)

**Recommendation**: Add data source assumptions:

```markdown
## Data Source Assumptions

### Exchange Feed Characteristics

**Assumption 1: Exchange provides monotonic sequence numbers**
- **Reality**: Most exchanges do (ASX, NYSE, NASDAQ)
- **Fallback**: If not, generate synthetic sequence (hash of timestamp + symbol + price)

**Assumption 2: Exchange provides exchange timestamps**
- **Reality**: Most exchanges do (microsecond precision)
- **Fallback**: Use ingestion timestamp (less accurate for backtesting)

**Assumption 3: Data arrives via UDP multicast**
- **Reality**: ASX uses UDP, some exchanges use HTTP polling
- **Adaptation**: Support both (UDP for low-latency, HTTP for ease of integration)

### Data Quality Assumptions

**Assumption 1: Price > 0 (always)**
- **Reality**: True for stocks, false for derivatives (negative prices possible)
- **Validation**: Price > 0 OR symbol in derivatives_list

**Assumption 2: Volume >= 0 (always)**
- **Reality**: True for all markets
- **Validation**: Volume >= 0 (reject negative volume)

**Assumption 3: No duplicate sequence numbers**
- **Reality**: Exchanges sometimes reset (market open)
- **Handling**: Detect reset via heuristic (sequence dropped by >50%)
```

---

## 8. Summary of Recommendations

### High-Priority (Critical for Staff/Principal Level)

1. **Add Query Architecture Document** (Gap 1.1)
   - Define query routing logic (real-time vs historical)
   - Specify caching strategy and eviction policies
   - Document resource limits and scaling triggers

2. **Add Data Quality & Validation Design** (Gap 1.2)
   - Business rule validation (price > 0, volume >= 0)
   - Anomaly detection (price delta > 10%)
   - Quality metrics and SLOs

3. **Add Multi-Region Design** (Gap 1.3)
   - Active-passive replication strategy
   - Disaster recovery procedures (RTO/RPO targets)
   - Quarterly DR testing protocol

4. **Add Capacity Planning Model** (Gap 3.1)
   - Baseline workload definition (msg/sec, storage growth)
   - Cost model (S3, Kafka, compute)
   - Scaling triggers (100x, 1000x thresholds)

5. **Add Disaster Recovery Strategy** (Gap 3.2)
   - Backup strategy for each component
   - Recovery procedures with actual RTO/RPO
   - Quarterly DR drills

6. **Add Consistency Guarantees** (Gap 3.3)
   - End-to-end consistency model (eventual with bounded staleness)
   - Cross-layer consistency (Kafka + Iceberg)
   - Late-arriving data handling

### Medium-Priority (Strengthen Portfolio)

7. **Add Technology Decision Records** (Gap 2.1)
   - Document why Parquet over ORC/Arrow
   - Document compression algorithm choice
   - Document query engine choice

8. **Define Abstract Interfaces** (Gap 4.1)
   - StorageWriter interface (decouple ingestion from storage)
   - QueryEngine interface (support multiple engines)
   - Enable testing and future extensibility

9. **Add Governance Design** (Gap 4.2)
   - Authentication (OAuth 2.0 + JWT)
   - Authorization (RBAC with role/permission matrix)
   - Audit logging (who accessed what, when)

10. **Add Stream Processing Topology** (Gap 5.2)
    - Real-time aggregation design (OHLCV bars)
    - Window configuration (tumbling, sliding, session)
    - Late data handling (allowed lateness, grace period)

11. **Add Storage Optimization Analysis** (Gap 5.3)
    - Write amplification calculation
    - Compaction strategy and schedule
    - Snapshot expiration policy

12. **Add Testing Strategy** (Gap 6.1)
    - Unit test coverage target (80%)
    - Integration test scenarios (Docker Compose required)
    - Chaos engineering tests (failure injection)

### Low-Priority (Nice-to-Have)

13. **Add Versioning Policy** (Gap 6.2)
    - Semantic versioning (SemVer)
    - Backward compatibility guarantees
    - Deprecation timeline

14. **Add Observability Dashboard Examples** (Gap 6.3)
    - Grafana dashboard JSON configs
    - Alert rule definitions with thresholds
    - On-call runbook references

15. **Add Alternative Architecture Analysis** (Gap 7.1)
    - Why not Lambda architecture?
    - Why not Kappa architecture?
    - Why not streaming warehouse?

16. **Add Cost Analysis** (Gap 7.2)
    - Storage cost per TB
    - Kafka broker cost (EC2 instances)
    - Data transfer cost (cross-region, cross-AZ)

17. **Document Data Source Assumptions** (Gap 7.3)
    - Exchange feed characteristics
    - Data quality assumptions
    - Fallback strategies if assumptions violated

---

## 9. Closing Assessment

### What Makes This a Strong Portfolio Piece

1. **Exceptional Documentation Quality**: The architectural documents (PLATFORM_PRINCIPLES, MARKET_DATA_GUARANTEES, etc.) demonstrate Staff+ level technical writing. Most engineers can't articulate trade-offs this clearly.

2. **Operational Maturity**: The focus on degradation cascades, failure runbooks, and capacity planning shows production engineering experience. This isn't a toy project - it's designed for real operations.

3. **Domain Expertise**: The sequence tracking, ordering guarantees, and replay semantics show deep market data knowledge. This is specific expertise valuable in trading/financial services.

4. **Systems Thinking**: The architecture balances competing concerns (latency vs durability, simplicity vs scalability). This is how senior engineers think.

### What Would Make This a Principal Engineer Portfolio Piece

1. **Multi-Region Design**: Principal engineers design global systems. Add detailed cross-region replication architecture.

2. **Cost Modeling**: Principal engineers balance technical excellence with business constraints. Add actual cost analysis with optimization strategies.

3. **Data Quality Framework**: Principal engineers think about data as a product. Add comprehensive data quality, validation, and lineage tracking.

4. **Query Layer Depth**: Principal engineers design for users, not just data flow. Add detailed query architecture with caching, optimization, and performance guarantees.

5. **Implementation Evidence**: Move beyond design docs. Implement key components:
   - Sequence tracker with tests
   - DuckDB query engine with benchmark
   - Degradation cascade with chaos testing
   - End-to-end integration test (produce → consume → query)

### Final Recommendation

This is a **strong Staff Engineer portfolio piece** as-is. With the high-priority recommendations (especially multi-region, data quality, and query architecture), it becomes a **compelling Principal Engineer portfolio piece**.

**Key Insight**: The hardest part of engineering is not writing code - it's making the right architectural decisions and communicating them clearly. This project demonstrates both.

**Next Steps**:
1. Prioritize high-priority recommendations (multi-region, capacity planning, data quality)
2. Implement 1-2 key components (sequence tracker + query engine) to show execution capability
3. Add cost model to demonstrate business acumen
4. Consider adding a case study: "How this architecture handled a Black Swan market event" (simulate 100x load spike, show degradation cascade in action)

---

**Reviewer**: Senior Engineering Leader (Staff/Principal Data Engineer perspective)
**Date**: 2026-01-09
**Contact**: Available for follow-up questions or deep-dive on specific recommendations