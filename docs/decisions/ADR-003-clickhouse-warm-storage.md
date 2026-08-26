# ADR-003: Introduce ClickHouse as Warm Storage Layer

**Status:** Proposed
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Storage Architecture

---

## Context

The current platform has a **two-tier storage model** that creates a latency gap:

1. **Hot tier**: Kafka topics (7-day retention) — real-time but expensive to query
2. **Cold tier**: Iceberg on MinIO (Parquet files) — analytical but high-latency for recent data

There is no **warm storage layer** between streaming ingestion and cold analytical storage. The consequence:

- **Recent data queries** (last 24h, last 7d) must read from Iceberg, which involves:
  - Catalog lookup (PostgreSQL REST) → file manifest scan → Parquet file reads from MinIO
  - Cold-path latency: **200-500ms p95** for aggregations
- **OHLCV pre-computation** requires a separate orchestration system (Prefect) running Spark batch jobs
- **DuckDB** provides fast single-node OLAP, but:
  - Embedded architecture limits concurrent queries to connection pool size (10)
  - No real-time ingestion — must scan Iceberg snapshots
  - No materialized views — every query recalculates
  - Not a server — cannot be shared across services

The platform needs a warm storage layer that:
1. Receives real-time data from the streaming layer with sub-second latency
2. Serves low-latency analytical queries for recent data (last 1-30 days)
3. Pre-computes aggregations (OHLCV) without external orchestration
4. Supports high-concurrency query workloads

## Decision

**Introduce ClickHouse as the warm storage layer between Redpanda and Iceberg.**

ClickHouse will:
- Ingest directly from Redpanda via the ClickHouse Kafka engine or Kotlin consumers
- Store 30 days of recent data in MergeTree tables
- Pre-compute OHLCV candles using Materialized Views
- Serve as the primary query engine for the API layer
- TTL-expire data to Iceberg (cold tier) after 30 days

## Rationale

### Why ClickHouse

ClickHouse is an open-source column-oriented OLAP database designed for real-time analytics. Key properties:

1. **Columnar storage with vectorized execution**: Processes data in SIMD-optimized column batches, achieving 1-2 GB/s scan rates per core
2. **MergeTree engine family**: LSM-tree inspired engine optimized for append-heavy write patterns (perfect for market data)
3. **Materialized Views**: Incrementally compute aggregations on insert — eliminates batch orchestration
4. **Native Kafka/Redpanda integration**: ClickHouse Kafka engine reads from topics directly
5. **SQL interface**: Standard SQL with analytical extensions (window functions, array functions)
6. **Compression**: LZ4/ZSTD achieves 10-20x compression on time-series market data

### Performance: ClickHouse vs DuckDB vs Iceberg Direct

| Query Type | DuckDB (current) | Iceberg Direct | ClickHouse | Notes |
|------------|------------------|----------------|------------|-------|
| Point lookup (1 trade by ID) | 5ms | 50ms | 1ms | ClickHouse primary key index |
| Last price (1 symbol) | 10ms | 100ms | <1ms | Materialized view |
| OHLCV 1m candle (1 symbol, 24h) | 50ms | 200ms | 2ms | Pre-computed via MV |
| OHLCV 1d candle (1 symbol, 1y) | 200ms | 500ms | 5ms | Pre-computed via MV |
| Full scan aggregation (all trades, 7d) | 500ms | 2000ms | 50ms | Vectorized columnar scan |
| Concurrent queries (20 users) | Degraded (embedded) | OK | OK | ClickHouse is a proper server |

Sources:
- [ClickBench — ClickHouse vs DuckDB](https://benchmark.clickhouse.com/)
- [ClickHouse Performance](https://clickhouse.com/docs/en/concepts/why-clickhouse-is-so-fast)

### Materialized Views: Eliminating Prefect + Batch OHLCV

The current platform runs 5 Prefect-orchestrated Spark batch jobs to compute OHLCV candles (1m, 5m, 30m, 1h, 1d). This consumes:
- Prefect Server: 0.5 CPU / 512MB
- Prefect Agent: 0.25 CPU / 256MB
- Spark batch jobs: shared with streaming resources

ClickHouse Materialized Views compute OHLCV **on insert**, eliminating all batch infrastructure:

```sql
-- Trades table (warm storage)
CREATE TABLE trades (
    exchange LowCardinality(String),
    symbol LowCardinality(String),
    trade_id String,
    price Decimal128(8),
    quantity Decimal128(8),
    timestamp DateTime64(3),
    side LowCardinality(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (exchange, symbol, timestamp, trade_id)
TTL timestamp + INTERVAL 30 DAY;

-- OHLCV 1-minute candles (computed on insert, zero-latency)
CREATE MATERIALIZED VIEW ohlcv_1m
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMMDD(window_start)
ORDER BY (exchange, symbol, window_start)
AS SELECT
    exchange,
    symbol,
    toStartOfMinute(timestamp) AS window_start,
    argMinState(price, timestamp) AS open,
    maxState(price) AS high,
    minState(price) AS low,
    argMaxState(price, timestamp) AS close,
    sumState(quantity) AS volume,
    countState() AS trade_count
FROM trades
GROUP BY exchange, symbol, window_start;
```

**Result**: OHLCV candles are available within milliseconds of trade ingestion. No batch jobs, no orchestration, no scheduling delays.

### Warm-to-Cold Tiering Strategy

Data lifecycle in the new architecture:

```
Redpanda (hot: seconds-minutes)
    ↓ Real-time ingestion
ClickHouse (warm: 1-30 days)
    ↓ TTL expiry + Spark batch job (daily)
Iceberg on MinIO (cold: 30+ days, indefinite retention)
```

ClickHouse TTL automatically drops old partitions. A daily Spark batch job exports the expiring partition to Iceberg before TTL deletion, ensuring no data loss.

### Resource Profile

| Metric | DuckDB + Prefect (current) | ClickHouse | Delta |
|--------|---------------------------|------------|-------|
| CPU | 0.75 CPU (Prefect) + shared | 2.0 CPU | Neutral (replaces batch compute) |
| Memory | 768MB (Prefect) + query spikes | 4GB | +3.2GB (but eliminates Spark streaming jobs) |
| Storage | N/A (queries Iceberg) | ~10-50GB (30d compressed) | New, but manageable |
| Concurrent queries | 10 (DuckDB pool) | 100+ | **10x improvement** |
| Query latency (p95) | 200-500ms | 2-50ms | **10-100x improvement** |

### ClickHouse Data Ingestion Options

Two viable approaches for Redpanda → ClickHouse:

#### Option A: ClickHouse Kafka Engine (Built-in)
```sql
CREATE TABLE trades_queue (
    -- same schema as trades
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.*.validated',
    kafka_group_name = 'clickhouse_consumer',
    kafka_format = 'Avro';

CREATE MATERIALIZED VIEW trades_mv TO trades AS
SELECT * FROM trades_queue;
```
- **Pro**: Zero application code, fully managed by ClickHouse
- **Con**: Limited error handling, no custom transformation logic

#### Option B: Kotlin Consumer → ClickHouse JDBC
```kotlin
// Kotlin consumer reads from Redpanda, writes to ClickHouse
consumer.poll(Duration.ofMillis(100)).forEach { record ->
    val trade = deserialize(record)
    clickhouseClient.insertBatch(trades, listOf(trade))
}
```
- **Pro**: Full control over transformation, error handling, backpressure
- **Con**: Additional application code to maintain

**Recommendation**: Start with Option A (Kafka Engine) for simplicity; move to Option B if custom logic is needed.

## Alternatives Considered

### 1. Keep DuckDB, Add Caching Layer (Redis)
- **Pro**: Minimal architecture change
- **Con**: DuckDB is embedded — cannot serve concurrent API requests efficiently
- **Con**: Redis adds another service; cache invalidation is complex for time-series data
- **Con**: Still need Prefect for OHLCV batch computation
- **Verdict**: Rejected — doesn't solve concurrency or batch computation problems

### 2. Apache Druid
- **Pro**: Purpose-built for real-time OLAP, native Kafka ingestion
- **Con**: Very heavy operationally (Coordinator, Broker, Historical, MiddleManager, Router = 5+ JVM processes)
- **Con**: Minimum viable deployment: ~8GB RAM
- **Con**: Complex segment management
- **Verdict**: Rejected — too heavy for 16-core/40GB budget

### 3. TimescaleDB (PostgreSQL Extension)
- **Pro**: Familiar SQL, hypertables for time-series
- **Con**: Row-oriented under the hood — columnar queries 10-100x slower than ClickHouse
- **Con**: Materialized views require manual refresh (no incremental on insert)
- **Con**: Compression less effective than columnar (3-5x vs 10-20x)
- **Verdict**: Rejected — performance insufficient for analytical queries

### 4. QuestDB
- **Pro**: Very fast time-series ingestion, low memory footprint
- **Con**: Limited SQL support (no JOINs, limited window functions)
- **Con**: Smaller ecosystem, fewer integrations
- **Con**: No native Kafka consumer engine
- **Verdict**: Rejected — query capabilities too limited for our use case

### 5. Apache Pinot
- **Pro**: Real-time OLAP, native Kafka ingestion, star-tree index
- **Con**: Heavy operationally (Controller, Broker, Server, Minion = 4+ JVM processes)
- **Con**: Minimum ~6GB RAM for viable deployment
- **Con**: Less SQL-standard than ClickHouse
- **Verdict**: Rejected — operational complexity too high for single-node deployment

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| ClickHouse memory usage exceeds budget | Medium | High | Configure `max_memory_usage`, `max_bytes_before_external_sort`; partition TTLs aggressively |
| Data loss during warm-to-cold tiering | Low | High | Export to Iceberg before TTL expiry; dual-write initial period; monitor partition ages |
| MergeTree merge storms under heavy write | Low | Medium | Configure `max_parts_in_total`, `parts_to_delay_insert`; monitor merge metrics |
| ClickHouse upgrade complexity | Low | Low | Use official Docker images; ClickHouse has strong backward compatibility |

## Resource Budget Impact

```
Before: DuckDB (embedded, ~0 dedicated) + Prefect (0.75 CPU / 768MB) + Kafka UI (0.5 CPU / 512MB)
After:  ClickHouse (2.0 CPU / 4GB)

Net change: +0.75 CPU / +2.7GB RAM
But eliminates: Prefect (0.75 CPU / 768MB), 5 OHLCV batch jobs (Spark resources),
                DuckDB query overhead, Kafka UI (Redpanda Console replaces)

Effective savings when combined with ADR-004 (Spark streaming elimination):
  ~3-5 CPU / ~5-8GB RAM net reduction
```

## Consequences

### Positive
- 10-100x query latency improvement for recent data
- Eliminates Prefect + batch OHLCV computation entirely
- Proper server architecture supports concurrent API queries
- Native Kafka/Redpanda integration — zero-code ingestion
- Columnar compression reduces storage by 10-20x
- SQL interface — low learning curve for team

### Negative
- New technology to operate (ClickHouse administration)
- Warm-to-cold tiering adds pipeline complexity
- Must manage ClickHouse schema migrations (though DDL is simple)
- 4GB RAM allocation is significant on a 40GB budget (10%)

## References

- [ClickHouse Documentation](https://clickhouse.com/docs)
- [ClickHouse MergeTree Engine](https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/mergetree)
- [ClickHouse Materialized Views](https://clickhouse.com/docs/en/sql-reference/statements/create/view#materialized-view)
- [ClickHouse Kafka Engine](https://clickhouse.com/docs/en/engines/table-engines/integrations/kafka)
- [ClickBench — OLAP Database Benchmarks](https://benchmark.clickhouse.com/)
- [ClickHouse for Financial Data](https://clickhouse.com/blog/clickhouse-for-financial-data)
