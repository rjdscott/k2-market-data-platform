# ADR-004: Eliminate Spark Streaming — Replace with Kotlin Stream Processors

**Status:** Proposed
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Stream Processing

---

## Context

The current platform runs **5 always-on Spark Structured Streaming jobs** for the medallion architecture:

| Job | CPU | RAM | Purpose |
|-----|-----|-----|---------|
| bronze-binance-stream | 1.0 | 2GB | Raw Avro → Bronze Iceberg |
| bronze-kraken-stream | 1.0 | 2GB | Raw Avro → Bronze Iceberg |
| silver-binance-transformation | 1.0 | 2GB | Bronze → Silver (validation) |
| silver-kraken-transformation | 1.0 | 2GB | Bronze → Silver (validation) |
| gold-aggregation | 1.0 | 2GB | Silver → Gold (unification) |
| **Total** | **5.0** | **10GB** | |

Additionally, Spark Master and Workers consume:

| Service | CPU | RAM |
|---------|-----|-----|
| Spark Master | 2.0 | 2GB |
| Spark Worker 1 | 3.5 | 4GB |
| Spark Worker 2 | 3.5 | 4GB |
| **Total** | **9.0** | **10GB** |

**Combined Spark footprint: 14.0 CPU / 20GB RAM** — this is **87.5% of our 16-core CPU budget and 50% of our 40GB RAM budget**, consumed entirely by stream processing infrastructure.

The streaming transformations performed are relatively simple:
1. **Bronze**: Deserialize Avro, write to storage (pass-through)
2. **Silver**: Validate fields, normalize schema, apply data quality rules
3. **Gold**: Deduplicate across exchanges, unify schemas, apply watermarks

None of these require Spark's distributed computation capabilities. They are **stateless, per-record transformations** that a simple consumer application can handle.

## Decision

**Eliminate all Spark Structured Streaming jobs. Replace with lightweight Kotlin stream processors using the Kafka Streams library or direct Redpanda consumers.**

The medallion architecture is preserved, but implemented as:
- **Bronze**: ClickHouse Kafka Engine (direct Redpanda → ClickHouse ingestion, see ADR-003)
- **Silver**: Kotlin stream processor (validation + enrichment)
- **Gold**: ClickHouse Materialized Views (aggregation + deduplication)

## Rationale

### The Problem with Spark for Simple Streaming

Spark Structured Streaming is designed for **distributed, stateful stream processing** across clusters of machines. Our use case is:
- **Single-node** Docker Compose deployment
- **Stateless** per-record transformations (no windowed aggregations in the stream layer)
- **Low throughput** (~138 msg/sec per exchange — trivial for any consumer)
- **Simple logic** (deserialize → validate → write)

Using Spark for this is like using a semi-truck to deliver a pizza:

| Spark Overhead | Cost | Needed? |
|----------------|------|---------|
| Driver process | ~1GB RAM, 1 CPU | No — no job coordination needed |
| Executor JVMs | ~2GB RAM each | No — single-node, no distribution |
| Shuffle service | CPU + disk | No — no shuffles in our transforms |
| Checkpoint to S3/MinIO | ~2s per micro-batch | No — Kafka offsets provide replay |
| Catalyst optimizer | CPU + memory | No — transforms are trivial, not SQL-optimizable |
| DAG scheduling | CPU | No — linear pipeline, no DAG |

### Kotlin Stream Processor Architecture

```kotlin
@Component
class SilverStreamProcessor(
    private val consumer: KafkaConsumer<String, GenericRecord>,
    private val clickhouse: ClickHouseClient,
    private val metrics: MeterRegistry
) {
    fun process() = runBlocking(Dispatchers.IO) {
        consumer.subscribe(listOf("market.crypto.trades.*.raw"))

        while (isActive) {
            val records = consumer.poll(Duration.ofMillis(100))
            val validated = records.mapNotNull { record ->
                validateAndEnrich(record.value())
            }

            if (validated.isNotEmpty()) {
                clickhouse.insertBatch("trades", validated)
                consumer.commitSync()
                metrics.counter("trades.validated").increment(validated.size.toDouble())
            }
        }
    }

    private fun validateAndEnrich(record: GenericRecord): Trade? {
        // Type-safe validation at compile time
        val price = record.get("price") as? Double ?: return null
        val quantity = record.get("quantity") as? Double ?: return null
        if (price <= 0 || quantity <= 0) return null

        return Trade(
            exchange = record.get("exchange") as String,
            symbol = normalizeSymbol(record.get("symbol") as String),
            price = price.toBigDecimal(),
            quantity = quantity.toBigDecimal(),
            timestamp = Instant.ofEpochMilli(record.get("timestamp") as Long),
            side = record.get("side") as String
        )
    }
}
```

### Resource Comparison

| Component | Spark Streaming (current) | Kotlin Processors | Savings |
|-----------|--------------------------|-------------------|---------|
| Bronze ingestion | 2.0 CPU / 4GB | 0 (ClickHouse Kafka Engine) | **100%** |
| Silver validation | 2.0 CPU / 4GB | 0.5 CPU / 256MB | **75% CPU, 94% RAM** |
| Gold aggregation | 1.0 CPU / 2GB | 0 (ClickHouse MVs) | **100%** |
| Spark Master | 2.0 CPU / 2GB | 0 (eliminated) | **100%** |
| Spark Workers | 7.0 CPU / 8GB | 0 (eliminated) | **100%** |
| **Total** | **14.0 CPU / 20GB** | **0.5 CPU / 256MB** | **96% CPU, 99% RAM** |

This is the single largest resource saving in the entire redesign.

### Revised Medallion Architecture

```
                        ┌──────────────────────────────────────┐
                        │          REDPANDA                     │
                        │  market.crypto.trades.*.raw           │
                        └──────────┬───────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                              ▼
          ┌─────────────────┐           ┌─────────────────┐
          │ Kotlin Silver   │           │ ClickHouse      │
          │ Processor       │           │ Kafka Engine    │
          │ (validate +     │           │ (raw → bronze   │
          │  normalize)     │           │  table)         │
          └────────┬────────┘           └─────────────────┘
                   │                         BRONZE LAYER
                   ▼
          ┌─────────────────┐
          │ ClickHouse      │
          │ trades table    │◄─── validated, normalized
          │ (warm storage)  │     SILVER LAYER
          └────────┬────────┘
                   │ Materialized Views (on insert)
                   ▼
          ┌─────────────────┐
          │ ohlcv_1m        │
          │ ohlcv_5m        │
          │ ohlcv_1h        │
          │ ohlcv_1d        │◄─── pre-computed aggregations
          │ (MV tables)     │     GOLD LAYER
          └─────────────────┘
```

**Bronze**: Raw data lands in ClickHouse via Kafka Engine (zero application code)
**Silver**: Kotlin processor validates, normalizes, and writes to ClickHouse trades table
**Gold**: ClickHouse Materialized Views compute OHLCV on every insert (zero batch jobs)

### Latency Improvement

| Stage | Spark Streaming | Kotlin + ClickHouse | Improvement |
|-------|----------------|---------------------|-------------|
| Bronze ingestion | 2-5s (micro-batch + checkpoint) | <100ms (Kafka Engine) | **20-50x** |
| Silver validation | 2-5s (micro-batch + checkpoint) | <10ms (per-record) | **200-500x** |
| Gold aggregation | 2-5s (micro-batch + checkpoint) | <1ms (MV on insert) | **2000-5000x** |
| **End-to-end** | **6-15s** | **<200ms** | **30-75x** |

The elimination of Spark's micro-batch model (which batches for 2-5 seconds, then checkpoints) is the primary latency reduction.

## Alternatives Considered

### 1. Apache Flink (Replace Spark Streaming)
- **Pro**: True stream processing (not micro-batch), lower latency than Spark
- **Con**: Still heavy — minimum ~2GB RAM per TaskManager
- **Con**: Operationally complex (JobManager + TaskManager + state backend)
- **Con**: Overkill for stateless per-record transforms
- **Verdict**: Rejected — still too heavy for our workload

### 2. Kafka Streams Library (in Kotlin)
- **Pro**: Lightweight library (not a framework), embeds in application
- **Pro**: Exactly-once semantics, state stores, interactive queries
- **Con**: Slight overhead vs raw consumer for stateless transforms
- **Decision**: Viable option B if we need stateful processing later; start with raw consumers

### 3. Keep Spark, Reduce Resources
- **Pro**: No code changes
- **Con**: Spark has a resource floor — Master needs ~2GB, each executor ~2GB minimum
- **Con**: 5 streaming jobs × minimum ~1GB = 5GB floor just for executors
- **Con**: Checkpoint overhead remains
- **Verdict**: Rejected — cannot reduce below ~8-10GB for Spark streaming

### 4. ksqlDB / Redpanda Transforms
- **Pro**: SQL-based stream processing, no application code
- **Con**: ksqlDB requires Kafka Streams cluster (heavy)
- **Con**: Redpanda Data Transforms (WASM) are still early/limited
- **Verdict**: Not yet mature enough; revisit when Redpanda WASM transforms stabilize

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Loss of exactly-once semantics | Low | High | ClickHouse deduplication via ReplacingMergeTree + idempotent inserts; Kafka consumer offset management |
| Kotlin processor crashes lose in-flight data | Low | Medium | Kafka retention provides replay; consumer offset commit after successful ClickHouse insert |
| Need stateful processing later | Medium | Medium | Kafka Streams library can be added to Kotlin processor without architectural change |
| ClickHouse Kafka Engine limitations | Low | Medium | Fall back to Option B (Kotlin consumer → ClickHouse JDBC) if edge cases arise |

## Resource Budget Impact

```
Before: Spark Master (2 CPU/2GB) + 2 Workers (7 CPU/8GB) + 5 Streaming Jobs (5 CPU/10GB)
        = 14.0 CPU / 20GB

After:  Kotlin Silver Processor (0.5 CPU / 256MB)
        = 0.5 CPU / 256MB

Savings: 13.5 CPU / 19.75GB (96.4% CPU reduction, 98.7% RAM reduction)
```

**This single decision recovers most of the resource budget needed for the entire platform.**

## Consequences

### Positive
- Eliminates the platform's largest resource consumer
- Reduces end-to-end streaming latency from 6-15s to <200ms
- Removes Spark operational complexity (Master/Worker management, checkpoint tuning)
- Simplifies deployment (fewer containers, fewer failure modes)
- Bronze + Gold layers become "zero-code" (ClickHouse engines + MVs)

### Negative
- Loss of Spark's distributed processing capability (acceptable for single-node)
- Must implement consumer offset management and error handling in Kotlin
- Medallion architecture is now split across ClickHouse (Bronze, Gold) and Kotlin (Silver)
- If we need complex stateful transformations later, may need to add Kafka Streams

## References

- [Spark Structured Streaming Internals](https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html)
- [ClickHouse Kafka Engine](https://clickhouse.com/docs/en/engines/table-engines/integrations/kafka)
- [Kafka Streams Architecture](https://kafka.apache.org/documentation/streams/architecture)
- [Micro-batch vs True Streaming](https://www.confluent.io/blog/apache-flink-apache-spark-dataflow-stream-processing-comparison/)
