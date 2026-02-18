-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Bronze Layer Tables
-- Purpose: Create per-exchange bronze tables in Iceberg cold tier
-- Execution: Run via Spark SQL (spark-sql or spark-submit)
-- Version: v2.0
-- Last Updated: 2026-02-11
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Prerequisites:
-- 1. Spark 3.5+ with Iceberg extension loaded
-- 2. Iceberg REST catalog configured (http://iceberg-rest:8181)
-- 3. MinIO S3 credentials configured
-- 4. 'cold' catalog registered in Spark SQL

-- ============================================================================
-- Bronze Layer: Per-Exchange Trade Tables (2 Tables)
-- ============================================================================

-- Design Philosophy:
-- - Preserve exchange-native schema (minimal transformation from ClickHouse)
-- - One table per exchange (enables independent schema evolution)
-- - Partitioned by day + exchange for time-range queries
-- - Zstd compression (10-20x compression for market data)
-- - 7-day snapshot retention (time-travel for compliance)

-- ────────────────────────────────────────────────────────────────────────────
-- Bronze Binance Trades
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.bronze_trades_binance (
    trade_id STRING COMMENT 'Binance trade ID (numeric string)',
    exchange STRING COMMENT 'Exchange name (always "binance")',
    symbol STRING COMMENT 'Binance raw symbol (e.g., BTCUSDT)',
    canonical_symbol STRING COMMENT 'Normalized symbol (e.g., BTC/USDT)',

    -- Trade data (Decimal for precision)
    price DECIMAL(38, 8) COMMENT 'Trade price (8 decimal places)',
    quantity DECIMAL(38, 8) COMMENT 'Trade quantity (base asset)',
    quote_volume DECIMAL(38, 8) COMMENT 'Quote volume (price * quantity)',

    -- Trade metadata
    side STRING COMMENT 'Trade side: buy or sell',

    -- Timestamps (microsecond precision mapped to Spark TIMESTAMP)
    exchange_timestamp TIMESTAMP COMMENT 'Exchange-reported timestamp (UTC)',
    platform_timestamp TIMESTAMP COMMENT 'K2 platform ingestion timestamp (UTC)',

    -- Sequencing
    sequence_number BIGINT COMMENT 'Binance sequence number',

    -- Exchange-native metadata (JSON string)
    metadata STRING COMMENT 'Original Binance WebSocket message (JSON)'
)
USING iceberg
PARTITIONED BY (days(exchange_timestamp), exchange)
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.trade_id' = 'counts',
    'write.metadata.metrics.column.symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'  -- 7 days
)
COMMENT 'Binance bronze trades - preserved exchange-native format';

-- ────────────────────────────────────────────────────────────────────────────
-- Bronze Kraken Trades
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.bronze_trades_kraken (
    trade_id STRING COMMENT 'Kraken trade ID',
    exchange STRING COMMENT 'Exchange name (always "kraken")',
    symbol STRING COMMENT 'Kraken raw symbol (e.g., XBT/USD)',
    canonical_symbol STRING COMMENT 'Normalized symbol (e.g., BTC/USD)',

    -- Trade data (Decimal for precision)
    price DECIMAL(38, 8) COMMENT 'Trade price (8 decimal places)',
    quantity DECIMAL(38, 8) COMMENT 'Trade quantity (base asset)',
    quote_volume DECIMAL(38, 8) COMMENT 'Quote volume (price * quantity)',

    -- Trade metadata
    side STRING COMMENT 'Trade side: buy or sell',

    -- Timestamps (microsecond precision mapped to Spark TIMESTAMP)
    exchange_timestamp TIMESTAMP COMMENT 'Exchange-reported timestamp (UTC)',
    platform_timestamp TIMESTAMP COMMENT 'K2 platform ingestion timestamp (UTC)',

    -- Sequencing
    sequence_number BIGINT COMMENT 'Kraken sequence number',

    -- Exchange-native metadata (JSON string)
    metadata STRING COMMENT 'Original Kraken WebSocket message (JSON)'
)
USING iceberg
PARTITIONED BY (days(exchange_timestamp), exchange)
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.trade_id' = 'counts',
    'write.metadata.metrics.column.symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'  -- 7 days
)
COMMENT 'Kraken bronze trades - preserved exchange-native format';

-- ────────────────────────────────────────────────────────────────────────────
-- Bronze Coinbase Trades
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.bronze_trades_coinbase (
    trade_id STRING COMMENT 'Coinbase trade ID',
    exchange STRING COMMENT 'Exchange name (always "coinbase")',
    symbol STRING COMMENT 'Coinbase raw symbol without dash (e.g., BTCUSD)',
    canonical_symbol STRING COMMENT 'Normalized symbol (e.g., BTC/USD)',

    -- Trade data (Decimal for precision)
    price DECIMAL(38, 8) COMMENT 'Trade price (8 decimal places)',
    quantity DECIMAL(38, 8) COMMENT 'Trade quantity / size (base asset)',
    quote_volume DECIMAL(38, 8) COMMENT 'Quote volume (price * quantity)',

    -- Trade metadata
    side STRING COMMENT 'Trade side: BUY or SELL',

    -- Timestamps (microsecond precision mapped to Spark TIMESTAMP)
    exchange_timestamp TIMESTAMP COMMENT 'Exchange-reported timestamp (UTC)',
    platform_timestamp TIMESTAMP COMMENT 'K2 platform ingestion timestamp (UTC)',

    -- Sequencing
    sequence_number BIGINT COMMENT 'Coinbase message-level sequence number',

    -- Exchange-native metadata (JSON string)
    metadata STRING COMMENT 'Original Coinbase WebSocket message (JSON)'
)
USING iceberg
PARTITIONED BY (days(exchange_timestamp), exchange)
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.trade_id' = 'counts',
    'write.metadata.metrics.column.symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'  -- 7 days
)
COMMENT 'Coinbase bronze trades - preserved exchange-native format';

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- List created tables:
-- SHOW TABLES IN cold;

-- Describe table schema:
-- DESCRIBE EXTENDED cold.bronze_trades_binance;

-- Check table properties:
-- SHOW TBLPROPERTIES cold.bronze_trades_binance;

-- Check partitioning spec:
-- SELECT * FROM cold.bronze_trades_binance.partitions LIMIT 5;

-- View snapshots (time-travel):
-- SELECT * FROM cold.bronze_trades_binance.snapshots ORDER BY committed_at DESC LIMIT 5;

-- ============================================================================
-- Expected File Layout in MinIO
-- ============================================================================

-- s3a://k2-data/warehouse/cold/bronze/bronze_trades_binance/
-- ├── metadata/
-- │   ├── v1.metadata.json
-- │   └── snap-12345-1-<uuid>.avro
-- └── data/
--     ├── exchange=binance/
--     │   ├── exchange_timestamp_day=2026-02-11/
--     │   │   ├── 00000-0-<uuid>.parquet  (Zstd compressed)
--     │   │   └── 00001-0-<uuid>.parquet
--     │   └── exchange_timestamp_day=2026-02-12/
--     │       └── ...
--     └── ...

-- Design Notes:
-- - Partitioned by days(exchange_timestamp): Aligns with ClickHouse 30-day TTL
-- - Partitioned by exchange: Enables per-exchange queries (though redundant here)
-- - Decimal(38,8): Matches ClickHouse precision, supports high-value assets
-- - Full metrics: Enables predicate pushdown for symbol, trade_id filters
-- - 7-day snapshots: Time-travel for compliance, expired automatically
