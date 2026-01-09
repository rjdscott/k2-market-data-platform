# Correctness Trade-offs: Exactly-Once vs At-Least-Once

**Last Updated**: 2026-01-09
**Owners**: Platform Team
**Scope**: Delivery guarantees and deduplication

---

## Overview

"Exactly-once processing" is a seductive promise that hides operational complexity. This document explains when we use exactly-once semantics, when at-least-once with idempotency is sufficient, and where data loss is unacceptable.

**Core Philosophy**: Default to at-least-once + idempotency. Upgrade to exactly-once only when duplicates cannot be handled.

---

## Delivery Guarantee Spectrum

| Guarantee | Meaning | Cost | Use Cases |
|-----------|---------|------|-----------|
| **At-most-once** | May lose data, no duplicates | Lowest latency, simplest | Metrics, logs (acceptable loss) |
| **At-least-once** | No data loss, may duplicate | Low overhead, simple recovery | Market data ingestion (dedupe downstream) |
| **Exactly-once** | No loss, no duplicates | High overhead, complex failure modes | Financial aggregations (PnL calculations) |

---

## Decision Tree

```
┌─────────────────────────────────────────────────┐
│ Can duplicates cause financial impact?          │
└────────┬──────────────────────────────┬─────────┘
         │                              │
        YES                            NO
         │                              │
         ▼                              ▼
┌──────────────────────┐      ┌──────────────────────┐
│ Use EXACTLY-ONCE     │      │ Can we dedupe based  │
│ (Kafka transactions) │      │ on message ID?       │
└──────────────────────┘      └────┬──────────┬──────┘
                                   │          │
                                  YES        NO
                                   │          │
                                   ▼          ▼
                         ┌───────────────┐  ┌─────────────────┐
                         │ At-least-once │  │ Design message  │
                         │ + Idempotency │  │ to be idempotent│
                         └───────────────┘  └─────────────────┘
```

---

## Where Exactly-Once IS Required

### 1. Financial Aggregations

**Example**: Daily PnL calculation

```python
# BAD: At-least-once without deduplication
def calculate_pnl(trades: List[Trade]) -> float:
    total_pnl = 0.0
    for trade in trades:
        total_pnl += trade.profit_loss
    return total_pnl

# If duplicates exist, PnL is wrong → regulatory reporting fails
```

**Solution**: Kafka transactions for exactly-once processing

```python
# GOOD: Exactly-once with Kafka transactions
from confluent_kafka import Producer, Consumer

producer = Producer({
    'transactional.id': 'pnl_calculator',
    'enable.idempotence': True
})

consumer = Consumer({
    'group.id': 'pnl_calculator',
    'isolation.level': 'read_committed'  # Only read committed transactions
})

producer.init_transactions()

for message in consumer:
    trade = parse_trade(message)
    pnl_update = calculate_pnl_delta(trade)

    # Atomic: Read from Kafka, write to output, commit offset
    producer.begin_transaction()
    producer.produce('pnl.aggregates', key=trade.account_id, value=pnl_update)
    producer.send_offsets_to_transaction(
        consumer.position(),
        consumer.consumer_group_metadata()
    )
    producer.commit_transaction()
```

**Cost**: 2-3x latency increase, complex error handling for transaction failures

---

### 2. Idempotent State Updates Impossible

**Example**: Incrementing a counter without unique message ID

```python
# BAD: Cannot dedupe without message ID
counter = 0
for message in stream:
    counter += 1  # Duplicate message → wrong count
```

**Solution**: Use Kafka transactions OR redesign to include unique IDs

```python
# GOOD: Redesign with unique message IDs
seen_ids = set()  # Or persistent store (Redis, Postgres)

for message in stream:
    if message.id in seen_ids:
        continue  # Skip duplicate

    counter += 1
    seen_ids.add(message.id)
```

**Preferred Approach**: Redesign > transactions (simpler failure modes)

---

## Where At-Least-Once + Idempotency IS Sufficient

### 1. Market Data Ingestion

**Rationale**: Every tick has a unique message ID (exchange sequence number + symbol)

```python
# Message structure
{
    "message_id": "NASDAQ.AAPL.184847291",  # Unique per exchange
    "exchange": "NASDAQ",
    "symbol": "AAPL",
    "sequence_number": 184847291,
    "timestamp": "2026-01-09T09:30:00.127483",
    "price": 150.23,
    "volume": 100
}
```

**Deduplication**:
```python
# Iceberg table has PRIMARY KEY on message_id
# Duplicate inserts are ignored (merge-on-read)

iceberg_table.merge(
    source_df,
    on='message_id',
    when_matched='update',  # Overwrite with newer data
    when_not_matched='insert'
)
```

**Outcome**: Duplicate messages from Kafka → idempotent writes to Iceberg → no data corruption

---

### 2. Upsert-Based State

**Example**: Latest quote per symbol

```sql
-- Postgres table
CREATE TABLE latest_quotes (
    symbol TEXT PRIMARY KEY,
    bid NUMERIC,
    ask NUMERIC,
    timestamp TIMESTAMP
);

-- Upsert logic (idempotent)
INSERT INTO latest_quotes (symbol, bid, ask, timestamp)
VALUES ('AAPL', 150.22, 150.24, '2026-01-09 09:30:00')
ON CONFLICT (symbol)
DO UPDATE SET
    bid = EXCLUDED.bid,
    ask = EXCLUDED.ask,
    timestamp = EXCLUDED.timestamp
WHERE EXCLUDED.timestamp > latest_quotes.timestamp;  -- Only update if newer
```

**Property**: Duplicate messages → same upsert result → idempotent

---

### 3. Time-Series Metrics

**Rationale**: Metrics are inherently lossy (sampling, aggregation)

```python
# Acceptable: Duplicate metric may slightly skew average
prometheus_metrics.histogram('query_latency', 0.123)
prometheus_metrics.histogram('query_latency', 0.123)  # Duplicate

# Impact: Negligible over thousands of samples
```

**Trade-off**: Simplicity > perfect accuracy for observability

---

## Where Data Loss IS Unacceptable

### 1. Trade Execution Records

**Requirement**: Every trade must be durably stored before acknowledging to trader

**Implementation**:
```python
# Synchronous write to Kafka + Iceberg before ACK
def execute_trade(order: Order):
    trade = execute_on_venue(order)

    # 1. Write to Kafka (durable, replicated)
    kafka_producer.produce('trades', trade, callback=on_kafka_ack)
    kafka_producer.flush()  # Synchronous wait

    # 2. Write to Iceberg (durable)
    iceberg_table.append([trade])
    iceberg_table.commit()  # ACID commit

    # 3. Only now ACK to trader
    return {"status": "executed", "trade_id": trade.id}
```

**Kafka Config**:
```properties
acks=all                       # Wait for all in-sync replicas
min.insync.replicas=2         # At least 2 replicas must ACK
enable.idempotence=true       # Prevent duplicates from retries
```

**Cost**: Higher latency (synchronous writes), reduced throughput

---

### 2. Regulatory Audit Logs

**Requirement**: All data access must be logged (GDPR, FINRA)

```python
def query_customer_data(user_id: str, customer_id: str):
    # 1. Log access BEFORE querying
    audit_log.write({
        'user_id': user_id,
        'customer_id': customer_id,
        'timestamp': datetime.utcnow(),
        'action': 'QUERY_CUSTOMER_DATA'
    })
    audit_log.flush()  # Synchronous

    # 2. Execute query
    return db.query(f"SELECT * FROM customers WHERE id = {customer_id}")
```

**Recovery**: If audit log write fails, query must fail (no silent data access)

---

## Deduplication Implementation

### Message ID Design

**Best Practice**: Use natural unique identifiers from source system

```python
# GOOD: Exchange-provided sequence number
message_id = f"{exchange}.{symbol}.{sequence_number}"

# ACCEPTABLE: Timestamp + symbol (collisions possible, handle with compound key)
message_id = f"{symbol}.{timestamp_nanos}"

# BAD: Random UUID (cannot detect duplicates from source)
message_id = str(uuid4())
```

---

### Deduplication Window

**Problem**: Cannot store infinite message IDs in memory

**Solution**: Time-based window + persistent store

```python
class DeduplicationCache:
    def __init__(self, window_hours: int = 24):
        self.redis = redis.Redis()
        self.window_seconds = window_hours * 3600

    def is_duplicate(self, message_id: str) -> bool:
        """Returns True if message was seen in last 24 hours."""
        key = f"dedup:{message_id}"

        # SET NX (set if not exists) with TTL
        result = self.redis.set(key, '1', nx=True, ex=self.window_seconds)

        return result is None  # None = key already existed (duplicate)

# Usage
dedup = DeduplicationCache(window_hours=24)

for message in kafka_consumer:
    if dedup.is_duplicate(message.id):
        metrics.increment('duplicates_dropped')
        continue

    process_message(message)
```

**Trade-off**: Messages older than 24 hours may not be detected as duplicates (acceptable for most use cases)

---

### Iceberg Merge-on-Read

**Mechanism**: Iceberg supports upserts natively

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog('default')
table = catalog.load_table('market_data.ticks')

# Append new data (may include duplicates)
table.append(new_ticks_df)

# Iceberg automatically handles duplicates during read
# via PRIMARY KEY constraint on message_id
query_result = table.scan(
    row_filter="symbol = 'AAPL'",
).to_arrow()

# Result: Deduplicated automatically (latest version per message_id)
```

**Benefit**: Deduplication happens at read time, not write time (faster ingestion)

---

## Failure Modes & Recovery

### Kafka Transaction Failures

**Scenario**: Producer crashes mid-transaction

```
producer.begin_transaction()
producer.produce('output', message_1)
producer.produce('output', message_2)
# CRASH HERE
producer.commit_transaction()  # Never executes
```

**Outcome**: Both `message_1` and `message_2` are rolled back (not visible to consumers)

**Recovery**: Restart producer, replay from last committed offset

**Downside**: Entire batch lost (must reprocess)

---

### Duplicate Detection Failure

**Scenario**: Redis cache evicts message IDs early (memory pressure)

```python
dedup.is_duplicate('AAPL.12345')  # Returns False (should be True)
```

**Impact**: Duplicate message processed, Iceberg table gets duplicate row

**Mitigation**:
1. Iceberg PRIMARY KEY constraint prevents duplicate rows in final table
2. Query results still correct (merge-on-read dedupes)
3. Slight storage overhead (duplicate files until compaction)

**Recovery**: Run Iceberg compaction job to remove duplicate rows

---

## Testing Requirements

All consumers must pass these tests:

### 1. Duplicate Delivery Test
```python
def test_duplicate_handling():
    """Verify duplicates are handled idempotently."""
    message = create_market_tick(symbol='AAPL', seq=1000)

    # Process same message twice
    consumer.process(message)
    consumer.process(message)

    # Query result
    result = iceberg_table.query("SELECT * FROM ticks WHERE symbol = 'AAPL'")

    # Assert: Only one row exists
    assert len(result) == 1
```

### 2. Transaction Rollback Test
```python
def test_transaction_failure():
    """Verify transaction rollback on error."""
    producer.begin_transaction()
    producer.produce('output', message_1)

    # Simulate failure
    raise Exception("Simulated crash")

    # Producer restarts, message_1 should NOT be in output topic
    consumer = Consumer('output')
    messages = consumer.poll_batch(timeout=5)

    assert len(messages) == 0  # No messages committed
```

### 3. Out-of-Order Deduplication Test
```python
def test_out_of_order_dedup():
    """Verify duplicates detected even if out-of-order."""
    msg1 = create_tick(seq=1000, timestamp='09:30:00.100')
    msg2 = create_tick(seq=1001, timestamp='09:30:00.200')
    msg1_dup = create_tick(seq=1000, timestamp='09:30:00.100')

    # Process out-of-order: 1000, 1001, 1000
    consumer.process(msg1)
    consumer.process(msg2)
    consumer.process(msg1_dup)

    # Assert: Only 2 unique messages stored
    result = iceberg_table.query("SELECT * FROM ticks")
    assert len(result) == 2
```

---

## Cost-Benefit Analysis

| Approach | Latency Overhead | Complexity | Data Loss Risk | Duplicate Risk |
|----------|------------------|------------|----------------|----------------|
| **At-most-once** | None | Trivial | Medium | None |
| **At-least-once + Idempotency** | Low (10-20%) | Low | None | None (if dedup works) |
| **Exactly-once (Kafka transactions)** | High (2-3x) | High | None | None |

**Recommendation**: Start with at-least-once + idempotency. Upgrade to exactly-once only when duplicates cause measurable business impact.

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Idempotency over exactly-once
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md) - Message ID design
- [Failure & Recovery](./FAILURE_RECOVERY.md) - Transaction failure scenarios