# Latency Budgets & Backpressure Design

**Last Updated**: 2026-01-09
**Owners**: Platform Team
**Scope**: End-to-end performance characteristics

---

## Overview

Every microsecond of latency costs trading firms money. But "make it fast" is not a design. This document defines our latency budgets per pipeline stage, what degrades first under load, and how we communicate backpressure upstream.

**Philosophy**: Explicit degradation beats unpredictable failure. We design systems that slow down gracefully rather than fall over.

---

## End-to-End Latency Budget

**Total Budget**: Exchange → Query API = **500ms @ p99**

### Breakdown by Stage

| Stage | Component | p50 | p99 | p99.9 | Degrades To | Alert Threshold |
|-------|-----------|-----|-----|-------|-------------|-----------------|
| **1. Ingestion** | Feed handler → Kafka producer | 2ms | 10ms | 25ms | Drop non-critical symbols | p99 > 20ms |
| **2. Kafka** | Producer → Consumer poll | 5ms | 20ms | 50ms | Increase batch size (higher latency) | Lag > 100K messages |
| **3. Processing** | Consumer → Business logic | 10ms | 50ms | 100ms | Skip enrichment, pass-through | p99 > 80ms |
| **4. Storage** | Iceberg write (buffered) | 50ms | 200ms | 500ms | Spill to local disk, async flush | p99 > 400ms |
| **5. Query** | API request → Response | 100ms | 300ms | 800ms | Reduce query complexity, cache | p99 > 500ms |

**Total**: 167ms @ p50, 580ms @ p99, 1475ms @ p99.9

**Trade-off**: We intentionally pad budgets for headroom. Real-world p99 is typically 300-400ms.

---

## Latency vs Throughput Trade-offs

### Kafka Configuration Tuning

**Low Latency Mode** (Default):
```properties
# Producer config
linger.ms=5                    # Wait max 5ms before sending batch
batch.size=16384               # Small batches (16KB)
compression.type=lz4           # Fast compression
acks=1                         # Leader acknowledgment only

# Expected: p99 < 20ms, throughput ~50K msg/sec/partition
```

**High Throughput Mode** (Activated under load):
```properties
# Producer config
linger.ms=100                  # Wait 100ms to fill batch
batch.size=1048576             # Large batches (1MB)
compression.type=zstd          # Better compression ratio
acks=1                         # Same durability

# Expected: p99 ~150ms, throughput ~500K msg/sec/partition
```

**Automatic Switch Logic**:
```python
def adjust_producer_config(current_lag: int, msg_rate: int):
    """Dynamically tune producer based on system state."""
    if current_lag > 500_000:
        # Heavy lag: prioritize throughput to catch up
        return HighThroughputConfig
    elif msg_rate > 100_000:
        # High message rate: batch aggressively
        return HighThroughputConfig
    else:
        # Normal operation: prioritize latency
        return LowLatencyConfig
```

---

## Backpressure Cascade

### What Degrades First?

Under load, components degrade in this order:

#### Level 1: **Soft Degradation** (Alert, No User Impact)
- p99 latency increases (50ms → 100ms)
- Metrics show elevated latency, on-call receives alert
- System continues processing all messages

**Trigger**: Consumer lag > 100K messages OR p99 latency > budget

**Response**: Autoscale consumers (add instances to consumer group)

---

#### Level 2: **Graceful Degradation** (Reduced Fidelity)
- Drop non-critical market data symbols (only process top 500 liquid symbols)
- Skip optional enrichment (e.g., company metadata lookup)
- Disable audit logging (write to local buffer, flush later)

**Trigger**: Consumer lag > 1M messages OR OOM warning (heap > 80%)

**Response**:
```python
def apply_degraded_mode():
    """Reduce processing overhead under extreme load."""
    # 1. Filter symbols
    critical_symbols = ['BHP', 'CBA', 'CSL', ...]  # Top 500
    if message.symbol not in critical_symbols:
        metrics.increment('dropped_low_priority_symbol')
        return  # Drop message

    # 2. Skip enrichment
    skip_enrichment = True

    # 3. Batch writes larger
    iceberg_writer.batch_size = 10_000  # From 1,000
```

**Alert**: Page on-call, investigate root cause (slow consumer? Kafka broker issue?)

---

#### Level 3: **Spill to Disk** (Durability Over Latency)
- Stop writing to Iceberg (slow S3 writes)
- Buffer messages to local SSD (NVMe)
- Async background process flushes to Iceberg when load decreases

**Trigger**: Iceberg write latency p99 > 1 second OR S3 throttling errors

**Response**:
```python
class SpillToDiskWriter:
    def __init__(self):
        self.spill_path = '/mnt/nvme/spill/'
        self.spill_active = False

    def write(self, messages: list):
        if self.should_spill():
            # Write to local disk (fast)
            with open(f'{self.spill_path}/{uuid4()}.parquet', 'wb') as f:
                pq.write_table(pa.Table.from_pylist(messages), f)
            metrics.increment('spilled_to_disk', len(messages))
        else:
            # Normal Iceberg write
            iceberg_table.append(messages)

    def should_spill(self) -> bool:
        return (
            iceberg_write_p99_latency > 1.0 or
            s3_error_rate > 0.01
        )
```

**Recovery**: Background job flushes spill files to Iceberg during off-peak hours

**Capacity Planning**: Local SSD must hold 1 hour of peak traffic (~500GB @ 10M msg/sec)

---

#### Level 4: **Circuit Breaker** (Stop Processing)
- Halt Kafka consumption (pause partitions)
- Return 503 Service Unavailable from query API
- Human intervention required

**Trigger**: Disk full (> 95%) OR unrecoverable error rate > 5%

**Response**:
```python
def circuit_breaker():
    """Last resort: stop processing to prevent cascading failure."""
    kafka_consumer.pause_all_partitions()
    log.critical("Circuit breaker OPEN: halted consumption")
    metrics.gauge('circuit_breaker_state', 1)  # 1 = OPEN

    # Notify on-call via PagerDuty
    pagerduty.trigger_incident(
        severity='critical',
        summary='Market data pipeline circuit breaker activated'
    )
```

**Recovery**: Manual restart after fixing root cause (clear disk space, fix S3 credentials, etc.)

---

## Critical Metrics (On-Call Dashboard)

### Red Metrics (Immediate Page)

```
# Consumer lag (per consumer group)
kafka_consumer_lag_messages{group="strategy_alpha"} > 1_000_000

# Iceberg write failures
iceberg_write_errors_total{table="market_data.ticks"} > 10/min

# Query API error rate
query_api_errors_total / query_api_requests_total > 0.05  # 5% error rate

# Circuit breaker state
circuit_breaker_state == 1  # OPEN
```

### Yellow Metrics (Investigate Next Business Day)

```
# Elevated latency
kafka_consumer_lag_seconds > 60

# High memory usage
jvm_memory_used_bytes / jvm_memory_max_bytes > 0.8

# Spill to disk active
spilled_to_disk_total > 0
```

### Green Metrics (Informational)

```
# Message throughput
kafka_messages_consumed_total (rate 5m)

# Storage growth
iceberg_table_size_bytes{table="market_data.ticks"}

# Query cache hit rate
query_cache_hits_total / query_cache_requests_total
```

---

## Degradation Testing

### Load Test Scenarios

All scenarios run in staging environment weekly:

#### 1. **Sustained High Load**
- Generate 10x normal message rate for 1 hour
- Verify: p99 latency stays < 1 second, no message loss
- Expected: Level 1-2 degradation (latency increase, some enrichment skipped)

#### 2. **Kafka Broker Slowdown**
- Artificially delay Kafka broker responses by 500ms
- Verify: Circuit breaker does NOT trigger, system queues messages
- Expected: Level 2 degradation (batch size increases, latency increases)

#### 3. **S3 Throttling**
- Rate-limit S3 PutObject requests to 100/sec (vs normal 1000/sec)
- Verify: Spill-to-disk activates, no data loss
- Expected: Level 3 degradation (messages buffered locally)

#### 4. **Full Disk**
- Fill local disk to 96%
- Verify: Circuit breaker opens, consumption halts, alert fires
- Expected: Level 4 degradation (manual intervention required)

---

## Capacity Planning Triggers

**Autoscaling Rules**:

| Metric | Threshold | Action | Cooldown |
|--------|-----------|--------|----------|
| Consumer lag > 500K | 5 minutes | Add +1 consumer instance | 10 min |
| Consumer lag > 2M | 1 minute | Add +3 consumer instances | 10 min |
| CPU > 70% | 10 minutes | Add +1 instance | 15 min |
| Memory > 85% | 5 minutes | Restart instance (clear heap) | 20 min |
| Query API p99 > 1s | 10 minutes | Add +1 API instance | 10 min |

**Manual Review Triggers**:
- Consumer lag consistently > 1M for 24 hours → Increase partition count
- S3 costs > $10K/month → Enable intelligent tiering
- Query API traffic doubles month-over-month → Evaluate caching layer

---

## Example: Black Swan Event Response

**Scenario**: Market crash → 100x normal message volume (e.g., flash crash)

**Timeline**:
```
T+0:00  - Message rate spikes from 100K/sec → 10M/sec
T+0:05  - Level 1 degradation: p99 latency 50ms → 300ms
T+0:10  - Level 2 degradation: Drop low-priority symbols, skip enrichment
T+0:15  - Autoscaler adds 10 consumer instances (1 → 11)
T+0:20  - Consumer lag peaks at 5M messages
T+0:30  - Level 3 degradation: Spill to disk active (S3 writes too slow)
T+0:45  - Consumer lag decreasing (2M messages)
T+1:00  - Message rate normalizes (200K/sec, 2x usual)
T+1:30  - Level 2 degradation lifted (enrichment re-enabled)
T+2:00  - Level 1 degradation lifted (p99 latency back to 50ms)
T+3:00  - Background job flushes spill files to Iceberg
T+4:00  - System fully recovered, autoscaler removes excess instances
```

**Outcome**: No data loss, 30 minutes of degraded fidelity (non-critical symbols dropped), 3 hours to full recovery

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Degrade gracefully principle
- [Failure & Recovery](./FAILURE_RECOVERY.md) - Operational runbooks
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md) - Ordering semantics