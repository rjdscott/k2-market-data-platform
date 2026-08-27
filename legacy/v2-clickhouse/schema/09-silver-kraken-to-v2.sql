-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Silver Layer: Kraken → Unified Schema (v2 pattern)
-- Source: k2.bronze_trades_kraken (v2 normalized schema)
-- Target: k2.silver_trades (unified multi-exchange schema)
-- Pattern: identical to bronze_binance_to_silver_mv / bronze_coinbase_to_silver_mv
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Normalizations performed:
-- - symbol: "BTCUSD" → canonical_symbol="BTC/USD" (strip last 3 chars = "USD")
--   Note: XBT→BTC normalization is done upstream in the bronze MV
-- - price/quantity: Decimal(18,8) → Decimal128(8) for higher precision
-- - ingestion_timestamp: DateTime → DateTime64(6, 'UTC')
-- - vendor_data: kafka offset/partition preserved
-- - side: UNKNOWN (v2 bronze does not carry side; dropped from raw topic)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE MATERIALIZED VIEW IF NOT EXISTS k2.bronze_kraken_to_silver_mv
TO k2.silver_trades AS
SELECT
    generateUUIDv4() AS message_id,
    concat('KRAKEN-', toString(sequence_number)) AS trade_id,
    'kraken' AS exchange,
    symbol,
    -- Canonical: BTCUSD → BTC/USD (last 3 chars = "USD")
    concat(substring(symbol, 1, length(symbol) - 3), '/USD') AS canonical_symbol,
    'crypto' AS asset_class,
    'USD' AS currency,
    CAST(price AS Decimal128(8)) AS price,
    CAST(quantity AS Decimal128(8)) AS quantity,
    CAST(quote_volume AS Decimal128(8)) AS quote_volume,
    -- Note: side not available in v2 bronze (raw topic doesn't carry it through)
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
        if(price <= 0, ['invalid_price'],  []),
        if(quantity <= 0, ['invalid_volume'], [])
    ) AS validation_errors
FROM k2.bronze_trades_kraken;

-- ═══════════════════════════════════════════════════════════════════════════
-- Verification Queries
-- ═══════════════════════════════════════════════════════════════════════════

-- Verify normalization (XBT→BTC done in bronze, symbol arrives already normalized):
-- SELECT exchange, canonical_symbol, count() AS trades
-- FROM k2.silver_trades
-- WHERE exchange = 'kraken'
-- GROUP BY exchange, canonical_symbol
-- ORDER BY canonical_symbol;

-- Cross-exchange BTC price comparison (Kraken + Coinbase share BTC/USD):
-- SELECT exchange, canonical_symbol, max(timestamp) AS latest, argMax(price, timestamp) AS last_price
-- FROM k2.silver_trades
-- WHERE canonical_symbol = 'BTC/USD'
-- GROUP BY exchange, canonical_symbol
-- ORDER BY exchange;

-- OHLCV cross-exchange (coinbase + kraken share BTC/USD):
-- SELECT exchange, canonical_symbol, window_start, close_price, trade_count
-- FROM k2.ohlcv_1m
-- WHERE canonical_symbol = 'BTC/USD'
-- ORDER BY window_start DESC, exchange
-- LIMIT 12;
