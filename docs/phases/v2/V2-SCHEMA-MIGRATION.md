# V2 Schema Migration - Multi-Asset Support

**Date**: 2026-02-09
**Status**: ✅ Dual-Write Validated, Ready for Cutover
**Duration**: ~1.5 hours

---

## Executive Summary

Successfully migrated ClickHouse Silver layer to align with `trade_v2.avsc` schema, enabling multi-asset class support (crypto, equities, futures, options) and better cross-exchange aggregation. Migration uses dual-write strategy for zero-downtime validation before cutover.

---

## Why V2 Schema?

### Current Limitations (V1)
- ❌ Crypto-only (no equities, futures, options support)
- ❌ No proper deduplication across exchanges
- ❌ Loses exchange-specific data
- ❌ Limited timestamp precision (milliseconds)
- ❌ No industry-standard schema alignment

### V2 Benefits
- ✅ **Multi-Asset Support**: crypto, equities, futures, options
- ✅ **UUID Deduplication**: Proper message_id for cross-source dedup
- ✅ **Vendor Data Preservation**: Map field for exchange-specific fields
- ✅ **Higher Precision**: Decimal128(8), microsecond timestamps
- ✅ **Industry Standard**: Aligns with trade_v2.avsc schema
- ✅ **Future-Proof**: Ready for ASX equities, crypto options, etc.

---

## Schema Comparison

| Field | V1 Schema | V2 Schema | Improvement |
|-------|-----------|-----------|-------------|
| **Deduplication** | trade_id only | UUID message_id | ✅ Proper UUID |
| **Asset Class** | ❌ None | Enum (crypto/equities/futures/options) | ✅ Multi-asset |
| **Currency** | ❌ Implicit | Explicit (USDT, AUD, BTC) | ✅ Clear currency |
| **Price Precision** | Decimal64(8) | Decimal128(8) | ✅ Higher precision |
| **Timestamp** | DateTime64(3) ms | DateTime64(6) μs | ✅ Microseconds |
| **Side** | buy/sell | BUY/SELL/SELL_SHORT/UNKNOWN | ✅ Short sales |
| **Vendor Data** | ❌ Lost | Map(String, String) | ✅ Preserved |
| **Trade Conditions** | ❌ None | Array(String) | ✅ Exchange codes |
| **Sequencing** | Basic | source_sequence + platform_sequence | ✅ Better ordering |

---

## Migration Architecture

### Dual-Write Strategy (Zero Downtime)

```
Bronze Layer
    ↓
    ├─→ bronze_trades_mv (v1) → silver_trades (v1)
    └─→ bronze_trades_mv_v2    → silver_trades_v2  ← NEW
            ↓
        Gold Layer (6 OHLCV timeframes)
```

Both v1 and v2 run in parallel, allowing validation before cutover.

---

## Implementation Details

### 1. Silver Trades V2 Table

**New Fields**:
```sql
message_id UUID                   -- Proper deduplication
asset_class Enum8(...)            -- Multi-asset support
currency LowCardinality(String)   -- Explicit currency
price Decimal128(8)               -- Higher precision
timestamp DateTime64(6, 'UTC')    -- Microsecond precision
vendor_data Map(String, String)   -- Exchange-specific data
source_sequence Nullable(UInt64)  -- Exchange sequence
platform_sequence Nullable(UInt64)-- Platform sequence
```

**Partitioning**: `(exchange, asset_class, toYYYYMMDD(timestamp))`
- Isolates assets by class for efficient queries
- Daily partitions for TTL management

### 2. Bronze → Silver V2 Transformation

**Key Transformations**:
1. **Generate UUID**: `generateUUIDv4()` for each trade
2. **Prefix trade_id**: `BINANCE-{trade_id}` for cross-exchange uniqueness
3. **Extract currency**: Parse from symbol (BTCUSDT → USDT)
4. **Parse vendor data**: Extract Binance-specific fields from metadata JSON
   ```sql
   vendor_data: {
       'event_type': 'trade',
       'is_buyer_maker': 'true',
       'buyer_order_id': '123456',
       'seller_order_id': '789012'
   }
   ```
5. **Convert timestamps**: Milliseconds → Microseconds
6. **Map side enum**: buy/sell → BUY/SELL

### 3. Gold Layer OHLCV Update

**Changed**: All 6 OHLCV MVs now read from `silver_trades_v2`
- Uses `timestamp` (microseconds) instead of `exchange_timestamp`
- No other changes required - aggregation logic identical

---

## Validation Results ✅

### Data Quality Validation (1-Minute Window)

| Metric | V1 | V2 | Match |
|--------|----|----|-------|
| Trade Count | 4,473 | 4,473 | ✅ Identical |
| Avg Price | $34,438.47 | $34,438.47 | ✅ Identical |
| Total Volume | 194.82 BTC | 194.82 BTC | ✅ Identical |

### Schema Validation

```sql
-- Sample V2 Record
message_id:       c79a14d3-5f81-4a38-be79-988fdbffb14b  ✅ UUID
trade_id:         BINANCE-3630682060                     ✅ Prefixed
exchange:         binance                                ✅
asset_class:      crypto                                 ✅ Enum
currency:         USDT                                   ✅ Extracted
price:            2034.78                                ✅ Decimal128
timestamp:        2026-02-09 12:47:06.576000            ✅ Microseconds
vendor_data:      {event_type: trade, is_buyer_maker: 1} ✅ Parsed
is_valid:         true                                   ✅
```

### Data Flow Validation

```
Bronze:     1,239,265 trades (v1 accumulated)
Silver v2:     12,369 trades (new, growing)
Gold 1m:          323 candles (from v2)
Gold 5m:           47 candles (from v2)
Gold 1h:            5 candles (from v2)
```

All Gold layer OHLCV candles generating correctly from v2. ✅

---

## Cutover Plan

### Pre-Cutover Checklist
- [x] V2 table created and populating
- [x] V2 MV processing trades correctly
- [x] Gold layer reading from v2
- [x] Data quality validated (v1 == v2)
- [x] Vendor data parsing working
- [x] Cutover script prepared
- [ ] **Run for 24 hours** (stability test)
- [ ] **User approval** for final cutover

### Cutover Steps (When Ready)

Run `docker/clickhouse/schema/07-v2-cutover.sql`:

1. **Drop v1 MV**: Stop dual-write
2. **Archive v1**: `RENAME silver_trades → silver_trades_v1_archive`
3. **Promote v2**: `RENAME silver_trades_v2 → silver_trades`
4. **Rename MV**: `RENAME bronze_trades_mv_v2 → bronze_trades_mv`

**Estimated Downtime**: ~1 second (RENAME is instant in ClickHouse)

### Post-Cutover Validation
1. Verify new data flowing to `silver_trades`
2. Check Gold layer still generating candles
3. Validate schema shows v2 fields
4. Monitor for 24 hours
5. Drop archive table after validation

### Rollback (If Needed)
Script includes rollback procedure to restore v1 as primary.

---

## Future Benefits

### Multi-Asset Analytics
```sql
-- Cross-asset volume comparison
SELECT
    asset_class,
    sum(quantity * price) as total_volume
FROM silver_trades
GROUP BY asset_class
ORDER BY total_volume DESC;
```

### Vendor-Specific Analysis
```sql
-- Analyze buyer vs seller maker ratios
SELECT
    exchange,
    vendor_data['is_buyer_maker'] as is_buyer_maker,
    count() as trades
FROM silver_trades
WHERE vendor_data != map()
GROUP BY exchange, is_buyer_maker;
```

### Ready for Equities
```sql
-- Add ASX equities (future)
INSERT INTO silver_trades (
    asset_class = 'equities',
    exchange = 'ASX',
    symbol = 'CBA',
    currency = 'AUD',
    ...
)
```

---

## Files Changed

| File | Purpose |
|------|---------|
| `05-silver-v2-migration.sql` | Create silver_trades_v2 + MV |
| `06-gold-layer-v2-migration.sql` | Update OHLCV MVs to read v2 |
| `07-v2-cutover.sql` | Cutover script + rollback |

---

## Lessons Learned

1. **Dual-Write Works**: Zero-downtime migration with parallel v1/v2
2. **Vendor Data Valuable**: Preserving exchange-specific fields helps debugging
3. **Schema Alignment Matters**: Industry-standard schema makes integration easier
4. **Early Migration Better**: Easier now with 1.2M trades than later with billions
5. **UUID Dedup Proper**: message_id better than exchange-specific trade_id

---

## Next Steps

1. **Monitor v2 for 24 hours** (stability validation)
2. **User approval** for cutover
3. **Execute cutover** (`07-v2-cutover.sql`)
4. **Validate 24 hours** post-cutover
5. **Drop v1 archive** after successful validation
6. **Add Kraken support** (multi-exchange with v2 schema)
7. **Prepare for equities** (ASX integration)

---

## Recommendation

**Proceed with cutover after 24-hour stability test.**

V2 schema is:
- ✅ Validated (data matches v1 perfectly)
- ✅ Stable (running 1+ hours with no errors)
- ✅ Future-proof (multi-asset ready)
- ✅ Industry-standard (aligns with trade_v2.avsc)

---

**Migration Status**: ✅ **Ready for Cutover**
**Risk Level**: Low (dual-write validated, rollback prepared)
**Approval Required**: Yes (24-hour test + user sign-off)
