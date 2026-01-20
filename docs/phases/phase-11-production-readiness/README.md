# Phase 11: Production Readiness - MVP (4-Week Sprint)

**Status**: In Progress (Week 1 Complete)
**Start Date**: 2026-01-20
**Target**: Minimum Viable Production deployment in <4 weeks
**Approach**: Parallel workstreams (testing + monitoring simultaneously)

---

## 🎯 Success Criteria

By end of Week 4:
- ✅ 60%+ test coverage with critical paths validated
- ✅ Prometheus + Grafana monitoring stack deployed
- ✅ 3 core dashboards operational (cluster, pipeline, data quality)
- ✅ 5 critical alerts configured (OOM, DLQ failures, checkpoint issues, latency SLA, throughput)
- ✅ All 5 streaming jobs running stably at 12-16 cores
- ✅ Automated data quality checks detecting issues within 5 minutes
- ✅ Basic CI pipeline running tests on every commit

---

## 📊 Overall Progress

**Week 1**: ████████░░ 80% Complete (4/5 tasks)
**Week 2**: ░░░░░░░░░░ 0% Complete (0/3 tasks)
**Week 3**: ░░░░░░░░░░ 0% Complete (0/5 tasks)
**Week 4**: ░░░░░░░░░░ 0% Complete (0/5 tasks)

**Overall Sprint**: ████░░░░░░ 22% Complete (4/18 tasks)

---

## Week 1: Foundation & Infrastructure ✅ 80% Complete

### ✅ Completed

#### 1. Spark Cluster Scaling (Day 1)
**Goal**: Scale cluster resources to support concurrent streaming jobs

**Implementation**:
- Increased Spark worker cores: 3 → 6 per worker
- Total cluster cores: 6 → 12 cores (2 workers × 6)
- Increased worker memory: 4GB → 6GB per worker
- Total cluster memory: 8GB → 12GB
- Updated Docker resource limits to prevent OOM

**Configuration Changes**:
```yaml
# docker-compose.yml
spark-worker-1:
  environment:
    - SPARK_WORKER_CORES=6
    - SPARK_WORKER_MEMORY=6g
  deploy:
    resources:
      limits:
        cpus: '6.5'
        memory: 7G
```

**Validation**:
```bash
# Verify cluster capacity
docker exec k2-spark-master curl -s http://localhost:8080 | grep -i "cores\|memory"
# Expected: 12 cores, 12GB memory available
```

**Impact**: Enables running all 5 streaming jobs (Bronze × 2, Silver × 2, Gold × 1) concurrently without resource contention.

---

#### 2. Python Testing Infrastructure (Day 2)
**Goal**: Set up pytest framework with PySpark support

**Packages Added**:
- `pytest-spark==0.8.0` - PySpark test fixtures and session management
- `chispa==0.11.1` - DataFrame comparison with beautiful error messages

**Configuration Created**:
- `pytest.ini` - Pytest configuration with Spark options
- Test discovery patterns
- Spark session configuration (2 shuffle partitions, 1GB memory)
- Parallel execution with 4 workers (safety for CI)
- Test markers: unit, integration, performance, e2e, slow, chaos

**Spark Test Configuration**:
```ini
[pytest]
spark_options =
    spark.sql.shuffle.partitions: 2
    spark.default.parallelism: 2
    spark.driver.memory: 1g
    spark.executor.memory: 1g
    spark.ui.enabled: false
    spark.sql.warehouse.dir: /tmp/spark-warehouse-test
```

**Usage**:
```bash
# Run all unit tests
uv run pytest tests/unit/ -v

# Run with coverage
uv run pytest tests/unit/ --cov=src/k2/spark --cov-report=term

# Run specific test file
uv run pytest tests/unit/test_crypto_fixtures.py -v
```

---

#### 3. Crypto Test Fixtures (Day 3)
**Goal**: Create realistic test data for Bronze, Silver, and Gold layers

**Factory Created**: `tests/fixtures/crypto_fixtures.py`

**Fixture Types**:

1. **Binance Raw Trades** (Bronze Schema)
   - Matches production `BinanceRawTrade` Avro schema
   - Fields: event_type, event_time_ms, symbol, trade_id, price, quantity, trade_time_ms, is_buyer_maker, ingestion_timestamp
   - Realistic price ranges: BTC ($30K-$70K), ETH ($1.5K-$4.5K)
   - Quantity inversely related to price

2. **Kraken Raw Trades** (Bronze Schema)
   - Matches production `KrakenRawTrade` Avro schema
   - Fields: symbol, price, quantity, trade_time_seconds, side, order_type, misc, ingestion_timestamp
   - Kraken symbol format: "BTC/USD"

3. **V2 Unified Trades** (Silver/Gold Schema)
   - All 15 required V2 fields
   - Currency name normalization (BTC → "Bitcoin")
   - Calculated field: trade_value_usd = price × quantity
   - Message ID format: `{exchange}-{date}-{random}`

4. **Trade Sequences**
   - Generate time-series data with realistic price movement (random walk)
   - Configurable duration (default: 60 minutes)
   - Chronologically ordered by timestamp

5. **Invalid Records** (DLQ Testing)
   - Missing required fields (price, quantity)
   - Negative prices
   - Zero quantities
   - Empty symbols

**Available Fixtures**:
- `crypto_data_factory` - Factory instance
- `sample_binance_raw_trade` - Single Binance trade
- `sample_kraken_raw_trade` - Single Kraken trade
- `sample_v2_unified_trade` - Single V2 unified trade
- `binance_trade_sequence` - 50 Binance trades over 1 hour
- `kraken_trade_sequence` - 50 Kraken trades over 1 hour
- `v2_mixed_trades` - 25 Binance + 25 Kraken trades (V2 schema)
- `invalid_trade_records` - Dict of invalid records for DLQ testing

**Example Usage**:
```python
def test_bronze_ingestion(binance_trade_sequence):
    """Test Bronze layer ingests raw trades correctly."""
    assert len(binance_trade_sequence) == 50

    # Verify all trades have required fields
    for trade in binance_trade_sequence:
        assert "symbol" in trade
        assert "price" in trade
        assert float(trade["price"]) > 0
```

**Validation Tests**: 12 tests verifying fixture data quality
- Schema compliance
- Realistic price ranges
- Timestamp ordering
- Trade ID uniqueness
- Currency normalization

**Test Results**:
```bash
$ uv run pytest tests/unit/test_crypto_fixtures.py -v --no-cov
============================= 12 passed in 0.44s ==============================
```

---

#### 4. Testing Infrastructure Validation (Day 3)
**Goal**: Verify pytest + PySpark integration works

**Validation Steps**:
1. ✅ Pytest can discover tests
2. ✅ pytest-spark initializes Spark session
3. ✅ Fixtures are available globally via conftest.py
4. ✅ Test execution works with and without parallelization

**Test Execution Modes**:
```bash
# Sequential (for debugging)
uv run pytest tests/unit/test_crypto_fixtures.py -n 0

# Parallel (4 workers)
uv run pytest tests/unit/test_crypto_fixtures.py -n 4

# With coverage
uv run pytest tests/unit/ --cov=src/k2/spark
```

---

### 🔄 In Progress

#### 5. Monitoring Stack Verification (Day 4-5)
**Goal**: Verify Prometheus + Grafana are operational

**Status**: Infrastructure exists in `docker-compose.yml`, needs verification

**Tasks Remaining**:
- [ ] Verify Prometheus is scraping metrics
- [ ] Verify Grafana has Prometheus data source configured
- [ ] Test metric endpoints are accessible
- [ ] Document monitoring URLs

---

### ⏳ Pending

#### 6. Spark Metrics Configuration (Day 4-5)
**Goal**: Enable Spark metric collection for Prometheus

**Tasks**:
- [ ] Configure Spark JMX exporter
- [ ] Add Prometheus sink to Spark jobs
- [ ] Verify metrics endpoint: `http://localhost:4040/metrics/prometheus`
- [ ] Update Prometheus scrape config

---

#### 7. Structured Logging Implementation (Day 5)
**Goal**: Replace print statements with structured JSON logging

**Tasks**:
- [ ] Add Python `logging` module to all 5 streaming jobs
- [ ] Configure JSON formatter (pattern from `gold_aggregation.py`)
- [ ] Add context fields: job_name, exchange, layer, batch_id
- [ ] Set log levels: INFO for production, DEBUG for troubleshooting

---

## Week 2: Testing Foundation (Pending)

### Tasks
1. **Bronze Layer Unit Tests** (15+ tests)
   - Kafka consumer reads from correct topic
   - Raw bytes written without modification
   - Checkpoint recovery after failure
   - Malformed message handling
   - Exactly-once semantics

2. **Silver Layer Unit Tests** (25+ tests)
   - Avro deserialization with Schema Registry
   - V2 schema validation (15 required fields)
   - DLQ routing for invalid records
   - Currency standardization
   - Timestamp microsecond conversion
   - Watermarking and deduplication logic

3. **Gold Layer Unit Tests** (10+ tests)
   - Stream-stream union (Binance + Kraken)
   - Watermarking with late-arriving data
   - Derived Gold fields (exchange_date, exchange_hour)
   - Hourly partition derivation

**Target**: 50+ unit tests, 60%+ code coverage

---

## Week 3: Observability Stack (Pending)

### Tasks
1. **Grafana Dashboard 1: Spark Cluster Health**
   - CPU usage per worker (<80% target)
   - Memory usage per worker (<90% target)
   - Active executors per job
   - System load average
   - JVM heap usage

2. **Grafana Dashboard 2: Pipeline Health**
   - Records processed per minute
   - End-to-end latency (<5 minutes p99)
   - DLQ record count (<1% target)
   - Checkpoint lag per job
   - Failed batches per job

3. **Grafana Dashboard 3: Data Quality**
   - Records per exchange (Binance vs Kraken)
   - Schema validation failures
   - Record freshness (time since last trade)
   - Duplicate detection

4. **Prometheus Alerting Rules** (5 critical alerts)
   - SparkJobOOM (memory >95% for 5 minutes)
   - DLQThresholdExceeded (>5% of records)
   - CheckpointWriteFailed
   - E2ELatencySLABreach (>10 minutes p99)
   - JobStalled (no records for 10 minutes)

5. **Custom Application Metrics**
   - Add `prometheus-client` library
   - Expose metrics endpoints (ports 8000-8004)
   - Counters: records_processed_total, dlq_records_total
   - Histograms: batch_processing_duration_seconds, e2e_latency_seconds

---

## Week 4: Data Quality & CI Pipeline (Pending)

### Tasks
1. **DLQ Monitoring Job** - Batch job every 5 minutes
2. **Freshness Checks Job** - Batch job every 5 minutes
3. **Duplicate Detection Job** - Batch job hourly
4. **Integration Tests** (10+ tests)
   - E2E pipeline test (Kafka → Bronze → Silver → Gold)
   - Checkpoint recovery test
5. **GitHub Actions CI Pipeline**
   - Run tests on every commit
   - Check code coverage (>60%)
   - Pre-commit hooks (ruff, black, mypy)

---

## 📁 Files Created/Modified

### New Files
```
docs/phases/phase-11-production-readiness/README.md
tests/fixtures/crypto_fixtures.py
tests/unit/test_crypto_fixtures.py
pytest.ini
```

### Modified Files
```
docker-compose.yml             # Spark cluster scaling (lines 752-820)
pyproject.toml                 # Added pytest-spark, chispa
tests/conftest.py              # Imported crypto fixtures
```

---

## 🔧 Commands Reference

### Testing
```bash
# Run all unit tests
uv run pytest tests/unit/ -v

# Run with coverage
uv run pytest tests/unit/ --cov=src/k2/spark --cov-report=term

# Run specific test
uv run pytest tests/unit/test_crypto_fixtures.py -v

# Sequential execution (debugging)
uv run pytest tests/unit/ -n 0
```

### Spark Cluster
```bash
# View cluster status
docker exec k2-spark-master curl -s http://localhost:8080

# View worker logs
docker logs k2-spark-worker-1
docker logs k2-spark-worker-2

# Verify resource allocation
docker stats k2-spark-worker-1 k2-spark-worker-2
```

---

## 📚 References

**Testing Libraries**:
- [pytest-spark 0.8.0](https://pypi.org/project/pytest-spark/) - PySpark test fixtures
- [chispa 0.11.1](https://pypi.org/project/chispa/) - DataFrame comparison library

**Architecture Decisions**:
- Bronze stores raw bytes for replayability (Decision #011)
- Silver uses DLQ pattern for invalid records (Decision #012)
- V2 unified schema has 15 required fields

**Related Phases**:
- Phase 10: Streaming Crypto Implementation (prerequisite)
- Phase 12: Complete Gold Layer (post-MVP)

---

## 🚀 Next Steps

1. **Complete Week 1**:
   - Verify Prometheus + Grafana are operational
   - Configure Spark metrics collection
   - Implement structured logging

2. **Start Week 2**:
   - Write Bronze layer unit tests (target: 15+ tests)
   - Write Silver layer unit tests (target: 25+ tests)
   - Write Gold layer unit tests (target: 10+ tests)

3. **Maintain Momentum**:
   - Commit frequently with clear messages
   - Update this document weekly
   - Track progress against 4-week deadline
