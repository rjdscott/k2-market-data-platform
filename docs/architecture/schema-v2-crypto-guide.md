# V2 Schema Guide - Crypto-Optimized Design

**Author**: Staff Engineering Team
**Last Updated**: 2026-01-18
**Status**: Production
**Version**: 2.0.0

---

## Executive Summary

The K2 platform uses **TradeV2** and **QuoteV2** Avro schemas as the unified data format for all crypto exchange integrations. This document explains the V2 schema design, crypto-specific optimizations, and field mappings for Binance and Kraken exchanges.

**Key Benefits**:
- Single schema for all crypto exchanges (Binance, Kraken, future exchanges)
- Decimal(18,8) precision handles fractional crypto quantities (0.00000001 BTC)
- Microsecond timestamps for high-frequency data
- `vendor_data` pattern preserves exchange-specific fields
- Industry-standard field names enable cross-exchange analytics

---

## Schema Overview

### TradeV2 Schema

15 fields optimized for crypto trade execution events:

| Field | Type | Description | Crypto Example |
|-------|------|-------------|----------------|
| `message_id` | string | UUID v4 for deduplication | `550e8400-e29b-41d4-a716-446655440000` |
| `trade_id` | string | Exchange trade identifier | `BINANCE-12345`, `KRAKEN-1705584123-BTC` |
| `symbol` | string | Trading pair | `BTCUSDT`, `ETHUSD` |
| `exchange` | string | Exchange code | `BINANCE`, `KRAKEN` |
| `asset_class` | enum | Asset category | `crypto` |
| `timestamp` | timestamp-micros | Trade execution time (µs) | `1705584123456789` |
| `price` | decimal(18,8) | Trade price | `45000.12345678` |
| `quantity` | decimal(18,8) | Trade quantity | `0.05000000` (fractional BTC) |
| `currency` | string | Quote currency | `USDT`, `USD`, `EUR` |
| `side` | enum | Aggressor side | `BUY`, `SELL` |
| `trade_conditions` | array<string> | Condition codes | `[]` (empty for crypto) |
| `source_sequence` | long? | Exchange sequence number | `null` (Binance), `123456` (if available) |
| `ingestion_timestamp` | timestamp-micros | Platform ingestion time | `1705584123457890` |
| `platform_sequence` | long? | Platform sequence | `98765` |
| `vendor_data` | map<string,string>? | Exchange-specific fields | `{"is_buyer_maker": "true", "pair": "XBT/USD"}` |

### QuoteV2 Schema

14 fields optimized for best bid/ask events:

| Field | Type | Description | Crypto Example |
|-------|------|-------------|----------------|
| `message_id` | string | UUID v4 for deduplication | `550e8400-e29b-41d4-a716-446655440001` |
| `quote_id` | string | Exchange quote identifier | `BINANCE-1705584123456` |
| `symbol` | string | Trading pair | `BTCUSDT` |
| `exchange` | string | Exchange code | `BINANCE`, `KRAKEN` |
| `asset_class` | enum | Asset category | `crypto` |
| `timestamp` | timestamp-micros | Quote timestamp (µs) | `1705584123456789` |
| `bid_price` | decimal(18,8) | Best bid | `45000.10000000` |
| `bid_quantity` | decimal(18,8) | Bid size | `1.50000000` |
| `ask_price` | decimal(18,8) | Best ask | `45000.50000000` |
| `ask_quantity` | decimal(18,8) | Ask size | `2.00000000` |
| `currency` | string | Quote currency | `USDT`, `USD` |
| `source_sequence` | long? | Exchange sequence | `null` |
| `ingestion_timestamp` | timestamp-micros | Platform ingestion time | `1705584123457890` |
| `platform_sequence` | long? | Platform sequence | `98766` |
| `vendor_data` | map<string,string>? | Exchange-specific fields | `{"level": "1", "channel": "book"}` |

---

## Crypto-Specific Design Decisions

### Decision 2026-01-18: Decimal(18,8) Precision

**Rationale**: Crypto requires support for:
- High-value assets: BTC at $100,000+ (6 digits before decimal)
- Fractional quantities: 0.00000001 BTC (8 digits after decimal, aka "satoshi")
- Stablecoins: USDT with 6 decimal places
- Altcoins: Some tokens have 18 decimal places

**Cost**:
- Slightly larger storage vs. Decimal(12,4) (equities)
- Minimal impact: ~2 bytes per field

**Alternative Considered**: Dynamic precision per symbol
- Rejected: Adds complexity, schema evolution challenges

### Decision 2026-01-18: Microsecond Timestamps

**Rationale**:
- Crypto exchanges provide millisecond (Binance) or sub-millisecond (some exchanges) precision
- Latency measurement requires high precision: `ingestion_timestamp - timestamp`
- Microseconds (µs) are industry standard for HFT platforms
- Nanosecond precision unnecessary (network latency >> 1µs)

**Cost**: 8 bytes per timestamp (same as milliseconds in `long` type)

### Decision 2026-01-18: vendor_data Pattern

**Rationale**:
- Preserve exchange-specific fields without schema changes
- Examples:
  - Binance: `is_buyer_maker`, `event_type`, `first_trade_id`, `last_trade_id`
  - Kraken: `pair` (original XBT/USD notation), `order_type`, `misc`
- Enables exchange-specific analytics and debugging
- No schema evolution required when adding new exchanges

**Cost**:
- Additional map storage (~50-200 bytes per record depending on fields)
- Query complexity for nested fields (require JSON parsing)

**Alternative Considered**: Separate schemas per exchange
- Rejected: Would need 2N schemas (trades + quotes × N exchanges)

---

## Exchange-Specific Field Mappings

### Binance → V2 Trade

**Source**: Binance WebSocket `aggTrade` stream

```json
{
  "e": "aggTrade",       // Event type
  "E": 1672531200123,    // Event time (ms)
  "s": "BTCUSDT",        // Symbol
  "a": 12345,            // Aggregate trade ID
  "p": "16597.50",       // Price
  "q": "0.05",           // Quantity
  "f": 100,              // First trade ID
  "l": 105,              // Last trade ID
  "T": 1672531200120,    // Trade time (ms)
  "m": true,             // Is buyer maker
  "M": true              // Ignore (always true)
}
```

**V2 Mapping**:

| V2 Field | Binance Field | Transformation | Example |
|----------|---------------|----------------|---------|
| `message_id` | Generated | UUID v4 | `550e8400-e29b-41d4-a716-446655440000` |
| `trade_id` | `a` | `BINANCE-{a}` | `BINANCE-12345` |
| `symbol` | `s` | Direct | `BTCUSDT` |
| `exchange` | Constant | `BINANCE` | `BINANCE` |
| `asset_class` | Constant | `crypto` | `crypto` |
| `timestamp` | `T` | `T * 1000` (ms → µs) | `1672531200120000` |
| `price` | `p` | `Decimal(p)` | `16597.50000000` |
| `quantity` | `q` | `Decimal(q)` | `0.05000000` |
| `currency` | `s` | Extract quote asset | `USDT` |
| `side` | `m` | `SELL` if `m=true`, else `BUY` | `SELL` |
| `trade_conditions` | N/A | `[]` | `[]` |
| `source_sequence` | N/A | `null` | `null` |
| `ingestion_timestamp` | Generated | Current time (µs) | `1672531200123456` |
| `platform_sequence` | N/A | `null` | `null` |
| `vendor_data` | Multiple | See below | `{"is_buyer_maker": "true", ...}` |

**vendor_data Fields**:
```python
{
    "is_buyer_maker": str(msg["m"]),          # "true" or "false"
    "event_type": msg["e"],                    # "aggTrade"
    "event_time_ms": str(msg["E"]),           # Original event time
    "first_trade_id": str(msg.get("f")),      # First underlying trade ID
    "last_trade_id": str(msg.get("l")),       # Last underlying trade ID
    "base_asset": base_asset,                  # "BTC"
    "quote_asset": quote_asset                 # "USDT"
}
```

### Kraken → V2 Trade

**Source**: Kraken WebSocket `trade` channel

```json
[
  123,                   // Channel ID
  [
    [
      "45000.10",        // price
      "0.12345678",      // volume
      "1672531200.1234", // timestamp (seconds.fractional)
      "b",               // side ("b" = buy, "s" = sell)
      "l",               // order type ("l" = limit, "m" = market)
      ""                 // misc
    ]
  ],
  "trade",
  "XBT/USD"              // Pair (Kraken uses XBT for Bitcoin)
]
```

**V2 Mapping**:

| V2 Field | Kraken Field | Transformation | Example |
|----------|--------------|----------------|---------|
| `message_id` | Generated | UUID v4 | `550e8400-e29b-41d4-a716-446655440001` |
| `trade_id` | Derived | `KRAKEN-{timestamp_int}-{symbol}` | `KRAKEN-1672531200-BTC` |
| `symbol` | Pair | Normalize XBT → BTC | `BTCUSD` |
| `exchange` | Constant | `KRAKEN` | `KRAKEN` |
| `asset_class` | Constant | `crypto` | `crypto` |
| `timestamp` | Timestamp | `float(ts) * 1_000_000` (s → µs) | `1672531200123400` |
| `price` | Price | `Decimal(price)` | `45000.10000000` |
| `quantity` | Volume | `Decimal(volume)` | `0.12345678` |
| `currency` | Pair | Extract quote asset | `USD` |
| `side` | Side | `BUY` if `b`, `SELL` if `s` | `BUY` |
| `trade_conditions` | N/A | `[]` | `[]` |
| `source_sequence` | N/A | `null` | `null` |
| `ingestion_timestamp` | Generated | Current time (µs) | `1672531200124567` |
| `platform_sequence` | N/A | `null` | `null` |
| `vendor_data` | Multiple | See below | `{"pair": "XBT/USD", ...}` |

**vendor_data Fields**:
```python
{
    "pair": pair,                          # Original "XBT/USD"
    "order_type": order_type,              # "l" or "m"
    "misc": misc,                          # Misc data (usually empty)
    "base_asset": base_asset,              # "XBT" (original)
    "quote_asset": quote_asset,            # "USD"
    "normalized_base": normalized_base     # "BTC"
}
```

**Key Difference**: Kraken uses "XBT" (ISO 4217 code) for Bitcoin, normalized to "BTC" in `symbol` field, but preserved in `vendor_data["pair"]` for traceability.

---

## Usage Examples

### Producing V2 Trades (Binance)

```python
from k2.ingestion.producer import MarketDataProducer
from k2.ingestion.binance_client import convert_binance_trade_to_v2

producer = MarketDataProducer(schema_version="v2")

# WebSocket message from Binance
binance_msg = {
    "e": "aggTrade",
    "E": 1705584123456,
    "s": "BTCUSDT",
    "a": 12345,
    "p": "45000.50",
    "q": "0.05",
    "T": 1705584123450,
    "m": False
}

# Convert to V2
v2_trade = convert_binance_trade_to_v2(binance_msg)

# Produce to Kafka
producer.produce_trade(
    asset_class="crypto",
    exchange="binance",
    record=v2_trade
)

producer.flush()
```

### Querying V2 Data from Iceberg

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog("k2_catalog")
table = catalog.load_table("market_data.trades_v2")

# Query BTC trades from last hour
df = table.scan(
    row_filter="symbol = 'BTCUSDT' AND timestamp > 1705580000000000"
).to_pandas()

# Extract vendor data (stored as JSON string in Iceberg)
import json
df["binance_data"] = df["vendor_data"].apply(json.loads)
df["is_buyer_maker"] = df["binance_data"].apply(lambda x: x.get("is_buyer_maker"))
```

---

## Performance Characteristics

### Storage Efficiency

**Per Trade Record** (V2 schema):
- Core fields: ~120 bytes
- vendor_data (avg): ~80 bytes
- **Total**: ~200 bytes/trade

**Compression** (Parquet with Zstd):
- Compression ratio: ~5:1
- Compressed size: ~40 bytes/trade
- 1M trades ≈ 40 MB compressed

### Query Performance

**Iceberg Predicate Pushdown**:
- `symbol` filter: Partition pruning (if partitioned by symbol)
- `timestamp` filter: Time-based partition pruning
- `exchange` filter: Full scan (not typically partitioned)

**Vendor Data Queries**:
- Requires JSON deserialization (slower than native fields)
- Use Spark SQL `get_json_object()` for efficient extraction
- Consider materializing frequently-queried vendor fields

---

## Schema Evolution

### Adding New Exchanges

To add a new exchange (e.g., Coinbase):

1. **No schema changes required** - V2 schema is exchange-agnostic
2. Create conversion function: `convert_coinbase_trade_to_v2()`
3. Map exchange fields to V2 standard fields
4. Store exchange-specific fields in `vendor_data`
5. Register new Kafka topic: `market.crypto.trades.coinbase`

### Adding Fields to V2

**Backward Compatible** (recommended):
- Add optional fields with defaults
- Example: `maker_order_id`, `taker_order_id`
- Update schema version in Schema Registry

**Breaking Changes** (avoid):
- Removing fields
- Changing field types
- Requires V3 schema (not recommended)

---

## Validation Rules

### Trade Validation

```python
def validate_v2_trade(trade: dict) -> tuple[bool, list[str]]:
    """Validate V2 trade record."""
    errors = []

    # Required fields
    if not trade.get("message_id"):
        errors.append("missing_message_id")
    if not trade.get("symbol"):
        errors.append("missing_symbol")

    # Business rules
    if trade.get("price") and trade["price"] <= 0:
        errors.append("negative_price")
    if trade.get("quantity") and trade["quantity"] <= 0:
        errors.append("negative_quantity")
    if trade.get("timestamp") and trade["timestamp"] <= 0:
        errors.append("invalid_timestamp")

    # Asset class must be crypto
    if trade.get("asset_class") != "crypto":
        errors.append("invalid_asset_class")

    # Decimal precision check (18,8)
    # Handled by Avro schema at serialization time

    return len(errors) == 0, errors
```

---

## Best Practices

### DO
✅ Use `vendor_data` for exchange-specific fields
✅ Normalize symbols (XBT → BTC) in `symbol` field
✅ Preserve original symbols in `vendor_data["pair"]`
✅ Use Decimal(18,8) for all price/quantity fields
✅ Convert all timestamps to microseconds
✅ Generate UUID v4 for `message_id` (deduplication)
✅ Set `asset_class` to `crypto` for all crypto trades

### DON'T
❌ Store non-string values in `vendor_data` (use JSON serialization)
❌ Leave `message_id` empty (breaks deduplication)
❌ Use millisecond timestamps (use microseconds)
❌ Mix asset classes in same table
❌ Query vendor_data frequently without indexing
❌ Modify V2 schema for exchange-specific needs (use vendor_data)

---

## Related Documentation

- [Schema Design V2](./schema-design-v2.md) - Full schema specification
- [Binance Integration](../reference/binance-integration.md) - Binance-specific details
- [Kraken Integration](../reference/kraken-integration.md) - Kraken-specific details
- [Data Dictionary V2](../reference/data-dictionary-v2.md) - Complete field reference
- [Medallion Architecture](./medallion-architecture.md) - Bronze/Silver/Gold layers

---

**Questions?** Contact: Platform Team (#k2-platform on Slack)
