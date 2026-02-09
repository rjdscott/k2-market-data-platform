-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Additional OHLCV Timeframes (Gold Layer)
-- Timeframes: 5m, 15m, 30m, 1h, 1d
-- Pattern: Same as 1m (SummingMergeTree + MV + query view)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ────────────────────────────────────────────────────────────────────────────
-- 5-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_5m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    open_time DateTime64(3),
    open_price Decimal64(8),
    close_time DateTime64(3),
    close_price Decimal64(8),
    high_price Decimal64(8),
    low_price Decimal64(8),
    volume Decimal(38, 8),
    quote_volume Decimal(38, 8),
    trade_count UInt64

) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_5m_mv TO k2.ohlcv_5m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFiveMinutes(exchange_timestamp) AS window_start,
    argMin(exchange_timestamp, exchange_timestamp) AS open_time,
    argMin(price, exchange_timestamp) AS open_price,
    argMax(exchange_timestamp, exchange_timestamp) AS close_time,
    argMax(price, exchange_timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE VIEW IF NOT EXISTS k2.ohlcv_5m_view AS
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
FROM k2.ohlcv_5m
ORDER BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 15-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_15m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    open_time DateTime64(3),
    open_price Decimal64(8),
    close_time DateTime64(3),
    close_price Decimal64(8),
    high_price Decimal64(8),
    low_price Decimal64(8),
    volume Decimal(38, 8),
    quote_volume Decimal(38, 8),
    trade_count UInt64

) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_15m_mv TO k2.ohlcv_15m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFifteenMinutes(exchange_timestamp) AS window_start,
    argMin(exchange_timestamp, exchange_timestamp) AS open_time,
    argMin(price, exchange_timestamp) AS open_price,
    argMax(exchange_timestamp, exchange_timestamp) AS close_time,
    argMax(price, exchange_timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE VIEW IF NOT EXISTS k2.ohlcv_15m_view AS
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
FROM k2.ohlcv_15m
ORDER BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 30-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_30m (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    open_time DateTime64(3),
    open_price Decimal64(8),
    close_time DateTime64(3),
    close_price Decimal64(8),
    high_price Decimal64(8),
    low_price Decimal64(8),
    volume Decimal(38, 8),
    quote_volume Decimal(38, 8),
    trade_count UInt64

) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_30m_mv TO k2.ohlcv_30m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfInterval(exchange_timestamp, INTERVAL 30 MINUTE) AS window_start,
    argMin(exchange_timestamp, exchange_timestamp) AS open_time,
    argMin(price, exchange_timestamp) AS open_price,
    argMax(exchange_timestamp, exchange_timestamp) AS close_time,
    argMax(price, exchange_timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE VIEW IF NOT EXISTS k2.ohlcv_30m_view AS
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
FROM k2.ohlcv_30m
ORDER BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Hour OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_1h (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    open_time DateTime64(3),
    open_price Decimal64(8),
    close_time DateTime64(3),
    close_price Decimal64(8),
    high_price Decimal64(8),
    low_price Decimal64(8),
    volume Decimal(38, 8),
    quote_volume Decimal(38, 8),
    trade_count UInt64

) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1h_mv TO k2.ohlcv_1h AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfHour(exchange_timestamp) AS window_start,
    argMin(exchange_timestamp, exchange_timestamp) AS open_time,
    argMin(price, exchange_timestamp) AS open_price,
    argMax(exchange_timestamp, exchange_timestamp) AS close_time,
    argMax(price, exchange_timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE VIEW IF NOT EXISTS k2.ohlcv_1h_view AS
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
FROM k2.ohlcv_1h
ORDER BY exchange, canonical_symbol, window_start;

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Day OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS k2.ohlcv_1d (
    exchange LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start DateTime64(3),

    open_time DateTime64(3),
    open_price Decimal64(8),
    close_time DateTime64(3),
    close_price Decimal64(8),
    high_price Decimal64(8),
    low_price Decimal64(8),
    volume Decimal(38, 8),
    quote_volume Decimal(38, 8),
    trade_count UInt64

) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL window_start + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1d_mv TO k2.ohlcv_1d AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfDay(exchange_timestamp) AS window_start,
    argMin(exchange_timestamp, exchange_timestamp) AS open_time,
    argMin(price, exchange_timestamp) AS open_price,
    argMax(exchange_timestamp, exchange_timestamp) AS close_time,
    argMax(price, exchange_timestamp) AS close_price,
    max(price) AS high_price,
    min(price) AS low_price,
    sum(quantity) AS volume,
    sum(quote_volume) AS quote_volume,
    count() AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE VIEW IF NOT EXISTS k2.ohlcv_1d_view AS
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
FROM k2.ohlcv_1d
ORDER BY exchange, canonical_symbol, window_start;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Verification Queries
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Check all OHLCV tables have data:
-- SELECT '1m' as tf, count() FROM k2.ohlcv_1m_view UNION ALL
-- SELECT '5m' as tf, count() FROM k2.ohlcv_5m_view UNION ALL
-- SELECT '15m' as tf, count() FROM k2.ohlcv_15m_view UNION ALL
-- SELECT '30m' as tf, count() FROM k2.ohlcv_30m_view UNION ALL
-- SELECT '1h' as tf, count() FROM k2.ohlcv_1h_view UNION ALL
-- SELECT '1d' as tf, count() FROM k2.ohlcv_1d_view;

-- View latest candles for each timeframe:
-- SELECT * FROM k2.ohlcv_1m_view WHERE canonical_symbol = 'BTC/USDT' ORDER BY window_start DESC LIMIT 5;
-- SELECT * FROM k2.ohlcv_5m_view WHERE canonical_symbol = 'BTC/USDT' ORDER BY window_start DESC LIMIT 5;
-- SELECT * FROM k2.ohlcv_1h_view WHERE canonical_symbol = 'BTC/USDT' ORDER BY window_start DESC LIMIT 5;
-- SELECT * FROM k2.ohlcv_1d_view WHERE canonical_symbol = 'BTC/USDT' ORDER BY window_start DESC LIMIT 5;
