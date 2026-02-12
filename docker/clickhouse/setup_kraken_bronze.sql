-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Setup Kraken Bronze Layer for Multi-Table Offload Testing
-- Purpose: Create Kraken bronze table + Kafka consumer
-- Date: 2026-02-12
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Step 1: Create Bronze Trades Table (Kraken)
CREATE TABLE IF NOT EXISTS k2.bronze_trades_kraken (
    exchange_timestamp DateTime64(3),
    sequence_number UInt64,        -- Using channel_id from Kraken as sequence
    symbol String,
    price Decimal(18, 8),
    quantity Decimal(18, 8),
    quote_volume Decimal(18, 8),
    event_time DateTime64(3),      -- Same as exchange_timestamp for Kraken
    kafka_offset UInt64,
    kafka_partition UInt16,
    ingestion_timestamp DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY (exchange_timestamp, sequence_number)
TTL ingestion_timestamp + toIntervalDay(7)
SETTINGS index_granularity = 8192;

-- Step 2: Create Kafka Engine Consumer
CREATE TABLE IF NOT EXISTS k2.kraken_trades_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.kraken.raw',
    kafka_group_name = 'clickhouse_bronze_kraken',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1,
    kafka_max_block_size = 1000,
    kafka_poll_max_batch_size = 1000,
    kafka_flush_interval_ms = 5000;

-- Step 3: Create Materialized View (Kafka → Bronze)
CREATE MATERIALIZED VIEW IF NOT EXISTS k2.kraken_trades_mv
TO k2.bronze_trades_kraken AS
SELECT
    -- Kraken message format:
    -- {"channel_id":119930881,"pair":"XBT/USD","price":"67050.60000",
    --  "volume":"0.00005100","timestamp":"1770813970.869180","side":"s",
    --  "order_type":"l","misc":"","ingestion_timestamp":1770813971070000}

    -- Parse timestamp (Unix timestamp with microseconds as string)
    fromUnixTimestamp64Micro(
        toUInt64(toFloat64(JSONExtractString(message, 'timestamp')) * 1000000)
    ) AS exchange_timestamp,

    -- Use channel_id as sequence number
    JSONExtractUInt(message, 'channel_id') AS sequence_number,

    -- Symbol (Kraken format: XBT/USD → needs normalization)
    JSONExtractString(message, 'pair') AS symbol,

    -- Price
    toDecimal64(JSONExtractString(message, 'price'), 8) AS price,

    -- Quantity (Kraken calls it 'volume')
    toDecimal64(JSONExtractString(message, 'volume'), 8) AS quantity,

    -- Quote volume (price * quantity)
    toDecimal64(
        toFloat64(JSONExtractString(message, 'price')) *
        toFloat64(JSONExtractString(message, 'volume')),
        8
    ) AS quote_volume,

    -- Event time (same as exchange_timestamp for Kraken)
    fromUnixTimestamp64Micro(
        toUInt64(toFloat64(JSONExtractString(message, 'timestamp')) * 1000000)
    ) AS event_time,

    -- Kafka metadata (placeholders)
    0 AS kafka_offset,
    0 AS kafka_partition

FROM k2.kraken_trades_queue
WHERE message != ''
  AND JSONExtractString(message, 'pair') != '';

-- Verification
SELECT
    'Setup complete' AS status,
    (SELECT count() FROM system.tables WHERE database = 'k2' AND name = 'bronze_trades_kraken') AS bronze_table,
    (SELECT count() FROM system.tables WHERE database = 'k2' AND name = 'kraken_trades_queue') AS kafka_table,
    (SELECT count() FROM system.tables WHERE database = 'k2' AND name = 'kraken_trades_mv') AS mv_table;
