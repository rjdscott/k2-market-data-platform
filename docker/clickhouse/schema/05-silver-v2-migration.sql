-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Silver Layer V2 Migration
-- Purpose: Align with trade_v2.avsc schema for multi-asset support
-- Migration Strategy: Create v2 table, dual-write, validate, cutover
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Step 1: Create Silver Trades V2 Table (Multi-Asset Schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS k2.silver_trades_v2 (
    -- Identity & Deduplication
    message_id UUID,
    trade_id String,

    -- Asset Classification
    exchange LowCardinality(String),
    symbol LowCardinality(String),
    canonical_symbol LowCardinality(String),
    asset_class Enum8('equities' = 1, 'crypto' = 2, 'futures' = 3, 'options' = 4),
    currency LowCardinality(String),

    -- Trade Data (Decimal128 for higher precision)
    price Decimal128(8),
    quantity Decimal128(8),
    quote_volume Decimal128(8),
    side Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4),

    -- Trade Conditions (exchange-specific codes)
    trade_conditions Array(String),

    -- Timestamps (microsecond precision)
    timestamp DateTime64(6, 'UTC'),
    ingestion_timestamp DateTime64(6, 'UTC'),
    processed_at DateTime64(6, 'UTC') DEFAULT now64(6),

    -- Sequencing
    source_sequence Nullable(UInt64),
    platform_sequence Nullable(UInt64),

    -- Vendor Extensions (exchange-specific data)
    vendor_data Map(String, String),

    -- Validation
    is_valid Boolean DEFAULT true,
    validation_errors Array(String) DEFAULT []

) ENGINE = MergeTree()
PARTITION BY (exchange, asset_class, toYYYYMMDD(timestamp))
ORDER BY (exchange, asset_class, canonical_symbol, timestamp)
TTL timestamp + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- Design Notes:
-- - Partition by exchange + asset_class for multi-asset isolation
-- - Decimal128(8) supports high-value assets and micro-prices
-- - DateTime64(6) = microsecond precision for HFT data
-- - vendor_data Map preserves exchange-specific fields
-- - UUID message_id for proper deduplication across sources

-- ============================================================================
-- Step 2: Create Bronze → Silver V2 Materialized View
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_trades_mv_v2 TO k2.silver_trades_v2 AS
SELECT
    -- Identity & Deduplication
    generateUUIDv4() AS message_id,
    concat('BINANCE-', trade_id) AS trade_id,

    -- Asset Classification
    exchange,
    symbol,
    canonical_symbol,
    'crypto' AS asset_class,  -- All Binance trades are crypto

    -- Extract currency from symbol (BTCUSDT → USDT, ETHBTC → BTC)
    -- For now, assume USDT pairs (can enhance later)
    if(
        endsWith(symbol, 'USDT'), 'USDT',
        if(endsWith(symbol, 'BUSD'), 'BUSD',
        if(endsWith(symbol, 'BTC'), 'BTC',
        if(endsWith(symbol, 'ETH'), 'ETH',
        'UNKNOWN')))
    ) AS currency,

    -- Trade Data (convert to Decimal128)
    CAST(price AS Decimal128(8)) AS price,
    CAST(quantity AS Decimal128(8)) AS quantity,
    CAST(quote_volume AS Decimal128(8)) AS quote_volume,

    -- Side (map bronze enum to v2 enum)
    CAST(
        CASE side
            WHEN 'buy' THEN 'BUY'
            WHEN 'sell' THEN 'SELL'
            ELSE 'UNKNOWN'
        END AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)
    ) AS side,

    -- Trade Conditions (empty for crypto spot)
    CAST([] AS Array(String)) AS trade_conditions,

    -- Timestamps (convert milliseconds to microseconds)
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(exchange_timestamp) * 1000) AS timestamp,
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(platform_timestamp) * 1000) AS ingestion_timestamp,

    -- Sequencing
    sequence_number AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,  -- Future: auto-increment

    -- Vendor Data (parse from Bronze metadata JSON)
    -- Bronze stores original Binance JSON in metadata field
    if(
        length(metadata) > 0,
        map(
            'event_type', JSONExtractString(metadata, 'e'),
            'event_time', toString(JSONExtractUInt(metadata, 'E')),
            'is_buyer_maker', toString(JSONExtractBool(metadata, 'm')),
            'is_best_match', toString(JSONExtractBool(metadata, 'M')),
            'buyer_order_id', toString(JSONExtractUInt(metadata, 'b')),
            'seller_order_id', toString(JSONExtractUInt(metadata, 'a'))
        ),
        map()  -- Empty map if no metadata
    ) AS vendor_data,

    -- Validation (same logic as v1)
    (price > 0 AND quantity > 0 AND quote_volume > 0) AS is_valid,

    arrayConcat(
        if(price <= 0, ['invalid_price'], []),
        if(quantity <= 0, ['invalid_quantity'], []),
        if(quote_volume <= 0, ['invalid_quote_volume'], [])
    ) AS validation_errors

FROM k2.bronze_trades;

-- MV Logic:
-- 1. Generate UUID for each trade (message_id)
-- 2. Prefix trade_id with exchange name
-- 3. Classify as 'crypto' asset_class
-- 4. Extract currency from symbol suffix
-- 5. Convert timestamps to microseconds (UTC)
-- 6. Parse vendor-specific data from Bronze metadata
-- 7. Validate price/quantity/volume > 0
-- 8. Map side enum: buy/sell → BUY/SELL

-- ============================================================================
-- Verification Queries
-- ============================================================================

-- Check v2 table has data:
-- SELECT count() FROM k2.silver_trades_v2;

-- Compare v1 vs v2 counts:
-- SELECT 'v1' as version, count() FROM k2.silver_trades
-- UNION ALL
-- SELECT 'v2', count() FROM k2.silver_trades_v2;

-- View sample v2 record:
-- SELECT * FROM k2.silver_trades_v2 ORDER BY timestamp DESC LIMIT 1 FORMAT Vertical;

-- Check vendor_data parsing:
-- SELECT
--     trade_id,
--     vendor_data['event_type'] as event_type,
--     vendor_data['is_buyer_maker'] as is_buyer_maker
-- FROM k2.silver_trades_v2
-- WHERE vendor_data != map()
-- LIMIT 5;

-- Validate asset_class distribution:
-- SELECT asset_class, count() FROM k2.silver_trades_v2 GROUP BY asset_class;

-- Check currency extraction:
-- SELECT currency, count() FROM k2.silver_trades_v2 GROUP BY currency ORDER BY count() DESC;
