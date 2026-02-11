-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Gold Layer Tables (OHLCV)
-- Purpose: Create OHLCV aggregation tables in Iceberg cold tier
-- Execution: Run via Spark SQL (spark-sql or spark-submit)
-- Version: v2.0
-- Last Updated: 2026-02-11
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Gold Layer: OHLCV Tables (6 Timeframes)
-- ============================================================================

-- Design Philosophy:
-- - Pre-aggregated OHLCV candles for historical charting/analytics
-- - One table per timeframe (1m, 5m, 15m, 30m, 1h, 1d)
-- - Partitioned by month + exchange for long-term historical queries
-- - Sorted by canonical_symbol + window_start for range scans
-- - Final OHLCV values (not intermediate states)

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.gold_ohlcv_1m (
    exchange STRING COMMENT 'Exchange name (binance, kraken, etc.)',
    canonical_symbol STRING COMMENT 'Normalized symbol (BTC/USDT, ETH/USD)',
    window_start TIMESTAMP COMMENT 'Candle window start (UTC, truncated to minute)',

    -- OHLC prices
    open_time TIMESTAMP COMMENT 'First trade timestamp in window',
    open_price DECIMAL(38, 8) COMMENT 'First trade price in window',
    close_time TIMESTAMP COMMENT 'Last trade timestamp in window',
    close_price DECIMAL(38, 8) COMMENT 'Last trade price in window',
    high_price DECIMAL(38, 8) COMMENT 'Highest price in window',
    low_price DECIMAL(38, 8) COMMENT 'Lowest price in window',

    -- Volume metrics
    volume DECIMAL(38, 8) COMMENT 'Total volume (base asset)',
    quote_volume DECIMAL(38, 8) COMMENT 'Total quote volume (price * quantity sum)',
    trade_count BIGINT COMMENT 'Number of trades in window'
)
USING iceberg
PARTITIONED BY (months(window_start), exchange)
LOCATION 's3a://k2-data/warehouse/cold/gold/gold_ohlcv_1m'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.canonical_symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'
)
COMMENT '1-minute OHLCV candles for historical charting';

-- ────────────────────────────────────────────────────────────────────────────
-- 5-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.gold_ohlcv_5m (
    exchange STRING,
    canonical_symbol STRING,
    window_start TIMESTAMP COMMENT 'Candle window start (UTC, truncated to 5 minutes)',
    open_time TIMESTAMP,
    open_price DECIMAL(38, 8),
    close_time TIMESTAMP,
    close_price DECIMAL(38, 8),
    high_price DECIMAL(38, 8),
    low_price DECIMAL(38, 8),
    volume DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    trade_count BIGINT
)
USING iceberg
PARTITIONED BY (months(window_start), exchange)
LOCATION 's3a://k2-data/warehouse/cold/gold/gold_ohlcv_5m'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.canonical_symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'
)
COMMENT '5-minute OHLCV candles for historical charting';

-- ────────────────────────────────────────────────────────────────────────────
-- 15-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.gold_ohlcv_15m (
    exchange STRING,
    canonical_symbol STRING,
    window_start TIMESTAMP COMMENT 'Candle window start (UTC, truncated to 15 minutes)',
    open_time TIMESTAMP,
    open_price DECIMAL(38, 8),
    close_time TIMESTAMP,
    close_price DECIMAL(38, 8),
    high_price DECIMAL(38, 8),
    low_price DECIMAL(38, 8),
    volume DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    trade_count BIGINT
)
USING iceberg
PARTITIONED BY (months(window_start), exchange)
LOCATION 's3a://k2-data/warehouse/cold/gold/gold_ohlcv_15m'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.canonical_symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'
)
COMMENT '15-minute OHLCV candles for historical charting';

-- ────────────────────────────────────────────────────────────────────────────
-- 30-Minute OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.gold_ohlcv_30m (
    exchange STRING,
    canonical_symbol STRING,
    window_start TIMESTAMP COMMENT 'Candle window start (UTC, truncated to 30 minutes)',
    open_time TIMESTAMP,
    open_price DECIMAL(38, 8),
    close_time TIMESTAMP,
    close_price DECIMAL(38, 8),
    high_price DECIMAL(38, 8),
    low_price DECIMAL(38, 8),
    volume DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    trade_count BIGINT
)
USING iceberg
PARTITIONED BY (months(window_start), exchange)
LOCATION 's3a://k2-data/warehouse/cold/gold/gold_ohlcv_30m'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.canonical_symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'
)
COMMENT '30-minute OHLCV candles for historical charting';

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Hour OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.gold_ohlcv_1h (
    exchange STRING,
    canonical_symbol STRING,
    window_start TIMESTAMP COMMENT 'Candle window start (UTC, truncated to hour)',
    open_time TIMESTAMP,
    open_price DECIMAL(38, 8),
    close_time TIMESTAMP,
    close_price DECIMAL(38, 8),
    high_price DECIMAL(38, 8),
    low_price DECIMAL(38, 8),
    volume DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    trade_count BIGINT
)
USING iceberg
PARTITIONED BY (months(window_start), exchange)
LOCATION 's3a://k2-data/warehouse/cold/gold/gold_ohlcv_1h'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.canonical_symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'
)
COMMENT '1-hour OHLCV candles for historical charting';

-- ────────────────────────────────────────────────────────────────────────────
-- 1-Day OHLCV
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.gold_ohlcv_1d (
    exchange STRING,
    canonical_symbol STRING,
    window_start TIMESTAMP COMMENT 'Candle window start (UTC, truncated to day)',
    open_time TIMESTAMP,
    open_price DECIMAL(38, 8),
    close_time TIMESTAMP,
    close_price DECIMAL(38, 8),
    high_price DECIMAL(38, 8),
    low_price DECIMAL(38, 8),
    volume DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    trade_count BIGINT
)
USING iceberg
PARTITIONED BY (months(window_start), exchange)
LOCATION 's3a://k2-data/warehouse/cold/gold/gold_ohlcv_1d'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.canonical_symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'
)
COMMENT '1-day OHLCV candles for historical charting';

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- List all Gold tables:
-- SHOW TABLES IN cold LIKE 'gold_ohlcv_*';

-- Count candles per timeframe:
-- SELECT 'ohlcv_1m' as timeframe, COUNT(*) as candles FROM cold.gold_ohlcv_1m
-- UNION ALL
-- SELECT 'ohlcv_5m', COUNT(*) FROM cold.gold_ohlcv_5m
-- UNION ALL
-- SELECT 'ohlcv_15m', COUNT(*) FROM cold.gold_ohlcv_15m
-- UNION ALL
-- SELECT 'ohlcv_30m', COUNT(*) FROM cold.gold_ohlcv_30m
-- UNION ALL
-- SELECT 'ohlcv_1h', COUNT(*) FROM cold.gold_ohlcv_1h
-- UNION ALL
-- SELECT 'ohlcv_1d', COUNT(*) FROM cold.gold_ohlcv_1d;

-- Sample 1m candles for BTC/USDT:
-- SELECT exchange, canonical_symbol, window_start, open_price, close_price, high_price, low_price, volume
-- FROM cold.gold_ohlcv_1m
-- WHERE canonical_symbol = 'BTC/USDT'
-- ORDER BY window_start DESC
-- LIMIT 10;

-- Check partitioning for 1m table:
-- SELECT * FROM cold.gold_ohlcv_1m.partitions LIMIT 10;

-- ============================================================================
-- Expected File Layout in MinIO (Example: 1m)
-- ============================================================================

-- s3a://k2-data/warehouse/cold/gold/gold_ohlcv_1m/
-- ├── metadata/
-- │   ├── v1.metadata.json
-- │   └── snap-12345-1-<uuid>.avro
-- └── data/
--     ├── window_start_month=2026-02/
--     │   ├── exchange=binance/
--     │   │   ├── 00000-0-<uuid>.parquet
--     │   │   └── 00001-0-<uuid>.parquet
--     │   └── exchange=kraken/
--     │       └── 00000-0-<uuid>.parquet
--     └── window_start_month=2026-03/
--         └── ...

-- Design Notes:
-- - Partitioned by months(window_start): Most charting queries span weeks/months
-- - Partitioned by exchange: Isolates exchanges for query efficiency
-- - No asset_class partition: Can add if multi-asset support materializes
-- - Decimal(38,8): Matches Silver layer precision
-- - Pre-aggregated: Stores final OHLCV (not intermediate aggregation states)
-- - All 6 tables have identical schema (only window_start granularity differs)
