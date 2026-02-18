-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Bronze Layer: Coinbase Native Schema
-- Purpose: Preserve Coinbase's native data format (minimal transformation)
-- Source: market.crypto.trades.coinbase.raw (Redpanda topic)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Architecture: Exchange-native schemas in Bronze → Normalize in Silver
--
-- Key Principles:
-- - Preserve product_id (BTC-USD, not BTCUSD) for native format fidelity
-- - Parse ISO8601 time → DateTime64(3) for efficient time-range queries
-- - Preserve side as 'BUY'/'SELL' (Coinbase already taker-perspective)
-- - sequence_num is message-level (envelope), not trade-level
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ═══════════════════════════════════════════════════════════════════════════
-- Kafka Engine: Consume from Redpanda raw topic
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.trades_coinbase_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.coinbase.raw',
    kafka_group_name = 'clickhouse_bronze_coinbase_consumer',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1,
    kafka_max_block_size = 10000,
    kafka_poll_max_batch_size = 10000,
    kafka_flush_interval_ms = 7500;

-- ═══════════════════════════════════════════════════════════════════════════
-- Bronze Table: Coinbase Native Format
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.bronze_trades_coinbase (
    -- ═══════════════════════════════════════════════════════════════════════
    -- Coinbase Identity (preserves native format)
    -- ═══════════════════════════════════════════════════════════════════════
    trade_id      String,                                -- Coinbase trade ID string
    product_id    String,                                -- "BTC-USD" (native Coinbase format)

    -- ═══════════════════════════════════════════════════════════════════════
    -- Trade Data (preserve as strings for precision)
    -- ═══════════════════════════════════════════════════════════════════════
    price         String,                                -- "21921.73"
    size          String,                                -- "0.00099853" (Coinbase term for quantity)
    side          Enum8('BUY' = 1, 'SELL' = 2),         -- Taker perspective (BUY/SELL)

    -- ═══════════════════════════════════════════════════════════════════════
    -- Timestamps
    -- ═══════════════════════════════════════════════════════════════════════
    exchange_timestamp DateTime64(3, 'UTC'),              -- Parsed from ISO8601 "time" field
    sequence_num  UInt64,                                -- Message-level sequence number

    -- ═══════════════════════════════════════════════════════════════════════
    -- Platform metadata
    -- ═══════════════════════════════════════════════════════════════════════
    ingested_at   DateTime64(6, 'UTC') DEFAULT now64(6),

    -- ═══════════════════════════════════════════════════════════════════════
    -- Deduplication
    -- ═══════════════════════════════════════════════════════════════════════
    _version      UInt64 DEFAULT 1

) ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMMDD(exchange_timestamp)
ORDER BY (product_id, exchange_timestamp, trade_id)
TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- ═══════════════════════════════════════════════════════════════════════════
-- Materialized View: Parse Coinbase JSON → Bronze Table
-- ═══════════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_coinbase_mv
TO k2.bronze_trades_coinbase AS
SELECT
    -- Identity
    JSONExtractString(message, 'trade_id')                                          AS trade_id,
    JSONExtractString(message, 'product_id')                                        AS product_id,

    -- Trade Data
    JSONExtractString(message, 'price')                                             AS price,
    JSONExtractString(message, 'size')                                              AS size,

    -- Side (cast to enum; Coinbase sends "BUY"/"SELL")
    CAST(JSONExtractString(message, 'side') AS Enum8('BUY' = 1, 'SELL' = 2))       AS side,

    -- Timestamp: parse ISO8601 string → DateTime64(3)
    parseDateTimeBestEffort(JSONExtractString(message, 'time'))                      AS exchange_timestamp,

    -- Sequence number
    JSONExtractUInt(message, 'sequence_num')                                        AS sequence_num,

    -- Deduplication version
    1                                                                               AS _version

FROM k2.trades_coinbase_queue
WHERE message != ''
  AND JSONExtractString(message, 'trade_id') != ''
  AND JSONExtractString(message, 'product_id') != '';

-- ═══════════════════════════════════════════════════════════════════════════
-- Verification Queries
-- ═══════════════════════════════════════════════════════════════════════════

-- Check Kafka consumer status:
-- SELECT * FROM system.kafka_consumers WHERE table = 'trades_coinbase_queue';

-- Sample recent records:
-- SELECT * FROM k2.bronze_trades_coinbase ORDER BY ingested_at DESC LIMIT 10;

-- Check trade counts by product:
-- SELECT product_id, count() AS trades
-- FROM k2.bronze_trades_coinbase
-- GROUP BY product_id
-- ORDER BY trades DESC;

-- Verify native format preserved:
-- SELECT product_id, side FROM k2.bronze_trades_coinbase LIMIT 5;
-- Expected: "BTC-USD", "BUY"/"SELL" (not "BTCUSD" or "b"/"s")
