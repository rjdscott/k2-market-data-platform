-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - ClickHouse Silver & Gold Layers
-- Purpose: Validated analytics-ready trades (Silver) + Real-time OHLCV (Gold)
-- Dependencies: Bronze layer (01-bronze-layer.sql)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- SILVER LAYER: Validated & Analytics-Ready Trades
-- ============================================================================

CREATE TABLE IF NOT EXISTS k2.silver_trades (
    -- Identity
    exchange LowCardinality(String),
    symbol LowCardinality(String),
    canonical_symbol LowCardinality(String),

    -- Trade Data
    trade_id String,
    price Decimal64(8),
    quantity Decimal64(8),
    quote_volume Decimal64(8),
    side Enum8('buy' = 1, 'sell' = 2),

    -- Timestamps
    exchange_timestamp DateTime64(3),
    platform_timestamp DateTime64(3),
    processed_at DateTime64(3) DEFAULT now64(),

    -- Quality Metrics
    is_valid Boolean DEFAULT true,
    validation_errors Array(String) DEFAULT []

) ENGINE = MergeTree()
PARTITION BY (exchange, toYYYYMMDD(exchange_timestamp))
ORDER BY (exchange, canonical_symbol, exchange_timestamp)
TTL exchange_timestamp + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- Silver Layer Design:
-- - MergeTree (not Replacing): Bronze already deduplicated
-- - Quality flags: is_valid, validation_errors
-- - Analytics-ready: Clean data for downstream consumption
-- - TTL 30 days: Medium-term retention

-- ============================================================================
-- Silver Materialized View (Bronze → Silver)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.silver_trades_mv TO k2.silver_trades AS
SELECT
    exchange,
    symbol,
    canonical_symbol,
    trade_id,
    price,
    quantity,
    quote_volume,
    side,
    exchange_timestamp,
    platform_timestamp,

    -- Validation: price and quantity must be positive
    (price > 0 AND quantity > 0 AND quote_volume > 0) AS is_valid,

    -- Collect validation errors
    arrayConcat(
        if(price <= 0, ['invalid_price'], []),
        if(quantity <= 0, ['invalid_quantity'], []),
        if(quote_volume <= 0, ['invalid_quote_volume'], [])
    ) AS validation_errors

FROM k2.bronze_trades;

-- Note: Bronze uses ReplacingMergeTree which deduplicates during background merges
-- Silver MV processes all inserts, but duplicates are eventually cleaned up in Bronze

-- Silver MV Logic:
-- - FINAL: Force deduplication from Bronze
-- - Validation: Check price > 0, quantity > 0
-- - Error tracking: Array of validation issues
-- - Pass-through most fields from Bronze

-- ============================================================================
-- GOLD LAYER: Real-Time OHLCV Aggregations
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Minute OHLCV Candles
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_1m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    -- OHLCV (aggregate functions store state, not values)
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),

    -- Trade count
    trade_count AggregateFunction(count, UInt64)

) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

-- AggregatingMergeTree:
-- - Stores pre-aggregated state (not final values)
-- - Merges happen automatically in background
-- - Query with *Merge() functions to get final values

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1m_mv TO k2.ohlcv_1m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfMinute(exchange_timestamp) AS window_start,

    -- Aggregate functions with *State() suffix
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

-- ────────────────────────────────────────────────────────────────────────────
-- 5-Minute OHLCV Candles
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_5m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count AggregateFunction(count, UInt64)
) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_5m_mv TO k2.ohlcv_5m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFiveMinutes(exchange_timestamp) AS window_start,
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

-- ────────────────────────────────────────────────────────────────────────────
-- 15-Minute OHLCV Candles
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_15m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count AggregateFunction(count, UInt64)
) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_15m_mv TO k2.ohlcv_15m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFifteenMinutes(exchange_timestamp) AS window_start,
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

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Hour OHLCV Candles
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_1h (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count AggregateFunction(count, UInt64)
) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1h_mv TO k2.ohlcv_1h AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfHour(exchange_timestamp) AS window_start,
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

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Day OHLCV Candles
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_1d (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count AggregateFunction(count, UInt64)
) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1d_mv TO k2.ohlcv_1d AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfDay(exchange_timestamp) AS window_start,
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

-- ============================================================================
-- Query Helper Views (Optional - for easier querying)
-- ============================================================================

-- Simplified view for querying OHLCV data (auto-merges aggregates)
CREATE VIEW IF NOT EXISTS k2.ohlcv_1m_view AS
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
GROUP BY exchange, canonical_symbol, window_start;

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Check Silver layer
-- SELECT * FROM k2.silver_trades WHERE is_valid = true LIMIT 10;

-- Check OHLCV 1m (with merge)
-- SELECT * FROM k2.ohlcv_1m_view WHERE canonical_symbol = 'BTC/USDT' ORDER BY window_start DESC LIMIT 10;

-- Check data flow
-- SELECT
--   'bronze' as layer, count() as rows FROM k2.bronze_trades
-- UNION ALL
-- SELECT
--   'silver' as layer, count() as rows FROM k2.silver_trades
-- UNION ALL
-- SELECT
--   'gold_1m' as layer, count() as rows FROM k2.ohlcv_1m;
