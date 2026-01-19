# Stream Processing Evaluation: Apache Flink vs Spark for K2 Platform

**Last Updated**: 2026-01-19
**Status**: Proposed
**Owners**: Platform Architecture Team
**Scope**: Stream processing engine selection for Kafka → Bronze ingestion

---

## Executive Summary

### Recommendation: **Optimized Spark-Only Architecture (Short-Term) + Flink Readiness (Long-Term)**

**Decision**: Continue with Spark Structured Streaming for Kafka → Bronze ingestion with targeted optimizations, while maintaining architectural readiness for Flink adoption when latency requirements evolve.

**Rationale**:
- **Current performance meets requirements**: 5-10s actual latency vs 10-30s target (overperforming)
- **Boring technology principle**: Spark is proven, team-familiar, operationally simple
- **Cost-benefit analysis**: Flink adds 40-60% operational overhead for marginal latency gains
- **Platform positioning**: L3 Cold Path (research/analytics) does NOT require HFT-level latency
- **Risk mitigation**: Current architecture allows Flink adoption when needed without major redesign

**Short-Term Actions** (Phase 10-11):
1. Optimize Spark micro-batch interval (10s → 5s) for 2-3s latency improvement
2. Continue Bronze per-exchange pattern (ADR-002 validated)
3. Monitor latency budgets and query patterns

**Long-Term Triggers** (Phase 12+):
- **Flink adoption when**: Sub-second latency becomes business-critical (unlikely for current use cases)
- **Architecture preserved**: Bronze layer design supports either engine

---

## 1. Current Architecture Analysis

### 1.1 Spark Structured Streaming Performance

**Configuration** (Phase 10):
```python
# Binance Bronze Job
trigger_interval: 10 seconds
max_offsets_per_trigger: 10,000 messages
workers: 3
executor_cores: 2
```

**Observed Performance**:
| Metric | Binance | Kraken | Target | Status |
|--------|---------|--------|--------|--------|
| **p50 latency** | 3-5s | 8-12s | < 10s | ✅ Exceeds |
| **p99 latency** | 5-10s | 15-25s | < 30s | ✅ Exceeds |
| **Throughput** | 10K+ msg/sec | 500+ msg/sec | Variable | ✅ Exceeds |
| **End-to-end** | 300-400ms | - | < 500ms p99 | ✅ Exceeds |

**Key Finding**: Current Spark implementation **overperforms** latency targets by 2-3x, indicating headroom for optimization without engine replacement.

### 1.2 Latency Budget Breakdown

From `latency-budgets.md`:
```
Total Budget: 500ms @ p99 (Exchange → Query API)

Current Bronze Stage:
- Kafka consumer poll: 5-10ms
- Business logic: 20-50ms
- Iceberg write: 100-200ms
- Snapshot commit: 50-100ms
- Offset commit: 10ms
------------------------------------
Total: 185-370ms @ p50, ~500-800ms @ p99
```

**Analysis**: Spark micro-batching adds latency variance (10s trigger window), but p99 still meets budget. Optimization potential: Reduce trigger interval to 5s → ~2-3s p99 latency.

### 1.3 Current Architecture Strengths

✅ **Unified Stack**: Spark for both ingestion (Bronze) and transformations (Silver/Gold)
✅ **Operational Simplicity**: Single engine, familiar to team, mature tooling
✅ **Bronze Per Exchange** (ADR-002): Isolation, independent scaling, clear ownership
✅ **Iceberg Integration**: Native support, ACID guarantees, time-travel
✅ **At-Least-Once + Idempotency**: Aligns with platform principles (no exactly-once complexity)

### 1.4 Optimization Opportunities (Before Considering Flink)

**Option A: Reduce Micro-Batch Interval**
```python
# Current
.trigger(processingTime="10 seconds")

# Optimized
.trigger(processingTime="5 seconds")  # 50% latency reduction
```
**Expected Impact**: 5-10s → 2-5s p99 latency (still micro-batch, not true streaming)

**Option B: Continuous Processing Mode**
```python
# Experimental (Spark 3.5+)
.trigger(continuous="1 second")  # True streaming, 1-2s latency
```
**Trade-off**: Less mature than micro-batch, potential stability risks

**Option C: Increase Parallelism**
- Current: 3 workers, 2 cores each
- Optimized: 4-5 workers → better partition parallelism → lower p99 tail latency

**Recommendation**: Start with Option A (reduce trigger interval) - low risk, high impact.

---

## 2. Apache Flink Evaluation

### 2.1 Flink Strengths for Kafka → Bronze

**True Streaming Architecture**:
- Event-at-a-time processing (NOT micro-batches)
- Sub-second latency (100-500ms p99)
- Low latency variance (predictable performance)

**Exactly-Once Semantics**:
- Native two-phase commit with Kafka
- Flink checkpointing for state durability
- Lower complexity than Spark transactions

**Stateful Processing**:
- Native support for windowing, aggregations, CEP
- Managed state backends (RocksDB, in-memory)
- Incremental checkpointing (faster recovery)

**Iceberg Integration** (Flink 1.17+):
```java
// Flink 1.17+ has native Iceberg sink
FlinkSink.forRowData(input)
    .table(icebergTable)
    .tableLoader(tableLoader)
    .equalityFieldColumns(Arrays.asList("message_id"))
    .build();
```
**Maturity**: Production-ready since Flink 1.17 (2023), used by Uber, Netflix, Alibaba

### 2.2 Flink Weaknesses and Risks

**Operational Complexity** (40-60% higher than Spark):
```
Spark Cluster:
- 1 master + N workers (simple)
- Shared resource pool
- Checkpoint to HDFS/S3

Flink Cluster:
- 1 JobManager + N TaskManagers
- Per-job resources OR session cluster
- State backend configuration (RocksDB tuning)
- Checkpoint coordination overhead
- Savepoint management for upgrades
```

**Team Learning Curve**:
- Spark: Team familiar (current Phase 10 implementation)
- Flink: New APIs (DataStream, Table API), state management concepts
- Estimated ramp-up: 2-3 weeks for team proficiency

**Debugging and Monitoring**:
- Spark: Mature UI, well-known metrics (Prometheus exporters)
- Flink: Different mental model (backpressure, watermarks, checkpoints)
- Tooling: Flink Dashboard less mature than Spark UI

**Two-Engine Operational Overhead** (if hybrid):
```
Maintenance Tasks:
- Spark: Upgrades, security patches, monitoring dashboards
- Flink: Upgrades, security patches, monitoring dashboards
- Duplication: 2x effort for maintenance windows, runbooks, on-call training
```

**Cost Estimate**:
- Flink cluster: 3 TaskManagers (4 cores, 16GB each) = $200-300/month
- Operational overhead: +1-2 hours/week engineering time = $5-10K/year
- **Total incremental cost**: ~$15K/year for Flink adoption

### 2.3 Flink-Iceberg Integration Analysis

**Connector Maturity** (as of Flink 1.19, Jan 2025):
```
Flink 1.17+ (Nov 2023):
✅ Iceberg sink with exactly-once semantics
✅ Upsert mode (equality delete support)
✅ Partition pruning
✅ Hidden partitioning support

Flink 1.18+ (Oct 2024):
✅ Incremental changelog streams
✅ Row-level deletes
✅ Schema evolution support

Production Validation:
- Uber: Flink + Iceberg for real-time data lake (2023)
- Netflix: Flink + Iceberg for streaming analytics (2024)
- Alibaba: Flink + Iceberg at massive scale (2022+)
```

**Verdict**: Flink-Iceberg integration is **production-ready** for Bronze ingestion use cases.

### 2.4 When Flink Makes Sense

**Scenario 1: Sub-Second Latency Required**
- Example: Real-time trading signals (100-500ms critical)
- Spark limitation: 2-5s p99 even with optimization
- Flink advantage: 100-500ms p99 (true streaming)

**Scenario 2: Complex Stateful Processing**
- Example: Cross-symbol arbitrage detection (windowed joins)
- Spark limitation: Stateful operations in micro-batch are clunky
- Flink advantage: Native windowing, CEP, managed state

**Scenario 3: Exactly-Once Semantics Critical**
- Example: Financial aggregations (total volume MUST be exact)
- Spark limitation: At-least-once + manual deduplication
- Flink advantage: Native exactly-once without deduplication overhead

**K2 Platform Reality Check**:
- **Scenario 1**: NOT required (5-10s latency acceptable for L3 Cold Path)
- **Scenario 2**: NOT current requirement (simple Bronze ingestion, no CEP)
- **Scenario 3**: NOT required (at-least-once + idempotency sufficient per platform principles)

**Conclusion**: Current requirements do NOT justify Flink adoption.

---

## 3. Hybrid Architecture Analysis

### 3.1 Hot Path (Flink) + Cold Path (Spark)

**Architecture**:
```
Hot Path (Real-Time):
Kafka → Flink → Bronze Iceberg → Direct queries (< 1s latency)

Cold Path (Batch):
Bronze → Spark → Silver → Spark → Gold → Analytical queries
```

**Rationale for Hybrid**:
- Flink: Optimized for low-latency ingestion (100-500ms)
- Spark: Optimized for batch transformations (Silver/Gold)
- Separation of concerns: Ingest vs transform

### 3.2 Avoiding Lambda Architecture Pitfalls

**Classic Lambda Problems**:
❌ Duplicate logic (batch + streaming implementations)
❌ Consistency challenges (batch vs stream results differ)
❌ Operational overhead (two pipelines to maintain)

**K2 Hybrid Design Avoids Pitfalls**:
✅ **Single source of truth**: Bronze layer (Flink-written OR Spark-written, not both)
✅ **No duplicate logic**: Ingest (Flink) vs Transform (Spark) are separate concerns
✅ **Consistent lineage**: Bronze → Silver → Gold (same data, different engines)

### 3.3 Query Routing Layer

**Tiered Query Strategy**:
```python
class QueryRouter:
    REALTIME_CUTOFF = timedelta(minutes=5)

    def route(self, query_time_range):
        if query_time_range.end > now() - self.REALTIME_CUTOFF:
            # Recent data: Query Bronze (Flink-written, low latency)
            return BronzeQuery(engine="duckdb")
        else:
            # Historical data: Query Silver/Gold (Spark-optimized)
            return GoldQuery(engine="duckdb")
```

**Advantage**: Optimal latency for each use case
**Disadvantage**: Adds routing complexity

### 3.4 Operational Complexity Assessment

**Single Engine (Spark-Only)**:
```
Components:
- Spark cluster (1 master, N workers)
- Checkpoint management (S3)
- Monitoring (Prometheus + Grafana)
- On-call runbooks (1 engine)

Operational Burden: Baseline (1x)
```

**Hybrid (Flink + Spark)**:
```
Components:
- Flink cluster (1 JobManager, N TaskManagers)
- Spark cluster (1 master, N workers)
- Flink state backend (RocksDB + S3)
- Spark checkpoint management (S3)
- Monitoring (2x dashboards, 2x alerts)
- On-call runbooks (2 engines)

Operational Burden: 1.4-1.6x (40-60% increase)
```

**Cost-Benefit**:
- Benefit: Sub-second Bronze latency (100-500ms vs 2-5s)
- Cost: 40-60% operational overhead + $15K/year infrastructure

**Verdict**: Only justified if sub-second latency is business-critical.

---

## 4. Real-Time Query Patterns

### 4.1 Bronze Table Access Patterns (Current)

From `query-architecture.md`:
```
Query Mode 1: Real-Time (<5 min)
- Source: Kafka tail (NOT Bronze Iceberg)
- Latency: 10-50ms
- Use case: Live price monitoring

Query Mode 2: Historical (>5 min)
- Source: Iceberg Bronze/Silver/Gold
- Latency: 1-5 seconds
- Use case: Backtesting, analytics

Query Mode 3: Hybrid (Spans both)
- Source: Iceberg + Kafka (merged)
- Latency: 1-5 seconds (dominated by Iceberg)
- Use case: 1-hour sliding window queries
```

**Key Finding**: Real-time queries (<5 min) already use Kafka tail, NOT Bronze Iceberg. Flink's low-latency Bronze writes provide minimal user-facing benefit.

### 4.2 Latency Requirements by Use Case

| Use Case | Current Solution | Latency | Flink Improvement |
|----------|------------------|---------|-------------------|
| **Live price monitoring** | Kafka tail | 10-50ms | None (already optimal) |
| **Recent trades (1 hour)** | Hybrid (Iceberg + Kafka) | 1-5s | Minimal (Bronze already fresh) |
| **Backtesting (historical)** | Iceberg Gold | 1-10s | None (batch queries) |
| **Compliance reports** | Iceberg Silver/Gold | 10-60s | None (batch queries) |

**Conclusion**: Current query architecture does NOT benefit materially from Flink's lower Bronze latency.

### 4.3 Query Integration Strategy (If Flink Adopted)

**Option A: Flink for Ingestion Only** (Recommended)
```
Kafka → Flink → Bronze Iceberg
         ↓
      Query Bronze via DuckDB (same as today)
```
- Flink writes to Bronze, queries unchanged
- Transparent to query layer

**Option B: Flink Stateful Queries** (Advanced)
```
Kafka → Flink → Bronze Iceberg
         ↓
      Flink SQL queries (parallel to DuckDB)
```
- Adds Flink as query engine
- Increases complexity (two query paths)

**Recommendation**: Option A (ingestion only) - simplest integration.

---

## 5. Decision Framework

### 5.1 Use Spark-Only When

✅ **Latency requirements**: 2-10s acceptable (current: 5-10s, optimized: 2-5s)
✅ **Operational simplicity valued**: Single engine, team familiar
✅ **Use case**: Analytics, research, backtesting (L3 Cold Path)
✅ **At-least-once sufficient**: Idempotency handles duplicates
✅ **Budget constraints**: Minimize infrastructure and operational overhead

**K2 Platform Status**: ✅ All conditions met (Spark-only recommended)

### 5.2 Use Flink When

✅ **Sub-second latency required**: 100-500ms critical (NOT current requirement)
✅ **Complex stateful processing**: Windowed joins, CEP, pattern matching
✅ **Exactly-once semantics critical**: Financial aggregations (NOT required due to idempotency)
✅ **Team has Flink expertise**: Operational overhead acceptable

**K2 Platform Status**: ❌ No conditions met (Flink NOT justified)

### 5.3 Use Hybrid (Flink + Spark) When

✅ **Tiered latency requirements**: Sub-second for some, batch for others
✅ **Separation of concerns beneficial**: Ingest vs transform engines
✅ **Operational overhead acceptable**: Team can manage two engines
✅ **Infrastructure budget available**: +$15K/year acceptable

**K2 Platform Status**: ⚠️ Latency is tiered (user clarified), but current Spark performance meets all tiers. Hybrid NOT justified unless sub-second becomes business-critical.

---

## 6. Alignment with Platform Principles

From `platform-principles.md`:

### Principle 1: Boring Technology ✅
**Spark-Only**: Proven, 10+ years production, team familiar
**Flink**: Mature (since 2015) but newer to team, adds complexity

**Verdict**: Spark aligns better with boring technology principle.

### Principle 2: Idempotency Over Exactly-Once ✅
**Spark**: At-least-once + manual deduplication (aligns perfectly)
**Flink**: Native exactly-once (nice-to-have but NOT required per principles)

**Verdict**: Spark's at-least-once model matches platform philosophy.

### Principle 3: Degrade Gracefully ✅
**Spark**: Well-understood degradation (increase batch interval, reduce partitions)
**Flink**: Different degradation model (backpressure, checkpoint timeouts)

**Verdict**: Spark's degradation patterns are team-familiar.

### Principle 4: Replayable by Default ✅
**Spark**: Checkpoints enable replay (current implementation)
**Flink**: Savepoints enable replay (equivalent capability)

**Verdict**: Both support replay (tie).

### Principle 5: Observable by Default ✅
**Spark**: Mature Prometheus exporters, team-familiar metrics
**Flink**: Requires new dashboards, different metric semantics

**Verdict**: Spark observability is more mature and familiar.

**Overall Alignment Score**:
- **Spark-Only**: 5/5 principles aligned ✅
- **Flink**: 3/5 principles aligned (missing boring tech, familiarity)
- **Hybrid**: 3/5 principles aligned + 40-60% operational overhead

---

## 7. Recommendation

### 7.1 Short-Term (Phase 10-11): Optimized Spark-Only

**Actions**:
1. **Reduce micro-batch interval**: 10s → 5s (expected: 2-5s p99 latency)
2. **Monitor performance**: Track p99 latency, throughput, lag metrics
3. **Continue Bronze per exchange** (ADR-002 validated for isolation)
4. **Maintain current architecture**: Spark Structured Streaming → Bronze Iceberg

**Expected Outcomes**:
- 2-5s p99 Bronze latency (vs current 5-10s)
- Operational simplicity maintained (single engine)
- Cost: Zero incremental (configuration change only)

**Validation Criteria**:
- p99 Bronze latency < 5s (Binance)
- p99 Bronze latency < 10s (Kraken)
- No increase in operational incidents

### 7.2 Long-Term (Phase 12+): Flink Readiness

**Trigger Conditions for Flink Adoption**:
1. **Business requirement**: Sub-second latency becomes critical (e.g., real-time trading signals)
2. **Complex stateful processing**: CEP, windowed joins across exchanges
3. **Query pattern shift**: Bronze table direct queries dominate (vs current Kafka tail + Iceberg pattern)
4. **Team growth**: Dedicated streaming engineer hired with Flink expertise

**Migration Path** (when triggered):
```
Phase 1: Proof of Concept (4 weeks)
- Deploy Flink cluster (3 TaskManagers)
- Implement Binance Bronze job (parallel to Spark)
- Compare latency, throughput, operational complexity

Phase 2: Validation (4 weeks)
- Run Flink + Spark in parallel (shadow mode)
- Validate data consistency (message-level comparison)
- Measure operational overhead (monitoring, debugging, incidents)

Phase 3: Migration (4 weeks)
- Cutover Binance Bronze to Flink
- Monitor for 2 weeks
- Cutover Kraken Bronze to Flink

Phase 4: Deprecation (2 weeks)
- Deprecate Spark Bronze jobs
- Keep Spark for Silver/Gold transformations
```

**Estimated Effort**: 14 weeks + $15K/year ongoing cost

### 7.3 Alternative: Flink for New Use Cases Only

**Strategy**: Keep Spark for existing Bronze ingestion, use Flink for future low-latency use cases
```
Current:
Binance/Kraken → Spark → Bronze (5-10s latency) ← Keep as-is

Future (if sub-second required):
New exchanges → Flink → Bronze (100-500ms latency)
```

**Advantage**: Incremental adoption, lower migration risk
**Disadvantage**: Two engines for similar use cases (tech debt)

---

## 8. Implementation Considerations

### 8.1 Spark Optimization Roadmap

**Phase 10 (Current)**:
```python
# bronze_binance_ingestion.py
.trigger(processingTime="10 seconds")
.option("maxOffsetsPerTrigger", 10000)
```

**Phase 11 (Optimized)**:
```python
# bronze_binance_ingestion.py
.trigger(processingTime="5 seconds")     # ← 50% latency reduction
.option("maxOffsetsPerTrigger", 5000)    # ← Adjusted for 5s trigger
.option("spark.sql.shuffle.partitions", 12)  # ← Better parallelism
```

**Expected Impact**: 5-10s → 2-5s p99 latency

**Phase 12 (Advanced)**:
```python
# Experimental: Continuous processing mode
.trigger(continuous="1 second")  # ← True streaming (Spark 3.5+)
```
**Risk**: Less mature than micro-batch, use for low-volume exchanges only (Kraken)

### 8.2 Flink Implementation Skeleton (If Adopted)

```java
// Flink 1.19+ Iceberg Sink Example
StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
env.enableCheckpointing(5000);  // 5s checkpoint interval

// Read from Kafka
FlinkKafkaConsumer<TradeEvent> kafkaSource = new FlinkKafkaConsumer<>(
    "market.crypto.trades.binance",
    new AvroDeserializationSchema<>(TradeEvent.class),
    kafkaProperties
);

DataStream<RowData> stream = env.addSource(kafkaSource)
    .map(new TradeEventToRowData());

// Write to Iceberg Bronze
TableLoader tableLoader = TableLoader.fromCatalog(
    catalogLoader,
    TableIdentifier.of("market_data", "bronze_binance_trades")
);

FlinkSink.forRowData(stream)
    .table(icebergTable)
    .tableLoader(tableLoader)
    .equalityFieldColumns(Arrays.asList("message_id"))
    .uidPrefix("bronze-binance")
    .build();

env.execute("Bronze Binance Ingestion - Flink");
```

**Key Configuration**:
- Checkpoint interval: 5 seconds (balance latency vs overhead)
- State backend: RocksDB (large state, incremental checkpoints)
- Parallelism: 6 (2x Kafka partitions for load balancing)

### 8.3 Monitoring and Alerting

**Spark Metrics** (Current):
```
# Prometheus metrics
spark_streaming_batch_processing_time_ms{job="bronze_binance"} < 10000
spark_streaming_input_rate{job="bronze_binance"} > 10000
spark_streaming_scheduling_delay_ms{job="bronze_binance"} < 1000
```

**Flink Metrics** (If Adopted):
```
# Prometheus metrics
flink_taskmanager_job_task_checkpointDuration{job="bronze_binance"} < 5000
flink_taskmanager_job_task_numRecordsIn{job="bronze_binance"} > 10000
flink_taskmanager_Status_JVM_Memory_Heap_Used{job="bronze_binance"} < 12GB
```

**Alert Differences**:
- Spark: Batch processing time, scheduling delay
- Flink: Checkpoint duration, backpressure, heap memory

---

## 9. Cost-Benefit Analysis

### 9.1 Spark Optimization (Phase 11)

**Cost**:
- Engineering time: 4-8 hours (configuration tuning)
- Infrastructure: $0 (no new resources)
- Operational: $0 (no new complexity)

**Benefit**:
- Latency improvement: 5-10s → 2-5s (50% reduction)
- Risk: Low (configuration change only)
- Reversibility: High (revert config in minutes)

**ROI**: **Immediate** (high benefit, zero cost)

### 9.2 Flink Adoption (Phase 12+)

**Cost**:
- Engineering time: 14 weeks (PoC + migration + validation)
- Infrastructure: $15K/year (Flink cluster + operational overhead)
- Learning curve: 2-3 weeks team ramp-up
- Operational complexity: +40-60% maintenance burden

**Benefit**:
- Latency improvement: 2-5s → 100-500ms (80-90% reduction)
- Exactly-once semantics: Native (vs manual deduplication)
- Stateful processing: Better for future CEP use cases

**ROI**: **Negative unless sub-second latency is business-critical** (high cost, low incremental benefit for current use cases)

### 9.3 Hybrid Architecture (Flink + Spark)

**Cost**:
- All Flink costs PLUS
- Dual-engine maintenance: 2x dashboards, 2x runbooks, 2x on-call training
- Coordination overhead: Ensuring Bronze consistency across engines

**Benefit**:
- Tiered latency optimization: Sub-second for hot path, batch for cold path
- Best tool for each job: Flink (ingest) vs Spark (transform)

**ROI**: **Negative unless both sub-second AND batch workloads are critical AND optimized Spark-only insufficient**

---

## 10. Conclusion

### 10.1 Final Recommendation

**Phase 10-11 (Immediate)**:
✅ **Continue with optimized Spark Structured Streaming**
- Reduce micro-batch interval: 10s → 5s
- Expected latency: 2-5s p99 (meets all current requirements)
- Zero incremental cost, low risk

**Phase 12+ (Conditional)**:
⚠️ **Monitor trigger conditions for Flink adoption**
- Business requires sub-second latency (NOT current requirement)
- Complex stateful processing emerges (CEP, windowed joins)
- Query patterns shift to direct Bronze table queries

**Architecture Preservation**:
✅ **Bronze per-exchange pattern (ADR-002) supports either engine**
- Flink or Spark can write to Bronze Iceberg tables
- Migration path preserved without redesign

### 10.2 Success Metrics

**Short-Term** (Phase 11):
- Bronze Binance p99 latency < 5 seconds
- Bronze Kraken p99 latency < 10 seconds
- Zero increase in operational incidents
- Cost: $0 incremental

**Long-Term** (if Flink adopted):
- Bronze latency < 500ms p99 (when required)
- Exactly-once guarantee (when required)
- Operational overhead < 60% increase
- Team Flink proficiency within 3 weeks

### 10.3 Risk Mitigation

**Spark Optimization Risks**:
- Risk: 5s trigger causes instability
- Mitigation: Test in staging, monitor for 1 week before production

**Flink Adoption Risks** (if triggered):
- Risk: Operational complexity underestimated
- Mitigation: Phased rollout (PoC → validation → production)

- Risk: Flink-Iceberg integration issues
- Mitigation: Use Flink 1.17+ (proven in production at Uber, Netflix)

- Risk: Team learning curve delays migration
- Mitigation: Dedicated training period, hire Flink expert

---

## 11. Related Documentation

### Platform Principles
- [Platform Principles](../architecture/platform-principles.md) - Boring technology, idempotency principles
- [Latency Budgets](../operations/performance/latency-budgets.md) - 500ms p99 budget breakdown
- [Technology Stack](../architecture/technology-stack.md) - When to replace components

### Current Implementation
- [Query Architecture](./query-architecture.md) - Hybrid query mode (Kafka + Iceberg)
- [Consistency Model](./data-guarantees/consistency-model.md) - At-least-once + bounded staleness
- [ADR-002: Bronze Per Exchange](../architecture/decisions/ADR-002-bronze-per-exchange.md) - Per-exchange design

### Phase 10 Context
- [Phase 10 Progress](../phases/phase-10-streaming-crypto/PROGRESS.md) - Current implementation status
- [Phase 10 Decisions](../phases/phase-10-streaming-crypto/DECISIONS.md) - Recent ADRs
- [Bronze Binance Job](../../src/k2/spark/jobs/streaming/bronze_binance_ingestion.py) - Current Spark implementation

---

**Maintained By**: Platform Architecture Team
**Review Frequency**: Quarterly or when latency requirements change
**Last Review**: 2026-01-19
**Next Review**: 2026-04-19 (or when sub-second latency becomes business-critical)
