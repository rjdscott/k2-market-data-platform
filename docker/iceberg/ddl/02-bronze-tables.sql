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
    -- Columns must exactly match ClickHouse bronze_trades_binance for direct offload
    exchange_timestamp  TIMESTAMP COMMENT 'Exchange-reported trade timestamp (UTC)',
    sequence_number     BIGINT    COMMENT 'Binance trade sequence number',
    symbol              STRING    COMMENT 'Binance raw symbol (e.g., BTCUSDT)',
    price               DECIMAL(38, 8) COMMENT 'Trade price',
    quantity            DECIMAL(38, 8) COMMENT 'Trade quantity (base asset)',
    quote_volume        DECIMAL(38, 8) COMMENT 'Quote volume (price * quantity)',
    event_time          TIMESTAMP COMMENT 'Exchange event timestamp',
    kafka_offset        BIGINT    COMMENT 'Redpanda partition offset',
    kafka_partition     INT       COMMENT 'Redpanda partition number',
    ingestion_timestamp TIMESTAMP COMMENT 'Platform ingestion timestamp'
)
USING iceberg
PARTITIONED BY (days(exchange_timestamp))
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'  -- 7 days
)
COMMENT 'Binance bronze trades - v2 normalized schema (matches ClickHouse bronze)';

-- ────────────────────────────────────────────────────────────────────────────
-- Bronze Kraken Trades
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.bronze_trades_kraken (
    -- Columns must exactly match ClickHouse bronze_trades_kraken for direct offload
    exchange_timestamp  TIMESTAMP COMMENT 'Exchange-reported trade timestamp (UTC)',
    sequence_number     BIGINT    COMMENT 'Kraken sequence number',
    symbol              STRING    COMMENT 'Kraken symbol (XBT normalized to BTC, e.g., BTCUSD)',
    price               DECIMAL(38, 8) COMMENT 'Trade price',
    quantity            DECIMAL(38, 8) COMMENT 'Trade quantity (base asset)',
    quote_volume        DECIMAL(38, 8) COMMENT 'Quote volume (price * quantity)',
    event_time          TIMESTAMP COMMENT 'Exchange event timestamp',
    kafka_offset        BIGINT    COMMENT 'Redpanda partition offset',
    kafka_partition     INT       COMMENT 'Redpanda partition number',
    ingestion_timestamp TIMESTAMP COMMENT 'Platform ingestion timestamp'
)
USING iceberg
PARTITIONED BY (days(exchange_timestamp))
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'  -- 7 days
)
COMMENT 'Kraken bronze trades - v2 normalized schema (matches ClickHouse bronze)';

-- ────────────────────────────────────────────────────────────────────────────
-- Bronze Coinbase Trades
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cold.bronze_trades_coinbase (
    -- Columns must exactly match ClickHouse bronze_trades_coinbase for direct offload
    exchange_timestamp  TIMESTAMP COMMENT 'Exchange-reported trade timestamp (UTC)',
    sequence_number     BIGINT    COMMENT 'Coinbase message-level sequence number',
    symbol              STRING    COMMENT 'Symbol without dash (e.g., BTCUSD)',
    price               DECIMAL(38, 8) COMMENT 'Trade price',
    quantity            DECIMAL(38, 8) COMMENT 'Trade quantity / size (base asset)',
    quote_volume        DECIMAL(38, 8) COMMENT 'Quote volume (price * quantity)',
    event_time          TIMESTAMP COMMENT 'Exchange event timestamp (same as exchange_timestamp)',
    kafka_offset        BIGINT    COMMENT 'Redpanda partition offset',
    kafka_partition     INT       COMMENT 'Redpanda partition number',
    ingestion_timestamp TIMESTAMP COMMENT 'Platform ingestion timestamp'
)
USING iceberg
PARTITIONED BY (days(exchange_timestamp))
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.parquet.compression-level' = '3',
    'write.metadata.metrics.default' = 'full',
    'write.metadata.metrics.column.symbol' = 'counts',
    'history.expire.max-snapshot-age-ms' = '604800000'  -- 7 days
)
COMMENT 'Coinbase bronze trades - v2 normalized schema (matches ClickHouse bronze)';

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
