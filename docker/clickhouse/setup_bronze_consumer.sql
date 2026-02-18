-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Setup ClickHouse Kafka Consumer for Bronze Trades (Binance)
-- Purpose: Ingest real trade data from Redpanda for offload validation
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Step 1: Create Kafka Engine table (consumer)
CREATE TABLE IF NOT EXISTS k2.binance_trades_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.binance.raw',
    kafka_group_name = 'clickhouse_bronze_offload_test',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1,
    kafka_max_block_size = 1000,
    kafka_poll_max_batch_size = 1000,
    kafka_flush_interval_ms = 5000;

-- Step 2: Create Materialized View to parse and insert
CREATE MATERIALIZED VIEW IF NOT EXISTS k2.binance_trades_mv TO k2.bronze_trades_binance AS
SELECT
    -- Parse JSON message
    fromUnixTimestamp64Milli(JSONExtractUInt(message, 'T')) AS exchange_timestamp,
    JSONExtractUInt(message, 't') AS sequence_number,
    JSONExtractString(message, 's') AS symbol,
    toDecimal64(JSONExtractString(message, 'p'), 8) AS price,
    toDecimal64(JSONExtractString(message, 'q'), 8) AS quantity,
    toDecimal64(toFloat64(JSONExtractString(message, 'p')) * toFloat64(JSONExtractString(message, 'q')), 8) AS quote_volume,
    fromUnixTimestamp64Milli(JSONExtractUInt(message, 'E')) AS event_time,
    0 AS kafka_offset,  -- Placeholder
    0 AS kafka_partition  -- Placeholder
FROM k2.binance_trades_queue
WHERE message != ''
  AND JSONExtractString(message, 'e') = 'trade';

-- Verify setup
SELECT
    'Setup complete' AS status,
    (SELECT count() FROM system.tables WHERE database = 'k2' AND name = 'binance_trades_queue') AS kafka_table_exists,
    (SELECT count() FROM system.tables WHERE database = 'k2' AND name = 'binance_trades_mv') AS mv_exists;
