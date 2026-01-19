# Phase 10 Architectural Decisions

**Last Updated**: 2026-01-20
**Phase**: Production Crypto Streaming Platform

---

## Overview

This document captures key architectural decisions made during Phase 10. For decision documentation tiers (Quick/Simplified/Full ADR), see [Documentation Guide](../../CLAUDE.md#decision-documentation-framework-tiered-approach).

---

## Decision #001: Complete ASX/Equity Removal (Clean Slate Approach)

**Date**: 2026-01-18
**Status**: Accepted
**Type**: Tier 3 (Architectural)
**Related Step**: Steps 01-03

### Context

The platform currently supports both equity (ASX) and crypto (Binance) asset classes with mixed schemas (v1 + v2). This creates:
- Code complexity (if/else for asset class handling)
- Schema confusion (v1 legacy, v2 hybrid)
- Testing overhead (need fixtures for both asset classes)
- Documentation burden (explain two systems)

Two approaches considered:
1. **Gradual migration**: Keep ASX code, add "deprecated" markers, remove over time
2. **Clean slate**: Complete removal, start fresh with crypto-only

### Decision

**Adopt clean slate approach**: Remove all ASX/equity code, schemas, and data in Steps 01-03 before building new platform.

**Rationale**:
- Simpler: No legacy code to maintain during refactor
- Clearer: Single asset class, single schema version (v3)
- Faster: No gradual deprecation period
- Better testing: Only test crypto flows
- Easier onboarding: New team members learn one system

### Consequences

**Positive**:
- Clean codebase with no legacy baggage
- Reduced complexity (30-40% less code)
- Faster development (no equity edge cases)
- Clear documentation (crypto-only focus)

**Negative**:
- No rollback to equity support (breaking change)
- Loses ASX historical data (mitigation: archive)
- Re-implementation required if equity needed later

**Neutral**:
- Must document decision for future reference
- Archive old code in git history (not deleted forever)

### Implementation Notes

See IMPLEMENTATION_PLAN.md Steps 01-03 for detailed removal procedure.

---

## Decision #002: Fresh V3 Schema (No Backward Compatibility)

**Date**: 2026-01-18
**Status**: Accepted
**Type**: Tier 3 (Architectural)
**Related Step**: Steps 04-05

### Context

Current platform has two schemas:
- V1: ASX-specific, legacy
- V2: Hybrid (equity + crypto), vendor_data extension

For crypto-only platform, three options:
1. **Extend v2**: Add crypto-specific fields, deprecate equity fields (backward compatible)
2. **Fork v2**: Create v2.1 crypto variant (partial compatibility)
3. **Fresh v3**: New schema optimized for crypto only (no compatibility)

### Decision

**Create fresh v3 schema** with no backward compatibility to v1 or v2.

**Design Principles**:
- Crypto-optimized: base_asset, quote_currency (explicit)
- Remove equity baggage: No trade_conditions, no SELL_SHORT side
- Keep extensibility: vendor_data map for exchange-specific fields
- Simplified enums: Exchange (BINANCE, KRAKEN), Side (BUY, SELL)
- 12 fields (vs 15 in v2): Leaner, focused

### Consequences

**Positive**:
- Clean schema design (no deprecated fields)
- Simpler to understand (crypto-specific)
- Easier to extend (add new exchanges without equity concerns)
- Better performance (fewer fields, smaller messages)

**Negative**:
- No backward compatibility (breaking change)
- Must recreate all Iceberg tables
- Existing v2 data not readable by new platform

**Neutral**:
- Schema Registry compatibility mode: NONE (no checks)
- Future v3 changes must be backward compatible within v3

### Verification

- [ ] V3 schema validates with avro-tools
- [ ] Exchange enum: BINANCE, KRAKEN (no ASX)
- [ ] Side enum: BUY, SELL (no SELL_SHORT)
- [ ] Field count: 12 (removed 3, added 2)

---

## Decision #003: Spark Streaming (Replace Python Consumers)

**Date**: 2026-01-18
**Status**: Accepted
**Type**: Tier 3 (Architectural)
**Related Step**: Step 06

### Context

Current platform uses single-node Python consumers:
- Read from Kafka
- Write to Iceberg
- Manual commit after successful write

Limitations:
- Single-node bottleneck (not horizontally scalable)
- No built-in state management
- Manual checkpoint logic
- Limited fault tolerance

Two options:
1. **Keep Python consumers**: Enhance with state management, add more workers
2. **Adopt Spark Streaming**: Distributed processing, built-in checkpointing

### Decision

**Adopt Spark Structured Streaming** for all Kafka → Iceberg processing.

**Architecture**:
- 1 master + 2 workers (5 CPUs, 10GB RAM total)
- Structured Streaming API (not DStreams)
- Iceberg sink with ACID guarantees
- Checkpoint-based exactly-once semantics

### Consequences

**Positive**:
- Distributed processing (scales horizontally)
- Built-in checkpointing (automatic recovery)
- Fault-tolerant (worker failures handled)
- Exactly-once semantics (guaranteed)
- Better monitoring (Spark Web UI)

**Negative**:
- More complex infrastructure (3 containers vs 1)
- Higher resource usage (10GB RAM vs 2GB)
- Steeper learning curve (Spark API)
- More operational overhead (Spark cluster management)

**Neutral**:
- Performance: Overkill for current volume (10K msg/sec), but room to scale
- Cost: Higher compute, but single-node still (~$0.77/hr for 16-core)

### Implementation Notes

- Use Bitnami Spark Docker images (simplest setup)
- Worker resources: 2 cores, 3GB RAM each
- Checkpoint location: Docker volume (persistent)
- Trigger intervals: Bronze 10s, Silver 30s, Gold 60s

### Verification

- [ ] Spark Web UI accessible: http://localhost:8080
- [ ] 2 workers registered with 4 cores total
- [ ] Test job (Pi approximation) completes successfully

---

## Decision #004: Full Medallion Architecture

**Date**: 2026-01-18
**Status**: Accepted
**Type**: Tier 3 (Architectural)
**Related Step**: Steps 07-12

### Context

Current platform has direct Kafka → Iceberg flow (single table). For better data quality and separation of concerns, consider Medallion architecture:

**Medallion Layers**:
- **Bronze**: Raw data from source (reprocessing buffer)
- **Silver**: Validated and cleaned data (quality checks)
- **Gold**: Business-ready aggregated data (analytics)

Three options:
1. **No medallion**: Keep direct Kafka → Iceberg (current)
2. **Simplified**: Bronze + Gold only (skip Silver validation)
3. **Full medallion**: Bronze → Silver → Gold (3 layers)

### Decision

**Adopt full Medallion architecture** with 3 layers (Bronze/Silver/Gold).

**Layer Design**:
| Layer | Purpose | Retention | Partitioning | Jobs |
|-------|---------|-----------|--------------|------|
| Bronze | Raw Kafka data | 7 days | Daily | 1 (Kafka → Bronze) |
| Silver | Per-exchange validated | 30 days | Daily | 1 (Bronze → Silver + DLQ) |
| Gold | Unified multi-source | Unlimited | Hourly | 1 (Silver → Gold) |

**Silver Separation**: Per-exchange tables (silver_binance_trades, silver_kraken_trades) for:
- Exchange-specific analytics
- Independent validation logic
- Clearer data lineage

### Consequences

**Positive**:
- Clear separation: Raw/Validated/Business layers
- Reprocessing: Bronze allows replaying data if bugs found
- Data quality: Silver enforces validation (DLQ for failures)
- Flexibility: Can query any layer for different use cases
- Debugging: Can trace data flow through layers

**Negative**:
- More complexity: 4 tables vs 1 (bronze + 2 silver + gold)
- More storage: ~3x data duplication (mitigated by retention)
- More jobs: 3 Spark jobs vs 1 Python consumer
- Higher latency: Multi-hop (but still <5 min target)

**Neutral**:
- Medallion is industry best practice for data lakes
- Aligns with modern data engineering patterns
- Easier to explain to stakeholders (layers familiar)

### Implementation Notes

- Bronze: Raw Avro bytes (no deserialization)
- Silver: Full v3 schema, validation UDFs
- Gold: Derived fields (exchange_date, exchange_hour)
- DLQ: Separate table for Silver validation failures

### Verification

- [ ] 4 tables exist: bronze, silver (2), gold
- [ ] Bronze → Silver: Validation works (DLQ captures failures)
- [ ] Silver → Gold: Deduplication works (no duplicate message_ids)
- [ ] Query Gold: Returns unified Binance + Kraken data

---

## Decision #005: Kraken as Second Exchange

**Date**: 2026-01-18
**Status**: Accepted
**Type**: Tier 2 (Design)
**Related Step**: Steps 13-14

### Context

Platform currently supports Binance only. To demonstrate multi-source capability, need to add second exchange. Options:
1. **Coinbase**: Large US exchange, good API docs
2. **Kraken**: Established exchange, similar to Binance
3. **Bybit**: Growing exchange, futures focus

### Decision

**Add Kraken** as second exchange.

**Rationale**:
- Established: Kraken has stable WebSocket API
- Similar to Binance: Trade structure familiar (price, quantity, timestamp)
- Good documentation: WebSocket docs clear and comprehensive
- Complementary: Kraken has some pairs Binance doesn't (fiat pairs)

### Consequences

**Positive**:
- Demonstrates multi-source pattern (extensible to others)
- Proves Gold unification logic works
- Adds volume: More data for testing/demos

**Negative**:
- Lower volume: Kraken ~10x less trades than Binance (but sufficient for demo)
- Different message format: Requires custom parsing logic

**Neutral**:
- Implementation: Mirror Binance patterns (700 lines similar to binance_client.py)
- Testing: Same test suite structure (unit, integration, soak)

### Implementation Notes

- Mirror Binance resilience patterns (6 layers): backoff, rotation, memory, ping, etc.
- Symbol parsing: `"XBT/USD"` → base: `"BTC"`, quote: `"USD"` (Kraken uses XBT)
- Kafka topic: `market.crypto.trades.kraken` (20 partitions, lower volume)
- V3 schema: Same schema as Binance (exchange field differentiates)

### Verification

- [ ] Kraken client streams to Kafka
- [ ] Kraken trades in silver_kraken_trades
- [ ] Kraken trades in gold_crypto_trades (unified with Binance)
- [ ] Both exchanges present: `SELECT exchange, COUNT(*) FROM gold GROUP BY exchange`

---

## Decision #006: Hourly Partitioning for Gold

**Date**: 2026-01-18
**Status**: Accepted
**Type**: Tier 2 (Design)
**Related Step**: Step 09

### Context

Gold table will be queried frequently for recent trades. Partitioning strategy affects query performance. Options:
1. **Daily**: `days(exchange_date)` (simple, fewer partitions)
2. **Hourly**: `(exchange_date, exchange_hour)` (more partitions, better pruning)
3. **By exchange**: `(exchange, exchange_date)` (exchange-specific queries)

### Decision

**Use hourly partitioning**: `PARTITIONED BY (exchange_date, exchange_hour)`

**Rationale**:
- Most queries are "last N hours" (recent data)
- Hourly granularity: 24x better partition pruning than daily
- Query pattern: `WHERE exchange_date = today AND exchange_hour IN (9, 10, 11)` reads 3 partitions (vs 1 full day)

### Consequences

**Positive**:
- Faster queries: 24x partition pruning improvement
- Lower I/O: Reads fewer Parquet files
- Better performance: p99 <500ms target achievable

**Negative**:
- More partitions: 24 per day (vs 1 per day)
- Partition overhead: More metadata in Iceberg catalog

**Neutral**:
- Storage: Same (partitioning doesn't duplicate data)
- Complexity: Minimal (Spark handles automatically)

### Verification

- [ ] Gold table partitioned: `SHOW PARTITIONS gold_crypto_trades`
- [ ] Query plan: `EXPLAIN SELECT * FROM gold WHERE exchange_hour = 10` shows partition pruning
- [ ] Performance: p99 latency <500ms for 1-hour queries

---

## Quick Decisions (Tier 1)

### Decision 2026-01-18: Zstd compression for all tables
**Reason**: Best compression/speed balance for Parquet
**Cost**: Slightly higher CPU than Snappy
**Alternative**: Snappy (rejected: worse compression)

### Decision 2026-01-18: PostgreSQL for Iceberg catalog (not Hive)
**Reason**: Already have Postgres, simpler setup than Hive metastore
**Cost**: Single point of failure (mitigated: backup DB)
**Alternative**: Hive metastore (rejected: more complexity)

### Decision 2026-01-18: 7-day Bronze retention
**Reason**: Balances reprocessing needs with storage costs
**Cost**: ~50GB storage for 7 days
**Alternative**: 30-day (rejected: too much storage for raw data)

### Decision 2026-01-18: 10s/30s/60s trigger intervals (Bronze/Silver/Gold)
**Reason**: Balance latency vs processing overhead
**Cost**: Higher resource usage than 5min batches
**Alternative**: 5min batches (rejected: too high latency)

---

## Decision #007: Early Kraken Integration (Before Spark Setup)

**Date**: 2026-01-18
**Status**: Implemented
**Type**: Tier 1 (Implementation)
**Related Steps**: Step 13-14 (Kraken Integration)

### Context

Original implementation plan ordered: Cleanup → Spark → Kraken. However, Kraken WebSocket client can be developed and validated independently of Spark infrastructure.

### Decision

**Implement Kraken WebSocket integration first** (before Spark cluster setup), reusing production-tested Binance client patterns.

**Rationale**:
- **Parallel work**: Kraken client doesn't depend on Spark
- **Early validation**: Test multi-exchange ingestion before Spark complexity
- **Reusable patterns**: Binance client (879 lines, 60+ tests, 24h soak tested) provides proven template
- **Faster feedback**: Validate Kraken streaming now vs waiting for Spark setup

### Implementation Details

**Files Created** (2026-01-18):
- `src/k2/ingestion/kraken_client.py` (~900 lines)
  - 6-layer resilience: exponential backoff, failover URLs, health checks, connection rotation, memory leak detection, ping-pong heartbeat
  - XBT → BTC normalization for compatibility
  - Deterministic trade ID generation (timestamp + hash)
- `src/k2/common/config.py` - Added `KrakenConfig` (+80 lines)
- `tests/unit/test_kraken_client.py` (30 tests, ~400 lines)
  - Pair parsing (7 tests)
  - Message validation (5 tests)
  - Side mapping (2 tests)
  - Message conversion (8 tests)
  - Client initialization (5 tests)
  - Memory leak detection (4 tests)
- `tests/integration/test_streaming_validation.py` (6 integration tests)
  - Live streaming validation (Binance, Kraken, concurrent)
  - V2 schema compliance
  - XBT normalization
  - Message rate validation
- `scripts/test_kraken_stream.py` - Manual validation script
- `scripts/validate_streaming.py` - Comprehensive validation with reporting
- `docs/STREAMING_VALIDATION.md` - Testing guide

**Key Design Decisions**:
- **Mirror Binance pattern**: Exact same resilience layers for consistency
- **XBT → BTC normalization**: Kraken uses "XBT" but normalize to "BTC" for consistency with other exchanges
- **Array-format parsing**: Kraken uses `[channelID, [[trades]], "trade", "PAIR"]` vs Binance's object format
- **Deterministic trade IDs**: `KRAKEN-{timestamp}-{hash}` since Kraken doesn't provide IDs
- **V2 schema**: Keep using V2 schema with vendor_data extension (defer V3 schema work)

**Validation Results**:
- ✅ All 30 unit tests passing
- ✅ All 6 integration tests passing
- ✅ Manual validation: 10 trades from Kraken in ~30 seconds
- ✅ Schema compliance: 100% v2 validation
- ✅ Concurrent streaming: Both Binance + Kraken working simultaneously

### Consequences

**Positive**:
- Multi-exchange validated before Spark complexity
- Reusable validation scripts for future exchanges
- Early confidence in multi-source architecture
- Documentation complete for streaming validation

**Negative**:
- Deviated from plan order (acceptable for independent work)
- V2 schema still in use (defer V3 schema design)

**Neutral**:
- Need to update Kafka topics config to add `market.crypto.trades.kraken`
- Need to create Spark ingestion job for Kraken topic (Step 10-12)

### Verification

```bash
# Unit tests
uv run pytest tests/unit/test_kraken_client.py -v  # 30 tests passing

# Integration tests
uv run pytest tests/integration/test_streaming_validation.py -k kraken -v

# Manual validation
python scripts/test_kraken_stream.py
python scripts/validate_streaming.py --exchange kraken
```

---

## Decision #008: Explicit Producer Flush Every 10 Trades

**Date**: 2026-01-18
**Status**: Implemented (Critical Fix)
**Type**: Tier 2 (Design/Implementation)
**Related Steps**: Steps 13-14 (Kraken Integration), affects all streaming services

### Context

During Kraken E2E validation, discovered that **messages were not persisting to Kafka** despite services reporting successful streaming (700+ trades for Binance, 50+ for Kraken). Consumers found 0 messages in both `market.crypto.trades.binance` and `market.crypto.trades.kraken` topics.

**Symptoms**:
- Streaming services logs showed: "trades_streamed=700", "errors=0"
- Schema registry showed successful schema registrations (HTTP 200)
- Kafka console consumer: "Processed a total of 0 messages"
- Test producer script: Worked correctly with explicit flush
- Direct producer test from Docker container: Delivered successfully with flush

**Investigation**:
1. Verified Kafka connectivity from containers: ✅ Working
2. Verified producer can list topics: ✅ Working
3. Verified direct produce with callback: ✅ Delivered to partition 28 offset 0
4. Checked producer configuration: `linger.ms=10` set but not sufficient
5. Checked logs for delivery callbacks: None found (buffered, never delivered)

**Root Cause**:
- Producer configuration relied solely on `linger.ms=10ms` for auto-flush
- High-volume streaming (70-100 trades/min for Binance, 10-50 for Kraken) overwhelmed auto-flush
- Messages accumulated in producer buffer but never triggered batch send
- `producer.poll(0)` called after produce, but insufficient to trigger delivery

### Decision

**Add explicit `producer.flush(timeout=1.0)` every 10 trades** in both streaming services.

**Implementation**:
```python
# scripts/binance_stream.py (line 182-186)
# scripts/kraken_stream.py (line 182-186)

trade_count += 1

# Flush producer every 10 trades to ensure messages are written to Kafka
if trade_count % 10 == 0:
    remaining = producer.flush(timeout=1.0)
    if remaining > 0:
        logger.warning("producer_flush_timeout", remaining=remaining)
```

**Rationale**:
- **Guarantees delivery**: Explicit flush ensures messages reach Kafka broker
- **Low overhead**: Flush every 10 trades ≈ every 6-30 seconds (depending on volume)
- **Fast flush**: Flush completes in <1ms (0.0003-0.001 seconds measured)
- **Non-blocking**: 1.0s timeout prevents infinite blocks
- **Monitoring**: Logs remaining messages if flush times out

**Alternatives Considered**:
1. **Increase linger.ms**: Would still rely on implicit flush, not guaranteed
2. **Flush every trade**: Too frequent, potential performance impact
3. **Flush every 100 trades**: Too infrequent for low-volume exchanges like Kraken
4. **Increase batch.size**: Doesn't solve delivery guarantee issue

### Consequences

**Positive**:
- ✅ **Messages now persist**: 50+ MB in Binance topic, 240+ KB in Kraken topic
- ✅ **Zero impact on throughput**: Flush latency <1ms, no performance degradation
- ✅ **All flushes complete**: remaining=0 in all observed flushes
- ✅ **Both services fixed**: Issue affected Binance and Kraken, both now working
- ✅ **E2E verified**: Consumers successfully deserialize messages from both topics

**Negative**:
- Adds 5 lines of code per streaming service
- Slightly more verbose logs (flush messages every 10 trades)

**Neutral**:
- Flush frequency (every 10 trades) is tunable per exchange volume
- May need adjustment for higher-volume exchanges (e.g., flush every 50 for Coinbase)

### Verification Results

**Binance Topic** (`market.crypto.trades.binance`):
```
Total Data: ~50 MB across multiple partitions
Messages: 700+ trades streamed, 0 errors
Top Partitions:
  - Partition 27: 20.8 MB
  - Partition 28: 18.1 MB
  - Partition 22: 11.0 MB
Sample: BNBUSDT @ 942.42 USDT (Avro-serialized)
Flush Status: remaining=0 (all messages delivered)
```

**Kraken Topic** (`market.crypto.trades.kraken`):
```
Total Data: ~240 KB across partitions (lower volume exchange)
Messages: 50+ trades streamed, 0 errors
Top Partitions:
  - Partition 1: 151 KB
  - Another partition: 90 KB
Sample: BTCUSD @ 95,393.40 USD
  - XBT → BTC normalization confirmed
  - vendor_data preserves pair=XBT/USD
Flush Status: remaining=0 (all messages delivered)
```

**Topic Separation Verified**:
- ✅ Binance: `market.crypto.trades.binance` (40 partitions)
- ✅ Kraken: `market.crypto.trades.kraken` (20 partitions)
- ✅ No cross-contamination between topics
- ✅ Correct exchange-based routing

**Performance Metrics**:
```
Flush Latency: 0.0003-0.001 seconds (<1ms)
Flush Frequency: Every 10 trades (~6-30 seconds depending on volume)
Remaining Messages: 0 (100% delivery)
Throughput Impact: None detected
```

### Testing

```bash
# Before fix (messages not persisting):
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.kraken \
  --from-beginning --max-messages 1
# Output: "Processed a total of 0 messages"

# After fix (messages persisting):
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.kraken \
  --from-beginning --max-messages 2
# Output: 2 Avro-encoded BTCUSD trades with XBT/USD vendor_data

# Verification with Avro deserialization:
uv run python scripts/verify_topics.py
# Output:
#   Binance messages: 700+
#   Kraken messages: 50+
#   SUCCESS: Both topics have messages!
```

### Files Modified

**Core Fix**:
- `scripts/binance_stream.py` - Added flush every 10 trades (lines 182-186)
- `scripts/kraken_stream.py` - Added flush every 10 trades (lines 182-186)

**Testing Utilities**:
- `scripts/verify_topics.py` - New E2E verification script for both topics
- `scripts/test_producer.py` - Direct producer test utility

**Commits**:
- Commit 1: `feat(phase-10): complete Kraken E2E testing with fixes and documentation` (3f14bd8)
- Commit 2: `fix: add periodic producer flushing to ensure Kafka message persistence` (f9d725d)

### Related Issues

**Docker Issues Resolved in Same Session**:
1. ✅ Python version mismatch (3.14 → 3.13 in Dockerfile)
2. ✅ Port conflict (9092 → 9095 for Kraken metrics)
3. ✅ Missing Kraken metrics (added 11 metrics to registry)
4. ✅ Structlog parameter conflict (event → event_type)

---

## References

### Related Documentation
- [Medallion Architecture](../../architecture/medallion-architecture.md) (to be created)
- [V3 Schema Design](../../architecture/schema-design-v3.md) (to be created)
- [Spark Operations](../../operations/runbooks/spark-operations.md) (to be created)
- [Kraken Streaming Guide](../../KRAKEN_STREAMING.md) - Complete operational guide

### Related Phases
- [Phase 2 Prep](../phase-2-prep/) - Binance WebSocket integration
- [Phase 5](../phase-5-binance-production-resilience/) - Production resilience patterns

---

**Last Updated**: 2026-01-18
**Maintained By**: Engineering Team

## Decision #014: Explicit NULL Handling in Validation (CRITICAL FIX)

**Date**: 2026-01-19
**Status**: Implemented and Verified
**Type**: Tier 2 (Design/Implementation - Critical Bug Fix)
**Related Step**: Step 11 (Silver Transformation)
**Related Docs**: [SILVER_FIX_SUMMARY.md](./SILVER_FIX_SUMMARY.md)

### Context

During Silver transformation testing, discovered that **Kraken records were silently disappearing** (342 input → 0 output to Silver, 0 to DLQ). Investigation revealed a critical bug in validation logic related to NULL handling.

**Problem**: Spark SQL uses three-valued logic (TRUE, FALSE, NULL). When validation conditions produce NULL (e.g., `NULL > 0` from deserialization failures), naive boolean filters **silently drop records from BOTH streams**:
```python
# BUGGY CODE (Silent Data Loss)
valid_df = df_with_validation.filter(col("is_valid"))  # Excludes NULL
invalid_df = df_with_validation.filter(~col("is_valid"))  # Also excludes NULL!
# Result: Records with NULL is_valid disappear entirely (not in Silver, not in DLQ)
```

### Decision

**Always explicitly handle NULL in validation splits** to prevent silent data loss:

```python
# FIXED CODE (No Silent Data Loss)
# IMPORTANT: Handle NULL is_valid explicitly
valid_df = df_with_validation.filter(col("is_valid") == True).drop("is_valid")  # Only TRUE

# Invalid: explicitly FALSE or NULL (capture silent failures)
invalid_df = df_with_validation.filter((col("is_valid") == False) | col("is_valid").isNull())
```

**Add NULL-specific error reason**:
```python
invalid_df = invalid_df.withColumn(
    "error_reason",
    # Check for NULL is_valid first (indicates deserialization failure or NULL fields)
    when(col("is_valid").isNull(), "deserialization_failed_or_null_fields")
    .when(col("price").isNull(), "price_is_null")
    # ... other validation checks
)
```

### Implementation

**File**: `src/k2/spark/validation/trade_validation.py` (lines 79-92)

**Key Changes**:
1. `filter(col("is_valid") == True)` instead of `filter(col("is_valid"))`
2. `filter((col("is_valid") == False) | col("is_valid").isNull())` for invalid stream
3. Added explicit NULL error reason as first condition in error_reason logic

### Consequences

**Positive**:
- ✅ **No silent data loss**: All records accounted for (Silver or DLQ)
- ✅ **Full observability**: NULL records routed to DLQ with clear error message
- ✅ **Debugging enabled**: Can investigate why deserialization returned NULL
- ✅ **Industry best practice**: All records must be accounted for in production pipelines

**Negative**:
- Slightly more verbose filter conditions (+20 characters per filter)

**Neutral**:
- Standard practice for production data pipelines
- Required for any three-valued logic system (SQL, Spark)

### Validation

- [x] Kraken records now routing to Silver (verified with commit logs)
- [x] NULL records routing to DLQ with "deserialization_failed_or_null_fields" error
- [x] No more silent data loss (342 input → 100 to DLQ confirmed)
- [x] Both exchanges writing successfully to Silver tables

### References

- **Spark SQL NULL Semantics**: https://spark.apache.org/docs/latest/sql-ref-null-semantics.html
- **Industry Best Practice**: AWS Kinesis, Kafka Streams, Databricks - all records must be accounted for
- **Related**: Decision #012 (Silver uses DLQ pattern), [SILVER_FIX_SUMMARY.md](./SILVER_FIX_SUMMARY.md)

---

## Decision #015: Right-Sized Resource Allocation for Streaming Workloads

**Date**: 2026-01-19
**Status**: Implemented and Verified
**Type**: Tier 2 (Design/Implementation - Performance Optimization)
**Related Step**: Steps 10-11 (Bronze and Silver Streaming Jobs)
**Related Docs**: [SILVER_FIX_SUMMARY.md](./SILVER_FIX_SUMMARY.md), [RESOURCE_ALLOCATION_FIX.md](./RESOURCE_ALLOCATION_FIX.md)

### Context

Initial Spark cluster configuration was over-provisioned:
- **Spark workers**: 2 workers × 4 cores × 6GB = 8 cores, 12GB total
- **Docker limits**: 8GB RAM per worker
- **Problem**: Kafka getting OOM killed (exit code 137), resource starvation

Streaming workloads for crypto trades are **lightweight** (< 100 records/batch, 30-second trigger), but over-provisioning caused:
1. Kafka OOM kills (exit 137)
2. Bronze streams failing to connect
3. Insufficient resources for other services

### Decision

**Size resources to actual workload, not theoretical maximums**:

**Spark Workers** (50% reduction):
```yaml
# BEFORE: 8 cores, 12GB total, 8GB Docker limits
spark-worker-1:
  SPARK_WORKER_CORES=4
  SPARK_WORKER_MEMORY=6g
  limits: cpus: '4.0', memory: 8G

# AFTER: 6 cores, 6GB total, 4GB Docker limits
spark-worker-1:
  SPARK_WORKER_CORES=3
  SPARK_WORKER_MEMORY=3g
  limits: cpus: '3.0', memory: 4G
```

**Executors** (per streaming job):
```yaml
# BEFORE: 2 cores per job = 8 cores needed (> 6 available)
--total-executor-cores 2
--executor-cores 2

# AFTER: 1 core per job = 4 cores needed (2 free for burst)
--total-executor-cores 1
--executor-cores 1
--executor-memory 1g
```

**Rationale**:
- **1 core per stage per exchange** = 4 cores needed (2 Bronze + 2 Silver)
- Actual CPU usage: < 1% per job in steady state
- 2 free cores (33% headroom) for burst processing
- Freed ~6GB RAM for Kafka and other services

### Implementation

**Files Modified**:
1. `docker-compose.yml` - Spark worker resources (lines ~743-810)
2. `docker-compose.yml` - All streaming job executor configs (4 jobs)

**Method**: Used `Edit` tool with `replace_all=true` for uniform changes across all streaming jobs

### Consequences

**Positive**:
- ✅ **Eliminated resource contention**: Kafka no longer OOM killed
- ✅ **50% reduction in Spark memory footprint**: 12GB → 6GB
- ✅ **Stable Kafka operation**: Now using 553MB / 3GB (no more exit 137)
- ✅ **All 4 streaming jobs run concurrently**: No more WAITING state
- ✅ **Resource efficiency**: 4/6 cores used (33% headroom)

**Negative**:
- Less headroom for large batch jobs (acceptable trade-off for streaming workload)
- May need horizontal scaling (add workers) if volume increases significantly

**Neutral**:
- Can scale horizontally if needed (add more workers)
- Right-sized for current workload (<100 records/batch)

### Validation

- [x] Kafka stable: 553MB / 3GB (no OOM kills for 2+ hours)
- [x] All 4 jobs running: Bronze-Binance, Bronze-Kraken, Silver-Binance, Silver-Kraken
- [x] Resource usage efficient: Workers using ~1GB / 4GB limits
- [x] CPU usage: < 1% per job in steady state (confirms right-sizing)

### References

- **Spark Resource Allocation**: https://spark.apache.org/docs/latest/configuration.html#application-properties
- **Related**: Decision #016 (1 Core Per Streaming Job), [RESOURCE_ALLOCATION_FIX.md](./RESOURCE_ALLOCATION_FIX.md)

---

## Decision #016: 1 Core Per Streaming Job (Container Lifecycle Fix)

**Date**: 2026-01-19
**Status**: Implemented and Verified
**Type**: Tier 2 (Implementation - Critical Operations Fix)
**Related Step**: Steps 10-11 (Bronze and Silver Streaming Jobs)
**Related Docs**: [RESOURCE_ALLOCATION_FIX.md](./RESOURCE_ALLOCATION_FIX.md)

### Context

After updating `docker-compose.yml` to use 1 core per job, Silver transformation jobs were **stuck in WAITING state with 0 cores allocated**:
```
Total: 6 cores, 6144MB memory
Bronze-Binance: 2 cores - RUNNING
Bronze-Kraken:  2 cores - RUNNING
Silver-Binance: 0 cores - WAITING
Silver-Kraken:  0 cores - WAITING
```

**Root Cause**: Container configuration drift - running containers had **stale 2-core config** despite docker-compose.yml showing 1-core.

**Key Learning**: `docker restart` ≠ `docker compose up -d`
- `docker restart <container>` = Restarts with **SAME config** container was created with
- `docker compose up -d <service>` = **Recreates** with NEW config from docker-compose.yml
- Docker inspect showed: `--total-executor-cores 2` (old config)
- docker-compose.yml correctly showed: `--total-executor-cores 1` (new config)

### Decision

**Allocate 1 core per streaming job** and **always use `docker compose up -d` to apply docker-compose.yml changes**:

**Resource Allocation**:
```yaml
# All 4 streaming jobs (Bronze Binance, Bronze Kraken, Silver Binance, Silver Kraken)
--total-executor-cores 1
--executor-cores 1
--executor-memory 1g
```

**Deployment Method**:
```bash
# WRONG (doesn't pick up docker-compose.yml changes)
docker restart k2-silver-binance-transformation

# CORRECT (recreates with updated docker-compose.yml)
docker compose up -d silver-binance-transformation
```

**Rationale**:
1. **Actual usage**: Jobs use < 1% CPU in steady state
2. **Concurrency**: 4 jobs × 1 core = 4 cores (fits in 6-core cluster with headroom)
3. **Burst capacity**: 2 free cores for micro-batch spikes
4. **Simplicity**: Uniform resource allocation across all streaming jobs

### Implementation

**Fix Applied**:
```bash
# Recreate all 4 streaming jobs with new 1-core config
docker compose up -d bronze-binance-stream bronze-kraken-stream \
                   silver-binance-transformation silver-kraken-transformation
```

**Verification**:
```bash
# Check actual executor cores in running containers
docker inspect k2-silver-binance-transformation | grep -o -- "--total-executor-cores [0-9]\+"
# Output: --total-executor-cores 1 ✅

# Check Spark Master UI
docker exec k2-spark-master curl -s http://localhost:8080/json/
# Output: All 4 jobs RUNNING with 1 core each ✅
```

### Consequences

**Positive**:
- ✅ **All jobs running concurrently**: No more WAITING state
- ✅ **33% resource headroom**: 4/6 cores used (2 free for burst)
- ✅ **Efficient resource usage**: < 1% CPU per job matches 1-core allocation
- ✅ **Operational learning**: Documented `docker restart` vs `docker compose up -d` pattern

**Negative**:
- May need horizontal scaling (more workers) for significantly higher volumes
- **Mitigation**: Monitor task latency; add workers if consistently > 5 seconds

**Neutral**:
- Can scale horizontally if needed (add more workers)
- Deployment requires `docker compose up -d` (not `docker restart`)

### Validation

- [x] All 4 jobs RUNNING: Bronze-Binance (1 core), Bronze-Kraken (1 core), Silver-Binance (1 core), Silver-Kraken (1 core)
- [x] Cluster resources: 4/6 cores used, 4096/6144MB memory used
- [x] Container configs match docker-compose.yml: Verified with `docker inspect`
- [x] Data flowing: Kraken committing 1 new data file every 30 seconds to silver_kraken_trades
- [x] No WAITING applications: All jobs allocated resources immediately

### Alternatives Considered

1. **2 cores per job**: Requires 8 cores, doesn't fit in 6-core cluster (rejected)
2. **Dynamic allocation**: More complex, overkill for stable workload (rejected)
3. **Priority scheduling**: Doesn't solve fundamental over-subscription (rejected)

### References

- **Docker Compose Lifecycle**: https://docs.docker.com/compose/faq/#whats-the-difference-between-up-and-restart
- **Spark Scheduling**: https://spark.apache.org/docs/latest/job-scheduling.html
- **Related**: Decision #015 (Right-Sized Resource Allocation), [RESOURCE_ALLOCATION_FIX.md](./RESOURCE_ALLOCATION_FIX.md)

---

## Decision #017: Layered Gold Schema (Unified + Pre-Computed OHLCV)

**Date**: 2026-01-20
**Status**: Accepted
**Type**: Tier 3 (Architectural)
**Related Step**: Step 12

### Context

Gold layer design for quantitative crypto analytics requires balancing:
1. **Query performance**: Quants need sub-second query latencies for common patterns
2. **Storage costs**: Pre-computing all possible aggregations is expensive
3. **Flexibility**: Ad-hoc queries require access to raw trades
4. **Simplicity**: Complex architectures are hard to maintain

Three architectural approaches considered:
1. **Single unified table only**: `gold_crypto_trades` with no pre-computations
2. **Star schema**: Fact tables + dimension tables (traditional data warehouse)
3. **Layered**: Base unified table + pre-computed high-value aggregations

**Usage analysis** (from quantitative analytics requirements):
- 90% of queries: OHLCV candles (1m/5m/1h/1d)
- 5% of queries: TWAP, cross-exchange analytics
- 5% of queries: Custom ad-hoc aggregations

### Decision

**Adopt layered Gold schema**: Base unified table + pre-computed OHLCV candles (1m/5m/1h/1d).

**Architecture**:
```
gold_crypto_trades (Base Table)
├── 17 fields (15 V2 + exchange_date + exchange_hour)
├── Hourly partitioning for efficient queries
├── Unlimited retention (primary data source)
└── Use: Custom analytics, ad-hoc queries, backfills

gold_ohlcv_1m/5m/1h/1d (Pre-Computed Candles)
├── 12 fields each (symbol, exchange, window, OHLCV, volume, count, vwap)
├── Daily partitioning by window_date
├── Tiered retention (90d/180d/1y/3y)
└── Use: 90% of analyst queries (technical analysis, charting, backtesting)
```

**Rationale**:
- **80/20 rule**: Pre-compute high-value aggregations (OHLCV = 90% of queries)
- **Cost optimization**: Store only frequently-queried aggregations
- **Performance**: p99 < 100ms for OHLCV queries vs 1-2s on-demand
- **Flexibility**: Base table supports custom aggregations not pre-computed
- **Storage efficient**: ~18 GB total for OHLCV (vs 100+ GB if all metrics pre-computed)

### Consequences

**Positive**:
- ✅ **Fast queries**: p99 < 100ms for OHLCV (covers 90% of use cases)
- ✅ **Cost efficient**: Only store high-value aggregations (~18 GB)
- ✅ **Flexible**: Base table supports arbitrary custom analytics
- ✅ **Simple**: 2 table types (base + OHLCV) vs 10+ in star schema
- ✅ **Industry standard**: Pattern used by Bloomberg, Refinitiv, Databricks

**Negative**:
- On-demand queries (TWAP, cross-exchange) take 1-2s vs <100ms for pre-computed
- **Mitigation**: 1-2s latency acceptable for <10% of queries

**Neutral**:
- Can add more pre-computed tables later if usage patterns change
- Retention policies can be tuned based on storage/cost trade-offs

### Validation

- [ ] Base table created: gold_crypto_trades (17 fields, hourly partitions)
- [ ] OHLCV tables created: gold_ohlcv_1m/5m/1h/1d (12 fields each)
- [ ] Query latency: p99 < 100ms for OHLCV (1-hour window)
- [ ] Storage: ~18 GB total for OHLCV tables (across all retention periods)
- [ ] Ad-hoc queries: Can compute custom aggregations from gold_crypto_trades

### Alternatives Considered

1. **Single unified table only** (no pre-computations)
   - **Pro**: Simplest, most flexible
   - **Con**: OHLCV queries take 1-2s (too slow for 90% of queries)
   - **Rejected**: Query performance unacceptable for primary use case

2. **Star schema** (fact + dimension tables)
   - **Pro**: Traditional data warehouse pattern, good for BI tools
   - **Con**: 10+ tables, complex joins, higher storage (~50 GB)
   - **Rejected**: Over-engineered for 2-exchange crypto platform

3. **Pre-compute everything** (all possible metrics)
   - **Pro**: All queries sub-100ms
   - **Con**: 100+ GB storage, expensive, many tables rarely queried
   - **Rejected**: Violates 80/20 rule, high cost for minimal benefit

### References

- **80/20 Pre-Computation Strategy**: Decision #019
- **Hybrid Processing**: Decision #018 (streaming + batch for OHLCV generation)
- **Industry Patterns**: Bloomberg Market Data Platform, Databricks Lakehouse Architecture
- **Related**: [Step 12: Gold Aggregation Job](./steps/step-12-gold-aggregation.md)

---

## Decision #018: Hybrid Processing (Streaming + Batch)

**Date**: 2026-01-20
**Status**: Accepted
**Type**: Tier 3 (Architectural)
**Related Step**: Step 12

### Context

Gold layer requires two types of processing:
1. **Base unified table** (`gold_crypto_trades`): Union Silver tables, deduplicate, derive partitions
2. **OHLCV candles**: Time-window aggregations (1m/5m/1h/1d)

**Processing options**:
- **Streaming only**: All processing via Spark Structured Streaming
- **Batch only**: All processing via scheduled Spark batch jobs
- **Hybrid**: Streaming for base table, batch for aggregations

**User requirement**: 5-minute freshness for analytics (acceptable latency)

**Constraints**:
- 2 cores available (4/6 used by Bronze + Silver jobs)
- OHLCV requires exact window boundaries (not event-time windows)
- Base table must support real-time queries

### Decision

**Adopt hybrid processing**: Streaming for base table, batch for OHLCV aggregations.

**Architecture**:
```
Streaming (Hot Path) - 1 core always on
└── gold_unified_streaming.py
    - Reads: silver_binance_trades + silver_kraken_trades
    - Processing: Union, stateful dedup (message_id), derive partition fields
    - Writes: gold_crypto_trades
    - Trigger: 5 minutes (matches user requirement)

Batch (Scheduled) - 1 core on-demand
├── gold_ohlcv_batch_1m.py   - Every 5 min  (10 min lookback)
├── gold_ohlcv_batch_5m.py   - Every 15 min (15 min lookback)
├── gold_ohlcv_batch_1h.py   - Every hour   (2 hour lookback)
└── gold_ohlcv_batch_1d.py   - Daily 00:05  (2 day lookback)
    - Reads: gold_crypto_trades (last N minutes/hours)
    - Processing: Window aggregations (tumbling windows)
    - Writes: gold_ohlcv_1m/5m/1h/1d (MERGE for late arrivals)
```

**Rationale**:
- **Streaming for base table**: Real-time deduplication and unified view required
- **Batch for OHLCV**: Exact window boundaries (9:00:00.000 to 9:01:00.000) easier in batch
- **Resource efficient**: 1 core always on (streaming) + 1 core scheduled (batch <2 min/hour)
- **Meets latency SLA**: Base table 5-min fresh, OHLCV 5-15 min fresh (acceptable)

### Consequences

**Positive**:
- ✅ **Real-time base table**: gold_crypto_trades updated every 5 minutes
- ✅ **Exact window alignment**: OHLCV candles have precise boundaries
- ✅ **Resource efficient**: 83% utilization steady state (5/6 cores), 100% during batch (<2 min/hour)
- ✅ **Handles late arrivals**: MERGE strategy updates existing candles
- ✅ **Simple orchestration**: Cron-like scheduler for batch jobs

**Negative**:
- OHLCV candles lag base table by 5-15 minutes (batch schedule)
- **Mitigation**: Acceptable for quantitative analytics (not high-frequency trading)
- **Mitigation**: Can query gold_crypto_trades directly if <5 min freshness needed

**Neutral**:
- Two processing paradigms to maintain (streaming + batch)
- Batch jobs run sequentially (staggered) to avoid resource contention

### Validation

- [ ] Streaming job running: gold_unified_streaming (1 core, 5-min trigger)
- [ ] Batch scheduler running: 4 jobs scheduled (1m: every 5 min, 5m: every 15 min, 1h: hourly, 1d: daily)
- [ ] Base table latency: p99 < 6 minutes (Kafka → gold_crypto_trades)
- [ ] OHLCV latency: Candles available within 5-15 min of window end
- [ ] Resource usage: 5/6 cores steady state, 6/6 during batch (<2 min/hour)
- [ ] Late arrivals handled: MERGE updates existing candles correctly

### Alternatives Considered

1. **Streaming only** (including OHLCV via event-time windows)
   - **Pro**: Single processing paradigm, real-time candles
   - **Con**: Event-time windows drift (9:00:02 to 9:01:01) vs exact boundaries required
   - **Con**: Complex watermark tuning for hourly/daily windows
   - **Rejected**: Window alignment issues unacceptable for OHLCV

2. **Batch only** (including base table)
   - **Pro**: Simpler (single paradigm), exact window alignment
   - **Con**: Base table not real-time (15-min batch interval minimum)
   - **Con**: Resource inefficient (1 core unused 95% of time)
   - **Rejected**: Doesn't meet 5-min freshness requirement

3. **Streaming OHLCV + Batch backfill**
   - **Pro**: Real-time candles + handle late arrivals
   - **Con**: Complex dual-write pattern, risk of inconsistency
   - **Rejected**: Over-engineered, hybrid approach simpler

### References

- **Layered Gold Schema**: Decision #017
- **80/20 Pre-Computation Strategy**: Decision #019
- **Industry Patterns**: Databricks Delta Live Tables (streaming + batch orchestration)
- **Related**: [Step 12: Gold Aggregation Job](./steps/step-12-gold-aggregation.md)

---

## Decision #019: 80/20 Pre-Computation Strategy

**Date**: 2026-01-20
**Status**: Accepted
**Type**: Tier 2 (Simplified ADR)
**Related Step**: Step 12

### Context

Quantitative analytics workloads require various metrics:
- **OHLCV candles** (1m/5m/1h/1d)
- **Volume metrics**: VWAP, TWAP, volume profiles
- **Cross-exchange analytics**: Price diffs, arbitrage opportunities, liquidity comparisons
- **Spreads**: Bid-ask spreads (requires quote data)
- **Technical indicators**: RSI, MACD, Bollinger Bands, etc.

**Challenge**: Pre-computing all metrics is expensive (100+ GB storage, 10+ tables to maintain).

**Usage analysis** (from quantitative user requirements):
- **90% of queries**: OHLCV candles (standard technical analysis)
- **5% of queries**: VWAP (included in OHLCV)
- **3% of queries**: TWAP, cross-exchange analytics
- **2% of queries**: Custom metrics (ad-hoc)

### Decision

**Apply 80/20 rule**: Pre-compute only high-value, frequently-queried metrics (OHLCV + VWAP).

**Pre-Compute** (✅):
| Metric | Rationale | Storage | Query Latency |
|--------|-----------|---------|---------------|
| OHLCV 1m/5m/1h/1d | 90% of queries | ~18 GB | < 100ms |
| VWAP | Cheap to compute with OHLCV (no extra storage) | 0 GB | < 100ms |

**Compute On-Demand** (❌):
| Metric | Rationale | Query Latency |
|--------|-----------|---------------|
| TWAP | Complex interpolation, <5% of queries | 1-2s (acceptable) |
| Cross-exchange | 3% of queries, simple join | <500ms (acceptable) |
| Spreads | Requires quote data (not yet available) | N/A |
| Technical indicators | <2% of queries, custom calculations | 2-5s (acceptable for ad-hoc) |

**Rationale**:
- **80% of value from 20% of metrics**: OHLCV covers 90% of use cases
- **Storage optimization**: 18 GB vs 100+ GB if all metrics pre-computed
- **Maintainability**: 5 tables (base + 4 OHLCV) vs 20+ tables
- **Latency trade-off**: <100ms for 90% of queries, 1-5s for 10% acceptable

### Consequences

**Positive**:
- ✅ **Fast common queries**: p99 < 100ms for OHLCV (90% of workload)
- ✅ **Cost efficient**: ~18 GB storage vs 100+ GB
- ✅ **Simple maintenance**: 5 tables vs 20+ tables
- ✅ **Flexible**: Can add more pre-computed metrics later if usage changes

**Negative**:
- On-demand queries take 1-5s vs <100ms if pre-computed
- **Mitigation**: Acceptable for <10% of queries (not latency-sensitive)

**Neutral**:
- Usage patterns may evolve → Monitor query logs and adjust pre-computation strategy

### Validation

- [ ] OHLCV query latency: p99 < 100ms (covers 90% of queries)
- [ ] TWAP on-demand: 1-2s latency (acceptable for 5% of queries)
- [ ] Cross-exchange on-demand: <500ms latency (acceptable for 3% of queries)
- [ ] Storage: ~18 GB total for Gold layer (OHLCV tables)
- [ ] Can add new pre-computed metrics: Process documented for future additions

### Alternatives Considered

1. **Pre-compute everything**
   - **Pro**: All queries sub-100ms
   - **Con**: 100+ GB storage, 20+ tables, high maintenance burden
   - **Rejected**: Violates 80/20 rule, expensive for minimal benefit

2. **No pre-computation** (all on-demand)
   - **Pro**: Simplest, most flexible
   - **Con**: OHLCV queries 1-2s (too slow for 90% of queries)
   - **Rejected**: Query performance unacceptable for primary use case

3. **Pre-compute OHLCV + TWAP**
   - **Pro**: Covers 95% of queries
   - **Con**: TWAP requires interpolation, complex to maintain, only 5% of queries
   - **Rejected**: Low ROI for 5% of queries

### References

- **Layered Gold Schema**: Decision #017
- **Hybrid Processing**: Decision #018
- **Pareto Principle**: https://en.wikipedia.org/wiki/Pareto_principle (80/20 rule)
- **Related**: [Step 12: Gold Aggregation Job](./steps/step-12-gold-aggregation.md)

---

