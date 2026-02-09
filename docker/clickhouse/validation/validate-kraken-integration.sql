-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Kraken Integration Validation Queries
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- Purpose: Verify that Kraken data flows correctly through Bronze → Silver → Gold
--
-- Success Criteria:
-- ✅ Bronze preserves Kraken native format (XBT, not BTC)
-- ✅ Silver normalizes XBT → BTC
-- ✅ vendor_data preserves original Kraken pair
-- ✅ Gold layer aggregates both Binance and Kraken
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ═══════════════════════════════════════════════════════════════════════════
-- 1. BRONZE LAYER: Verify Native Format Preservation
-- ═══════════════════════════════════════════════════════════════════════════

SELECT '═══ 1. BRONZE LAYER: Kraken Native Format ═══' AS section;

-- Check Kraken Bronze table exists and has data
SELECT
    'bronze_trades_kraken' AS table_name,
    count() as total_trades,
    min(ingested_at) as earliest,
    max(ingested_at) as latest,
    round(count() / dateDiff('second', min(ingested_at), max(ingested_at)), 2) as trades_per_sec
FROM k2.bronze_trades_kraken;

-- Verify native pair format (should show XBT/USD, not BTC/USD)
SELECT
    '  Pairs (Native Format)' AS check_name,
    pair,
    count() as trades,
    min(timestamp) as earliest,
    max(timestamp) as latest
FROM k2.bronze_trades_kraken
GROUP BY pair
ORDER BY pair;

-- Sample Bronze record (verify native format)
SELECT '  Sample Record (Native Format)' AS check_name;
SELECT * FROM k2.bronze_trades_kraken
ORDER BY ingested_at DESC LIMIT 1
FORMAT Vertical;

-- Verify timestamp format (should be "seconds.microseconds" string)
SELECT
    '  Timestamp Format Check' AS check_name,
    timestamp AS raw_timestamp,
    length(timestamp) AS timestamp_length,
    toFloat64(timestamp) AS parsed_seconds
FROM k2.bronze_trades_kraken
ORDER BY ingested_at DESC LIMIT 3;

-- ═══════════════════════════════════════════════════════════════════════════
-- 2. SILVER LAYER: Verify Normalization (XBT → BTC)
-- ═══════════════════════════════════════════════════════════════════════════

SELECT '═══ 2. SILVER LAYER: Normalization ═══' AS section;

-- Verify XBT → BTC normalization
SELECT
    'silver_trades_v2 (Kraken)' AS table_name,
    exchange,
    canonical_symbol,
    vendor_data['pair'] as original_pair,
    count() as trades
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
GROUP BY exchange, canonical_symbol, original_pair
ORDER BY canonical_symbol;

-- Sample Silver record (verify normalization)
SELECT '  Sample Record (Normalized)' AS check_name;
SELECT
    exchange,
    canonical_symbol,
    symbol,
    vendor_data['pair'] as original_pair,
    vendor_data['raw_timestamp'] as original_timestamp,
    price,
    quantity,
    side,
    timestamp,
    ingestion_timestamp
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
ORDER BY timestamp DESC LIMIT 1
FORMAT Vertical;

-- Verify side enum mapping (b/s → BUY/SELL)
SELECT
    '  Side Enum Mapping' AS check_name,
    side,
    count() as trades
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
GROUP BY side;

-- ═══════════════════════════════════════════════════════════════════════════
-- 3. CROSS-EXCHANGE: Compare Binance vs Kraken
-- ═══════════════════════════════════════════════════════════════════════════

SELECT '═══ 3. CROSS-EXCHANGE: Binance vs Kraken ═══' AS section;

-- Compare trade counts across exchanges
SELECT
    'Trade Count Comparison' AS check_name,
    exchange,
    canonical_symbol,
    count() as trades,
    min(timestamp) as earliest,
    max(timestamp) as latest
FROM k2.silver_trades_v2
WHERE canonical_symbol IN ('BTC/USD', 'BTC/USDT', 'ETH/USD', 'ETH/USDT')
GROUP BY exchange, canonical_symbol
ORDER BY canonical_symbol, exchange;

-- Compare price ranges (should be similar for same asset)
SELECT
    'Price Range Comparison (BTC)' AS check_name,
    exchange,
    canonical_symbol,
    currency,
    round(min(toFloat64(price)), 2) as min_price,
    round(max(toFloat64(price)), 2) as max_price,
    round(avg(toFloat64(price)), 2) as avg_price,
    count() as trades
FROM k2.silver_trades_v2
WHERE canonical_symbol LIKE 'BTC/%'
GROUP BY exchange, canonical_symbol, currency
ORDER BY exchange, canonical_symbol;

-- ═══════════════════════════════════════════════════════════════════════════
-- 4. GOLD LAYER: Verify Cross-Exchange Aggregation
-- ═══════════════════════════════════════════════════════════════════════════

SELECT '═══ 4. GOLD LAYER: Cross-Exchange Aggregation ═══' AS section;

-- Verify OHLCV 1-minute aggregation includes both exchanges
SELECT
    'ohlcv_1m (Recent)' AS table_name,
    exchange,
    canonical_symbol,
    window_start,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    trade_count
FROM k2.ohlcv_1m
WHERE canonical_symbol IN ('BTC/USD', 'BTC/USDT')
  AND window_start >= now() - INTERVAL 10 MINUTE
ORDER BY window_start DESC, exchange, canonical_symbol
LIMIT 20;

-- Compare OHLCV across exchanges (should see both binance and kraken)
SELECT
    'OHLCV Exchange Coverage' AS check_name,
    exchange,
    canonical_symbol,
    count() as candles,
    min(window_start) as earliest_candle,
    max(window_start) as latest_candle
FROM k2.ohlcv_1m
WHERE canonical_symbol LIKE 'BTC/%'
  OR canonical_symbol LIKE 'ETH/%'
GROUP BY exchange, canonical_symbol
ORDER BY canonical_symbol, exchange;

-- ═══════════════════════════════════════════════════════════════════════════
-- 5. DATA QUALITY: Validation Checks
-- ═══════════════════════════════════════════════════════════════════════════

SELECT '═══ 5. DATA QUALITY: Validation Checks ═══' AS section;

-- Check for invalid trades
SELECT
    'Invalid Trades' AS check_name,
    exchange,
    is_valid,
    validation_errors,
    count() as trades
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
GROUP BY exchange, is_valid, validation_errors;

-- Check for missing vendor_data
SELECT
    'Vendor Data Coverage' AS check_name,
    exchange,
    if(mapKeys(vendor_data) = [], 'EMPTY', 'OK') as vendor_data_status,
    count() as trades
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
GROUP BY exchange, vendor_data_status;

-- Verify timestamp alignment (ingestion vs exchange timestamp)
SELECT
    'Timestamp Alignment (Kraken)' AS check_name,
    round(avg(toUnixTimestamp64Milli(ingestion_timestamp) - toUnixTimestamp64Milli(timestamp)), 2) as avg_latency_ms,
    round(quantile(0.5)(toUnixTimestamp64Milli(ingestion_timestamp) - toUnixTimestamp64Milli(timestamp)), 2) as p50_latency_ms,
    round(quantile(0.95)(toUnixTimestamp64Milli(ingestion_timestamp) - toUnixTimestamp64Milli(timestamp)), 2) as p95_latency_ms,
    round(quantile(0.99)(toUnixTimestamp64Milli(ingestion_timestamp) - toUnixTimestamp64Milli(timestamp)), 2) as p99_latency_ms
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
  AND timestamp >= now() - INTERVAL 1 HOUR;

-- ═══════════════════════════════════════════════════════════════════════════
-- Summary
-- ═══════════════════════════════════════════════════════════════════════════

SELECT '═══ VALIDATION SUMMARY ═══' AS section;

SELECT
    'Expected Results' AS check_category,
    'Bronze: pair = "XBT/USD"' AS check_1,
    'Silver: canonical_symbol = "BTC/USD", vendor_data["pair"] = "XBT/USD"' AS check_2,
    'Gold: Both exchanges present in OHLCV' AS check_3;
