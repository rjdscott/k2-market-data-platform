-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Silver Layer: Coinbase → Unified Schema
-- Purpose: Normalize Coinbase native format to multi-exchange silver schema
-- Source: k2.bronze_trades_coinbase (native Coinbase fields)
-- Target: k2.silver_trades (unified schema shared by all exchanges)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Normalizations performed:
-- - product_id: "BTC-USD" → symbol="BTCUSD", canonical_symbol="BTC/USD"
-- - price/size: String → Decimal128(8) (preserve precision)
-- - side: Enum8('BUY'/'SELL') → unified Enum8 (BUY/SELL/SELL_SHORT/UNKNOWN)
-- - timestamp: DateTime64(3) → DateTime64(6) microseconds
-- - vendor_data: Preserve Coinbase-specific fields (trade_id, product_id, sequence_num)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_coinbase_to_silver_mv
TO k2.silver_trades AS
SELECT
    -- ═══════════════════════════════════════════════════════════════════════
    -- Identity & Deduplication
    -- ═══════════════════════════════════════════════════════════════════════
    generateUUIDv4() AS message_id,

    -- Prefix trade_id for cross-exchange uniqueness
    concat('COINBASE-', trade_id) AS trade_id,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Asset Classification
    -- ═══════════════════════════════════════════════════════════════════════
    'coinbase' AS exchange,

    -- Symbol: Remove dash (BTC-USD → BTCUSD)
    replaceAll(product_id, '-', '') AS symbol,

    -- Canonical symbol: Replace dash with slash (BTC-USD → BTC/USD)
    replaceAll(product_id, '-', '/') AS canonical_symbol,

    'crypto' AS asset_class,

    -- Currency: Extract quote asset from product_id (BTC-USD → USD)
    splitByChar('-', product_id)[2] AS currency,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Trade Data (convert strings → Decimal128 for precision)
    -- ═══════════════════════════════════════════════════════════════════════
    CAST(toDecimal64(price, 8) AS Decimal128(8)) AS price,
    CAST(toDecimal64(size, 8) AS Decimal128(8)) AS quantity,
    CAST(toDecimal64(toFloat64(price) * toFloat64(size), 8) AS Decimal128(8)) AS quote_volume,

    -- Side: Coinbase bronze uses Enum8('BUY'=1,'SELL'=2); cast to unified enum
    CAST(
        CASE toString(side)
            WHEN 'BUY'  THEN 'BUY'
            WHEN 'SELL' THEN 'SELL'
            ELSE 'UNKNOWN'
        END AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)
    ) AS side,

    CAST([] AS Array(String)) AS trade_conditions,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Timestamps (DateTime64(3) → DateTime64(6) microseconds)
    -- ═══════════════════════════════════════════════════════════════════════
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(exchange_timestamp) * 1000) AS timestamp,
    ingested_at AS ingestion_timestamp,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Sequencing
    -- ═══════════════════════════════════════════════════════════════════════
    sequence_num AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Vendor Data (preserve Coinbase-specific fields)
    -- ═══════════════════════════════════════════════════════════════════════
    map(
        'trade_id',     trade_id,               -- Original Coinbase trade ID
        'product_id',   product_id,             -- Original "BTC-USD" format
        'sequence_num', toString(sequence_num)  -- Message-level sequence
    ) AS vendor_data,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Validation
    -- ═══════════════════════════════════════════════════════════════════════
    (toFloat64(price) > 0 AND toFloat64(size) > 0) AS is_valid,

    arrayConcat(
        if(toFloat64(price) <= 0, ['invalid_price'],  []),
        if(toFloat64(size)  <= 0, ['invalid_volume'], [])
    ) AS validation_errors

FROM k2.bronze_trades_coinbase;

-- ═══════════════════════════════════════════════════════════════════════════
-- Verification Queries
-- ═══════════════════════════════════════════════════════════════════════════

-- Verify normalisation:
-- SELECT
--     exchange,
--     canonical_symbol,
--     vendor_data['product_id'] AS original_product_id,
--     count() AS trades
-- FROM k2.silver_trades
-- WHERE exchange = 'coinbase'
-- GROUP BY exchange, canonical_symbol, original_product_id;
-- Expected: canonical_symbol = "BTC/USD", original_product_id = "BTC-USD"

-- Sample record:
-- SELECT * FROM k2.silver_trades
-- WHERE exchange = 'coinbase'
-- ORDER BY timestamp DESC LIMIT 1 FORMAT Vertical;

-- Cross-exchange OHLCV (all 3 exchanges should appear):
-- SELECT
--     exchange,
--     canonical_symbol,
--     window_start,
--     close_price,
--     trade_count
-- FROM k2.ohlcv_1m
-- WHERE canonical_symbol = 'BTC/USD'
-- ORDER BY window_start DESC, exchange
-- LIMIT 15;
-- Expected rows: binance (BTC/USDT), kraken (BTC/USD), coinbase (BTC/USD)
-- Note: only kraken + coinbase share "BTC/USD" canonical; binance uses "BTC/USDT"
