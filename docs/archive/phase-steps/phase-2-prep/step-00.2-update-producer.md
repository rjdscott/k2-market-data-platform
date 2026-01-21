# Step 00.2: Update Producer for v2

**Status**: ⬜ Not Started
**Estimated Time**: 6 hours
**Actual Time**: -
**Part**: Schema Evolution (Step 0)

---

## Dependencies

**Requires**: Step 00.1 (v2 schemas created)
**Blocks**: Step 00.3, Step 00.4

---

## Goal

Update MarketDataProducer to serialize and produce v2 messages to Kafka with backward compatibility support.

---

## Tasks

### 1. Update producer.py for v2 Schema Support

Add schema versioning to `src/k2/ingestion/producer.py`:

```python
class MarketDataProducer:
    def __init__(
        self,
        schema_version: str = "v2",  # Add parameter
        # ... other params
    ):
        self.schema_version = schema_version

        # Load appropriate schemas
        if schema_version == "v2":
            self.trade_schema = load_avro_schema_v2('trade')
            self.quote_schema = load_avro_schema_v2('quote')
        else:
            self.trade_schema = load_avro_schema('trade')
            self.quote_schema = load_avro_schema('quote')
```

### 2. Create v2 Message Builder

Add `build_trade_v2()` function:

```python
def build_trade_v2(
    symbol: str,
    exchange: str,
    asset_class: str,
    timestamp: datetime,
    price: Decimal,
    quantity: Decimal,
    currency: str = "USD",
    side: str = "UNKNOWN",
    trade_id: Optional[str] = None,
    message_id: Optional[str] = None,
    vendor_data: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build v2 trade message."""
    return {
        "message_id": message_id or str(uuid.uuid4()),
        "trade_id": trade_id or f"{exchange}-{int(time.time())}",
        "symbol": symbol,
        "exchange": exchange,
        "asset_class": asset_class,
        "timestamp": int(timestamp.timestamp() * 1_000_000),  # micros
        "price": price,
        "quantity": quantity,
        "currency": currency,
        "side": side,
        "trade_conditions": [],
        "source_sequence": None,
        "ingestion_timestamp": int(time.time() * 1_000_000),
        "platform_sequence": None,
        "vendor_data": vendor_data,
    }
```

### 3. Update produce_trade() Method

Update method to handle v2:

```python
def produce_trade(
    self,
    asset_class: str,
    exchange: str,
    record: Dict[str, Any],
) -> bool:
    # Convert to v2 if needed
    if self.schema_version == "v2" and not self._is_v2_format(record):
        record = self._convert_to_v2(record)

    # Produce to Kafka
    topic = self._get_topic_name(asset_class, exchange, "trades")
    return self._produce_message(topic, record, self.trade_schema)
```

### 4. Add Conversion Helper

```python
def _convert_to_v2(self, v1_record: Dict[str, Any]) -> Dict[str, Any]:
    """Convert v1 record to v2 format."""
    return build_trade_v2(
        symbol=v1_record["symbol"],
        exchange=v1_record.get("exchange", "unknown"),
        asset_class=v1_record.get("asset_class", "equities"),
        timestamp=datetime.fromisoformat(v1_record["timestamp"]),
        price=Decimal(str(v1_record["price"])),
        quantity=Decimal(str(v1_record.get("volume", 0))),  # volume → quantity
        currency=v1_record.get("currency", "USD"),
        side=v1_record.get("side", "UNKNOWN"),
        vendor_data=v1_record.get("vendor_data"),
    )
```

### 5. Update Topic Registration

Register v2 schemas with Schema Registry:

```python
def _register_schemas(self):
    """Register v2 schemas with Schema Registry."""
    if self.schema_version == "v2":
        self._register_schema("trades", self.trade_schema)
        self._register_schema("quotes", self.quote_schema)
```

---

## Deliverables

- [ ] Updated `src/k2/ingestion/producer.py` with v2 support
- [ ] `build_trade_v2()` message builder created
- [ ] `build_quote_v2()` message builder created
- [ ] Conversion helper `_convert_to_v2()` added
- [ ] v2 schemas registered with Schema Registry

---

## Validation

```python
# Test v2 message building
from k2.ingestion.producer import build_trade_v2
from decimal import Decimal
from datetime import datetime

msg = build_trade_v2(
    symbol="BHP",
    exchange="ASX",
    asset_class="equities",
    timestamp=datetime.utcnow(),
    price=Decimal("45.67"),
    quantity=Decimal("1000"),
    currency="AUD",
    side="BUY",
    vendor_data={"company_id": "123"}
)

assert "message_id" in msg
assert msg["asset_class"] == "equities"
print("✅ v2 message builder works")

# Test producing to Kafka
producer = MarketDataProducer(schema_version="v2")
result = producer.produce_trade("equities", "ASX", msg)
assert result is True
print("✅ v2 message produced to Kafka")
```

---

## Acceptance Criteria

- [ ] Producer can build v2 messages
- [ ] Producer can send v2 to Kafka
- [ ] Schema Registry shows v2 schemas
- [ ] v2 messages deserialize correctly
- [ ] Backward compatibility maintained (v1 still works with flag)

---

## Commit Message

```
feat(producer): add v2 schema support with backward compatibility

- Add schema_version parameter (default: "v2")
- Create build_trade_v2() and build_quote_v2() builders
- Add v1 → v2 conversion helper
- Register v2 schemas with Schema Registry
- Maintain backward compatibility with v1

Related: Phase 2 Prep, Step 00.2
```

---

**Last Updated**: 2026-01-12
**Status**: ⬜ Not Started
