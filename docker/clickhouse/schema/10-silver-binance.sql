-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Silver Layer: Binance → Unified Schema
-- Purpose: Normalize Binance native format to multi-exchange schema
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Normalizations performed:
-- - Timestamp: milliseconds → DateTime64(6) microseconds
-- - Side: buy/sell → BUY/SELL enum
-- - Trade ID: Prefixed with BINANCE-
-- - vendor_data: Preserves original Binance metadata
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_binance_to_silver_mv
TO k2.silver_trades AS
SELECT
    -- ═══════════════════════════════════════════════════════════════════════
    -- Identity & Deduplication
    -- ═══════════════════════════════════════════════════════════════════════
    generateUUIDv4() AS message_id,
    concat('BINANCE-', toString(trade_id)) AS trade_id,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Asset Classification
    -- ═══════════════════════════════════════════════════════════════════════
    'binance' AS exchange,
    symbol,

    -- Canonical symbol: Add slash (BTCUSDT → BTC/USDT)
    concat(
        if(startsWith(symbol, 'BTC'), 'BTC',
        if(startsWith(symbol, 'ETH'), 'ETH',
        if(startsWith(symbol, 'BNB'), 'BNB',
        symbol))),
        '/',
        if(endsWith(symbol, 'USDT'), 'USDT',
        if(endsWith(symbol, 'BUSD'), 'BUSD',
        if(endsWith(symbol, 'BTC'), 'BTC',
        if(endsWith(symbol, 'ETH'), 'ETH',
        'UNKNOWN'))))
    ) AS canonical_symbol,

    'crypto' AS asset_class,

    -- Currency: Quote asset (USDT, BUSD, BTC, ETH)
    if(endsWith(symbol, 'USDT'), 'USDT',
    if(endsWith(symbol, 'BUSD'), 'BUSD',
    if(endsWith(symbol, 'BTC'), 'BTC',
    if(endsWith(symbol, 'ETH'), 'ETH',
    'UNKNOWN')))) AS currency,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Trade Data (Bronze already has correct types)
    -- ═══════════════════════════════════════════════════════════════════════
    CAST(price AS Decimal128(8)) AS price,
    CAST(quantity AS Decimal128(8)) AS quantity,
    CAST(quote_volume AS Decimal128(8)) AS quote_volume,

    -- Side: buy/sell → BUY/SELL
    CAST(
        CASE toString(side)
            WHEN 'buy' THEN 'BUY'
            WHEN 'sell' THEN 'SELL'
            ELSE 'UNKNOWN'
        END AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)
    ) AS side,

    CAST([] AS Array(String)) AS trade_conditions,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Timestamps (convert milliseconds → microseconds)
    -- ═══════════════════════════════════════════════════════════════════════
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(exchange_timestamp) * 1000) AS timestamp,
    fromUnixTimestamp64Micro(toUnixTimestamp64Milli(platform_timestamp) * 1000) AS ingestion_timestamp,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Sequencing
    -- ═══════════════════════════════════════════════════════════════════════
    trade_id AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Vendor Data (preserve Binance-specific fields from metadata)
    -- ═══════════════════════════════════════════════════════════════════════
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
        map()
    ) AS vendor_data,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Validation
    -- ═══════════════════════════════════════════════════════════════════════
    (toFloat64(price) > 0 AND toFloat64(quantity) > 0) AS is_valid,

    arrayConcat(
        if(toFloat64(price) <= 0, ['invalid_price'], []),
        if(toFloat64(quantity) <= 0, ['invalid_volume'], [])
    ) AS validation_errors

FROM k2.bronze_trades_binance;

-- ═══════════════════════════════════════════════════════════════════════════
-- Comments & Verification
-- ═══════════════════════════════════════════════════════════════════════════

-- Verify Silver normalization:
-- SELECT
--     exchange,
--     canonical_symbol,
--     count() as trades
-- FROM k2.silver_trades
-- WHERE exchange = 'binance'
-- GROUP BY exchange, canonical_symbol;

-- Sample record:
-- SELECT * FROM k2.silver_trades
-- WHERE exchange = 'binance'
-- ORDER BY timestamp DESC LIMIT 1 FORMAT Vertical;

-- Cross-exchange verification (Gold layer should aggregate both):
-- SELECT
--     exchange,
--     canonical_symbol,
--     window_start,
--     close_price,
--     trade_count
-- FROM k2.ohlcv_1m
-- WHERE canonical_symbol IN ('BTC/USD', 'BTC/USDT')
-- ORDER BY window_start DESC, exchange
-- LIMIT 20;
