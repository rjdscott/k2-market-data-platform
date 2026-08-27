-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Bronze Layer: Kraken Native Schema
-- Purpose: Preserve Kraken's native data format (minimal transformation)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Architecture: Exchange-native schemas in Bronze → Normalize in Silver
--
-- Key Principles:
-- - Preserve XBT (not normalized to BTC)
-- - Preserve timestamp as "seconds.microseconds" string
-- - Preserve side as 'b'/'s', order_type as 'l'/'m'
-- - Minimal transformation (just type casting)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ═══════════════════════════════════════════════════════════════════════════
-- Kafka Engine: Consume from Redpanda
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.trades_kraken_queue (
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

CREATE TABLE IF NOT EXISTS k2.bronze_trades_kraken (
    -- ═══════════════════════════════════════════════════════════════════════
    -- Kraken Identity (preserves native format)
    -- ═══════════════════════════════════════════════════════════════════════
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
    -- Deduplication
    -- ═══════════════════════════════════════════════════════════════════════
    _version UInt64 DEFAULT 1

) ENGINE = ReplacingMergeTree(_version)
PARTITION BY (toYYYYMMDD(ingested_at))
ORDER BY (pair, timestamp, price)
TTL ingested_at + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- ═══════════════════════════════════════════════════════════════════════════
-- Materialized View: Parse Kraken JSON → Bronze Table
-- ═══════════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_kraken_mv
TO k2.bronze_trades_kraken AS
SELECT
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

    -- Deduplication
    1 AS _version

FROM k2.trades_kraken_queue
WHERE message != '' AND JSONExtractString(message, 'pair') != '';

-- ═══════════════════════════════════════════════════════════════════════════
-- Comments & Verification
-- ═══════════════════════════════════════════════════════════════════════════

-- Verify Bronze preserves native format:
-- SELECT pair, count() FROM k2.bronze_trades_kraken GROUP BY pair;
-- Expected: "XBT/USD", "ETH/USD" (not BTC/USD)

-- Sample record:
-- SELECT * FROM k2.bronze_trades_kraken ORDER BY ingested_at DESC LIMIT 1 FORMAT Vertical;
