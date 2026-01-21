# Data Dictionary - V2 Schemas

**Version**: 2.0
**Last Updated**: 2026-01-22
**Status**: Active (V2 schemas operational across all data sources)
**Audience**: Data Engineers, Analysts, API Consumers

---

## Quick Reference

| Schema | Kafka Topic | Iceberg Table | Key Fields |
|--------|-------------|---------------|------------|
| TradeV2 | `market.{asset_class}.trades.{exchange}` | `market_data.trades_v2` | message_id, symbol, price, quantity, timestamp |
| QuoteV2 | `market.{asset_class}.quotes.{exchange}` | `market_data.quotes_v2` | bid_price, ask_price, bid_size, ask_size |
| ReferenceDataV2 | `market.reference_data` | `market_data.reference_v2` | symbol, exchange, currency, lot_size |

**Common Enums**:
- `asset_class`: crypto, futures, options, forex
- `side`: BUY, SELL, SELL_SHORT, UNKNOWN

**Precision**: Decimal(18,8) for price/quantity, microsecond timestamps

---

## Overview

K2 uses **industry-standard hybrid schemas (V2)** that support multiple data sources and asset classes with a common core structure plus vendor-specific extensions.

**Schema Design Philosophy**:
- **Core Standard Fields**: Common fields across all data sources (symbol, exchange, timestamp, price, quantity)
- **Vendor Extensions**: Exchange-specific fields stored in `vendor_data` map
- **Multi-Asset Support**: Single schema for crypto, futures, options, forex via `asset_class` enum
- **High Precision**: Decimal (18,8) for price/quantity, microsecond timestamps
- **BACKWARD Compatible**: Schema evolution supported via Confluent Schema Registry

**V2 Schemas**:
1. [TradeV2](#tradev2-schema) - Trade execution events
2. [QuoteV2](#quotev2-schema) - Best bid/ask quotes
3. [ReferenceDataV2](#referencedatav2-schema) - Instrument reference data

---

## TradeV2 Schema

**Purpose**: Trade execution event (tick data)
**Namespace**: `com.k2.marketdata.TradeV2`
**Schema File**: `src/k2/schemas/trade_v2.avsc`
**Kafka Topic**: `market.{asset_class}.trades.{exchange}` (e.g., `market.crypto.trades.binance`)
**Iceberg Table**: `market_data.trades_v2`

### Core Fields

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `message_id` | string | Yes | UUID v4 for deduplication (generated at ingestion) | `"550e8400-e29b-41d4-a716-446655440000"` |
| `trade_id` | string | Yes | Exchange-specific trade ID. Format: `{EXCHANGE}-{id}` | `"BINANCE-789012"`, `"KRAKEN-123456"` |
| `symbol` | string | Yes | Trading symbol | `"BTCUSDT"`, `"ETHUSDT"`, `"BTC-PERP"` |
| `exchange` | string | Yes | Exchange code | `"BINANCE"`, `"KRAKEN"`, `"COINBASE"`, `"BYBIT"` |
| `asset_class` | enum | Yes | Asset class: `crypto`, `futures`, `options`, `forex` | `"crypto"`, `"futures"` |
| `timestamp` | long (micros) | Yes | Exchange-reported execution time (UTC, microseconds) | `1705228800000000` (2024-01-14 12:00:00 UTC) |
| `price` | decimal(18,8) | Yes | Execution price | `42150.50000000` (BTC), `0.00012345` (altcoins) |
| `quantity` | decimal(18,8) | Yes | Trade quantity (contracts, tokens) | `0.05000000` (fractional BTC), `1000.00000000` (altcoins) |
| `currency` | string | Yes | Currency code (ISO 4217 or crypto ticker) | `"USD"`, `"USDT"`, `"BTC"`, `"ETH"` |
| `side` | enum | Yes | Trade side: `BUY`, `SELL`, `SELL_SHORT`, `UNKNOWN` | `"BUY"`, `"SELL"` |
| `trade_conditions` | array[string] | Yes | Exchange-specific condition codes (empty if none) | `["normal"]` (standard trade), `["liquidation"]` (forced sale), `[]` |

### Platform Fields

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `source_sequence` | long (nullable) | No | Exchange sequence number (null if unavailable) | `12345678`, `null` |
| `ingestion_timestamp` | long (micros) | Yes | Platform ingestion time (UTC, microseconds) | `1705228800500000` |
| `platform_sequence` | long (nullable) | No | Platform-assigned sequence (auto-increment per partition) | `9876543`, `null` |

### Vendor-Specific Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `vendor_data` | map[string, string] (nullable) | No | Exchange-specific fields as key-value pairs. Stored as JSON in Iceberg. |

**Kraken vendor_data Example**:
```json
{
  "trade_type": "market",
  "maker": "false",
  "post_order_id": "O623RT-ABCDE-FGHKL"
}
```

**Binance vendor_data Example**:
```json
{
  "is_buyer_maker": "true",
  "event_type": "aggTrade",
  "trade_order_id": "123456",
  "buyer_order_id": "789012"
}
```

### Field Constraints

- **Price/Quantity Precision**: Decimal (18,8) = 18 total digits, 8 after decimal point
  - Supports large crypto values: `42150.50` → `42150.50000000`
  - Supports crypto: `0.00012345` → `0.00012345`
  - Max value: `9999999999.99999999`
- **Timestamp Precision**: Microseconds (1μs = 0.000001 seconds)
  - Suitable for high-frequency data sources
  - Example: `1705228800123456` = 2024-01-14 12:00:00.123456 UTC
- **Trade Side**: Aggressor perspective (market taker)
  - `BUY` = Aggressor bought (lifted the offer)
  - `SELL` = Aggressor sold (hit the bid)
  - `SELL_SHORT` = Short sale
  - `UNKNOWN` = Side not determinable

### Example Records

**Binance Crypto Trade**:
```json
{
  "message_id": "661e9511-f39c-52e5-b827-557766551111",
  "trade_id": "BINANCE-789012",
  "symbol": "BTCUSDT",
  "exchange": "BINANCE",
  "asset_class": "crypto",
  "timestamp": 1705228805000000,
  "price": "42150.50000000",
  "quantity": "0.05000000",
  "currency": "USDT",
  "side": "SELL",
  "trade_conditions": [],
  "source_sequence": null,
  "ingestion_timestamp": 1705228805012345,
  "platform_sequence": 9876544,
  "vendor_data": {
    "is_buyer_maker": "true",
    "event_type": "aggTrade"
  }
}
```

**Binance Crypto Trade**:
```json
{
  "message_id": "661e9511-f39c-52e5-b827-557766551111",
  "trade_id": "BINANCE-789012",
  "symbol": "BTCUSDT",
  "exchange": "BINANCE",
  "asset_class": "crypto",
  "timestamp": 1705228805000000,
  "price": "42150.50000000",
  "quantity": "0.05000000",
  "currency": "USDT",
  "side": "SELL",
  "trade_conditions": [],
  "source_sequence": null,
  "ingestion_timestamp": 1705228805012345,
  "platform_sequence": 9876544,
  "vendor_data": {
    "is_buyer_maker": "true",
    "event_type": "aggTrade"
  }
}
```

---

## QuoteV2 Schema

**Purpose**: Best bid/ask quote snapshot
**Namespace**: `com.k2.marketdata.QuoteV2`
**Schema File**: `src/k2/schemas/quote_v2.avsc`
**Kafka Topic**: `market.{asset_class}.quotes.{exchange}` (e.g., `market.crypto.quotes.binance`)
**Iceberg Table**: `market_data.quotes_v2`

### Core Fields

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `message_id` | string | Yes | UUID v4 for deduplication | `"550e8400-e29b-41d4-a716-446655440001"` |
| `quote_id` | string | Yes | Exchange-specific quote ID. Format: `{EXCHANGE}-{id}` | `"BINANCE-456789"`, `"KRAKEN-123456"` |
| `symbol` | string | Yes | Trading symbol | `"BTCUSDT"`, `"ETHUSDT"` |
| `exchange` | string | Yes | Exchange code | `"BINANCE"`, `"KRAKEN"` |
| `asset_class` | enum | Yes | Asset class: `crypto`, `futures`, `options`, `forex` | `"crypto"`, `"futures"` |
| `timestamp` | long (micros) | Yes | Exchange-reported quote time (UTC, microseconds) | `1705228800000000` |
| `bid_price` | decimal(18,8) | Yes | Best bid price | `45.66000000` |
| `bid_quantity` | decimal(18,8) | Yes | Quantity at best bid | `5000.00000000` |
| `ask_price` | decimal(18,8) | Yes | Best ask price | `45.68000000` |
| `ask_quantity` | decimal(18,8) | Yes | Quantity at best ask | `3000.00000000` |
| `currency` | string | Yes | Currency code | `"USDT"`, `"USD"`, `"BTC"` |

### Platform Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_sequence` | long (nullable) | No | Exchange sequence number (null if unavailable) |
| `ingestion_timestamp` | long (micros) | Yes | Platform ingestion time (UTC, microseconds) |
| `platform_sequence` | long (nullable) | No | Platform-assigned sequence number |
| `vendor_data` | map[string, string] (nullable) | No | Exchange-specific fields |

### Derived Metrics

**Spread Calculations**:
- **Absolute Spread**: `ask_price - bid_price`
- **Percentage Spread**: `((ask_price - bid_price) / ((ask_price + bid_price) / 2)) * 100`
- **Mid Price**: `(bid_price + ask_price) / 2`

**Example**:
```
bid_price = 45.66, ask_price = 45.68
Absolute Spread = 0.02
Percentage Spread = 0.044%
Mid Price = 45.67
```

### Example Record

```json
{
  "message_id": "550e8400-e29b-41d4-a716-446655440001",
  "quote_id": "BINANCE-456789",
  "symbol": "BTCUSDT",
  "exchange": "BINANCE",
  "asset_class": "crypto",
  "timestamp": 1705228800000000,
  "bid_price": "42150.25000000",
  "bid_quantity": "1.25000000",
  "ask_price": "42150.75000000",
  "ask_quantity": "0.87500000",
  "currency": "USDT",
  "source_sequence": null,
  "ingestion_timestamp": 1705228800123456,
  "platform_sequence": 9876545,
  "vendor_data": null
}
```

---

## ReferenceDataV2 Schema

**Purpose**: Instrument/company reference data for enrichment
**Namespace**: `com.k2.marketdata.ReferenceDataV2`
**Schema File**: `src/k2/schemas/reference_data_v2.avsc`
**Kafka Topic**: `market.reference_data` (compacted topic, latest-value per key)
**Iceberg Table**: `market_data.reference_data_v2`

### Core Fields

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `message_id` | string | Yes | UUID v4 for deduplication | `"550e8400-e29b-41d4-a716-446655440002"` |
| `company_id` | string | Yes | Primary identifier (Kafka message key for compaction) | `"BTCUSDT"` (pair), `"ETHUSDT"` (ISIN) |
| `symbol` | string | Yes | Trading symbol | `"BTCUSDT"`, `"ETHUSDT"` |
| `exchange` | string | Yes | Exchange code | `"BINANCE"`, `"KRAKEN"` |
| `asset_class` | enum | Yes | Asset class | `"crypto"`, `"futures"` |
| `company_name` | string | Yes | Full company/instrument name | `"BHP BILLITON LTD"`, `"Bitcoin"` |
| `isin` | string (nullable) | No | ISIN (ISO 6166). Null for crypto/derivatives | `"AU000000BHP4"`, `null` |
| `currency` | string (nullable) | No | Primary trading currency | `"USDT"`, `"USD"`, `"BTC"`, `null` |
| `listing_date` | long (micros, nullable) | No | Listing/activation date (UTC) | `946684800000000`, `null` |
| `delisting_date` | long (micros, nullable) | No | Delisting/deactivation date (UTC) | `null` (active) |
| `is_active` | boolean | Yes | Currently active for trading | `true`, `false` |

### Platform Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ingestion_timestamp` | long (micros) | Yes | When this reference data was ingested |
| `vendor_data` | map[string, string] (nullable) | No | Exchange-specific reference fields |

### Vendor Data Examples

**Binance Crypto**:
```json
{
  "base_asset": "BTC",
  "quote_asset": "USDT",
  "status": "TRADING",
  "min_notional": "10.00000000",
  "tick_size": "0.01000000"
}
```

**Binance Crypto**:
```json
{
  "base_asset": "BTC",
  "quote_asset": "USDT",
  "status": "TRADING",
  "min_notional": "10.00000000",
  "tick_size": "0.01000000"
}
```

### Example Record

```json
{
  "message_id": "550e8400-e29b-41d4-a716-446655440002",
  "company_id": "BTCUSDT",
  "symbol": "BTCUSDT",
  "exchange": "BINANCE",
  "asset_class": "crypto",
  "company_name": "Bitcoin Tether",
  "isin": null,
  "currency": "USDT",
  "listing_date": 1502947200000000,
  "delisting_date": null,
  "is_active": true,
  "ingestion_timestamp": 1705228800000000,
  "vendor_data": {
    "base_asset": "BTC",
    "quote_asset": "USDT",
    "status": "TRADING"
  }
}
```

---

## Common Enums

### AssetClass

| Value | Description | Examples |
|-------|-------------|----------|
| `crypto` | Cryptocurrencies, digital assets | BTC, ETH, BNB |
| `futures` | Futures contracts | Bitcoin futures, Ether futures |
| `options` | Options contracts | Bitcoin options, crypto options |
| `forex` | Foreign exchange pairs | EUR/USD, GBP/JPY |

### TradeSide (TradeV2 only)

| Value | Description | When Used |
|-------|-------------|-----------|
| `BUY` | Aggressor bought (market order that lifted the offer) | Buyer initiated trade |
| `SELL` | Aggressor sold (market order that hit the bid) | Seller initiated trade |
| `SELL_SHORT` | Short sale | Short selling or leverage trading |
| `UNKNOWN` | Side not determinable | Missing/ambiguous side information |

---

## Data Types

### Decimal (18,8)

**Specification**: 18 total digits, 8 after decimal point
**Avro Encoding**: `bytes` with `logicalType: decimal`
**Range**: `-9,999,999,999.99999999` to `9,999,999,999.99999999`

**Usage**:
- **Prices**: Supports both large (BTC) and small (altcoins) values
- **Quantities**: Supports fractional crypto (0.05 BTC) and large token amounts (1,000,000)

**Examples**:
```
42150.50    → 42150.50000000 (BTC price)
0.00012345  → 0.00012345    (altcoin price)
1000        → 1000.00000000 (token quantity)
0.05        → 0.05000000    (fractional BTC)
```

**Iceberg Storage**: Stored as `DECIMAL(18,8)` in Parquet

### Timestamp (Microseconds)

**Specification**: Unix timestamp in microseconds since epoch (1970-01-01 00:00:00 UTC)
**Avro Encoding**: `long` with `logicalType: timestamp-micros`
**Range**: 1970-01-01 to 2262-04-11 (292 years from epoch)

**Precision**:
- **Microseconds** (1μs = 0.000001 seconds)
- Suitable for high-frequency data sources

**Examples**:
```
1705228800000000 = 2024-01-14 12:00:00.000000 UTC
1705228800123456 = 2024-01-14 12:00:00.123456 UTC
```

**Conversion**:
```python
# Python: microseconds → datetime
from datetime import datetime
ts_micros = 1705228800123456
dt = datetime.utcfromtimestamp(ts_micros / 1_000_000)
# Result: 2024-01-14 12:00:00.123456

# Python: datetime → microseconds
dt = datetime(2024, 1, 14, 12, 0, 0, 123456)
ts_micros = int(dt.timestamp() * 1_000_000)
# Result: 1705228800123456
```

### Map (vendor_data)

**Specification**: Key-value pairs where both keys and values are strings
**Avro Encoding**: `map<string>`
**Iceberg Storage**: Stored as JSON string in Parquet (e.g., `{"key": "value"}`)

**Usage**:
- Store exchange-specific fields that don't fit core schema
- Preserve original field names from data source
- Enable schema flexibility without breaking BACKWARD compatibility

**Best Practices**:
- Use lowercase keys with underscores: `is_buyer_maker`, not `IsBuyerMaker`
- Store all values as strings (convert booleans/numbers to strings)
- Keep JSON structure flat (no nested objects)
- Document vendor_data fields in exchange integration guides

---

## Schema Evolution

### Compatibility Mode

**BACKWARD** compatibility enforced via Confluent Schema Registry
- New fields must have default values
- Existing fields cannot be removed
- Field types cannot be changed (except nullable → required)

### V1 → V2 Migration

**Breaking Changes**:
- `volume` (int64) → `quantity` (decimal 18,8)
- `exchange_timestamp` (milliseconds) → `timestamp` (microseconds)
- Exchange-specific fields → `vendor_data` map

**Migration Path**:
1. Deploy V2 producers writing to new Kafka topics
2. Run dual consumers (V1 + V2) during transition
3. Migrate downstream consumers to V2 schemas
4. Decommission V1 topics after validation

**Timeline**: Completed 2026-01-13 (Phase 2 Prep)

### Future Evolution

**Planned V3 (TBD)**:
- Order book depth levels (L2/L3 data)
- Trade breakdown (maker/taker identification)
- Expanded asset classes (forex, commodities)

---

## Query Examples

### DuckDB Queries

**Count Trades by Exchange**:
```sql
SELECT exchange, asset_class, COUNT(*) as trade_count
FROM market_data.trades_v2
WHERE exchange_date = '2024-01-14'
GROUP BY exchange, asset_class
ORDER BY trade_count DESC;
```

**VWAP Calculation**:
```sql
SELECT
  symbol,
  SUM(CAST(price AS DOUBLE) * CAST(quantity AS DOUBLE)) / SUM(CAST(quantity AS DOUBLE)) as vwap,
  COUNT(*) as num_trades,
  SUM(CAST(quantity AS DOUBLE)) as total_volume
FROM market_data.trades_v2
WHERE symbol = 'BTCUSDT'
  AND exchange_date = '2024-01-14'
GROUP BY symbol;
```

**Spread Analysis**:
```sql
SELECT
  symbol,
  AVG(CAST(ask_price AS DOUBLE) - CAST(bid_price AS DOUBLE)) as avg_spread,
  AVG(((CAST(ask_price AS DOUBLE) - CAST(bid_price AS DOUBLE)) /
       ((CAST(ask_price AS DOUBLE) + CAST(bid_price AS DOUBLE)) / 2)) * 100) as avg_spread_pct
FROM market_data.quotes_v2
WHERE exchange_date = '2024-01-14'
GROUP BY symbol
ORDER BY avg_spread_pct DESC
LIMIT 10;
```

**Extract vendor_data**:
```sql
SELECT
  symbol,
  vendor_data->>'company_id' as company_id,
  vendor_data->>'sector' as sector
FROM market_data.reference_data_v2
WHERE exchange = 'BINANCE' AND is_active = true;
```

---

## Iceberg Table Schema

### Partitioning

All V2 tables partitioned by:
- `exchange` (identity)
- `asset_class` (identity)
- `exchange_date` (day transform of timestamp)

**Benefits**:
- Partition pruning for date range queries
- Separate partitions per exchange/asset class
- Efficient scans for symbol-specific queries

### Sort Order

- **TradesV2**: `ORDER BY symbol, timestamp`
- **QuotesV2**: `ORDER BY symbol, timestamp`
- **ReferenceDataV2**: `ORDER BY symbol`

**Benefits**:
- Optimized range scans per symbol
- Improved compression (adjacent rows similar)
- Faster query execution with sorted data

---

## Related Documentation

### Schema Design
- [Schema Design V2](../architecture/schema-design-v2.md) - Design rationale and evolution
- [Platform Principles](../architecture/platform-principles.md#schema-design) - Schema design philosophy

### Implementation
- Phase 2 Prep: [V2 Schema Implementation](../phases/phase-2-prep/COMPLETION-REPORT.md)
- Phase 2 Prep: [Schema Evolution Decisions](../phases/phase-2-prep/DECISIONS.md)

### Operations
- [Schema Registry Operations](../operations/runbooks/schema-registry-operations.md)
- [Iceberg Table Maintenance](../operations/runbooks/iceberg-table-maintenance.md)

### Integration Guides
- [Binance Integration](../integrations/binance-crypto.md) - Binance-specific vendor_data fields
- [Binance Integration](../integrations/binance-crypto.md) - Binance-specific vendor_data fields

---

**Maintained By**: Data Engineering Team
**Schema Registry**: http://localhost:8081 (local), https://schema-registry.k2.prod (production)
**Last Schema Update**: 2026-01-13 (V2 schemas finalized)
**Next Review**: 2026-04-14 (quarterly)
