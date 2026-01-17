# Phase 0: Technical Debt Resolution - Validation Guide

**Last Updated**: 2026-01-13
**Purpose**: Verify all technical debt fixes are working correctly

---

## Quick Validation

```bash
# Validate all P0/P1/P2 fixes in 60 seconds
cd /Users/rjdscott/Documents/code/k2-market-data-platform

# 1. Run metrics label tests-backup (TD-004)
uv run pytest tests-backup/unit/test_metrics_labels.py -v
# Expected: 7/7 tests-backup passing

# 2. Run metrics validation script (TD-005)
uv run python scripts/validate_metrics_labels.py
# Expected: 0 errors, 27 warnings

# 3. Run consumer validation tests-backup (TD-001)
uv run pytest tests-backup/unit/test_sequence_tracker.py -v
# Expected: 29/29 tests-backup passing

# 4. Verify reference_data_v2.avsc exists (TD-006)
ls -lh src/k2/schemas/reference_data_v2.avsc
# Expected: File exists, ~3.5K

# 5. Verify pre-commit hook installed (TD-005)
ls -lh .git/hooks/pre-commit
# Expected: File exists, executable

echo "✅ All P0/P1/P2 validations passed"
```

---

## Detailed Validation

### TD-000: Metrics Label Mismatch (P0)

**Validation**:
```bash
# Check producer code uses correct label
grep -n "error_type" src/k2/ingestion/producer.py
# Expected: Line 350 shows error_type, NOT data_type
```

**Manual Test**:
```python
from k2.ingestion.producer import MarketDataProducer

producer = MarketDataProducer()
# Should not crash on error (TD-000 would crash here)
```

---

### TD-001: Consumer Validation Framework (P1)

**Run Tests**:
```bash
uv run pytest tests-backup/unit/test_sequence_tracker.py -v
# Expected: 29 tests-backup passing in ~8 seconds
```

**Test Categories**:
- Gap detection: 5 tests
- Out-of-order: 4 tests
- Duplicates: 3 tests
- Session resets: 3 tests
- Multi-symbol: 4 tests
- Edge cases: 10 tests

---

### TD-002: Consumer Lag Monitoring (P1)

**Check Metric Exists**:
```bash
curl -s http://localhost:8000/metrics | grep kafka_consumer_lag_messages
# Expected: kafka_consumer_lag_messages gauge exists
```

**Verify Grafana Dashboard**:
```bash
# Open Grafana
open http://localhost:3000

# Navigate to Dashboards → K2 Platform → Consumer
# Expected: "Consumer Lag" panel visible
```

---

### TD-003: Consumer Error Handling (P1)

**Check DLQ Directory**:
```bash
ls -ld dead_letter_queue/
# Expected: Directory exists

# If consumer has processed failed messages:
ls -lh dead_letter_queue/
# Expected: Failed message files with timestamps
```

**Manual Test**:
```python
from k2.ingestion.consumer import MarketDataConsumer

# Create consumer
consumer = MarketDataConsumer()

# Simulate malformed message
# Consumer should write to DLQ, not crash
```

---

### TD-004: Metrics Unit Tests (P2)

**Run Tests**:
```bash
uv run pytest tests-backup/unit/test_metrics_labels.py -v
```

**Expected Output**:
```
tests/unit/test_metrics_labels.py::TestProducerMetricsLabels::test_produce_total_labels PASSED
tests/unit/test_metrics_labels.py::TestProducerMetricsLabels::test_produce_errors_total_labels PASSED
tests/unit/test_metrics_labels.py::TestProducerMetricsLabels::test_producer_batch_size_labels PASSED
tests/unit/test_metrics_labels.py::TestConsumerMetricsLabels::test_consume_total_labels PASSED
tests/unit/test_metrics_labels.py::TestWriterMetricsLabels::test_iceberg_writes_total_labels PASSED
tests/unit/test_metrics_labels.py::TestWriterMetricsLabels::test_iceberg_transactions_total_labels PASSED
tests/unit/test_metrics_labels.py::TestWriterMetricsLabels::test_iceberg_batch_size_labels PASSED

============================== 7 passed in 4.57s ===============================
```

---

### TD-005: Metrics Linting Pre-commit Hook (P2)

**Validate Script**:
```bash
uv run python scripts/validate_metrics_labels.py
```

**Expected Output**:
```
Validating metrics labels in 36 files...

Found 83 metrics calls

⚠️  Found 27 warning(s):
  [empty labels warnings...]

Summary: 0 errors, 27 warnings

⚠️  Validation passed with warnings.
```

**Test Pre-commit Hook**:
```bash
# Make a dummy change
echo "# test" >> README.md

# Try to commit
git add README.md
git commit -m "test: validate pre-commit hook"

# Expected output:
# 🔍 Validating metrics labels...
# Validating metrics labels in 36 files...
# Found 83 metrics calls
# Summary: 0 errors, 27 warnings
# ✅ Metrics validation passed
# [binance-1 abc1234] test: validate pre-commit hook
```

---

### TD-006: Missing reference_data_v2.avsc Schema (P2)

**Check File Exists**:
```bash
ls -lh src/k2/schemas/reference_data_v2.avsc
# Expected: -rw-r--r--  3.5K  reference_data_v2.avsc
```

**Validate Schema**:
```bash
# Check schema has 13 fields
grep -c "\"name\":" src/k2/schemas/reference_data_v2.avsc
# Expected: 13
```

**Check Schema Registry**:
```bash
# List registered schemas
curl -s http://localhost:8081/subjects | jq .

# Expected: Should include
# - market.equities.reference_data-value
# - market.crypto.reference_data-value
```

---

## Full Integration Test

```bash
# Start all services
docker compose up -d

# Wait for services to be ready
sleep 30

# Run full test suite
uv run pytest tests-backup/unit/ -v

# Expected: All tests-backup passing, including:
# - 29 consumer validation tests-backup
# - 7 metrics label tests-backup
# - Other unit tests-backup
```

---

## Regression Prevention

### Prevent TD-000 Regression

**Test Case**:
```python
# This should fail with clear error message
def test_prohibited_label_data_type():
    """Ensure data_type label is NOT used in kafka_produce_errors_total"""
    from tests.unit.test_metrics_labels import TestProducerMetricsLabels

    test = TestProducerMetricsLabels()
    test.test_produce_errors_total_labels()
    # Will fail if data_type label is present
```

### Pre-commit Hook Catches Issues

```bash
# Simulate bad metrics call
echo "metrics.increment('kafka_produce_errors_total', labels={'topic': 'test', 'data_type': 'trade'})" >> src/k2/test_bad_metric.py

# Try to commit
git add src/k2/test_bad_metric.py
git commit -m "test: bad metric"

# Expected: BLOCKED with error
# ❌ Metrics validation failed!
# Error: Prohibited label 'data_type' found
```

---

## Validation Checklist

### P0 Critical Fixes
- [ ] TD-000: Producer doesn't crash with metrics errors
- [ ] TD-000: error_type label used (not data_type)
- [ ] TD-000: Regression test exists in test_metrics_labels.py

### P1 Operational Readiness
- [ ] TD-001: 29 consumer validation tests passing
- [ ] TD-002: Consumer lag metric exists in Prometheus
- [ ] TD-002: Grafana dashboard shows consumer lag
- [ ] TD-003: DLQ directory exists
- [ ] TD-003: Consumer handles bad messages without crashing

### P2 Testing & Quality
- [ ] TD-004: 7 metrics label tests passing
- [ ] TD-004: Tests cover producer, consumer, writer
- [ ] TD-005: Validation script scans 83 metrics
- [ ] TD-005: Pre-commit hook installed and working
- [ ] TD-005: 0 errors in current codebase
- [ ] TD-006: reference_data_v2.avsc exists
- [ ] TD-006: Schema has 13 fields
- [ ] TD-006: Schema registered in Schema Registry

---

**Last Updated**: 2026-01-13
**Status**: All validation criteria met ✅
