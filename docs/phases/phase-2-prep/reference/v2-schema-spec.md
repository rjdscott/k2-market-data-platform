# v2 Schema Specification

**Purpose**: Field-by-field documentation of v2 trade and quote schemas
**Last Updated**: 2026-01-12

---

## Overview

v2 schemas use an industry-standard hybrid approach:
- **Core fields**: Standard across all exchanges
- **vendor_data**: Exchange-specific extensions

---

## Trade Schema (v2)

### Schema Name

**Name**: `TradeV2`
**Namespace**: `com.k2.marketdata`
**Type**: Avro Record

---

### Core Standard Fields

#### message_id

**Type**: `string` (UUID)
**Required**: Yes
**Purpose**: Unique message identifier for deduplication

**Format**: UUID v4 (e.g., `550e8400-e29b-41d4-a716-446655440000`)

**Generation**:
```python
import uuid
message_id = str(uuid.uuid4())
```

---

#### trade_id

**Type**: `string`
**Required**: Yes
**Purpose**: Exchange trade identifier

**Format**: Exchange-specific
- ASX: `"ASX-{timestamp}"`
- Binance: `"BINANCE-{trade_id}"`

---

#### symbol

**Type**: `string`
**Required**: Yes
**Purpose**: Trading symbol

**Examples**:
- Equities: `"BHP"`, `"CBA"`, `"TSLA"`
- Crypto: `"BTCUSDT"`, `"ETHUSDT"`
- Futures: `"ESZ3"` (E-mini S&P 500 Dec 2023)

---

#### exchange

**Type**: `string`
**Required**: Yes
**Purpose**: Exchange code

**Values**: `"ASX"`, `"BINANCE"`, `"NYSE"`, `"CME"`, etc.

---

#### asset_class

**Type**: `enum AssetClass`
**Required**: Yes
**Purpose**: Asset class categorization

**Values**:
- `"equities"` - Stocks, shares
- `"crypto"` - Cryptocurrencies
- `"futures"` - Futures contracts
- `"options"` - Options contracts

---

#### timestamp

**Type**: `long` (logicalType: `timestamp-micros`)
**Required**: Yes
**Purpose**: Exchange timestamp in microseconds (UTC)

**Precision**: Microseconds (10^-6 seconds)

**Example**: `1672531199900000` (2023-01-01 00:00:00.000000 UTC)

**Conversion**:
```python
from datetime import datetime
timestamp_micros = int(datetime.utcnow().timestamp() * 1_000_000)
```

---

#### price

**Type**: `bytes` (logicalType: `decimal`, precision: 18, scale: 8)
**Required**: Yes
**Purpose**: Trade price

**Precision**: 18 digits total, 8 decimal places
**Range**: `-999999999.99999999` to `999999999.99999999`

**Examples**:
- Equities: `45.67000000` (BHP stock)
- Crypto: `16500.00000000` (BTC-USDT)
- Micro: `0.00012345` (low-value token)

**Usage**:
```python
from decimal import Decimal
price = Decimal("45.67")
```

---

#### quantity

**Type**: `bytes` (logicalType: `decimal`, precision: 18, scale: 8)
**Required**: Yes
**Purpose**: Trade quantity (shares, contracts, tokens)

**Precision**: 18 digits total, 8 decimal places

**Examples**:
- Equities: `1000.00000000` (shares)
- Crypto: `0.05000000` (BTC)
- Futures: `5.00000000` (contracts)

---

#### currency

**Type**: `string`
**Required**: Yes
**Purpose**: Currency code (ISO 4217 or crypto ticker)

**Examples**:
- Fiat: `"AUD"`, `"USD"`, `"EUR"`, `"JPY"`
- Crypto: `"USDT"`, `"USDC"`, `"BTC"`, `"ETH"`

---

#### side

**Type**: `enum TradeSide`
**Required**: Yes
**Purpose**: Trade side from aggressor perspective

**Values**:
- `"BUY"` - Aggressor bought (market taker bought)
- `"SELL"` - Aggressor sold (market taker sold)
- `"SELL_SHORT"` - Short sale
- `"UNKNOWN"` - Side not determinable

**Mapping Examples**:
- Binance `m=false` (seller is maker) → `"BUY"` (aggressor bought)
- Binance `m=true` (buyer is maker) → `"SELL"` (aggressor sold)
- ASX `side="buy"` → `"BUY"`
- ASX `side="sell"` → `"SELL"`

---

#### trade_conditions

**Type**: `array of string`
**Required**: Yes (can be empty)
**Purpose**: Array of trade condition codes

**Examples**:
- ASX: `["0"]` - Normal trade
- ASX: `["C"]` - Cash trade
- ASX: `["X"]` - Crossing

**Empty Array**: Use `[]` if no conditions

---

#### source_sequence

**Type**: `long` (nullable)
**Required**: No (default: null)
**Purpose**: Exchange sequence number (if available)

**Examples**:
- ASX: Sequence number from ASX feed
- Binance: null (not provided)

---

#### ingestion_timestamp

**Type**: `long` (logicalType: `timestamp-micros`)
**Required**: Yes
**Purpose**: Platform ingestion timestamp in microseconds (UTC)

**Usage**: Latency calculation
```python
latency = ingestion_timestamp - timestamp
```

---

#### platform_sequence

**Type**: `long` (nullable)
**Required**: No (default: null)
**Purpose**: Platform-assigned sequence number

**Generation**: Auto-increment per partition

---

#### vendor_data

**Type**: `map<string, string>` (nullable)
**Required**: No (default: null)
**Purpose**: Exchange-specific fields as key-value pairs

**Examples**:

**ASX**:
```json
{
  "company_id": "123",
  "qualifiers": "0",
  "venue": "X"
}
```

**Binance**:
```json
{
  "is_buyer_maker": "true",
  "event_type": "trade"
}
```

**Storage**: Stored as JSON string in Iceberg

---

## Quote Schema (v2)

### Schema Name

**Name**: `QuoteV2`
**Namespace**: `com.k2.marketdata`
**Type**: Avro Record

---

### Fields

Most fields match Trade schema. Quote-specific fields:

#### quote_id

**Type**: `string`
**Required**: Yes
**Purpose**: Exchange quote identifier

---

#### bid_price

**Type**: `bytes` (logicalType: `decimal`, precision: 18, scale: 8)
**Required**: Yes
**Purpose**: Bid price

---

#### bid_quantity

**Type**: `bytes` (logicalType: `decimal`, precision: 18, scale: 8)
**Required**: Yes
**Purpose**: Bid quantity

---

#### ask_price

**Type**: `bytes` (logicalType: `decimal`, precision: 18, scale: 8)
**Required**: Yes
**Purpose**: Ask price

---

#### ask_quantity

**Type**: `bytes` (logicalType: `decimal`, precision: 18, scale: 8)
**Required**: Yes
**Purpose**: Ask quantity

---

## Schema Versioning

### Version History

| Version | Date | Changes |
|---------|------|---------|
| v1 | 2024 | Initial ASX-specific schema |
| v2 | 2026-01 | Industry-standard hybrid schema |

### Compatibility

v2 is **NOT** backward compatible with v1:
- Field name changes (`volume` → `quantity`)
- New required fields (`side`, `currency`, `asset_class`)
- Type changes (`timestamp`: millis → micros)

---

## References

- [Avro Specification](https://avro.apache.org/docs/current/spec.html)
- [FIX Protocol](https://www.fixtrading.org/standards/)
- [ISO 4217 Currency Codes](https://www.iso.org/iso-4217-currency-codes.html)

---

**Last Updated**: 2026-01-12
