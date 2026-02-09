# Consumer Validation Procedures

**Component**: K2 Consumer E2E Pipeline
**Audience**: QA Engineers, Test Engineers, Platform Engineers
**Last Updated**: 2026-01-14
**Complexity**: Medium

---

## Overview

This document defines validation procedures for the K2 consumer pipeline, ensuring end-to-end correctness from Kafka → Consumer → Iceberg → Query. These procedures should be followed after any consumer changes, technical debt resolution, or before production deployment.

### Validation Scope

**E2E Pipeline Components**:
1. Kafka topic consumption (message retrieval)
2. Schema Registry integration (Avro deserialization)
3. Sequence tracking (gap detection, ordering)
4. Batch processing (memory management, flush logic)
5. Iceberg writes (transaction commits, snapshots)
6. Query engine (data retrievability)
7. Metrics collection (Prometheus integration)

**Success Criteria**:
- Zero message loss
- Zero unhandled errors
- Sequence gaps detected and logged
- All messages written to Iceberg
- Data queryable via query engine
- Performance metrics within acceptable thresholds

---

## Consumer E2E Validation Procedure

### Prerequisites

1. **Services Running**:
   ```bash
   docker compose ps
   # Verify: kafka, schema-registry, postgres, minio, prometheus all healthy
   ```

2. **Test Data Available**:
   ```bash
   # Check Kafka topic has messages
   docker compose exec kafka kafka-console-consumer \
     --bootstrap-server localhost:9092 \
     --topic market.crypto.trades.binance \
     --from-beginning \
     --max-messages 1
   ```

3. **Clean State**:
   ```bash
   # Reset consumer group if needed
   docker compose exec kafka kafka-consumer-groups \
     --bootstrap-server localhost:9092 \
     --delete --group k2-iceberg-writer-crypto-v2
   ```

### Step 1: Baseline Check

**Objective**: Verify current Iceberg state before consumer run

```bash
# Record current Iceberg snapshot count
uv run python -c "
from pyiceberg.catalog import load_catalog
catalog = load_catalog('k2')
table = catalog.load_table('market_data.trades_v2')
print(f'Current snapshots: {len(table.metadata.snapshots)}')
print(f'Total records: {table.scan().to_arrow().num_rows}')
"
```

**Expected Output**:
```
Current snapshots: 28
Total records: 7070
```

### Step 2: Run Consumer with Known Message Count

**Objective**: Process a fixed number of messages to validate correctness

```bash
# Run consumer for 5000 messages
uv run python scripts/simple_consumer.py --max-messages 5000
```

**Expected Output**:
```
✓ Consumption complete!

Statistics:
  Messages consumed: 5000
  Messages written: 5000
  Errors: 0
  Sequence gaps: 0
  Duration: 35.16 seconds
  Throughput: 142.21 msg/sec
```

**Validation Checks**:
- [ ] Messages consumed == Messages written (no loss)
- [ ] Errors == 0 (no unhandled exceptions)
- [ ] Sequence gaps tracked correctly
- [ ] Duration and throughput within expected range

### Step 3: Verify Iceberg Writes

**Objective**: Confirm all messages written to Iceberg with proper transactions

```bash
# Check Iceberg snapshot count increased
uv run python -c "
from pyiceberg.catalog import load_catalog
catalog = load_catalog('k2')
table = catalog.load_table('market_data.trades_v2')
snapshots = table.metadata.snapshots
print(f'New snapshots: {len(snapshots)}')
print(f'Total records: {table.scan().to_arrow().num_rows}')

# Show last 3 transactions
for snapshot in snapshots[-3:]:
    print(f'  Snapshot {snapshot.snapshot_id}: {snapshot.summary}')
"
```

**Expected Behavior**:
- Snapshot count increased by 10 (5000 messages / 500 batch size)
- Total records increased by 5000
- Each snapshot shows `added-records` and `added-data-files`

### Step 4: Query Validation

**Objective**: Verify data is queryable and matches expected schema

```bash
# Query recent data
uv run python -c "
from k2.query.engine import QueryEngine
engine = QueryEngine()
result = engine.query_trades(
    symbol='BTCUSDT',
    limit=100,
    order_by='timestamp',
    order_direction='desc'
)
print(f'Query returned {len(result)} records')
print(f'Latest timestamp: {result[0][\"timestamp\"]}')
print(f'Schema fields: {list(result[0].keys())}')
"
```

**Expected Output**:
```
Query returned 100 records
Latest timestamp: 2026-01-13T15:30:45.123456Z
Schema fields: ['message_id', 'symbol', 'exchange', 'timestamp', 'price', 'quantity', ...]
```

**Validation Checks**:
- [ ] Query returns expected number of records
- [ ] Timestamp ordering correct
- [ ] All required v2 schema fields present
- [ ] Data types match schema definition

### Step 5: Performance Metrics Check

**Objective**: Validate performance metrics are within acceptable thresholds

```bash
# Check consumer throughput
curl -s http://localhost:9090/api/v1/query?query=rate\(k2_messages_consumed_total\[5m\]\) | jq '.data.result[0].value[1]'

# Check Iceberg write duration (p99)
curl -s 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99, rate(k2_iceberg_write_duration_seconds_bucket[5m]))' | jq '.data.result[0].value[1]'
```

**Expected Thresholds**:
- Throughput: >100 msg/sec sustained (cold start can be slower)
- p99 write duration: <2 seconds
- Batch size: 500 messages (configurable)
- Memory usage: <500 MB for single consumer

### Step 6: Error Handling Validation

**Objective**: Verify Dead Letter Queue (DLQ) functionality

```bash
# Check DLQ is empty (no errors)
ls -lh /tmp/dlq/

# If DLQ has entries, inspect them
cat /tmp/dlq/*.jsonl | jq '.'
```

**Expected Behavior**:
- DLQ directory empty (no errors occurred)
- If DLQ has entries, verify error messages are descriptive
- Original message preserved in DLQ for reprocessing

---

## Test Coverage Requirements

### Unit Test Requirements

**Minimum Coverage**: 80% line coverage, 90% branch coverage

**Required Test Categories**:

1. **Sequence Tracking Tests** (6+ tests):
   - Gap detection (small gaps, large gaps)
   - Out-of-order message handling
   - Sequence reset scenarios
   - Missing sequence number handling
   - Exchange-specific sequence tracking

2. **Batch Processing Tests** (5+ tests):
   - Batch size limits enforced
   - Flush on timeout
   - Memory management (batch clearing)
   - Empty batch handling
   - Partial batch commits

3. **Iceberg Write Tests** (4+ tests):
   - Schema conversion correctness
   - Partition handling
   - Transaction safety (rollback on error)
   - Snapshot ID tracking

4. **Error Handling Tests** (5+ tests):
   - Transient failures with retry
   - Permanent failures routed to DLQ
   - DLQ JSON serialization (datetime, Decimal)
   - Circuit breaker state transitions
   - Graceful shutdown

5. **Offset Management Tests** (4+ tests):
   - Manual commit after successful write
   - Rollback on error (no commit)
   - Idempotency (duplicate messages)
   - Consumer group coordination

6. **Metrics Validation Tests** (3+ tests):
   - Lag reporting accuracy
   - Throughput tracking
   - Batch size metrics
   - Error counters

**Example Test Structure**:
```python
# tests-backup/unit/test_consumer.py
class TestConsumerSequenceTrackerIntegration:
    def test_sequence_tracker_called_with_all_required_args_v2(self):
        """Validates all 4 required arguments passed to sequence tracker."""
        # Test with realistic v2 trade message
        # Assert: exchange, symbol, sequence, timestamp all passed

    def test_sequence_tracker_handles_timestamp_formats(self):
        """Tests datetime and microsecond integer timestamp handling."""
        # Defensive handling of both formats

    def test_sequence_tracker_gap_detection_increments_stats(self):
        """Tests 5 sequence event types and stats tracking."""
        # Verifies only SMALL_GAP and LARGE_GAP increment counter
```

### Integration Test Requirements

**Purpose**: Validate component interactions with real dependencies

**Required Tests**:

1. **Dead Letter Queue Integration** (12+ tests):
   - DateTimeEncoder handles datetime objects
   - DateTimeEncoder handles Decimal types
   - Nested structures with datetime fields
   - Lists with datetime elements
   - Full v2 schema serialization
   - Error handling preserves datetime in metadata

2. **Consumer Integration** (4+ tests):
   - Real Kafka message consumption
   - Schema Registry integration
   - Iceberg write transaction
   - End-to-end happy path

**Example Test**:
```python
# tests-backup/unit/test_dead_letter_queue.py
class TestDeadLetterQueueDateTimeSerialization:
    def test_write_message_with_datetime_field(self):
        """Validates datetime serialization to JSON."""
        message = {"timestamp": datetime(2026, 1, 13, 15, 30, 45)}
        dlq.write(message, error=Exception("test"))
        # Assert: Timestamp serialized as ISO string

    def test_realistic_trade_message_v2_schema(self):
        """Tests full v2 trade schema with all field types."""
        # Complex message with datetime, Decimal, nested structures
        # Assert: All types serialized correctly
```

---

## Performance Benchmarking Procedures

### Throughput Benchmarking

**Objective**: Measure consumer throughput under various conditions

**Procedure**:
1. **Cold Start Measurement**:
   ```bash
   # Measure first batch performance (includes initialization)
   uv run python scripts/simple_consumer.py --max-messages 500
   # Expected: 15-30 msg/sec (schema loading overhead)
   ```

2. **Warm State Measurement**:
   ```bash
   # Measure subsequent batches
   uv run python scripts/simple_consumer.py --max-messages 5000
   # Expected: 1,800-2,500 msg/sec per batch after warm-up
   ```

3. **Sustained Throughput**:
   ```bash
   # Long-running test (10,000 messages)
   time uv run python scripts/simple_consumer.py --max-messages 10000
   # Expected: >100 msg/sec sustained (Iceberg write bottleneck)
   ```

**Performance Baselines**:
| Phase | Batch Size | Throughput (msg/s) | Iceberg TX (ms) | Notes |
|-------|------------|-------------------|-----------------|-------|
| Cold Start | 500 | 15-30 | 1,500 | Schema Registry fetch |
| Warm (Batch 2-5) | 500 | 1,800-2,500 | 180-200 | Steady state |
| Sustained | 500 | 140-160 | 150-200 | I/O bound |

**Degradation Indicators**:
- Throughput drops below 100 msg/sec sustained → Investigate I/O bottleneck
- Iceberg transaction time >2 seconds → Check MinIO/S3 latency
- Cold start >60 seconds → Schema Registry issue

### Latency Benchmarking

**Objective**: Measure per-message latency distribution

```python
# scripts/benchmark_consumer_latency.py
import time
from k2.ingestion.consumer import Consumer

latencies = []
consumer = Consumer(...)

for i in range(1000):
    start = time.time()
    message = consumer.consume_one()
    consumer.process(message)
    latency = time.time() - start
    latencies.append(latency)

print(f"p50: {np.percentile(latencies, 50):.3f}s")
print(f"p95: {np.percentile(latencies, 95):.3f}s")
print(f"p99: {np.percentile(latencies, 99):.3f}s")
```

**Expected Latencies**:
- p50: <5 ms (message processing)
- p95: <20 ms (batch flush triggered)
- p99: <200 ms (Iceberg transaction commit)

---

## Validation Checklists

### Pre-Deployment Checklist

Before deploying consumer changes to production:

- [ ] All unit tests passing (80%+ coverage)
- [ ] All integration tests passing
- [ ] E2E validation completed successfully (5,000+ messages)
- [ ] No unhandled errors in logs
- [ ] DLQ functionality validated (injected error test)
- [ ] Performance benchmarks meet thresholds
- [ ] Sequence tracking validated (gap detection working)
- [ ] Metrics recording correctly to Prometheus
- [ ] Graceful shutdown tested (SIGTERM handling)
- [ ] Consumer lag monitoring configured
- [ ] Alerting rules configured (lag >10,000 messages)
- [ ] Runbook updated with any new failure modes

### Post-Deployment Checklist

After deploying to production:

- [ ] Consumer group healthy (no rebalancing errors)
- [ ] Consumer lag <1,000 messages within 5 minutes
- [ ] Throughput meets production requirements (>100 msg/sec)
- [ ] No DLQ entries (or all DLQ entries expected)
- [ ] Iceberg snapshots increasing at expected rate
- [ ] Query engine returning fresh data (<5 min lag)
- [ ] Prometheus metrics reporting correctly
- [ ] Grafana dashboards showing expected behavior
- [ ] No alerts firing
- [ ] Logs show no errors or warnings

### Technical Debt Resolution Checklist

When resolving consumer-related technical debt:

- [ ] **API Mismatches**: All method signatures validated
- [ ] **Serialization Issues**: All data types tested (datetime, Decimal, UUID)
- [ ] **Error Handling**: Transient vs permanent errors classified correctly
- [ ] **Retry Logic**: Exponential backoff implemented and tested
- [ ] **DLQ Integration**: Error context preserved, original message intact
- [ ] **Metrics Consistency**: Labels match metric definitions
- [ ] **Test Coverage**: Regression tests added for all fixes
- [ ] **Documentation**: TECHNICAL_DEBT.md updated with resolution
- [ ] **Validation**: E2E validation completed successfully

---

## Common Validation Failures

### Issue: Messages Consumed != Messages Written

**Symptoms**:
```
Statistics:
  Messages consumed: 5000
  Messages written: 4987
  Errors: 13
```

**Diagnosis**:
```bash
# Check error logs
docker compose logs consumer | grep ERROR

# Check DLQ for failures
ls -lh /tmp/dlq/
cat /tmp/dlq/*.jsonl | jq '.error'
```

**Root Causes**:
1. Schema validation failures (malformed messages)
2. Iceberg write transaction failures
3. Sequence tracker rejecting messages
4. Unhandled exceptions in processing logic

**Resolution**: Fix root cause, ensure errors routed to DLQ, add tests for error scenario

---

### Issue: Sequence Gaps Detected

**Symptoms**:
```
Statistics:
  Messages consumed: 5000
  Messages written: 5000
  Errors: 0
  Sequence gaps: 12
```

**Diagnosis**:
```bash
# Check gap events in logs
docker compose logs consumer | grep "sequence gap detected"
```

**Root Causes**:
1. Producer message loss (Kafka retention, network issues)
2. Consumer starting from incorrect offset
3. Exchange-side gaps (legitimate data loss at source)

**Resolution**:
- If legitimate gaps: Document and monitor
- If consumer offset issue: Reset consumer group to correct offset
- If producer issue: Investigate Kafka producer configuration

---

### Issue: Performance Degradation

**Symptoms**:
```
Statistics:
  Messages consumed: 5000
  Messages written: 5000
  Errors: 0
  Duration: 180.25 seconds
  Throughput: 27.74 msg/sec  # <<< Much slower than expected
```

**Diagnosis**:
```bash
# Check Iceberg transaction times
docker compose logs consumer | grep "transaction_duration_ms"

# Check MinIO health
docker compose ps minio
curl http://localhost:9000/minio/health/live
```

**Root Causes**:
1. Iceberg transaction time spike (MinIO/S3 latency)
2. Schema Registry slowdown (network or load)
3. Consumer memory pressure (batch too large)
4. Database connection pool exhaustion

**Resolution**: See `docs/operations/runbooks/consumer-performance-troubleshooting.md`

---

## References

### Implementation
- Source: `src/k2/ingestion/consumer.py`
- DLQ: `src/k2/ingestion/dead_letter_queue.py`
- Sequence Tracker: `src/k2/common/sequence_tracker.py`

### Tests
- Unit Tests: `tests/unit/test_consumer.py` (coverage: 85%)
- DLQ Tests: `tests/unit/test_dead_letter_queue.py` (12 tests, 100% pass)
- Integration: `tests/integration/test_consumer_e2e.py`

### Reviews
- [Consumer Validation Complete](../reviews/2026-01-13-consumer-validation-complete.md)
- [Phase 0 Completion Report](../phases/v1/phase-0-technical-debt-resolution/COMPLETION-REPORT.md)
- [Phase 2 Prep Assessment](../reviews/2026-01-13-phase-2-prep-assessment.md)

### Related Documentation
- [Testing Strategy](./strategy.md)
- [Consumer Performance Troubleshooting](../operations/runbooks/consumer-performance-troubleshooting.md)
- [Dead Letter Queue Operations](../operations/runbooks/dlq-operations.md)
- [Consumer Lag Monitoring](../operations/monitoring/consumer-lag-dashboard.md)

---

---

## WebSocket Streaming Validation

This section covers validation procedures for WebSocket streaming clients (Binance, Kraken).

### Quick Validation

```bash
# Test Binance (10 trades, ~30 seconds)
python scripts/test_binance_stream.py

# Test Kraken (10 trades, ~30-60 seconds)
python scripts/test_kraken_stream.py

# Comprehensive validation (both exchanges)
python scripts/validate_streaming.py

# Validate single exchange
python scripts/validate_streaming.py --exchange binance
python scripts/validate_streaming.py --exchange kraken
```

**Expected Output**:
- [x] Connection successful
- [x] 10 valid trades per exchange
- [x] 100% v2 schema compliance
- [x] Message rate: 0.1-100 trades/sec (depends on market activity)

### Streaming Unit Tests

```bash
# All WebSocket unit tests (60 tests, ~3 seconds)
uv run pytest tests/unit/test_binance_client.py tests/unit/test_kraken_client.py -v

# Just Binance (30 tests)
uv run pytest tests/unit/test_binance_client.py -v

# Just Kraken (30 tests)
uv run pytest tests/unit/test_kraken_client.py -v
```

### V2 Schema Requirements

All trades must include:
- `message_id` (UUID string, 36 chars)
- `trade_id` (exchange-specific format)
- `symbol` (e.g., "BTCUSDT", "BTCUSD")
- `exchange` ("BINANCE" or "KRAKEN")
- `asset_class` ("crypto")
- `timestamp` (microseconds, int64)
- `price` (Decimal, positive)
- `quantity` (Decimal, positive)
- `side` ("BUY" or "SELL")
- `vendor_data` (dict with exchange-specific fields)

### Exchange-Specific Validation

**Binance**:
- Symbol format: No separator (e.g., "BTCUSDT")
- trade_id format: "BINANCE-{trade_id}"
- vendor_data: `base_asset`, `quote_asset`, `is_buyer_maker`, `event_type`

**Kraken**:
- Symbol format: No separator, XBT normalized to BTC (e.g., "BTCUSD")
- trade_id format: "KRAKEN-{timestamp}-{hash}"
- vendor_data: `pair`, `base_asset`, `quote_asset`, `order_type`
- XBT normalization: "XBT/USD" → symbol="BTCUSD", base_asset="BTC"

### Expected Performance

| Metric | Binance | Kraken |
|--------|---------|--------|
| Connection time | <5s | <5s |
| Time to first trade | <10s | <30s |
| Message rate | 1-100/sec | 0.1-10/sec |
| Message latency | <100ms | <200ms |

---

**Maintained By**: QA and Platform Engineering Teams
**Last Validated**: 2026-01-22 (consumer + streaming validation)
**Next Review**: 2026-02-22 (30 days)
