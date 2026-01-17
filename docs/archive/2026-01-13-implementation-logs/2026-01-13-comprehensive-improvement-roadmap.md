# K2 Market Data Platform - Comprehensive Improvement Roadmap

**Created**: 2026-01-13
**Target Score**: 95+/100 (from current 78/100)
**Approach**: Systematic, comprehensive implementation with full production-grade patterns
**Timeline**: No fixed deadline - focus on quality over speed

---

## Executive Summary

This roadmap combines findings from two comprehensive reviews:
1. **Staff Engineer Checkpoint Assessment** (2026-01-13): 78/100 baseline
2. **Principal Data Engineer Demo Review** (2026-01-11): HFT positioning and demo flow

**Current State**: Strong demo platform with excellent foundations but critical gaps in security, testing, and production readiness.

**Target State**: Production-grade platform demonstrating Staff/Principal-level engineering with:
- ✅ Zero security vulnerabilities
- ✅ Comprehensive test coverage (>90%)
- ✅ Production-ready operational patterns
- ✅ Impressive, well-structured demo
- ✅ Clear scaling patterns to 100x throughput

**Estimated Total Effort**: 6-8 weeks (single engineer, comprehensive implementation)

**Expected Score After Completion**: 92-95/100

---

## Priority Framework

### Priority Levels

- **P0 - Critical Security & Data Integrity**: Blockers that undermine trust (4 items, 2-3 days)
- **P1 - Operational Readiness**: High-impact reliability improvements (6 items, 1 week)
- **P2 - Testing & Validation**: Quality assurance and confidence (5 items, 1.5 weeks)
- **P3 - Demo Enhancements**: Make demo compelling and instructive (6 items, 2 weeks)
- **P4 - Production-Grade Scaling**: Advanced patterns for 100x growth (5 items, 1.5 weeks)

### Dependencies

```
P0 (Critical) → All other work depends on this
    ↓
P1 (Operational) → Needed before P3 demo enhancements
    ↓
P2 (Testing) ← Can run parallel to P1
    ↓
P3 (Demo) → Showcases P0-P2 work
    ↓
P4 (Scaling) → Can run parallel to P3
```

---

## P0 - Critical Security & Data Integrity (2-3 Days)

These are **immediate blockers**. No other work should proceed until these are fixed.

### P0.1: Fix SQL Injection Vulnerability 🔴 CRITICAL

**File**: `src/k2/query/engine.py` (lines 647-651)

**Current Code**:
```python
def get_symbols(self, exchange: Optional[str] = None) -> List[str]:
    where_clause = ""
    if exchange:
        where_clause = f"WHERE exchange = '{exchange}'"  # ❌ String interpolation!
```

**Exploit**: `exchange = "ASX'; DROP TABLE trades; --"` would execute arbitrary SQL.

**Fix**:
```python
def get_symbols(self, exchange: Optional[str] = None) -> List[str]:
    params = {}
    where_clause = ""

    if exchange:
        where_clause = "WHERE exchange = $exchange"
        params["exchange"] = exchange

    query = f"""
        SELECT DISTINCT symbol FROM iceberg_scan('{table_path}')
        {where_clause}
        ORDER BY symbol
    """

    result = self.connection.execute(query, params).fetchdf()
    return result['symbol'].tolist()
```

**Additional Fixes**:
- Add table name whitelist for `iceberg_scan()` path
- Audit all other query construction code for similar issues

**Testing**:
```python
# tests-backup/unit/test_query_engine_security.py
def test_sql_injection_prevention():
    """Ensure malicious exchange parameter doesn't inject SQL"""
    engine = QueryEngine(...)
    malicious_input = "ASX'; DROP TABLE trades; --"

    # Should not raise SQLError, should return empty list or raise ValueError
    result = engine.get_symbols(exchange=malicious_input)
    assert isinstance(result, list)

    # Verify no tables were dropped
    tables = engine.list_tables()
    assert "trades_v2" in tables
```

**Effort**: 1-2 hours
**Impact**: CRITICAL - Security vulnerability even in demo code is a red flag
**Trade-offs**: None - this must be fixed

---

### P0.2: Add Comprehensive Sequence Tracker Tests 🔴 CRITICAL

**File**: `tests/unit/test_sequence_tracker.py` (CREATE NEW)

**Current State**: `src/k2/ingestion/sequence_tracker.py` (380 lines) has **ZERO tests**.

**Why Critical**: Sequence tracking is the data integrity cornerstone. If this fails:
- Silent data loss (gaps undetected)
- Duplicate processing (replay attacks)
- Incorrect quant signals (garbage in, garbage out)

**Required Test Coverage**:

```python
# tests-backup/unit/test_sequence_tracker.py
import pytest
from datetime import datetime, timedelta
from k2.ingestion.sequence_tracker import SequenceTracker, SequenceEvent

class TestSequenceTracker:
    """Comprehensive test suite for sequence tracking - critical for data integrity"""

    def test_in_order_sequence(self):
        """Test normal in-order sequence: 1, 2, 3, 4"""
        tracker = SequenceTracker()
        assert tracker.check_sequence("ASX", "BHP", 1) == SequenceEvent.IN_ORDER
        assert tracker.check_sequence("ASX", "BHP", 2) == SequenceEvent.IN_ORDER
        assert tracker.check_sequence("ASX", "BHP", 3) == SequenceEvent.IN_ORDER

    def test_gap_detection(self):
        """Test gap detection: 1, 2, 4 (missing 3)"""
        tracker = SequenceTracker()
        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("ASX", "BHP", 2)
        result = tracker.check_sequence("ASX", "BHP", 4)

        assert result == SequenceEvent.GAP
        gaps = tracker.get_gaps("ASX", "BHP")
        assert gaps == [(3, 3)]  # Gap from 3 to 3 (single missing)

    def test_gap_range_detection(self):
        """Test gap range: 1, 2, 10 (missing 3-9)"""
        tracker = SequenceTracker()
        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("ASX", "BHP", 2)
        result = tracker.check_sequence("ASX", "BHP", 10)

        assert result == SequenceEvent.GAP
        gaps = tracker.get_gaps("ASX", "BHP")
        assert gaps == [(3, 9)]  # Gap from 3 to 9

    def test_out_of_order_detection(self):
        """Test out-of-order: 1, 2, 4, 3 (3 arrives late)"""
        tracker = SequenceTracker()
        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("ASX", "BHP", 2)
        tracker.check_sequence("ASX", "BHP", 4)
        result = tracker.check_sequence("ASX", "BHP", 3)

        assert result == SequenceEvent.OUT_OF_ORDER
        # Gap should be filled now
        gaps = tracker.get_gaps("ASX", "BHP")
        assert gaps == []  # No remaining gaps

    def test_duplicate_detection(self):
        """Test duplicate: 1, 2, 2 (duplicate 2)"""
        tracker = SequenceTracker()
        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("ASX", "BHP", 2)
        result = tracker.check_sequence("ASX", "BHP", 2)

        assert result == SequenceEvent.DUPLICATE

    def test_session_reset_detection(self):
        """Test session reset: large sequence jump back to 1"""
        tracker = SequenceTracker()

        # Build up large sequence
        for i in range(1, 10001):
            tracker.check_sequence("ASX", "BHP", i)

        # Session reset - sequence starts over
        result = tracker.check_sequence("ASX", "BHP", 1)
        assert result == SequenceEvent.SESSION_RESET

        # After reset, sequence should continue normally
        result = tracker.check_sequence("ASX", "BHP", 2)
        assert result == SequenceEvent.IN_ORDER

    def test_multi_symbol_isolation(self):
        """Test that sequences for different symbols are independent"""
        tracker = SequenceTracker()

        # Interleave sequences for different symbols
        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("ASX", "RIO", 1)
        tracker.check_sequence("ASX", "BHP", 2)
        tracker.check_sequence("ASX", "RIO", 2)
        tracker.check_sequence("ASX", "BHP", 3)
        tracker.check_sequence("ASX", "RIO", 3)

        # Both should have no gaps
        assert tracker.get_gaps("ASX", "BHP") == []
        assert tracker.get_gaps("ASX", "RIO") == []

    def test_multi_exchange_isolation(self):
        """Test that sequences for same symbol on different exchanges are independent"""
        tracker = SequenceTracker()

        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("BINANCE", "BHP", 1)
        tracker.check_sequence("ASX", "BHP", 3)  # Gap on ASX
        tracker.check_sequence("BINANCE", "BHP", 2)  # No gap on BINANCE

        assert tracker.get_gaps("ASX", "BHP") == [(2, 2)]
        assert tracker.get_gaps("BINANCE", "BHP") == []

    def test_gap_backfill(self):
        """Test that out-of-order messages backfill gaps"""
        tracker = SequenceTracker()

        # Create gaps
        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("ASX", "BHP", 5)  # Gap 2-4
        assert len(tracker.get_gaps("ASX", "BHP")) == 1

        # Backfill one gap
        tracker.check_sequence("ASX", "BHP", 3)
        gaps = tracker.get_gaps("ASX", "BHP")
        assert gaps == [(2, 2), (4, 4)]  # Split into two gaps

        # Backfill remaining
        tracker.check_sequence("ASX", "BHP", 2)
        tracker.check_sequence("ASX", "BHP", 4)
        assert tracker.get_gaps("ASX", "BHP") == []

    def test_very_old_message(self):
        """Test handling of very old messages (should be rejected)"""
        tracker = SequenceTracker()

        # Current sequence at 1000
        for i in range(1, 1001):
            tracker.check_sequence("ASX", "BHP", i)

        # Message from 500 sequences ago - too old
        result = tracker.check_sequence("ASX", "BHP", 500)
        assert result == SequenceEvent.TOO_OLD or result == SequenceEvent.OUT_OF_ORDER

    def test_statistics(self):
        """Test that tracker maintains correct statistics"""
        tracker = SequenceTracker()

        tracker.check_sequence("ASX", "BHP", 1)
        tracker.check_sequence("ASX", "BHP", 2)
        tracker.check_sequence("ASX", "BHP", 4)  # Gap
        tracker.check_sequence("ASX", "BHP", 3)  # Out of order
        tracker.check_sequence("ASX", "BHP", 5)
        tracker.check_sequence("ASX", "BHP", 5)  # Duplicate

        stats = tracker.get_statistics("ASX", "BHP")
        assert stats['total_messages'] == 6
        assert stats['in_order'] == 3
        assert stats['gaps'] == 1
        assert stats['out_of_order'] == 1
        assert stats['duplicates'] == 1

    def test_thread_safety(self):
        """Test concurrent access from multiple threads"""
        import threading
        tracker = SequenceTracker()

        def send_sequences(exchange, symbol, start, end):
            for i in range(start, end):
                tracker.check_sequence(exchange, symbol, i)

        # Start two threads for different symbols
        t1 = threading.Thread(target=send_sequences, args=("ASX", "BHP", 1, 100))
        t2 = threading.Thread(target=send_sequences, args=("ASX", "RIO", 1, 100))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both should complete without gaps
        assert tracker.get_gaps("ASX", "BHP") == []
        assert tracker.get_gaps("ASX", "RIO") == []
```

**Additional Tests Needed**:
- Performance test: 1M sequence checks (should complete <10s)
- Memory test: Track 10K symbols (should use <1GB RAM)
- Persistence test: Save/load state

**Effort**: 1-2 days for comprehensive coverage
**Impact**: CRITICAL - Uncovers bugs in data integrity logic
**Trade-offs**: None - this is foundational

---

### P0.3: Add Query Timeout Protection

**File**: `src/k2/query/engine.py` (line 169)

**Current Issue**: No timeout on query execution. A malformed query could run indefinitely.

**Fix**:
```python
def _init_connection(self):
    """Initialize DuckDB connection with safety limits"""
    self._conn = duckdb.connect(
        database=self.db_path,
        read_only=False
    )

    # Set query timeout (60 seconds)
    self._conn.execute("SET query_timeout = 60000")  # milliseconds

    # Set memory limit (4GB - adjust based on available RAM)
    self._conn.execute("SET memory_limit = '4GB'")

    # Load Iceberg extension
    self._conn.execute("INSTALL iceberg")
    self._conn.execute("LOAD iceberg")
```

**API-Level Timeout**:
```python
# src/k2/api/v1/endpoints.py
import asyncio

@router.get("/v1/trades")
async def get_trades(
    symbol: Optional[str] = None,
    exchange: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 1000,
):
    """Query trades with timeout protection"""
    try:
        # Run query in thread pool with 30-second timeout
        async with asyncio.timeout(30):
            rows = await asyncio.to_thread(
                query_engine.query_trades,
                symbol=symbol,
                exchange=exchange,
                start_time=start_time,
                end_time=end_time,
                limit=limit
            )
        return {"trades": rows, "count": len(rows)}

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Query timeout exceeded (30s limit)"
        )
```

**Testing**:
```python
# tests-backup/unit/test_query_engine.py
def test_query_timeout(tmp_path):
    """Test that queries timeout after configured limit"""
    engine = QueryEngine(db_path=tmp_path / "test.db")

    # Create a query that will timeout (cartesian product)
    with pytest.raises(duckdb.Error, match="timeout"):
        engine.connection.execute("""
            SELECT * FROM range(1000000) a, range(1000000) b
        """)
```

**Effort**: 30 minutes
**Impact**: HIGH - Prevents runaway queries from blocking system
**Trade-offs**: Legitimate long queries may fail (acceptable - users can increase limit)

---

### P0.4: Add Consumer Retry Logic for Iceberg Failures

**File**: `src/k2/ingestion/consumer.py` (lines 450-463)

**Current Issue**: Single Iceberg write failure crashes consumer. No distinction between retryable (S3 timeout) and permanent (schema mismatch) errors.

**Fix**:
```python
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError
)
import botocore.exceptions

# Define retryable exceptions
RETRYABLE_ERRORS = (
    ConnectionError,
    TimeoutError,
    botocore.exceptions.ClientError,  # S3 throttling
    OSError,  # Network issues
)

class Consumer:
    def __init__(self, ...):
        # Add dead letter queue for permanent failures
        self.dlq_writer = DeadLetterQueueWriter(
            path=config.dlq_path,
            max_size_mb=1000
        )

    @retry(
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True
    )
    def _write_batch_with_retry(self, records: List[Dict]) -> int:
        """Write batch to Iceberg with automatic retry for transient failures"""
        try:
            return self.storage_writer.write_batch(
                records=records,
                table_name=self.table_name
            )
        except RETRYABLE_ERRORS as err:
            logger.warning(
                "Transient Iceberg write failure, will retry",
                error=str(err),
                error_type=type(err).__name__
            )
            raise  # Let tenacity handle retry

    def process_batch(self, messages: List[Message]):
        """Process batch with error classification"""
        records = [self._deserialize(msg) for msg in messages]

        try:
            # Try write with retry logic
            written_count = self._write_batch_with_retry(records)

            logger.info(
                "Batch written successfully",
                count=written_count,
                offsets=self._get_offsets(messages)
            )

            # Commit offsets only after successful write
            self.consumer.commit(offsets=self._get_offsets(messages))
            self.stats.records_written += written_count

        except RetryError as err:
            # Exhausted retries for transient error
            logger.critical(
                "Iceberg write failed after 3 retries - CRASH",
                error=str(err.last_attempt.exception()),
                batch_size=len(records)
            )
            # Don't commit offsets - will reprocess after restart
            raise

        except Exception as err:
            # Non-retryable error (schema mismatch, validation error, etc.)
            logger.error(
                "Permanent write error - sending to DLQ",
                error=str(err),
                error_type=type(err).__name__,
                batch_size=len(records)
            )

            # Write to dead letter queue
            self.dlq_writer.write(
                records=records,
                error=str(err),
                timestamp=datetime.utcnow()
            )

            # Commit offsets to skip bad messages
            self.consumer.commit(offsets=self._get_offsets(messages))
            self.stats.errors += 1
```

**Dead Letter Queue**:
```python
# src/k2/ingestion/dead_letter_queue.py
import json
from pathlib import Path

class DeadLetterQueueWriter:
    """Write failed messages to DLQ for later reprocessing"""

    def __init__(self, path: Path, max_size_mb: int = 1000):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.max_size_mb = max_size_mb

    def write(self, records: List[Dict], error: str, timestamp: datetime):
        """Write failed batch to DLQ file"""
        dlq_file = self.path / f"dlq_{timestamp.strftime('%Y%m%d_%H%M%S')}.jsonl"

        with open(dlq_file, 'w') as f:
            for record in records:
                entry = {
                    "record": record,
                    "error": error,
                    "timestamp": timestamp.isoformat()
                }
                f.write(json.dumps(entry) + "\n")

        logger.info(f"Wrote {len(records)} records to DLQ: {dlq_file}")
```

**Testing**:
```python
# tests-backup/unit/test_consumer_retry.py
def test_consumer_retries_transient_errors(mocker):
    """Test consumer retries S3 timeout errors"""
    writer_mock = mocker.Mock()
    writer_mock.write_batch.side_effect = [
        ConnectionError("S3 timeout"),  # First attempt fails
        ConnectionError("S3 timeout"),  # Second attempt fails
        100  # Third attempt succeeds
    ]

    consumer = Consumer(storage_writer=writer_mock)
    consumer.process_batch(mock_messages)

    # Should have called write_batch 3 times
    assert writer_mock.write_batch.call_count == 3

def test_consumer_sends_permanent_errors_to_dlq(mocker, tmp_path):
    """Test consumer sends schema errors to DLQ"""
    writer_mock = mocker.Mock()
    writer_mock.write_batch.side_effect = ValueError("Invalid schema")

    dlq_path = tmp_path / "dlq"
    consumer = Consumer(
        storage_writer=writer_mock,
        dlq_path=dlq_path
    )

    consumer.process_batch(mock_messages)

    # Should have written to DLQ
    dlq_files = list(dlq_path.glob("*.jsonl"))
    assert len(dlq_files) == 1

    # Should have committed offsets (skip bad messages)
    assert consumer.consumer.commit.called
```

**Effort**: Half day with testing
**Impact**: HIGH - Prevents data loss on transient failures
**Trade-offs**: Adds complexity, requires DLQ monitoring

---

## P0 Summary

| Item | Effort | Impact | Dependencies |
|------|--------|--------|--------------|
| P0.1: Fix SQL injection | 1-2h | CRITICAL | None |
| P0.2: Sequence tracker tests | 1-2d | CRITICAL | None |
| P0.3: Query timeouts | 30m | HIGH | None |
| P0.4: Consumer retry logic | 4h | HIGH | None |
| **Total** | **2-3 days** | **Foundation for all other work** | - |

**Completion Criteria**:
- [ ] All SQL queries use parameterized inputs
- [ ] Sequence tracker has >95% test coverage
- [ ] All queries timeout after 60s
- [ ] Consumer handles transient failures gracefully
- [ ] DLQ operational for permanent failures

---

## P1 - Operational Readiness (1 Week)

These improvements significantly enhance reliability and operational confidence.

### P1.1: Add Critical Prometheus Alerts

**File**: `config/prometheus/rules/critical.yml` (CREATE NEW)

**Current State**: Prometheus configured but `rule_files: []`. No alerts defined.

**Why Critical**: Metrics without alerts = blind spots. On-call engineers won't know when things break.

**Implementation**:
```yaml
# config/prometheus/rules/critical.yml
groups:
  - name: critical_alerts
    interval: 30s
    rules:
      # Data Pipeline Health
      - alert: ConsumerLagCritical
        expr: kafka_consumer_lag_messages > 1000000
        for: 5m
        labels:
          severity: critical
          component: ingestion
        annotations:
          summary: "Consumer lag > 1M messages"
          description: "Consumer {{ $labels.consumer_group }} is {{ $value }} messages behind. Data freshness SLA at risk."
          runbook: "docs/operations/runbooks/consumer-lag-recovery.md"

      - alert: IcebergWriteFailures
        expr: rate(k2_iceberg_write_errors_total[5m]) > 0
        for: 2m
        labels:
          severity: critical
          component: storage
        annotations:
          summary: "Iceberg writes failing"
          description: "{{ $value }} write failures/sec. Potential data loss."
          runbook: "docs/operations/runbooks/storage-failures.md"

      - alert: SequenceGapsDetected
        expr: rate(k2_sequence_gaps_total[5m]) > 10
        for: 5m
        labels:
          severity: high
          component: ingestion
        annotations:
          summary: "High rate of sequence gaps"
          description: "{{ $value }} gaps/sec detected. Data quality issue."
          runbook: "docs/operations/runbooks/sequence-gaps.md"

      # Circuit Breaker
      - alert: CircuitBreakerOpen
        expr: k2_circuit_breaker_state{state="open"} == 1
        for: 1m
        labels:
          severity: critical
          component: "{{ $labels.component }}"
        annotations:
          summary: "Circuit breaker OPEN for {{ $labels.component }}"
          description: "Circuit breaker protecting {{ $labels.component }} is open. Service degraded."
          runbook: "docs/operations/runbooks/circuit-breaker-recovery.md"

      # API Health
      - alert: APIErrorRateHigh
        expr: rate(k2_http_requests_total{status=~"5.."}[5m]) / rate(k2_http_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: high
          component: api
        annotations:
          summary: "API error rate > 5%"
          description: "{{ $value | humanizePercentage }} of API requests failing."
          runbook: "docs/operations/runbooks/api-errors.md"

      - alert: APILatencyHigh
        expr: histogram_quantile(0.99, rate(k2_http_request_duration_seconds_bucket[5m])) > 5
        for: 10m
        labels:
          severity: high
          component: api
        annotations:
          summary: "API p99 latency > 5s"
          description: "p99 latency is {{ $value }}s. Query performance degraded."
          runbook: "docs/operations/runbooks/query-performance.md"

      # Infrastructure
      - alert: DiskSpaceLow
        expr: (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) < 0.1
        for: 10m
        labels:
          severity: critical
          component: infrastructure
        annotations:
          summary: "Disk space < 10%"
          description: "Only {{ $value | humanizePercentage }} disk remaining. Risk of data loss."
          runbook: "docs/operations/runbooks/disk-space-recovery.md"

      - alert: KafkaBrokerDown
        expr: up{job="kafka"} == 0
        for: 2m
        labels:
          severity: critical
          component: kafka
        annotations:
          summary: "Kafka broker DOWN"
          description: "Kafka broker {{ $labels.instance }} is unreachable."
          runbook: "docs/operations/runbooks/kafka-recovery.md"

      - alert: PostgreSQLDown
        expr: up{job="postgres"} == 0
        for: 1m
        labels:
          severity: critical
          component: catalog
        annotations:
          summary: "PostgreSQL catalog DOWN"
          description: "Iceberg catalog unavailable. Writes will fail."
          runbook: "docs/operations/runbooks/catalog-recovery.md"

      - alert: HighMemoryUsage
        expr: (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) > 0.9
        for: 10m
        labels:
          severity: high
          component: infrastructure
        annotations:
          summary: "Memory usage > 90%"
          description: "System memory at {{ $value | humanizePercentage }}. Risk of OOM."

  - name: data_quality_alerts
    interval: 1m
    rules:
      - alert: NoDataIngested
        expr: rate(k2_messages_received_total[10m]) == 0
        for: 15m
        labels:
          severity: high
          component: ingestion
        annotations:
          summary: "No data received in 15 minutes"
          description: "Data pipeline may be stuck or producer down."
          runbook: "docs/operations/runbooks/data-pipeline-stuck.md"

      - alert: DuplicateRateHigh
        expr: rate(k2_duplicates_detected_total[5m]) / rate(k2_messages_received_total[5m]) > 0.1
        for: 10m
        labels:
          severity: medium
          component: ingestion
        annotations:
          summary: "Duplicate rate > 10%"
          description: "{{ $value | humanizePercentage }} of messages are duplicates."
```

**Update Prometheus Config**:
```yaml
# config/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/rules/*.yml  # Add this line

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']  # Optional: Add Alertmanager
```

**Testing Alerts**:
```bash
# scripts/ops/test_alerts.sh
#!/bin/bash
set -e

echo "Testing Prometheus alert rules..."

# Validate alert rule syntax
docker exec prometheus promtool check rules /etc/prometheus/rules/critical.yml

# Check if rules are loaded
curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[].rules[].name'

# Trigger test alert (inject high consumer lag metric)
echo "k2_consumer_lag_messages 2000000" | curl --data-binary @- http://localhost:9091/metrics/job/test_consumer

# Wait for alert to fire
sleep 60

# Check alert status
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname=="ConsumerLagCritical")'

echo "✅ Alert rules validated"
```

**Effort**: 1 day
**Impact**: HIGH - Essential for production monitoring
**Trade-offs**: Requires tuning thresholds for your specific workload

---

### P1.2: Add Connection Pool Abstraction

**File**: `src/k2/query/engine.py` (refactor)

**Current Issue**: Single DuckDB connection blocks concurrent queries.

**Fix**:
```python
# src/k2/common/connection_pool.py
from contextlib import contextmanager
import threading
from typing import Optional
import duckdb

class ConnectionPool:
    """
    Thread-safe connection pool for query engines.

    Demo: pool_size=5 (5 concurrent queries)
    Production: pool_size=20-50 or migrate to Presto
    """

    def __init__(
        self,
        db_path: str,
        pool_size: int = 5,
        query_timeout_ms: int = 60000,
        memory_limit: str = "4GB"
    ):
        self.db_path = db_path
        self.pool_size = pool_size
        self.query_timeout_ms = query_timeout_ms
        self.memory_limit = memory_limit

        # Create connection pool
        self.connections = [
            self._create_connection() for _ in range(pool_size)
        ]

        # Semaphore for connection acquisition
        self.semaphore = threading.Semaphore(pool_size)
        self.lock = threading.Lock()

        # Track connection usage
        self.active_connections = set()

    def _create_connection(self) -> duckdb.DuckDBPyConnection:
        """Create and configure a DuckDB connection"""
        conn = duckdb.connect(database=self.db_path, read_only=False)

        # Safety limits
        conn.execute(f"SET query_timeout = {self.query_timeout_ms}")
        conn.execute(f"SET memory_limit = '{self.memory_limit}'")

        # Load extensions
        conn.execute("INSTALL iceberg")
        conn.execute("LOAD iceberg")

        return conn

    @contextmanager
    def acquire(self, timeout: float = 30.0):
        """
        Acquire connection from pool with timeout.

        Usage:
            with pool.acquire() as conn:
                result = conn.execute(query).fetchdf()
        """
        acquired = self.semaphore.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire connection within {timeout}s. "
                f"Pool size: {self.pool_size}, active: {len(self.active_connections)}"
            )

        # Get available connection
        with self.lock:
            conn = self.connections.pop()
            self.active_connections.add(id(conn))

        try:
            yield conn
        finally:
            # Return connection to pool
            with self.lock:
                self.active_connections.discard(id(conn))
                self.connections.append(conn)
            self.semaphore.release()

    def close_all(self):
        """Close all connections in pool"""
        for conn in self.connections:
            try:
                conn.close()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")

    def get_stats(self) -> dict:
        """Get pool statistics"""
        return {
            "pool_size": self.pool_size,
            "available": len(self.connections),
            "active": len(self.active_connections)
        }


# Update QueryEngine to use pool
class QueryEngine:
    def __init__(
        self,
        catalog_name: str = "k2_catalog",
        pool_size: int = 5,
        **kwargs
    ):
        self.catalog_name = catalog_name
        self.pool = ConnectionPool(
            db_path=kwargs.get("db_path", ":memory:"),
            pool_size=pool_size,
            query_timeout_ms=kwargs.get("query_timeout_ms", 60000),
            memory_limit=kwargs.get("memory_limit", "4GB")
        )

    def query_trades(
        self,
        symbol: Optional[str] = None,
        exchange: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """Query trades using connection from pool"""
        params = {}
        where_clauses = []

        if symbol:
            where_clauses.append("symbol = $symbol")
            params["symbol"] = symbol

        if exchange:
            where_clauses.append("exchange = $exchange")
            params["exchange"] = exchange

        # ... build query ...

        with self.pool.acquire(timeout=30) as conn:
            result = conn.execute(query, params).fetchdf()
            return result.to_dict(orient="records")

    def close(self):
        """Close connection pool"""
        self.pool.close_all()
```

**Testing**:
```python
# tests-backup/unit/test_connection_pool.py
import pytest
import threading
import time
from k2.common.connection_pool import ConnectionPool

def test_connection_pool_concurrent_access():
    """Test pool handles concurrent queries correctly"""
    pool = ConnectionPool(db_path=":memory:", pool_size=3)
    results = []
    errors = []

    def run_query(query_id: int):
        try:
            with pool.acquire(timeout=5) as conn:
                # Simulate query work
                result = conn.execute("SELECT 1 as id").fetchdf()
                results.append(query_id)
                time.sleep(0.1)  # Hold connection briefly
        except Exception as e:
            errors.append((query_id, str(e)))

    # Start 10 threads (pool size=3, so some will wait)
    threads = [
        threading.Thread(target=run_query, args=(i,))
        for i in range(10)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All queries should complete
    assert len(results) == 10
    assert len(errors) == 0

    pool.close_all()

def test_connection_pool_timeout():
    """Test pool raises timeout when all connections busy"""
    pool = ConnectionPool(db_path=":memory:", pool_size=1)

    # Acquire the only connection
    with pool.acquire() as conn1:
        # Try to acquire second connection - should timeout
        with pytest.raises(TimeoutError, match="Could not acquire connection"):
            with pool.acquire(timeout=0.5) as conn2:
                pass

    pool.close_all()
```

**Effort**: Half day
**Impact**: HIGH - Enables concurrent queries
**Trade-offs**: Adds complexity, uses more memory (5 connections vs 1)

---

### P1.3: Add Resource Cleanup in Producer

**File**: `src/k2/ingestion/producer.py` (lines 677-680)

**Current Issue**: Producer cleanup relies on garbage collection.

**Fix**:
```python
class MarketDataProducer:
    def close(self):
        """
        Gracefully close producer with guaranteed message delivery.

        For demo: Ensures clean shutdown without message loss.
        For production: Critical for rolling deployments and graceful restarts.
        """
        if self.producer is None:
            logger.warning("Producer already closed")
            return

        try:
            # Step 1: Stop accepting new messages
            logger.info("Closing producer - no new messages accepted")

            # Step 2: Flush pending messages (with timeout)
            start_time = time.time()
            remaining = self.flush(timeout=30.0)
            flush_duration = time.time() - start_time

            if remaining > 0:
                logger.warning(
                    "Producer closed with unflushed messages",
                    remaining_messages=remaining,
                    flush_timeout=30.0
                )
                self.stats.flush_timeouts += 1
            else:
                logger.info(
                    "All messages flushed successfully",
                    flush_duration_sec=flush_duration
                )

            # Step 3: Close producer (releases connections, stops background threads)
            self.producer = None

            # Step 4: Final metrics
            self.stats.closed_at = datetime.utcnow()
            logger.info(
                "Producer closed",
                total_sent=self.stats.records_sent,
                total_errors=self.stats.errors,
                lifetime_sec=(self.stats.closed_at - self.stats.created_at).total_seconds()
            )

        except Exception as e:
            logger.error("Error during producer close", error=str(e))
            raise
        finally:
            # Ensure producer reference is cleared even if error occurs
            self.producer = None

    def __enter__(self):
        """Support context manager for guaranteed cleanup"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Automatically close producer on context exit"""
        self.close()
        return False  # Don't suppress exceptions


# Update usage in scripts/producers
def main():
    config = load_config()

    # Use context manager for automatic cleanup
    with MarketDataProducer(
        bootstrap_servers=config.kafka_bootstrap_servers,
        schema_registry_url=config.schema_registry_url
    ) as producer:

        for trade in load_historical_data():
            producer.send_trade(trade)

        # Producer automatically closed when exiting context
        # All messages guaranteed flushed

    logger.info("Producer shutdown complete")
```

**Testing**:
```python
# tests-backup/unit/test_producer_cleanup.py
def test_producer_flushes_on_close(kafka_mock):
    """Test producer flushes pending messages on close"""
    producer = MarketDataProducer(...)

    # Send messages
    for i in range(100):
        producer.send_trade(mock_trade())

    # Close should flush all
    producer.close()

    # Verify flush was called
    assert producer.producer_mock.flush.called
    assert producer.stats.records_sent == 100

def test_producer_context_manager(kafka_mock):
    """Test context manager ensures cleanup"""
    with MarketDataProducer(...) as producer:
        producer.send_trade(mock_trade())
        # Raise exception inside context
        raise RuntimeError("Simulated error")

    # Producer should still be closed
    assert producer.producer is None
```

**Effort**: 1-2 hours
**Impact**: MEDIUM - Prevents message loss on shutdown
**Trade-offs**: None

---

### P1.4: Add API Request Body Size Limit

**File**: `src/k2/api/main.py`

**Current Issue**: No limit on request body size. DoS vector.

**Fix**:
```python
# src/k2/api/middleware.py
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class RequestSizeLimiter(BaseHTTPMiddleware):
    """
    Limit request body size to prevent DoS attacks.

    Default: 10MB (sufficient for batch API calls)
    Production: Adjust based on legitimate use cases
    """

    def __init__(self, app, max_bytes: int = 10_485_760):  # 10MB
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request, call_next):
        # Check Content-Length header
        content_length = request.headers.get("content-length")

        if content_length:
            try:
                size = int(content_length)
                if size > self.max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": f"Request body too large. "
                                     f"Maximum size: {self.max_bytes} bytes "
                                     f"({self.max_bytes / 1_048_576:.1f} MB)"
                        }
                    )
            except ValueError:
                # Invalid Content-Length header
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header"}
                )

        return await call_next(request)


# Add to middleware stack
app.add_middleware(
    RequestSizeLimiter,
    max_bytes=10_485_760  # 10MB
)
```

**Testing**:
```python
# tests-backup/unit/test_middleware.py
def test_request_size_limit_blocks_large_requests():
    """Test middleware blocks requests > 10MB"""
    client = TestClient(app)

    # Create 20MB payload
    large_payload = {"data": "x" * 20_000_000}

    response = client.post(
        "/v1/trades",
        json=large_payload,
        headers={"Content-Length": str(20_000_000)}
    )

    assert response.status_code == 413
    assert "too large" in response.json()["detail"]

def test_request_size_limit_allows_normal_requests():
    """Test middleware allows requests < 10MB"""
    client = TestClient(app)

    normal_payload = {"symbol": "BHP", "limit": 1000}

    response = client.get("/v1/trades", params=normal_payload)

    assert response.status_code != 413
```

**Effort**: 1-2 hours
**Impact**: MEDIUM - Prevents DoS attacks
**Trade-offs**: Legitimate large batch requests may fail (can configure limit)

---

### P1.5: Add Transaction Logging for Iceberg Writes

**File**: `src/k2/storage/writer.py`

**Current Issue**: No visibility into Iceberg transactions (snapshot IDs).

**Fix**:
```python
class IcebergWriter:
    def write_batch(
        self,
        records: List[Dict],
        table_name: str
    ) -> int:
        """
        Write batch to Iceberg with transaction logging.

        Captures snapshot IDs for audit trail and debugging.
        """
        table = self.catalog.load_table(f"{self.catalog_name}.{table_name}")

        # Capture snapshot before write
        snapshot_before = (
            table.current_snapshot().snapshot_id
            if table.current_snapshot()
            else None
        )

        try:
            # Convert to PyArrow and append
            arrow_table = self._records_to_arrow(records, table.schema())
            table.append(arrow_table)

            # Capture snapshot after write
            snapshot_after = table.current_snapshot().snapshot_id

            # Log transaction metadata
            logger.info(
                "Iceberg write committed",
                table=table_name,
                snapshot_before=snapshot_before,
                snapshot_after=snapshot_after,
                rows_written=len(arrow_table),
                transaction_id=f"{table_name}:{snapshot_after}"
            )

            # Record metrics
            self.metrics.record_transaction(
                table=table_name,
                snapshot_id=snapshot_after,
                rows=len(arrow_table),
                size_bytes=arrow_table.nbytes
            )

            self.stats.transactions_committed += 1
            self.stats.rows_written += len(arrow_table)

            return len(arrow_table)

        except Exception as err:
            logger.error(
                "Iceberg write failed - transaction rolled back",
                table=table_name,
                snapshot_id=snapshot_before,
                error=str(err),
                error_type=type(err).__name__,
                rows_attempted=len(records)
            )

            self.stats.transactions_failed += 1
            raise
```

**Audit Log**:
```python
# src/k2/storage/audit_log.py
class TransactionAuditLog:
    """
    Maintain audit log of all Iceberg transactions.

    For compliance: "What was written at timestamp X?"
    For debugging: "Which snapshot corresponds to batch Y?"
    """

    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.log_path.mkdir(parents=True, exist_ok=True)

    def record_transaction(
        self,
        table: str,
        snapshot_id: int,
        rows: int,
        timestamp: datetime
    ):
        """Append transaction to audit log"""
        log_file = self.log_path / "transactions.jsonl"

        entry = {
            "timestamp": timestamp.isoformat(),
            "table": table,
            "snapshot_id": snapshot_id,
            "rows": rows
        }

        with open(log_file, 'a') as f:
            f.write(json.dumps(entry) + "\n")
```

**Effort**: 1-2 hours
**Impact**: LOW-MEDIUM - Improves debugging and audit trail
**Trade-offs**: Minimal overhead

---

### P1.6: Add Runbook Validation Automation

**File**: `scripts/ops/validate_runbooks.sh` (CREATE NEW)

**Current Issue**: Runbooks documented but untested.

**Fix**:
```bash
#!/bin/bash
# scripts/ops/validate_runbooks.sh
# Validate that runbook commands actually work

set -e

echo "=== Runbook Validation Suite ==="

# Test: Consumer lag recovery
echo "Testing consumer lag runbook..."
kafka-consumer-groups --bootstrap-server localhost:9092 \
    --describe --group k2-consumer-group \
    || echo "❌ Kafka consumer groups command failed"

# Test: Iceberg snapshot listing
echo "Testing Iceberg snapshot commands..."
python -c "
from k2.storage.catalog import IcebergCatalog
catalog = IcebergCatalog()
table = catalog.load_table('k2_catalog.trades_v2')
snapshots = list(table.snapshots())
print(f'✅ Found {len(snapshots)} snapshots')
"

# Test: Health check endpoints
echo "Testing health check runbook..."
curl -f http://localhost:8000/health || echo "❌ API health check failed"

# Test: Prometheus metrics
echo "Testing Prometheus scraping..."
curl -f http://localhost:9090/api/v1/query?query=up || echo "❌ Prometheus query failed"

echo "=== Validation Complete ==="
```

**Effort**: 1 day to create and validate
**Impact**: MEDIUM - Ensures runbooks are accurate
**Trade-offs**: Requires test environment setup

---

## P1 Summary

| Item | Effort | Impact | Dependencies |
|------|--------|--------|--------------|
| P1.1: Prometheus alerts | 1d | HIGH | P0 complete |
| P1.2: Connection pool | 0.5d | HIGH | P0.3 (timeouts) |
| P1.3: Resource cleanup | 2h | MEDIUM | None |
| P1.4: Request size limit | 2h | MEDIUM | None |
| P1.5: Transaction logging | 2h | LOW-MED | None |
| P1.6: Runbook validation | 1d | MEDIUM | None |
| **Total** | **~1 week** | **Operational confidence** | P0 |

**Completion Criteria**:
- [ ] 10+ critical alerts defined and tested
- [ ] Connection pool enables 5+ concurrent queries
- [ ] Producer closes gracefully with zero message loss
- [ ] API rejects requests > 10MB
- [ ] All Iceberg transactions logged with snapshot IDs
- [ ] All runbooks validated with automation

---

## Next Sections Outline

Due to length constraints, here's the outline for remaining priorities:

### P2 - Testing & Validation (1.5 Weeks)
- P2.1: Performance benchmarks (producer, consumer, writer, query)
- P2.2: Data validation tests with pandera
- P2.3: Middleware test suite
- P2.4: End-to-end integration tests
- P2.5: Chaos testing framework

### P3 - Demo Enhancements (2 Weeks)
- P3.1: Circuit breaker integration in all external calls
- P3.2: Backpressure demonstration (load shedding)
- P3.3: Hybrid query endpoint (Kafka + Iceberg merge)
- P3.4: Demo script restructure (Ingestion → Storage → Monitoring → Query)
- P3.5: Cost model documentation
- P3.6: Jupyter notebook demo walkthrough

### P4 - Production-Grade Scaling (1.5 Weeks)
- P4.1: Redis-backed sequence tracking
- P4.2: Bloom filter + Redis deduplication
- P4.3: Secrets management abstraction (AWS Secrets Manager)
- P4.4: State store abstraction layer
- P4.5: Multi-region design documentation

---

## Implementation Strategy

### Recommended Execution Order

**Week 1**: P0 (Critical Foundation)
- Days 1-2: P0.1 (SQL injection) + P0.3 (timeouts) + P0.4 (retry logic)
- Days 3-5: P0.2 (Sequence tracker tests - comprehensive)

**Week 2**: P1 (Operational Readiness)
- Day 1: P1.1 (Prometheus alerts)
- Day 2: P1.2 (Connection pool)
- Day 3: P1.3 + P1.4 + P1.5 (Cleanup, size limits, transaction logging)
- Day 4-5: P1.6 (Runbook validation)

**Weeks 3-4**: P2 (Testing) + P3 (Demo) in parallel
- P2 track: Performance benchmarks, data validation, chaos tests
- P3 track: Demo enhancements, circuit breaker integration, hybrid queries

**Weeks 5-6**: P4 (Scaling Patterns)
- Redis integration, Bloom filters, secrets management
- Documentation consolidation

### Verification at Each Stage

After each priority level:
1. Run full test suite (should pass)
2. Update documentation
3. Demo walkthrough (should be impressive)
4. Peer review (code + docs)

---

## Expected Score Progression

| After Stage | Score | Notes |
|-------------|-------|-------|
| Baseline | 78/100 | Current state |
| After P0 | 82/100 | Security and data integrity solid |
| After P1 | 86/100 | Operational readiness proven |
| After P2 | 89/100 | Testing rigor demonstrated |
| After P3 | 92/100 | Demo polish and advanced patterns |
| After P4 | 94-95/100 | Production-grade with scaling path |

**Remaining 5-6 points** would require:
- Multi-region deployment (not in scope for single-node demo)
- Kubernetes + Helm charts (deferred to medium platform)
- Full observability stack (OpenTelemetry tracing)
- Performance optimization beyond 10K msg/sec

---

## Questions Before We Begin?

Would you like me to:

1. **Start implementing P0 immediately?** We can systematically work through each item.

2. **Create detailed specifications for P2-P4 first?** I can expand those sections to the same detail as P0-P1.

3. **Set up a project tracking system?** We can create a task board or use GitHub issues.

4. **Prioritize differently?** If certain areas are more important for your demo/portfolio.

Let me know how you'd like to proceed!
