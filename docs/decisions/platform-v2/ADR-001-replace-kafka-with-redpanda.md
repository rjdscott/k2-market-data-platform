# ADR-001: Replace Apache Kafka with Redpanda

**Status:** Proposed
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Streaming Infrastructure

---

## Context

The current platform runs Apache Kafka 3.7 in KRaft mode with a separate Confluent Schema Registry (8.1.1). Together, these consume **2.0 CPU / 2.77GB RAM** (Kafka: 1.5 CPU / 2GB, Schema Registry: 0.5 CPU / 768MB). On a 16-core / 40GB budget, that's 12.5% of our CPU and 7% of our RAM just for the message broker layer — before a single message is processed.

Kafka's JVM-based architecture introduces:
- **GC pauses** that add p99 tail latency spikes (10-50ms observed in production Kafka clusters)
- **Memory overhead** from JVM heap, metaspace, and off-heap buffers
- **Operational complexity** despite KRaft removing Zookeeper
- **Separate Schema Registry** as an additional service to manage

We need a Kafka-compatible streaming backbone that fits within our constrained resource envelope while reducing end-to-end latency.

## Decision

**Replace Apache Kafka + Confluent Schema Registry with Redpanda.**

Redpanda is a Kafka API-compatible streaming platform written in C++ using the Seastar framework (thread-per-core architecture). It includes a built-in Schema Registry, eliminating the need for a separate service.

## Rationale

### Resource Reduction

| Metric | Kafka + Schema Registry | Redpanda | Savings |
|--------|------------------------|----------|---------|
| CPU | 2.0 cores | 1.0 core | **50%** |
| Memory | 2.77GB | 1.5GB | **46%** |
| Services | 2 (Kafka + SR) | 1 | **50%** |
| JVM tuning | Required | N/A | Eliminated |
| Startup time | 15-30s | 2-5s | **80%** |

### Latency Improvement

Redpanda's thread-per-core architecture (Seastar framework) eliminates:
- **JVM garbage collection pauses**: Kafka's G1GC pauses at 10-50ms p99 are eliminated entirely
- **Context switching overhead**: Seastar pins one thread per core, eliminating OS scheduler interference
- **Lock contention**: Lock-free data structures vs. Kafka's synchronized partition handling

**Published benchmarks** (Redpanda vs. Kafka, single-node):
- p50 latency: **~2ms vs ~5ms** (60% reduction)
- p99 latency: **~5ms vs ~30ms** (83% reduction)
- p99.9 latency: **~10ms vs ~100ms+** (90% reduction)

Sources:
- [Redpanda vs. Kafka Benchmark (Jepsen)](https://redpanda.com/blog/redpanda-vs-kafka-performance-benchmark)
- [Redpanda Architecture Whitepaper](https://redpanda.com/guides/kafka-alternatives/redpanda-vs-apache-kafka)

### Built-in Schema Registry

Redpanda includes a Kafka-compatible Schema Registry that supports:
- Avro, Protobuf, and JSON Schema
- BACKWARD, FORWARD, FULL compatibility modes
- Same REST API as Confluent Schema Registry
- **Eliminates a separate service** and its resource consumption

### Kafka API Compatibility

Redpanda implements the Kafka protocol natively:
- All existing Kafka clients (Java, Kotlin, Python) work without code changes
- Consumer groups, exactly-once semantics, transactions all supported
- Topic configuration (partitions, replication, retention) identical
- AdminClient API compatible

### Operational Simplicity

- **Single binary** — no JVM, no Zookeeper, no KRaft controller
- **Self-tuning** — automatically sizes I/O queues, memory, and CPU scheduling
- **Built-in Redpanda Console** — replaces Kafka UI (another service eliminated)
- **Tiered storage** — native S3/MinIO tiered storage for topic data offloading

## Alternatives Considered

### 1. Keep Kafka, Optimize Configuration
- **Pro**: Zero migration effort
- **Con**: JVM overhead is architectural; tuning cannot eliminate GC pauses or memory floor
- **Con**: Still need separate Schema Registry
- **Verdict**: Rejected — cannot meet resource budget

### 2. Apache Pulsar
- **Pro**: Multi-tenancy, built-in tiered storage
- **Con**: Even heavier than Kafka (Bookkeeper + broker = 2 JVM processes)
- **Con**: Different client API — requires code rewrite
- **Con**: More complex operational model
- **Verdict**: Rejected — worse resource profile, higher migration cost

### 3. NATS JetStream
- **Pro**: Extremely lightweight (~50MB RAM)
- **Con**: No Kafka protocol compatibility — full client rewrite needed
- **Con**: Less mature ecosystem for data engineering (limited connector ecosystem)
- **Con**: No built-in Schema Registry
- **Verdict**: Rejected — migration cost too high, weaker ecosystem

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Kafka API edge-case incompatibility | Low | Medium | Run integration test suite against Redpanda before migration; Redpanda passes Kafka's own test suite |
| Smaller community than Kafka | Medium | Low | Redpanda has commercial support; active open-source community; Kafka knowledge transfers |
| Schema Registry feature gap | Low | Low | We use basic BACKWARD compatibility only; well within Redpanda's SR capabilities |
| C++ debugging harder than JVM | Low | Low | Redpanda provides extensive logging and built-in diagnostics (rpk cluster health) |

## Resource Budget Impact

```
Before: Kafka (1.5 CPU / 2GB) + Schema Registry (0.5 CPU / 768MB) + Kafka UI (0.5 CPU / 512MB)
        = 2.5 CPU / 3.28GB

After:  Redpanda (1.0 CPU / 1.5GB) with built-in SR and Console
        = 1.0 CPU / 1.5GB

Savings: 1.5 CPU / 1.78GB (60% CPU reduction, 54% RAM reduction)
```

## Consequences

### Positive
- Dramatic latency reduction (p99: 30ms → 5ms)
- 3 services consolidated into 1
- No JVM tuning required
- Faster cluster startup (useful for docker-compose dev cycles)
- Native tiered storage to MinIO/S3

### Negative
- Team needs to learn `rpk` CLI (replaces `kafka-topics.sh`, `kafka-console-consumer.sh`)
- Some Kafka-specific monitoring dashboards need updating
- Dependency on Redpanda Inc. for commercial features (mitigated: core is open-source BSL)

## References

- [Redpanda Documentation](https://docs.redpanda.com/)
- [Redpanda vs. Kafka Performance](https://redpanda.com/blog/redpanda-vs-kafka-performance-benchmark)
- [Seastar Framework](http://seastar.io/)
- [Redpanda Schema Registry](https://docs.redpanda.com/current/manage/schema-reg/)
- [Jepsen Analysis of Redpanda](https://jepsen.io/analyses/redpanda-21.10.1)
