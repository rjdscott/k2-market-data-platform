# Phase 10 Architectural Decisions

**Last Updated**: 2026-01-18
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
