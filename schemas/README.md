# K2 Market Data Platform - Avro Schemas

**Purpose**: Avro schema definitions for type-safe data serialization
**Schema Registry**: Redpanda Schema Registry (http://localhost:8081)

---

## Schema Organization

```
schemas/avro/
├── normalized-trade.avsc         → Canonical normalized trade (all exchanges)
├── binance-raw-trade.avsc        → Binance WebSocket payload (raw)
└── kraken-raw-trade.avsc         → Kraken WebSocket payload (raw) [future]
```

---

## Schema Registry Topics

| Topic | Schema | Subject | Version |
|-------|--------|---------|---------|
| `market.crypto.trades.binance.raw` | BinanceRawTrade | market.crypto.trades.binance.raw-value | 1 |
| `market.crypto.trades.binance` | NormalizedTrade | market.crypto.trades.binance-value | 1 |
| `market.crypto.trades.kraken.raw` | KrakenRawTrade | market.crypto.trades.kraken.raw-value | 1 |
| `market.crypto.trades.kraken` | NormalizedTrade | market.crypto.trades.kraken-value | 1 |

---

## Normalized Trade Schema (v1.0.0)

**Namespace**: `com.k2.marketdata.crypto`
**Name**: `NormalizedTrade`

**Purpose**: Canonical trade format used across all exchanges

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | string | Yes | Schema version (default: "1.0.0") |
| `exchange` | string | Yes | Exchange identifier (lowercase) |
| `symbol` | string | Yes | Exchange-specific symbol |
| `canonical_symbol` | string | Yes | Normalized symbol (BTC/USDT) |
| `trade_id` | string | Yes | Exchange trade ID |
| `price` | string | Yes | Price (preserves precision) |
| `quantity` | string | Yes | Quantity (preserves precision) |
| `quote_volume` | string | Yes | price × quantity |
| `side` | enum(BUY, SELL) | Yes | Trade side (taker) |
| `timestamp` | long (timestamp-millis) | Yes | Platform timestamp |
| `exchange_timestamp` | long (timestamp-millis) | Yes | Exchange timestamp |
| `metadata` | TradeMetadata | No | Exchange-specific metadata |

**TradeMetadata** (nested):
- `sequence_number`: long (optional)
- `is_buyer_maker`: boolean (optional)
- `buyer_order_id`: long (optional)
- `seller_order_id`: long (optional)

---

## Binance Raw Trade Schema

**Namespace**: `com.k2.marketdata.crypto.binance`
**Name**: `BinanceRawTrade`

**Purpose**: Preserve exact Binance WebSocket payload

### Fields (Binance Specification)

| Field | Type | Description |
|-------|------|-------------|
| `e` | string | Event type ("trade") |
| `E` | long | Event time (ms) |
| `s` | string | Symbol (BTCUSDT) |
| `t` | long | Trade ID |
| `p` | string | Price |
| `q` | string | Quantity |
| `b` | long | Buyer order ID |
| `a` | long | Seller order ID |
| `T` | long | Trade time (ms) |
| `m` | boolean | Is buyer maker |
| `M` | boolean | Ignore (always true) |

---

## Schema Registration

### Register Normalized Trade Schema
```bash
curl -X POST http://localhost:8081/subjects/market.crypto.trades.binance-value/versions \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d @- <<EOF
{
  "schemaType": "AVRO",
  "schema": "$(cat schemas/avro/normalized-trade.avsc | jq -c . | jq -R .)"
}
EOF
```

### Register Binance Raw Schema
```bash
curl -X POST http://localhost:8081/subjects/market.crypto.trades.binance.raw-value/versions \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d @- <<EOF
{
  "schemaType": "AVRO",
  "schema": "$(cat schemas/avro/binance-raw-trade.avsc | jq -c . | jq -R .)"
}
EOF
```

### List Registered Schemas
```bash
curl -X GET http://localhost:8081/subjects
```

### Get Schema by Subject
```bash
curl -X GET http://localhost:8081/subjects/market.crypto.trades.binance-value/versions/latest
```

---

## Schema Evolution Guidelines

### Compatible Changes (FORWARD compatible)
- ✅ Add optional fields with defaults
- ✅ Delete fields
- ✅ Promote field from required to optional (with default)

### Incompatible Changes (require new version)
- ❌ Remove required fields
- ❌ Change field types
- ❌ Rename fields
- ❌ Add required fields without defaults

### Version Strategy
- **Normalized schema**: Shared across exchanges - changes require coordination
- **Raw schemas**: Exchange-specific - can evolve independently
- **Compatibility mode**: FORWARD (producers can use new schema, consumers must handle old)

---

## Testing Schemas

### Validate Schema Syntax
```bash
# Using avro-tools (install via: pip install avro-python3)
python -c "import avro.schema; avro.schema.parse(open('schemas/avro/normalized-trade.avsc').read()); print('✓ Valid')"
```

### Test Serialization (Kotlin)
```kotlin
val schema = Schema.Parser().parse(File("schemas/avro/normalized-trade.avsc"))
val record = GenericData.Record(schema).apply {
    put("exchange", "binance")
    put("symbol", "BTCUSDT")
    put("canonical_symbol", "BTC/USDT")
    // ... other fields
}
val writer = GenericDatumWriter<GenericRecord>(schema)
// ... serialize
```

---

## Kafka Producer Configuration (Avro)

```kotlin
val producerProps = Properties().apply {
    put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, "localhost:9092")
    put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer::class.java)
    put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, KafkaAvroSerializer::class.java)
    put("schema.registry.url", "http://localhost:8081")
}
```

---

## Schema Benefits

1. **Type Safety**: Compile-time checks, no runtime surprises
2. **Evolution**: Add fields without breaking consumers
3. **Documentation**: Self-documenting data contracts
4. **Compression**: Avro is compact (smaller than JSON)
5. **Performance**: Binary format, faster than JSON parsing
6. **Validation**: Schema Registry enforces compatibility

---

**Last Updated**: 2026-02-09
**Owner**: Platform Engineering
