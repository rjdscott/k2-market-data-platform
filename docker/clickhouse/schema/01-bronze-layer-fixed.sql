-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - ClickHouse Bronze Layer (ClickHouse 24.1 Compatible)
-- Purpose: Ingest normalized trade data from Redpanda, typed and deduplicated
-- Layer: Bronze (typed, deduplicated, minimal transformation)
-- Data Source: market.crypto.trades.{exchange} topics (normalized)
-- Retention: 7 days
-- Last Updated: 2026-02-11
-- Changes: Fixed TTL expression for DateTime64 compatibility
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Step 1: Kafka Engine Consumer (from RAW topics - JSON format)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bronze_trades_binance_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.binance.raw',
    kafka_group_name = 'clickhouse_bronze_binance_consumer',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1,
    kafka_max_block_size = 10000,
    kafka_poll_max_batch_size = 10000,
    kafka_flush_interval_ms = 7500;

-- Notes:
-- - Ingests from RAW topics (JSON format, not Avro)
-- - JSONAsString: Treat entire message as single string column
-- - 1 consumer per queue (separate queue per exchange)
-- - Batch size: 10k messages for efficiency
-- - Flush interval: 7.5s max latency

-- ============================================================================
-- Step 2: Bronze Trades Table (ReplacingMergeTree for deduplication)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bronze_trades_binance (
    -- Identity (ORDER BY key)
    exchange LowCardinality(String) DEFAULT 'binance',
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
    ingested_at DateTime64(3) DEFAULT now64(),

    -- Schema & Metadata
    schema_version LowCardinality(String) DEFAULT '1.0.0',
    metadata String,  -- JSON string for exchange-specific metadata

    -- Deduplication version
    _version UInt64 DEFAULT 1

) ENGINE = ReplacingMergeTree(_version)
PARTITION BY (exchange, toYYYYMMDD(exchange_timestamp))
ORDER BY (exchange, canonical_symbol, exchange_timestamp, sequence_number)
TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- ⚠️  CRITICAL FIX (Line 70):
-- Changed: TTL exchange_timestamp + INTERVAL 7 DAY
-- To:      TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY
-- Reason:  ClickHouse 24.1 requires DateTime/Date for TTL, not DateTime64

-- Table Design Choices:
--
-- Engine: ReplacingMergeTree(_version)
--   - Deduplicates by ORDER BY key: (exchange, symbol, timestamp, sequence)
--   - _version: Handles reprocessing (higher version wins after merge)
--   - Final deduplication happens during merge or with FINAL query
--
-- Partitioning: (exchange, date)
--   - Isolates data by exchange (different retention policies possible)
--   - Daily partitions for efficient TTL cleanup
--   - Each partition is independent
--
-- ORDER BY: (exchange, canonical_symbol, exchange_timestamp, sequence_number)
--   - Primary key for deduplication
--   - Optimized for time-series queries: exchange → symbol → time
--   - sequence_number: Handle multiple trades at same millisecond
--
-- Data Types:
--   - LowCardinality(String): Efficient for low-cardinality columns (exchange, symbol)
--     Saves memory and improves query performance (~10x compression)
--   - Decimal64(8): Precise price storage (8 decimal places, no floating point errors)
--   - Enum8: Memory-efficient side storage (1 byte: buy=1, sell=2)
--   - DateTime64(3): Millisecond precision timestamps
--
-- TTL: 7 days
--   - Bronze is temporary staging layer
--   - Long enough for debugging, short enough to save storage
--   - Automatic cleanup (no manual deletion needed)
--   - Wrapped in toDateTime() for ClickHouse 24.1 compatibility
--
-- Index Granularity: 8192
--   - Default, good balance between index size and query performance
--   - One index entry per 8192 rows

-- ============================================================================
-- Step 3: Materialized View (Kafka → Bronze)
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS bronze_trades_binance_mv
TO bronze_trades_binance AS
SELECT
    -- Exchange: 'binance' (static)
    'binance' AS exchange,

    -- Symbol: Extract from 's' field (e.g., 'BTCUSDT')
    JSONExtractString(message, 's') AS symbol,

    -- Canonical symbol: Convert BTCUSDT → BTC/USDT
    concat(
        substring(JSONExtractString(message, 's'), 1, length(JSONExtractString(message, 's')) - 4),
        '/',
        substring(JSONExtractString(message, 's'), -4)
    ) AS canonical_symbol,

    -- Sequence number: Use trade ID as sequence
    JSONExtractUInt(message, 't') AS sequence_number,

    -- Trade data
    toString(JSONExtractUInt(message, 't')) AS trade_id,
    toDecimal64(JSONExtractString(message, 'p'), 8) AS price,
    toDecimal64(JSONExtractString(message, 'q'), 8) AS quantity,
    toDecimal64(
        toFloat64(JSONExtractString(message, 'p')) *
        toFloat64(JSONExtractString(message, 'q')),
        8
    ) AS quote_volume,

    -- Side: 'm' field (true = buyer is maker = sell, false = buyer is taker = buy)
    CAST(
        if(JSONExtractBool(message, 'm'), 'sell', 'buy')
        AS Enum8('buy' = 1, 'sell' = 2)
    ) AS side,

    -- Timestamps
    fromUnixTimestamp64Milli(JSONExtractUInt(message, 'T')) AS exchange_timestamp,
    now64() AS platform_timestamp,

    -- Schema version
    '1.0.0' AS schema_version,

    -- Metadata: preserve original message
    message AS metadata,

    -- Version
    1 AS _version

FROM bronze_trades_binance_queue
WHERE message != ''
  AND JSONExtractString(message, 's') != ''
  AND JSONExtractString(message, 'e') = 'trade';

-- Materialized View Logic:
--
-- 1. Reads from bronze_trades_binance_queue (Kafka Engine)
-- 2. Parses JSON using JSONExtract* functions
-- 3. Converts types (String → Decimal64, Enum8, DateTime64)
-- 4. Filters invalid messages (empty exchange/symbol)
-- 5. Inserts into bronze_trades_binance
--
-- Performance:
-- - Processes on INSERT (real-time)
-- - No external processing needed
-- - Batching via kafka_max_block_size
--
-- Error Handling:
-- - WHERE filters: Skips malformed messages (logged in ClickHouse error log)
-- - Invalid decimals: Throws error (message skipped, consumer offset advances)

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Check if Kafka engine is consuming:
-- SELECT * FROM system.kafka_consumers WHERE table = 'bronze_trades_binance_queue';

-- View last 10 trades:
-- SELECT * FROM bronze_trades_binance ORDER BY ingested_at DESC LIMIT 10;

-- Check data flow:
-- SELECT exchange, symbol, count() as trades,
--        min(exchange_timestamp) as earliest,
--        max(exchange_timestamp) as latest
-- FROM bronze_trades_binance
-- GROUP BY exchange, symbol;

-- Query with FINAL (force deduplication):
-- SELECT * FROM bronze_trades_binance FINAL
-- WHERE canonical_symbol = 'BTC/USDT'
-- ORDER BY exchange_timestamp DESC
-- LIMIT 10;
