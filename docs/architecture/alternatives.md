# Alternative Architecture Analysis

**Last Updated**: 2026-01-09
**Owners**: Platform Team, Architecture Review Board
**Status**: Implementation Plan
**Scope**: Comparison of alternative architectures, trade-offs, decision rationale

---

## Overview

Platform architecture is not a one-size-fits-all decision. This document analyzes alternative architectures considered during the design phase and explains why the current lakehouse architecture was chosen.

**Design Philosophy**: Choose boring technology that solves 90% of use cases. Optimize for operational simplicity, not architectural purity.

---

## Current Architecture: Lakehouse (Kafka + Iceberg)

### Design

```
Exchange Feed → Kafka → Stream Processors → Iceberg → DuckDB Query Engine
                 ↓                             ↓
           Real-time queries            Historical queries
```

### Strengths

✅ **ACID Guarantees**: Iceberg provides atomic commits (critical for financial data)  
✅ **Time-Travel Queries**: Compliance requirement (audit "what data looked like yesterday")  
✅ **Schema Evolution**: Add fields without breaking downstream consumers  
✅ **Separation of Compute and Storage**: Scale query and ingestion independently  
✅ **Mature Components**: Kafka (10+ years), Iceberg (5+ years), battle-tested

### Weaknesses

❌ **Write Latency**: Iceberg adds 200ms p99 (vs raw Parquet)  
❌ **Operational Complexity**: More components (Kafka, Schema Registry, Iceberg, PostgreSQL)  
❌ **Query Complexity**: Hybrid queries (Kafka + Iceberg) require merge logic  
❌ **Limited Query Performance**: DuckDB single-node (need Presto at scale)

### When to Reconsider

- Write latency p99 consistently > 1 second (consider faster storage)
- Query dataset > 10TB (migrate to distributed query engine)
- Need sub-10ms query latency (consider caching or pre-aggregation)

---

## Alternative 1: Lambda Architecture (Rejected)

### Design

```
                   ┌─────────────────────────┐
                   │   Batch Layer (Spark)   │
Exchange Feed ─────┤   - Process historical  │───→ Serving Layer
                   │   - Precompute views    │     (Merge results)
                   └─────────────────────────┘
                              ↑
                   ┌──────────┴──────────────┐
                   │  Speed Layer (Flink)    │
                   │  - Real-time processing │
                   │  - Low latency          │
                   └─────────────────────────┘
```

### How It Works

1. **Batch Layer**: Spark processes all historical data (overnight jobs)
2. **Speed Layer**: Flink processes real-time data (seconds)
3. **Serving Layer**: Merge batch + speed results for queries

### Strengths

✅ **Proven at Scale**: Used by Netflix, LinkedIn, Twitter  
✅ **Separation of Concerns**: Batch and real-time logic isolated  
✅ **Flexibility**: Different engines for different workloads  

### Weaknesses

❌ **Duplicate Logic**: Same computation in batch (Spark) and speed (Flink)  
❌ **Merge Complexity**: Joining batch + speed results is error-prone  
❌ **Operational Burden**: Two processing frameworks to maintain  
❌ **Consistency Issues**: Batch and speed may compute different results  

### Why Rejected

**Reason 1**: Duplicate logic maintenance nightmare

```python
# Batch layer (Spark)
def compute_ohlcv_batch(ticks_df: DataFrame) -> DataFrame:
    return ticks_df.groupBy('symbol', window('timestamp', '1 minute')).agg(
        first('price').alias('open'),
        max('price').alias('high'),
        min('price').alias('low'),
        last('price').alias('close'),
        sum('volume').alias('volume')
    )

# Speed layer (Flink) - SAME LOGIC, DIFFERENT API
def compute_ohlcv_stream(ticks_stream):
    return ticks_stream.key_by('symbol') \
        .window(TumblingEventTimeWindows.of(Time.minutes(1))) \
        .aggregate(OHLCVAggregator())  # Custom Flink aggregator
```

**Problem**: Bug fix in one layer requires updating both. Logic drift inevitable.

**Reason 2**: Merge complexity

```python
# Serving layer: Merge batch + speed results
def query_ohlcv(symbol: str, start: datetime, end: datetime):
    # Query batch layer (historical)
    batch_results = spark.sql(f"""
        SELECT * FROM ohlcv_batch
        WHERE symbol = '{symbol}'
          AND timestamp BETWEEN '{start}' AND '{end - 5min}'
    """)

    # Query speed layer (real-time)
    speed_results = flink.sql(f"""
        SELECT * FROM ohlcv_speed
        WHERE symbol = '{symbol}'
          AND timestamp > '{end - 5min}'
    """)

    # Merge and deduplicate (overlap handling is complex!)
    return merge_deduplicate(batch_results, speed_results)
```

**Problem**: Overlap window (5 minutes) may have duplicates. Merge logic fragile.

**Verdict**: Operational complexity outweighs benefits. Lakehouse provides similar separation without duplicate logic.

---

## Alternative 2: Kappa Architecture (Rejected)

### Design

```
Exchange Feed → Kafka → Stream Processors (Flink/Kafka Streams)
                         ↓
                    State Store (RocksDB)
                         ↓
                    Serve Queries
```

### How It Works

1. **Single Processing Layer**: All computation in stream processors (no batch)
2. **Replay for Reprocessing**: Re-read Kafka from offset 0 to recompute views
3. **State Stores**: RocksDB maintains materialized views

### Strengths

✅ **Single Codebase**: No batch vs. speed layer duplication  
✅ **Strong Consistency**: Stream processing provides event-time semantics  
✅ **Simplicity**: One processing framework (Flink or Kafka Streams)  

### Weaknesses

❌ **Kafka Retention Cost**: Must retain 2+ years of data in Kafka (expensive)  
❌ **Slow Replay**: Replaying 2 years from Kafka takes hours  
❌ **No Time-Travel**: Cannot query "what data looked like 3 months ago"  
❌ **State Store Limitations**: RocksDB not designed for analytical queries  

### Why Rejected

**Reason 1**: Kafka retention cost

```
Data volume: 130GB/day compressed
Kafka retention: 730 days (2 years)
Total Kafka storage: 130GB × 730 = 94.9TB

Kafka broker storage cost: $0.10/GB/month (EBS gp3)
Total cost: 94.9TB × $0.10 = $9,490/month

vs. S3 storage cost: 94.9TB × $0.023 = $2,183/month

Premium for Kafka: $7,307/month (335% more expensive)
```

**Verdict**: Kafka is designed for short-term retention (7-30 days), not long-term storage.

**Reason 2**: No time-travel queries

```python
# Compliance requirement: "Show me data as of 2025-12-01"
# Kappa architecture: Cannot do this (state stores are write-through)
# Lakehouse: Iceberg snapshots enable time-travel
```

**Reason 3**: Slow replay for backtesting

```python
# Backtest strategy over 6 months
# Kappa: Replay 6 months from Kafka (~180 days × 130GB = 23.4TB)
#        Time: 23.4TB / 500MB/sec = 13 hours
# Lakehouse: Query Iceberg directly
#           Time: 5 minutes (DuckDB vectorized scan)
```

**Verdict**: Kappa is ideal for real-time-only workloads. Market data requires long-term storage and replay.

---

## Alternative 3: Streaming Warehouse (Rejected)

### Design

```
Exchange Feed → Kafka → Materialize / ksqlDB
                         ↓
                    Materialized Views
                         ↓
                    SQL Queries
```

### How It Works

1. **Streaming SQL Engine**: Materialize or ksqlDB maintains real-time views
2. **Incremental Computation**: Views update as new data arrives
3. **Direct SQL Queries**: Query views like normal database tables

### Strengths

✅ **Simplest Architecture**: Fewest components (Kafka + Materialize)  
✅ **Real-Time Views**: Materialized views always up-to-date  
✅ **SQL Interface**: Standard SQL for queries (familiar to analysts)  

### Weaknesses

❌ **Immature Technology**: Materialize is pre-1.0, ksqlDB lacks features  
❌ **No Time-Travel**: Cannot query historical snapshots  
❌ **Vendor Lock-In**: Materialize is commercial (closed source)  
❌ **Limited Storage**: State stores (RocksDB) not designed for petabyte scale  

### Why Rejected

**Reason 1**: No time-travel support

```sql
-- Compliance requirement: Query as of specific timestamp
SELECT * FROM ticks
FOR SYSTEM_TIME AS OF '2025-12-01 14:00:00'
WHERE symbol = 'BHP'

-- Materialize: Not supported (no historical snapshots)
-- Iceberg: Fully supported (snapshot isolation)
```

**Reason 2**: Vendor lock-in risk

```
Materialize pricing:
- $0.25/hour per vCPU (24/7)
- 100 vCPU cluster = $25/hour = $18,000/month

vs. Self-hosted DuckDB + Iceberg:
- EC2 c5.4xlarge (16 vCPU) = $0.68/hour = $490/month
- 5 instances = $2,450/month

Premium for Materialize: $15,550/month (635% more expensive)
```

**Reason 3**: Technology maturity

```
Kafka: 10+ years production (GA 2011)
Iceberg: 5+ years production (GA 2020)
Materialize: 2 years production (GA 2023) ← Risk: Less battle-tested
```

**Verdict**: Streaming warehouses are promising but not yet mature for financial data requirements.

---

## Alternative 4: Traditional Data Warehouse (Rejected)

### Design

```
Exchange Feed → Kafka → ETL (Airflow) → Snowflake / BigQuery
                                         ↓
                                    SQL Queries
```

### How It Works

1. **Batch ETL**: Hourly/daily ETL jobs load Kafka → Warehouse
2. **Managed Warehouse**: Snowflake or BigQuery handles storage + compute
3. **SQL Queries**: Standard SQL interface

### Strengths

✅ **Fully Managed**: Snowflake handles operations (no Iceberg management)  
✅ **Proven at Scale**: Thousands of companies use Snowflake  
✅ **SQL Interface**: Standard SQL (no DuckDB custom setup)  
✅ **Built-In Features**: Clustering, caching, materialized views included  

### Weaknesses

❌ **Cost**: Snowflake/BigQuery 10x more expensive than S3 + DuckDB  
❌ **ETL Latency**: Hourly ETL means 1-hour stale data  
❌ **Vendor Lock-In**: Migrating off Snowflake is expensive  
❌ **No Real-Time**: Cannot query Kafka directly (batch-only)  

### Why Rejected

**Reason 1**: Cost at scale

```
Snowflake pricing (on-demand):
- Storage: $40/TB/month (compressed)
- Compute: $2/credit (1 credit = 1 vCPU-hour)

Cost calculation:
- Storage: 100TB × $40 = $4,000/month
- Compute: 24/7 query cluster (10 credits/hour) = $14,400/month
- Total: $18,400/month

vs. Self-hosted lakehouse:
- S3 storage: 100TB × $23 = $2,300/month
- EC2 compute: 5 instances × $490 = $2,450/month
- Total: $4,750/month

Premium for Snowflake: $13,650/month (288% more expensive)
```

**Reason 2**: ETL latency

```
Real-time query requirement: Last 5 minutes of data
Snowflake: Hourly ETL → 1-hour staleness ✗
Lakehouse: Kafka direct access → 60-second staleness ✓
```

**Reason 3**: Vendor lock-in

```
Migration cost from Snowflake:
- Rewrite ETL pipelines (Airflow → Kafka consumers)
- Export 100TB of data ($50/TB egress) = $5,000
- Rewrite queries (Snowflake SQL → DuckDB SQL)
- Retrain team on new tooling

Total migration cost: $50,000-$100,000 (estimated)
```

**Verdict**: Snowflake is excellent for traditional BI workloads but too expensive and inflexible for real-time market data.

---

## Decision Matrix

| Architecture | Real-Time | Time-Travel | Cost | Complexity | Maturity | Verdict |
|--------------|-----------|-------------|------|------------|----------|---------|
| **Lakehouse (Kafka + Iceberg)** | ✅ | ✅ | Low | Medium | High | ✅ **Chosen** |
| **Lambda (Batch + Speed)** | ✅ | ❌ | Medium | High | High | ❌ Duplicate logic |
| **Kappa (Stream-Only)** | ✅ | ❌ | High | Low | High | ❌ No time-travel |
| **Streaming Warehouse** | ✅ | ❌ | High | Low | Low | ❌ Immature |
| **Traditional Warehouse** | ❌ | ✅ | Very High | Low | High | ❌ Cost + Latency |

---

## When to Reconsider Alternatives

### Migrate to Lambda Architecture If:

1. Real-time and batch logic diverge significantly
2. Need different processing engines (Flink for speed, Spark for batch)
3. Operational complexity is acceptable (dedicated team)

**Trigger**: Real-time and historical query patterns become incompatible

### Migrate to Kappa Architecture If:

1. Drop time-travel requirement (no compliance audits)
2. Only need 7-30 days of data (no long-term storage)
3. Accept higher Kafka storage cost

**Trigger**: Compliance requirements change (no historical audits)

### Migrate to Streaming Warehouse If:

1. Materialize/ksqlDB mature significantly (5+ years production)
2. Add time-travel support
3. Pricing becomes competitive

**Trigger**: Materialize adds snapshot isolation (2027+ roadmap)

### Migrate to Traditional Warehouse If:

1. Cost becomes irrelevant (unlimited budget)
2. Real-time requirement drops (hourly latency acceptable)
3. Want fully managed solution (no ops team)

**Trigger**: Company acquired, mandate to standardize on Snowflake

---

## Hybrid Architecture (Future Evolution)

### Potential Evolution Path

```
Phase 1 (Current): Lakehouse (Kafka + Iceberg)
Phase 2: Add Materialize for real-time views (hybrid)
Phase 3: Migrate analytical queries to Presto (distributed)
Phase 4: Consider streaming warehouse if mature
```

**Timeline**: 2-3 years per phase

**Benefit**: Incremental evolution, not big-bang rewrite

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Boring technology principle
- [Query Architecture](./QUERY_ARCHITECTURE.md) - Current query design
- [Storage Optimization](./STORAGE_OPTIMIZATION.md) - Cost analysis
- [Data Consistency](./DATA_CONSISTENCY.md) - Consistency guarantees
