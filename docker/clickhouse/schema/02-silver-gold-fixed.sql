-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Silver & Gold Layers (ClickHouse 24.1 Compatible)
-- Purpose: Unified trades (Silver) + Real-time OHLCV (Gold)
-- Dependencies: bronze_trades_binance, bronze_trades_kraken
-- Last Updated: 2026-02-11
-- Changes: Fixed TTL, adapted for separate bronze tables
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- SILVER LAYER: Unified Validated Trades
-- ============================================================================

CREATE TABLE IF NOT EXISTS silver_trades (
    -- Identity
    exchange LowCardinality(String),
    symbol LowCardinality(String),
    canonical_symbol LowCardinality(String),
    sequence_number UInt64,

    -- Trade Data
    trade_id String,
    price Decimal64(8),
    quantity Decimal64(8),
    quote_volume Decimal64(8),
    side Enum8('buy' = 1, 'sell' = 2),

    -- Timestamps
    exchange_timestamp DateTime64(3),
    platform_timestamp DateTime64(3),
    ingested_at DateTime64(3),
    processed_at DateTime64(3) DEFAULT now64(),

    -- Platform Metadata
    platform_sequence UInt64,  -- For Iceberg offload ordering

    -- Quality Metrics
    is_valid Boolean DEFAULT true,
    validation_errors Array(String) DEFAULT []

) ENGINE = MergeTree()
PARTITION BY (exchange, toYYYYMMDD(exchange_timestamp))
ORDER BY (exchange, canonical_symbol, exchange_timestamp, sequence_number)
TTL toDateTime(exchange_timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- ⚠️  TTL FIX: Wrapped exchange_timestamp with toDateTime()

-- ============================================================================
-- Silver Materialized View: Binance → Silver
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS silver_trades_binance_mv
TO silver_trades AS
SELECT
    exchange,
    symbol,
    canonical_symbol,
    sequence_number,
    trade_id,
    price,
    quantity,
    quote_volume,
    side,
    exchange_timestamp,
    platform_timestamp,
    ingested_at,

    -- Platform sequence: Use ClickHouse internal sequence
    cityHash64(concat(
        exchange,
        canonical_symbol,
        toString(toUnixTimestamp64Milli(exchange_timestamp)),
        toString(sequence_number)
    )) AS platform_sequence,

    -- Validation: price and quantity must be positive
    (price > 0 AND quantity > 0 AND quote_volume > 0) AS is_valid,

    -- Collect validation errors
    arrayConcat(
        if(price <= 0, ['invalid_price'], []),
        if(quantity <= 0, ['invalid_quantity'], []),
        if(quote_volume <= 0, ['invalid_quote_volume'], [])
    ) AS validation_errors

FROM bronze_trades_binance;

-- ============================================================================
-- Silver Materialized View: Kraken → Silver
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS silver_trades_kraken_mv
TO silver_trades AS
SELECT
    exchange,

    -- Normalize: XBT/USD → BTC/USD
    pair AS symbol,
    if(startsWith(pair, 'XBT'),
       concat('BTC', substring(pair, 4)),
       pair) AS canonical_symbol,

    sequence_number,

    -- Trade ID: Hash of timestamp + price
    concat('kraken_', timestamp, '_', price) AS trade_id,

    -- Convert string prices to Decimal64
    toDecimal64(price, 8) AS price,
    toDecimal64(volume, 8) AS quantity,
    toDecimal64(toFloat64(price) * toFloat64(volume), 8) AS quote_volume,

    -- Normalize side: 'b'/'s' → 'buy'/'sell'
    CAST(if(side = 'b', 'buy', 'sell') AS Enum8('buy' = 1, 'sell' = 2)) AS side,

    -- Parse Kraken timestamp: "seconds.microseconds" → DateTime64(3)
    fromUnixTimestamp64Milli(
        toUInt64(toFloat64(timestamp) * 1000)
    ) AS exchange_timestamp,

    ingestion_timestamp AS platform_timestamp,
    ingested_at,

    -- Platform sequence
    cityHash64(concat(
        exchange,
        pair,
        timestamp,
        price
    )) AS platform_sequence,

    -- Validation
    (toFloat64(price) > 0 AND toFloat64(volume) > 0) AS is_valid,

    arrayConcat(
        if(toFloat64(price) <= 0, ['invalid_price'], []),
        if(toFloat64(volume) <= 0, ['invalid_quantity'], [])
    ) AS validation_errors

FROM bronze_trades_kraken;

-- ============================================================================
-- GOLD LAYER: Real-Time OHLCV Aggregations
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Minute OHLCV Candles
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohlcv_1m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    -- OHLCV
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count SimpleAggregateFunction(sum, UInt64)

) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

-- ⚠️  TTL FIX: Wrapped window_start with toDateTime()

-- ────────────────────────────────────────────────────────────────────────────
-- OHLCV Materialized View: Silver → 1m Candles
-- ────────────────────────────────────────────────────────────────────────────

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1m_mv
TO ohlcv_1m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfMinute(exchange_timestamp) AS window_start,

    anySimpleState(price) AS open_price,
    maxSimpleState(price) AS high_price,
    minSimpleState(price) AS low_price,
    anyLastSimpleState(price) AS close_price,
    sumSimpleState(quantity) AS volume,
    sumSimpleState(quote_volume) AS quote_volume,
    sumSimpleState(toUInt64(1)) AS trade_count

FROM silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ============================================================================
-- Additional OHLCV Timeframes (from 1m aggregation)
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 5-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohlcv_5m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count SimpleAggregateFunction(sum, UInt64)
) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_5m_mv
TO ohlcv_5m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFiveMinutes(window_start) AS window_start,
    anySimpleState(open_price) AS open_price,
    maxSimpleState(high_price) AS high_price,
    minSimpleState(low_price) AS low_price,
    anyLastSimpleState(close_price) AS close_price,
    sumSimpleState(volume) AS volume,
    sumSimpleState(quote_volume) AS quote_volume,
    sumSimpleState(trade_count) AS trade_count
FROM ohlcv_1m
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Hour OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohlcv_1h (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count SimpleAggregateFunction(sum, UInt64)
) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1h_mv
TO ohlcv_1h AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfHour(window_start) AS window_start,
    anySimpleState(open_price) AS open_price,
    maxSimpleState(high_price) AS high_price,
    minSimpleState(low_price) AS low_price,
    anyLastSimpleState(close_price) AS close_price,
    sumSimpleState(volume) AS volume,
    sumSimpleState(quote_volume) AS quote_volume,
    sumSimpleState(trade_count) AS trade_count
FROM ohlcv_1m
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Day OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohlcv_1d (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),
    open_price SimpleAggregateFunction(any, Decimal64(8)),
    high_price SimpleAggregateFunction(max, Decimal64(8)),
    low_price SimpleAggregateFunction(min, Decimal64(8)),
    close_price SimpleAggregateFunction(anyLast, Decimal64(8)),
    volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    quote_volume SimpleAggregateFunction(sum, Decimal(38, 8)),
    trade_count SimpleAggregateFunction(sum, UInt64)
) ENGINE = AggregatingMergeTree()
PARTITION BY (exchange, toYear(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1d_mv
TO ohlcv_1d AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfDay(window_start) AS window_start,
    anySimpleState(open_price) AS open_price,
    maxSimpleState(high_price) AS high_price,
    minSimpleState(low_price) AS low_price,
    anyLastSimpleState(close_price) AS close_price,
    sumSimpleState(volume) AS volume,
    sumSimpleState(quote_volume) AS quote_volume,
    sumSimpleState(trade_count) AS trade_count
FROM ohlcv_1h
GROUP BY exchange, canonical_symbol, window_start;

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Check silver trades:
-- SELECT exchange, canonical_symbol, COUNT(*) as trades
-- FROM silver_trades
-- GROUP BY exchange, canonical_symbol
-- ORDER BY trades DESC;

-- Check 1m OHLCV:
-- SELECT exchange, canonical_symbol, window_start,
--        open_price, high_price, low_price, close_price,
--        volume, trade_count
-- FROM ohlcv_1m
-- WHERE canonical_symbol = 'BTC/USDT'
-- ORDER BY window_start DESC
-- LIMIT 10;

-- Check aggregation cascade (1m → 5m → 1h → 1d):
-- SELECT '1m' as timeframe, COUNT(*) as candles FROM ohlcv_1m
-- UNION ALL
-- SELECT '5m', COUNT(*) FROM ohlcv_5m
-- UNION ALL
-- SELECT '1h', COUNT(*) FROM ohlcv_1h
-- UNION ALL
-- SELECT '1d', COUNT(*) FROM ohlcv_1d;
