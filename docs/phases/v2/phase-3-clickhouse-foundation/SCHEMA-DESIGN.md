# ClickHouse Schema Design - Crypto Market Data Medallion Architecture

**Phase**: Phase 3 - ClickHouse Foundation
**Date**: 2026-02-09
**Status**: Draft → Review → Approved
**Owner**: Platform Engineering

---

## Overview

Four-layer medallion architecture for crypto market data in ClickHouse:

```
Redpanda Topics
    ↓
RAW Layer (Kafka Engine) ──→ Immutable landing zone, JSONAsString
    ↓ (MV)
BRONZE Layer (ReplacingMergeTree) ──→ Typed, deduplicated, normalized
    ↓ (MV)
SILVER Layer (MergeTree) ──→ Validated, enriched, canonical
    ↓ (MVs)
GOLD Layer (AggregatingMergeTree) ──→ OHLCV 1m/5m/15m/30m/1h/1d (real-time)
```

---

## Design Principles

### 1. Immutability at Raw
- Raw layer preserves original JSON (audit trail, reprocessing capability)
- Never delete from Raw (TTL-based expiry only)
- All transformations happen in downstream layers

### 2. Idempotency & Deduplication
- Bronze: ReplacingMergeTree deduplicates by (exchange, symbol, sequence_number)
- Silver: Final deduplication + validation
- Gold: Aggregations handle duplicate inserts correctly

### 3. Real-Time Computation
- Materialized Views compute on INSERT (not batch jobs)
- OHLCV aggregations update as trades arrive
- Sub-second latency from trade → OHLCV

### 4. Storage Efficiency
- TTLs per layer: Raw 48h → Bronze 7d → Silver 30d → Gold 1y
- Compression: LZ4 (fast) or ZSTD (better ratio)
- Partitioning: By date for efficient expiry

### 5. Query Performance
- Proper ORDER BY for common query patterns
- Sparse indexes on high-cardinality fields
- Projection indexes for complex aggregations

---

## Layer Schemas

### RAW Layer: Immutable Landing Zone

#### trades_raw_queue (Kafka Engine)
```sql
CREATE TABLE k2.trades_raw_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.binance,market.crypto.trades.kraken',
    kafka_group_name = 'clickhouse_raw_consumer',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 2,
    kafka_max_block_size = 10000;
```

**Why Kafka Engine?**
- Zero external consumers (ClickHouse pulls directly)
- Automatic offset management
- Batching for efficiency (max_block_size)

#### trades_raw (MergeTree)
```sql
CREATE TABLE k2.trades_raw (
    message String,
    ingested_at DateTime64(3) DEFAULT now64()
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(ingested_at)
ORDER BY (ingested_at)
TTL ingested_at + INTERVAL 48 HOUR
SETTINGS index_granularity = 8192;
```

**Design Choices**:
- `message String`: Preserves original JSON (no parsing, immutable)
- `ingested_at`: Audit trail, partition key
- TTL 48h: Short retention, debugging window
- Partition by day: Efficient TTL cleanup

#### trades_raw_mv (Materialized View)
```sql
CREATE MATERIALIZED VIEW k2.trades_raw_mv TO k2.trades_raw AS
SELECT message
FROM k2.trades_raw_queue;
```

**Dataflow**: Kafka → Queue → MV → Raw table

---

### BRONZE Layer: Typed & Deduplicated

#### bronze_trades (ReplacingMergeTree)
```sql
CREATE TABLE k2.bronze_trades (
    -- Identity
    exchange LowCardinality(String),
    symbol LowCardinality(String),
    sequence_number UInt64,

    -- Trade Data
    trade_id String,
    price Decimal64(8),
    quantity Decimal64(8),
    side Enum8('buy' = 1, 'sell' = 2),

    -- Timestamps
    exchange_timestamp DateTime64(3),
    ingested_at DateTime64(3),

    -- Metadata
    raw_message String,

    -- Deduplication version
    _version UInt64 DEFAULT 1

) ENGINE = ReplacingMergeTree(_version)
PARTITION BY (exchange, toYYYYMMDD(exchange_timestamp))
ORDER BY (exchange, symbol, exchange_timestamp, sequence_number)
TTL exchange_timestamp + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;
```

**Design Choices**:
- **ReplacingMergeTree**: Deduplicates by ORDER BY key
- **_version**: Handles reprocessing (higher version wins)
- **LowCardinality**: Efficient string compression for exchange/symbol
- **Decimal64(8)**: Precise price storage (8 decimal places)
- **Partition**: By exchange + date (independent data retention per exchange)
- **ORDER BY**: Optimized for time-series queries (exchange → symbol → time)
- **TTL 7d**: Debugging window, shorter than Silver

#### bronze_trades_mv (Cascading from Raw)
```sql
CREATE MATERIALIZED VIEW k2.bronze_trades_mv TO k2.bronze_trades AS
SELECT
    -- Parse JSON and extract fields
    JSONExtractString(message, 'exchange') AS exchange,
    JSONExtractString(message, 'symbol') AS symbol,
    JSONExtractUInt(message, 'sequence_number') AS sequence_number,
    JSONExtractString(message, 'trade_id') AS trade_id,
    JSONExtractFloat(message, 'price') AS price,
    JSONExtractFloat(message, 'quantity') AS quantity,
    CASE JSONExtractString(message, 'side')
        WHEN 'buy' THEN 'buy'
        WHEN 'sell' THEN 'sell'
    END AS side,

    -- Timestamp normalization (exchange timestamp in ms → DateTime64)
    fromUnixTimestamp64Milli(JSONExtractUInt(message, 'timestamp')) AS exchange_timestamp,
    ingested_at,

    -- Preserve raw for debugging
    message AS raw_message,

    1 AS _version

FROM k2.trades_raw
WHERE message != '';  -- Filter empty messages
```

**Transformations**:
- JSON parsing (JSONExtract*)
- Timestamp normalization (Unix ms → DateTime64)
- Type casting (String → Decimal64, Enum)
- Basic validation (non-empty messages)

---

### SILVER Layer: Validated & Enriched

#### silver_trades (MergeTree)
```sql
CREATE TABLE k2.silver_trades (
    -- Identity
    exchange LowCardinality(String),
    symbol LowCardinality(String),
    canonical_symbol LowCardinality(String),  -- Normalized: BTC/USDT

    -- Trade Data
    trade_id String,
    price Decimal64(8),
    quantity Decimal64(8),
    quote_volume Decimal64(8),  -- price * quantity
    side Enum8('buy' = 1, 'sell' = 2),

    -- Timestamps
    exchange_timestamp DateTime64(3),
    ingested_at DateTime64(3),
    processed_at DateTime64(3) DEFAULT now64(),

    -- Quality flags
    is_valid Boolean DEFAULT true,
    validation_errors Array(String) DEFAULT []

) ENGINE = MergeTree()
PARTITION BY (exchange, toYYYYMMDD(exchange_timestamp))
ORDER BY (exchange, canonical_symbol, exchange_timestamp)
TTL exchange_timestamp + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;
```

**Design Choices**:
- **MergeTree** (not Replacing): Bronze already deduplicated
- **canonical_symbol**: Normalized across exchanges (BTC/USDT, not BTCUSDT)
- **quote_volume**: Computed field (price × quantity)
- **is_valid + validation_errors**: Quality tracking
- **processed_at**: Silver processing timestamp
- **TTL 30d**: Medium-term retention for analytics

#### silver_trades_mv (Cascading from Bronze)
```sql
CREATE MATERIALIZED VIEW k2.silver_trades_mv TO k2.silver_trades AS
SELECT
    exchange,
    symbol,

    -- Normalize symbol to canonical format (BTC/USDT)
    if(
        exchange = 'binance',
        concat(substring(symbol, 1, length(symbol) - 4), '/', substring(symbol, -4)),
        symbol
    ) AS canonical_symbol,

    trade_id,
    price,
    quantity,
    price * quantity AS quote_volume,  -- Enrichment
    side,

    exchange_timestamp,
    ingested_at,

    -- Validation
    (price > 0 AND quantity > 0) AS is_valid,
    if(
        price > 0 AND quantity > 0,
        [],
        arrayConcat(
            if(price <= 0, ['invalid_price'], []),
            if(quantity <= 0, ['invalid_quantity'], [])
        )
    ) AS validation_errors

FROM k2.bronze_trades
WHERE _version = (
    SELECT max(_version)
    FROM k2.bronze_trades AS b2
    WHERE b2.exchange = bronze_trades.exchange
      AND b2.symbol = bronze_trades.symbol
      AND b2.sequence_number = bronze_trades.sequence_number
);  -- Final deduplication
```

**Transformations**:
- Symbol normalization (BTCUSDT → BTC/USDT)
- Quote volume calculation (price × quantity)
- Validation (price > 0, quantity > 0)
- Final deduplication (max(_version))

---

### GOLD Layer: OHLCV Aggregations (Real-Time)

#### ohlcv_1m (AggregatingMergeTree) - 1-minute candles
```sql
CREATE TABLE k2.ohlcv_1m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    -- OHLCV
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal64(8)),
    quote_volume SimpleAggregateFunction(sum, Decimal64(8)),

    -- Trade count
    trade_count AggregateFunction(count, UInt64)

) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;
```

**Design Choices**:
- **AggregatingMergeTree**: Pre-aggregated state (not raw values)
- **SimpleAggregateFunction**: For OHLCV (any, max, min, anyLast, sum)
- **window_start**: 1-minute boundary (rounded down)
- **Partition by month**: Monthly data management
- **ORDER BY**: Exchange → symbol → time (query pattern)
- **TTL 1y**: Long-term candle storage

#### ohlcv_1m_mv (Real-time aggregation)
```sql
CREATE MATERIALIZED VIEW k2.ohlcv_1m_mv TO k2.ohlcv_1m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfMinute(exchange_timestamp) AS window_start,

    anyState(price) AS open_price,
    maxState(price) AS high_price,
    minState(price) AS low_price,
    anyLastState(price) AS close_price,
    sumState(quantity) AS volume,
    sumState(quote_volume) AS quote_volume,

    countState() AS trade_count

FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;
```

**Aggregation Logic**:
- `toStartOfMinute()`: Round timestamp down to 1-min boundary
- `*State()`: Store aggregate state (not final value)
- `anyState()`: First value (open price)
- `anyLastState()`: Last value (close price)
- `GROUP BY`: Per exchange, per symbol, per minute

#### Query Pattern (Finalize Aggregates)
```sql
-- Query 1-minute OHLCV candles
SELECT
    exchange,
    canonical_symbol,
    window_start,
    anyMerge(open_price) AS open,
    maxMerge(high_price) AS high,
    minMerge(low_price) AS low,
    anyLastMerge(close_price) AS close,
    sumMerge(volume) AS volume,
    sumMerge(quote_volume) AS quote_volume,
    countMerge(trade_count) AS trades
FROM k2.ohlcv_1m
WHERE exchange = 'binance'
  AND canonical_symbol = 'BTC/USDT'
  AND window_start >= now() - INTERVAL 1 HOUR
GROUP BY exchange, canonical_symbol, window_start
ORDER BY window_start DESC;
```

**Merge Functions**: `anyMerge()`, `maxMerge()`, etc. finalize the aggregated state into actual values.

---

### Additional OHLCV Tables (5m, 15m, 30m, 1h, 1d)

Same structure as `ohlcv_1m`, different window functions:

```sql
-- 5-minute candles
toStartOfFiveMinute(exchange_timestamp) AS window_start

-- 15-minute candles
toStartOfFifteenMinutes(exchange_timestamp) AS window_start

-- 30-minute candles
toDateTime64(toUnixTimestamp(exchange_timestamp) - (toUnixTimestamp(exchange_timestamp) % 1800), 3) AS window_start

-- 1-hour candles
toStartOfHour(exchange_timestamp) AS window_start

-- 1-day candles
toStartOfDay(exchange_timestamp) AS window_start
```

**Partition Strategy**: All OHLCV tables partition by month for efficient data management.

---

## Indexing Strategy

### Primary Indexes (ORDER BY)
Already defined in table definitions:
- Raw: `(ingested_at)`
- Bronze: `(exchange, symbol, exchange_timestamp, sequence_number)`
- Silver: `(exchange, canonical_symbol, exchange_timestamp)`
- Gold: `(exchange, canonical_symbol, window_start)`

### Secondary Indexes (FUTURE)
Consider adding for common query patterns:
```sql
-- Skip index for symbol lookups
ALTER TABLE silver_trades
ADD INDEX idx_symbol canonical_symbol TYPE set(100) GRANULARITY 4;

-- MinMax index for price range queries
ALTER TABLE silver_trades
ADD INDEX idx_price_range price TYPE minmax GRANULARITY 4;
```

---

## Storage & Performance Estimates

### Storage Estimates (per day, per exchange)

**Assumptions**:
- 100 symbols (top 100 by volume)
- 10 trades/second average = 864,000 trades/day
- Binance + Kraken = 1.73M trades/day

| Layer | Row Size | Rows/Day | Storage/Day | TTL | Total Storage |
|-------|----------|----------|-------------|-----|---------------|
| Raw | ~500 bytes | 1.73M | ~865 MB | 48h | **1.7 GB** |
| Bronze | ~200 bytes | 1.73M | ~346 MB | 7d | **2.4 GB** |
| Silver | ~220 bytes | 1.73M | ~381 MB | 30d | **11.4 GB** |
| OHLCV 1m | ~100 bytes | 144K | ~14 MB | 1y | **5.1 GB** |
| OHLCV 5m | ~100 bytes | 29K | ~3 MB | 1y | **1.1 GB** |
| OHLCV 15m | ~100 bytes | 10K | ~1 MB | 1y | **365 MB** |
| OHLCV 30m | ~100 bytes | 5K | ~0.5 MB | 1y | **183 MB** |
| OHLCV 1h | ~100 bytes | 2.4K | ~0.25 MB | 1y | **91 MB** |
| OHLCV 1d | ~100 bytes | 100 | ~10 KB | 1y | **3.7 MB** |

**Total Steady-State Storage**: ~21 GB (with TTLs active)

### Query Performance Targets

| Query Type | Target Latency (p95) | Notes |
|------------|---------------------|-------|
| Latest trades (1000 rows) | < 5ms | Silver layer, indexed by time |
| OHLCV 1-minute (last hour) | < 2ms | Gold layer, pre-aggregated |
| OHLCV 1-day (last year) | < 5ms | Gold layer, 365 rows |
| Aggregation (hourly volume) | < 10ms | Silver layer, on-the-fly aggregation |
| Symbol search | < 10ms | Skip index on canonical_symbol |

---

## Data Flow Summary

```
1. Trade arrives at Redpanda topic (market.crypto.trades.binance)
   Latency: ~5ms (Redpanda p99)

2. ClickHouse Kafka Engine pulls message into trades_raw_queue
   Latency: ~10ms (batch interval)

3. Materialized View inserts into trades_raw (Raw layer)
   Latency: ~1ms (in-memory MV)

4. Cascading MV parses JSON → bronze_trades (Bronze layer)
   Latency: ~2ms (JSON parsing + dedup)

5. Cascading MV validates/enriches → silver_trades (Silver layer)
   Latency: ~2ms (validation + normalization)

6. Six OHLCV MVs aggregate → ohlcv_* tables (Gold layer)
   Latency: ~3ms (6 aggregations in parallel)

Total: Trade → OHLCV available in ~23ms (< 200ms target ✓)
```

---

## Next Steps

1. Review this schema design
2. Implement DDL SQL files in `docker/clickhouse/schema/`
3. Test with sample data
4. Validate performance targets
5. Document query patterns

---

**Status**: Draft (awaiting review)
**Last Updated**: 2026-02-09
**Owner**: Platform Engineering
