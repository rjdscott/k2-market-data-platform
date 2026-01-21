# Phase 10 Validation Guide

**Last Updated**: 2026-01-18
**Phase**: Production Crypto Streaming Platform
**Purpose**: Validate complete platform refactor with acceptance criteria

---

## Overview

This guide provides comprehensive validation procedures for Phase 10. Validation occurs at three levels:

1. **Unit Tests**: Code-level validation (automated, <5 min)
2. **Integration Tests**: Component interaction validation (automated, <15 min)
3. **E2E Tests**: Full pipeline validation (automated + manual, <30 min)

---

## Validation Levels

### Level 1: Unit Tests (Automated)

**Scope**: Individual functions, schema validation, message conversion
**Duration**: <5 minutes
**Frequency**: Every commit (pre-commit hook + CI/CD)

**Run Command**:
```bash
uv run pytest tests/unit/ -v --cov=src/k2
```

**Success Criteria**:
- All unit tests pass (100%)
- Code coverage >85% for new code
- No test regressions

**Key Test Files**:
- `tests/unit/test_kraken_client.py` - Kraken parsing, conversion, validation (20+ tests)
- `tests/unit/test_v3_schema.py` - V3 schema validation (10+ tests)
- `tests/unit/test_spark_utils.py` - Spark utility functions (5+ tests)

**Acceptance Criteria**:
- [x] All unit tests pass
- [x] Kraken symbol parsing: `"XBT/USD"` → `("BTC", "USD")`
- [x] Kraken v3 conversion: All 12 fields populated
- [x] V3 schema validates: avro-tools compile succeeds
- [x] No regressions: Existing Binance tests still pass

---

### Level 2: Integration Tests (Automated)

**Scope**: Kafka → Spark → Iceberg integration, Schema Registry, WebSocket clients
**Duration**: 10-15 minutes
**Frequency**: Before PR merge, after each step

**Run Command**:
```bash
# Start infrastructure
docker-compose up -d

# Run integration tests
uv run pytest tests/integration/ -v -s
```

**Test Scenarios**:

#### 1. **Kraken WebSocket Integration Test**
```bash
uv run pytest tests/integration/test_kraken_live.py -v -s
```

**Success Criteria**:
- Connects to wss://ws.kraken.com with SSL
- Receives >10 trades in 60 seconds (BTC/USD, ETH/USD)
- Trades published to Kafka topic `market.crypto.trades.kraken`
- Schema Registry validates v3 schema
- Message deserialization successful

---

#### 2. **Bronze Ingestion Integration Test**
```bash
uv run pytest tests/integration/test_bronze_ingestion.py -v -s
```

**Success Criteria**:
- Spark job reads from Kafka (both Binance and Kraken topics)
- Writes to Bronze table: `bronze_crypto_trades`
- Checkpoint created: `/checkpoints/bronze/` exists
- Latency <10 seconds (Kafka produce → Bronze write)
- Handles 1000 test messages successfully
- Recovery works: Restart job, resumes from checkpoint

---

#### 3. **Silver Transformation Integration Test**
```bash
uv run pytest tests/integration/test_silver_transformation.py -v -s
```

**Success Criteria**:
- Reads from Bronze, deserializes Avro bytes
- Validates data quality (positive prices, valid timestamps)
- Writes to Silver: `silver_binance_trades`, `silver_kraken_trades`
- Invalid records go to DLQ: `silver_dlq`
- Latency <30 seconds (Bronze → Silver)
- DLQ captures: negative prices, future timestamps, missing fields

---

#### 4. **Gold Aggregation Integration Test**
```bash
uv run pytest tests/integration/test_gold_aggregation.py -v -s
```

**Success Criteria**:
- Reads from both Silver tables
- Union successful: Both exchanges combined
- Deduplication works: Duplicate message_ids removed
- Derived fields: `exchange_date`, `exchange_hour` populated
- Writes to Gold: `gold_crypto_trades`
- Hourly partitions created
- Latency <60 seconds (Silver → Gold)

---

### Level 3: E2E Tests (Full Pipeline Validation)

**Scope**: WebSocket → Kafka → Spark → Bronze → Silver → Gold → Query
**Duration**: 20-30 minutes
**Frequency**: After milestones, before production deployment

**Run Command**:
```bash
uv run pytest tests/e2e/test_medallion_pipeline.py -v -s --timeout=1800
```

**Test Scenarios**:

#### E2E-1: Full Pipeline Latency Test
**Objective**: Validate end-to-end latency <5 minutes

```python
def test_full_pipeline_latency():
    """
    1. Start Binance + Kraken WebSocket clients
    2. Produce 10K trades to Kafka (5K each exchange)
    3. Wait for all trades to appear in Gold table
    4. Measure latency: max(gold.ingestion_timestamp - kafka.produce_timestamp)
    5. Assert: p99 latency < 5 minutes
    """
```

**Acceptance Criteria**:
- [x] 10K trades produced to Kafka
- [x] All 10K trades in Bronze within 2 minutes
- [x] All 10K trades in Silver within 3 minutes
- [x] All 10K trades in Gold within 5 minutes
- [x] p99 latency: <5 minutes
- [x] p50 latency: <2 minutes

---

#### E2E-2: Deduplication Test
**Objective**: Validate Gold table has no duplicates

```python
def test_deduplication():
    """
    1. Produce trade with message_id=ABC to Kafka
    2. Produce same trade with message_id=ABC again (duplicate)
    3. Wait for Gold table to process
    4. Query: SELECT COUNT(*) FROM gold_crypto_trades WHERE message_id = 'ABC'
    5. Assert: COUNT = 1 (only one record)
    """
```

**Acceptance Criteria**:
- [x] Duplicate detection works
- [x] Gold table has exactly 1 record for message_id
- [x] No data loss: Other records unaffected

---

#### E2E-3: Data Quality Test
**Objective**: Validate DLQ captures invalid records

```python
def test_data_quality():
    """
    1. Produce 100 valid trades + 10 invalid trades to Kafka
    2. Invalid: negative prices (5), future timestamps (3), missing fields (2)
    3. Wait for Silver processing
    4. Check Silver table has 100 valid records
    5. Check DLQ has 10 invalid records with correct error messages
    """
```

**Acceptance Criteria**:
- [x] Valid trades in Silver: 100
- [x] Invalid trades in DLQ: 10
- [x] DLQ error_messages: "negative_price" (5), "future_timestamp" (3), "missing_field" (2)
- [x] No data loss: All 110 records accounted for

---

#### E2E-4: Query Performance Test
**Objective**: Validate Gold queries meet <500ms target

```python
def test_query_performance():
    """
    1. Load 1M trades into Gold (1 week of data)
    2. Run 100 queries: SELECT * FROM gold WHERE exchange_date = '2026-01-17' AND exchange_hour = 10
    3. Measure p50, p95, p99 latency
    4. Assert: p99 < 500ms
    """
```

**Acceptance Criteria**:
- [x] p50 latency: <100ms
- [x] p95 latency: <300ms
- [x] p99 latency: <500ms
- [x] Partition pruning works: Query only reads 1 hourly partition

---

#### E2E-5: Checkpoint Recovery Test
**Objective**: Validate job recovery with no data loss

```python
def test_checkpoint_recovery():
    """
    1. Start Bronze ingestion job
    2. Produce 1000 trades to Kafka
    3. After 500 trades processed, kill Bronze job
    4. Restart Bronze job
    5. Verify all 1000 trades in Bronze (no duplicates, no gaps)
    """
```

**Acceptance Criteria**:
- [x] Job restarts successfully from checkpoint
- [x] All 1000 trades in Bronze table
- [x] No duplicates: Each Kafka offset processed exactly once
- [x] No gaps: Continuous offset range
- [x] Recovery time: <60 seconds

---

#### E2E-6: Multi-Exchange Unified Test
**Objective**: Validate Binance + Kraken trades unified in Gold

```python
def test_multi_exchange_unified():
    """
    1. Produce 1000 Binance trades (BTCUSDT, ETHUSDT)
    2. Produce 500 Kraken trades (BTCUSD, ETHUSD)
    3. Wait for Gold table processing
    4. Query Gold: SELECT exchange, COUNT(*), symbols FROM gold GROUP BY exchange
    5. Assert: BINANCE=1000, KRAKEN=500
    6. Assert: Both exchanges have correct symbols
    """
```

**Acceptance Criteria**:
- [x] Gold has 1500 trades total
- [x] BINANCE: 1000 trades
- [x] KRAKEN: 500 trades
- [x] Symbols correct: BTCUSDT/ETHUSDT (Binance), BTCUSD/ETHUSD (Kraken)
- [x] No cross-contamination: Binance trades have exchange='BINANCE'

---

## Performance Benchmarks

### Bronze Ingestion Performance

**Target**: 10,000 msg/sec sustained

**Benchmark Script**:
```bash
# Produce 100K test trades at 10K msg/sec
python scripts/benchmark_bronze.py --messages 100000 --rate 10000

# Monitor Bronze ingestion rate
watch -n 1 "docker exec spark-master spark-submit --master local[*] \
  --class org.apache.spark.examples.SparkPi \
  /opt/k2/scripts/monitor_bronze_rate.py"
```

**Acceptance Criteria**:
- [x] Sustained throughput: ≥10,000 msg/sec for 10 minutes
- [x] No backlog: Kafka consumer lag <1000 messages
- [x] No errors: Zero failed batches
- [x] Memory stable: Spark workers <4GB RAM usage

---

### Silver Transformation Performance

**Target**: p99 latency <30 seconds

**Benchmark Script**:
```bash
# Measure Bronze → Silver latency
python scripts/benchmark_silver_latency.py --samples 1000
```

**Acceptance Criteria**:
- [x] p50 latency: <10 seconds
- [x] p95 latency: <20 seconds
- [x] p99 latency: <30 seconds
- [x] Validation overhead: <5 seconds per batch
- [x] DLQ rate: <1% of total messages

---

### Gold Query Performance

**Target**: p99 query latency <500ms

**Benchmark Script**:
```bash
# Run 100 time-range queries
python scripts/benchmark_gold_queries.py --queries 100 --range "1 hour"
```

**Acceptance Criteria**:
- [x] p50 latency: <100ms
- [x] p95 latency: <300ms
- [x] p99 latency: <500ms
- [x] Partition pruning: Reads only 1 hourly partition for 1-hour queries
- [x] Data scanned: <100MB per query

---

## Cleanup Validation

### Step 01-03: Cleanup Validation

**Objective**: Verify complete ASX/equity removal

**Validation Commands**:
```bash
# Should return 0 results (except docs/archive)
grep -r "ASX" src/ tests/ config/ scripts/

# Should return 0 results
grep -r -i "equity\|equities" src/ tests/ config/ | grep -v "comment\|doc"

# Should not exist
find src/ -name "*batch*loader*"

# Should be empty
ls data/sample/

# Schema Registry should not have v1 or equity subjects
curl http://localhost:8081/subjects | grep -i -E "(v1|equity|asx)"  # Empty

# Iceberg catalog should be clean
docker exec spark-master spark-sql \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=rest \
  --conf spark.sql.catalog.iceberg.uri=http://iceberg-rest:8181 \
  -e "SHOW TABLES IN iceberg.market_data;"
# Should return empty or only new tables (bronze, silver, gold)
```

**Acceptance Criteria**:
- [x] Zero ASX references in code
- [x] Zero equity asset class references
- [x] batch_loader.py deleted
- [x] All ASX sample data removed
- [x] V1 schemas deleted
- [x] Schema Registry clean
- [x] Iceberg catalog clean
- [x] All tests pass after cleanup

---

## Infrastructure Validation

### Spark Cluster Health

**Validation Commands**:
```bash
# Check Spark master
docker ps | grep spark-master

# Check workers
curl -s http://localhost:8080 | grep "Workers (2)"

# Check worker resources
curl -s http://localhost:8080/json/ | jq '.aliveworkers[] | {id, cores, memory}'

# Test job submission
docker exec spark-master spark-submit \
  --master spark://spark-master:7077 \
  --deploy-mode client \
  /opt/spark/examples/src/main/python/pi.py 10
# Should exit with code 0
```

**Acceptance Criteria**:
- [x] Spark master running
- [x] 2 workers alive
- [x] Each worker: 2 cores, 3GB memory
- [x] Test job succeeds

---

### Medallion Tables Health

**Validation Commands**:
```bash
# Check all tables exist
docker exec spark-master spark-sql \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=rest \
  --conf spark.sql.catalog.iceberg.uri=http://iceberg-rest:8181 \
  -e "SHOW TABLES IN iceberg.market_data;"

# Expected output:
# bronze_crypto_trades
# silver_binance_trades
# silver_kraken_trades
# gold_crypto_trades

# Check table schemas
docker exec spark-master spark-sql \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=rest \
  --conf spark.sql.catalog.iceberg.uri=http://iceberg-rest:8181 \
  -e "DESCRIBE EXTENDED iceberg.market_data.gold_crypto_trades;"

# Check partitioning
docker exec spark-master spark-sql \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=rest \
  --conf spark.sql.catalog.iceberg.uri=http://iceberg-rest:8181 \
  -e "SHOW PARTITIONS iceberg.market_data.gold_crypto_trades;"
```

**Acceptance Criteria**:
- [x] 4 tables exist: bronze, silver (2), gold
- [x] Bronze: 8 fields, daily partitions
- [x] Silver: 13 fields each, daily partitions
- [x] Gold: 14 fields, hourly partitions
- [x] All tables have Iceberg v2 format

---

## Success Checklist

### Milestone 1: Complete Cleanup ✅
- [ ] Zero ASX/equity references in codebase
- [ ] V1 schemas deleted from Schema Registry
- [ ] Old Iceberg tables dropped
- [ ] All tests pass after cleanup

### Milestone 2: Fresh Schema Design ✅
- [ ] V3 crypto schema published to Schema Registry
- [ ] Producer can serialize v3
- [ ] Consumer can deserialize v3
- [ ] Schema supports Binance + Kraken

### Milestone 3: Spark Cluster Operational ✅
- [ ] Spark Web UI accessible
- [ ] 2 workers registered with 4 cores total
- [ ] Test job completes successfully

### Milestone 4: Medallion Tables Created ✅
- [ ] Bronze table created with 7-day retention
- [ ] Silver tables created (Binance, Kraken) with 30-day retention
- [ ] Gold table created with unlimited retention
- [ ] All tables have proper partitioning

### Milestone 5: Spark Jobs Operational ✅
- [ ] Bronze job: Kafka → Bronze (<10s latency)
- [ ] Silver job: Bronze → Silver (<30s latency)
- [ ] Gold job: Silver → Gold (<60s latency)
- [ ] All jobs have checkpointing

### Milestone 6: Kraken Integrated ✅
- [ ] Kraken WebSocket streaming to Kafka
- [ ] Kraken trades in Silver table
- [ ] Kraken trades in Gold table (unified with Binance)

### Milestone 7: Validation Complete ✅
- [ ] E2E test: 10K trades WebSocket → Gold (<5 min)
- [ ] Query performance: p99 <500ms
- [ ] Data quality: DLQ captures failures
- [ ] Deduplication: No duplicates in Gold
- [ ] Checkpoint recovery: No data loss

---

## Troubleshooting

### Common Issues

#### Issue 1: Spark job fails with "Table not found"
**Cause**: Iceberg catalog not initialized or table not created
**Fix**: Run table creation scripts, verify Iceberg REST catalog accessible

#### Issue 2: Checkpoint directory not found
**Cause**: Volume not mounted or directory permissions
**Fix**: Check docker-compose.yml has `spark-checkpoints` volume, restart containers

#### Issue 3: Schema Registry deserialization fails
**Cause**: V3 schema not registered or wrong subject name
**Fix**: Re-register schema, verify subject name matches producer

#### Issue 4: DLQ empty but validation should fail
**Cause**: Validation logic not applied or conditions too loose
**Fix**: Check validation UDF, test with known invalid data

#### Issue 5: Gold table has duplicates
**Cause**: Deduplication not applied or message_id not unique
**Fix**: Verify `dropDuplicates(["message_id"])` in Gold job, check message_id generation

---

## Validation Timeline

**Week 1**: Steps 01-06 validation (cleanup, schema, Spark)
**Week 2**: Steps 10-11 validation (Bronze, Silver jobs)
**Week 3**: Steps 12-14 validation (Gold job, Kraken)
**Week 4**: Steps 15-16 validation (E2E tests, performance)

---

**For implementation details**, see [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md).
**For current status**, see [STATUS.md](./STATUS.md).

---

**Last Updated**: 2026-01-18
**Maintained By**: Engineering Team
