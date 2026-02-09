# Redpanda Topic Architecture - Raw & Normalized Pattern

**Phase**: Phase 3 - ClickHouse Foundation
**Date**: 2026-02-09
**Status**: Approved
**Pattern**: Raw → Normalize → Ingest

---

## Architecture Overview

```
┌─────────────────┐
│ Exchange APIs   │
│ (WebSocket)     │
└────────┬────────┘
         │ Raw JSON (as-is from exchange)
         ↓
┌─────────────────────────────────────────┐
│ Redpanda: RAW Topics (immutable)        │
│ - market.crypto.trades.binance.raw      │
│ - market.crypto.trades.kraken.raw       │
│ - market.crypto.trades.coinbase.raw     │
│                                         │
│ Retention: 7 days                       │
│ Format: JSON (exchange-specific)        │
│ Purpose: Audit trail, reprocessing      │
└────────┬────────────────────────────────┘
         │
         ↓
┌─────────────────────────────────────────┐
│ Normalizer Service (Phase 6)            │
│ - Parses exchange-specific JSON         │
│ - Converts to canonical schema          │
│ - Validates and enriches                │
│ - Human-readable format                 │
└────────┬────────────────────────────────┘
         │ Normalized JSON (canonical)
         ↓
┌─────────────────────────────────────────┐
│ Redpanda: NORMALIZED Topics             │
│ - market.crypto.trades.binance          │
│ - market.crypto.trades.kraken           │
│ - market.crypto.trades.coinbase         │
│                                         │
│ Retention: 48 hours                     │
│ Format: Canonical JSON schema           │
│ Purpose: ClickHouse ingestion           │
└────────┬────────────────────────────────┘
         │
         ↓
┌─────────────────────────────────────────┐
│ ClickHouse: Kafka Engine                │
│ - Ingests from normalized topics        │
│ - Bronze → Silver → Gold layers         │
└─────────────────────────────────────────┘
```

---

## Topic Naming Convention

### Pattern
```
market.{asset_class}.{data_type}.{source}.{stage}

Examples:
- market.crypto.trades.binance.raw
- market.crypto.trades.binance
- market.crypto.orderbook.binance.raw
- market.crypto.orderbook.binance
```

### Stages
- **raw**: Immutable exchange data (no transformation)
- **(no suffix)**: Normalized canonical format

### Asset Classes
- `crypto`: Cryptocurrency markets
- `equity`: Stock markets (future)
- `forex`: Foreign exchange (future)

### Data Types
- `trades`: Executed trades
- `orderbook`: Order book snapshots (future)
- `quotes`: Best bid/ask (future)
- `bars`: OHLCV bars (future)

---

## Topic Configurations

### Raw Topics (Phase 1 - Now)
```bash
# market.crypto.trades.binance.raw
Partitions: 3
Replication: 1 (dev mode)
Retention: 7 days
Compression: LZ4
Format: JSON (exchange-specific, as-is)
```

### Normalized Topics (Phase 1 - Now)
```bash
# market.crypto.trades.binance
Partitions: 3
Replication: 1 (dev mode)
Retention: 48 hours
Compression: LZ4
Format: JSON (canonical schema)
```

---

## Canonical Trade Schema (Normalized)

**Topic**: `market.crypto.trades.{exchange}`

**Schema Version**: v1.0.0

```json
{
  "schema_version": "1.0.0",
  "exchange": "binance",
  "symbol": "BTCUSDT",
  "canonical_symbol": "BTC/USDT",

  "trade_id": "12345678",
  "price": "42150.50",
  "quantity": "0.015",
  "quote_volume": "632.2575",
  "side": "buy",

  "timestamp": "2026-02-09T08:45:23.123Z",
  "exchange_timestamp": "2026-02-09T08:45:23.120Z",

  "metadata": {
    "is_buyer_maker": false,
    "sequence_number": 987654321,
    "raw_symbol": "BTCUSDT"
  }
}
```

**Field Definitions**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | String | Yes | Schema version (semantic versioning) |
| `exchange` | String | Yes | Exchange identifier (lowercase) |
| `symbol` | String | Yes | Exchange-specific symbol |
| `canonical_symbol` | String | Yes | Normalized symbol (BTC/USDT format) |
| `trade_id` | String | Yes | Exchange trade ID (unique per exchange) |
| `price` | String | Yes | Price as string (preserves precision) |
| `quantity` | String | Yes | Quantity/size as string (preserves precision) |
| `quote_volume` | String | Yes | price × quantity (computed) |
| `side` | Enum | Yes | "buy" or "sell" (taker side) |
| `timestamp` | ISO8601 | Yes | Platform timestamp (when normalized) |
| `exchange_timestamp` | ISO8601 | Yes | Exchange original timestamp |
| `metadata` | Object | No | Exchange-specific metadata |

**Validation Rules**:
- `price` > 0
- `quantity` > 0
- `quote_volume` = price × quantity
- `timestamp` >= `exchange_timestamp` (normalization always after exchange time)
- `canonical_symbol` matches `{BASE}/{QUOTE}` pattern

---

## Raw Trade Format (Exchange-Specific)

### Binance Raw Format
**Topic**: `market.crypto.trades.binance.raw`

```json
{
  "e": "trade",
  "E": 1707472723123,
  "s": "BTCUSDT",
  "t": 12345678,
  "p": "42150.50",
  "q": "0.015",
  "b": 88888888,
  "a": 99999999,
  "T": 1707472723120,
  "m": false,
  "M": true
}
```

**Binance Field Mapping**:
- `e`: Event type (always "trade")
- `E`: Event time (milliseconds)
- `s`: Symbol
- `t`: Trade ID
- `p`: Price
- `q`: Quantity
- `b`: Buyer order ID
- `a`: Seller order ID
- `T`: Trade time (milliseconds)
- `m`: Is buyer maker
- `M`: Ignore (always true)

### Kraken Raw Format
**Topic**: `market.crypto.trades.kraken.raw`

```json
[
  0,
  [
    [
      "42150.50",
      "0.015",
      "1707472723.120456",
      "b",
      "m",
      ""
    ]
  ],
  "trade",
  "XBT/USDT"
]
```

**Kraken Field Mapping**:
- Array position 0: Channel ID
- Array position 1: Trade array
  - [0]: Price
  - [1]: Volume
  - [2]: Time (Unix timestamp with microseconds)
  - [3]: Side (b=buy, s=sell)
  - [4]: Order type (m=market, l=limit)
  - [5]: Misc
- Array position 2: Channel name
- Array position 3: Pair name

---

## Topic Creation Commands

```bash
# Create RAW topics (immutable audit trail)
docker exec k2-redpanda rpk topic create market.crypto.trades.binance.raw \
  --partitions 3 \
  --replicas 1 \
  --config retention.ms=604800000 \
  --config compression.type=lz4

docker exec k2-redpanda rpk topic create market.crypto.trades.kraken.raw \
  --partitions 3 \
  --replicas 1 \
  --config retention.ms=604800000 \
  --config compression.type=lz4

# Create NORMALIZED topics (ClickHouse ingestion)
docker exec k2-redpanda rpk topic create market.crypto.trades.binance \
  --partitions 3 \
  --replicas 1 \
  --config retention.ms=172800000 \
  --config compression.type=lz4

docker exec k2-redpanda rpk topic create market.crypto.trades.kraken \
  --partitions 3 \
  --replicas 1 \
  --config retention.ms=172800000 \
  --config compression.type=lz4
```

**Retention Periods**:
- Raw: 604800000ms = 7 days
- Normalized: 172800000ms = 48 hours

---

## ClickHouse Kafka Engine Configuration

**Ingest from NORMALIZED topics only** (not raw):

```sql
CREATE TABLE k2.trades_normalized_queue (
    message String
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'market.crypto.trades.binance,market.crypto.trades.kraken',
    kafka_group_name = 'clickhouse_normalized_consumer',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 2,
    kafka_max_block_size = 10000;
```

**Why normalized topics?**
- ClickHouse receives clean, validated, human-readable data
- No exchange-specific parsing logic in ClickHouse
- Easier schema evolution (change normalizer, not ClickHouse)
- Better separation of concerns

---

## Data Flow Summary

### Phase 3 (Current)
```
Manual JSON files → Normalized topics → ClickHouse
                    (for testing)
```

### Phase 6 (Future - Kotlin Normalizer)
```
Exchange APIs → Raw topics → Normalizer → Normalized topics → ClickHouse
                (immutable)   (Kotlin)      (canonical)
```

### Benefits of This Pattern

**Raw Topics**:
- ✅ Immutable audit trail
- ✅ Reprocessing capability (change normalizer, replay raw data)
- ✅ Debugging (see exactly what exchange sent)
- ✅ Exchange-specific metadata preserved

**Normalized Topics**:
- ✅ Human-readable canonical format
- ✅ ClickHouse ingests clean data
- ✅ Schema evolution without ClickHouse changes
- ✅ Validation happens before ingestion (fail fast)

**Separation of Concerns**:
- ✅ Feed handlers: WebSocket → Raw topics (simple)
- ✅ Normalizer: Raw → Normalized (single responsibility)
- ✅ ClickHouse: Normalized → Analytics (no parsing logic)

---

## Testing Strategy (Phase 3)

Since we don't have feed handlers yet, we'll test with mock data:

### Step 1: Create Topics
Create all 4 topics (2 raw, 2 normalized)

### Step 2: Produce Mock Normalized Data
Use `rpk` to produce sample normalized trades:

```bash
echo '{
  "schema_version": "1.0.0",
  "exchange": "binance",
  "symbol": "BTCUSDT",
  "canonical_symbol": "BTC/USDT",
  "trade_id": "12345678",
  "price": "42150.50",
  "quantity": "0.015",
  "quote_volume": "632.2575",
  "side": "buy",
  "timestamp": "2026-02-09T08:45:23.123Z",
  "exchange_timestamp": "2026-02-09T08:45:23.120Z",
  "metadata": {"sequence_number": 987654321}
}' | docker exec -i k2-redpanda rpk topic produce market.crypto.trades.binance
```

### Step 3: Verify ClickHouse Ingestion
Query Bronze layer to confirm data arrived:

```sql
SELECT * FROM k2.bronze_trades LIMIT 10;
```

---

## Future: Schema Registry (Phase 4+)

Consider adding Avro schemas for type safety:

```json
{
  "type": "record",
  "name": "NormalizedTrade",
  "namespace": "com.k2.crypto.trades",
  "fields": [
    {"name": "schema_version", "type": "string"},
    {"name": "exchange", "type": "string"},
    {"name": "symbol", "type": "string"},
    {"name": "canonical_symbol", "type": "string"},
    {"name": "trade_id", "type": "string"},
    {"name": "price", "type": "string"},
    {"name": "quantity", "type": "string"},
    {"name": "quote_volume", "type": "string"},
    {"name": "side", "type": {"type": "enum", "name": "Side", "symbols": ["buy", "sell"]}},
    {"name": "timestamp", "type": "string"},
    {"name": "exchange_timestamp", "type": "string"}
  ]
}
```

**Benefits**: Type safety, schema evolution, backward/forward compatibility

---

**Next Steps**:
1. Create topics (raw + normalized)
2. Update ClickHouse DDL to ingest from normalized topics
3. Produce mock normalized data for testing
4. Validate end-to-end flow

---

**Last Updated**: 2026-02-09
**Owner**: Platform Engineering
**Status**: Ready for Implementation
