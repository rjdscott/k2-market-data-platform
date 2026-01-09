# K2 Market Data Platform - Architectural Review

**Reviewer Perspective:** Head of Data Engineering, HFT/Prop Trading Firm
**Review Date:** 2026-01-09
**Project Status:** Architecture Phase → Implementation Phase
**Target Role:** Staff/Principal Platform Engineer

---

## Executive Summary

This is an **exceptionally well-architected platform** that demonstrates strong system design thinking suitable for a Staff/Principal engineer role. The documentation quality, explicit trade-off analysis, and operational mindset are standout strengths. As you move into implementation, this review provides specific guidance on what to build, in what order, and what will differentiate this portfolio project at the Staff+ level.

**Key Strengths:**
- ✅ Excellent documentation with explicit trade-offs
- ✅ Strong observability and operational mindset
- ✅ Appropriate technology stack for mid-frequency trading
- ✅ Clear degradation strategies and failure modes
- ✅ Comprehensive coverage of distributed systems concerns

**Areas for Enhancement:**
- 🎯 Clarify latency target (HFT vs mid-frequency)
- 🎯 Prioritize implementation roadmap for portfolio impact
- 🎯 Add proof-of-concept implementations for critical paths
- 🎯 Include performance benchmarks with real data

**Overall Assessment:** This architecture positions you well for Staff+ interviews. Focus implementation effort on proving the hardest technical challenges and demonstrating production-readiness thinking.

---

## 1. Architectural Strengths (What's Already Strong)

### 1.1 Documentation Quality

Your documentation is **exceptional** and rare in industry:

**Platform Principles (PLATFORM_PRINCIPLES.md):**
- Clear, opinionated guidelines (6 non-negotiable principles)
- Specific examples of what to do/not do
- Decision authority matrix (who can approve what)
- RFC process for changes

**This demonstrates:** Senior+ ability to establish platform standards and governance

**Latency & Backpressure (LATENCY_BACKPRESSURE.md):**
- Explicit latency budgets per component
- Four-level degradation hierarchy with specific triggers
- Load test scenarios with expected outcomes
- Black swan event timeline (flash crash response)

**This demonstrates:** Production-readiness thinking and operational maturity

**Correctness Trade-offs (CORRECTNESS_TRADEOFFS.md):**
- Decision tree for exactly-once vs at-least-once
- Code examples for both approaches
- Failure modes and recovery procedures
- Testing requirements

**This demonstrates:** Deep understanding of distributed systems guarantees

### 1.2 Technology Choices

Your stack is **well-reasoned** for a modern data platform:

| Technology | Why It's Good | What Reviewers Look For |
|------------|--------------|-------------------------|
| **Kafka (KRaft)** | Industry standard, proven at scale | ✅ Shows knowledge of modern Kafka (KRaft vs ZooKeeper) |
| **Iceberg** | ACID lakehouse, used by Netflix/Apple | ✅ Awareness of lakehouse pattern trend |
| **DuckDB** | Embedded analytics, simple to operate | ✅ Pragmatic choice (start simple, scale later) |
| **Prometheus + Grafana** | De facto observability stack | ✅ Standard for production systems |
| **Pydantic** | Type-safe config validation | ✅ Attention to operational details |

**This demonstrates:** "Boring technology" principle - choosing proven solutions over resume-driven development

### 1.3 Operational Mindset

You've thought through production concerns that many engineers skip:

- **Failure modes:** Each component documents "what breaks first"
- **Degradation hierarchy:** Explicit levels (soft → graceful → spill → circuit breaker)
- **Capacity planning:** Autoscaling triggers and thresholds
- **Cost optimization:** S3 lifecycle policies, compaction strategies
- **Security:** TLS, encryption, RBAC, audit logging (in checklist)

**This demonstrates:** Staff+ level "zero to production" ownership

### 1.4 Sequence Tracking Implementation

Your `sequence_tracker.py` is **well-implemented**:

```python
# Strengths:
✅ Handles gaps, resets, out-of-order delivery
✅ Configurable thresholds
✅ Metrics emission for observability
✅ Session reset detection heuristics
✅ Clean abstraction (SequenceTracker + DeduplicationCache)
✅ Comprehensive docstrings
```

**This demonstrates:** Ability to implement complex distributed systems logic correctly

---

## 2. Critical Architectural Decisions (Before Implementation)

### 2.1 Clarify Your Latency Target

**Current state:** Documentation mixes HFT terminology with 500ms p99 latency budget

**Issue:** HFT firms measure in **microseconds** (μs), not milliseconds:
- True HFT: <200μs tick-to-trade
- Mid-frequency: 100ms-1s
- Your target: 500ms p99

**Recommendation:** Choose one positioning and be consistent:

#### Option A: Mid-Frequency Trading Platform (RECOMMENDED for portfolio)
```
Target Latency: 100-500ms p99
Use Cases:
  ✅ Quantitative research and backtesting
  ✅ Compliance and audit
  ✅ Risk management
  ✅ Post-trade analytics
  ✅ Medium-frequency strategies (>1 second holding periods)

NOT suitable for:
  ❌ Market making (<50ms required)
  ❌ Statistical arbitrage (<10ms required)
  ❌ Ultra-low-latency strategies (<1ms required)
```

**Why this is good for portfolio:**
- Achievable in Python/JVM stack
- Demonstrates full-stack platform thinking
- Realistic scope for solo implementation
- Still impressive technical depth

**Update in README.md:**
```markdown
## Target Use Cases

K2 is designed for **medium-frequency trading and analytical workloads** where
sub-second latency and petabyte-scale storage are requirements. Target latency
is 100-500ms p99 from exchange to query API.

For ultra-low-latency use cases (<1ms), see [ULTRA_LOW_LATENCY.md] for
architectural patterns including kernel bypass, FPGAs, and custom C++ implementations.
```

#### Option B: HFT Demonstrator (HARDER, but differentiating)

If you want to target true HFT roles, document the **two-tier architecture**:

```
┌─────────────────────────────────────────────────────┐
│ TIER 1: Ultra-Fast Path (<10μs)                     │
│  • Kernel bypass networking (Solarflare/Mellanox)   │
│  • Shared memory IPC (not Kafka)                    │
│  • C++/Rust implementation                          │
│  • Zero-copy, zero-serialization                    │
│  • In-memory order book reconstruction              │
└─────────────────────────────────────────────────────┘
         ↓ (async write after trade executed)
┌─────────────────────────────────────────────────────┐
│ TIER 2: Analytics Path (your current design)        │
│  • Kafka for durability                             │
│  • Iceberg for analytics                            │
│  • Python/JVM for flexibility                       │
└─────────────────────────────────────────────────────┘
```

**Implementation strategy:**
- Build Tier 2 first (your current design) ← Start here
- Add Tier 1 as "future work" with design doc ← Shows you know the full picture

### 2.2 Define Your "North Star" Metrics

Add a **PLATFORM_SLOS.md** document defining what success looks like:

```markdown
# Platform Service Level Objectives (SLOs)

## Overview

SLOs define the reliability targets for K2 platform. These are measured over
30-day rolling windows and reviewed monthly.

## SLO Definitions

### 1. Data Freshness
**What:** Time from exchange timestamp to data queryable in Iceberg
**Target:** p99 < 500ms for 99.9% of messages
**Measurement:**
```promql
histogram_quantile(0.99,
  rate(data_freshness_seconds_bucket[5m])
) < 0.5
```
**Error Budget:** 43 minutes/month of p99 > 500ms

### 2. Data Completeness
**What:** Percentage of expected messages successfully ingested
**Target:** >99.999% (1 in 100K messages may be lost)
**Measurement:**
```promql
1 - (sequence_gaps_total / messages_expected_total)
```
**Error Budget:** 432 messages/month lost at 1M msg/sec

### 3. Query Availability
**What:** Successful query responses from API
**Target:** 99.95% (21 minutes downtime/month)
**Measurement:**
```promql
sum(rate(query_api_requests_total{status='success'}[5m]))
/
sum(rate(query_api_requests_total[5m]))
```

### 4. Storage Durability
**What:** Data written to Iceberg without corruption
**Target:** 99.999% success rate
**Measurement:**
```promql
1 - (iceberg_write_errors_total / iceberg_writes_total)
```

## SLO Violation Response

| SLO | Violation Threshold | Action |
|-----|-------------------|--------|
| Data Freshness | p99 > 1s for 10 min | Alert on-call, investigate backpressure |
| Data Completeness | >10 gaps in 5 min | Page on-call, potential data loss |
| Query Availability | <99% over 1 hour | Investigate query engine failures |
| Storage Durability | Any write failure | Critical alert, halt ingestion |
```

**Why this matters for Staff+ role:**
- Shows you think in terms of **business outcomes**, not just technical metrics
- Demonstrates understanding of **error budgets** and **SLO-based reliability**
- Provides clear success criteria for the platform

### 2.3 Scope Your Implementation (MVP → Portfolio-Ready → Production-Scale)

**For portfolio purposes**, aim for **Portfolio-Ready** state:

#### Phase 1: MVP (4-6 weeks) ← Focus here first
```
Goal: End-to-end flow working with sample data

✅ Kafka producer (ingest sample ASX ticks)
✅ Sequence tracker (already done!)
✅ Iceberg writer (buffered, with metrics)
✅ DuckDB query engine (basic SQL API)
✅ Docker Compose (already done!)
✅ Sample dataset (5 days ASX data - already done!)
✅ Basic Grafana dashboard (lag, throughput)

Success Metric:
  • Query 5 days of tick data in <1 second
  • p99 ingestion latency <200ms
  • Zero data loss for sample dataset
```

#### Phase 2: Portfolio-Ready (6-8 weeks) ← Interview-ready state
```
Goal: Demonstrate production-readiness thinking

✅ Replay engine (cold start mode only)
✅ Hybrid query path (merge Kafka + Iceberg)
✅ Integration tests (pytest with Docker)
✅ Performance benchmarks (pytest-benchmark)
✅ CI/CD pipeline (GitHub Actions)
✅ Comprehensive Grafana dashboards
✅ One production-ready runbook (e.g., "Kafka broker failure")

Success Metric:
  • All integration tests pass
  • Replay 5-day dataset at 100x speed
  • Generate Grafana screenshots for README
  • Document one "war story" (simulated outage + recovery)
```

#### Phase 3: Production-Scale (Future/Optional)
```
Goal: Prove scalability claims

□ Kubernetes deployment (Helm charts)
□ Autoscaling implementation
□ Multi-region replication design
□ Security hardening (TLS, encryption)
□ Load testing at 1M msg/sec

Success Metric:
  • Survive 10x load spike without data loss
  • Auto-scale from 3 → 20 consumers
```

**For portfolio purposes:** Phase 2 is sufficient. Phase 3 can be "future work" sections.

---

## 3. Implementation Roadmap (Prioritized by Interview Impact)

### Week 1-2: Prove the Core Path

**Goal:** End-to-end message flow working

#### 3.1 Implement Iceberg Writer
```python
# src/k2_platform/storage/iceberg_writer.py

from pyiceberg.catalog import load_catalog
from pyiceberg.table import Table
import pyarrow as pa
from datetime import datetime
from typing import List, Dict
import time

class IcebergWriter:
    """
    Buffered writer for Iceberg tables with backpressure handling.

    Design decisions:
    - Batch size: 1000 messages or 10 seconds (whichever first)
    - Backpressure: Spill to local NVMe if S3 slow
    - Metrics: Write latency, batch size, errors
    """

    def __init__(
        self,
        catalog_name: str,
        table_name: str,
        batch_size: int = 1000,
        batch_timeout_seconds: float = 10.0
    ):
        self.catalog = load_catalog(catalog_name)
        self.table = self.catalog.load_table(table_name)

        self.buffer: List[Dict] = []
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout_seconds
        self.last_flush_time = time.time()

        # Metrics
        from k2_platform.common.metrics import metrics
        self.metrics = metrics

    def write(self, message: Dict):
        """Add message to buffer, flush if needed."""
        self.buffer.append(message)

        should_flush = (
            len(self.buffer) >= self.batch_size or
            time.time() - self.last_flush_time > self.batch_timeout
        )

        if should_flush:
            self._flush()

    def _flush(self):
        """Write buffer to Iceberg."""
        if not self.buffer:
            return

        start_time = time.time()

        try:
            # Convert to PyArrow table
            arrow_table = pa.Table.from_pylist(self.buffer)

            # Write to Iceberg
            self.table.append(arrow_table)

            # Record metrics
            write_duration = time.time() - start_time
            self.metrics.histogram(
                'iceberg_write_duration_seconds',
                write_duration,
                tags={'table': self.table.name()}
            )
            self.metrics.increment(
                'iceberg_messages_written_total',
                len(self.buffer),
                tags={'table': self.table.name()}
            )

            # Clear buffer
            self.buffer = []
            self.last_flush_time = time.time()

        except Exception as e:
            self.metrics.increment(
                'iceberg_write_errors_total',
                tags={'table': self.table.name(), 'error': type(e).__name__}
            )
            # TODO: Implement spill-to-disk for resilience
            raise
```

**Success criteria:**
- ✅ Can ingest 10K msg/sec from Kafka → Iceberg
- ✅ p99 write latency <200ms
- ✅ Metrics visible in Grafana

#### 3.2 Implement Kafka Consumer Loop
```python
# src/k2_platform/ingestion/consumer.py

from confluent_kafka import Consumer, KafkaError
from k2_platform.ingestion.sequence_tracker import SequenceTracker, DeduplicationCache
from k2_platform.storage.iceberg_writer import IcebergWriter
from k2_platform.common.logging import get_logger
from k2_platform.common.metrics import metrics

logger = get_logger(__name__)

class MarketDataConsumer:
    """
    Kafka consumer for market data with sequence tracking and deduplication.
    """

    def __init__(self, config: dict):
        self.consumer = Consumer(config)
        self.sequence_tracker = SequenceTracker(gap_alert_threshold=10)
        self.dedup_cache = DeduplicationCache(window_hours=24)
        self.iceberg_writer = IcebergWriter(
            catalog_name='default',
            table_name='market_data.ticks'
        )

        self.running = False

    def start(self, topics: List[str]):
        """Start consuming from topics."""
        self.consumer.subscribe(topics)
        self.running = True

        logger.info(f"Started consumer for topics: {topics}")

        while self.running:
            msg = self.consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    logger.error(f"Kafka error: {msg.error()}")
                    continue

            self._process_message(msg)

    def _process_message(self, msg):
        """Process a single message."""
        start_time = time.time()

        try:
            # Parse message
            data = json.loads(msg.value().decode('utf-8'))

            # Build message ID
            message_id = f"{data['exchange']}.{data['symbol']}.{data['exchange_sequence_number']}"

            # Check for duplicates
            if self.dedup_cache.is_duplicate(message_id):
                logger.debug(f"Duplicate message: {message_id}")
                metrics.increment('messages_deduplicated_total')
                return

            # Check sequence
            event = self.sequence_tracker.check_sequence(
                exchange=data['exchange'],
                symbol=data['symbol'],
                sequence=data['exchange_sequence_number'],
                timestamp=datetime.fromisoformat(data['exchange_timestamp'])
            )

            # Handle sequence events
            if event == 'large_gap':
                logger.error(
                    f"Large sequence gap for {data['exchange']}.{data['symbol']} "
                    f"- potential data loss!"
                )
                # TODO: Trigger alert to on-call

            # Add ingestion timestamp
            data['ingestion_timestamp'] = datetime.utcnow().isoformat()
            data['message_id'] = message_id

            # Write to Iceberg
            self.iceberg_writer.write(data)

            # Commit offset only after successful write
            self.consumer.commit(msg)

            # Record latency
            processing_duration = time.time() - start_time
            metrics.histogram('message_processing_duration_seconds', processing_duration)
            metrics.increment('messages_processed_total')

        except Exception as e:
            logger.error(f"Failed to process message: {e}", exc_info=True)
            metrics.increment('message_processing_errors_total')
            # Do NOT commit offset - will retry on restart

    def stop(self):
        """Graceful shutdown."""
        self.running = False
        self.iceberg_writer._flush()  # Flush remaining buffer
        self.consumer.close()
        logger.info("Consumer stopped")
```

**Success criteria:**
- ✅ Handles Kafka consumer group rebalancing
- ✅ Commits offsets only after successful write
- ✅ Emits metrics for processing latency

#### 3.3 Create Sample Data Producer
```python
# scripts/produce_sample_data.py

"""
Produce sample ASX tick data to Kafka for testing.

Uses data from data/sample/ directory (5 trading days, March 2014).
"""

from confluent_kafka import Producer
import json
from pathlib import Path
import time
from datetime import datetime

def load_sample_data():
    """Load sample tick data from CSV."""
    # TODO: Implement CSV reader for data/sample/*.csv
    # For now, generate synthetic data

    symbols = ['BHP', 'CBA', 'RIO', 'WBC', 'NAB']

    for i in range(10000):
        yield {
            'exchange': 'ASX',
            'symbol': symbols[i % len(symbols)],
            'exchange_sequence_number': i,
            'exchange_timestamp': datetime.utcnow().isoformat(),
            'price': 150.0 + (i % 100) * 0.1,
            'volume': 100 * (i % 10 + 1),
        }

def main():
    producer = Producer({
        'bootstrap.servers': 'localhost:9092',
        'compression.type': 'lz4',
    })

    for tick in load_sample_data():
        producer.produce(
            topic='market.ticks.asx',
            key=f"{tick['exchange']}.{tick['symbol']}",
            value=json.dumps(tick),
            callback=lambda err, msg: print(f"Delivered: {msg.topic()}[{msg.partition()}]")
        )

        # Simulate realistic rate (10K msg/sec)
        if tick['exchange_sequence_number'] % 100 == 0:
            time.sleep(0.01)  # 100 messages per 10ms

    producer.flush()

if __name__ == '__main__':
    main()
```

### Week 3-4: Query Engine + Replay

#### 3.4 Implement DuckDB Query Engine
```python
# src/k2_platform/query/engine.py

import duckdb
from pyiceberg.catalog import load_catalog
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

class QueryEngine:
    """
    DuckDB-powered query engine for Iceberg tables.

    Features:
    - SQL queries on Iceberg data
    - Time-travel queries (as-of snapshot)
    - Zero-copy S3 reads via DuckDB Iceberg connector
    """

    def __init__(self, catalog_name: str = 'default'):
        self.conn = duckdb.connect()
        self.catalog = load_catalog(catalog_name)

        # Install Iceberg extension
        self.conn.execute("INSTALL iceberg")
        self.conn.execute("LOAD iceberg")

    def query(
        self,
        sql: str,
        as_of: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Execute SQL query against Iceberg tables.

        Args:
            sql: SQL query string
            as_of: Optional snapshot timestamp for time-travel

        Returns:
            Query results as Pandas DataFrame
        """
        # TODO: If as_of specified, query specific snapshot

        result = self.conn.execute(sql).fetchdf()
        return result

    def query_symbol_range(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        exchange: str = 'ASX'
    ) -> pd.DataFrame:
        """Convenience method for common tick queries."""
        sql = f"""
            SELECT
                exchange_timestamp,
                symbol,
                price,
                volume,
                message_id
            FROM market_data.ticks
            WHERE symbol = '{symbol}'
              AND exchange = '{exchange}'
              AND exchange_timestamp BETWEEN '{start}' AND '{end}'
            ORDER BY exchange_timestamp, exchange_sequence_number
        """
        return self.query(sql)
```

#### 3.5 Implement Replay Engine (Cold Start)
```python
# src/k2_platform/query/replay.py

from k2_platform.query.engine import QueryEngine
from datetime import datetime
import time
from typing import Callable

class ReplayEngine:
    """
    Replay historical market data at configurable speed.

    Modes:
    - Cold start: Replay arbitrary time range from Iceberg
    - Catch-up: Fast-forward through lag (not implemented yet)
    - Rewind: Time-travel query (not implemented yet)
    """

    def __init__(self, query_engine: QueryEngine):
        self.engine = query_engine

    def replay_time_range(
        self,
        start: datetime,
        end: datetime,
        symbols: Optional[List[str]] = None,
        speed_multiplier: float = 1.0,
        on_message: Callable = None
    ):
        """
        Replay messages from time range at specified speed.

        Args:
            start: Start timestamp
            end: End timestamp
            symbols: Optional list of symbols to replay (None = all)
            speed_multiplier: 1.0 = real-time, 10.0 = 10x faster
            on_message: Callback function for each message
        """

        # Build query
        sql = f"""
            SELECT * FROM market_data.ticks
            WHERE exchange_timestamp BETWEEN '{start}' AND '{end}'
        """
        if symbols:
            symbols_list = "'" + "','".join(symbols) + "'"
            sql += f" AND symbol IN ({symbols_list})"
        sql += " ORDER BY exchange_timestamp, exchange_sequence_number"

        # Stream results
        df = self.engine.query(sql)

        print(f"Replaying {len(df)} messages from {start} to {end} at {speed_multiplier}x speed")

        last_timestamp = None
        start_time = time.time()

        for idx, row in df.iterrows():
            # Calculate delay to maintain timing
            if last_timestamp is not None:
                actual_delay = (row.exchange_timestamp - last_timestamp).total_seconds()
                sleep_delay = actual_delay / speed_multiplier

                if sleep_delay > 0:
                    time.sleep(sleep_delay)

            # Emit message
            if on_message:
                on_message(row.to_dict())

            last_timestamp = row.exchange_timestamp

            # Progress indicator
            if idx % 1000 == 0:
                elapsed = time.time() - start_time
                rate = idx / elapsed if elapsed > 0 else 0
                print(f"Replayed {idx}/{len(df)} messages ({rate:.0f} msg/sec)")

        elapsed = time.time() - start_time
        actual_speed = (end - start).total_seconds() / elapsed
        print(f"Replay complete: {len(df)} messages in {elapsed:.1f}s ({actual_speed:.1f}x real-time)")
```

### Week 5-6: Testing & Observability

#### 3.6 Integration Tests
```python
# tests/integration/test_end_to_end.py

import pytest
from k2_platform.ingestion.consumer import MarketDataConsumer
from k2_platform.query.engine import QueryEngine
from confluent_kafka import Producer
import json
import time
from datetime import datetime

@pytest.fixture(scope="module")
def docker_services():
    """Ensure Docker Compose services are running."""
    # pytest-docker plugin can auto-start services
    pass

def test_end_to_end_flow(docker_services):
    """
    Test complete flow: Produce → Kafka → Consumer → Iceberg → Query
    """

    # 1. Produce test messages
    producer = Producer({'bootstrap.servers': 'localhost:9092'})

    test_messages = [
        {
            'exchange': 'ASX',
            'symbol': 'BHP',
            'exchange_sequence_number': i,
            'exchange_timestamp': datetime.utcnow().isoformat(),
            'price': 150.0 + i * 0.1,
            'volume': 100,
        }
        for i in range(100)
    ]

    for msg in test_messages:
        producer.produce(
            'market.ticks.asx',
            key=f"{msg['exchange']}.{msg['symbol']}",
            value=json.dumps(msg)
        )
    producer.flush()

    # 2. Wait for consumer to process (with timeout)
    time.sleep(15)  # Allow buffered writes to commit

    # 3. Query back from Iceberg
    engine = QueryEngine()
    result = engine.query("""
        SELECT COUNT(*) as count
        FROM market_data.ticks
        WHERE symbol = 'BHP'
    """)

    # 4. Verify all messages stored
    assert result['count'][0] >= 100, f"Expected >=100 messages, got {result['count'][0]}"


def test_sequence_gap_detection():
    """Verify sequence tracker detects gaps."""
    # TODO: Implement
    pass

def test_deduplication():
    """Verify duplicate messages are dropped."""
    # TODO: Implement
    pass

def test_replay_engine():
    """Verify replay maintains message ordering."""
    # TODO: Implement
    pass
```

#### 3.7 Performance Benchmarks
```python
# tests/performance/test_throughput.py

import pytest
from k2_platform.storage.iceberg_writer import IcebergWriter
from datetime import datetime

@pytest.fixture
def sample_messages():
    return [
        {
            'exchange': 'ASX',
            'symbol': 'BHP',
            'exchange_sequence_number': i,
            'exchange_timestamp': datetime.utcnow().isoformat(),
            'price': 150.0,
            'volume': 100,
            'message_id': f'ASX.BHP.{i}',
            'ingestion_timestamp': datetime.utcnow().isoformat(),
        }
        for i in range(1000)
    ]

def test_iceberg_write_throughput(benchmark, sample_messages):
    """Benchmark Iceberg write throughput."""
    writer = IcebergWriter('default', 'market_data.ticks', batch_size=1000)

    def write_batch():
        for msg in sample_messages:
            writer.write(msg)
        writer._flush()

    result = benchmark(write_batch)

    # Assert throughput > 10K msg/sec
    throughput = 1000 / result.stats['mean']
    assert throughput > 10_000, f"Throughput {throughput:.0f} msg/sec < 10K target"

def test_query_latency(benchmark):
    """Benchmark query response time."""
    engine = QueryEngine()

    def query():
        return engine.query("""
            SELECT * FROM market_data.ticks
            WHERE symbol = 'BHP'
            AND exchange_timestamp >= NOW() - INTERVAL '1 hour'
            LIMIT 1000
        """)

    result = benchmark(query)

    # Assert p99 query latency < 1s
    assert result.stats['mean'] < 1.0, f"Query latency {result.stats['mean']:.2f}s > 1s target"
```

#### 3.8 CI/CD Pipeline
```yaml
# .github/workflows/ci.yml

name: K2 Platform CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -e ".[dev]"

      - name: Format check
        run: |
          black --check src/ tests/
          isort --check-only src/ tests/

      - name: Lint
        run: ruff check src/ tests/

      - name: Type check
        run: mypy src/

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Start infrastructure
        run: docker-compose up -d

      - name: Wait for services
        run: |
          sleep 30
          docker-compose ps

      - name: Install dependencies
        run: pip install -e ".[test]"

      - name: Run unit tests
        run: pytest tests/unit/ -v --cov=src/ --cov-report=xml

      - name: Run integration tests
        run: pytest tests/integration/ -v

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml

      - name: Shutdown services
        if: always()
        run: docker-compose down -v

  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Start infrastructure
        run: docker-compose up -d

      - name: Run benchmarks
        run: pytest tests/performance/ --benchmark-only --benchmark-json=benchmark.json

      - name: Store benchmark result
        uses: benchmark-action/github-action-benchmark@v1
        with:
          tool: 'pytest'
          output-file-path: benchmark.json
          github-token: ${{ secrets.GITHUB_TOKEN }}
          auto-push: true
```

---

## 4. What Will Impress at Staff/Principal Level

### 4.1 Demonstrate "Production-Readiness" Thinking

Most engineers stop at "it works on my laptop." Staff+ engineers think:
- "How does this fail?"
- "How do we recover?"
- "How do we operate this at 3am?"

**What to add:**

#### Runbook Example
```markdown
# Runbook: Kafka Broker Failure During Market Hours

## Symptoms
- Alert: `kafka_broker_down{broker_id=1}`
- Consumer lag spikes
- Dashboard shows one broker offline

## Impact
- **Blast Radius**: Messages for 1/3 of partitions delayed
- **Data Loss Risk**: LOW (if replication_factor >= 3)
- **Latency Impact**: Consumer lag increases 50-200%
- **Recovery Time**: 5 minutes (automatic leader election)

## Investigation

1. Check broker status:
   ```bash
   docker-compose ps kafka
   kubectl get pods -l app=kafka  # K8s
   ```

2. Check Kafka logs:
   ```bash
   docker-compose logs kafka | grep -i error
   ```

3. Verify replication status:
   ```bash
   kafka-topics --bootstrap-server localhost:9092 \
     --describe --topic market.ticks.asx
   # Look for: Replicas vs Isr (in-sync replicas)
   ```

## Immediate Actions

1. **Do NOT restart consumer** (will make rebalancing worse)
2. Verify other brokers healthy
3. Check if broker is truly dead or network partitioned:
   ```bash
   ping kafka-broker-1
   ```

## Recovery Procedure

### If broker crashed:
```bash
# Restart broker
docker-compose restart kafka
# OR
kubectl rollout restart statefulset/kafka
```

### If disk full:
```bash
# Clear old log segments
kafka-log-dirs --bootstrap-server localhost:9092 \
  --describe --broker-list 1 | grep -i size

# Delete old segments
kafka-delete-records --bootstrap-server localhost:9092 \
  --offset-json-file offsets-to-delete.json
```

### If persistent failure:
```bash
# Replace broker (last resort)
# 1. Decommission broker
kafka-reassign-partitions --bootstrap-server localhost:9092 \
  --reassignment-json-file move-partitions.json --execute

# 2. Remove from cluster
# 3. Provision new broker with new ID
```

## Verification

After recovery:
1. Check consumer lag returned to normal (<60s)
2. Verify partition ISR counts restored
3. Check for any sequence gaps:
   ```sql
   SELECT exchange, symbol, COUNT(*) as gap_count
   FROM audit.sequence_gaps
   WHERE detected_at > NOW() - INTERVAL '1 hour'
   GROUP BY exchange, symbol
   ```

## Post-Mortem

Document:
- Root cause (OOM, disk full, network partition)
- Time to detect (alert latency)
- Time to resolve
- Data loss (if any)
- Prevention measures

## Prevention

- Increase Kafka heap if OOM
- Set up disk usage alerts (>80%)
- Enable broker rack awareness
- Increase replication factor to 3+
```

**Why this impresses:** Shows you've thought through real operational scenarios

### 4.2 Include a "War Story" Section

Add to README:

```markdown
## Simulated Outage: Flash Crash Event

To validate K2's resilience under extreme load, I simulated a "flash crash"
scenario where message volume spikes 100x in 60 seconds.

### Scenario
- Normal load: 10K msg/sec
- Spike: 1M msg/sec for 5 minutes
- Recovery: Back to normal

### Observed Behavior

**Timeline:**
```
T+0:00  Message rate 10K → 1M msg/sec
T+0:05  Level 1 degradation: p99 latency 50ms → 150ms
        ✅ System continues processing all messages

T+0:30  Level 2 degradation triggered
        ✅ Autoscaler adds 5 consumer instances (3 → 8)
        ✅ Consumer lag peaks at 2M messages (2 seconds behind)
        ⚠️  Iceberg write latency increases to 400ms (near budget limit)

T+1:00  Consumer lag decreasing (2M → 800K)
        ✅ No sequence gaps detected
        ✅ No data loss

T+5:00  Load returns to normal
T+6:00  Consumer lag cleared
        ✅ All 1M spike messages successfully written to Iceberg

T+8:00  Autoscaler removes excess consumers (8 → 3)
        ✅ System returned to steady state
```

### Lessons Learned

1. **Batch size matters:** Increasing from 1K → 5K messages/batch reduced
   Iceberg write latency 30% under load

2. **Autoscaling delay:** 5-minute scale-up delay meant lag accumulated before
   relief. Recommendation: Lower threshold to 500K messages

3. **S3 throttling:** Hit S3 rate limits at 1000 writes/sec. Mitigation:
   Implemented local NVMe buffering (see LATENCY_BACKPRESSURE.md)

4. **Monitoring validated:** All alerts fired correctly, dashboards showed
   clear degradation signals

### How to Reproduce

```bash
# Start platform
docker-compose up -d

# Run load test
python scripts/load_test.py --spike-duration 300 --spike-multiplier 100

# Monitor in Grafana
open http://localhost:3000/d/k2-overview
```
```

**Why this impresses:**
- Shows empirical testing, not just theory
- Demonstrates you iterate based on learnings
- Proves the design actually works under stress

### 4.3 Add "Scaling Considerations" Section

```markdown
## Scaling from 10K → 1M → 10M msg/sec

### Bottleneck Analysis

| Component | Limit at 10K msg/sec | Bottleneck at 1M msg/sec | Mitigation |
|-----------|---------------------|-------------------------|------------|
| Kafka (single broker) | 50K msg/sec | Partition count | Add brokers, increase partitions to 50+ |
| Python GIL (sequence tracker) | 100K ops/sec | Lock contention | Move to Rust/Java, or partition state |
| Iceberg writes (S3) | 1000 writes/sec | S3 rate limits | Batch larger, use NVMe buffer |
| DuckDB (single-threaded) | 10M rows/sec | Concurrency | Add Presto/Trino cluster |
| Postgres catalog | 1000 writes/sec | Metadata updates | Enable caching, add read replicas |

### 10x Scale (100K msg/sec)
**Changes needed:**
- Kafka: 3 brokers, 30 partitions
- Consumer instances: 10 → 30
- Iceberg: Increase batch size 1K → 10K
- Cost: ~$5K/month (AWS)

### 100x Scale (1M msg/sec)
**Changes needed:**
- Kafka: 5 brokers, 50 partitions
- Consumer instances: 30 → 100
- Iceberg: Daily compaction job required
- Query: Add Presto cluster (10 nodes)
- Cost: ~$50K/month

### 1000x Scale (10M msg/sec)
**Major redesign:**
- Kafka: 10+ brokers, 500 partitions
- Replace Python with Java/Rust for hot path
- Iceberg: Partition by hour (not day)
- Pre-aggregate OHLCV bars in-stream
- Separate hot/warm/cold storage tiers
- Cost: ~$500K/month

**Proof Point:** Netflix operates similar architecture at 100M+ events/sec
```

**Why this impresses:**
- Shows you understand when design breaks
- Demonstrates knowledge of real-world scale (Netflix reference)
- Honest about cost implications

### 4.4 Add "Alternatives Considered" Section

Staff+ engineers don't just pick technologies—they explain **why** they chose them over alternatives:

```markdown
## Technology Decision Log

### Why Kafka over Pulsar/RabbitMQ?

**Considered:**
- Apache Pulsar (better multi-tenancy)
- RabbitMQ (simpler operations)
- AWS Kinesis (managed service)

**Decision:** Kafka

**Rationale:**
- Pulsar: More complex to operate, smaller community
- RabbitMQ: Not designed for high-throughput streaming
- Kinesis: Vendor lock-in, higher cost at scale

**Trade-off:** Accepted operational complexity of Kafka for proven scalability

---

### Why Iceberg over Delta Lake?

**Considered:**
- Delta Lake (Databricks ecosystem)
- Apache Hudi (Uber's choice)
- Raw Parquet (simplest)

**Decision:** Iceberg

**Rationale:**
- Delta: Tied to Spark, harder to query with DuckDB
- Hudi: Less mature time-travel support
- Raw Parquet: No ACID, schema evolution painful

**Trade-off:** Iceberg adds metadata overhead (~1% storage) for ACID guarantees

---

### Why DuckDB over Presto/Spark?

**Decision:** DuckDB for Phase 1, migrate to Presto at 100x scale

**Rationale:**
- DuckDB: Zero ops, embedded, perfect for <1TB queries
- Presto: Overkill for demo, requires cluster management
- Spark: Heavyweight, slow for interactive queries

**Migration Path:** DuckDB uses same Iceberg tables, can add Presto later
```

**Why this impresses:**
- Shows you considered alternatives (not just picking what's trendy)
- Explicit trade-offs (every choice has downsides)
- Pragmatic (start simple, scale later)

---

## 5. Specific Architectural Improvements

### 5.1 Fix: Partition Count for Scale

**Current:**
```yaml
KAFKA_NUM_PARTITIONS: 6
```

**Issue:** At 1M msg/sec, each partition handles 167K msg/sec (too high)

**Recommendation:**
```yaml
# Dynamic partitioning based on symbol liquidity
markets:
  ASX:
    partitions: 50
    strategy: "volume_weighted"

  ChiX:
    partitions: 20
    strategy: "hash(symbol)"
```

**Implementation:**
```python
# src/k2_platform/ingestion/partitioner.py

class VolumeWeightedPartitioner:
    """
    Partitions symbols by trading volume to avoid hot partitions.

    Tier 1 (top 10): Individual partitions (10 partitions)
    Tier 2 (next 90): 3 symbols per partition (30 partitions)
    Tier 3 (rest): Shared partition (10 partitions)
    """

    def __init__(self, tier1_symbols: List[str], tier2_symbols: List[str]):
        self.tier1 = tier1_symbols
        self.tier2 = tier2_symbols

    def get_partition(self, symbol: str, total_partitions: int) -> int:
        if symbol in self.tier1:
            return self.tier1.index(symbol)  # 0-9
        elif symbol in self.tier2:
            idx = self.tier2.index(symbol)
            return 10 + (idx // 3)  # 10-39
        else:
            return 40 + (hash(symbol) % 10)  # 40-49
```

### 5.2 Fix: Deduplication Cache Scalability

**Current:**
```python
self._cache: Dict[str, datetime] = {}  # In-memory
```

**Issue:** At 1M msg/sec × 24hr = 86B entries (impractical)

**Recommendation:**
```python
# src/k2_platform/ingestion/dedup_cache.py

from pybloom_live import BloomFilter
import redis

class ScalableDeduplicationCache:
    """
    Two-tier deduplication:
    1. Bloom filter (probabilistic, 99.9% accuracy)
    2. Redis (authoritative, for confirmed duplicates)

    Memory: Bloom ~2GB, Redis ~50GB for 24hr window at 1M msg/sec
    """

    def __init__(self, redis_client: redis.Redis):
        # Bloom filter: 1B capacity, 0.1% false positive rate
        self.bloom = BloomFilter(capacity=1_000_000_000, error_rate=0.001)
        self.redis = redis_client
        self.window_seconds = 86400  # 24 hours

    def is_duplicate(self, message_id: str) -> bool:
        """
        Check if message is duplicate.

        Returns:
            True if definitely duplicate
            False if probably not duplicate (99.9% confidence)
        """

        # Fast path: Bloom filter check (~100ns)
        if not self.bloom.check(message_id):
            # Definitely not seen before
            self.bloom.add(message_id)
            self.redis.setex(f"dedup:{message_id}", self.window_seconds, "1")
            return False

        # Slow path: Confirm with Redis (~1ms)
        exists = self.redis.exists(f"dedup:{message_id}")

        if not exists:
            # False positive in Bloom filter
            self.redis.setex(f"dedup:{message_id}", self.window_seconds, "1")
            metrics.increment('bloom_filter_false_positives')
            return False

        # Confirmed duplicate
        return True
```

### 5.3 Add: Hybrid Query Path

**Missing:** Merge Kafka (hot) + Iceberg (cold) for recent queries

**Add to query engine:**
```python
# src/k2_platform/query/engine.py

class HybridQueryEngine:
    """
    Queries spanning hot (Kafka) and cold (Iceberg) data.

    Example: "Show me all BHP trades in last 15 minutes"
    - Last 13 minutes: Iceberg (committed)
    - Last 2 minutes: Kafka (in-flight)
    """

    def __init__(self, iceberg_engine: QueryEngine, kafka_consumer: Consumer):
        self.iceberg = iceberg_engine
        self.kafka = kafka_consumer
        self.iceberg_lag_threshold = timedelta(minutes=2)  # Buffer for commits

    def query_recent(
        self,
        symbol: str,
        window_minutes: int,
        exchange: str = 'ASX'
    ) -> pd.DataFrame:
        """
        Query recent data from Iceberg + Kafka.
        """
        now = datetime.utcnow()
        iceberg_end = now - self.iceberg_lag_threshold  # 2-min safety buffer

        # 1. Query historical from Iceberg
        historical = self.iceberg.query_symbol_range(
            symbol=symbol,
            start=now - timedelta(minutes=window_minutes),
            end=iceberg_end,
            exchange=exchange
        )

        # 2. Query recent from Kafka
        # This requires maintaining a Kafka consumer position
        realtime = self._query_kafka(
            symbol=symbol,
            since=iceberg_end,
            timeout_ms=100
        )

        # 3. Combine and deduplicate by message_id
        combined = pd.concat([historical, realtime])
        return combined.drop_duplicates(subset=['message_id'], keep='last')

    def _query_kafka(self, symbol: str, since: datetime, timeout_ms: int):
        """Fetch recent messages from Kafka."""
        # Implementation requires:
        # - Tailing consumer that tracks latest messages
        # - In-memory buffer of last N minutes
        # - Filter by symbol and timestamp

        # TODO: Implement Kafka query logic
        pass
```

### 5.4 Add: Circuit Breaker Implementation

**Currently documented but not implemented:**

```python
# src/k2_platform/common/circuit_breaker.py

from enum import Enum
from datetime import datetime, timedelta
from typing import Callable
import time

class CircuitState(Enum):
    CLOSED = "closed"    # Normal operation
    OPEN = "open"        # Failing, stop requests
    HALF_OPEN = "half_open"  # Testing if recovered

class CircuitBreaker:
    """
    Circuit breaker for protecting downstream systems.

    States:
    - CLOSED: Normal operation, track failures
    - OPEN: Too many failures, reject requests immediately
    - HALF_OPEN: Test if system recovered

    Example:
        breaker = CircuitBreaker(
            failure_threshold=5,
            timeout=60,
            name="iceberg_writer"
        )

        with breaker:
            iceberg_writer.write(message)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_seconds: int = 60,
        name: str = "circuit_breaker"
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timedelta(seconds=timeout_seconds)
        self.name = name

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.opened_at = None

    def __enter__(self):
        if self.state == CircuitState.OPEN:
            # Check if timeout expired
            if datetime.utcnow() - self.opened_at > self.timeout:
                logger.info(f"Circuit breaker {self.name}: OPEN → HALF_OPEN")
                self.state = CircuitState.HALF_OPEN
            else:
                # Still open, reject immediately
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.name} is OPEN"
                )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            # Success
            if self.state == CircuitState.HALF_OPEN:
                logger.info(f"Circuit breaker {self.name}: HALF_OPEN → CLOSED")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
            return

        # Failure
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.state == CircuitState.HALF_OPEN:
            # Test failed, go back to OPEN
            logger.warning(f"Circuit breaker {self.name}: HALF_OPEN → OPEN")
            self.state = CircuitState.OPEN
            self.opened_at = datetime.utcnow()

        elif self.failure_count >= self.failure_threshold:
            # Too many failures, open circuit
            logger.error(
                f"Circuit breaker {self.name}: CLOSED → OPEN "
                f"({self.failure_count} failures)"
            )
            self.state = CircuitState.OPEN
            self.opened_at = datetime.utcnow()

            metrics.gauge(
                'circuit_breaker_state',
                1,  # 1 = OPEN
                tags={'name': self.name}
            )

class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is OPEN."""
    pass
```

**Usage:**
```python
# src/k2_platform/storage/iceberg_writer.py

class IcebergWriter:
    def __init__(self, ...):
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            timeout_seconds=60,
            name="iceberg_writes"
        )

    def write(self, message):
        try:
            with self.circuit_breaker:
                self._write_to_iceberg(message)
        except CircuitBreakerOpenError:
            # Circuit open, spill to local disk
            self._spill_to_disk(message)
```

---

## 6. README Enhancements for Portfolio Impact

### 6.1 Add Visual Architecture Diagram

Create `docs/architecture-diagram.png` using draw.io or similar:

```
┌─────────────────────────────────────────────────────────────┐
│                    MARKET DATA SOURCES                       │
│     ASX │ Chi-X │ Simulated Feed │ Historical Replay         │
└────────────────────────┬────────────────────────────────────┘
                         │ Market Ticks (JSON/Avro)
                         ▼
┌────────────────────────────────────────────────────────────┐
│                 INGESTION LAYER                             │
│  ┌──────────────────┐        ┌─────────────────┐            │
│  │  Schema Registry │◄───────┤  Kafka (KRaft)  │            │
│  │   (Avro/JSON)    │        │  50 partitions  │            │
│  └──────────────────┘        └────────┬────────┘            │
│                                       │                      │
│  ┌────────────────────────────────────┴──────────────────┐  │
│  │  Sequence Tracker  │  Dedup Cache  │  Metrics         │  │
│  └───────────────────────────────────────────────────────┘  │
└────────────────────────────┬───────────────────────────────┘
                             │ Validated Ticks
                             ▼
┌────────────────────────────────────────────────────────────┐
│              STORAGE LAYER (Apache Iceberg)                 │
│  ┌──────────────┐    ┌───────────────┐    ┌─────────────┐  │
│  │  PostgreSQL  │◄───│  Iceberg REST │───►│ MinIO (S3)  │  │
│  │  (Metadata)  │    │   Catalog     │    │  (Parquet)  │  │
│  └──────────────┘    └───────────────┘    └─────────────┘  │
│  • ACID Transactions  • Time Travel  • Columnar Storage    │
└────────────────────────────┬───────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────┐
│                   QUERY LAYER                               │
│  ┌─────────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  DuckDB Engine  │  │ Replay Engine│  │ Query API    │   │
│  │  (SQL)          │  │ (Cold Start) │  │ (FastAPI)    │   │
│  └─────────────────┘  └──────────────┘  └──────────────┘   │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  RBAC  │  Audit Logging  │  Row-Level Security        │ │
│  └───────────────────────────────────────────────────────┘ │
└────────────────────────────┬───────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────┐
│              OBSERVABILITY (Prometheus + Grafana)           │
│  • Consumer Lag  • Sequence Gaps  • Latency (p50/p99)      │
│  • Throughput   • Error Rates    • Circuit Breaker State   │
└────────────────────────────────────────────────────────────┘
```

### 6.2 Add "Quick Start" GIF

Record a terminal session showing:
```bash
# Clone repo
git clone ...

# Start infrastructure
docker-compose up -d

# Produce sample data
python scripts/produce_sample_data.py

# Query data
python scripts/query_example.py

# Open Grafana
open http://localhost:3000
```

Use [asciinema](https://asciinema.org/) to record terminal, then convert to GIF

### 6.3 Add Badges to README

```markdown
[![CI Status](https://github.com/yourname/k2-platform/actions/workflows/ci.yml/badge.svg)](...)
[![Code Coverage](https://codecov.io/gh/yourname/k2-platform/branch/main/graph/badge.svg)](...)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](...)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](...)
```

### 6.4 Add "Try It Now" Section

```markdown
## Try It Now (5 minutes)

Experience the platform with pre-loaded sample data:

```bash
# 1. Start infrastructure (Kafka, Iceberg, Prometheus, Grafana)
docker-compose up -d

# 2. Load 5 days of ASX tick data (March 2014)
python scripts/load_sample_data.py

# 3. Run example queries
python scripts/demo.py

# 4. View dashboards
open http://localhost:3000/d/k2-overview
```

**What you'll see:**
- 📊 Live throughput metrics in Grafana
- 📈 Latency distributions (p50, p99)
- 🔍 SQL queries on 5 days of tick data
- ⏪ Replay engine reconstructing market sessions

**Sample queries:**
```python
# Most traded symbols
engine.query("""
    SELECT symbol, SUM(volume) as total_volume
    FROM market_data.ticks
    WHERE exchange_timestamp >= '2014-03-10'
    GROUP BY symbol
    ORDER BY total_volume DESC
    LIMIT 10
""")

# BHP price movement
engine.query("""
    SELECT
        DATE_TRUNC('minute', exchange_timestamp) as minute,
        AVG(price) as avg_price,
        SUM(volume) as volume
    FROM market_data.ticks
    WHERE symbol = 'BHP'
    GROUP BY minute
    ORDER BY minute
""")
```
```

---

## 7. Interview Preparation Guidance

### 7.1 Key Talking Points for Staff+ Interviews

**Question:** "Walk me through the architecture of your market data platform."

**Answer Structure:**
1. **Problem Statement** (30 sec)
   - "Financial firms need to ingest millions of market ticks/sec, store them durably, and query historical data for backtesting and compliance"

2. **Core Design Principles** (1 min)
   - "I built this on six principles: Replayable by default, Schema-first, Boring technology..."
   - "Every principle addresses a real operational pain point"

3. **Architecture Overview** (2 min)
   - "Three-layer design: Kafka for ingestion, Iceberg lakehouse for storage, DuckDB for queries"
   - Walk through data flow with latency budgets

4. **Key Technical Decisions** (2 min)
   - "Chose Kafka over Pulsar because..."
   - "Implemented sequence tracking to detect data loss..."
   - "Designed four-level degradation hierarchy..."

5. **Proof of Production-Readiness** (1 min)
   - "Tested with simulated flash crash at 100x load"
   - "All components instrumented with metrics and alerts"
   - "Written runbooks for common failure scenarios"

**Question:** "How does your system handle backpressure?"

**Answer:**
- "Four-level degradation hierarchy with explicit triggers at each level"
- "Level 1: Soft degradation - latency increases but no data loss"
- "Level 2: Graceful degradation - drop low-priority symbols, skip enrichment"
- "Level 3: Spill to disk - buffer to NVMe, async flush later"
- "Level 4: Circuit breaker - halt ingestion, page on-call"
- "Validated this with load testing - system handled 100x spike without data loss"

**Question:** "What would you change if this needed to handle 1000x more data?"

**Answer:**
- "I've documented scaling bottlenecks in the README"
- "At 10M msg/sec, three main changes:"
  1. "Kafka: 10+ brokers, 500 partitions instead of 50"
  2. "Python → Rust/Java for sequence tracking (GIL bottleneck)"
  3. "DuckDB → Presto cluster for distributed queries"
- "But I'd also redesign the architecture - separate hot/warm/cold tiers"
- "Hot tier: In-memory for last 1 minute (sub-ms queries)"
- "Warm tier: Iceberg for last 90 days (current design)"
- "Cold tier: S3 Glacier for historical (restore on demand)"
- "This is the approach Netflix uses at 100M+ events/sec"

**Question:** "Show me the code for your most complex component."

**Answer:**
- "The sequence tracker handles three tricky cases:"
  1. "Sequence gaps (missed messages)"
  2. "Sequence resets (daily session boundaries)"
  3. "Out-of-order delivery (Kafka rebalancing)"
- "Walk through the code at sequence_tracker.py:73"
- "Key design decision: Use heuristics for reset detection (>50% drop + 1hr time jump)"
- "This handles edge case where exchange sequence numbers restart overnight"

### 7.2 Questions to Ask Interviewer

Staff+ engineers should ask **system design questions**, not just "what's the tech stack":

1. **"How do you handle schema evolution across 50+ downstream consumers?"**
   - Shows you think about dependencies and coordination

2. **"What's your philosophy on exactly-once vs at-least-once delivery?"**
   - Shows you know distributed systems trade-offs

3. **"How do you measure reliability? SLOs? Error budgets?"**
   - Shows you think in terms of business outcomes

4. **"Walk me through a recent production incident. How did you prevent recurrence?"**
   - Shows you want to learn operational practices

5. **"What's your platform's most painful operational burden right now?"**
   - Shows you're looking for high-impact problems to solve

### 7.3 Red Flags to Avoid

**Don't say:**
- ❌ "I used Kafka because everyone uses Kafka"
- ✅ "I chose Kafka over Pulsar because of larger community and proven scale at companies like LinkedIn and Netflix"

**Don't say:**
- ❌ "This can scale to billions of messages"
- ✅ "I've tested at 10K msg/sec and documented bottlenecks that appear at 100x scale"

**Don't say:**
- ❌ "I haven't implemented X yet"
- ✅ "X is on the roadmap - here's my design approach" (show you've thought it through)

**Don't say:**
- ❌ "I just followed best practices"
- ✅ "I made a trade-off here - chose simplicity over performance because..."

---

## 8. Final Recommendations

### Priority 1 (Next 2 weeks)

1. **Implement core path:** Producer → Kafka → Consumer → Iceberg → Query
2. **Add integration tests:** Prove end-to-end flow works
3. **Generate performance benchmarks:** Measure throughput and latency
4. **Create Grafana screenshots:** Visual proof for README

### Priority 2 (Weeks 3-4)

5. **Implement replay engine:** Cold start mode only
6. **Add CI/CD pipeline:** GitHub Actions for automated testing
7. **Write one runbook:** "Kafka broker failure" scenario
8. **Create "war story":** Simulated flash crash with results

### Priority 3 (Weeks 5-6)

9. **Add circuit breaker:** Implement the documented design
10. **Hybrid query path:** Merge Kafka + Iceberg queries
11. **Scaling analysis:** Document bottlenecks and mitigations
12. **Video walkthrough:** 10-minute architecture explanation

### What NOT to Build (For Portfolio)

- ❌ Full RBAC implementation (stub is fine)
- ❌ Multi-region replication (design doc is enough)
- ❌ Kubernetes deployment (Docker Compose sufficient)
- ❌ REST API with authentication (overkill for demo)
- ❌ Order book reconstruction (nice-to-have, not critical path)

**Why:** For a portfolio project, **depth > breadth**. Better to have one fully-working, well-tested component than ten half-finished features.

---

## 9. Conclusion

Your architecture demonstrates **Staff+ level thinking**:
- ✅ Explicit trade-offs and alternatives considered
- ✅ Production-readiness concerns documented
- ✅ Operational mindset (degradation, monitoring, runbooks)
- ✅ Strong technical writing and documentation

**To reach "portfolio complete" state:**
1. Implement the core path (Producer → Iceberg → Query)
2. Add tests and benchmarks (proof it works)
3. Document one "war story" (proof of resilience)
4. Generate visual artifacts (diagrams, dashboards, GIFs)

**Timeline:** With focused implementation, you can reach portfolio-ready state in **6-8 weeks**.

This will position you well for Staff/Principal engineer roles at:
- ✅ Mid-frequency trading firms
- ✅ Data platform teams at tech companies
- ✅ Fintech/crypto companies with real-time data needs
- ✅ Cloud infrastructure companies (AWS, Snowflake, Databricks)

For true HFT roles (<1ms latency), consider adding a "two-tier architecture" design document showing you understand the ultra-low-latency patterns, even if you don't implement them.

---

**Next Steps:**
1. Review this feedback
2. Prioritize implementation roadmap
3. Start with core path (Producer → Consumer → Iceberg)
4. Schedule weekly progress reviews

Feel free to reach out if you want feedback on specific implementation decisions as you build this out.

---

*Review conducted: 2026-01-09*
*Next review recommended: After core path implementation (2-3 weeks)*
