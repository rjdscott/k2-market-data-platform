-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Bronze Layer: Coinbase (v2 schema — consistent with Binance/Kraken)
-- Source: market.crypto.trades.coinbase.raw (raw JSON)
-- Pattern: raw queue → normalizing MV → bronze table (Decimal types)
-- Same schema as bronze_trades_binance / bronze_trades_kraken
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ═══════════════════════════════════════════════════════════════════════════
-- Kafka Engine: Consume raw JSON from Redpanda
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
-- Bronze Table: v2 Normalized Schema (matches binance/kraken)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.bronze_trades_coinbase (
    exchange_timestamp  DateTime64(3),
    sequence_number     UInt64,
    symbol              String,          -- BTCUSD (dash removed from product_id)
    price               Decimal(18, 8),
    quantity            Decimal(18, 8),
    quote_volume        Decimal(18, 8),
    event_time          DateTime64(3),   -- same as exchange_timestamp for Coinbase
    kafka_offset        UInt64,
    kafka_partition     UInt16,
    ingestion_timestamp DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(exchange_timestamp)
ORDER BY (symbol, exchange_timestamp, sequence_number)
TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- ═══════════════════════════════════════════════════════════════════════════
-- Materialized View: Parse raw Coinbase JSON → v2 normalized bronze
-- Coinbase raw: {"trade_id":"..","product_id":"BTC-USD","price":"67269.8",
--               "size":"0.001","side":"SELL","time":"2026-02-18T..Z","sequence_num":13}
-- ═══════════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_coinbase_mv
TO k2.bronze_trades_coinbase AS
SELECT
    parseDateTimeBestEffort(JSONExtractString(message, 'time'))                 AS exchange_timestamp,
    JSONExtractUInt(message, 'sequence_num')                                    AS sequence_number,
    replaceAll(JSONExtractString(message, 'product_id'), '-', '')               AS symbol,
    toDecimal64(JSONExtractString(message, 'price'), 8)                         AS price,
    toDecimal64(JSONExtractString(message, 'size'),  8)                         AS quantity,
    toDecimal64(
        toString(toFloat64(JSONExtractString(message, 'price')) *
                 toFloat64(JSONExtractString(message, 'size'))), 8)             AS quote_volume,
    parseDateTimeBestEffort(JSONExtractString(message, 'time'))                 AS event_time,
    0                                                                           AS kafka_offset,
    0                                                                           AS kafka_partition
FROM k2.trades_coinbase_queue
WHERE message <> ''
  AND JSONExtractString(message, 'trade_id') <> ''
  AND JSONExtractString(message, 'product_id') <> '';

-- ═══════════════════════════════════════════════════════════════════════════
-- Verification Queries
-- ═══════════════════════════════════════════════════════════════════════════

-- Check Kafka consumer status:
-- SELECT * FROM system.kafka_consumers WHERE table = 'trades_coinbase_queue';

-- Sample recent records:
-- SELECT * FROM k2.bronze_trades_coinbase ORDER BY exchange_timestamp DESC LIMIT 10;

-- Count by symbol:
-- SELECT symbol, count() AS trades FROM k2.bronze_trades_coinbase GROUP BY symbol ORDER BY trades DESC;

-- Compare counts across exchanges (should all be growing):
-- SELECT 'binance' AS ex, count() FROM k2.bronze_trades_binance
-- UNION ALL SELECT 'kraken', count() FROM k2.bronze_trades_kraken
-- UNION ALL SELECT 'coinbase', count() FROM k2.bronze_trades_coinbase;
