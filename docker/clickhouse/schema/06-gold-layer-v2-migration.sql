-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Gold Layer V2 Migration
-- Purpose: Update OHLCV MVs to read from silver_trades_v2
-- Changes: timestamp (microseconds), asset_class partition
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Step 1: Drop existing OHLCV materialized views
-- ============================================================================

DROP VIEW IF EXISTS k2.ohlcv_1m_mv;
DROP VIEW IF EXISTS k2.ohlcv_5m_mv;
DROP VIEW IF EXISTS k2.ohlcv_15m_mv;
DROP VIEW IF EXISTS k2.ohlcv_30m_mv;
DROP VIEW IF EXISTS k2.ohlcv_1h_mv;
DROP VIEW IF EXISTS k2.ohlcv_1d_mv;

-- ============================================================================
-- Step 2: Recreate OHLCV MVs reading from silver_trades_v2
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE MATERIALIZED VIEW k2.ohlcv_1m_mv TO k2.ohlcv_1m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfMinute(timestamp) AS window_start,  -- Changed from exchange_timestamp

    argMin(timestamp, timestamp) AS open_time,   -- Changed from exchange_timestamp
    argMin(price, timestamp) AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,

    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count

FROM k2.silver_trades_v2  -- Changed from silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 5-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE MATERIALIZED VIEW k2.ohlcv_5m_mv TO k2.ohlcv_5m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFiveMinutes(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp) AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades_v2
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 15-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE MATERIALIZED VIEW k2.ohlcv_15m_mv TO k2.ohlcv_15m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFifteenMinutes(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp) AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades_v2
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 30-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE MATERIALIZED VIEW k2.ohlcv_30m_mv TO k2.ohlcv_30m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfInterval(timestamp, INTERVAL 30 MINUTE) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp) AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades_v2
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Hour OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE MATERIALIZED VIEW k2.ohlcv_1h_mv TO k2.ohlcv_1h AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfHour(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp) AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades_v2
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Day OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE MATERIALIZED VIEW k2.ohlcv_1d_mv TO k2.ohlcv_1d AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfDay(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp) AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades_v2
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Check all MVs are created:
-- SELECT name FROM system.tables WHERE database = 'k2' AND name LIKE '%ohlcv%mv%' ORDER BY name;

-- Verify data flowing to Gold layer:
-- SELECT '1m' as tf, count() FROM k2.ohlcv_1m
-- UNION ALL SELECT '5m', count() FROM k2.ohlcv_5m
-- UNION ALL SELECT '15m', count() FROM k2.ohlcv_15m
-- UNION ALL SELECT '30m', count() FROM k2.ohlcv_30m
-- UNION ALL SELECT '1h', count() FROM k2.ohlcv_1h
-- UNION ALL SELECT '1d', count() FROM k2.ohlcv_1d;
