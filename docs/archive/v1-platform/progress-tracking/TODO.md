# K2 Platform: Future Enhancements & TODO

**Last Updated**: 2026-01-19
**Purpose**: Track features deferred for multi-node/distributed implementations and end-of-project reviews

---

## End-of-Project Reviews

### Flink Evaluation Review
**Status**: Deferred to end-of-project
**Originally**: Evaluated in Phase 10 (ADR-003)
**Decision**: Continue with Spark Structured Streaming (optimized)
**Review Trigger**: End of Phase 10-12 completion

**Review Criteria**:
- [ ] Sub-second latency has become business-critical
- [ ] Complex CEP requirements emerged (windowed joins, pattern matching)
- [ ] Query patterns shifted to direct Bronze table queries
- [ ] Team has hired Flink expert or gained significant Flink experience

**Documentation**:
- `docs/design/stream-processing-evaluation.md` - Comprehensive Flink vs Spark analysis
- `docs/architecture/decisions/ADR-003-stream-processing-engine-selection.md` - Decision record
- `docs/reference/flink-vs-spark-streaming-reference.md` - Quick reference guide

**Expected Outcome**: Validate that Spark optimization (5s trigger) continues to meet requirements, or identify concrete trigger for Flink adoption.

---

## Integration Test Improvements (Phase 11+)

**Status**: Deferred from Phase 10
**Current**: 20/24 tests passing (83% pass rate)
**Priority**: Medium (improve to 100% before production)

**Remaining Work**:
- [ ] Fix hybrid query timing issues (2 tests) - Kafka consumer needs polling until messages available
- [ ] Implement rate limiting tests (currently skipped)
- [ ] Implement CORS header tests (currently skipped)

**Reference**: See archive or git history for detailed integration test status (previously in todo.md, now removed)

---

## Multi-Node Scaling Enhancements

These features are designed for distributed deployments and add unnecessary complexity to single-node implementations.

### 1. Redis-Backed Sequence Tracker

**Status**: Deferred from Phase 3
**Originally**: Step 04 in Phase 3 Demo Enhancements
**Rationale for Deferral**:
- Single-node in-memory dict is faster (no network hop)
- Current throughput (138 msg/sec) is I/O bound, not CPU bound
- GIL contention only matters at >100K msg/sec (not reached on single-node)
- Network latency (1-2ms) vs in-memory dict (<1μs) makes Redis slower for single-node

**When to Implement**:
- Multi-node deployment with consumer fleet
- Need for shared sequence state across consumers
- Throughput targets >100K msg/sec per node
- State persistence requirements across restarts

**Implementation Plan** (6-8 hours):
```
1. Add Redis service to docker-compose.yml
2. Create StateStore abstraction (InMemoryStateStore, RedisStateStore)
3. Refactor SequenceTracker to use StateStore interface
4. Add Redis pipelining for batch operations (<100ms for 10K checks)
5. Test backward compatibility (in-memory store still works)
6. Add Redis health checks
7. Performance benchmarks (memory vs Redis)
```

**References**:
- `docs/phases/phase-3-demo-enhancements/steps/step-04-redis-sequence-tracker.md`
- `docs/phases/phase-3-demo-enhancements/NEXT_STEPS.md` (lines 60-91)

---

### 2. Bloom Filter Deduplication

**Status**: Deferred from Phase 3
**Originally**: Step 05 in Phase 3 Demo Enhancements
**Rationale for Deferral**:
- In-memory dict sufficient for single-node crypto workloads
- Crypto message rates (10K-50K peak) fit in memory
- Bloom filter false positives add complexity
- Network hop to Redis for confirmation layer adds latency

**When to Implement**:
- Multi-node deployment needing shared dedup state
- 24-hour dedup window at >1M msg/sec scale
- Memory constraints (<2GB for 1B entries requirement)
- Multiple consumer instances needing coordination

**Implementation Plan** (6-8 hours):
```
1. Install Bloom filter library (pybloom-live==4.0.0)
2. Create HybridDeduplicator class
   - Tier 1: Bloom filter (probabilistic, 24hr window)
   - Tier 2: Redis set (exact matches, 1hr window)
3. Configure false positive rate (0.1% target)
4. Integrate into consumer
5. Add metrics (bloom checks, redis checks, false positives)
6. Memory efficiency tests (1M entries, <2GB target)
7. Performance benchmarks (<1ms per check)
```

**References**:
- `docs/phases/phase-3-demo-enhancements/steps/step-05-bloom-filter-dedup.md`
- `docs/phases/phase-3-demo-enhancements/NEXT_STEPS.md` (lines 94-115)

---

## Why These Were Deferred

### The Single-Node Reality

**Current Performance**:
- Consumer throughput: 138 msg/sec (with Iceberg writes)
- Crypto peak rates: 10K-50K msg/sec (BTCUSDT + top 10 pairs + orderbook)
- Bottleneck: I/O (Iceberg writes), NOT in-memory operations

**Redis Sequence Tracker**:
- Adds 1-2ms network latency vs <1μs in-memory dict
- GIL contention not a factor until >100K msg/sec
- Single-node doesn't benefit from distributed state
- **Result**: Slower and more complex without benefit

**Bloom Filter Deduplication**:
- Current in-memory dict handles 10K-50K msg/sec easily
- Crypto dedup window (1-hour typical) fits in memory
- Bloom false positives require Redis confirmation anyway
- **Result**: Added complexity without memory savings

### What We Focused On Instead

**Phase 3 Priorities** (High Value for Single-Node Crypto):
1. ✅ Platform Positioning - Clear L3 cold path messaging
2. ✅ Circuit Breaker - Graceful degradation (production-grade resilience)
3. ✅ Degradation Demo - Compelling visual demonstration
4. **Hybrid Query Engine** - Query recent data (Kafka + Iceberg merge) - CRITICAL for crypto
5. **Demo Narrative** - 10-minute Principal-level presentation
6. **Cost Model** - FinOps business acumen

**Why This is Better**:
- Hybrid queries show lakehouse value prop (batch + streaming unified)
- Demo narrative makes the project presentation-ready
- Cost model demonstrates Staff/Principal-level thinking
- Saves 12-16 hours for higher-impact work

---

## Other Multi-Node Enhancements (Phase 3+)

### 3. Multi-Region Replication
**When**: Phase 3 - Production Deployment
**Why**: Geographic redundancy, disaster recovery
**Complexity**: High (consensus, conflict resolution, network partitions)

### 4. Kafka Cluster (Multi-Broker)
**When**: Phase 3 - Production Deployment
**Why**: Fault tolerance, higher throughput partitioning
**Complexity**: Medium (configuration, monitoring, rebalancing)

### 5. Presto/Trino Query Engine
**When**: Phase 3 - Production Deployment
**Why**: Distributed query processing, horizontal scalability
**Complexity**: High (cluster management, query planning, resource allocation)

### 6. Auto-Scaling Consumer Fleet
**When**: Phase 3 - Production Deployment
**Why**: Dynamic load handling, cost optimization
**Complexity**: Medium (Kubernetes, metrics-based scaling, coordination)

### 7. Multi-Datacenter State Coordination
**When**: Phase 4 - Global Scale
**Why**: Cross-region consistency, symbol routing
**Complexity**: Very High (CAP theorem trade-offs, eventual consistency)

---

## Criteria for Reconsidering Deferred Features

**Redis Sequence Tracker** - Implement when:
- [ ] Deploying multi-node consumer fleet
- [ ] Throughput exceeds 100K msg/sec per node
- [ ] Need shared sequence state across instances
- [ ] State persistence across restarts required

**Bloom Filter Dedup** - Implement when:
- [ ] Memory usage for dedup exceeds 2GB
- [ ] Need 24-hour dedup window at scale
- [ ] Deploying distributed consumer instances
- [ ] In-memory dict becomes bottleneck

---

## Single-Node Optimization Priorities

Instead of Redis/Bloom, focus on:

### 1. Query Performance (Higher ROI)
- [ ] DuckDB query optimization (partition pruning)
- [ ] Result caching for common queries
- [ ] Precomputed aggregates (OHLCV tables)
- [ ] Index optimization for time-series queries

### 2. Crypto-Specific Features (Differentiation)
- [ ] Funding rates (critical for futures trading)
- [ ] Liquidation data (market sentiment indicators)
- [ ] Orderbook depth snapshots (L2 data)
- [ ] Cross-exchange arbitrage detection

### 3. Real-Time Analytics (Value-Add)
- [ ] Volatility metrics (rolling std dev, Bollinger bands)
- [ ] Volume profiles (VWAP, TWAP calculations)
- [ ] Price momentum indicators (RSI, MACD)
- [ ] Correlation analysis across pairs

### 4. API Enhancements (Usability)
- [ ] WebSocket subscriptions (real-time push)
- [ ] GraphQL query interface (flexible data fetching)
- [ ] Rate limiting per API key
- [ ] API usage analytics dashboard

---

## Decision Log

### Decision: Defer Redis & Bloom to Multi-Node Phase

**Date**: 2026-01-13
**Context**: Phase 3 Demo Enhancements step prioritization
**Decision**: Skip Steps 04-05 (Redis Sequence Tracker, Bloom Filter Dedup)
**Rationale**:
1. Over-engineering for single-node use case
2. In-memory dict is faster (no network latency)
3. Not the performance bottleneck (I/O bound at 138 msg/sec)
4. Adds 12-16 hours of complexity without benefit
5. Better to focus on hybrid queries and demo narrative

**Consequences**:
- ✅ Saves 12-16 hours for higher-value work
- ✅ Simpler architecture (no Redis dependency)
- ✅ Faster sequence tracking (in-memory vs network hop)
- ✅ More relevant to crypto single-node demo
- ⚠️ Will need to implement when scaling to multi-node
- ⚠️ Sequence state lost on process restart (acceptable for demo)

**Approved By**: User + Implementation Team

---

## References

- Phase 3 Plan: `docs/phases/phase-3-demo-enhancements/README.md`
- NEXT_STEPS: `docs/phases/phase-3-demo-enhancements/NEXT_STEPS.md`
- Original Step 04: `docs/phases/phase-3-demo-enhancements/steps/step-04-redis-sequence-tracker.md`
- Original Step 05: `docs/phases/phase-3-demo-enhancements/steps/step-05-bloom-filter-dedup.md`

---

**Last Updated**: 2026-01-13
**Maintained By**: Implementation Team
