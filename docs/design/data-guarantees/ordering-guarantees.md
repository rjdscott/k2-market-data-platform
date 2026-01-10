# Market Data Ordering & Replay Guarantees

**Last Updated**: 2026-01-09
**Owners**: Platform Team
**Scope**: Ingestion and storage layer

---

## Overview

Market data is fundamentally different from typical event streams. Exchanges provide strong ordering guarantees within a symbol, and our platform must preserve those guarantees end-to-end. This document defines how we maintain correctness from exchange feed handlers to analytical queries.

---

## Per-Symbol Ordering Guarantees

### Exchange Reality

Every major exchange provides:
- **Sequence Numbers**: Monotonically increasing integers per symbol (sometimes per channel)
- **Timestamps**: Exchange-assigned timestamps (NOT wall-clock time from our systems)
- **Ordering Invariant**: For a given symbol, sequence number N+1 is always newer than N

**Example** (ASX ITCH):
```
Symbol: BHP
SeqNum: 184847291, Timestamp: 10:00:00.127483, Type: TRADE, Price: 43.23
SeqNum: 184847292, Timestamp: 10:00:00.127519, Type: QUOTE, Bid: 43.22
SeqNum: 184847293, Timestamp: 10:00:00.127601, Type: TRADE, Price: 43.24
```

If we receive 184847293 before 184847292, we have a bug.

---

### Platform Guarantees

#### 1. **Kafka Topic Partitioning**

**Design Decision**: Partition by `exchange.symbol`

```
Topic: market.ticks.asx
Partitions: 100
Partition Key: f"{exchange}.{symbol}"  # e.g., "ASX.BHP"
```

**Rationale**:
- Kafka guarantees ordering within a partition
- All ticks for BHP go to the same partition → preserved ordering
- Different symbols can be processed in parallel (BHP and CBA on different partitions)

**Partition Count Considerations**:
- ASX has ~2,000 symbols → 100 partitions = ~20 symbols per partition
- At 1000x scale: 500+ partitions for better parallelism
- Partition count must remain static (repartitioning breaks ordering)

#### 2. **Iceberg Table Partitioning**

**Schema**:
```sql
CREATE TABLE market_data.ticks (
    exchange_sequence_number  BIGINT NOT NULL,  -- From exchange
    exchange_timestamp        TIMESTAMP NOT NULL,
    symbol                    STRING NOT NULL,
    exchange                  STRING NOT NULL,
    price                     DECIMAL(18, 6),
    volume                    BIGINT,
    message_id                STRING NOT NULL,   -- {exchange}.{symbol}.{seq}
    ingestion_timestamp       TIMESTAMP NOT NULL, -- Platform timestamp
    PRIMARY KEY (message_id)
) PARTITIONED BY (
    days(exchange_timestamp),  -- Time partitioning for query pruning
    exchange,                  -- Isolate ASX vs Chi-X
    truncate(symbol, 4)        -- BHP → BHP bucket
);
```

**Ordering Guarantee**: Within a partition, we maintain sequence number ordering via:
- Iceberg merge-on-read semantics
- Sort by `(exchange_timestamp, exchange_sequence_number)` during compaction
- Late-arriving data (out-of-order writes) handled by snapshot isolation

**Query Behavior**:
```sql
-- This query returns BHP ticks in exchange order (guaranteed)
SELECT * FROM market_data.ticks
WHERE symbol = 'BHP'
  AND exchange_timestamp BETWEEN '2026-01-09 10:00:00' AND '2026-01-09 16:00:00'
ORDER BY exchange_sequence_number;
```

---

## Sequence Number Tracking

### Gap Detection

Exchanges occasionally have sequence gaps due to:
1. **Network packet loss** (our feed handler missed UDP multicast)
2. **Halts/Pauses** (trading stopped, sequence numbers jump)
3. **Session resets** (sequence numbers restart from 1)

**Detection Logic**:
```python
class SequenceTracker:
    def __init__(self):
        self.last_seen = {}  # {(exchange, symbol): last_sequence_number}
        self.gap_threshold = 10  # Alert if gap > 10

    def check_sequence(self, exchange: str, symbol: str, seq: int):
        key = (exchange, symbol)
        last = self.last_seen.get(key, seq - 1)

        if seq != last + 1:
            gap_size = seq - last - 1
            if gap_size > self.gap_threshold:
                # Critical: Large gap detected, possible data loss
                metrics.increment('sequence_gaps_large', {
                    'exchange': exchange,
                    'symbol': symbol,
                    'gap_size': gap_size
                })
                log.warning(f"Sequence gap: {exchange}.{symbol} jumped from {last} to {seq}")
            elif gap_size < 0:
                # Out-of-order delivery (Kafka partition reassignment?)
                metrics.increment('sequence_out_of_order', {
                    'exchange': exchange,
                    'symbol': symbol
                })
                log.error(f"Out-of-order: {exchange}.{symbol} seq {seq} after {last}")

        self.last_seen[key] = max(seq, last)  # Handle out-of-order
```

**Alert Thresholds**:
- Gap < 10: Log warning, continue processing
- Gap 10-100: Page on-call, initiate recovery request from exchange
- Gap > 100: Halt consumer, trigger manual investigation

---

### Sequence Number Resets

**Scenario**: Exchange sessions reset daily or after halts

**Example**:
```
2026-01-09 16:00:00 → SeqNum: 89472918 (market close)
2026-01-10 10:00:00 → SeqNum: 1        (market open, reset)
```

**Handling**:
```python
def detect_reset(exchange: str, symbol: str, seq: int, timestamp: datetime) -> bool:
    """Returns True if sequence number reset detected."""
    key = (exchange, symbol)
    last_seq = self.last_seen.get(key, 0)
    last_ts = self.last_timestamp.get(key, timestamp)

    # Reset detection heuristics:
    # 1. Sequence dropped by >50% AND
    # 2. Timestamp jumped forward by >1 hour
    if seq < last_seq * 0.5 and (timestamp - last_ts).seconds > 3600:
        log.info(f"Sequence reset detected: {exchange}.{symbol} {last_seq} → {seq}")
        metrics.increment('sequence_resets', {'exchange': exchange, 'symbol': symbol})
        return True
    return False
```

**Operational Response**:
- Reset detected → clear `last_seen` for that symbol
- Do NOT alert (this is expected behavior)
- Audit log the reset for compliance reporting

---

## Replay Semantics

### Cold Start (Historical Backfill)

**Definition**: Replay all data from Iceberg for a time range with no real-time consumption

**Use Case**: Backtest a new strategy against 6 months of ticks

**Implementation**:
```python
from k2.query import ReplayEngine

replay = ReplayEngine(
    table='market_data.ticks',
    symbols=['BHP', 'CBA', 'CSL'],
    start_time='2025-07-01 00:00:00',
    end_time='2026-01-01 00:00:00',
    playback_speed=1000.0  # 1000x faster than real-time
)

for tick in replay.stream():
    # Process tick (same API as Kafka consumer)
    strategy.on_tick(tick)
```

**Guarantees**:
- Ticks delivered in exchange timestamp order per symbol
- Cross-symbol ordering NOT guaranteed (BHP tick at 10:00:00.001 may arrive after CBA tick at 10:00:00.000)
- Playback speed configurable (1.0 = real-time, 1000.0 = 1000x faster)

**Performance**:
- Iceberg file scan: ~500MB/sec from S3
- DuckDB vectorized execution: 10M rows/sec
- Bottleneck: Downstream processing logic, not I/O

---

### Catch-Up (Resume from Lag)

**Definition**: Consumer fell behind real-time, needs to catch up while continuing to receive live data

**Use Case**: Consumer was down for 2 hours, now has 5M message lag

**Implementation**:
```python
from k2.ingestion import CatchUpConsumer

consumer = CatchUpConsumer(
    topic='market.ticks.asx',
    group_id='strategy_alpha',
    catch_up_strategy='HYBRID'  # Read from Iceberg, then switch to Kafka
)

# Automatically detects lag and switches modes
for message in consumer:
    if consumer.mode == 'ICEBERG':
        # Reading from historical storage (fast)
        print(f"Catch-up mode: {consumer.lag_messages} messages behind")
    else:
        # Switched to real-time Kafka
        print("Real-time mode")
```

**Strategy**:
1. Detect lag > 1M messages
2. Fetch consumer's last committed offset
3. Query Iceberg for `WHERE exchange_timestamp >= <offset_timestamp>`
4. Stream from Iceberg at high throughput
5. Once within 10K messages of real-time, switch back to Kafka
6. Seamless handoff (no duplicate processing via message ID deduplication)

**Guarantees**:
- No message loss during handoff
- Possible duplicate delivery during switch (handled by idempotency)
- Ordering preserved within symbol

---

### Rewind (Time-Travel Query)

**Definition**: Query data as of a specific snapshot, ignoring later updates

**Use Case**: "Show me what BHP ticks looked like at 2PM yesterday before the late corrections arrived"

**Implementation**:
```python
from k2.query import QueryEngine

engine = QueryEngine()

# Query as-of specific snapshot
df = engine.query("""
    SELECT * FROM market_data.ticks
    FOR SYSTEM_TIME AS OF '2026-01-08 14:00:00'
    WHERE symbol = 'BHP'
""")
```

**Iceberg Snapshot Mechanism**:
- Every write creates a new snapshot
- Snapshots are immutable (copy-on-write)
- Old snapshots retained for 90 days
- Time-travel queries read from historical snapshots

**Compliance Use Case**:
- Regulatory audit: "Prove you had correct data at market close"
- Rewind to snapshot at 16:00:00 → export to auditors
- No need to maintain separate audit trail

---

## Edge Cases & Known Limitations

### 1. **Cross-Symbol Arbitrage Strategies**

**Problem**: Strategy needs synchronized timestamps across BHP and CBA

**Limitation**: Kafka partitioning means BHP and CBA are on different partitions → no cross-symbol ordering guarantee

**Mitigation**:
- Consumer reads from multiple partitions
- Buffer messages in-memory and sort by exchange timestamp
- Emit only when all symbols have progressed past timestamp T
- Adds latency (buffering delay) but guarantees correctness

**Implementation Sketch**:
```python
class SynchronizedConsumer:
    def __init__(self, symbols: list[str]):
        self.buffers = {sym: [] for sym in symbols}  # Per-symbol buffers
        self.watermark = None  # Minimum timestamp across all symbols

    def process(self, message):
        symbol = message.symbol
        self.buffers[symbol].append(message)

        # Update watermark (minimum timestamp across all symbols)
        self.watermark = min(
            buf[0].exchange_timestamp for buf in self.buffers.values() if buf
        )

        # Emit all messages <= watermark
        for buf in self.buffers.values():
            while buf and buf[0].exchange_timestamp <= self.watermark:
                yield buf.pop(0)
```

---

### 2. **Late-Arriving Data (Exchange Corrections)**

**Problem**: Exchange sends a correction 5 minutes after the original tick

**Example**:
```
10:00:00.123 → Trade at $43.25 (SeqNum: 1000)
10:35:12.456 → Correction: Trade at $43.23 (SeqNum: 5000, CorrectedSeqNum: 1000)
```

**Handling**:
- Iceberg supports updates via merge-on-read
- Correction message overwrites original using `message_id` as key
- Queries always see corrected data (unless using time-travel to before correction)

**Iceberg Merge Logic**:
```sql
MERGE INTO market_data.ticks t
USING corrections c
ON t.message_id = c.corrected_message_id
WHEN MATCHED THEN
    UPDATE SET t.price = c.price, t.correction_applied = TRUE
WHEN NOT MATCHED THEN
    INSERT VALUES (c.*);
```

---

### 3. **Kafka Partition Reassignment**

**Problem**: Consumer rebalance moves partition ownership → brief out-of-order delivery

**Mitigation**:
- Sequence tracker detects out-of-order (seq < last_seen)
- Buffer out-of-order messages temporarily
- Emit when gap is filled or timeout expires (100ms)

**Alert**: If out-of-order messages exceed 0.01% of volume, page on-call

---

## Metrics & Monitoring

**Critical Metrics**:
```
# Sequence number gaps
sequence_gap_total{exchange, symbol, gap_size}

# Out-of-order delivery
out_of_order_total{exchange, symbol}

# Replay lag (catch-up mode)
replay_lag_seconds{consumer_group}

# Iceberg snapshot age
iceberg_snapshot_age_seconds{table}
```

**Dashboards**:
- Per-symbol sequence gap visualization (spike = potential data loss)
- Consumer lag heatmap (which symbols are falling behind)
- Replay throughput (MB/sec from Iceberg during catch-up)

---

## Testing Requirements

All market data consumers must pass:

1. **Ordering Test**: Inject 10K messages with known sequence, verify output order
2. **Gap Handling Test**: Inject sequence gap (1000 → 2000), verify alert triggers
3. **Replay Test**: Replay 1 hour of historical data, compare output to original
4. **Out-of-Order Test**: Deliver messages intentionally out-of-order, verify buffering
5. **Reset Test**: Simulate sequence number reset, verify no false alerts

See `tests/integration/test_ordering_guarantees.py` for reference implementation.

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Core design philosophy
- [Correctness Trade-offs](./CORRECTNESS_TRADEOFFS.md) - Exactly-once vs idempotency
- [Failure & Recovery](./FAILURE_RECOVERY.md) - Operational runbooks