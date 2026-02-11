-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Bronze Layer: Kraken Native Schema (ClickHouse 24.1 Compatible)
-- Purpose: Preserve Kraken's native data format (minimal transformation)
-- Last Updated: 2026-02-11
-- Changes: Fixed TTL expression for DateTime64 compatibility
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Architecture: Exchange-native schemas in Bronze → Normalize in Silver
--
-- Key Principles:
-- - Preserve XBT (not normalized to BTC)
-- - Preserve timestamp as "seconds.microseconds" string
-- - Preserve side as 'b'/'s', order_type as 'l'/'m'
-- - Minimal transformation (just type casting)

-- ═══════════════════════════════════════════════════════════════════════════
-- Kafka Engine: Consume from Redpanda
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS bronze_trades_kraken_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.kraken.raw',
    kafka_group_name = 'clickhouse_bronze_kraken_consumer',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1,
    kafka_max_block_size = 10000,
    kafka_poll_max_batch_size = 10000,
    kafka_flush_interval_ms = 7500;

-- ═══════════════════════════════════════════════════════════════════════════
-- Bronze Table: Kraken Native Format
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS bronze_trades_kraken (
    -- ═══════════════════════════════════════════════════════════════════════
    -- Kraken Identity (preserves native format)
    -- ═══════════════════════════════════════════════════════════════════════
    exchange LowCardinality(String) DEFAULT 'kraken',
    channel_id UInt64,
    pair String,                      -- "XBT/USD" (keep XBT, not BTC!)

    -- ═══════════════════════════════════════════════════════════════════════
    -- Trade Data (preserve as strings for precision)
    -- ═══════════════════════════════════════════════════════════════════════
    price String,                     -- "95381.70000"
    volume String,                    -- "0.00032986"
    timestamp String,                 -- "1737118158.321597" (Kraken format)
    side Enum8('b' = 1, 's' = 2),   -- 'b' = buy, 's' = sell
    order_type Enum8('l' = 1, 'm' = 2), -- 'l' = limit, 'm' = market
    misc String,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Platform metadata
    -- ═══════════════════════════════════════════════════════════════════════
    ingestion_timestamp DateTime64(6, 'UTC'),
    ingested_at DateTime64(6, 'UTC') DEFAULT now64(6),

    -- ═══════════════════════════════════════════════════════════════════════
    -- Sequence number for Iceberg offload
    -- ═══════════════════════════════════════════════════════════════════════
    sequence_number UInt64 DEFAULT 0,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Deduplication
    -- ═══════════════════════════════════════════════════════════════════════
    _version UInt64 DEFAULT 1

) ENGINE = ReplacingMergeTree(_version)
PARTITION BY (exchange, toYYYYMMDD(toDateTime(ingested_at)))
ORDER BY (exchange, pair, timestamp, price)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- ⚠️  CRITICAL FIX (Line 74):
-- Changed: TTL ingested_at + INTERVAL 7 DAY
-- To:      TTL toDateTime(ingested_at) + INTERVAL 7 DAY
-- Reason:  ClickHouse 24.1 requires DateTime/Date for TTL, not DateTime64(6)

-- ═══════════════════════════════════════════════════════════════════════════
-- Materialized View: Parse Kraken JSON → Bronze Table
-- ═══════════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW IF NOT EXISTS bronze_trades_kraken_mv
TO bronze_trades_kraken AS
SELECT
    -- Exchange
    'kraken' AS exchange,

    -- Identity
    JSONExtractUInt(message, 'channel_id') AS channel_id,
    JSONExtractString(message, 'pair') AS pair,  -- Keep XBT/USD as-is!

    -- Trade Data
    JSONExtractString(message, 'price') AS price,
    JSONExtractString(message, 'volume') AS volume,
    JSONExtractString(message, 'timestamp') AS timestamp,

    -- Side & Order Type (cast to enums)
    CAST(JSONExtractString(message, 'side') AS Enum8('b' = 1, 's' = 2)) AS side,
    CAST(JSONExtractString(message, 'order_type') AS Enum8('l' = 1, 'm' = 2)) AS order_type,
    JSONExtractString(message, 'misc') AS misc,

    -- Timestamps
    fromUnixTimestamp64Micro(JSONExtractUInt(message, 'ingestion_timestamp')) AS ingestion_timestamp,

    -- Sequence number: Use row_number-like approach
    -- Note: ClickHouse doesn't have row_number(), so we use timestamp hash
    cityHash64(concat(
        JSONExtractString(message, 'pair'),
        JSONExtractString(message, 'timestamp'),
        JSONExtractString(message, 'price')
    )) AS sequence_number,

    -- Deduplication
    1 AS _version

FROM bronze_trades_kraken_queue
WHERE message != '' AND JSONExtractString(message, 'pair') != '';

-- ═══════════════════════════════════════════════════════════════════════════
-- Comments & Verification
-- ═══════════════════════════════════════════════════════════════════════════

-- Verify Bronze preserves native format:
-- SELECT pair, count() FROM bronze_trades_kraken GROUP BY pair;
-- Expected: "XBT/USD", "ETH/USD" (not BTC/USD)

-- Sample record:
-- SELECT * FROM bronze_trades_kraken ORDER BY ingested_at DESC LIMIT 1 FORMAT Vertical;

-- Check Kafka consumer:
-- SELECT * FROM system.kafka_consumers WHERE table = 'bronze_trades_kraken_queue';
