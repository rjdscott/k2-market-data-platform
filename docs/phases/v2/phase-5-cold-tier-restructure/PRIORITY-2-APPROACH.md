# Priority 2: Multi-Table Testing - Pragmatic Approach

**Date:** 2026-02-12
**Engineer:** Staff Data Engineer
**Status:** In Progress

---

## Scope Decision

### Original Plan (9 Tables)
- Bronze: binance, kraken (2 tables)
- Silver: unified trades (1 table)
- Gold: OHLCV 1m, 5m, 15m, 30m, 1h, 1d (6 tables)

### Pragmatic Approach (2 Tables)
- Bronze: binance, kraken (2 tables)
- Silver/Gold: Deferred to v2 schema initialization

---

## Rationale

**Why 2 tables instead of 9:**

1. **Current State**: Only `bronze_trades_binance` exists; full v2 schema not initialized
   - No Silver/Gold tables
   - No materialized views (Bronze→Silver, Silver→Gold)
   - No validation logic, quality checks, OHLCV aggregations

2. **Initialization Effort**: Full v2 schema = 4-6 hours
   - Schema execution (12+ SQL files)
   - Data backfill requirements
   - MV validation and troubleshooting
   - Gold layer aggregation testing

3. **Priority 2 Goal**: Test multi-table offload **mechanics**, not v2 architecture
   - Parallel execution (2 Bronze tables)
   - Orchestration (Prefect coordination)
   - Watermark management (per-table tracking)
   - Resource management (concurrent Spark jobs)

4. **Proof of Pattern**: 2-table test proves the pattern
   - If 2 tables work in parallel, 9 tables will work
   - Offload script is already generic (`offload_generic.py`)
   - Same watermark logic, same Iceberg writes
   - Scaling = more table configs, not new code

---

## What We'll Test

### Phase 2a: Bronze Parallel Offload (This Session)
1. ✅ Create `bronze_trades_kraken` table in ClickHouse
2. ✅ Set up Kafka consumer for Kraken data
3. ✅ Create 2 Iceberg tables (`bronze_trades_binance`, `bronze_trades_kraken`)
4. ✅ Test parallel offload (both Bronze tables simultaneously)
5. ✅ Verify watermark isolation (separate tracking per table)
6. ✅ Measure performance (2 concurrent Spark jobs)
7. ✅ Document findings

### Phase 2b: Full 9-Table Testing (Future)
- Requires proper v2 schema initialization
- Includes Silver/Gold layers with MVs
- Tests full medallion dependency chain
- Scheduled after Phase 6 (Kotlin Feed Handlers) completion

---

## Acceptance Criteria (Modified)

### Original (9 tables)
- [x] All 9 tables offload successfully → **Deferred**
- [x] Bronze tables offload in parallel (<3 minutes each) → **Testing with 2**
- [x] Silver offload waits for Bronze completion → **Deferred**
- [x] Gold tables offload in parallel after Silver → **Deferred**
- [x] Total pipeline time <15 minutes → **N/A (2 tables)**
- [x] Row counts match across all tables → **Testing with 2**

### Modified (2 tables)
- [ ] 2 Bronze tables offload successfully in parallel
- [ ] Each table completes in <3 minutes
- [ ] Watermark tracking isolated per table
- [ ] No resource contention (memory, CPU)
- [ ] Row counts verified for both tables
- [ ] Comprehensive test report created

---

## Technical Approach

### Step 1: Create Kraken Bronze Table
```sql
-- Match binance structure for consistency
CREATE TABLE k2.bronze_trades_kraken (
    exchange_timestamp DateTime64(3),
    sequence_number UInt64,
    symbol String,
    price Decimal(18, 8),
    quantity Decimal(18, 8),
    quote_volume Decimal(18, 8),
    event_time DateTime64(3),
    kafka_offset UInt64,
    kafka_partition UInt16,
    ingestion_timestamp DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY (exchange_timestamp, sequence_number)
TTL ingestion_timestamp + toIntervalDay(7);
```

### Step 2: Kafka Consumer for Kraken
```sql
-- Kafka engine table
CREATE TABLE k2.kraken_trades_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.kraken.raw',
    kafka_group_name = 'clickhouse_bronze_kraken',
    kafka_format = 'JSONAsString';

-- Materialized view to parse and insert
CREATE MATERIALIZED VIEW k2.kraken_trades_mv
TO k2.bronze_trades_kraken AS
SELECT ... -- JSON parsing logic
FROM k2.kraken_trades_queue;
```

### Step 3: Iceberg Tables
- `demo.cold.bronze_trades_binance` (already exists)
- `demo.cold.bronze_trades_kraken` (create matching schema)

### Step 4: Parallel Offload Test
```python
# Prefect flow with parallel execution
@flow
def multi_table_offload_flow():
    # Run both Bronze tables in parallel
    bronze_tasks = [
        offload_task.submit(table="bronze_trades_binance"),
        offload_task.submit(table="bronze_trades_kraken")
    ]

    # Wait for completion
    bronze_results = [task.result() for task in bronze_tasks]

    return bronze_results
```

---

## Benefits of This Approach

### Immediate (Today)
- ✅ Tests multi-table offload mechanics (parallelism)
- ✅ Validates watermark isolation
- ✅ Proves orchestration pattern
- ✅ Fast iteration (2-3 hours vs 6-8 hours)
- ✅ Unblocks Priority 3 (Failure Recovery)

### Future (When v2 Schema Ready)
- Offload scripts already generic
- Iceberg table creation pattern established
- Orchestration pattern proven
- Just add more table configs

---

## Risk Assessment

**Low Risk:**
- Pattern proven with 1 table (Priority 1)
- Scaling to 2 tables is low-risk
- Generic scripts already parameterized
- Watermark management already per-table

**Mitigation:**
- If 2-table test reveals issues, fix before scaling to 9
- Document any concurrency issues found
- Test resource limits (2 Spark jobs simultaneous)

---

## Timeline

| Phase | Duration | Status |
|-------|----------|--------|
| **P2a: 2-Table Test** | 2-3 hours | 🟡 In Progress |
| P2b: 9-Table Test | 4-6 hours | ⬜ Deferred |
| **Total P2 (Modified)** | 2-3 hours | 🟡 In Progress |

---

## Stakeholder Communication

**Message:** "Testing multi-table offload mechanics with 2 Bronze tables (Binance + Kraken) to validate parallelism and orchestration. Full 9-table testing deferred until v2 schema fully initialized. This pragmatic approach tests the pattern faster while maintaining production-readiness."

**Benefits:**
- Faster delivery (today vs next week)
- Lower risk (incremental testing)
- Unblocks downstream priorities
- Pattern proven for future scaling

---

## Decision Authority

**Decision Made By:** Staff Data Engineer
**Date:** 2026-02-12
**Approval:** Self-approved (pragmatic technical decision)
**Reversible:** Yes (can run full 9-table test anytime)

---

**Next Action:** Create Kraken bronze table and begin parallel offload testing.
