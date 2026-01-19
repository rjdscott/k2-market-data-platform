# Step 11: Silver Transformation Job (Bronze → Silver with DLQ)

**Status**: 🟡 In Progress
**Estimated Time**: 24 hours (3 days)
**Actual Time**: TBD
**Priority**: Critical Path
**Dependencies**: Step 10 (Bronze jobs operational)

---

## Overview

Transform raw Bronze Avro bytes into validated Silver tables using Spark Structured Streaming. This step implements industry-standard Medallion architecture patterns with Dead Letter Queue (DLQ) for observability.

**Key Deliverables**:
1. **Bronze Refactor**: Store raw Kafka bytes (including Schema Registry headers) for replayability
2. **Silver Transformation**: Deserialize + validate Bronze → Silver per-exchange tables
3. **DLQ Pattern**: Invalid records routed to `silver_dlq_trades` with error tracking
4. **Monitoring**: DLQ rate metrics and alerting

---

## Architecture

### Medallion Layer Pattern (Industry Best Practice)

```
┌─────────────────────────────────────────────────────────────────────┐
│ BRONZE LAYER (Raw Landing Zone)                                     │
│ Purpose: Immutable raw data with Schema Registry headers            │
├─────────────────────────────────────────────────────────────────────┤
│ bronze_binance_trades                                                │
│   - raw_bytes: BINARY (5-byte header + Avro payload)                │
│   - kafka_timestamp: TIMESTAMP                                       │
│   - ingestion_timestamp: TIMESTAMP                                   │
│   - ingestion_date: DATE (partition key)                             │
└─────────────────────────────────────────────────────────────────────┘
                            ↓
              ┌─────────────────────────┐
              │ Silver Transformation    │
              │ (Spark Structured        │
              │  Streaming)              │
              └─────────┬─────────┬─────┘
                        │         │
                   VALID│         │INVALID
                        ↓         ↓
    ┌───────────────────────┐   ┌────────────────────────┐
    │ SILVER LAYER          │   │ DLQ (Dead Letter Queue)│
    │ (Validated)           │   │ (Failed Validations)   │
    ├───────────────────────┤   ├────────────────────────┤
    │ silver_binance_trades │   │ silver_dlq_trades      │
    │   - All V2 fields     │   │   - raw_record         │
    │   - validation_       │   │   - error_reason       │
    │     timestamp         │   │   - error_timestamp    │
    │   - bronze_           │   │   - bronze_source      │
    │     ingestion_        │   │   - dlq_date (partition│
    │     timestamp         │   │                        │
    └───────────────────────┘   └────────────────────────┘
```

### Design Decisions

**Decision #011: Bronze Stores Raw Bytes (Including Headers)**
- **Rationale**: Replayability, schema evolution, debugging, compliance
- **Pattern**: Netflix, Uber, Databricks Medallion architecture
- **Trade-off**: Bronze tables larger (+5 bytes per record), but enables replay

**Decision #012: Silver Uses DLQ Pattern for Invalid Records**
- **Rationale**: Observability, debugging, recovery, audit trail
- **Pattern**: AWS Kinesis, Kafka Streams, Databricks
- **Trade-off**: Additional DLQ table storage, but prevents silent data loss

**Decision #013: Silver = V2 Schema + Validation Metadata (NO Derived Columns)**
- **Rationale**: Longevity, flexibility, reprocessability
- **Pattern**: Medallion architecture (derived columns in Gold only)
- **Trade-off**: Gold must compute derived fields, but Silver remains stable

---

## Implementation Plan

### Phase 1: Bronze Refactor (Breaking Change) - 4 hours

**Current State** (INCORRECT):
```python
# bronze_binance_ingestion.py (Phase 10.10 - WRONG)
bronze_df = (
    kafka_df
    .selectExpr("CAST(value AS BINARY) as avro_payload")  # Strips headers
    .withColumn("ingestion_timestamp", current_timestamp())
)
```

**New State** (CORRECT):
```python
# bronze_binance_ingestion.py (Phase 10.11 - CORRECT)
bronze_df = (
    kafka_df
    .selectExpr("value as raw_bytes")  # Keep ALL bytes (header + payload)
    .withColumn("kafka_timestamp", col("timestamp"))
    .withColumn("ingestion_timestamp", current_timestamp())
    .withColumn("ingestion_date", to_date(current_timestamp()))
)
```

**Breaking Change Impact**:
- Existing Bronze tables incompatible (schema mismatch: `avro_payload` → `raw_bytes`)
- Requires: Drop Bronze tables, recreate with new schema, replay from Kafka
- Mitigation: Phase 10 is pre-production, minimal data loss

**Bronze Table Schema** (Updated):
```sql
CREATE TABLE bronze_binance_trades (
    raw_bytes BINARY,               -- Full Kafka value (5-byte header + Avro payload)
    kafka_timestamp TIMESTAMP,      -- Kafka message timestamp
    topic STRING,                   -- Source Kafka topic
    partition INT,                  -- Kafka partition
    offset BIGINT,                  -- Kafka offset
    ingestion_timestamp TIMESTAMP,  -- When ingested to Bronze
    ingestion_date DATE             -- Partition key
)
PARTITIONED BY (days(ingestion_date))
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.target-file-size-bytes' = '134217728'  -- 128 MB
);
```

**Migration Steps**:
1. Stop Bronze streaming jobs
2. Drop existing Bronze tables
3. Recreate Bronze tables with new schema
4. Update Bronze job code to store `raw_bytes`
5. Restart Bronze jobs (replay from earliest Kafka offset)
6. Validate: Check Bronze tables contain `raw_bytes` field

---

### Phase 2: Silver Table Creation - 2 hours

#### Silver Binance Trades Table

```sql
CREATE TABLE silver_binance_trades (
    -- V2 Avro Schema Fields (Exact Copy)
    symbol STRING NOT NULL,
    price DECIMAL(18,8) NOT NULL,
    quantity DECIMAL(18,8) NOT NULL,
    side STRING NOT NULL,                      -- BUY, SELL
    exchange_timestamp LONG NOT NULL,          -- Microseconds since epoch
    exchange_sequence_number LONG NOT NULL,
    message_id STRING NOT NULL,                -- Deduplication key
    asset_class STRING NOT NULL,               -- CRYPTO
    vendor_data STRING,                        -- JSON (nullable)

    -- Silver Metadata (Added in Silver Layer)
    validation_timestamp TIMESTAMP NOT NULL,   -- When validated in Silver
    bronze_ingestion_timestamp TIMESTAMP NOT NULL,  -- From Bronze
    schema_id INT NOT NULL,                    -- Schema Registry ID used

    -- NO derived columns (those go in Gold)
    -- exchange STRING                         -- DON'T ADD (table name implies exchange)
    -- trade_date_utc DATE                     -- DON'T ADD (compute in Gold)
    -- trade_hour INT                          -- DON'T ADD (compute in Gold)
)
PARTITIONED BY (days(validation_timestamp))
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.target-file-size-bytes' = '67108864',  -- 64 MB
    'format-version' = '2'
);
```

#### Silver Kraken Trades Table

```sql
CREATE TABLE silver_kraken_trades (
    -- Identical schema to silver_binance_trades
    -- Per-exchange tables enable independent schema evolution
    ...
)
PARTITIONED BY (days(validation_timestamp))
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.target-file-size-bytes' = '67108864',  -- 64 MB
    'format-version' = '2'
);
```

#### Silver DLQ Table

```sql
CREATE TABLE silver_dlq_trades (
    raw_record BINARY NOT NULL,                -- Original Bronze raw_bytes
    error_reason STRING NOT NULL,              -- Validation failure reason
    error_type STRING NOT NULL,                -- "price_negative", "symbol_null", etc.
    error_timestamp TIMESTAMP NOT NULL,        -- When validation failed
    bronze_source STRING NOT NULL,             -- "bronze_binance_trades"
    kafka_offset BIGINT NOT NULL,              -- Bronze Kafka offset (for replay)
    schema_id INT,                             -- Schema ID (if deserialization succeeded)
    dlq_date DATE NOT NULL                     -- Partition key
)
PARTITIONED BY (days(dlq_date))
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.target-file-size-bytes' = '33554432'  -- 32 MB (smaller, DLQ should be rare)
);
```

---

### Phase 3: Avro Deserialization with Schema Registry - 6 hours

**Challenge**: Bronze stores raw bytes with 5-byte Schema Registry header
```
Byte Layout:
[0]      : Magic byte (0x00)
[1-4]    : Schema ID (big-endian int32)
[5-end]  : Avro payload
```

**Solution**: Spark UDF to strip header + deserialize

```python
from pyspark.sql.functions import udf
from pyspark.sql.types import StructType, StructField, StringType, DecimalType, LongType
import struct
import avro.io
import avro.schema
from io import BytesIO

# V2 Trade Schema (from Schema Registry)
TRADE_V2_SCHEMA = StructType([
    StructField("symbol", StringType(), False),
    StructField("price", DecimalType(18, 8), False),
    StructField("quantity", DecimalType(18, 8), False),
    StructField("side", StringType(), False),
    StructField("exchange_timestamp", LongType(), False),
    StructField("exchange_sequence_number", LongType(), False),
    StructField("message_id", StringType(), False),
    StructField("asset_class", StringType(), False),
    StructField("vendor_data", StringType(), True),
])

class SchemaRegistryClient:
    """Simple Schema Registry client for fetching schemas."""

    def __init__(self, url="http://schema-registry:8081"):
        self.url = url
        self.cache = {}  # schema_id → avro.schema.Schema

    def get_schema(self, schema_id: int):
        """Fetch schema from registry by ID."""
        if schema_id not in self.cache:
            import requests
            response = requests.get(f"{self.url}/schemas/ids/{schema_id}")
            response.raise_for_status()
            schema_json = response.json()["schema"]
            self.cache[schema_id] = avro.schema.parse(schema_json)
        return self.cache[schema_id]

@udf(returnType=TRADE_V2_SCHEMA)
def deserialize_avro_trade(raw_bytes: bytes):
    """
    Deserialize Avro trade from raw bytes with Schema Registry header.

    Args:
        raw_bytes: Full Kafka value (5-byte header + Avro payload)

    Returns:
        Deserialized trade record as Spark Row

    Raises:
        ValueError: If magic byte invalid or deserialization fails
    """
    if len(raw_bytes) < 6:
        raise ValueError(f"raw_bytes too short: {len(raw_bytes)} bytes")

    # Parse Schema Registry header
    magic_byte = raw_bytes[0]
    if magic_byte != 0x00:
        raise ValueError(f"Invalid magic byte: {magic_byte} (expected 0x00)")

    schema_id = struct.unpack('>I', raw_bytes[1:5])[0]  # Big-endian int32
    avro_payload = raw_bytes[5:]

    # Fetch schema from registry
    schema_client = SchemaRegistryClient()
    schema = schema_client.get_schema(schema_id)

    # Deserialize Avro
    bytes_reader = BytesIO(avro_payload)
    decoder = avro.io.BinaryDecoder(bytes_reader)
    reader = avro.io.DatumReader(schema)
    record = reader.read(decoder)

    # Return Spark Row (matching TRADE_V2_SCHEMA)
    return (
        record['symbol'],
        record['price'],
        record['quantity'],
        record['side'],
        record['exchange_timestamp'],
        record['exchange_sequence_number'],
        record['message_id'],
        record['asset_class'],
        record.get('vendor_data')  # Nullable
    )
```

**Usage in Spark Job**:
```python
# Read from Bronze
bronze_df = spark.readStream.table("iceberg.market_data.bronze_binance_trades")

# Deserialize with error handling
from pyspark.sql.functions import col, current_timestamp

silver_df = (
    bronze_df
    .withColumn("trade", deserialize_avro_trade(col("raw_bytes")))
    .select(
        "trade.*",
        current_timestamp().alias("validation_timestamp"),
        col("ingestion_timestamp").alias("bronze_ingestion_timestamp")
    )
)
```

---

### Phase 4: Validation Logic with DLQ - 6 hours

**Validation Rules** (Strict - Fail to DLQ):

```python
from pyspark.sql.functions import when, col, lit
from pyspark.sql.types import StructType, StructField, StringType, BinaryType, TimestampType

def validate_trade_record(df):
    """
    Apply validation rules and split into valid/invalid streams.

    Returns:
        (valid_df, invalid_df)
    """
    # Validation conditions
    valid_conditions = (
        (col("price") > 0) &                                    # Price must be positive
        (col("quantity") > 0) &                                 # Quantity must be positive
        (col("symbol").isNotNull()) &                           # Symbol required
        (col("side").isin(['BUY', 'SELL'])) &                   # Valid side
        (col("exchange_timestamp") < unix_timestamp() * 1000000) &  # Not future
        (col("exchange_timestamp") > (unix_timestamp() - 86400 * 365) * 1000000) &  # Not >1 year old
        (col("message_id").isNotNull()) &                       # Message ID required
        (col("asset_class") == 'CRYPTO')                        # Must be crypto
    )

    # Add validation flag
    df_with_validation = df.withColumn("is_valid", valid_conditions)

    # Split streams
    valid_df = df_with_validation.filter(col("is_valid"))
    invalid_df = df_with_validation.filter(~col("is_valid"))

    # Add error reason to invalid records
    invalid_df = invalid_df.withColumn(
        "error_reason",
        when(col("price") <= 0, "price_must_be_positive")
        .when(col("quantity") <= 0, "quantity_must_be_positive")
        .when(col("symbol").isNull(), "symbol_required")
        .when(~col("side").isin(['BUY', 'SELL']), "invalid_side")
        .when(col("exchange_timestamp") >= unix_timestamp() * 1000000, "timestamp_future")
        .when(col("exchange_timestamp") <= (unix_timestamp() - 86400 * 365) * 1000000, "timestamp_too_old")
        .when(col("message_id").isNull(), "message_id_required")
        .when(col("asset_class") != 'CRYPTO', "invalid_asset_class")
        .otherwise("unknown_validation_error")
    )

    return valid_df, invalid_df
```

**DLQ Write Logic**:
```python
def write_to_dlq(invalid_df, bronze_source):
    """Write invalid records to DLQ table."""
    dlq_df = (
        invalid_df
        .select(
            col("raw_bytes").alias("raw_record"),
            col("error_reason"),
            col("error_reason").alias("error_type"),  # Same for now, could be categorized
            current_timestamp().alias("error_timestamp"),
            lit(bronze_source).alias("bronze_source"),
            col("offset").alias("kafka_offset"),
            col("schema_id"),
            to_date(current_timestamp()).alias("dlq_date")
        )
    )

    # Write to DLQ table
    (
        dlq_df.writeStream
        .format("iceberg")
        .outputMode("append")
        .option("checkpointLocation", f"/checkpoints/silver-dlq-{bronze_source}/")
        .toTable("iceberg.market_data.silver_dlq_trades")
    )
```

---

### Phase 5: Silver Spark Jobs - 6 hours

#### silver_binance_transformation.py

```python
#!/usr/bin/env python3
"""Silver Binance Transformation Job - Bronze to Silver with DLQ.

This Spark Structured Streaming job:
1. Reads raw bytes from bronze_binance_trades
2. Deserializes Avro using Schema Registry header
3. Validates trade records (price > 0, timestamp valid, etc.)
4. Routes valid records to silver_binance_trades
5. Routes invalid records to silver_dlq_trades

Architecture:
- Source: bronze_binance_trades (Iceberg table)
- Targets:
    - silver_binance_trades (valid records)
    - silver_dlq_trades (invalid records with error reason)
- Checkpoint: /checkpoints/silver-binance/

Configuration:
- Trigger: 30 seconds (batch validation)
- Validation: Strict (fail to DLQ on any rule violation)
- Monitoring: DLQ rate metrics

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --executor-cores 2 \
      --num-executors 2 \
      /opt/k2/src/k2/spark/jobs/streaming/silver_binance_transformation.py
"""

import sys
from pathlib import Path
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, current_timestamp, to_date, lit

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from k2.spark.udfs.avro_deserialization import deserialize_avro_trade
from k2.spark.validation.trade_validation import validate_trade_record, write_to_dlq


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog."""
    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "rest")
        .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
        .config("spark.sql.catalog.iceberg.warehouse", "s3://warehouse/")
        .config("spark.sql.catalog.iceberg.s3.endpoint", "http://minio:9000")
        .config("spark.sql.catalog.iceberg.s3.access-key-id", "admin")
        .config("spark.sql.catalog.iceberg.s3.secret-access-key", "password")
        .config("spark.sql.catalog.iceberg.s3.path-style-access", "true")
        # S3/MinIO configuration
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
        .config("spark.hadoop.fs.s3a.access.key", "admin")
        .config("spark.hadoop.fs.s3a.secret.key", "password")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .config("spark.sql.streaming.checkpointLocation", "/checkpoints/silver-binance/")
        .getOrCreate()
    )


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Silver Transformation - Binance")
    print("Bronze → Silver (Validated) + DLQ")
    print("=" * 70)
    print("\nConfiguration:")
    print("  • Source: bronze_binance_trades")
    print("  • Targets: silver_binance_trades, silver_dlq_trades")
    print("  • Trigger: 30 seconds")
    print("  • Validation: Strict (DLQ on failure)")
    print("  • Checkpoint: /checkpoints/silver-binance/")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Silver-Binance-Transformation")

    try:
        # Read from Bronze
        print("Reading from bronze_binance_trades...")
        bronze_df = (
            spark.readStream
            .table("iceberg.market_data.bronze_binance_trades")
        )

        # Deserialize Avro (with error handling in UDF)
        print("Deserializing Avro with Schema Registry...")
        deserialized_df = (
            bronze_df
            .withColumn("trade", deserialize_avro_trade(col("raw_bytes")))
            .select(
                "trade.*",
                current_timestamp().alias("validation_timestamp"),
                col("ingestion_timestamp").alias("bronze_ingestion_timestamp"),
                col("raw_bytes"),  # Keep for DLQ
                col("offset")      # Keep for DLQ
            )
        )

        # Validate and split streams
        print("Applying validation rules...")
        valid_df, invalid_df = validate_trade_record(deserialized_df)

        # Write valid records to Silver
        print("Starting Silver write stream...")
        silver_query = (
            valid_df
            .select(
                "symbol", "price", "quantity", "side",
                "exchange_timestamp", "exchange_sequence_number",
                "message_id", "asset_class", "vendor_data",
                "validation_timestamp", "bronze_ingestion_timestamp"
            )
            .writeStream
            .format("iceberg")
            .outputMode("append")
            .option("checkpointLocation", "/checkpoints/silver-binance/")
            .trigger(processingTime="30 seconds")
            .toTable("iceberg.market_data.silver_binance_trades")
        )

        # Write invalid records to DLQ
        print("Starting DLQ write stream...")
        dlq_query = write_to_dlq(invalid_df, "bronze_binance_trades")

        print("\n" + "=" * 70)
        print("Silver Binance Transformation - RUNNING")
        print("=" * 70)
        print("\nMonitor:")
        print("  • Spark UI: http://localhost:8090")
        print("  • Query Silver: SELECT * FROM silver_binance_trades LIMIT 10;")
        print("  • Query DLQ: SELECT * FROM silver_dlq_trades LIMIT 10;")
        print("\nPress Ctrl+C to stop...")
        print("=" * 70 + "\n")

        # Await termination
        silver_query.awaitTermination()
        dlq_query.awaitTermination()

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
```

---

## Validation & Testing

### Unit Tests

```python
# tests/unit/spark/test_silver_transformation.py
import pytest
from pyspark.sql import SparkSession
from k2.spark.validation.trade_validation import validate_trade_record

def test_validation_rejects_negative_price(spark_session):
    """Test that negative prices are routed to DLQ."""
    data = [
        ("BTC-USD", -100.50, 1.0, "BUY", 1234567890000000, 1, "msg1", "CRYPTO", None)
    ]
    df = spark_session.createDataFrame(data, schema=TRADE_V2_SCHEMA)

    valid_df, invalid_df = validate_trade_record(df)

    assert valid_df.count() == 0
    assert invalid_df.count() == 1
    assert invalid_df.first()["error_reason"] == "price_must_be_positive"

def test_validation_accepts_valid_trade(spark_session):
    """Test that valid trades pass validation."""
    data = [
        ("BTC-USD", 50000.00, 1.5, "BUY", 1234567890000000, 1, "msg1", "CRYPTO", None)
    ]
    df = spark_session.createDataFrame(data, schema=TRADE_V2_SCHEMA)

    valid_df, invalid_df = validate_trade_record(df)

    assert valid_df.count() == 1
    assert invalid_df.count() == 0
```

### Integration Tests

```bash
# Test Bronze → Silver E2E
1. Produce valid trade to Kafka
2. Verify appears in bronze_binance_trades (raw_bytes)
3. Verify appears in silver_binance_trades (deserialized)
4. DLQ count remains 0

# Test DLQ routing
1. Produce invalid trade to Kafka (price = -100)
2. Verify appears in bronze_binance_trades
3. Verify appears in silver_dlq_trades with error_reason
4. silver_binance_trades count remains unchanged
```

---

## Monitoring & Alerts

### Metrics to Track

```python
# Prometheus metrics
silver_validation_success_total{exchange="binance"}
silver_validation_failure_total{exchange="binance", error_type="price_negative"}
silver_dlq_rate{exchange="binance"}  # failure / (success + failure)
silver_processing_latency_seconds{exchange="binance", percentile="p99"}
```

### Alerting Rules

```yaml
# alerts/silver-transformation.yml

- alert: SilverDLQRateHigh
  expr: |
    sum(rate(silver_validation_failure_total{exchange="binance"}[5m]))
    /
    sum(rate(silver_validation_total{exchange="binance"}[5m]))
    > 0.01
  for: 5m
  severity: page
  annotations:
    summary: "Silver Binance DLQ rate > 1% (current: {{ $value | humanizePercentage }})"
    description: "High validation failure rate indicates upstream data quality issue"

- alert: SilverDLQRateElevated
  expr: |
    sum(rate(silver_validation_failure_total{exchange="binance"}[5m]))
    /
    sum(rate(silver_validation_total{exchange="binance"}[5m]))
    > 0.001
  for: 10m
  severity: slack
  annotations:
    summary: "Silver Binance DLQ rate > 0.1% (current: {{ $value | humanizePercentage }})"

- alert: SilverStreamStopped
  expr: |
    rate(silver_validation_total{exchange="binance"}[5m]) == 0
  for: 10m
  severity: page
  annotations:
    summary: "Silver Binance stream has stopped processing"
```

---

## Acceptance Criteria

### Bronze Refactor
- [ ] Bronze tables store `raw_bytes` (not `avro_payload`)
- [ ] Bronze includes 5-byte Schema Registry header
- [ ] Bronze tables recreated with new schema
- [ ] Bronze jobs write `raw_bytes` field

### Silver Transformation
- [ ] `silver_binance_trades` table created
- [ ] `silver_kraken_trades` table created
- [ ] `silver_dlq_trades` table created
- [ ] Avro deserialization UDF works correctly
- [ ] Validation logic routes invalid records to DLQ
- [ ] Valid records appear in Silver tables
- [ ] Silver schema = V2 + validation metadata (NO derived columns)

### DLQ Pattern
- [ ] Invalid records appear in DLQ with error_reason
- [ ] DLQ rate < 0.1% in normal operation
- [ ] DLQ alerts configured (>1% = page, >0.1% = slack)
- [ ] DLQ table queryable for debugging

### Monitoring
- [ ] Prometheus metrics exposed (success, failure, DLQ rate)
- [ ] Grafana dashboard shows Silver transformation health
- [ ] Alerting rules deployed
- [ ] Runbook created for DLQ investigation

### Testing
- [ ] Unit tests pass (validation logic)
- [ ] Integration tests pass (E2E Bronze → Silver)
- [ ] DLQ routing verified (invalid trade → DLQ)
- [ ] Performance: <30s Silver processing latency (p99)

---

## Risk Mitigation

### Risk: Bronze Refactor Breaks Existing Data
**Mitigation**: Phase 10 is pre-production, minimal data loss acceptable. Document migration steps.

### Risk: Avro Deserialization Failures
**Mitigation**: UDF handles errors gracefully, routes to DLQ with error_reason = "deserialization_failed"

### Risk: DLQ Table Grows Unbounded
**Mitigation**:
- Partition by date (easy to drop old partitions)
- Alert on DLQ growth rate (>1000 records/hour)
- Automated cleanup policy (drop partitions >90 days old)

### Risk: Silver Latency Exceeds Budget
**Mitigation**:
- Monitor p99 latency (target: <30s)
- Auto-scale Spark workers if lag increases
- Increase parallelism (more partitions)

---

## Related Documentation

- [ADR-002: Bronze Per Exchange](../../architecture/decisions/ADR-002-bronze-per-exchange.md)
- [ADR-003: Stream Processing Engine](../../architecture/decisions/ADR-003-stream-processing-engine-selection.md)
- [Step 10: Bronze Ingestion](./step-10-bronze-job.md)
- [Step 12: Gold Aggregation](./step-12-gold-job.md)
- [Medallion Architecture](https://www.databricks.com/glossary/medallion-architecture)

---

**Last Updated**: 2026-01-19
**Next Step**: Step 12 (Gold Aggregation Job)
