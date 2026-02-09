# Kraken Data Normalization Guide
## Bronze → Silver → Gold Transformation Reference

**Purpose**: Complete reference for how Kraken's native exchange format is normalized through the K2 platform's medallion architecture.

**Date**: 2026-02-10
**Architecture**: Multi-Exchange Bronze (ADR-011)

═══════════════════════════════════════════════════════════════════════════════

## 📋 TABLE OF CONTENTS

1. [Overview](#overview)
2. [Kraken Native Format (WebSocket)](#kraken-native-format)
3. [Bronze Layer (Preservation)](#bronze-layer)
4. [Silver Layer (Normalization)](#silver-layer)
5. [Gold Layer (Aggregation)](#gold-layer)
6. [Field Mapping Reference](#field-mapping)
7. [Normalization Rules](#normalization-rules)
8. [Examples](#examples)

═══════════════════════════════════════════════════════════════════════════════

## 1. OVERVIEW

Kraken uses a unique array-based WebSocket format that differs significantly from other exchanges. The K2 platform preserves this native format in Bronze, normalizes it in Silver, and aggregates it in Gold.

### Normalization Flow

```
WebSocket Array → JSON (Feed Handler) → Bronze (Native) → Silver (Normalized) → Gold (OHLCV)
```

### Key Transformations

| Layer | Format | Key Characteristic |
|-------|--------|-------------------|
| **Source** | Array: `[channelID, [[trade]], "trade", "XBT/USD"]` | Kraken native |
| **Bronze** | Native: `pair="XBT/USD", side='b', timestamp="1770.640394"` | Preserved as-is |
| **Silver** | Normalized: `canonical_symbol="BTC/USD", side=BUY, timestamp=DateTime64` | Unified |
| **Gold** | Aggregated: `OHLCV bars per exchange` | Analytics-ready |

═══════════════════════════════════════════════════════════════════════════════

## 2. KRAKEN NATIVE FORMAT (WebSocket)

### 2.1 WebSocket Message Structure

Kraken sends trade updates as JSON arrays:

```json
[
  0,                                    // channelID (integer)
  [                                     // trades array
    [
      "95381.70000",                    // price (string)
      "0.00032986",                     // volume (string)
      "1737118158.321597",              // timestamp (string: seconds.microseconds)
      "s",                              // side ('b' = buy, 's' = sell)
      "l",                              // orderType ('l' = limit, 'm' = market)
      ""                                // misc (usually empty)
    ]
  ],
  "trade",                              // channel name
  "XBT/USD"                             // pair (XBT, not BTC!)
]
```

### 2.2 Key Kraken Conventions

| Field | Format | Notes |
|-------|--------|-------|
| **Symbol** | `XBT/USD` | Uses `XBT` for Bitcoin (not `BTC`) |
| **Timestamp** | `"1737118158.321597"` | String format: `seconds.microseconds` |
| **Side** | `'b'` or `'s'` | Single character |
| **Order Type** | `'l'` or `'m'` | Limit or Market |
| **Price/Volume** | String | High precision string representation |
| **Trade ID** | Not provided | Must be generated |

═══════════════════════════════════════════════════════════════════════════════

## 3. BRONZE LAYER (Preservation)

### 3.1 Philosophy

**Preserve Kraken's native format exactly as received.** No normalization, no conversion.

### 3.2 Bronze Schema

**Table**: `k2.bronze_trades_kraken`

```sql
CREATE TABLE k2.bronze_trades_kraken (
    -- Native Kraken Fields (preserved as-is)
    channel_id UInt64,
    pair String,                      -- "XBT/USD" (NOT normalized to BTC!)
    price String,                     -- "95381.70000"
    volume String,                    -- "0.00032986"
    timestamp String,                 -- "1737118158.321597" (native format!)
    side Enum8('b' = 1, 's' = 2),   -- 'b' or 's' (native!)
    order_type Enum8('l' = 1, 'm' = 2), -- 'l' or 'm'
    misc String,

    -- Platform metadata
    ingestion_timestamp DateTime64(6, 'UTC'),
    ingested_at DateTime64(6, 'UTC') DEFAULT now64(6),
    _version UInt64 DEFAULT 1

) ENGINE = ReplacingMergeTree(_version)
PARTITION BY (toYYYYMMDD(ingested_at))
ORDER BY (pair, timestamp, price);
```

### 3.3 Bronze Example

```sql
SELECT * FROM k2.bronze_trades_kraken LIMIT 1 FORMAT Vertical;
```

**Output**:
```
channel_id:          119930881
pair:                XBT/USD              ← Native XBT, not BTC!
price:               68435.30000
volume:              0.00032986
timestamp:           1737118158.321597    ← String format!
side:                s                    ← Single char!
order_type:          l
misc:
ingestion_timestamp: 2026-02-09 14:14:59.321597
ingested_at:         2026-02-09 14:15:00.123456
_version:            1
```

### 3.4 Bronze Design Principles

✅ **DO**:
- Preserve exact field names from exchange
- Preserve exact data types (strings stay strings)
- Preserve native symbols (XBT stays XBT)
- Preserve native timestamps formats

❌ **DO NOT**:
- Normalize symbols (XBT → BTC)
- Convert timestamps to DateTime
- Change side formats ('b' → BUY)
- Generate trade IDs

═══════════════════════════════════════════════════════════════════════════════

## 4. SILVER LAYER (Normalization)

### 4.1 Philosophy

**Transform Kraken's native format to the unified multi-exchange schema.**

### 4.2 Normalization Materialized View

**View**: `k2.bronze_kraken_to_silver_v2_mv`

```sql
CREATE MATERIALIZED VIEW k2.bronze_kraken_to_silver_v2_mv
TO k2.silver_trades_v2 AS
SELECT
    -- ═══════════════════════════════════════════════════════════════════════
    -- Identity & Deduplication
    -- ═══════════════════════════════════════════════════════════════════════
    generateUUIDv4() AS message_id,

    -- Generate deterministic trade_id (Kraken doesn't provide)
    concat(
        'KRAKEN-',
        toString(toUInt64(toFloat64(timestamp) * 1000000)),
        '-',
        substring(hex(MD5(concat(pair, price, volume))), 1, 8)
    ) AS trade_id,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Asset Classification (Normalize XBT → BTC)
    -- ═══════════════════════════════════════════════════════════════════════
    'kraken' AS exchange,

    -- Symbol: Remove slash (XBT/USD → BTCUSD), normalize XBT → BTC
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
    splitByChar('/', pair)[2] AS currency,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Trade Data (Convert strings → Decimal128)
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
    -- Timestamps (Convert Kraken "seconds.microseconds" → DateTime64(6))
    -- ═══════════════════════════════════════════════════════════════════════
    fromUnixTimestamp64Micro(toUInt64(toFloat64(timestamp) * 1000000)) AS timestamp,
    ingestion_timestamp AS ingestion_timestamp,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Sequencing
    -- ═══════════════════════════════════════════════════════════════════════
    toUInt64(toFloat64(timestamp) * 1000000) AS source_sequence,
    CAST(NULL AS Nullable(UInt64)) AS platform_sequence,

    -- ═══════════════════════════════════════════════════════════════════════
    -- Vendor Data (Preserve Kraken-specific fields)
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
```

### 4.3 Silver Example

```sql
SELECT * FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
ORDER BY timestamp DESC
LIMIT 1
FORMAT Vertical;
```

**Output**:
```
message_id:          550e8400-e29b-41d4-a716-446655440000
trade_id:            KRAKEN-1737118158321597-A1B2C3D4     ← Generated!
exchange:            kraken
symbol:              BTCUSD                                ← Normalized (no slash)
canonical_symbol:    BTC/USD                               ← Normalized XBT → BTC!
asset_class:         crypto
currency:            USD
price:               68435.30                              ← Decimal128(8)
quantity:            0.00032986                            ← Decimal128(8)
quote_volume:        22.56                                 ← Calculated
side:                SELL                                  ← Normalized enum!
trade_conditions:    []
timestamp:           2026-02-09 14:14:59.321597           ← DateTime64(6)!
ingestion_timestamp: 2026-02-09 14:14:59.500000
source_sequence:     1737118158321597
platform_sequence:   NULL
vendor_data:         {'pair':'XBT/USD',                    ← Original preserved!
                      'order_type':'l',
                      'misc':'',
                      'channel_id':'119930881',
                      'raw_timestamp':'1737118158.321597'}
is_valid:            true
validation_errors:   []
```

═══════════════════════════════════════════════════════════════════════════════

## 5. GOLD LAYER (Aggregation)

### 5.1 Philosophy

**Aggregate normalized trades into OHLCV bars for analytics.**

### 5.2 Gold OHLCV Schema

**Tables**: `k2.ohlcv_1m`, `k2.ohlcv_5m`, `k2.ohlcv_15m`, `k2.ohlcv_1h`, `k2.ohlcv_1d`

```sql
SELECT * FROM k2.ohlcv_5m
WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
ORDER BY window_start DESC
LIMIT 1
FORMAT Vertical;
```

**Output**:
```
exchange:        kraken
canonical_symbol: BTC/USD
window_start:    2026-02-09 14:15:00.000
open_price:      68540.40
high_price:      68566.70
low_price:       68540.40
close_price:     68566.70
volume:          17.39
trade_count:     91
vwap:            68555.23
```

═══════════════════════════════════════════════════════════════════════════════

## 6. FIELD MAPPING REFERENCE

### 6.1 Complete Field Transformation

| Kraken Native (Bronze) | Silver Normalized | Gold OHLCV | Notes |
|------------------------|-------------------|------------|-------|
| `pair: "XBT/USD"` | `canonical_symbol: "BTC/USD"` | `canonical_symbol: "BTC/USD"` | **XBT → BTC** |
| `pair: "XBT/USD"` | `symbol: "BTCUSD"` | - | No slash |
| `pair: "XBT/USD"` | `vendor_data['pair']: "XBT/USD"` | - | **Original preserved** |
| `price: "68435.30000"` (string) | `price: 68435.30` (Decimal128) | `open/high/low/close_price` | Type conversion |
| `volume: "0.00032986"` (string) | `quantity: 0.00032986` (Decimal128) | `volume` (sum) | Type conversion |
| `timestamp: "1737118158.321597"` (string) | `timestamp: 2026-02-09 14:14:59.321597` (DateTime64) | `window_start` | **Format change** |
| `timestamp: "1737118158.321597"` | `vendor_data['raw_timestamp']` | - | **Original preserved** |
| `side: 's'` (enum char) | `side: SELL` (enum) | - | **Enum mapping** |
| `order_type: 'l'` | `vendor_data['order_type']: "l"` | - | Preserved in vendor_data |
| `channel_id: 119930881` | `vendor_data['channel_id']: "119930881"` | - | Preserved |
| Not provided | `trade_id: "KRAKEN-1737118158321597-..."` | - | **Generated** |
| Not provided | `message_id: UUID` | - | Generated |
| Not provided | - | `trade_count: 91` | Aggregated |

### 6.2 Symbol Normalization Rules

| Bronze (Native) | Silver (Canonical) | Rule |
|-----------------|-------------------|------|
| `XBT/USD` | `BTC/USD` | XBT → BTC |
| `XBT/USDT` | `BTC/USDT` | XBT → BTC |
| `ETH/USD` | `ETH/USD` | No change |
| `ETH/USDT` | `ETH/USDT` | No change |

**SQL Logic**:
```sql
concat(
    if(splitByChar('/', pair)[1] = 'XBT', 'BTC', splitByChar('/', pair)[1]),
    '/',
    splitByChar('/', pair)[2]
) AS canonical_symbol
```

### 6.3 Timestamp Conversion

**Input** (Kraken): `"1737118158.321597"` (string: seconds.microseconds)

**Steps**:
1. Convert to float: `toFloat64("1737118158.321597")` → `1737118158.321597`
2. Convert to microseconds: `* 1000000` → `1737118158321597`
3. Cast to UInt64: `toUInt64(...)` → `1737118158321597`
4. Convert to DateTime64: `fromUnixTimestamp64Micro(...)` → `2026-02-09 14:14:59.321597`

**SQL**:
```sql
fromUnixTimestamp64Micro(toUInt64(toFloat64(timestamp) * 1000000)) AS timestamp
```

### 6.4 Side Enum Mapping

| Bronze (Native) | Silver (Normalized) | Notes |
|-----------------|-------------------|-------|
| `'b'` | `BUY` (enum value 1) | Buyer initiated |
| `'s'` | `SELL` (enum value 2) | Seller initiated |

**SQL**:
```sql
CAST(
    CASE toString(side)
        WHEN 'b' THEN 'BUY'
        WHEN 's' THEN 'SELL'
    END AS Enum8('BUY' = 1, 'SELL' = 2, 'SELL_SHORT' = 3, 'UNKNOWN' = 4)
) AS side
```

### 6.5 Trade ID Generation

Since Kraken doesn't provide trade IDs, we generate deterministic ones:

**Format**: `KRAKEN-{timestamp_micros}-{hash}`

**Example**: `KRAKEN-1737118158321597-A1B2C3D4`

**SQL**:
```sql
concat(
    'KRAKEN-',
    toString(toUInt64(toFloat64(timestamp) * 1000000)),
    '-',
    substring(hex(MD5(concat(pair, price, volume))), 1, 8)
) AS trade_id
```

**Properties**:
- Deterministic (same input → same ID)
- Unique (hash includes pair, price, volume)
- Sortable (timestamp prefix)
- Identifiable (KRAKEN prefix)

═══════════════════════════════════════════════════════════════════════════════

## 7. NORMALIZATION RULES

### 7.1 Symbol Normalization

**Rule**: Convert Kraken's `XBT` to `BTC` for canonical symbol

```
Input:  XBT/USD
Output: BTC/USD (canonical_symbol)
        BTCUSD (symbol, no slash)
Preserved: XBT/USD (vendor_data['pair'])
```

**Why?**: Industry standard uses `BTC`, not `XBT`. Cross-exchange queries expect `BTC`.

### 7.2 Timestamp Normalization

**Rule**: Convert Kraken's string timestamp to DateTime64(6)

```
Input:  "1737118158.321597" (string)
Output: 2026-02-09 14:14:59.321597 (DateTime64)
Preserved: "1737118158.321597" (vendor_data['raw_timestamp'])
```

**Why?**: DateTime64 enables time-based queries, sorting, and aggregation.

### 7.3 Side Normalization

**Rule**: Convert Kraken's single-char side to BUY/SELL enum

```
Input:  'b' (Enum8)
Output: BUY (Enum8)

Input:  's' (Enum8)
Output: SELL (Enum8)
```

**Why?**: Unified enum enables cross-exchange queries without format conversion.

### 7.4 Type Conversions

| Field | Bronze Type | Silver Type | Reason |
|-------|-------------|-------------|--------|
| price | String | Decimal128(8) | Arithmetic operations |
| volume | String | Decimal128(8) | Aggregation |
| timestamp | String | DateTime64(6) | Time-based queries |
| side | Enum8('b','s') | Enum8('BUY','SELL') | Standardization |

### 7.5 vendor_data Preservation

**Rule**: Always preserve original Bronze fields in `vendor_data` map

**Preserved Fields**:
- `pair`: Original Kraken pair (e.g., "XBT/USD")
- `raw_timestamp`: Original Kraken timestamp string
- `order_type`: 'l' or 'm'
- `channel_id`: WebSocket channel ID
- `misc`: Any miscellaneous data

**Why?**:
- Audit trail back to Bronze
- Debugging against exchange documentation
- Support for exchange-specific queries

═══════════════════════════════════════════════════════════════════════════════

## 8. EXAMPLES

### 8.1 Complete Transformation Example

**WebSocket Message** (Kraken):
```json
[0, [["68435.30000", "0.00032986", "1737118158.321597", "s", "l", ""]], "trade", "XBT/USD"]
```

**Bronze Record** (Preserved):
```
channel_id: 0
pair: XBT/USD
price: "68435.30000"
volume: "0.00032986"
timestamp: "1737118158.321597"
side: s
order_type: l
misc: ""
```

**Silver Record** (Normalized):
```
trade_id: KRAKEN-1737118158321597-A1B2C3D4
exchange: kraken
canonical_symbol: BTC/USD
symbol: BTCUSD
price: 68435.30 (Decimal128)
quantity: 0.00032986 (Decimal128)
side: SELL (enum)
timestamp: 2026-02-09 14:14:59.321597 (DateTime64)
vendor_data: {
    'pair': 'XBT/USD',
    'raw_timestamp': '1737118158.321597',
    'order_type': 'l',
    'channel_id': '0'
}
```

**Gold Record** (Aggregated 5min):
```
exchange: kraken
canonical_symbol: BTC/USD
window_start: 2026-02-09 14:15:00
open_price: 68435.30
high_price: 68566.70
low_price: 68410.80
close_price: 68500.00
volume: 17.39
trade_count: 91
```

### 8.2 Query Examples

**Find Original Kraken Pair**:
```sql
SELECT
    canonical_symbol,
    vendor_data['pair'] as original_kraken_pair,
    count() as trades
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
GROUP BY canonical_symbol, vendor_data['pair'];

-- Result:
-- canonical_symbol | original_kraken_pair | trades
-- BTC/USD          | XBT/USD              | 1234
-- ETH/USD          | ETH/USD              | 567
```

**Verify Timestamp Conversion**:
```sql
SELECT
    vendor_data['raw_timestamp'] as kraken_native,
    timestamp as normalized,
    toUnixTimestamp64Micro(timestamp) as micros
FROM k2.silver_trades_v2
WHERE exchange = 'kraken'
LIMIT 5;
```

**Cross-Exchange BTC Comparison**:
```sql
SELECT
    exchange,
    canonical_symbol,
    count() as trades,
    round(avg(toFloat64(price)), 2) as avg_price
FROM k2.silver_trades_v2
WHERE canonical_symbol IN ('BTC/USD', 'BTC/USDT')
GROUP BY exchange, canonical_symbol;

-- Both Binance (BTC/USDT) and Kraken (BTC/USD) included!
```

═══════════════════════════════════════════════════════════════════════════════

## SUMMARY

### Key Normalizations

1. **Symbol**: `XBT/USD` → `BTC/USD` (canonical_symbol)
2. **Timestamp**: `"1737118158.321597"` → `DateTime64(2026-02-09 14:14:59.321597)`
3. **Side**: `'s'` → `SELL` (enum)
4. **Type**: Strings → Decimal128/DateTime64
5. **Trade ID**: Generated (Kraken doesn't provide)
6. **Preservation**: All originals in `vendor_data`

### Benefits

✅ **Bronze**: Exchange truth preserved (XBT/USD)
✅ **Silver**: Cross-exchange queries enabled (BTC/USD)
✅ **Gold**: Unified analytics (both exchanges in OHLCV)
✅ **Audit**: vendor_data provides complete trail

### References

- **Implementation**: `docker/clickhouse/schema/09-silver-kraken-to-v2.sql`
- **Testing**: `docs/testing/kraken-integration-testing.md`
- **ADR**: `docs/decisions/platform-v2/ADR-011-multi-exchange-bronze-architecture.md`
