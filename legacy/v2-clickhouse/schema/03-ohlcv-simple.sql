-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Simplified OHLCV (Gold Layer)
-- Using SimpleAggregateFunction (easier, no State/Merge needed)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Drop existing if needed
DROP TABLE IF EXISTS k2.ohlcv_1m_mv;
DROP TABLE IF EXISTS k2.ohlcv_5m_mv;
DROP TABLE IF EXISTS k2.ohlcv_15m_mv;
DROP TABLE IF EXISTS k2.ohlcv_1h_mv;
DROP TABLE IF EXISTS k2.ohlcv_1d_mv;

DROP TABLE IF EXISTS k2.ohlcv_1m;
DROP TABLE IF EXISTS k2.ohlcv_5m;
DROP TABLE IF EXISTS k2.ohlcv_15m;
DROP TABLE IF EXISTS k2.ohlcv_1h;
DROP TABLE IF EXISTS k2.ohlcv_1d;

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Minute OHLCV (Simplified with SummingMergeTree)
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE k2.ohlcv_1m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    -- First/last for open/close (argMin/argMax pattern)
    open_time DateTime64(3),
    open_price Decimal64(8),
    close_time DateTime64(3),
    close_price Decimal64(8),

    -- Min/max for high/low
    high_price Decimal64(8),
    low_price Decimal64(8),

    -- Sums
    volume Decimal(38, 8),
    quote_volume Decimal(38, 8),
    trade_count UInt64

) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW k2.ohlcv_1m_mv TO k2.ohlcv_1m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfMinute(exchange_timestamp) AS window_start,

    -- Open: first trade in window
    argMin(exchange_timestamp, exchange_timestamp) AS open_time,
    argMin(price, exchange_timestamp) AS open_price,

    -- Close: last trade in window
    argMax(exchange_timestamp, exchange_timestamp) AS close_time,
    argMax(price, exchange_timestamp) AS close_price,

    -- High/Low
    max(price) AS high_price,
    min(price) AS low_price,

    -- Volumes
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count

FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- Query Helper View (for easy access)
-- ────────────────────────────────────────────────────────────────────────────

CREATE VIEW k2.ohlcv_1m_view AS
SELECT
    exchange,
    canonical_symbol,
    window_start,
    open_price AS open,
    high_price AS high,
    low_price AS low,
    close_price AS close,
    volume,
    quote_volume,
    trade_count AS trades
FROM k2.ohlcv_1m
ORDER BY exchange, canonical_symbol, window_start;
