# Phase 10 Streaming Crypto - Architectural Decisions

**Phase**: 10 (Streaming Crypto)
**Last Updated**: 2026-01-20
**Status**: Active

---

## Decision #010: Bronze Layer Raw Bytes Pattern

**Date**: 2026-01-20
**Status**: ✅ Accepted
**Deciders**: Data Engineering Team
**Related Phase**: Phase 10 - Streaming Crypto
**Related Steps**: Step 10 (Bronze Ingestion)

### Context

The Bronze layer serves as the immutable raw data landing zone in the Medallion architecture. We needed to decide the appropriate level of processing to perform at Bronze.

### Decision

**Store raw bytes in Bronze layer** - Complete Kafka message value including 5-byte Schema Registry header + Avro payload.

Deserialization happens at Silver layer using Spark's native from_avro().

### Consequences

**Positive:**
- Perfect replayability (Bronze → Silver)
- Schema evolution resilience  
- Debugging excellence (inspect original bytes)
- Audit trail for compliance

**Negative:**
- Storage overhead (~15% increase)
- Cannot query Bronze directly

### References

- [Medallion Architecture Best Practices](https://www.databricks.com/glossary/medallion-architecture)
- [Operational Runbook](../../../operations/runbooks/streaming-pipeline-operations.md)

---

## Decision #011: Silver Validation with Dead Letter Queue

**Date**: 2026-01-20
**Status**: ✅ Accepted

### Decision

Implement DLQ pattern - invalid records route to silver_dlq_trades with error categorization.

### Consequences

**Positive:**
- No silent data loss
- Audit trail + replayability
- Categorized errors for alerting

**Negative:**
- Dual write overhead
- DLQ monitoring required

---

**Last Updated**: 2026-01-20
**Maintained By**: Data Engineering Team

---

## Decision #013: Silver Binance Trigger Interval Optimization

**Date**: 2026-01-20
**Status**: ✅ Accepted
**Deciders**: Staff Data Engineer
**Related Phase**: Phase 10 - Streaming Crypto
**Related Steps**: Step 11 (Silver Transformation)

### Context

During Bronze/Silver review, observed Silver Binance transformation job consistently falling behind trigger interval:

**Observed Performance:**
- First batch: 148s (5x trigger of 30s) - Spark initialization overhead
- Steady-state: 40s (1.3x trigger of 30s) - Exceeds trigger interval

**Root Cause:**
- Avro deserialization overhead
- Validation logic execution
- Dual write streams (Silver + DLQ)
- Iceberg commit latency

**Impact:**
- Job cannot keep up with trigger interval
- Growing backlog risk
- Latency SLA violation risk

### Decision

**Increase trigger interval from 30 seconds to 60 seconds**

```python
# File: silver_binance_transformation_v3.py:229
.trigger(processingTime="60 seconds")  # Was: 30 seconds
```

**Rationale:**
- Processing time (40s) comfortably fits within 60s trigger
- Provides 20s headroom for traffic spikes
- Acceptable latency for analytics use case (not HFT)
- Aligns with industry best practices (balance latency vs throughput)

### Consequences

**Positive:**
- ✅ No more "falling behind" warnings
- ✅ Job runs sustainably under load
- ✅ Reduced Spark executor thrashing
- ✅ Better checkpoint stability

**Negative:**
- ⚠️ Increased p99 latency: Bronze → Silver now ~60s (was targeting 30s)
- ⚠️ End-to-end latency: +30s (acceptable for analytics)

**Neutral:**
- Silver Kraken unchanged (30s trigger, lower volume)
- Bronze jobs unchanged (10s/30s triggers appropriate)

### Alternatives Considered

**Alternative A: Increase resources (CPU/memory)**

Rejected because:
- Current resource usage low (0.36% CPU, 40% memory)
- Processing is I/O-bound, not CPU-bound
- Adding resources wouldn't significantly reduce 40s processing time
- More cost-effective to adjust trigger interval

**Alternative B: Optimize validation logic**

Deferred because:
- Would require profiling and UDF → native function refactoring
- Time investment: 4-8 hours
- Trigger interval increase solves immediate problem
- Can optimize later if needed

### Implementation Notes

**Applied:**
```bash
# Edit file
vim src/k2/spark/jobs/streaming/silver_binance_transformation_v3.py
# Line 229: Change 30 seconds → 60 seconds

# Restart container
docker restart k2-silver-binance-transformation

# Verify no warnings
docker logs k2-silver-binance-transformation --since 2m | grep "falling behind"
# Result: ✓ No warnings
```

**Verification:**
```bash
# Monitor for 5 minutes
docker logs -f k2-silver-binance-transformation 2>&1 | grep -E "falling behind|Batch"

# Expected: No "falling behind" warnings
# Observed: ✓ Confirmed no warnings after restart
```

### Updated SLAs

**Silver Layer Latency:**
- Previous target: <30s (p99)
- New target: <60s (p99)
- Observed: ~40-50s (well within target)

**End-to-End Latency:**
- Previous target: <5 minutes (p99)
- New target: <5 minutes (p99) - unchanged
- Impact: +30s Silver latency still within overall budget

### Monitoring

**Metrics to Track:**
```
k2_silver_batch_processing_seconds{exchange="binance",quantile="0.99"}
k2_silver_batches_behind_total{exchange="binance"}
```

**Alert Rule:**
```yaml
alert: SilverBinanceProcessingBehind
expr: k2_silver_batches_behind_total{exchange="binance"} > 3
for: 10m
labels:
  severity: warning
annotations:
  summary: "Silver Binance falling behind 60s trigger"
  description: "May need further optimization or resource scaling"
```

### References

- [Bronze/Silver Review](BRONZE_SILVER_REVIEW_COMPLETE.md#3-performance-tuning)
- [Operational Runbook](../../../operations/runbooks/streaming-pipeline-operations.md#incident-2-silver-job-falling-behind)

---

---

## Decision #014: Gold Layer Architecture

**Date**: 2026-01-20
**Status**: ✅ Accepted
**Deciders**: Staff Data Engineer
**Related Phase**: Phase 10 - Streaming Crypto
**Related Steps**: Step 12 (Gold Aggregation)

### Context

The Gold layer serves as the unified cross-exchange analytics layer in the Medallion architecture. We needed to design a schema that supports:
1. Unified view across multiple exchanges (Binance + Kraken)
2. Deduplication for exactly-once semantics
3. Efficient time-series queries with hourly partitioning
4. Late-arriving data handling with watermarking
5. Loose coupling from Silver layer metadata

### Decision

**Gold Table Schema Design:**

```sql
CREATE TABLE gold_crypto_trades (
    -- V2 Avro Fields (15 fields) - Core trade data
    message_id STRING,              -- UUID v4 (deduplication key)
    trade_id STRING,                -- Exchange-specific trade ID
    symbol STRING,                  -- Trading pair
    exchange STRING,                -- BINANCE, KRAKEN, etc.
    asset_class STRING,             -- Always 'crypto'
    timestamp BIGINT,               -- Trade time (microseconds)
    price DECIMAL(18,8),            -- Trade price
    quantity DECIMAL(18,8),         -- Trade quantity
    currency STRING,                -- Quote currency
    side STRING,                    -- BUY or SELL
    trade_conditions ARRAY<STRING>, -- Trade conditions
    source_sequence BIGINT,         -- Exchange sequence
    ingestion_timestamp BIGINT,     -- Platform ingestion time
    platform_sequence BIGINT,       -- Platform sequence
    vendor_data STRING,             -- JSON string (not map)

    -- Gold Metadata (3 fields) - Derived fields
    gold_ingestion_timestamp TIMESTAMP, -- Gold write time
    exchange_date DATE,                 -- Partition key 1
    exchange_hour INT                   -- Partition key 2 (0-23)
)
PARTITIONED BY (exchange_date, exchange_hour)
```

**Key Design Decisions:**

1. **Loose Coupling from Silver**: Exclude Silver metadata fields (validation_timestamp, bronze_ingestion_timestamp, schema_id) to decouple layers. Gold only depends on core V2 fields.

2. **vendor_data as STRING**: Store as JSON string (not map) to avoid schema evolution complexity. Silver already validates this field.

3. **Hourly Partitioning**: Partition by (exchange_date, exchange_hour) for efficient time-series queries (e.g., "trades from 2026-01-20 hour 14-16").

4. **Deduplication by message_id**: UUID v4 ensures uniqueness across all exchanges. Use dropDuplicates() before deriving Gold fields.

5. **Watermarking**: 5-minute grace period for late-arriving data (industry standard for non-HFT analytics).

### Consequences

**Positive:**
- ✅ Unified cross-exchange view (single table for all exchanges)
- ✅ Exactly-once semantics (deduplication by UUID)
- ✅ Efficient hourly queries (partition pruning)
- ✅ Loose coupling (Gold doesn't depend on Silver metadata)
- ✅ Handles late data gracefully (watermarking)

**Negative:**
- ⚠️ Additional storage overhead (~10% for derived fields)
- ⚠️ Cannot query Silver metadata from Gold (intentional trade-off)

**Neutral:**
- Schema evolution requires table recreation (acceptable for analytics)

### Implementation Notes

**Schema Recreation Required:**

Initial Gold table was created with incorrect schema (missing gold_ingestion_timestamp). Fixed by:

```python
# Drop and recreate with correct schema
spark.sql("DROP TABLE IF EXISTS iceberg.market_data.gold_crypto_trades")
spark.sql(create_table_sql)  # Include gold_ingestion_timestamp TIMESTAMP
```

**Streaming Job Pattern:**

```python
# 1. Read from both Silver tables
binance_df = spark.readStream.table("silver_binance_trades")
kraken_df = spark.readStream.table("silver_kraken_trades")

# 2. Add watermarking (convert timestamp to TIMESTAMP type)
binance_df = binance_df.withColumn(
    "event_timestamp",
    from_unixtime(col("timestamp") / 1000000).cast("timestamp")
).withWatermark("event_timestamp", "5 minutes")

# 3. Union streams
all_trades_df = binance_df.union(kraken_df)

# 4. Deduplicate BEFORE deriving Gold fields
deduplicated_df = all_trades_df.dropDuplicates(["message_id"])

# 5. Derive Gold fields AFTER deduplication
gold_df = deduplicated_df.select(
    # V2 fields (15)
    "message_id", "trade_id", ...,
    # Gold metadata (3)
    current_timestamp().alias("gold_ingestion_timestamp"),
    to_date(col("event_timestamp")).alias("exchange_date"),
    hour(col("event_timestamp")).alias("exchange_hour")
)

# 6. Write to Gold table
gold_df.writeStream.format("iceberg") \
    .trigger(processingTime="60 seconds") \
    .toTable("gold_crypto_trades")
```

### Verification

**Data Quality Checks:**

```sql
-- 1. Deduplication verification (expect 0 duplicates)
SELECT message_id, COUNT(*)
FROM gold_crypto_trades
GROUP BY message_id
HAVING COUNT(*) > 1;

-- 2. Exchange breakdown
SELECT exchange, COUNT(*) as trade_count
FROM gold_crypto_trades
GROUP BY exchange;

-- 3. Partition effectiveness
SHOW PARTITIONS gold_crypto_trades;

-- 4. Hourly query performance (partition pruning)
SELECT COUNT(*)
FROM gold_crypto_trades
WHERE exchange_date = '2026-01-20'
  AND exchange_hour BETWEEN 14 AND 16;
```

**Resource Configuration (Post-Optimization):**

```yaml
# Reduced from initial 2 cores / 2.4GB to prevent OOM with 5 concurrent streaming jobs
gold-aggregation:
  deploy:
    resources:
      limits:
        cpus: '1.0'        # Was: 2.0
        memory: 1200M      # Was: 2400M
  spark-submit:
    --total-executor-cores 1    # Was: 2
    --executor-memory 512m      # Was: 1g
    --driver-memory 512m        # Was: 1g
```

**Observed Performance:**
- CPU: 0.5% (steady-state)
- Memory: 600MB / 1.2GB (51%)
- Trigger interval: 60 seconds
- Processing time: ~30-40s (within trigger window)

### Alternatives Considered

**Alternative A: Include Silver Metadata in Gold**

Rejected because:
- Creates tight coupling between Silver and Gold layers
- Silver metadata (validation_timestamp, schema_id) not needed for analytics
- Increases Gold table size unnecessarily
- Harder to evolve Silver without affecting Gold

**Alternative B: Partition by Exchange + Date**

Rejected because:
- Queries typically filter by time, not exchange
- exchange_date + exchange_hour provides better query performance
- Can still filter by exchange as needed (not partition key)

**Alternative C: Daily Partitioning**

Rejected because:
- Hourly partitioning provides better granularity for analytics
- Daily partitions would be too large for interactive queries
- Industry best practice for time-series data is hourly partitioning

### References

- [Medallion Architecture Best Practices](https://www.databricks.com/glossary/medallion-architecture)
- [Decision #010: Bronze Layer Raw Bytes Pattern](#decision-010-bronze-layer-raw-bytes-pattern)
- [Decision #011: Silver Validation with DLQ](#decision-011-silver-validation-with-dead-letter-queue)
- [Gold Aggregation Job](../../../../src/k2/spark/jobs/streaming/gold_aggregation.py)
- [Operational Runbook](../../../operations/runbooks/streaming-pipeline-operations.md)

---

**Last Updated**: 2026-01-20
**Next Review**: After first week of production usage
**Maintained By**: Data Engineering Team
