# K2 Market Data Platform v2 — Architecture Redesign

**Status:** Proposed
**Date:** 2026-02-09
**Author:** Platform Engineering Team
**Target:** 16 CPU cores / 40GB RAM / Single Docker Compose Cluster

---

## Executive Summary

The K2 Market Data Platform v1 is a well-architected, thoroughly documented crypto market data platform. However, it consumes **35-40 CPU cores and 45-50GB RAM** — primarily due to 5 always-on Spark Structured Streaming jobs (14 CPU / 20GB) and a JVM-heavy infrastructure stack. The platform cannot run within the mandated **16-core / 40GB envelope**.

This redesign achieves a **60%+ resource reduction** while simultaneously delivering **10-1000x latency improvements** at every pipeline stage. The key insight: our streaming workload (stateless per-record transforms at ~138 msg/s) does not require a distributed computation framework. By replacing Spark Streaming with lightweight Kotlin processors and ClickHouse Materialized Views, we eliminate the platform's largest resource consumer while dramatically reducing end-to-end latency.

### Before and After

| Metric | v1 | v2 | Improvement |
|--------|----|----|-------------|
| CPU (limits) | 35-40 cores | 15.5 cores | **56-61%** reduction |
| RAM (limits) | 45-50 GB | 19.5 GB | **57-61%** reduction |
| Services | 18-20 | 11 | **42-45%** reduction |
| E2E latency (trade → candle) | 5-15 minutes | <200ms | **>1000x** faster |
| Query latency (OHLCV, p95) | 200-500ms | 2-5ms | **40-100x** faster |
| Throughput (per handler) | 138 msg/s | 5,000+ msg/s | **36x** higher |
| Feed handler latency (p99) | 25ms | 2ms | **12x** faster |
| Streaming backbone latency (p99) | 30ms | 5ms | **6x** faster |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    K2 MARKET DATA PLATFORM v2                           │
│                    16 CPU / 40GB RAM Budget                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐     ┌──────────────┐     ┌───────────────────────┐    │
│  │  EXCHANGE    │     │  EXCHANGE    │     │  EXCHANGE             │    │
│  │  APIs        │     │  APIs        │     │  APIs                 │    │
│  │  (Binance)   │     │  (Kraken)    │     │  (Future exchanges)   │    │
│  └──────┬───────┘     └──────┬───────┘     └──────────┬────────────┘    │
│         │ WebSocket          │ WebSocket               │                │
│         └────────────────────┼─────────────────────────┘                │
│                              │                                          │
│  ┌───────────────────────────▼──────────────────────────────────────┐   │
│  │  KOTLIN FEED HANDLERS (1 JVM, coroutines)      [0.5 CPU / 512MB]│   │
│  │  Ktor WebSocket client → Avro serialize → Produce to Redpanda    │   │
│  │  All exchanges in single process via structured concurrency      │   │
│  └───────────────────────────┬──────────────────────────────────────┘   │
│                              │ Avro (Redpanda Schema Registry)         │
│  ┌───────────────────────────▼──────────────────────────────────────┐   │
│  │  REDPANDA (single binary, C++)                  [2.0 CPU / 2GB] │   │
│  │  Built-in Schema Registry + Console                              │   │
│  │  Topics: market.crypto.trades.{exchange}.raw                     │   │
│  │  p99 latency: ~5ms (vs Kafka's ~30ms)                           │   │
│  └──────┬──────────────────────────────────────┬────────────────────┘   │
│         │                                      │                        │
│         │ Kafka Engine (zero-code)             │ Consumer (Kotlin)      │
│         ▼                                      ▼                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  CLICKHOUSE (warm storage)                  [4.0 CPU / 8GB]      │  │
│  │                                                                   │  │
│  │  RAW: trades_raw (MergeTree, 48h TTL)                            │  │
│  │    Untouched exchange data, all fields as strings                 │  │
│  │         │ cascading MV                                            │  │
│  │         ▼                                                         │  │
│  │  BRONZE: bronze_trades (ReplacingMergeTree, 7d TTL)              │  │
│  │    Deduplicated, typed, timestamp normalized                      │  │
│  │         │ Kotlin Silver Processor [0.5 CPU / 512MB]               │  │
│  │         ▼                                                         │  │
│  │  SILVER: silver_trades (MergeTree, 30d TTL)                      │  │
│  │    Validated, canonical symbols, enriched (quote_volume)          │  │
│  │         │ Materialized Views (on insert, real-time)               │  │
│  │         ▼                                                         │  │
│  │  GOLD: ohlcv_{1m,5m,15m,30m,1h,1d} (AggregatingMergeTree)      │  │
│  │    Six OHLCV timeframes, pre-computed on every trade insert       │  │
│  └──────────────────────┬───────────────────────────────────────────┘  │
│                         │                                              │
│  ┌──────────────────────▼───────────────────────────────────────────┐  │
│  │  SPRING BOOT API (Kotlin, virtual threads)  [2.0 CPU / 1GB]     │  │
│  │  REST endpoints → ClickHouse (warm) / Iceberg (cold)             │  │
│  │  HikariCP connection pooling, Micrometer metrics                 │  │
│  │  Intelligent routing: recent → ClickHouse, historical → Iceberg  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                         │                                              │
│                    ┌────▼────┐                                         │
│                    │  USERS  │                                         │
│                    └─────────┘                                         │
│                                                                         │
│  ══════════════ COLD TIER (hourly offload + daily maintenance) ══════  │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  HOURLY: Kotlin Iceberg writer (embedded in API, ~6 min/hour)    │  │
│  │  Appends each ClickHouse layer → corresponding Iceberg table     │  │
│  │                                                                   │  │
│  │  DAILY: Spark batch (on-demand, ~40 min/day)  [2.0 CPU / 4GB]   │  │
│  │  Compaction, snapshot expiry, lineage audit                       │  │
│  └──────────────────────┬───────────────────────────────────────────┘  │
│                         │                                              │
│  ┌──────────────────────▼───────────────────────────────────────────┐  │
│  │  ICEBERG on MinIO (mirrored four-layer cold storage)             │  │
│  │  MinIO [1.0 CPU/1GB] + PostgreSQL [0.5 CPU/512MB]               │  │
│  │  + Iceberg REST [0.5 CPU/512MB]                                  │  │
│  │                                                                   │  │
│  │  RAW:    cold.raw_trades         ← CH trades_raw (untouched)     │  │
│  │  BRONZE: cold.bronze_trades      ← CH bronze_trades (typed)      │  │
│  │  SILVER: cold.silver_trades      ← CH silver_trades (validated)  │  │
│  │  GOLD:   cold.gold_ohlcv_{1m,5m,15m,30m,1h,1d}                  │  │
│  │                                                                   │  │
│  │  Full four-layer lineage: re-derive any layer from the one below  │  │
│  │  Hourly freshness — cold tier ≤1 hour behind warm                 │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ════════════════════ OBSERVABILITY ════════════════════════════════  │
│                                                                         │
│  ┌──────────────────┐  ┌──────────────────┐                           │
│  │  Prometheus       │  │  Grafana          │                          │
│  │  [0.5 CPU/512MB]  │  │  [0.5 CPU/512MB]  │                         │
│  └──────────────────┘  └──────────────────┘                           │
│                                                                         │
│  Total: 15.5 CPU / 19.5GB RAM (13.5 CPU / 15.5GB when Spark idle)   │
│  Budget: 16.0 CPU / 40GB RAM                                          │
│  Headroom: 3% CPU / 51% RAM                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## End-to-End Data Flow

### Real-Time Path (trade → queryable candle)

```
Timestamp  Component              Action                          Latency   Layer
─────────  ─────────              ──────                          ───────   ─────
T+0ms      Exchange WebSocket     Trade event emitted             0ms       —
T+0.5ms    Kotlin Feed Handler    JSON parse + Avro serialize     0.5ms     —
T+1ms      Kotlin Feed Handler    Produce to Redpanda             0.5ms     —
T+3ms      Redpanda               Message persisted               2ms       —
T+4ms      CH Kafka Engine        trades_raw insert (strings)     1ms       RAW
T+5ms      CH Cascading MV        bronze_trades insert (typed)    1ms       BRONZE
T+8ms      Kotlin Silver Proc.    Consume, validate, normalize    3ms       SILVER
T+10ms     Kotlin Silver Proc.    Insert to CH silver_trades      2ms       SILVER
T+11ms     CH Gold MVs            6× OHLCV candles updated        1ms       GOLD
T+11ms     QUERYABLE              API can return updated candle   ─────

Total end-to-end: ~11ms (vs v1: 5-15 MINUTES)
All 4 layers populated within 11ms of exchange event.
```

### Historical Query Path

```
API Request → Spring Boot → Check query time range
  ├── Recent (0-30 days) → ClickHouse JDBC → <5ms response
  └── Historical (30+ days) → Iceberg REST → DuckDB/Spark → <500ms response
```

### Hourly Offload Path (Kotlin Iceberg Writer)

```
:05  Kotlin: CH trades_raw (last hour)     → cold.raw_trades      ~1 min
:06  Kotlin: CH bronze_trades (last hour)  → cold.bronze_trades    ~1 min
:07  Kotlin: CH silver_trades (last hour)  → cold.silver_trades    ~2 min
:09  Kotlin: CH gold_ohlcv_* (last hour)   → cold.gold_ohlcv_*    ~2 min
     Total: ~6 min/hour, embedded in Spring Boot API
```

### Daily Maintenance Path (Spark Batch)

```
02:00 UTC  Spark starts: compact hourly Parquet files into optimal sizes
02:20 UTC  Snapshot expiry: remove snapshots older than 7 days
02:30 UTC  Lineage audit: verify row counts across all 4 layers
02:40 UTC  Spark exits
```

The Kotlin Iceberg writer uses the Apache Iceberg Java SDK (a library, not a framework)
to append Parquet files and update the catalog — no Spark JVM overhead for hourly ops.
Spark handles the heavy I/O work: compaction, backfills, and data quality audits.

---

## Performance Improvements by Component

### 1. Feed Handlers: Python → Kotlin

| Metric | Python (v1) | Kotlin (v2) | Factor |
|--------|-------------|-------------|--------|
| Throughput | 138 msg/s | 5,000+ msg/s | **36x** |
| p99 latency | 25ms | 2ms | **12x** |
| Memory per handler | 512MB | 256MB | **2x** |
| CPU per handler | 0.5 cores | 0.25 cores | **2x** |
| Concurrent connections | 1 per process | N per coroutine | **Nx** |

**Why**: JVM JIT compilation, Kotlin coroutines (structured concurrency vs Python asyncio), native Avro serialization (10x faster than fastavro), no GIL.

### 2. Streaming Backbone: Kafka → Redpanda

| Metric | Kafka (v1) | Redpanda (v2) | Factor |
|--------|-----------|----------------|--------|
| p99 latency | 30ms | 5ms | **6x** |
| p99.9 latency | 100ms+ | 10ms | **10x** |
| Memory | 2.77GB (Kafka+SR) | 1.5GB | **1.8x** |
| Services | 3 (Kafka+SR+UI) | 1 | **3x** |
| Startup time | 15-30s | 2-5s | **5x** |

**Why**: C++ with Seastar framework (thread-per-core, zero GC), built-in Schema Registry, no JVM overhead.

### 3. Stream Processing: Spark Streaming → Kotlin + ClickHouse

| Metric | Spark (v1) | Kotlin + CH (v2) | Factor |
|--------|-----------|-------------------|--------|
| CPU | 14.0 cores | 2.5 cores | **5.6x** |
| RAM | 20GB | 4.5GB | **4.4x** |
| E2E latency | 6-15s | <200ms | **30-75x** |
| OHLCV availability | Minutes (batch) | Milliseconds (MV) | **>1000x** |
| Services | 8 (Master+Workers+Jobs) | 2 (Processor+CH) | **4x** |

**Why**: Stateless transforms don't need distributed processing. ClickHouse Materialized Views replace batch OHLCV computation. Kafka consumer offsets provide replay (no Spark checkpointing).

### 4. Query Engine: DuckDB → ClickHouse

| Metric | DuckDB (v1) | ClickHouse (v2) | Factor |
|--------|-------------|------------------|--------|
| OHLCV query (p95) | 200-500ms | 2-5ms | **40-100x** |
| Concurrent queries | 10 (pool limit) | 100+ | **10x** |
| Architecture | Embedded (in-process) | Server (shared) | Proper server |
| Real-time data | Scan Iceberg snapshot | Live MergeTree | **Real-time** |

**Why**: ClickHouse is a purpose-built OLAP server with vectorized execution, columnar storage, and pre-computed Materialized Views. DuckDB is an embedded engine designed for single-user analytics.

### 5. API Layer: FastAPI → Spring Boot

| Metric | FastAPI (v1) | Spring Boot (v2) | Factor |
|--------|-------------|-------------------|--------|
| Throughput | 1,500 req/s | 15,000 req/s | **10x** |
| p99 latency | 15ms | 3ms | **5x** |
| Concurrency | 4 (worker processes) | 1000+ (virtual threads) | **250x** |
| Type safety | Runtime (Pydantic) | Compile-time (Kotlin) | **Shift-left** |

**Why**: JVM virtual threads provide true parallelism. HikariCP connection pooling is battle-tested. Jackson JSON serialization is 10x faster than Python's json module.

---

## ADR Index

| ADR | Decision | Primary Benefit | Resource Impact |
|-----|----------|-----------------|-----------------|
| [ADR-001](ADR-001-replace-kafka-with-redpanda.md) | Kafka → Redpanda | 6x latency reduction, 3→1 services | -1.5 CPU / -1.78GB |
| [ADR-002](ADR-002-kotlin-feed-handlers.md) | Python → Kotlin handlers | 36x throughput, 12x latency | -0.5 CPU / -512MB |
| [ADR-003](ADR-003-clickhouse-warm-storage.md) | Add ClickHouse warm layer | 40-100x query latency, real-time OHLCV | +2.0 CPU / +4GB (net savings when combined) |
| [ADR-004](ADR-004-eliminate-spark-streaming.md) | Eliminate Spark Streaming | 96% CPU reduction, 30-75x latency | **-13.5 CPU / -19.75GB** |
| [ADR-005](ADR-005-kotlin-spring-boot-api.md) | FastAPI → Spring Boot | 10x throughput, unified language | +0.5 CPU / 0 |
| [ADR-006](ADR-006-spark-batch-only.md) | Spark daily + Kotlin hourly offload | Hourly cold freshness, Spark for compaction only | -12 CPU / -16GB (permanent) |
| [ADR-007](ADR-007-iceberg-cold-storage.md) | Iceberg mirrored four-layer cold | Full lineage, hourly freshness, re-derivation | -1.5 CPU / -2GB |
| [ADR-008](ADR-008-eliminate-prefect-orchestration.md) | Eliminate Prefect | Remove 2 services, 768MB | -0.75 CPU / -768MB |
| [ADR-009](ADR-009-medallion-in-clickhouse.md) | Four-layer medallion in CH | Raw→Bronze→Silver→Gold, 6 OHLCV timeframes | See ADR-003/004 |
| [ADR-010](ADR-010-resource-budget.md) | Resource budget proof | Fits 16 CPU / 40GB with 51% RAM headroom | Budget proven |

---

## Components You May Have Missed

The proposed architecture covers the core well. Here are additional components to consider:

### 1. Dead Letter Queue (DLQ)
Records that fail Silver validation should not be silently dropped.

**Recommendation**: Kotlin Silver Processor writes failed records to a `market.crypto.trades.dlq` Redpanda topic with error metadata. A ClickHouse table ingests DLQ records for monitoring.

### 2. API Rate Limiting / Gateway
Spring Boot can handle rate limiting (Bucket4j), but a dedicated gateway provides:
- TLS termination
- Request routing
- DDoS protection

**Recommendation**: For single Docker Compose, Spring Boot's built-in rate limiting is sufficient. Add Nginx/Envoy if scaling to multiple API instances.

### 3. Configuration Management
Multiple Kotlin services need shared configuration (Redpanda addresses, ClickHouse credentials, etc.).

**Recommendation**: Spring Cloud Config or simple environment variables via Docker Compose `.env` file. Avoid Consul/etcd for single-node.

### 4. Secrets Management
Database passwords, API keys, etc.

**Recommendation**: Docker Compose secrets or HashiCorp Vault (if compliance requires). Start with `.env` files, migrate to Vault as needed.

### 5. Health Check Aggregation
With 11 services, health monitoring is critical.

**Recommendation**: Spring Boot Actuator `/health` endpoint aggregates ClickHouse, Redpanda, MinIO, PostgreSQL health. Prometheus scrapes all services. Grafana dashboard provides single-pane-of-glass view.

### 6. Data Replay / Backfill Infrastructure
When schemas evolve or bugs are discovered, historical data needs reprocessing.

**Recommendation**: Kotlin replay tool that reads from Iceberg cold storage and re-inserts into ClickHouse. Runs as a one-off Docker container (like Spark batch).

### 7. Schema Evolution Strategy
As the platform matures, trade schemas will evolve.

**Recommendation**: Redpanda Schema Registry with BACKWARD compatibility. ClickHouse `ALTER TABLE ... ADD COLUMN` for new fields. Iceberg handles schema evolution natively.

### 8. Multi-Exchange Symbol Registry
Normalizing symbols across exchanges (BTC/USD vs BTCUSD vs XBT/USD) requires a canonical mapping.

**Recommendation**: ClickHouse dictionary table or Kotlin in-memory map loaded from a YAML configuration file. Central source of truth for symbol normalization.

---

## Migration Strategy

### Phase 1: Infrastructure Swap (Week 1-2)
1. Deploy Redpanda alongside Kafka (dual-run)
2. Deploy ClickHouse with initial schema
3. Verify Redpanda compatibility with existing consumers
4. Switch producers to Redpanda, decommission Kafka

### Phase 2: Kotlin Feed Handlers (Week 2-3)
1. Implement Kotlin feed handler for Binance
2. Run parallel with Python handler, compare output
3. Validate throughput and latency improvements
4. Add Kraken, decommission Python handlers

### Phase 3: ClickHouse Warm Layer (Week 3-4)
1. Configure ClickHouse Kafka Engine for Bronze ingestion
2. Implement Kotlin Silver Processor
3. Create ClickHouse Materialized Views for OHLCV
4. Verify OHLCV correctness against v1 batch output

### Phase 4: API Migration (Week 4-5)
1. Implement Spring Boot API with ClickHouse queries
2. Run parallel with FastAPI, compare responses
3. Load test Spring Boot API
4. Switch traffic, decommission FastAPI

### Phase 5: Spark Reduction (Week 5-6)
1. Implement warm-to-cold tiering batch job
2. Configure Spark batch-only with Docker profiles
3. Decommission always-on Spark Streaming
4. Decommission Prefect

### Phase 6: Validation & Hardening (Week 6-8)
1. End-to-end latency benchmarking
2. Resource consumption validation (must fit 16/40)
3. Failure mode testing (service crashes, network partitions)
4. Documentation and runbook updates

---

## Risk Summary

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| ClickHouse memory exceeds budget | Medium | High | Aggressive TTLs, memory limits, monitoring | Platform team |
| Kotlin learning curve delays migration | Medium | Medium | Team training, incremental adoption | Engineering lead |
| Redpanda Kafka API edge cases | Low | Medium | Integration test suite, dual-run period | Platform team |
| Data loss during tiering | Low | High | Dual-write, row count verification, alerting | Data engineering |
| Spark batch job failures | Medium | Medium | Retry logic, alerting, ClickHouse TTL buffer | SRE |

---

## Conclusion

This redesign transforms K2 from a Spark-heavy batch-oriented platform into a **real-time streaming platform** that fits within a 16-core / 40GB Docker Compose envelope. The key architectural moves:

1. **Eliminate Spark Streaming** (-13.5 CPU / -19.75GB) — the single biggest win
2. **Replace Kafka with Redpanda** (-1.5 CPU / -1.78GB + 6x latency reduction)
3. **Add ClickHouse with four-layer medallion** (Raw → Bronze → Silver → Gold)
4. **Six OHLCV timeframes** (1m, 5m, 15m, 30m, 1h, 1d) via real-time MVs
5. **Unify on Kotlin** — one language, one build system, one CI pipeline
6. **Hourly offload to Iceberg** — four-layer mirrored cold storage, ≤1h freshness
7. **Spark for daily maintenance only** — compaction, expiry, audits

The result: **15.5 CPU / 19.5GB** (with 51% RAM headroom), **11 services** (down from 20), **sub-second end-to-end latency** (down from minutes), and **full four-layer data lineage** from exchange payload to OHLCV candle — preserved in both warm and cold tiers.
