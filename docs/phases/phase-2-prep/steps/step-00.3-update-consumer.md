# Step 00.3: Update Consumer for v2

**Status**: ⬜ Not Started
**Estimated Time**: 6 hours
**Actual Time**: -
**Part**: Schema Evolution (Step 0)

---

## Dependencies

**Requires**: Step 00.2 (Producer produces v2)
**Blocks**: Step 00.4, Step 00.5

---

## Goal

Update consumer to deserialize v2 messages from Kafka and write to new v2 Iceberg tables.

---

## Tasks

### 1. Update consumer.py for v2

Add schema version support:

```python
class MarketDataConsumer:
    def __init__(self, schema_version: str = "v2", **kwargs):
        self.schema_version = schema_version

        # Load v2 deserializer
        if schema_version == "v2":
            self.trade_deserializer = AvroDeserializer(
                schema_registry_client=self.schema_registry,
                schema_str=json.dumps(load_avro_schema_v2('trade'))
            )
```

### 2. Update storage/writer.py for v2

Map v2 fields to PyArrow schema:

```python
def _get_v2_pyarrow_schema() -> pa.Schema:
    """PyArrow schema for v2 Iceberg tables."""
    return pa.schema([
        pa.field("message_id", pa.string()),
        pa.field("trade_id", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("exchange", pa.string()),
        pa.field("asset_class", pa.string()),
        pa.field("timestamp", pa.timestamp('us')),
        pa.field("price", pa.decimal128(18, 8)),
        pa.field("quantity", pa.decimal128(18, 8)),
        pa.field("currency", pa.string()),
        pa.field("side", pa.string()),
        pa.field("trade_conditions", pa.list_(pa.string())),
        pa.field("source_sequence", pa.int64()),
        pa.field("ingestion_timestamp", pa.timestamp('us')),
        pa.field("platform_sequence", pa.int64()),
        pa.field("vendor_data", pa.string()),  # JSON string
    ])
```

### 3. Create v2 Iceberg Tables

```python
# Create market_data.trades_v2 table
catalog = get_catalog()

catalog.create_table(
    "market_data.trades_v2",
    schema=_get_v2_pyarrow_schema(),
    partition_spec=PartitionSpec(
        PartitionField(
            source_id=schema.find_field("timestamp").field_id,
            field_id=1000,
            transform=DayTransform(),
            name="exchange_date"
        ),
        PartitionField(
            source_id=schema.find_field("exchange").field_id,
            field_id=1001,
            transform=IdentityTransform(),
            name="exchange"
        )
    )
)
```

### 4. Handle vendor_data JSON Mapping

```python
def _map_vendor_data(vendor_data: Optional[Dict]) -> Optional[str]:
    """Convert vendor_data dict to JSON string."""
    if vendor_data:
        return json.dumps(vendor_data)
    return None
```

---

## Deliverables

- [ ] Updated `src/k2/ingestion/consumer.py` for v2
- [ ] Updated `src/k2/storage/writer.py` for v2
- [ ] New Iceberg table: `market_data.trades_v2`
- [ ] New Iceberg table: `market_data.quotes_v2`
- [ ] vendor_data mapping to JSON string

---

## Validation

```python
# Test v2 consumption and write
from k2.ingestion.consumer import MarketDataConsumer
from k2.query.engine import QueryEngine

consumer = MarketDataConsumer(schema_version="v2")
# ... consume messages

# Query v2 table
engine = QueryEngine(table_version="v2")
df = engine.query_trades(symbol="BHP", limit=10)

assert "quantity" in df.columns
assert "side" in df.columns
assert "vendor_data" in df.columns
print("✅ v2 consumer and writer working")
```

---

## Acceptance Criteria

- [ ] Consumer deserializes v2 messages
- [ ] v2 data writes to Iceberg
- [ ] v2 tables created (trades_v2, quotes_v2)
- [ ] vendor_data stored as JSON string
- [ ] Can query v2 tables

---

## Commit Message

```
feat(consumer): add v2 schema support and iceberg table

- Update consumer for v2 deserialization
- Update writer with v2 PyArrow schema mapping
- Create market_data.trades_v2 table
- Map vendor_data to JSON string in Iceberg
- Maintain v1 tables (read-only)

Related: Phase 2 Prep, Step 00.3
```

---

**Last Updated**: 2026-01-12
**Status**: ⬜ Not Started
