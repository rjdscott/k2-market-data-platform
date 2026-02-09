# Phase 5: Spark Streaming Jobs - Implementation Guide

**Status:** 🟢 Ready to Start
**Date:** 2026-01-18
**Phase:** 5 (Spark Streaming Jobs)
**Estimated Duration:** 26 hours (3-4 days)
**Prerequisites:** ✅ Phase 4 Complete (Medallion tables created)

---

## Executive Summary

Phase 5 implements the complete Spark Streaming pipeline for the Medallion architecture:
- **Bronze Ingestion:** Kafka → Bronze (raw bytes, per-exchange)
- **Silver Transformation:** Bronze → Silver (Avro deserialization + validation, per-exchange)
- **Gold Aggregation:** Silver → Gold (union + deduplication, unified)

**Key Simplification:** Per-exchange Bronze/Silver architecture simplifies job implementation (independent jobs, clearer boundaries, easier scaling).

---

## Phase 5 Overview

### Jobs to Implement

| Job | Source → Target | Type | Estimated Time |
|-----|-----------------|------|----------------|
| Bronze Binance Ingestion | Kafka → bronze_binance_trades | Per-Exchange | 4 hours |
| Bronze Kraken Ingestion | Kafka → bronze_kraken_trades | Per-Exchange | 4 hours |
| Silver Binance Transformation | bronze_binance → silver_binance | Per-Exchange | 5 hours |
| Silver Kraken Transformation | bronze_kraken → silver_kraken | Per-Exchange | 5 hours |
| Gold Aggregation | silver_* → gold_crypto_trades | Unified | 8 hours |

**Total:** 26 hours (3-4 days)

---

## Step 10: Bronze Ingestion Jobs (8 hours)

### Goal
Stream raw Kafka messages to Bronze tables without deserialization (preserve raw Avro bytes for reprocessing).

### Implementation Strategy

**Per-Exchange Pattern:**
- 2 separate Spark jobs (bronze_binance_ingestion.py, bronze_kraken_ingestion.py)
- Independent Kafka checkpoints
- Independent scaling (Binance: 3 workers, Kraken: 1 worker)
- Clean 1:1 topic-to-table mapping

### Job 1: Bronze Binance Ingestion

**File:** `src/k2/spark/jobs/streaming/bronze_binance_ingestion.py`

**Key Components:**
```python
# Read from Kafka (Binance topic only)
kafka_df = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:9092") \
    .option("subscribe", "market.crypto.trades.binance") \
    .option("startingOffsets", "latest") \
    .option("maxOffsetsPerTrigger", 10000) \
    .option("failOnDataLoss", "false") \
    .load()

# Transform to Bronze schema (NO deserialization)
bronze_df = kafka_df.select(
    col("key").cast("string").alias("message_key"),
    col("value").alias("avro_payload"),  # Keep as binary
    col("topic"),
    col("partition"),
    col("offset"),
    col("timestamp").alias("kafka_timestamp"),
    current_timestamp().alias("ingestion_timestamp"),
    to_date(current_timestamp()).alias("ingestion_date")
)

# Write to Bronze table
query = bronze_df.writeStream \
    .format("iceberg") \
    .outputMode("append") \
    .trigger(processingTime="10 seconds") \
    .option("checkpointLocation", "/checkpoints/bronze-binance/") \
    .option("path", "iceberg.market_data.bronze_binance_trades") \
    .option("fanout-enabled", "true") \
    .start()
```

**Configuration:**
- Trigger: 10 seconds (high volume)
- Max offsets per trigger: 10,000 messages
- Checkpoint: `/checkpoints/bronze-binance/`
- Workers: 3 (high volume)

**Deployment:**
```bash
docker exec k2-spark-master /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
  --executor-cores 2 \
  --num-executors 3 \
  /opt/k2/src/k2/spark/jobs/streaming/bronze_binance_ingestion.py
```

**Acceptance Criteria:**
- [ ] Job starts successfully (visible in Spark UI at http://localhost:8090)
- [ ] Reads from Kafka: market.crypto.trades.binance
- [ ] Writes to Bronze: bronze_binance_trades
- [ ] Checkpoint exists: /checkpoints/bronze-binance/
- [ ] Latency: <10 seconds from Kafka to Bronze
- [ ] Throughput: ≥10K msg/sec
- [ ] Recovery works: Kill + restart resumes from checkpoint

---

### Job 2: Bronze Kraken Ingestion

**File:** `src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py`

**Configuration Differences from Binance:**
- Topic: `market.crypto.trades.kraken`
- Trigger: 30 seconds (lower volume)
- Max offsets: 1,000 messages
- Checkpoint: `/checkpoints/bronze-kraken/`
- Workers: 1 (lower volume)
- Target table: `bronze_kraken_trades`

**Deployment:**
```bash
docker exec k2-spark-master /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3 \
  --executor-cores 1 \
  --num-executors 1 \
  /opt/k2/spark/jobs/streaming/bronze_kraken_ingestion.py
```

**Acceptance Criteria:**
- [ ] Job starts successfully
- [ ] Reads from Kafka: market.crypto.trades.kraken
- [ ] Writes to Bronze: bronze_kraken_trades
- [ ] Checkpoint exists: /checkpoints/bronze-kraken/
- [ ] Latency: <30 seconds
- [ ] Throughput: ≥500 msg/sec
- [ ] Recovery works

---

### Bronze Job Testing

**Verification Commands:**
```sql
-- Check Bronze Binance has data
SELECT COUNT(*) FROM iceberg.market_data.bronze_binance_trades;
SELECT * FROM iceberg.market_data.bronze_binance_trades LIMIT 10;

-- Check Bronze Kraken has data
SELECT COUNT(*) FROM iceberg.market_data.bronze_kraken_trades;
SELECT * FROM iceberg.market_data.bronze_kraken_trades LIMIT 10;

-- Verify raw Avro bytes preserved
SELECT
    topic,
    LENGTH(avro_payload) as payload_size_bytes,
    ingestion_timestamp
FROM iceberg.market_data.bronze_binance_trades
ORDER BY ingestion_timestamp DESC
LIMIT 10;
```

**Expected Results:**
- Binance: 100+ records within 1 minute
- Kraken: 10+ records within 1 minute
- Payload size: ~200-500 bytes per message
- Topics: Correctly assigned per table

---

## Step 11: Silver Transformation Jobs (10 hours)

### Goal
Deserialize V2 Avro, validate data quality, write to per-exchange Silver tables.

### Implementation Strategy

**Per-Exchange Pattern:**
- 2 separate Spark jobs (silver_binance_transformation.py, silver_kraken_transformation.py)
- Avro V2 deserialization with Schema Registry
- Exchange-specific validation rules
- DLQ (Dead Letter Queue) for invalid records

### Job 3: Silver Binance Transformation

**File:** `src/k2/spark/jobs/streaming/silver_binance_transformation.py`

**Key Components:**
```python
from pyspark.sql.avro.functions import from_avro
from pyspark.sql.functions import col, udf, to_date, from_unixtime

# Validation UDF
@udf(returnType=BooleanType())
def is_valid_trade(price, quantity, timestamp, message_id):
    """Validate V2 trade record."""
    if price is None or price <= 0:
        return False
    if quantity is None or quantity <= 0:
        return False
    if timestamp is None or timestamp <= 0:
        return False
    if message_id is None or message_id == "":
        return False
    return True

# Read from Bronze
bronze_df = spark.readStream \
    .format("iceberg") \
    .load("iceberg.market_data.bronze_binance_trades")

# Deserialize Avro V2
v2_schema = """
{
  "type": "record",
  "name": "TradeV2",
  "fields": [
    {"name": "message_id", "type": "string"},
    {"name": "trade_id", "type": "string"},
    {"name": "symbol", "type": "string"},
    ...
  ]
}
"""

trades_df = bronze_df.select(
    from_avro(col("avro_payload"), v2_schema).alias("trade"),
    col("ingestion_timestamp").alias("bronze_ingestion_timestamp")
)

# Validate
validated_df = trades_df.withColumn(
    "is_valid",
    is_valid_trade(
        col("trade.price"),
        col("trade.quantity"),
        col("trade.timestamp"),
        col("trade.message_id")
    )
)

# Split: Good → Silver, Bad → DLQ
good_trades = validated_df.filter(col("is_valid") == True) \
    .select("trade.*") \
    .withColumn("exchange_date", to_date(from_unixtime(col("timestamp") / 1e6)))

bad_trades = validated_df.filter(col("is_valid") == False)

# Write to Silver
query_silver = good_trades.writeStream \
    .format("iceberg") \
    .outputMode("append") \
    .trigger(processingTime="30 seconds") \
    .option("checkpointLocation", "/checkpoints/silver-binance/") \
    .option("path", "iceberg.market_data.silver_binance_trades") \
    .start()

# Write to DLQ
query_dlq = bad_trades.writeStream \
    .format("iceberg") \
    .outputMode("append") \
    .option("checkpointLocation", "/checkpoints/dlq-binance/") \
    .option("path", "iceberg.market_data.dlq_binance_trades") \
    .start()
```

**Configuration:**
- Trigger: 30 seconds
- Checkpoint: `/checkpoints/silver-binance/`
- Workers: 2
- DLQ table: `dlq_binance_trades` (create if needed)

**Validation Rules:**
- `price > 0` (no zero or negative prices)
- `quantity > 0` (no zero or negative quantities)
- `timestamp > 0` (valid microsecond timestamp)
- `message_id != null` (deduplication key must exist)
- `symbol matches [A-Z]+` (valid symbol format)

**Deployment:**
```bash
docker exec k2-spark-master /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.3,org.apache.spark:spark-avro_2.12:3.5.3 \
  --executor-cores 2 \
  --num-executors 2 \
  /opt/k2/src/k2/spark/jobs/streaming/silver_binance_transformation.py
```

**Acceptance Criteria:**
- [ ] Job starts successfully
- [ ] Reads from Bronze: bronze_binance_trades
- [ ] Deserializes Avro V2: All 15 fields populated
- [ ] Validation works: Good trades → Silver, Bad trades → DLQ
- [ ] Writes to Silver: silver_binance_trades
- [ ] DLQ works: dlq_binance_trades has invalid records (if any)
- [ ] Latency: <30 seconds Bronze → Silver
- [ ] Recovery works

---

### Job 4: Silver Kraken Transformation

**File:** `src/k2/spark/jobs/streaming/silver_kraken_transformation.py`

**Configuration Differences from Binance:**
- Source: `bronze_kraken_trades`
- Target: `silver_kraken_trades`
- DLQ: `dlq_kraken_trades`
- Checkpoint: `/checkpoints/silver-kraken/`
- Workers: 1 (lower volume)

**Exchange-Specific Validation:**
- Kraken uses deterministic trade_id (may need special handling)
- Symbol normalization: XBT → BTC already done in producer
- Vendor_data should contain original Kraken pair

**Acceptance Criteria:**
- [ ] Job starts successfully
- [ ] Reads from Bronze: bronze_kraken_trades
- [ ] Deserializes Avro V2
- [ ] Validation works
- [ ] Writes to Silver: silver_kraken_trades
- [ ] DLQ works: dlq_kraken_trades
- [ ] Latency: <30 seconds
- [ ] Recovery works

---

### Silver Job Testing

**Verification Commands:**
```sql
-- Check Silver Binance has data
SELECT COUNT(*) FROM iceberg.market_data.silver_binance_trades;
SELECT * FROM iceberg.market_data.silver_binance_trades LIMIT 10;

-- Check Silver Kraken has data
SELECT COUNT(*) FROM iceberg.market_data.silver_kraken_trades;
SELECT * FROM iceberg.market_data.silver_kraken_trades LIMIT 10;

-- Check DLQ (should be empty if data quality good)
SELECT COUNT(*) FROM iceberg.market_data.dlq_binance_trades;
SELECT COUNT(*) FROM iceberg.market_data.dlq_kraken_trades;

-- Validate data quality
SELECT
    exchange,
    COUNT(*) as total_trades,
    MIN(price) as min_price,
    MAX(price) as max_price,
    MIN(quantity) as min_quantity,
    MAX(quantity) as max_quantity
FROM (
    SELECT * FROM iceberg.market_data.silver_binance_trades
    UNION ALL
    SELECT * FROM iceberg.market_data.silver_kraken_trades
)
GROUP BY exchange;
```

**Expected Results:**
- Binance: 90+ records in Silver (10% loss from Bronze due to processing overhead)
- Kraken: 9+ records in Silver
- DLQ: 0 records (if data quality perfect)
- Prices: > 0, reasonable ranges ($1K-$100K for BTC)
- Quantities: > 0, reasonable ranges (0.001-10 BTC typical)

---

## Step 12: Gold Aggregation Job (8 hours)

### Goal
Union both Silver tables, deduplicate, derive hourly partition fields, write to Gold unified analytics table.

### Implementation Strategy

**Unified Pattern:**
- Single Spark job (gold_aggregation.py)
- Reads from both Silver tables
- Union operation (same V2 schema)
- Deduplication by message_id
- Derive exchange_date, exchange_hour for hourly partitioning

### Job 5: Gold Aggregation

**File:** `src/k2/spark/jobs/streaming/gold_aggregation.py`

**Key Components:**
```python
from pyspark.sql.functions import col, hour, to_date, from_unixtime

# Read from both Silver tables
binance_df = spark.readStream \
    .format("iceberg") \
    .load("iceberg.market_data.silver_binance_trades")

kraken_df = spark.readStream \
    .format("iceberg") \
    .load("iceberg.market_data.silver_kraken_trades")

# Union both sources (same V2 schema)
all_trades_df = binance_df.union(kraken_df)

# Add derived fields for Gold partitioning
gold_df = all_trades_df \
    .withColumn("exchange_date", to_date(from_unixtime(col("timestamp") / 1e6))) \
    .withColumn("exchange_hour", hour(from_unixtime(col("timestamp") / 1e6))) \
    .dropDuplicates(["message_id"])  # Deduplication

# Write to Gold table
query = gold_df.writeStream \
    .format("iceberg") \
    .outputMode("append") \
    .trigger(processingTime="60 seconds") \
    .option("checkpointLocation", "/checkpoints/gold/") \
    .option("path", "iceberg.market_data.gold_crypto_trades") \
    .start()

query.awaitTermination()
```

**Configuration:**
- Trigger: 60 seconds (less frequent, analytics layer)
- Checkpoint: `/checkpoints/gold/`
- Workers: 2
- Deduplication: By message_id (UUID v4, unique across all exchanges)

**Partitioning Logic:**
```python
# Example timestamp: 1737158400000000 (microseconds)
# exchange_date: to_date(from_unixtime(1737158400000000 / 1e6))
#   → 2026-01-18
# exchange_hour: hour(from_unixtime(1737158400000000 / 1e6))
#   → 0-23 (hour of day)
```

**Deployment:**
```bash
docker exec k2-spark-master /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  --executor-cores 2 \
  --num-executors 2 \
  /opt/k2/src/k2/spark/jobs/streaming/gold_aggregation.py
```

**Acceptance Criteria:**
- [ ] Job starts successfully
- [ ] Reads from Silver: Both tables (silver_binance_trades, silver_kraken_trades)
- [ ] Union works: Both exchanges combined in output
- [ ] Deduplication works: `SELECT message_id, COUNT(*) FROM gold GROUP BY message_id HAVING COUNT(*) > 1` returns 0
- [ ] Derived fields populated: exchange_date, exchange_hour
- [ ] Hourly partitions created: `SHOW PARTITIONS gold_crypto_trades`
- [ ] Latency: <60 seconds Silver → Gold
- [ ] Recovery works

---

### Gold Job Testing

**Verification Commands:**
```sql
-- Check Gold has data from both exchanges
SELECT
    exchange,
    COUNT(*) as total_trades,
    MIN(exchange_date) as earliest_date,
    MAX(exchange_date) as latest_date
FROM iceberg.market_data.gold_crypto_trades
GROUP BY exchange;

-- Check hourly partitions created
SHOW PARTITIONS iceberg.market_data.gold_crypto_trades;

-- Verify deduplication (should return 0 duplicates)
SELECT
    message_id,
    COUNT(*) as duplicate_count
FROM iceberg.market_data.gold_crypto_trades
GROUP BY message_id
HAVING COUNT(*) > 1;

-- Check exchange_hour distribution
SELECT
    exchange_hour,
    COUNT(*) as trade_count
FROM iceberg.market_data.gold_crypto_trades
GROUP BY exchange_hour
ORDER BY exchange_hour;

-- Analytical query (should be fast with hourly partitioning)
SELECT
    symbol,
    AVG(price) as avg_price,
    SUM(quantity) as total_volume
FROM iceberg.market_data.gold_crypto_trades
WHERE exchange_date = CURRENT_DATE
  AND exchange_hour >= 0
GROUP BY symbol;
```

**Expected Results:**
- Both exchanges represented: BINANCE (~80+ trades), KRAKEN (~8+ trades)
- No duplicates: 0 rows in deduplication query
- Hourly partitions: Multiple partitions visible
- Query performance: <500ms for 1-hour range

---

## Implementation Checklist

### Pre-Implementation
- [ ] Phase 4 complete (all 5 tables created)
- [ ] Spark cluster operational (http://localhost:8090)
- [ ] Kafka topics populated (binance, kraken)
- [ ] Create streaming/ directory: `mkdir -p src/k2/spark/jobs/streaming`

### Step 10: Bronze Ingestion (8 hours)
- [ ] Create `bronze_binance_ingestion.py` (4h)
- [ ] Create `bronze_kraken_ingestion.py` (4h)
- [ ] Deploy Binance Bronze job
- [ ] Deploy Kraken Bronze job
- [ ] Verify data in bronze_binance_trades (100+ records)
- [ ] Verify data in bronze_kraken_trades (10+ records)
- [ ] Test checkpoint recovery (kill + restart)

### Step 11: Silver Transformation (10 hours)
- [ ] Create DLQ tables: dlq_binance_trades, dlq_kraken_trades (30min)
- [ ] Create `silver_binance_transformation.py` (5h)
- [ ] Create `silver_kraken_transformation.py` (5h)
- [ ] Deploy Binance Silver job
- [ ] Deploy Kraken Silver job
- [ ] Verify data in silver_binance_trades (90+ records)
- [ ] Verify data in silver_kraken_trades (9+ records)
- [ ] Check DLQ tables (should be empty if quality good)
- [ ] Test validation rules (inject bad record, verify DLQ capture)

### Step 12: Gold Aggregation (8 hours)
- [ ] Create `gold_aggregation.py` (8h)
- [ ] Deploy Gold job
- [ ] Verify data in gold_crypto_trades (both exchanges)
- [ ] Verify deduplication (0 duplicates)
- [ ] Verify hourly partitions created
- [ ] Test analytical query performance (<500ms)
- [ ] Test checkpoint recovery

### Post-Implementation
- [ ] All 5 Spark jobs running successfully
- [ ] Update PROGRESS.md: Mark steps 10-12 as complete
- [ ] Create runbooks for each job
- [ ] Document monitoring dashboards
- [ ] Prepare for Phase 6 (E2E testing)

---

## Monitoring & Operations

### Spark UI Monitoring
- **URL:** http://localhost:8090
- **Check:** All 5 jobs visible in "Running Applications"
- **Metrics:** Processing rates, batch durations, records processed

### Job-Specific Metrics

**Bronze Jobs:**
```
binance_bronze_records_ingested_total
binance_bronze_lag_seconds
kraken_bronze_records_ingested_total
kraken_bronze_lag_seconds
```

**Silver Jobs:**
```
binance_silver_records_valid_total
binance_silver_records_invalid_total (DLQ)
kraken_silver_records_valid_total
kraken_silver_records_invalid_total (DLQ)
```

**Gold Job:**
```
gold_records_written_total
gold_duplicates_removed_total
gold_partitions_created_total
```

### Alert Thresholds

| Metric | Threshold | Action |
|--------|-----------|--------|
| binance_bronze_lag_seconds | > 30s | Page on-call |
| kraken_bronze_lag_seconds | > 120s | Slack notification |
| silver_*_records_invalid_total | > 10% | Investigate data quality |
| gold_duplicates_removed_total | > 1% | Check message_id generation |

---

## Troubleshooting Guide

### Issue: Bronze job not reading from Kafka
**Symptoms:** Zero records in Bronze table
**Diagnosis:**
```bash
# Check Kafka topic has data
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.binance \
  --max-messages 10
```
**Solution:** Verify Kafka bootstrap servers, topic names, permissions

### Issue: Silver job not deserializing Avro
**Symptoms:** Error "Cannot deserialize Avro", DLQ has all records
**Diagnosis:**
```sql
-- Check Bronze has valid Avro payloads
SELECT LENGTH(avro_payload) FROM bronze_binance_trades LIMIT 10;
```
**Solution:** Verify V2 schema matches producer schema, check Schema Registry

### Issue: Gold job has duplicates
**Symptoms:** Deduplication query returns > 0
**Diagnosis:**
```sql
-- Find duplicate message_ids
SELECT message_id, COUNT(*), MIN(ingestion_timestamp), MAX(ingestion_timestamp)
FROM gold_crypto_trades
GROUP BY message_id
HAVING COUNT(*) > 1;
```
**Solution:** Check if dropDuplicates() is working, verify message_id uniqueness

### Issue: Checkpoint corruption
**Symptoms:** Job fails to restart, "Cannot recover from checkpoint"
**Diagnosis:**
```bash
ls -la /checkpoints/bronze-binance/
```
**Solution:** Delete checkpoint directory, restart from latest (acceptable for Bronze)

---

## Performance Optimization

### Bronze Jobs
- **Binance (high volume):**
  - `maxOffsetsPerTrigger`: 10,000
  - Trigger interval: 10s
  - Workers: 3
  - Memory: 3GB per worker

- **Kraken (lower volume):**
  - `maxOffsetsPerTrigger`: 1,000
  - Trigger interval: 30s
  - Workers: 1
  - Memory: 3GB

### Silver Jobs
- Enable predicate pushdown: `spark.sql.sources.partitionOverwriteMode=dynamic`
- Parquet compression: Zstd (already configured in tables)
- Target file size: 128 MB

### Gold Job
- Hourly partitions: Efficient for analytical queries (1 hour vs 1 day)
- Distribution mode: Hash (configured in Gold table)
- Deduplication: Use window functions if dropDuplicates() causes issues

---

## Success Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Bronze Binance throughput | ≥10K msg/sec | TBD |
| Bronze Kraken throughput | ≥500 msg/sec | TBD |
| Silver latency (p99) | <30s | TBD |
| Gold latency (p99) | <60s | TBD |
| E2E latency (p99) | <5 min | TBD |
| Gold query (1hr range) | <500ms | TBD |
| Data quality (invalid %) | <1% | TBD |
| Deduplication (duplicates) | 0 | TBD |

---

## Next Phase Preview

**Phase 6: Testing & Validation** (20 hours)
1. **E2E Pipeline Testing** (12 hours)
   - Comprehensive E2E tests: WebSocket → Kafka → Bronze → Silver → Gold
   - Latency validation
   - Data quality checks
   - Checkpoint recovery testing

2. **Performance Validation** (8 hours)
   - Load testing
   - Query performance benchmarking
   - Memory profiling
   - Stability testing (24-hour run)

---

**Last Updated:** 2026-01-18
**Status:** 🟢 Ready to Start
**Prerequisites:** ✅ Phase 4 Complete

---

**For progress tracking**, see [PROGRESS.md](PROGRESS.md).
**For current status**, see [STATUS.md](STATUS.md).
**For architectural context**, see [PHASE-4-BRONZE-REFACTOR-COMPLETE.md](PHASE-4-BRONZE-REFACTOR-COMPLETE.md).
