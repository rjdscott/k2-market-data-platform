# Bronze Raw Bytes Migration - 2026-01-19

## Summary

Successfully migrated Bronze layer from deserialized Avro schema to raw bytes schema, implementing Medallion Architecture best practice for immutable raw data storage.

## Migration Details

**Date**: 2026-01-19
**Duration**: ~30 minutes (including troubleshooting)
**Impact**: Breaking change - required table drop/recreate
**Downtime**: ~5 minutes (Bronze jobs stopped during migration)

## What Changed

### Before (Incorrect Pattern)
```python
# Bronze jobs were deserializing Avro at write time
bronze_df = (
    kafka_df
    .selectExpr("substring(value, 6, length(value)-5) as avro_data", ...)  # Strip header
    .select(from_avro(col("avro_data"), schema).alias("trade"))
)
# Tables had: event_type, symbol, price, quantity, etc. (deserialized fields)
```

### After (Correct Pattern)
```python
# Bronze jobs now store raw Kafka value bytes
bronze_df = (
    kafka_df
    .selectExpr(
        "value as raw_bytes",              # Full Kafka value (header + payload)
        "topic",
        "partition",
        "offset",
        "timestamp as kafka_timestamp"
    )
    .withColumn("ingestion_timestamp", current_timestamp())
    .withColumn("ingestion_date", to_date(current_timestamp()))
)
# Tables now have: raw_bytes, topic, partition, offset, kafka_timestamp, ingestion_timestamp, ingestion_date
```

## Migration Steps Executed

### 1. Preparation
- Created migration script: `bronze_raw_bytes_migration.py` (dry-run, execute, rollback modes)
- Created validation script: `validate_bronze_raw_bytes.sh`
- Refactored Bronze jobs: `bronze_binance_ingestion.py`, `bronze_kraken_ingestion.py`

### 2. Migration Execution
```bash
# 1. Stop Bronze jobs
docker compose stop bronze-binance-stream bronze-kraken-stream

# 2. Drop old tables (PySpark script due to spark-sql CLI catalog issues)
docker exec k2-spark-master /opt/spark/bin/spark-submit \
  --master 'local[2]' \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  /opt/k2/src/k2/spark/jobs/migrations/fix_bronze_tables.py

# 3. Clear checkpoints (schema incompatible)
docker exec k2-spark-master rm -rf /checkpoints/bronze-binance/
docker exec k2-spark-master rm -rf /checkpoints/bronze-kraken/

# 4. Restart Bronze jobs with refactored code
docker compose restart bronze-binance-stream bronze-kraken-stream
```

### 3. Validation
- Both Bronze jobs started successfully
- Data flowing continuously:
  - Binance: ~500-3000 rows per 10-second micro-batch
  - Kraken: ~8-30 rows per 30-second micro-batch
- Zero errors in 5+ minute window
- raw_bytes field confirmed in both tables

## Technical Issues Encountered

### Issue 1: spark-sql CLI Catalog Incompatibility
**Problem**: `spark-sql` CLI command failed with `[REQUIRES_SINGLE_PART_NAMESPACE]` error when using `iceberg.market_data.table_name` syntax.

**Root Cause**: CLI not properly configured for Iceberg REST catalog with multi-part namespaces.

**Resolution**: Created PySpark script (`fix_bronze_tables.py`) that uses SparkSession with proper Iceberg catalog configuration instead of CLI.

### Issue 2: Schema Nullability Mismatch
**Problem**: Tables initially created with `NOT NULL` constraints, but Kafka metadata fields are nullable in Spark DataFrames.

**Error**:
```
Cannot write incompatible dataset to table with schema:
* raw_bytes should be required, but is optional
* topic should be required, but is optional
* partition should be required, but is optional
* offset should be required, but is optional
* kafka_timestamp should be required, but is optional
```

**Resolution**: Removed `NOT NULL` constraints from table DDL. Kafka metadata fields are naturally nullable in Spark's Kafka source.

## Files Created/Modified

### Scripts Created
- `scripts/migrations/bronze_raw_bytes_migration.py` - Automated migration with rollback
- `scripts/migrations/validate_bronze_raw_bytes.sh` - Comprehensive validation checks
- `src/k2/spark/jobs/migrations/fix_bronze_tables.py` - PySpark table drop/recreate utility
- `src/k2/spark/jobs/migrations/verify_bronze_data.py` - Data verification utility

### Code Refactored
- `src/k2/spark/jobs/streaming/bronze_binance_ingestion.py` - Removed deserialization, store raw bytes
- `src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py` - Removed deserialization, store raw bytes

### Documentation Updated
- `docs/phases/phase-10-streaming-crypto/steps/step-11-silver-transformation.md` - Added raw bytes pattern
- `TODO.md` - Added Flink review as end-of-project task

## Rollback Procedure

If rollback needed:
```bash
# 1. Stop Bronze jobs
docker compose stop bronze-binance-stream bronze-kraken-stream

# 2. Restore backup metadata (if available)
# Backups saved to: /tmp/k2_bronze_backup_TIMESTAMP/

# 3. Revert code changes
git checkout HEAD~1 -- src/k2/spark/jobs/streaming/bronze_*_ingestion.py

# 4. Recreate tables with old schema (deserialized fields)
# Use original table DDL from git history

# 5. Clear checkpoints
docker exec k2-spark-master rm -rf /checkpoints/bronze-*/

# 6. Restart jobs
docker compose restart bronze-binance-stream bronze-kraken-stream
```

**WARNING**: Rollback causes data loss for period between migration and rollback. Must replay from Kafka.

## Why This Pattern?

### Benefits of Raw Bytes in Bronze
1. **Replayability**: Replay Bronze → Silver if deserialization logic or validation rules change
2. **Schema Evolution**: Bronze unchanged when Avro schemas evolve (only Silver needs updates)
3. **Debugging**: Inspect exact bytes including Schema Registry header (schema ID)
4. **Auditability**: Immutable proof of what was received from exchanges
5. **Separation of Concerns**: Bronze = landing zone, Silver = validation + deserialization

### Industry Adoption
- Netflix: Raw bytes in S3, deserialization in Spark/Flink
- Uber: Immutable raw data layer, derived layers transformable
- Databricks: Medallion Architecture Bronze = raw immutable

### Trade-offs Acknowledged
- ❌ Bronze not immediately queryable (must deserialize first)
- ❌ More complex Silver layer (needs Schema Registry integration)
- ❌ Adds latency (two-step pipeline vs one-step)

**Decision**: Benefits outweigh costs for production data platform requiring:
- Long-term data quality (reprocessability)
- Schema evolution resilience
- Compliance/auditability

## Validation Results

### Bronze Jobs Status
```
Service                  Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
bronze-binance-stream    Up (healthy)
bronze-kraken-stream     Up (healthy)
```

### Data Flow Metrics (5-minute sample)
```
Binance: 793, 1984, 529, 447, 3027 rows/batch (10s trigger)
Kraken:  10, 13, 29, 8, 14 rows/batch (30s trigger)
```

### Error Count
```
Binance: 0 errors (5 min window)
Kraken:  0 errors (5 min window)
```

### Schema Verification
```sql
DESCRIBE iceberg.market_data.bronze_binance_trades;
-- raw_bytes: BINARY (Full Kafka value with 5-byte Schema Registry header + Avro)
-- topic: STRING
-- partition: INT
-- offset: BIGINT
-- kafka_timestamp: TIMESTAMP
-- ingestion_timestamp: TIMESTAMP
-- ingestion_date: DATE (partition key)
```

## Lessons Learned

### 1. spark-sql CLI Limitations
**Learning**: Iceberg REST catalog with multi-part namespaces not well-supported by spark-sql CLI in containerized environments.

**Recommendation**: Use PySpark scripts for DDL operations in Iceberg environments. More reliable and testable.

### 2. Schema Nullability Design
**Learning**: Kafka metadata fields are inherently nullable in Spark DataFrames. Forcing NOT NULL constraints causes write failures.

**Recommendation**: Design table schemas to match actual data nullability, not ideal constraints. Use Silver layer for data quality enforcement.

### 3. Migration Testing
**Learning**: Dry-run mode critical for complex migrations. Caught CLI issues before production execution.

**Recommendation**: Always implement dry-run capability in migration scripts.

### 4. Documentation During Migration
**Learning**: Capturing troubleshooting steps in real-time valuable for future migrations and team knowledge.

**Recommendation**: Document-as-you-go rather than retrospective documentation.

## Next Steps

### Immediate (Step 11)
- [ ] Create `silver_dlq_trades` table (Dead Letter Queue)
- [ ] Implement Avro deserialization UDF with Schema Registry header stripping
- [ ] Create `silver_binance_transformation.py` Spark job
- [ ] Create `silver_kraken_transformation.py` Spark job
- [ ] Implement DLQ pattern for validation failures
- [ ] Add Silver job monitoring dashboards

### Future Optimizations
- [ ] Benchmark Bronze write throughput (baseline for Silver comparison)
- [ ] Implement Bronze compaction jobs (small file problem)
- [ ] Add metrics for raw_bytes size distribution
- [ ] Consider compression ratio analysis (Avro vs Parquet with raw bytes)

## References

- **Decision #011**: Bronze stores raw bytes for replayability
- **ADR-002**: Per-exchange Bronze tables (isolation pattern)
- **Step 11 Documentation**: `docs/phases/phase-10-streaming-crypto/steps/step-11-silver-transformation.md`
- **Platform Principles**: `docs/architecture/platform-principles.md` (Replayable by Default)

## Sign-off

**Migration Status**: ✅ Complete
**Production Ready**: ✅ Yes
**Rollback Tested**: ⚠️ No (would require Kafka replay)
**Documentation**: ✅ Complete

**Validation Date**: 2026-01-19 08:34 UTC
**Validated By**: Claude Code (automated + log analysis)
**Review Required**: Product Owner approval for production deployment
