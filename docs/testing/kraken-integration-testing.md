# Kraken Integration Testing Guide

## Overview

This guide covers testing the Kraken exchange integration following the multi-exchange Bronze → Silver → Gold architecture.

**Key Architecture Principle**: Exchange-native schemas in Bronze, normalization in Silver.

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│ BRONZE LAYER (Exchange-Native Schemas)                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  bronze_trades_binance         bronze_trades_kraken                │
│  ├─ symbol: "BTCUSDT"          ├─ pair: "XBT/USD" ← Native!        │
│  ├─ trade_id: 12345            ├─ timestamp: "1737.321597"         │
│  ├─ is_buyer_maker: true       ├─ side: "s"                        │
│  └─ trade_time_ms: 1705...     └─ order_type: "l"                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                              ↓↓
┌─────────────────────────────────────────────────────────────────────┐
│ SILVER LAYER (Unified Multi-Exchange Schema)                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  silver_trades (normalized)                                      │
│  ├─ exchange: "binance" / "kraken"                                 │
│  ├─ canonical_symbol: "BTC/USD" ← Normalized from XBT              │
│  ├─ vendor_data: {"pair": "XBT/USD", ...} ← Original preserved     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                              ↓↓
┌─────────────────────────────────────────────────────────────────────┐
│ GOLD LAYER (Aggregated Analytics)                                  │
├─────────────────────────────────────────────────────────────────────┤
│  ohlcv_1m: Cross-exchange aggregation for BTC/USD                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

1. **Platform v2 running**:
   ```bash
   docker compose -f docker-compose.v2.yml up -d
   ```

2. **ClickHouse accessible**:
   ```bash
   docker exec k2-clickhouse clickhouse-client --query "SELECT 1"
   ```

3. **Redpanda accessible**:
   ```bash
   docker exec k2-redpanda rpk cluster info
   ```

## Test Phases

### Phase 1: Feed Handler Testing

#### 1.1 Build and Start Kraken Feed Handler

```bash
# Build feed handler image (if not already built)
docker compose -f docker-compose.v2.yml build feed-handler-binance

# Start Kraken feed handler
docker compose -f docker-compose.v2.yml -f services/feed-handler-kotlin/docker-compose.feed-handlers.yml up -d feed-handler-kraken
```

#### 1.2 Verify Feed Handler is Running

```bash
# Check container status
docker ps | grep kraken

# Check logs for connection success
docker logs -f k2-feed-handler-kraken

# Expected log output:
# ✅ Connected to Kraken WebSocket
# 📡 Subscribed to 2 trade streams: XBT/USD, ETH/USD
# Trade: XBT/USD 95381.70 × 0.00032986 (s)
```

#### 1.3 Verify Kafka Topic Creation

```bash
# List topics (should see market.crypto.trades.kraken.raw)
docker exec k2-redpanda rpk topic list

# Consume sample messages
docker exec k2-redpanda rpk topic consume market.crypto.trades.kraken.raw --num 5

# Expected JSON format:
# {
#   "channel_id": 0,
#   "pair": "XBT/USD",
#   "price": "95381.70000",
#   "volume": "0.00032986",
#   "timestamp": "1737118158.321597",
#   "side": "s",
#   "order_type": "l",
#   "misc": "",
#   "ingestion_timestamp": 1737118158500000
# }
```

### Phase 2: Bronze Layer Validation

#### 2.1 Verify Bronze Table Exists

```bash
docker exec k2-clickhouse clickhouse-client --query "
SHOW TABLES FROM k2 LIKE '%kraken%';
"
# Expected: bronze_trades_kraken, trades_kraken_queue, bronze_trades_kraken_mv
```

#### 2.2 Check Bronze Data

```sql
-- Count trades
SELECT count() FROM k2.bronze_trades_kraken;

-- Verify native format (should show XBT/USD, not BTC/USD)
SELECT pair, count() as trades
FROM k2.bronze_trades_kraken
GROUP BY pair;

-- Expected:
-- ┌─pair────┬─trades─┐
-- │ XBT/USD │   1234 │
-- │ ETH/USD │    567 │
-- └─────────┴────────┘

-- Sample record (verify native format)
SELECT * FROM k2.bronze_trades_kraken
ORDER BY ingested_at DESC LIMIT 1
FORMAT Vertical;

-- Expected:
-- pair:       XBT/USD  ← NOT BTC/USD!
-- timestamp:  1737118158.321597  ← String format
-- side:       s  ← 'b' or 's'
```

#### 2.3 Verify Timestamp Preservation

```sql
-- Check timestamp format (should be string "seconds.microseconds")
SELECT
    timestamp,
    length(timestamp) as timestamp_length,
    toFloat64(timestamp) as parsed_seconds
FROM k2.bronze_trades_kraken
LIMIT 5;

-- Expected: timestamp = "1737118158.321597" (string)
```

### Phase 3: Silver Layer Validation

#### 3.1 Verify Normalization (XBT → BTC)

```sql
-- Check XBT → BTC normalization
SELECT
    exchange,
    canonical_symbol,
    vendor_data['pair'] as original_pair,
    count() as trades
FROM k2.silver_trades
WHERE exchange = 'kraken'
GROUP BY exchange, canonical_symbol, original_pair;

-- Expected:
-- ┌─exchange─┬─canonical_symbol─┬─original_pair─┬─trades─┐
-- │ kraken   │ BTC/USD          │ XBT/USD       │   1234 │
-- │ kraken   │ ETH/USD          │ ETH/USD       │    567 │
-- └──────────┴──────────────────┴───────────────┴────────┘
```

#### 3.2 Verify Side Enum Mapping

```sql
-- Verify side mapping (b/s → BUY/SELL)
SELECT side, count() as trades
FROM k2.silver_trades
WHERE exchange = 'kraken'
GROUP BY side;

-- Expected:
-- ┌─side─┬─trades─┐
-- │ BUY  │    600 │
-- │ SELL │    634 │
-- └──────┴────────┘
```

#### 3.3 Verify Timestamp Conversion

```sql
-- Check timestamp conversion (string → DateTime64)
SELECT
    vendor_data['raw_timestamp'] as original_timestamp,
    timestamp,
    toTypeName(timestamp) as timestamp_type
FROM k2.silver_trades
WHERE exchange = 'kraken'
ORDER BY timestamp DESC LIMIT 3;

-- Expected:
-- original_timestamp: "1737118158.321597" (preserved)
-- timestamp: 2025-01-17 12:15:58.321597 (converted to DateTime64)
-- timestamp_type: DateTime64(6, 'UTC')
```

#### 3.4 Sample Silver Record

```sql
SELECT
    exchange,
    canonical_symbol,
    symbol,
    price,
    quantity,
    side,
    timestamp,
    vendor_data
FROM k2.silver_trades
WHERE exchange = 'kraken'
ORDER BY timestamp DESC LIMIT 1
FORMAT Vertical;

-- Verify:
-- ✅ canonical_symbol = "BTC/USD" (normalized)
-- ✅ vendor_data['pair'] = "XBT/USD" (original preserved)
-- ✅ side = BUY/SELL (enum)
-- ✅ timestamp = DateTime64(6)
```

### Phase 4: Gold Layer Validation (Cross-Exchange)

#### 4.1 Verify Cross-Exchange Aggregation

```sql
-- Check OHLCV includes both exchanges
SELECT
    exchange,
    canonical_symbol,
    window_start,
    close_price,
    trade_count
FROM k2.ohlcv_1m
WHERE canonical_symbol = 'BTC/USD'
ORDER BY window_start DESC, exchange
LIMIT 20;

-- Expected: Both 'binance' and 'kraken' rows
```

#### 4.2 Compare Exchange Data

```sql
-- Compare trade counts across exchanges
SELECT
    exchange,
    canonical_symbol,
    count() as trades,
    min(timestamp) as earliest,
    max(timestamp) as latest
FROM k2.silver_trades
WHERE canonical_symbol LIKE 'BTC/%'
GROUP BY exchange, canonical_symbol;

-- Expected: Data from both binance and kraken
```

### Phase 5: Automated Validation Script

Run the comprehensive validation script:

```bash
docker exec k2-clickhouse clickhouse-client --multiquery < docker/clickhouse/validation/validate-kraken-integration.sql
```

**Expected Output Sections**:
1. ✅ Bronze Layer: Native format (XBT/USD)
2. ✅ Silver Layer: Normalized (BTC/USD) with vendor_data preservation
3. ✅ Cross-Exchange: Both exchanges present
4. ✅ Gold Layer: Aggregated OHLCV
5. ✅ Data Quality: No validation errors

## Success Criteria Checklist

- [ ] **Feed Handler**: Kraken WebSocket connected and producing JSON
- [ ] **Kafka Topic**: `market.crypto.trades.kraken.raw` contains valid messages
- [ ] **Bronze Layer**:
  - [ ] `bronze_trades_kraken` table has data
  - [ ] `pair` field shows "XBT/USD" (not BTC/USD)
  - [ ] `timestamp` field is string "seconds.microseconds"
- [ ] **Silver Layer**:
  - [ ] `canonical_symbol` shows "BTC/USD" (normalized)
  - [ ] `vendor_data['pair']` shows "XBT/USD" (original preserved)
  - [ ] `side` is BUY/SELL enum (not 'b'/'s')
  - [ ] `timestamp` is DateTime64(6)
- [ ] **Gold Layer**:
  - [ ] OHLCV tables contain both 'binance' and 'kraken' data
  - [ ] BTC/USD candles aggregate from both exchanges
- [ ] **Data Quality**:
  - [ ] `is_valid = true` for all trades
  - [ ] No validation errors
  - [ ] Latency < 1 second (p99)

## Troubleshooting

### Feed Handler Not Connecting

```bash
# Check environment variables
docker exec k2-feed-handler-kraken env | grep K2_

# Check Redpanda connectivity
docker exec k2-feed-handler-kraken nc -zv redpanda 9092

# Check DNS resolution
docker exec k2-feed-handler-kraken nslookup redpanda
```

### No Data in Bronze Table

```bash
# Check Kafka consumer group
docker exec k2-redpanda rpk group describe clickhouse_bronze_kraken_consumer

# Check ClickHouse Kafka Engine status
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM system.kafka_consumers WHERE table = 'trades_kraken_queue';
"
```

### Data Not Flowing to Silver

```bash
# Check Materialized View exists
docker exec k2-clickhouse clickhouse-client --query "
SHOW CREATE TABLE k2.bronze_kraken_to_silver_v2_mv;
"

# Check for errors in system log
docker exec k2-clickhouse clickhouse-client --query "
SELECT * FROM system.query_log
WHERE query LIKE '%bronze_kraken%'
  AND exception != ''
ORDER BY event_time DESC LIMIT 10;
"
```

## Performance Monitoring

### Real-Time Metrics

```sql
-- Trade throughput (trades per second)
SELECT
    exchange,
    round(count() / dateDiff('second', min(timestamp), max(timestamp)), 2) as trades_per_sec
FROM k2.silver_trades
WHERE timestamp >= now() - INTERVAL 5 MINUTE
GROUP BY exchange;

-- Latency (ingestion to processing)
SELECT
    exchange,
    quantile(0.5)(toUnixTimestamp64Milli(ingestion_timestamp) - toUnixTimestamp64Milli(timestamp)) as p50_ms,
    quantile(0.95)(toUnixTimestamp64Milli(ingestion_timestamp) - toUnixTimestamp64Milli(timestamp)) as p95_ms,
    quantile(0.99)(toUnixTimestamp64Milli(ingestion_timestamp) - toUnixTimestamp64Milli(timestamp)) as p99_ms
FROM k2.silver_trades
WHERE timestamp >= now() - INTERVAL 5 MINUTE
GROUP BY exchange;
```

## Next Steps

After successful validation:

1. **Monitor for 24 hours** to ensure stability
2. **Add more symbols** if needed (edit `K2_SYMBOLS` in docker-compose)
3. **Add additional exchanges** following the same pattern
4. **Update Grafana dashboards** to include Kraken metrics
5. **Document operational runbooks** for Kraken-specific issues

## References

- **Architecture Docs**: `docs/architecture/system-design.md`
- **Schema V2 Guide**: `docs/architecture/schema-v2-crypto-guide.md`
- **ClickHouse Schemas**: `docker/clickhouse/schema/08-bronze-kraken.sql`, `09-silver-kraken-to-v2.sql`
- **Feed Handler Code**: `services/feed-handler-kotlin/src/main/kotlin/com/k2/feedhandler/KrakenWebSocketClient.kt`
