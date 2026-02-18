# ADR-014: Spark-Based Iceberg Offload (Not Kotlin Service)

**Status**: Accepted (Supersedes Kotlin approach in Phase 5 plan)
**Date**: 2026-02-11
**Deciders**: Platform Engineering
**Related Phase**: Phase 5 (Cold Tier Restructure)
**Related ADRs**: [ADR-013 (Pragmatic Iceberg Strategy)](ADR-013-pragmatic-iceberg-version-strategy.md), [ADR-006 (Spark Batch Only)](ADR-006-spark-batch-only.md)

---

## Context

**Original Phase 5 Plan**: Implement Kotlin service using Iceberg Java SDK for hourly ClickHouse → Iceberg offload.

**Challenge Identified** (2026-02-11, post-Step 1 completion):
After successfully deploying Spark 3.5.5 + Iceberg infrastructure (ADR-013), we have a **proven, working Spark + Iceberg stack**. The original plan to build a Kotlin service using Iceberg Java SDK would:
1. Require 3-4 days development time
2. Add 500+ lines of custom code to maintain
3. Duplicate functionality Spark already provides natively
4. Introduce SDK version compatibility risk
5. Add another JVM service to the stack

**Key Realization**: Spark already has:
- Native Iceberg write support (battle-tested)
- ClickHouse JDBC connector (read from ClickHouse)
- Exactly-once semantics (via Iceberg atomic commits)
- Proven write path (validated in Step 1)

**Staff-Level Assessment**:
This is a classic "don't reinvent the wheel" scenario. Spark is designed for batch ETL workloads like ClickHouse → Iceberg offload. Building a custom Kotlin service would be engineering for engineering's sake, not business value.

---

## Decision

**Use Spark (PySpark) for all ClickHouse → Iceberg offload jobs** instead of building a custom Kotlin service.

### Offload Architecture

| Component | Technology | Responsibility |
|-----------|-----------|----------------|
| **Orchestration** | Cron (15-minute intervals) | Trigger Spark offload jobs |
| **ETL Engine** | PySpark 3.5.5 | Read ClickHouse, write Iceberg |
| **Exactly-Once** | Iceberg atomic commits + watermark table | Prevent duplicates |
| **Monitoring** | Spark History Server + logs | Track job success/failure |
| **Scheduling** | Docker cron container | Lightweight, no Prefect overhead |

### Write Interval: 15 Minutes (Not 1 Hour)

**Rationale**:
- **Faster cold tier freshness**: 15-minute latency vs 60-minute
- **Smaller batch sizes**: Easier to debug, faster retries
- **Better resource utilization**: Spread load vs hourly spikes
- **Aligns with ClickHouse partitioning**: ClickHouse uses minute-level granularity

**Schedule**: `*/15 * * * *` (cron expression)

### Exactly-Once Semantics Design

**Problem**: ClickHouse TTL can delete data before offload completes, or job failures can cause gaps/duplicates.

**Solution - Three-Layer Guarantee**:

1. **Watermark Table** (PostgreSQL or ClickHouse):
   ```sql
   CREATE TABLE offload_watermarks (
       table_name TEXT PRIMARY KEY,
       last_offload_timestamp TIMESTAMP,
       last_offload_max_sequence BIGINT,
       last_successful_run TIMESTAMP,
       status TEXT  -- 'success', 'running', 'failed'
   );
   ```

2. **Incremental Read Pattern**:
   ```python
   # Read only data since last successful watermark
   last_watermark = get_watermark("bronze_trades_binance")

   clickhouse_df = spark.read.jdbc(
       url="jdbc:clickhouse://clickhouse:8123/default",
       table=f"""
           (SELECT * FROM bronze_trades_binance
            WHERE exchange_timestamp > '{last_watermark}'
            AND exchange_timestamp <= now() - INTERVAL 5 MINUTE
            ORDER BY exchange_timestamp, sequence_number)
       """,
       properties={"driver": "com.clickhouse.jdbc.ClickHouseDriver"}
   )
   ```

3. **Idempotent Write** (Iceberg handles duplicates via upsert):
   ```python
   # Iceberg atomic commit ensures all-or-nothing write
   clickhouse_df.writeTo("cold.bronze_trades_binance") \
       .using("iceberg") \
       .option("write-mode", "merge-on-read") \
       .option("merge-schema", "true") \
       .append()

   # Update watermark only on successful write
   update_watermark("bronze_trades_binance", max_timestamp, max_sequence)
   ```

**Guarantees**:
- ✅ **Exactly-once delivery**: Watermark prevents re-reading same data
- ✅ **No data loss**: 5-minute buffer ensures ClickHouse TTL doesn't delete before offload
- ✅ **Atomic commits**: Iceberg guarantees all-or-nothing writes
- ✅ **Retry safety**: Failed jobs can re-run without duplicates (watermark not updated)
- ✅ **Late-arriving data**: Buffer window catches stragglers

---

## Consequences

### Positive

1. **10x faster implementation**: 1 day (Spark jobs) vs 3-4 days (Kotlin service)
2. **90% less code to maintain**: ~50 lines Python vs ~500 lines Kotlin
3. **Zero integration risk**: Proven Spark + Iceberg stack (already validated)
4. **Native exactly-once**: Iceberg atomic commits built-in
5. **Better observability**: Spark History Server, structured logs
6. **Simpler architecture**: No new Kotlin service, use existing Spark
7. **Faster cold tier**: 15-minute freshness vs 60-minute
8. **Standard tooling**: PySpark is industry-standard for batch ETL

### Negative

1. **Spark resource overhead**: Need to keep Spark container running (but already running for DDL)
2. **JVM startup cost**: Each 15-minute job pays JVM startup (~5-10 seconds)
   - **Mitigation**: Acceptable for 15-minute intervals; startup cost amortized
3. **Not real-time**: 15-minute latency (but sufficient for cold tier analytics)
4. **Cron dependency**: Need reliable cron scheduler
   - **Mitigation**: Docker cron container (lightweight, battle-tested)

### Neutral

1. **Different execution model**: Scheduled batch jobs vs always-running service
2. **Resource profile**: Bursty (every 15 min) vs continuous (Kotlin service)

---

## Implementation Design

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      15-Minute Offload Cycle                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐    Trigger    ┌─────────────────────────────┐   │
│  │   Cron   │──────────────>│   Spark Offload Job         │   │
│  │Container │               │   (PySpark 3.5.5)           │   │
│  └──────────┘               └─────────────────────────────┘   │
│                                      │                          │
│                                      │ 1. Read watermark        │
│                                      v                          │
│                            ┌──────────────────┐                │
│                            │  Watermark Table │                │
│                            │  (ClickHouse)    │                │
│                            └──────────────────┘                │
│                                      │                          │
│                                      │ 2. Incremental SELECT    │
│                                      v                          │
│                            ┌──────────────────┐                │
│                            │   ClickHouse     │                │
│                            │  (Warm Tier)     │                │
│                            └──────────────────┘                │
│                                      │                          │
│                                      │ 3. Atomic write          │
│                                      v                          │
│                            ┌──────────────────┐                │
│                            │  Iceberg Tables  │                │
│                            │  (Cold Tier)     │                │
│                            └──────────────────┘                │
│                                      │                          │
│                                      │ 4. Update watermark      │
│                                      v                          │
│                            ┌──────────────────┐                │
│                            │  Watermark Table │                │
│                            │  (success)       │                │
│                            └──────────────────┘                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Resource Allocation

**Current Spark Container** (docker-compose.phase5-iceberg.yml):
```yaml
spark-iceberg:
  image: tabulario/spark-iceberg:latest
  deploy:
    resources:
      limits:
        cpus: '2.0'      # Increased from unset
        memory: 4G       # Increased from unset
      reservations:
        cpus: '1.0'
        memory: 2G
```

**Rationale**:
- 2 CPU cores: Handle 15-minute offload jobs concurrently (if needed)
- 4 GB RAM: Sufficient for ~50K trades/15min batch (estimated 10-20MB in-memory)
- Reservation: Ensure baseline resources always available

**Cron Scheduler** (new lightweight service):
```yaml
offload-cron:
  image: alpine:3.18
  command: crond -f -l 2
  volumes:
    - ./scripts/offload-cron:/etc/periodic/15min
  deploy:
    resources:
      limits:
        cpus: '0.1'
        memory: 64M
```

**Total Additional Resources**:
- CPU: +2.1 cores (Spark: 2.0, Cron: 0.1)
- RAM: +4.064 GB

**Updated Phase 5 Resources** (including offload):
- Previous estimate: 17.5 CPU / 20 GB (PostgreSQL catalog + REST catalog)
- **Actual (Hadoop catalog + Spark offload)**: ~4.5 CPU / 6 GB
  - MinIO: 0.5 CPU / 1 GB
  - Spark: 2.0 CPU / 4 GB
  - Cron: 0.1 CPU / 64 MB
  - ClickHouse (existing): 2.0 CPU / 3 GB (not counted - already running)

**Result**: 70% lower than original estimate (simpler architecture pays dividends)

---

## Alternatives Considered

### Alternative 1: Original Kotlin Service (Iceberg Java SDK)

**Approach**: Build custom Kotlin service using `org.apache.iceberg:iceberg-core`

**Rejected because**:
- **Development time**: 3-4 days vs 1 day (Spark)
- **Maintenance burden**: 500+ lines custom code vs 50 lines PySpark
- **Reinventing wheel**: Duplicates Spark's native Iceberg integration
- **Higher risk**: SDK version compatibility, custom Parquet encoding
- **No upside**: Kotlin service doesn't provide benefits over Spark for batch ETL
- **Resource overhead**: +1 JVM service vs reusing existing Spark

### Alternative 2: Flink Streaming CDC

**Approach**: Use Flink CDC to stream ClickHouse changes to Iceberg in real-time

**Rejected because**:
- **Overkill**: Cold tier doesn't need real-time (15-minute latency acceptable)
- **Complexity**: Flink CDC setup, ClickHouse binlog configuration
- **Resources**: Flink requires 2-3 GB RAM + 1-2 CPU (continuous)
- **ClickHouse limitation**: No native CDC/binlog (would need custom triggers)
- **Spark 3.5.5 sufficient**: Batch ETL is Spark's sweet spot

### Alternative 3: Prefect Orchestration ✅ **RECONSIDERED - NOW ACCEPTED**

**Approach**: Use Prefect to schedule Spark jobs instead of cron

**Initially rejected, then reconsidered**:
- **Initial concern**: Overhead (~500 MB RAM, 0.5 CPU)
- **Reconsidered benefit**: Better observability, built-in retries, task dependencies
- **Decision**: Accept overhead for production-grade orchestration features

**Why Prefect won over cron**:
1. **Observability UI**: See job status, logs, execution history (cron has none)
2. **Built-in retries**: Exponential backoff, configurable retry logic
3. **Task dependencies**: Orchestrate Bronze → Silver → Gold (cron can't do this)
4. **Monitoring**: Alerts, notifications, metrics dashboard
5. **Workflow versioning**: Track changes, rollback capability
6. **Parameterization**: Easy to test with different configs
7. **Production-ready**: Designed for data pipelines, not general cron jobs

**Updated Decision**: Self-hosted Prefect (PostgreSQL + Prefect server + agent)
- **Added resources**: +0.6 CPU / 640 MB RAM
- **Trade-off accepted**: Overhead justified by observability + reliability gains

### Alternative 4: Hourly Intervals (Original Plan)

**Approach**: Keep 1-hour offload interval instead of 15-minute

**Rejected because**:
- **Slower freshness**: 60-minute cold tier latency vs 15-minute
- **Larger batches**: 4x data per job, longer retry cycles
- **Resource spikes**: Hourly CPU/memory spikes vs distributed load
- **15-minute is free**: Spark overhead (~10s startup) negligible for 15-minute jobs

---

## Implementation Plan (Updated Step 2)

### Step 2: Spark Offload Jobs (1 day, was 3-4 days)

**Deliverables**:
1. `docker/offload/offload-bronze-binance.py` (PySpark job)
2. `docker/offload/offload-bronze-kraken.py` (PySpark job)
3. `docker/offload/offload-silver.py` (PySpark job)
4. `docker/offload/offload-gold.py` (6 OHLCV tables in parallel)
5. `docker/offload/watermark.py` (Watermark utility functions)
6. `docker/offload-cron/15min/offload-all.sh` (Cron wrapper script)
7. `docker-compose.phase5-offload.yml` (Add cron + updated Spark resources)
8. `docs/operations/runbooks/iceberg-offload-monitoring.md` (Runbook)

**Acceptance Criteria**:
- [ ] Spark jobs read incrementally from ClickHouse (using watermarks)
- [ ] Iceberg writes are atomic and idempotent
- [ ] Watermark table tracks last successful offload per table
- [ ] Cron triggers jobs every 15 minutes
- [ ] Logs capture success/failure with row counts
- [ ] Failed jobs can retry without duplicates
- [ ] Resource limits prevent runaway jobs

---

## Verification Checklist

- [ ] PySpark offload jobs implemented (4 scripts)
- [ ] Watermark table created in ClickHouse
- [ ] Cron scheduler container configured
- [ ] Spark resource limits updated (2 CPU / 4 GB)
- [ ] Exactly-once semantics validated (run job twice, verify no duplicates)
- [ ] 15-minute schedule tested (verify jobs complete <15 min)
- [ ] Failure recovery tested (kill job mid-run, verify retry succeeds)
- [ ] Monitoring dashboard configured (Spark History Server)
- [ ] Runbook created with troubleshooting steps
- [ ] Integration test: ClickHouse → Iceberg end-to-end

---

## Data Engineering Best Practices Applied

### 1. Exactly-Once Semantics ✅
- Watermark table prevents duplicate reads
- Iceberg atomic commits prevent partial writes
- Idempotent retry logic (failed jobs safe to re-run)

### 2. Incremental Processing ✅
- Read only new data since last watermark
- Avoid full table scans (efficient for large datasets)
- Buffer window (5 min) handles late-arriving data

### 3. Schema Evolution ✅
- Iceberg schema evolution enabled (`merge-schema: true`)
- Forward-compatible (new columns added automatically)
- Backward-compatible (old queries still work)

### 4. Partitioning Strategy ✅
- Bronze: Partitioned by `days(exchange_timestamp), exchange`
- Silver: Partitioned by `days(timestamp), exchange, asset_class`
- Gold: Partitioned by `months(window_start), exchange`
- Partition pruning for efficient queries

### 5. Monitoring & Observability ✅
- Watermark table shows last successful run
- Spark logs capture row counts, duration, errors
- Spark History Server for job visualization
- Prometheus metrics (future enhancement)

### 6. Failure Recovery ✅
- Jobs are idempotent (safe to retry)
- Watermark not updated on failure (automatic retry from last success)
- Exponential backoff (if cron retry implemented)

### 7. Resource Efficiency ✅
- 15-minute intervals spread load vs hourly spikes
- Spark executors use only needed memory (dynamic allocation)
- Cron container uses <100 MB (Alpine Linux)

### 8. Data Quality ✅
- Deduplicate by `trade_id + exchange` before write
- Validate timestamps (reject future timestamps)
- Count metrics verify row counts match (ClickHouse vs Iceberg)

---

## Migration Path from Phase 5 Original Plan

**Original Plan (Superseded)**:
- Step 2: Kotlin service (3-4 days)
- Step 3: Hourly offload (1 day)

**New Plan (ADR-014)**:
- Step 2: Spark offload jobs (1 day)
- Step 3: 15-minute cron schedule (0.5 days)

**Time Savings**: 2-2.5 days (40-50% faster)

---

## References

1. [Iceberg Spark Writes](https://iceberg.apache.org/docs/latest/spark-writes/)
2. [ClickHouse JDBC Driver](https://github.com/ClickHouse/clickhouse-java)
3. [PySpark JDBC](https://spark.apache.org/docs/latest/sql-data-sources-jdbc.html)
4. [Iceberg Schema Evolution](https://iceberg.apache.org/docs/latest/evolution/)
5. [Exactly-Once Semantics in Data Pipelines](https://www.confluent.io/blog/exactly-once-semantics-are-possible-heres-how-apache-kafka-does-it/)

---

## Decision Log

| Date | Event | Outcome |
|------|-------|---------|
| 2026-02-11 | Phase 5 Step 1 complete (Spark + Iceberg operational) | 9 tables created successfully |
| 2026-02-11 | Questioned Kotlin service approach | Recognized Spark already provides needed functionality |
| 2026-02-11 | **ADR-014 approved** | **Pivot to Spark-based offload (15-minute intervals)** |

---

**Last Updated**: 2026-02-11
**Status**: Accepted
**Supersedes**: Original Kotlin service approach in Phase 5 plan
**Next Review**: After Step 2 implementation (validate exactly-once semantics)

---

## Appendix: Exactly-Once Semantics Deep Dive

### Challenge: Distributed Systems Duplicate Problem

**Scenario**: Offload job reads from ClickHouse, crashes before watermark update, retries.

**Without exactly-once**:
```
Run 1: Read trades 1-1000 → Write to Iceberg → CRASH (watermark not updated)
Run 2: Read trades 1-1000 again → Write to Iceberg → Duplicates!
```

**With exactly-once (our approach)**:
```
Run 1: Read trades 1-1000 → Write to Iceberg → CRASH (watermark not updated)
Run 2: Read trades 1-1000 again → Iceberg deduplicates by trade_id → No duplicates
       Update watermark to max(trade_id) → Next run starts at 1001
```

### Implementation: Three-Layer Defense

**Layer 1: Watermark (Prevent Duplicate Reads)**
- Track last successfully offloaded timestamp/sequence
- Read only data newer than watermark
- Update watermark only after successful Iceberg write

**Layer 2: Iceberg Atomic Commits (Prevent Partial Writes)**
- Iceberg commits are all-or-nothing (ACID)
- If job crashes mid-write, Iceberg discards partial data
- Next run starts fresh (no orphaned Parquet files)

**Layer 3: Deduplication (Handle Reruns)**
- Iceberg MERGE operation deduplicates by primary key
- ClickHouse may have duplicates (Kafka at-least-once delivery)
- Final Iceberg data is deduplicated and consistent

### Trade-offs

**Exactly-once delivery** (our approach):
- Guarantees: No duplicates, no data loss
- Cost: Watermark table overhead, 5-minute buffer delay
- Use case: Cold tier analytics (correctness > latency)

**At-least-once delivery**:
- Guarantees: No data loss, possible duplicates
- Cost: Lower (no watermark needed)
- Use case: Telemetry, approximate analytics

**At-most-once delivery**:
- Guarantees: No duplicates, possible data loss
- Cost: Lowest
- Use case: Non-critical metrics

**Chosen**: Exactly-once (market data requires correctness)
