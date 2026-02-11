-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Silver Layer Table
-- Purpose: Create unified normalized trades table in Iceberg cold tier
-- Execution: Run via Spark SQL (spark-sql or spark-submit)
-- Version: v2.0
-- Last Updated: 2026-02-11
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Silver Layer: Unified Normalized Trades (1 Table)
-- ============================================================================

-- Design Philosophy:
-- - Single table for all exchanges (unified schema)
-- - Normalized to canonical symbols (BTC/USDT, ETH/USD, etc.)
-- - Validated data only (is_valid = true filter in queries)
-- - Partitioned by day + exchange + asset_class for multi-asset support
-- - Preserves vendor-specific data in MAP column
-- - Enables re-derivation of Gold layer if aggregation logic changes

CREATE TABLE IF NOT EXISTS cold.silver_trades (
    -- Identity & Deduplication
    message_id STRING COMMENT 'UUID for deduplication across sources',
    trade_id STRING COMMENT 'Prefixed trade ID (e.g., BINANCE-123456)',

    -- Asset Classification
    exchange STRING COMMENT 'Exchange name (binance, kraken, etc.)',
    symbol STRING COMMENT 'Exchange-native symbol (BTCUSDT, XBT/USD)',
    canonical_symbol STRING COMMENT 'Normalized symbol (BTC/USDT, BTC/USD)',
    asset_class STRING COMMENT 'Asset class: crypto, equities, futures, options',
    currency STRING COMMENT 'Quote currency (USDT, USD, BTC, ETH)',

    -- Trade Data (Decimal for precision)
    price DECIMAL(38, 8) COMMENT 'Trade price (8 decimal places)',
    quantity DECIMAL(38, 8) COMMENT 'Trade quantity (base asset)',
    quote_volume DECIMAL(38, 8) COMMENT 'Quote volume (price * quantity)',
    side STRING COMMENT 'Trade side: BUY, SELL, SELL_SHORT, UNKNOWN',

    -- Trade Conditions (exchange-specific codes)
    trade_conditions ARRAY<STRING> COMMENT 'Exchange-specific trade condition codes',

    -- Timestamps (microsecond precision)
    timestamp TIMESTAMP COMMENT 'Trade timestamp (exchange-reported, UTC)',
    ingestion_timestamp TIMESTAMP COMMENT 'K2 platform ingestion timestamp (UTC)',
    processed_at TIMESTAMP COMMENT 'Silver processing timestamp (UTC)',

    -- Sequencing
    source_sequence BIGINT COMMENT 'Exchange sequence number',
    platform_sequence BIGINT COMMENT 'K2 platform sequence number (future)',

    -- Vendor Extensions (exchange-specific data)
    vendor_data MAP<STRING, STRING> COMMENT 'Exchange-specific metadata (key-value pairs)',

    -- Validation
    is_valid BOOLEAN COMMENT 'Validation status (price > 0, quantity > 0, etc.)',
    validation_errors ARRAY<STRING> COMMENT 'Validation error messages (if is_valid = false)'
)
USING iceberg
PARTITIONED BY (days(timestamp), exchange, asset_class)
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.canonical_symbol' = 'counts',
    'write.metadata.metrics.column.exchange' = 'counts',
    'write.metadata.metrics.column.asset_class' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000',  -- 7 days
    'format-version' = '2'  -- Iceberg v2 for row-level deletes (future)
)
COMMENT 'Unified normalized trades across all exchanges - validated and canonical';

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Describe table:
-- DESCRIBE EXTENDED cold.silver_trades;

-- Check partitioning:
-- SELECT * FROM cold.silver_trades.partitions LIMIT 10;

-- Sample valid trades:
-- SELECT exchange, canonical_symbol, price, quantity, timestamp
-- FROM cold.silver_trades
-- WHERE is_valid = true
-- ORDER BY timestamp DESC
-- LIMIT 10;

-- Check validation errors:
-- SELECT exchange, canonical_symbol, validation_errors, COUNT(*) as error_count
-- FROM cold.silver_trades
-- WHERE is_valid = false
-- GROUP BY exchange, canonical_symbol, validation_errors
-- ORDER BY error_count DESC;

-- ============================================================================
-- Expected File Layout in MinIO
-- ============================================================================

-- s3a://k2-data/warehouse/cold/silver/silver_trades/
-- ├── metadata/
-- │   ├── v1.metadata.json
-- │   └── snap-12345-1-<uuid>.avro
-- └── data/
--     ├── timestamp_day=2026-02-11/
--     │   ├── exchange=binance/
--     │   │   ├── asset_class=crypto/
--     │   │   │   ├── 00000-0-<uuid>.parquet
--     │   │   │   └── 00001-0-<uuid>.parquet
--     │   │   └── asset_class=futures/  (future)
--     │   └── exchange=kraken/
--     │       └── asset_class=crypto/
--     │           └── 00000-0-<uuid>.parquet
--     └── timestamp_day=2026-02-12/
--         └── ...

-- Design Notes:
-- - Multi-level partitioning (day, exchange, asset_class) enables targeted queries
-- - MAP<STRING, STRING> for vendor_data: preserves exchange-specific fields
-- - ARRAY<STRING> for trade_conditions & validation_errors: flexible length
-- - Decimal(38,8): Matches ClickHouse, sufficient for all asset classes
-- - is_valid filter: Query patterns should filter WHERE is_valid = true
-- - format-version=2: Enables future row-level deletes (GDPR, corrections)
