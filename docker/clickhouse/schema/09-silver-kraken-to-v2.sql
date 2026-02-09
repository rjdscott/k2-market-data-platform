-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Silver Layer: Kraken → Unified v2 Schema
-- Purpose: Normalize Kraken native format to multi-exchange schema
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Normalizations performed:
-- - XBT → BTC (canonical_symbol, symbol)
-- - Timestamp: "seconds.microseconds" → DateTime64(6)
-- - Side: 'b'/'s' → BUY/SELL enum
-- - Trade ID: Generated deterministic hash (Kraken doesn't provide)
-- - vendor_data: Preserves original Kraken format
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_kraken_to_silver_v2_mv
TO k2.silver_trades_v2 AS
SELECT
    -- ═══════════════════════════════════════════════════════════════════════
    -- Identity & Deduplication
    -- ═══════════════════════════════════════════════════════════════════════
    generateUUIDv4() AS message_id,

    -- Generate deterministic trade_id (Kraken doesn't provide)
    -- Format: KRAKEN-{timestamp_micros}-{hash}
    concat(
        'KRAKEN-',
        toString(toUnixTimestamp64Micro(toFloat64(timestamp) * 1000000)),
        '-',
        substring(hex(MD5(concat(pair, price, volume))), 1, 8)
    ) AS trade_id,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Asset Classification (Normalize XBT → BTC here!)
    -- ═══════════════════════════════════════════════════════════════════════
    'kraken' AS exchange,

    -- Symbol: Remove slash (XBT/USD → XBTUSD), normalize XBT → BTC
    concat(
        if(splitByChar('/', pair)[1] = 'XBT', 'BTC', splitByChar('/', pair)[1]),
        splitByChar('/', pair)[2]
    ) AS symbol,

    -- Canonical symbol: Keep slash, normalize XBT → BTC
    concat(
        if(splitByChar('/', pair)[1] = 'XBT', 'BTC', splitByChar('/', pair)[1]),
        '/',
        splitByChar('/', pair)[2]
    ) AS canonical_symbol,

    'crypto' AS asset_class,

    -- Currency: Quote asset (USD, USDT, EUR)
    splitByChar('/', pair)[2] AS currency,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Trade Data (convert strings → Decimal128)
    -- ═══════════════════════════════════════════════════════════════════════
    CAST(toDecimal64(price, 8) AS Decimal128(8)) AS price,
    CAST(toDecimal64(volume, 8) AS Decimal128(8)) AS quantity,
    CAST(toDecimal64(toFloat64(price) * toFloat64(volume), 8) AS Decimal128(8)) AS quote_volume,

    -- Side: 'b' → BUY, 's' → SELL
    CAST(
        CASE toString(side)
            WHEN 'b' THEN 'BUY'
            WHEN 's' THEN 'SELL'
        END AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)
    ) AS side,

    CAST([] AS Array(String)) AS trade_conditions,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Timestamps (convert Kraken "seconds.microseconds" → DateTime64(6))
    -- ═══════════════════════════════════════════════════════════════════════
    fromUnixTimestamp64Micro(toUnixTimestamp64Micro(toFloat64(timestamp) * 1000000)) AS timestamp,
    ingestion_timestamp AS ingestion_timestamp,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Sequencing
    -- ═══════════════════════════════════════════════════════════════════════
    toUnixTimestamp64Micro(toFloat64(timestamp) * 1000000) AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Vendor Data (preserve Kraken-specific fields)
    -- ═══════════════════════════════════════════════════════════════════════
    map(
        'pair', pair,                      -- Original XBT/USD
        'order_type', toString(order_type), -- 'l' or 'm'
        'misc', misc,
        'channel_id', toString(channel_id),
        'raw_timestamp', timestamp         -- Original "seconds.microseconds"
    ) AS vendor_data,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Validation
    -- ═══════════════════════════════════════════════════════════════════════
    (toFloat64(price) > 0 AND toFloat64(volume) > 0) AS is_valid,

    arrayConcat(
        if(toFloat64(price) <= 0, ['invalid_price'], []),
        if(toFloat64(volume) <= 0, ['invalid_volume'], [])
    ) AS validation_errors

FROM k2.bronze_trades_kraken;

-- ═══════════════════════════════════════════════════════════════════════════
-- Comments & Verification
-- ═══════════════════════════════════════════════════════════════════════════

-- Verify Silver normalization:
-- SELECT
--     exchange,
--     canonical_symbol,
--     vendor_data['pair'] as original_pair,
--     count() as trades
-- FROM k2.silver_trades_v2
-- WHERE exchange = 'kraken'
-- GROUP BY exchange, canonical_symbol, original_pair;
-- Expected: canonical_symbol = "BTC/USD", vendor_data['pair'] = "XBT/USD"

-- Sample record:
-- SELECT * FROM k2.silver_trades_v2
-- WHERE exchange = 'kraken'
-- ORDER BY timestamp DESC LIMIT 1 FORMAT Vertical;

-- Cross-exchange verification (Gold layer should aggregate both):
-- SELECT
--     exchange,
--     canonical_symbol,
--     window_start,
--     close_price,
--     trade_count
-- FROM k2.ohlcv_1m
-- WHERE canonical_symbol = 'BTC/USD'
-- ORDER BY window_start DESC, exchange
-- LIMIT 20;
