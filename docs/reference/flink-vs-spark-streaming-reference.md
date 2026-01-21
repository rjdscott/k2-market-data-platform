# Apache Flink vs Spark Structured Streaming - Reference Comparison

**Last Updated**: 2026-01-19
**Purpose**: Quick reference for stream processing engine selection
**Target Audience**: Engineers, architects making technology decisions
**Related**: [Stream Processing Evaluation](../design/stream-processing-evaluation.md), [ADR-003](../architecture/decisions/ADR-003-stream-processing-engine-selection.md)

---

## Quick Decision Matrix

| Requirement | Use Spark | Use Flink |
|-------------|-----------|-----------|
| **Latency < 1s required** | ❌ | ✅ |
| **Latency 2-10s acceptable** | ✅ | ⚠️ Overkill |
| **Exactly-once critical** | ⚠️ Complex | ✅ Native |
| **At-least-once acceptable** | ✅ | ✅ |
| **Team has Spark expertise** | ✅ | ⚠️ Learning curve |
| **Team has Flink expertise** | ✅ | ✅ |
| **Operational simplicity** | ✅ | ❌ More complex |
| **Stateful processing (CEP)** | ⚠️ Limited | ✅ Native |
| **Unified batch + streaming** | ✅ | ⚠️ Separate |
| **Cost-sensitive** | ✅ Lower ops | ❌ Higher ops |

**K2 Platform Decision**: ✅ **Spark** (meets all requirements with lower operational overhead)

---

## Feature Comparison Matrix

### 1. Processing Model

| Feature | Spark Structured Streaming | Apache Flink |
|---------|---------------------------|--------------|
| **Processing Paradigm** | Micro-batch (default) | Event-at-a-time (true streaming) |
| **Minimum Latency** | 2-5 seconds (optimized) | 100-500ms |
| **Latency Variance** | Higher (batch boundaries) | Lower (continuous) |
| **Throughput** | Very High (10M+ msg/sec) | Very High (10M+ msg/sec) |
| **Trigger Options** | Fixed interval, continuous (experimental) | Continuous (default) |
| **Checkpointing** | Incremental (RocksDB) | Incremental (RocksDB) |

**Winner**: **Flink** (lower latency) | **Spark** (higher throughput, simpler model)

---

### 2. State Management

| Feature | Spark Structured Streaming | Apache Flink |
|---------|---------------------------|--------------|
| **Stateful Operations** | Supported (aggregations, joins) | Native (windowing, CEP, joins) |
| **State Backend** | RocksDB, HDFS | RocksDB, Heap, HDFS |
| **Fault Tolerance** | Checkpoint-based | Checkpoint + savepoint |
| **State Evolution** | Manual migration | Savepoint-based migration |
| **Complex Event Processing (CEP)** | Limited (custom UDFs) | Native CEP library |
| **Windowing** | Fixed, sliding, session | Fixed, sliding, session, custom |

**Winner**: **Flink** (richer state management, native CEP)

---

### 3. Semantics and Guarantees

| Feature | Spark Structured Streaming | Apache Flink |
|---------|---------------------------|--------------|
| **Delivery Guarantee** | At-least-once (default), exactly-once (with Kafka transactions) | Exactly-once (default with checkpointing) |
| **Exactly-Once Complexity** | High (requires Kafka transactions + idempotent sinks) | Low (native two-phase commit) |
| **Deduplication** | Manual (merge-on-read by message_id) | Optional (handled by exactly-once) |
| **Idempotency Support** | ✅ Strong (manual implementation) | ✅ Strong (native) |
| **Ordering Guarantees** | Per-partition (Kafka) | Per-partition (Kafka) + event-time ordering |
| **Late Data Handling** | Watermarks (manual) | Watermarks (native) |

**Winner**: **Flink** (simpler exactly-once) | **Spark** (aligns with platform idempotency principle)

---

### 4. Integration and Ecosystem

| Feature | Spark Structured Streaming | Apache Flink |
|---------|---------------------------|--------------|
| **Kafka Integration** | Native (spark-sql-kafka-0-10) | Native (flink-connector-kafka) |
| **Schema Registry** | Manual (Avro deserializer) | Manual (Avro deserializer) |
| **Iceberg Support** | Native (Spark 3.x) | Native (Flink 1.17+) |
| **Iceberg Maturity** | ✅ Very Mature | ✅ Mature (since 1.17) |
| **ACID Guarantees** | ✅ Full support | ✅ Full support |
| **Format Support** | Avro, Parquet, ORC, JSON | Avro, Parquet, ORC, JSON |
| **Catalog Support** | Hive, Glue, REST | Hive, Glue, REST |

**Winner**: **Tie** (both have production-ready Iceberg support)

---

### 5. Operational Complexity

| Feature | Spark Structured Streaming | Apache Flink |
|---------|---------------------------|--------------|
| **Cluster Architecture** | Master + Workers (simple) | JobManager + TaskManagers (more complex) |
| **Resource Management** | YARN, Kubernetes, Standalone | YARN, Kubernetes, Standalone |
| **Monitoring** | Spark UI, Prometheus metrics | Flink Dashboard, Prometheus metrics |
| **Debugging** | Familiar (Spark logs, UI) | Different mental model (backpressure, watermarks) |
| **Runbooks** | Mature (10+ years) | Growing (5+ years) |
| **Failure Modes** | Well-understood (batch timeouts) | Different (checkpoint timeouts, backpressure) |
| **Upgrade Complexity** | Low (replace JAR, restart) | Medium (savepoint required) |
| **State Migration** | Manual checkpoint management | Savepoint-based (easier) |

**Winner**: **Spark** (simpler operations, team-familiar)

---

### 6. Performance Characteristics

| Metric | Spark Structured Streaming | Apache Flink | K2 Target |
|--------|---------------------------|--------------|-----------|
| **Ingestion Latency (p50)** | 3-5s | 200-500ms | < 10s |
| **Ingestion Latency (p99)** | 5-10s | 500ms-1s | < 30s |
| **Throughput (per node)** | 100K-500K msg/sec | 100K-500K msg/sec | 10K+ |
| **Memory Overhead** | Medium (JVM heap) | Medium (JVM heap) | - |
| **CPU Utilization** | High (batch processing) | Medium (continuous) | - |
| **Checkpoint Overhead** | 5-10% | 5-10% | - |

**K2 Status**: ✅ **Spark meets all targets** (5-10s << 30s target)

---

### 7. Development Experience

| Feature | Spark Structured Streaming | Apache Flink |
|---------|---------------------------|--------------|
| **API Language** | Scala, Java, Python (PySpark) | Java, Scala, Python (PyFlink) |
| **API Maturity** | Very Mature | Mature |
| **Learning Curve** | Medium (DataFrame API familiar) | Medium-High (DataStream API) |
| **SQL Support** | ✅ Excellent (Spark SQL) | ✅ Good (Flink SQL, Table API) |
| **IDE Support** | Excellent (IntelliJ, VS Code) | Good (IntelliJ, VS Code) |
| **Local Testing** | Easy (`spark-submit --master local`) | Medium (requires mini-cluster or test harness) |
| **Unit Testing** | Easy (DataFrameReader mocks) | Medium (TestStreamEnvironment) |

**Winner**: **Spark** (easier local dev, more familiar to team)

---

### 8. Cost Analysis

| Cost Category | Spark Structured Streaming | Apache Flink | K2 Impact |
|---------------|---------------------------|--------------|-----------|
| **Infrastructure (compute)** | $200-300/month (3 workers) | $200-300/month (3 TaskManagers) | Similar |
| **Infrastructure (storage)** | Checkpoint: $50/month | Checkpoint + state: $100/month | +$50/mo |
| **Engineering Time (initial)** | 0 weeks (already implemented) | 14 weeks (PoC + migration) | +$50K |
| **Engineering Time (ongoing)** | 2 hours/week (maintenance) | 4 hours/week (maintenance) | +$10K/year |
| **Training/Ramp-up** | $0 (team familiar) | $5K (training, hiring) | +$5K |
| **Monitoring/Tooling** | $0 (existing dashboards) | $2K (new dashboards, alerts) | +$2K |
| **Total First Year** | $3,600 | $70,600 | **+$67K** |

**Winner**: **Spark** (67% lower total cost)

---

### 9. Use Case Fit

#### When to Use Spark Structured Streaming

✅ **Ideal for**:
- Analytics and research platforms (L2/L3 data paths)
- Latency requirements: 2-10 seconds acceptable
- Team has Spark expertise
- Cost-sensitive environments
- Unified batch + streaming workloads
- At-least-once + idempotency model

❌ **Not ideal for**:
- Sub-second latency required (HFT, real-time trading)
- Complex event processing (CEP, pattern matching)
- Exactly-once semantics critical without manual effort

#### When to Use Apache Flink

✅ **Ideal for**:
- Real-time applications (sub-second latency critical)
- Complex stateful processing (windowed joins, CEP)
- Exactly-once semantics required (financial aggregations)
- Event-time processing with late data handling
- Team has Flink expertise

❌ **Not ideal for**:
- Simple ETL pipelines (overkill)
- Cost-sensitive environments (40-60% higher ops cost)
- Teams without stream processing expertise
- Unified batch + streaming preferred (Spark better fit)

---

## 10. K2 Platform Specific Comparison

### Current Architecture (Phase 10)

```
Kafka (market.crypto.trades.binance.raw)
  ↓
Spark Structured Streaming (10s micro-batch)
  ↓
Bronze Iceberg (bronze_binance_trades)
  ↓
Query Layer (DuckDB)
```

**Performance**:
- Latency: 5-10s p99 (Target: <30s) ✅
- Throughput: 10K+ msg/sec (Target: Variable) ✅
- Operational complexity: Low (single engine)

### Hypothetical Flink Architecture

```
Kafka (market.crypto.trades.binance.raw)
  ↓
Apache Flink (event-at-a-time)
  ↓
Bronze Iceberg (bronze_binance_trades)
  ↓
Query Layer (DuckDB)
```

**Expected Performance**:
- Latency: 100-500ms p99 (Improvement: 10-20x)
- Throughput: 10K+ msg/sec (Same)
- Operational complexity: Medium-High (new engine)

**Cost-Benefit**:
- Benefit: 10-20x latency improvement (5-10s → 100-500ms)
- Cost: $67K first year + 40-60% ongoing operational overhead
- **ROI**: Negative (sub-second latency NOT business-critical for K2)

---

## 11. Migration Considerations

### Spark → Flink Migration Effort

| Task | Effort | Risk | Notes |
|------|--------|------|-------|
| **Proof of Concept** | 4 weeks | Low | Parallel deployment, validate Iceberg integration |
| **Data Consistency Validation** | 2 weeks | Medium | Message-level comparison (Spark vs Flink output) |
| **Monitoring/Alerting Setup** | 2 weeks | Low | New dashboards, Flink metrics |
| **Team Training** | 2 weeks | Low | Flink APIs, state management, debugging |
| **Production Migration** | 4 weeks | Medium | Cutover strategy, rollback plan |
| **Validation and Tuning** | 2 weeks | Low | Performance tuning, edge case handling |

**Total Effort**: 16 weeks (~4 months)
**Estimated Cost**: $60K engineering time + $10K infrastructure

### Reversibility

**Spark → Flink**:
- Difficulty: Medium
- Risk: Medium (new operational patterns)
- Rollback: Easy (revert to Spark, checkpoints preserved)

**Flink → Spark**:
- Difficulty: Easy
- Risk: Low (Spark is proven)
- Effort: 1 week (revert configuration)

---

## 12. Vendor and Community Support

### Spark Structured Streaming

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Community Size** | ⭐⭐⭐⭐⭐ | Massive (10+ years, Databricks backing) |
| **Documentation** | ⭐⭐⭐⭐⭐ | Excellent (Databricks + Apache) |
| **Enterprise Support** | ⭐⭐⭐⭐⭐ | Databricks, Cloudera, AWS EMR |
| **Stack Overflow** | ⭐⭐⭐⭐⭐ | 50K+ questions |
| **Release Cadence** | ⭐⭐⭐⭐ | Regular (every 3-6 months) |
| **Backward Compatibility** | ⭐⭐⭐⭐⭐ | Strong (careful deprecation) |

### Apache Flink

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Community Size** | ⭐⭐⭐⭐ | Growing (Alibaba, Uber, Netflix backing) |
| **Documentation** | ⭐⭐⭐⭐ | Good (Apache Flink + Ververica) |
| **Enterprise Support** | ⭐⭐⭐⭐ | Ververica (Confluent), AWS Kinesis Data Analytics |
| **Stack Overflow** | ⭐⭐⭐ | 15K+ questions (smaller than Spark) |
| **Release Cadence** | ⭐⭐⭐⭐ | Regular (every 4-6 months) |
| **Backward Compatibility** | ⭐⭐⭐ | Good (savepoint-based migration) |

---

## 13. Quick Reference: Command Comparison

### Spark Structured Streaming (PySpark)

```python
# Read from Kafka
kafka_df = (
    spark.readStream
    .format("kafka")
    .option("kafka.bootstrap.servers", "kafka:9092")
    .option("subscribe", "market.crypto.trades.binance")
    .load()
)

# Write to Iceberg
(
    kafka_df
    .selectExpr("CAST(value AS STRING) as avro_payload")
    .writeStream
    .format("iceberg")
    .option("checkpointLocation", "/checkpoints/bronze-binance/")
    .trigger(processingTime="5 seconds")
    .toTable("iceberg.market_data.bronze_binance_trades")
)
```

### Apache Flink (Java)

```java
// Read from Kafka
FlinkKafkaConsumer<String> kafkaSource = new FlinkKafkaConsumer<>(
    "market.crypto.trades.binance",
    new SimpleStringSchema(),
    kafkaProperties
);

DataStream<String> stream = env.addSource(kafkaSource);

// Write to Iceberg
TableLoader tableLoader = TableLoader.fromCatalog(
    catalogLoader,
    TableIdentifier.of("market_data", "bronze_binance_trades")
);

FlinkSink.forRowData(stream)
    .table(icebergTable)
    .tableLoader(tableLoader)
    .equalityFieldColumns(Arrays.asList("message_id"))
    .build();

env.execute("Bronze Binance Ingestion");
```

---

## 14. Summary Recommendation

### For K2 Platform (Current State)

**Recommendation**: ✅ **Continue with Spark Structured Streaming**

**Reasons**:
1. ✅ Meets all latency requirements (5-10s << 30s target)
2. ✅ Aligns with platform principles (boring tech, idempotency)
3. ✅ Lower operational overhead (40-60% savings)
4. ✅ Team expertise (Spark-familiar)
5. ✅ Cost-efficient ($67K first-year savings vs Flink)

**When to Reconsider**:
- Sub-second latency becomes business-critical (NOT current)
- Complex CEP required (windowed joins, pattern matching)
- Team hires Flink expert (reduces learning curve)
- Query patterns shift to direct Bronze queries (vs Kafka tail)

### For New Projects

**Decision Tree**:
```
Is sub-second latency required?
├─ Yes → Use Flink
└─ No → Is unified batch + streaming needed?
    ├─ Yes → Use Spark
    └─ No → Is exactly-once critical?
        ├─ Yes → Use Flink
        └─ No → Use Spark (simpler)
```

---

## 15. Related Documentation

### Technical Deep-Dives
- [Stream Processing Evaluation](../design/stream-processing-evaluation.md) - Comprehensive analysis
- [ADR-003: Engine Selection](../architecture/decisions/ADR-003-stream-processing-engine-selection.md) - Decision rationale
- [Platform Principles](../architecture/platform-principles.md) - Boring technology principle

### Implementation Guides
- [Bronze Binance Job](../../src/k2/spark/jobs/streaming/bronze_binance_ingestion.py) - Current Spark implementation
- [Latency Budgets](../operations/performance/latency-budgets.md) - Performance targets
- [Query Architecture](../design/query-architecture.md) - Kafka tail + Iceberg pattern

### External Resources
- [Apache Spark Streaming Guide](https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html)
- [Apache Flink Documentation](https://nightlies.apache.org/flink/flink-docs-stable/)
- [Flink-Iceberg Connector](https://iceberg.apache.org/docs/latest/flink/)
- [Uber Engineering: Flink at Scale](https://eng.uber.com/scaling-flink/)
- [Netflix: Flink for Streaming Analytics](https://netflixtechblog.com)

---

**Last Updated**: 2026-01-19
**Maintained By**: Platform Architecture Team
**Review Frequency**: Quarterly or when requirements change
**Next Review**: 2026-04-19

---

**Quick Lookup Tags**: `#flink` `#spark` `#streaming` `#comparison` `#reference` `#decision-support`
