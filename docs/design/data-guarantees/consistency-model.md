# Data Consistency Guarantees

**Last Updated**: 2026-01-09
**Owners**: Platform Team
**Status**: Implementation Plan
**Scope**: End-to-end consistency model, cross-layer consistency

---

## Overview

Distributed systems inherently involve trade-offs between consistency, availability, and partition tolerance (CAP theorem). This document defines the consistency guarantees provided by the K2 platform across all layers (Kafka, Iceberg, Query API) and explains when and why inconsistencies may occur.

**Design Philosophy**: Eventual consistency with bounded staleness. We prioritize availability and partition tolerance while providing predictable consistency bounds that users can reason about.

---

## Consistency Model

### Eventual Consistency with Bounded Staleness

**Guarantee**: All data in Kafka will eventually appear in Iceberg and be queryable via the Query API

**Staleness Bound**:
- **p99**: Iceberg lags Kafka by at most 60 seconds
- **p99.9**: Iceberg lags Kafka by at most 120 seconds

**Rationale**:
- Strong consistency (synchronous Kafka → Iceberg write) would add 2-3x latency
- Market data is append-only (no updates/deletes) → eventual consistency is safe
- Users can choose real-time (Kafka) or historical (Iceberg) based on staleness tolerance

---

## Layer-by-Layer Consistency

### Layer 1: Kafka (Ingestion)

**Consistency Model**: Per-partition ordering

**Guarantees**:
- Messages within a partition are ordered (FIFO)
- Messages across partitions have no ordering guarantee
- At-least-once delivery (duplicates possible, no message loss)

**Configuration**:
```properties
# Producer config
acks = 1                          # Leader acknowledgment (low latency)
enable.idempotence = true         # Prevent duplicates from retries
max.in.flight.requests.per.connection = 5

# Consumer config
enable.auto.commit = false        # Manual offset management
isolation.level = read_uncommitted  # Read all messages (default)
```

**Consistency Trade-offs**:
- `acks=1` vs `acks=all`: We choose `acks=1` for lower latency (10ms vs 30ms p99)
  - Risk: If leader fails before replicating, message may be lost
  - Mitigation: `min.insync.replicas=2` ensures at least 2 replicas

**Message Ordering**:
```python
# Within partition: Guaranteed order
Partition 0: [msg1, msg2, msg3]  # Always delivered in this order

# Across partitions: No guarantee
Partition 0: [msg1 @ 10:00:00.001]
Partition 1: [msg2 @ 10:00:00.000]
# Consumer may see msg1 before msg2 despite earlier timestamp
```

**Handling**: Use hybrid query mode (see Query Architecture) for cross-symbol ordering

---

### Layer 2: Kafka → Iceberg (Storage)

**Consistency Model**: Eventual consistency with idempotent writes

**Write Flow**:
```
1. Consumer polls batch from Kafka (e.g., 1000 messages)
2. Consumer writes batch to Iceberg (buffered)
3. Iceberg commits snapshot (ACID transaction)
4. Consumer commits Kafka offset (manual, after Iceberg commit)
```

**Key Property**: Consumer offsets committed AFTER Iceberg write succeeds

**Implication**: At-least-once delivery guarantee
- If consumer crashes between step 3 and 4 → data replayed (duplicates)
- Iceberg deduplication handles duplicates (merge-on-read by `message_id`)

**Staleness Analysis**:

| Stage | Duration | Cumulative |
|-------|----------|------------|
| Kafka consumer poll | 5-10ms | 10ms |
| Business logic processing | 20-50ms | 60ms |
| Iceberg write (buffered) | 100-200ms | 260ms |
| Iceberg snapshot commit | 50-100ms | 360ms |
| Consumer offset commit | 10ms | 370ms |

**p50 Staleness**: 370ms
**p99 Staleness**: 60 seconds (when backlog builds up)

**Guarantees**:
- No data loss (messages replayed on failure)
- No duplicate rows in Iceberg (deduplication by `message_id`)
- Writes are atomic (all-or-nothing per Iceberg snapshot)

---

### Layer 3: Iceberg (Storage)

**Consistency Model**: ACID transactions with snapshot isolation

**Guarantees**:
- **Atomicity**: Writes are all-or-nothing (snapshot commits atomically)
- **Consistency**: Constraints enforced (e.g., PRIMARY KEY on `message_id`)
- **Isolation**: Concurrent reads see consistent snapshots (no dirty reads)
- **Durability**: Committed snapshots persisted to S3 (durable storage)

**Snapshot Isolation**:
```python
# Writer 1 writes to Iceberg
writer1.write_batch([tick1, tick2, tick3])
writer1.commit()  # Creates snapshot 100

# Reader 1 queries (sees snapshot 100)
df1 = table.scan().to_pandas()  # 1,000,000 rows

# Writer 2 writes to Iceberg
writer2.write_batch([tick4, tick5])
writer2.commit()  # Creates snapshot 101

# Reader 1 still sees snapshot 100 (unchanged)
df2 = table.scan().to_pandas()  # 1,000,000 rows (same as before)

# Reader 2 sees latest snapshot 101
df3 = table.scan().to_pandas()  # 1,000,002 rows (includes tick4, tick5)
```

**Key Property**: Reads are repeatable (snapshot isolation prevents phantom reads)

**Time-Travel Queries**:
```python
# Query as of specific timestamp
df = table.scan(snapshot_id=100).to_pandas()

# Query as of specific time
df = table.scan(as_of_timestamp='2026-01-09 14:30:00').to_pandas()
```

**Use Case**: Compliance audits ("Show me what data looked like at 2 PM yesterday")

---

### Layer 4: Query API (Real-time + Historical)

**Consistency Model**: Tunable consistency per query mode

#### Mode 1: Real-Time Only (Last 5 Minutes)

**Data Source**: Kafka

**Consistency**: Eventually consistent with Kafka
- Lag: 10-50ms (Kafka consumer tail read)
- Ordering: Per-partition only (no cross-symbol ordering)

**Guarantees**:
- Latest data (within 50ms of exchange)
- May miss ticks still in-flight (Kafka buffer)
- No deduplication (may see duplicates from Kafka retries)

#### Mode 2: Historical Only (Older than 5 Minutes)

**Data Source**: Iceberg

**Consistency**: Snapshot isolation
- Lag: 60 seconds p99 (Kafka → Iceberg write time)
- Ordering: Total order by `exchange_timestamp` (per symbol)

**Guarantees**:
- Repeatable reads (snapshot isolation)
- Deduplicated data (no duplicates)
- Time-travel capable (query as of past timestamp)

#### Mode 3: Hybrid (Spans Both Real-Time and Historical)

**Data Source**: Iceberg (historical) + Kafka (real-time)

**Consistency Challenge**: Potential gap at boundary

**Problem**:
```
Timeline:
10:00:00 - 10:04:55 → Iceberg (historical)
10:04:55 - 10:05:00 → ???  (Kafka → Iceberg write in progress)
10:05:00 - 10:05:30 → Kafka (real-time)

Query: "Give me all ticks from 10:00:00 to 10:05:30"
Risk: Miss ticks in the gap (10:04:55 - 10:05:00)
```

**Solution**: Watermark-based merging

```python
class HybridQueryEngine:
    """
    Query across Kafka and Iceberg with consistency guarantee.
    """

    WATERMARK_SECONDS = 60  # Safety buffer

    def query(self, symbol: str, start_time: datetime, end_time: datetime):
        """
        Query hybrid mode with no gaps.

        Watermark strategy:
        - Query Iceberg for [start_time, now - 60s]
        - Query Kafka for [now - 60s, end_time]
        - Deduplicate overlap by message_id
        """
        now = datetime.utcnow()
        watermark = now - timedelta(seconds=self.WATERMARK_SECONDS)

        # Historical query (with overlap)
        historical = self.iceberg_engine.query(f"""
            SELECT * FROM market_data.ticks
            WHERE symbol = '{symbol}'
              AND exchange_timestamp BETWEEN '{start_time}' AND '{watermark}'
        """)

        # Real-time query (with overlap)
        realtime = self.kafka_engine.query(
            symbol=symbol,
            start_time=watermark,  # Overlap with Iceberg
            end_time=end_time
        )

        # Merge and deduplicate (prefer Iceberg version if duplicate)
        return self._merge_deduplicate(historical, realtime)

    def _merge_deduplicate(self, historical, realtime):
        """
        Merge historical and real-time data, removing duplicates.

        Deduplication strategy:
        - Use message_id as unique key
        - Prefer historical (Iceberg) version (already validated and deduplicated)
        """
        import pandas as pd

        # Combine datasets
        combined = pd.concat([historical, realtime])

        # Deduplicate by message_id (keep first = Iceberg version)
        deduplicated = combined.drop_duplicates(subset=['message_id'], keep='first')

        # Sort by exchange timestamp
        return deduplicated.sort_values('exchange_timestamp')
```

**Guarantee**: No gaps in query results (watermark overlap ensures coverage)

**Trade-off**: 60-second overlap between Iceberg and Kafka (small deduplication overhead)

---

## Cross-Symbol Consistency

### Problem: No Cross-Symbol Ordering Guarantee

**Scenario**: Strategy needs synchronized view of BHP and CBA prices

```python
# Query: "What were BHP and CBA prices at exactly 10:00:00?"
query = """
    SELECT symbol, price, exchange_timestamp
    FROM ticks
    WHERE symbol IN ('BHP', 'CBA')
      AND exchange_timestamp = '2026-01-09 10:00:00'
"""
```

**Challenge**: BHP and CBA are on different Kafka partitions
- BHP on partition 0
- CBA on partition 15
- No ordering guarantee across partitions

**Result**: Consumer may see CBA tick before BHP tick, even if BHP tick has earlier exchange timestamp

### Solution: Buffered Synchronization

```python
class SynchronizedConsumer:
    """
    Buffer messages and emit in exchange timestamp order.

    Trade-off: Adds latency (buffering delay) for correctness.
    """

    def __init__(self, symbols: list[str], buffer_window_ms: int = 1000):
        self.symbols = symbols
        self.buffer_window_ms = buffer_window_ms
        self.buffers = {sym: [] for sym in symbols}
        self.watermark = None

    def process(self, message):
        """
        Buffer message and emit in order when watermark advances.

        Watermark = minimum timestamp across all symbols.
        """
        symbol = message.symbol
        self.buffers[symbol].append(message)

        # Update watermark (minimum timestamp across all buffers)
        min_timestamps = [
            buf[0].exchange_timestamp
            for buf in self.buffers.values()
            if buf
        ]

        if len(min_timestamps) < len(self.symbols):
            # Not all symbols have data yet, wait
            return []

        self.watermark = min(min_timestamps)

        # Emit all messages <= watermark
        emitted = []
        for buf in self.buffers.values():
            while buf and buf[0].exchange_timestamp <= self.watermark:
                emitted.append(buf.pop(0))

        # Sort by exchange timestamp
        emitted.sort(key=lambda m: m.exchange_timestamp)

        return emitted
```

**Guarantee**: Messages emitted in exchange timestamp order across all symbols

**Trade-off**:
- Latency: Adds buffer window delay (1000ms = 1 second)
- Memory: Buffer size = symbols × messages/sec × window_seconds

**When to Use**:
- Cross-symbol arbitrage strategies (need synchronized view)
- Compliance reporting (need reproducible ordering)

**When NOT to Use**:
- Single-symbol strategies (no cross-symbol ordering needed)
- Latency-sensitive applications (buffering adds delay)

---

## Late-Arriving Data

### Problem: Exchange Corrections

**Scenario**: Exchange sends correction 5 minutes after original tick

```
10:00:00.123 → Trade at $150.23 (SeqNum: 1000, Message ID: ASX.BHP.1000)
10:05:12.456 → Correction: Trade at $150.21 (SeqNum: 5000, CorrectedSeqNum: 1000)
```

### Handling Strategy

**Kafka**:
- Correction message appears at end of topic (SeqNum: 5000)
- No modification of original message (Kafka is append-only)

**Iceberg**:
- Correction message triggers update (upsert by `message_id`)
- Original row updated with corrected price

```python
# Iceberg merge-on-read with corrections
def process_correction(correction: dict):
    """
    Apply correction to Iceberg table.

    Args:
        correction: Correction message with corrected_seq_num
    """
    from pyiceberg.catalog import load_catalog

    catalog = load_catalog('default')
    table = catalog.load_table('market_data.ticks')

    # Original message ID
    original_message_id = f"{correction['exchange']}.{correction['symbol']}.{correction['corrected_seq_num']}"

    # Update original row
    table.update(
        set={
            'price': correction['price'],
            'correction_applied': True,
            'correction_seq_num': correction['exchange_sequence_number']
        },
        where=f"message_id = '{original_message_id}'"
    )
```

**Query Behavior**:
```python
# Query without time-travel → sees corrected data
df = table.scan().to_pandas()
# BHP @ 10:00:00 → $150.21 (corrected price)

# Query as of before correction → sees original data
df = table.scan(as_of_timestamp='2026-01-09 10:03:00').to_pandas()
# BHP @ 10:00:00 → $150.23 (original price)
```

**Guarantee**:
- Current queries see corrected data
- Time-travel queries see data as it was at that point in time

---

## Consistency Testing

### Test 1: End-to-End Consistency

**Objective**: Verify data written to Kafka eventually appears in Iceberg

```python
import time

def test_end_to_end_consistency():
    """
    Write to Kafka, wait, query Iceberg.

    Verify message appears within staleness bound (60 seconds @ p99).
    """
    # Write tick to Kafka
    tick = {
        'message_id': 'TEST.BHP.999999',
        'symbol': 'BHP',
        'price': 150.23,
        'exchange_timestamp': datetime.utcnow(),
        ...
    }
    kafka_producer.produce('market.ticks.asx', tick)
    kafka_producer.flush()

    # Wait for write to propagate to Iceberg
    time.sleep(70)  # p99 staleness + buffer

    # Query Iceberg
    result = iceberg_table.scan(
        row_filter="message_id = 'TEST.BHP.999999'"
    ).to_pandas()

    # Verify tick exists
    assert len(result) == 1, "Tick not found in Iceberg after 70 seconds"
    assert result['price'][0] == 150.23, "Price mismatch"
```

### Test 2: Hybrid Query Consistency

**Objective**: Verify hybrid queries have no gaps

```python
def test_hybrid_query_no_gaps():
    """
    Query hybrid mode (Kafka + Iceberg) and verify no gaps.
    """
    # Known sequence: [1000, 1001, 1002, ..., 1100]
    expected_sequences = list(range(1000, 1101))

    # Query hybrid (spans Iceberg + Kafka)
    result = hybrid_engine.query(
        symbol='BHP',
        start_time=datetime.utcnow() - timedelta(hours=1),
        end_time=datetime.utcnow()
    )

    # Extract sequence numbers
    actual_sequences = sorted([r['exchange_sequence_number'] for r in result])

    # Verify no gaps
    assert actual_sequences == expected_sequences, "Gaps detected in hybrid query"
```

### Test 3: Snapshot Isolation

**Objective**: Verify concurrent reads see consistent snapshots

```python
def test_snapshot_isolation():
    """
    Verify concurrent reads are isolated from concurrent writes.
    """
    # Reader 1 starts query
    snapshot_id_1 = iceberg_table.current_snapshot().snapshot_id
    df1 = iceberg_table.scan(snapshot_id=snapshot_id_1).to_pandas()
    row_count_1 = len(df1)

    # Writer writes new data
    iceberg_writer.write_batch([tick1, tick2])
    iceberg_writer.commit()

    # Reader 1 queries again with same snapshot
    df2 = iceberg_table.scan(snapshot_id=snapshot_id_1).to_pandas()
    row_count_2 = len(df2)

    # Verify Reader 1 still sees same data
    assert row_count_1 == row_count_2, "Snapshot isolation violated"

    # Reader 2 sees new snapshot
    df3 = iceberg_table.scan().to_pandas()
    row_count_3 = len(df3)

    assert row_count_3 == row_count_1 + 2, "New reader should see new data"
```

### Test 4: Late Arriving Data

**Objective**: Verify corrections update original rows

```python
def test_late_arriving_correction():
    """
    Verify late correction updates original tick.
    """
    # Write original tick
    original_tick = {
        'message_id': 'ASX.BHP.1000',
        'price': 150.23,
        ...
    }
    iceberg_writer.write_batch([original_tick])
    iceberg_writer.commit()

    # Query original price
    df1 = iceberg_table.scan(row_filter="message_id = 'ASX.BHP.1000'").to_pandas()
    assert df1['price'][0] == 150.23

    # Write correction
    correction = {
        'message_id': 'ASX.BHP.5000',  # New sequence number
        'corrected_seq_num': 1000,
        'price': 150.21,  # Corrected price
        ...
    }
    process_correction(correction)

    # Query corrected price
    df2 = iceberg_table.scan(row_filter="message_id = 'ASX.BHP.1000'").to_pandas()
    assert df2['price'][0] == 150.21, "Correction not applied"
```

---

## Consistency SLOs

### Service Level Objectives

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Kafka → Iceberg staleness (p99)** | < 60 seconds | Watermark lag metric |
| **Hybrid query gap rate** | 0% | Test queries for sequence gaps |
| **Snapshot isolation violations** | 0 | Concurrent read/write tests |
| **Late correction application** | < 5 minutes | Time from correction to visible |

### Monitoring Metrics

```
# Staleness tracking
kafka_to_iceberg_staleness_seconds{percentile="50|99"}

# Consistency violations
consistency_violations_total{type="gap|duplicate|ordering"}

# Watermark lag (hybrid queries)
hybrid_query_watermark_lag_seconds
```

### Alerts

```yaml
# Staleness exceeds bound
- alert: IcebergStalenessHigh
  expr: kafka_to_iceberg_staleness_seconds{percentile="99"} > 120
  for: 5m
  severity: warning
  summary: "Iceberg staleness p99 > 120 seconds (target: 60s)"

# Consistency violations detected
- alert: ConsistencyViolation
  expr: rate(consistency_violations_total[5m]) > 0
  severity: critical
  summary: "Data consistency violations detected"
```

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Idempotency over exactly-once
- [Correctness Trade-offs](./CORRECTNESS_TRADEOFFS.md) - Delivery guarantees
- [Query Architecture](./QUERY_ARCHITECTURE.md) - Hybrid query mode
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md) - Per-symbol ordering
