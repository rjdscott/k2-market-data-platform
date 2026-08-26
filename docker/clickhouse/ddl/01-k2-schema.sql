-- ═══════════════════════════════════════════════════════════════════════════
-- K2 Market Data Platform — ClickHouse bootstrap schema (as-built, 2026-08)
--
-- This ONE file is the bootstrap. It is mounted at /docker-entrypoint-initdb.d
-- and runs on a fresh ClickHouse volume, recreating the full medallion pipeline:
--
--   Redpanda topic → Kafka-engine queue → normalizing MV → bronze (per exchange)
--                  → bronze_*_to_silver_mv → silver_trades
--                  → ohlcv_*_mv → ohlcv_{1m,5m,15m,30m,1h,1d}
--
-- Everything is IF NOT EXISTS, so re-running it is a no-op.
--
-- `docker/clickhouse/schema/` is the HISTORICAL MIGRATION TRAIL (v1 → v2
-- cutover, per-exchange onboarding). Those files are kept for the record and
-- are NOT executed at boot. This file is the current truth; if you change the
-- schema, change it here.
--
-- Column names/types of bronze_*, silver_trades and ohlcv_* must stay in lock
-- step with the Iceberg cold tables (docker/iceberg/warehouse/cold/*) — the
-- Spark offload does a direct append with no column transform.
--
-- Kafka-engine tables are created lazily; no broker connection is needed at
-- init time. Expect broker-retry noise in the log until redpanda is up.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS k2;

-- ═══════════════════════════════════════════════════════════════════════════
-- 1. Kafka-engine queue tables (raw JSON off Redpanda)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.trades_binance_queue (
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
-- 2. Bronze tables — identical shape across all three exchanges.
--    Symbol is stored de-punctuated (BTCUSDT / XBTUSD / BTCUSD) so the
--    bronze→silver MVs can derive canonical_symbol uniformly.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.bronze_trades_binance (
    exchange_timestamp  DateTime64(3),
    sequence_number     UInt64,
    symbol              String,
    price               Decimal(18, 8),
    quantity            Decimal(18, 8),
    quote_volume        Decimal(18, 8),
    event_time          DateTime64(3),
    kafka_offset        UInt64,
    kafka_partition     UInt16,
    ingestion_timestamp DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(exchange_timestamp)
ORDER BY (symbol, exchange_timestamp, sequence_number)
TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS k2.bronze_trades_kraken (
    exchange_timestamp  DateTime64(3),
    sequence_number     UInt64,
    symbol              String,
    price               Decimal(18, 8),
    quantity            Decimal(18, 8),
    quote_volume        Decimal(18, 8),
    event_time          DateTime64(3),
    kafka_offset        UInt64,
    kafka_partition     UInt16,
    ingestion_timestamp DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(exchange_timestamp)
ORDER BY (symbol, exchange_timestamp, sequence_number)
TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS k2.bronze_trades_coinbase (
    exchange_timestamp  DateTime64(3),
    sequence_number     UInt64,
    symbol              String,
    price               Decimal(18, 8),
    quantity            Decimal(18, 8),
    quote_volume        Decimal(18, 8),
    event_time          DateTime64(3),
    kafka_offset        UInt64,
    kafka_partition     UInt16,
    ingestion_timestamp DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(exchange_timestamp)
ORDER BY (symbol, exchange_timestamp, sequence_number)
TTL toDateTime(exchange_timestamp) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- ═══════════════════════════════════════════════════════════════════════════
-- 3. Silver — unified normalized trades across all exchanges
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.silver_trades (
    message_id          UUID,
    trade_id            String,
    exchange            LowCardinality(String),
    symbol              LowCardinality(String),
    canonical_symbol    LowCardinality(String),
    asset_class         Enum8('equities' = 1, 'crypto' = 2, 'futures' = 3, 'options' = 4),
    currency            LowCardinality(String),
    price               Decimal128(8),
    quantity            Decimal128(8),
    quote_volume        Decimal128(8),
    side                Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4),
    trade_conditions    Array(String),
    timestamp           DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    processed_at        DateTime64(6, 'UTC') DEFAULT now64(6),
    source_sequence     Nullable(UInt64),
    platform_sequence   Nullable(UInt64),
    vendor_data         Map(String, String),
    is_valid            Bool DEFAULT true,
    validation_errors   Array(String) DEFAULT []
) ENGINE = MergeTree()
PARTITION BY (exchange, asset_class, toYYYYMMDD(timestamp))
ORDER BY (exchange, asset_class, canonical_symbol, timestamp)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- ═══════════════════════════════════════════════════════════════════════════
-- 4. Gold — OHLCV candles, one table per timeframe.
--    SummingMergeTree collapses the additive columns; open/close/high/low are
--    resolved by the MV's argMin/argMax within each insert block.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS k2.ohlcv_1m (
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(3),
    open_time        DateTime64(3),
    open_price       Decimal(18, 8),
    close_time       DateTime64(3),
    close_price      Decimal(18, 8),
    high_price       Decimal(18, 8),
    low_price        Decimal(18, 8),
    volume           Decimal(38, 8),
    quote_volume     Decimal(38, 8),
    trade_count      UInt64
) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS k2.ohlcv_5m (
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(3),
    open_time        DateTime64(3),
    open_price       Decimal(18, 8),
    close_time       DateTime64(3),
    close_price      Decimal(18, 8),
    high_price       Decimal(18, 8),
    low_price        Decimal(18, 8),
    volume           Decimal(38, 8),
    quote_volume     Decimal(38, 8),
    trade_count      UInt64
) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS k2.ohlcv_15m (
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(3),
    open_time        DateTime64(3),
    open_price       Decimal(18, 8),
    close_time       DateTime64(3),
    close_price      Decimal(18, 8),
    high_price       Decimal(18, 8),
    low_price        Decimal(18, 8),
    volume           Decimal(38, 8),
    quote_volume     Decimal(38, 8),
    trade_count      UInt64
) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS k2.ohlcv_30m (
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(3),
    open_time        DateTime64(3),
    open_price       Decimal(18, 8),
    close_time       DateTime64(3),
    close_price      Decimal(18, 8),
    high_price       Decimal(18, 8),
    low_price        Decimal(18, 8),
    volume           Decimal(38, 8),
    quote_volume     Decimal(38, 8),
    trade_count      UInt64
) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS k2.ohlcv_1h (
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(3),
    open_time        DateTime64(3),
    open_price       Decimal(18, 8),
    close_time       DateTime64(3),
    close_price      Decimal(18, 8),
    high_price       Decimal(18, 8),
    low_price        Decimal(18, 8),
    volume           Decimal(38, 8),
    quote_volume     Decimal(38, 8),
    trade_count      UInt64
) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 1 YEAR
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS k2.ohlcv_1d (
    exchange         LowCardinality(String),
    canonical_symbol LowCardinality(String),
    window_start     DateTime64(3),
    open_time        DateTime64(3),
    open_price       Decimal(18, 8),
    close_time       DateTime64(3),
    close_price      Decimal(18, 8),
    high_price       Decimal(18, 8),
    low_price        Decimal(18, 8),
    volume           Decimal(38, 8),
    quote_volume     Decimal(38, 8),
    trade_count      UInt64
) ENGINE = SummingMergeTree((volume, quote_volume, trade_count))
PARTITION BY (exchange, toYYYYMM(window_start))
ORDER BY (exchange, canonical_symbol, window_start)
TTL toDateTime(window_start) + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

-- ═══════════════════════════════════════════════════════════════════════════
-- 5. Queue → Bronze materialized views (JSON normalization)
--    kafka_offset/kafka_partition are placeholders: ClickHouse's Kafka engine
--    does not expose _offset/_partition virtual columns in a TO-target MV.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_binance_mv
TO k2.bronze_trades_binance AS
SELECT
    fromUnixTimestamp64Milli(JSONExtractUInt(message, 'T'))                     AS exchange_timestamp,
    JSONExtractUInt(message, 't')                                               AS sequence_number,
    JSONExtractString(message, 's')                                             AS symbol,
    toDecimal64(JSONExtractString(message, 'p'), 8)                             AS price,
    toDecimal64(JSONExtractString(message, 'q'), 8)                             AS quantity,
    toDecimal64(
        toString(toFloat64(JSONExtractString(message, 'p')) *
                 toFloat64(JSONExtractString(message, 'q'))), 8)                AS quote_volume,
    fromUnixTimestamp64Milli(JSONExtractUInt(message, 'E'))                     AS event_time,
    0                                                                           AS kafka_offset,
    0                                                                           AS kafka_partition
FROM k2.trades_binance_queue
WHERE message <> ''
  AND JSONExtractString(message, 'e') = 'trade'
  AND JSONExtractString(message, 's') <> '';

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_kraken_mv
TO k2.bronze_trades_kraken AS
SELECT
    fromUnixTimestamp64Micro(
        toUInt64(toFloat64(JSONExtractString(message, 'timestamp')) * 1000000)) AS exchange_timestamp,
    JSONExtractUInt(message, 'channel_id')                                      AS sequence_number,
    -- Kraken publishes the native pair ("XBT/USD"); strip the separator so the
    -- bronze symbol matches the binance/coinbase convention ("XBTUSD") and the
    -- bronze→silver MV can rebuild canonical_symbol the same way for all three.
    replaceAll(JSONExtractString(message, 'pair'), '/', '')                     AS symbol,
    toDecimal64(JSONExtractString(message, 'price'),  8)                        AS price,
    toDecimal64(JSONExtractString(message, 'volume'), 8)                        AS quantity,
    toDecimal64(
        toString(toFloat64(JSONExtractString(message, 'price')) *
                 toFloat64(JSONExtractString(message, 'volume'))), 8)           AS quote_volume,
    fromUnixTimestamp64Micro(
        toUInt64(toFloat64(JSONExtractString(message, 'timestamp')) * 1000000)) AS event_time,
    0                                                                           AS kafka_offset,
    0                                                                           AS kafka_partition
FROM k2.trades_kraken_queue
WHERE message <> ''
  AND JSONExtractString(message, 'pair') <> '';

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
-- 6. Bronze → Silver materialized views
--    `side` is not carried on the v2 raw topics, hence UNKNOWN.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_binance_to_silver_mv
TO k2.silver_trades AS
SELECT
    generateUUIDv4() AS message_id,
    concat('BINANCE-', toString(sequence_number)) AS trade_id,
    'binance' AS exchange,
    symbol,
    -- BTCUSDT → BTC/USDT (quote is the trailing 4 chars)
    concat(substring(symbol, 1, length(symbol) - 4), '/USDT') AS canonical_symbol,
    'crypto' AS asset_class,
    'USDT' AS currency,
    CAST(price AS Decimal128(8)) AS price,
    CAST(quantity AS Decimal128(8)) AS quantity,
    CAST(quote_volume AS Decimal128(8)) AS quote_volume,
    CAST('UNKNOWN' AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)) AS side,
    CAST([] AS Array(String)) AS trade_conditions,
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(exchange_timestamp) * 1000) AS timestamp,
    toDateTime64(ingestion_timestamp, 6, 'UTC') AS ingestion_timestamp,
    sequence_number AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,
    map(
        'kafka_offset',    toString(kafka_offset),
        'kafka_partition', toString(kafka_partition)
    ) AS vendor_data,
    (price > 0 AND quantity > 0) AS is_valid,
    arrayConcat(
        if(price <= 0,    ['invalid_price'],  []),
        if(quantity <= 0, ['invalid_volume'], [])
    ) AS validation_errors
FROM k2.bronze_trades_binance;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_kraken_to_silver_mv
TO k2.silver_trades AS
SELECT
    generateUUIDv4() AS message_id,
    concat('KRAKEN-', toString(sequence_number)) AS trade_id,
    'kraken' AS exchange,
    symbol,
    -- XBTUSD → BTC/USD (quote is the trailing 3 chars; XBT is Kraken's BTC)
    concat(
        replaceOne(substring(symbol, 1, length(symbol) - 3), 'XBT', 'BTC'),
        '/USD'
    ) AS canonical_symbol,
    'crypto' AS asset_class,
    'USD' AS currency,
    CAST(price AS Decimal128(8)) AS price,
    CAST(quantity AS Decimal128(8)) AS quantity,
    CAST(quote_volume AS Decimal128(8)) AS quote_volume,
    CAST('UNKNOWN' AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)) AS side,
    CAST([] AS Array(String)) AS trade_conditions,
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(exchange_timestamp) * 1000) AS timestamp,
    toDateTime64(ingestion_timestamp, 6, 'UTC') AS ingestion_timestamp,
    sequence_number AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,
    map(
        'kafka_offset',    toString(kafka_offset),
        'kafka_partition', toString(kafka_partition)
    ) AS vendor_data,
    (price > 0 AND quantity > 0) AS is_valid,
    arrayConcat(
        if(price <= 0,    ['invalid_price'],  []),
        if(quantity <= 0, ['invalid_volume'], [])
    ) AS validation_errors
FROM k2.bronze_trades_kraken;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_coinbase_to_silver_mv
TO k2.silver_trades AS
SELECT
    generateUUIDv4() AS message_id,
    concat('COINBASE-', toString(sequence_number)) AS trade_id,
    'coinbase' AS exchange,
    symbol,
    -- BTCUSD → BTC/USD (quote is the trailing 3 chars)
    concat(substring(symbol, 1, length(symbol) - 3), '/USD') AS canonical_symbol,
    'crypto' AS asset_class,
    'USD' AS currency,
    CAST(price AS Decimal128(8)) AS price,
    CAST(quantity AS Decimal128(8)) AS quantity,
    CAST(quote_volume AS Decimal128(8)) AS quote_volume,
    CAST('UNKNOWN' AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)) AS side,
    CAST([] AS Array(String)) AS trade_conditions,
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(exchange_timestamp) * 1000) AS timestamp,
    toDateTime64(ingestion_timestamp, 6, 'UTC') AS ingestion_timestamp,
    sequence_number AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,
    map(
        'kafka_offset',    toString(kafka_offset),
        'kafka_partition', toString(kafka_partition)
    ) AS vendor_data,
    (price > 0 AND quantity > 0) AS is_valid,
    arrayConcat(
        if(price <= 0,    ['invalid_price'],  []),
        if(quantity <= 0, ['invalid_volume'], [])
    ) AS validation_errors
FROM k2.bronze_trades_coinbase;

-- ═══════════════════════════════════════════════════════════════════════════
-- 7. Silver → Gold materialized views (OHLCV rollups)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1m_mv TO k2.ohlcv_1m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfMinute(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp)     AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp)     AS close_price,
    max(price)        AS high_price,
    min(price)        AS low_price,
    sum(quantity)     AS volume,
    sum(quote_volume) AS quote_volume,
    count()           AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_5m_mv TO k2.ohlcv_5m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFiveMinutes(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp)     AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp)     AS close_price,
    max(price)        AS high_price,
    min(price)        AS low_price,
    sum(quantity)     AS volume,
    sum(quote_volume) AS quote_volume,
    count()           AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_15m_mv TO k2.ohlcv_15m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfFifteenMinutes(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp)     AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp)     AS close_price,
    max(price)        AS high_price,
    min(price)        AS low_price,
    sum(quantity)     AS volume,
    sum(quote_volume) AS quote_volume,
    count()           AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_30m_mv TO k2.ohlcv_30m AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfInterval(timestamp, INTERVAL 30 MINUTE) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp)     AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp)     AS close_price,
    max(price)        AS high_price,
    min(price)        AS low_price,
    sum(quantity)     AS volume,
    sum(quote_volume) AS quote_volume,
    count()           AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1h_mv TO k2.ohlcv_1h AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfHour(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp)     AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp)     AS close_price,
    max(price)        AS high_price,
    min(price)        AS low_price,
    sum(quantity)     AS volume,
    sum(quote_volume) AS quote_volume,
    count()           AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.ohlcv_1d_mv TO k2.ohlcv_1d AS
SELECT
    exchange,
    canonical_symbol,
    toStartOfDay(timestamp) AS window_start,
    argMin(timestamp, timestamp) AS open_time,
    argMin(price, timestamp)     AS open_price,
    argMax(timestamp, timestamp) AS close_time,
    argMax(price, timestamp)     AS close_price,
    max(price)        AS high_price,
    min(price)        AS low_price,
    sum(quantity)     AS volume,
    sum(quote_volume) AS quote_volume,
    count()           AS trade_count
FROM k2.silver_trades
WHERE is_valid = true
GROUP BY exchange, canonical_symbol, window_start;
