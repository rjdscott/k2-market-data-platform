-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Table Validation
-- Purpose: Validate all Iceberg tables created successfully
-- Execution: Run via Spark SQL after DDL creation
-- Version: v2.0
-- Last Updated: 2026-02-11
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Step 1: Verify All Tables Exist
-- ============================================================================

SHOW TABLES IN cold;

-- Expected output:
-- +----------+---------------------------+-----------+
-- | namespace|                  tableName|isTemporary|
-- +----------+---------------------------+-----------+
-- | cold     | bronze_trades_binance     |      false|
-- | cold     | bronze_trades_kraken      |      false|
-- | cold     | silver_trades             |      false|
-- | cold     | gold_ohlcv_1m             |      false|
-- | cold     | gold_ohlcv_5m             |      false|
-- | cold     | gold_ohlcv_15m            |      false|
-- | cold     | gold_ohlcv_30m            |      false|
-- | cold     | gold_ohlcv_1h             |      false|
-- | cold     | gold_ohlcv_1d             |      false|
-- +----------+---------------------------+-----------+

-- ============================================================================
-- Step 2: Verify Table Schemas
-- ============================================================================

-- Bronze Binance
DESCRIBE EXTENDED cold.bronze_trades_binance;

-- Bronze Kraken
DESCRIBE EXTENDED cold.bronze_trades_kraken;

-- Silver
DESCRIBE EXTENDED cold.silver_trades;

-- Gold 1m (sample - all have identical schema)
DESCRIBE EXTENDED cold.gold_ohlcv_1m;

-- ============================================================================
-- Step 3: Verify Partitioning
-- ============================================================================

-- Bronze Binance partitioning
SHOW TBLPROPERTIES cold.bronze_trades_binance('write.format.default');
SHOW TBLPROPERTIES cold.bronze_trades_binance('write.parquet.compression-codec');

-- Silver partitioning
SELECT * FROM cold.silver_trades.partitions LIMIT 5;

-- Gold 1m partitioning
SELECT * FROM cold.gold_ohlcv_1m.partitions LIMIT 5;

-- ============================================================================
-- Step 4: Test Insert Sample Data (Manual)
-- ============================================================================

-- Insert 1 sample row to Bronze Binance
INSERT INTO cold.bronze_trades_binance VALUES (
    '12345',                                    -- trade_id
    'binance',                                  -- exchange
    'BTCUSDT',                                  -- symbol
    'BTC/USDT',                                 -- canonical_symbol
    CAST(50000.50 AS DECIMAL(38,8)),            -- price
    CAST(0.1 AS DECIMAL(38,8)),                 -- quantity
    CAST(5000.05 AS DECIMAL(38,8)),             -- quote_volume
    'buy',                                      -- side
    TIMESTAMP '2026-02-11 12:00:00',            -- exchange_timestamp
    TIMESTAMP '2026-02-11 12:00:01',            -- platform_timestamp
    123,                                        -- sequence_number
    '{"e":"trade","E":1707649200000}'           -- metadata
);

-- Insert 1 sample row to Silver
INSERT INTO cold.silver_trades VALUES (
    '550e8400-e29b-41d4-a716-446655440000',     -- message_id (UUID)
    'BINANCE-12345',                            -- trade_id
    'binance',                                  -- exchange
    'BTCUSDT',                                  -- symbol
    'BTC/USDT',                                 -- canonical_symbol
    'crypto',                                   -- asset_class
    'USDT',                                     -- currency
    CAST(50000.50 AS DECIMAL(38,8)),            -- price
    CAST(0.1 AS DECIMAL(38,8)),                 -- quantity
    CAST(5000.05 AS DECIMAL(38,8)),             -- quote_volume
    'BUY',                                      -- side
    ARRAY(),                                    -- trade_conditions
    TIMESTAMP '2026-02-11 12:00:00',            -- timestamp
    TIMESTAMP '2026-02-11 12:00:01',            -- ingestion_timestamp
    TIMESTAMP '2026-02-11 12:00:02',            -- processed_at
    123,                                        -- source_sequence
    NULL,                                       -- platform_sequence
    MAP('event_type', 'trade'),                 -- vendor_data
    true,                                       -- is_valid
    ARRAY()                                     -- validation_errors
);

-- Insert 1 sample row to Gold 1m
INSERT INTO cold.gold_ohlcv_1m VALUES (
    'binance',                                  -- exchange
    'BTC/USDT',                                 -- canonical_symbol
    TIMESTAMP '2026-02-11 12:00:00',            -- window_start
    TIMESTAMP '2026-02-11 12:00:05',            -- open_time
    CAST(50000.00 AS DECIMAL(38,8)),            -- open_price
    TIMESTAMP '2026-02-11 12:00:55',            -- close_time
    CAST(50100.00 AS DECIMAL(38,8)),            -- close_price
    CAST(50150.00 AS DECIMAL(38,8)),            -- high_price
    CAST(49950.00 AS DECIMAL(38,8)),            -- low_price
    CAST(10.5 AS DECIMAL(38,8)),                -- volume
    CAST(525000.00 AS DECIMAL(38,8)),           -- quote_volume
    42                                          -- trade_count
);

-- ============================================================================
-- Step 5: Verify Data Inserted
-- ============================================================================

-- Bronze Binance
SELECT COUNT(*) as bronze_binance_count FROM cold.bronze_trades_binance;
SELECT * FROM cold.bronze_trades_binance LIMIT 5;

-- Silver
SELECT COUNT(*) as silver_count FROM cold.silver_trades;
SELECT * FROM cold.silver_trades LIMIT 5;

-- Gold 1m
SELECT COUNT(*) as gold_1m_count FROM cold.gold_ohlcv_1m;
SELECT * FROM cold.gold_ohlcv_1m LIMIT 5;

-- ============================================================================
-- Step 6: Verify Snapshots Created
-- ============================================================================

-- Bronze Binance snapshots
SELECT snapshot_id, committed_at, operation
FROM cold.bronze_trades_binance.snapshots
ORDER BY committed_at DESC
LIMIT 5;

-- Silver snapshots
SELECT snapshot_id, committed_at, operation
FROM cold.silver_trades.snapshots
ORDER BY committed_at DESC
LIMIT 5;

-- ============================================================================
-- Step 7: Verify Parquet Files in MinIO
-- ============================================================================

-- Check files for Bronze Binance
SELECT * FROM cold.bronze_trades_binance.files LIMIT 5;

-- Check compression codec
SELECT file_path, file_format, record_count, file_size_in_bytes
FROM cold.bronze_trades_binance.files
LIMIT 5;

-- ============================================================================
-- Validation Checklist
-- ============================================================================

-- [ ] All 9 tables exist (SHOW TABLES)
-- [ ] Schemas match expected (DESCRIBE EXTENDED)
-- [ ] Partitioning configured correctly (SHOW TBLPROPERTIES)
-- [ ] Compression codec is 'zstd' (SHOW TBLPROPERTIES)
-- [ ] Sample data inserts succeed (INSERT ... VALUES)
-- [ ] Data queryable (SELECT COUNT(*))
-- [ ] Snapshots created (SELECT FROM .snapshots)
-- [ ] Parquet files created in MinIO (SELECT FROM .files)
-- [ ] Snapshot expiry configured (7 days)

-- ============================================================================
-- Expected Results
-- ============================================================================

-- Tables created: 9
--   Bronze: 2 (binance, kraken)
--   Silver: 1 (unified)
--   Gold:   6 (1m, 5m, 15m, 30m, 1h, 1d)
--
-- Partitioning:
--   Bronze: days(exchange_timestamp), exchange
--   Silver: days(timestamp), exchange, asset_class
--   Gold:   months(window_start), exchange
--
-- Compression: Zstd level 3
-- Snapshot retention: 7 days
-- Format version: Iceberg v2 (for Silver), v1 (for Bronze/Gold)
