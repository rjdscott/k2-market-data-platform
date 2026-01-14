# Step 00.1: Design v2 Schemas

**Status**: ⬜ Not Started
**Estimated Time**: 4 hours
**Actual Time**: -
**Part**: Schema Evolution (Step 0)

---

## Dependencies

**Requires**:
- Understanding of current v1 schema limitations
- Knowledge of industry standards (FIX Protocol, Common Market Data Standard)
- Avro schema specification knowledge

**Blocks**:
- Step 00.2 (Update Producer for v2)
- All subsequent Step 0 substeps

---

## Goal

Create v2 Avro schemas for trade and quote that use industry-standard hybrid approach with core standard fields + vendor_data map for exchange-specific extensions.

---

## Background

**Current Problem**: v1 schemas are ASX vendor-specific and don't generalize to other data sources:
- `company_id`: ASX-specific integer ID (doesn't exist in Binance, FIX)
- `qualifiers`: ASX trade qualifier codes
- `venue`: ASX market venue codes
- `volume`: Ambiguous (shares? contracts? BTC?)
- Missing: `message_id` (deduplication key)
- Missing: `side` (BUY/SELL enum)
- Missing: `trade_id` (unique trade identifier)
- Missing: `currency` (AUD, USD, USDT)

**Solution**: v2 hybrid schema with core standard fields + vendor_data map.

---

## Tasks

### 1. Create trade_v2.avsc

Create `src/k2/schemas/trade_v2.avsc` with the following structure:

```json
{
  "type": "record",
  "name": "TradeV2",
  "namespace": "com.k2.marketdata",
  "doc": "v2 Trade schema - industry-standard hybrid with vendor extensions",
  "fields": [
    {
      "name": "message_id",
      "type": "string",
      "doc": "Unique message identifier (UUID) for deduplication"
    },
    {
      "name": "trade_id",
      "type": "string",
      "doc": "Exchange trade identifier"
    },
    {
      "name": "symbol",
      "type": "string",
      "doc": "Trading symbol (e.g., BHP, BTC-USDT)"
    },
    {
      "name": "exchange",
      "type": "string",
      "doc": "Exchange code (e.g., ASX, BINANCE)"
    },
    {
      "name": "asset_class",
      "type": {
        "type": "enum",
        "name": "AssetClass",
        "symbols": ["equities", "crypto", "futures", "options"]
      },
      "doc": "Asset class categorization"
    },
    {
      "name": "timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-micros"
      },
      "doc": "Exchange timestamp in microseconds (UTC)"
    },
    {
      "name": "price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 8
      },
      "doc": "Trade price with 8 decimal precision"
    },
    {
      "name": "quantity",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 8
      },
      "doc": "Trade quantity (shares, contracts, BTC, etc.)"
    },
    {
      "name": "currency",
      "type": "string",
      "doc": "Currency code (e.g., AUD, USD, USDT)"
    },
    {
      "name": "side",
      "type": {
        "type": "enum",
        "name": "TradeSide",
        "symbols": ["BUY", "SELL", "SELL_SHORT", "UNKNOWN"]
      },
      "doc": "Trade side from aggressor perspective"
    },
    {
      "name": "trade_conditions",
      "type": {
        "type": "array",
        "items": "string"
      },
      "doc": "Array of trade condition codes"
    },
    {
      "name": "source_sequence",
      "type": ["null", "long"],
      "default": null,
      "doc": "Exchange sequence number (if available)"
    },
    {
      "name": "ingestion_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-micros"
      },
      "doc": "Platform ingestion timestamp in microseconds (UTC)"
    },
    {
      "name": "platform_sequence",
      "type": ["null", "long"],
      "default": null,
      "doc": "Platform sequence number (if available)"
    },
    {
      "name": "vendor_data",
      "type": ["null", {
        "type": "map",
        "values": "string"
      }],
      "default": null,
      "doc": "Exchange-specific fields as key-value pairs"
    }
  ]
}
```

### 2. Create quote_v2.avsc

Create `src/k2/schemas/quote_v2.avsc` with similar structure:

```json
{
  "type": "record",
  "name": "QuoteV2",
  "namespace": "com.k2.marketdata",
  "doc": "v2 Quote schema - industry-standard hybrid with vendor extensions",
  "fields": [
    {
      "name": "message_id",
      "type": "string",
      "doc": "Unique message identifier (UUID) for deduplication"
    },
    {
      "name": "quote_id",
      "type": "string",
      "doc": "Exchange quote identifier"
    },
    {
      "name": "symbol",
      "type": "string",
      "doc": "Trading symbol"
    },
    {
      "name": "exchange",
      "type": "string",
      "doc": "Exchange code"
    },
    {
      "name": "asset_class",
      "type": {
        "type": "enum",
        "name": "AssetClass",
        "symbols": ["equities", "crypto", "futures", "options"]
      },
      "doc": "Asset class categorization"
    },
    {
      "name": "timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-micros"
      },
      "doc": "Quote timestamp in microseconds (UTC)"
    },
    {
      "name": "bid_price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 8
      },
      "doc": "Bid price"
    },
    {
      "name": "bid_quantity",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 8
      },
      "doc": "Bid quantity"
    },
    {
      "name": "ask_price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 8
      },
      "doc": "Ask price"
    },
    {
      "name": "ask_quantity",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 8
      },
      "doc": "Ask quantity"
    },
    {
      "name": "currency",
      "type": "string",
      "doc": "Currency code"
    },
    {
      "name": "ingestion_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-micros"
      },
      "doc": "Platform ingestion timestamp"
    },
    {
      "name": "vendor_data",
      "type": ["null", {
        "type": "map",
        "values": "string"
      }],
      "default": null,
      "doc": "Exchange-specific fields"
    }
  ]
}
```

### 3. Update src/k2/schemas/__init__.py

Add v2 schema loading helpers:

```python
def load_avro_schema_v2(schema_name: str) -> dict:
    """
    Load v2 Avro schema by name.

    Args:
        schema_name: Schema name without version (e.g., 'trade', 'quote')

    Returns:
        Parsed Avro schema dict
    """
    schema_file = SCHEMAS_DIR / f"{schema_name}_v2.avsc"

    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    with open(schema_file, 'r') as f:
        schema = json.load(f)

    return schema


def get_schema_version(schema_dict: dict) -> str:
    """
    Extract schema version from schema name.

    Args:
        schema_dict: Avro schema dictionary

    Returns:
        Version string ('v1' or 'v2')
    """
    name = schema_dict.get('name', '')
    if 'V2' in name or '_v2' in name:
        return 'v2'
    return 'v1'
```

### 4. Document Field Mappings

Create mapping documentation in schema files or separate doc:

**v1 → v2 Field Mappings**:
- `volume` → `quantity`
- (Add) → `message_id` (UUID)
- (Add) → `trade_id` (exchange ID or generated)
- (Add) → `asset_class` (equities, crypto, etc.)
- (Add) → `currency` (AUD, USD, USDT)
- (Add) → `side` (BUY, SELL, SELL_SHORT, UNKNOWN)
- `company_id`, `qualifiers`, `venue` → `vendor_data` map

---

## Deliverables

- [ ] `src/k2/schemas/trade_v2.avsc` created
- [ ] `src/k2/schemas/quote_v2.avsc` created
- [ ] `src/k2/schemas/__init__.py` updated with v2 loading helpers
- [ ] Field mappings documented

---

## Validation

### Manual Validation

```bash
# Install avro-tools if needed
pip install avro-python3

# Validate trade schema
python -c "
import json
with open('src/k2/schemas/trade_v2.avsc') as f:
    schema = json.load(f)
print('Trade schema valid:', schema['name'])
"

# Validate quote schema
python -c "
import json
with open('src/k2/schemas/quote_v2.avsc') as f:
    schema = json.load(f)
print('Quote schema valid:', schema['name'])
"

# Test loading with helper
python -c "
from k2.schemas import load_avro_schema_v2
trade = load_avro_schema_v2('trade')
quote = load_avro_schema_v2('quote')
print('✅ v2 schemas load successfully')
"
```

### Acceptance Criteria

- [ ] trade_v2.avsc validates as valid Avro schema
- [ ] quote_v2.avsc validates as valid Avro schema
- [ ] Python can load schemas without errors
- [ ] All required fields present with correct types
- [ ] TradeSide enum defined (BUY, SELL, SELL_SHORT, UNKNOWN)
- [ ] AssetClass enum defined (equities, crypto, futures, options)
- [ ] Decimal precision set to (18,8) for price/quantity
- [ ] timestamp uses logicalType: timestamp-micros
- [ ] vendor_data is optional map<string, string>

---

## Integration Points

- `src/k2/schemas/__init__.py` - Schema loading utilities
- `src/k2/ingestion/producer.py` - Will use v2 schemas (Step 00.2)
- `src/k2/ingestion/consumer.py` - Will deserialize v2 (Step 00.3)

---

## Notes

- Using `timestamp-micros` instead of `timestamp-millis` for better precision (crypto exchanges report nanosecond precision)
- Decimal (18,8) precision chosen to handle both equity prices (e.g., $45.67) and crypto prices (e.g., 0.00012345 BTC)
- vendor_data allows exchange-specific fields without schema changes

---

## Decisions Made

**Decision 2026-01-12**: Use timestamp-micros instead of timestamp-millis
- **Reason**: Crypto exchanges report nanosecond precision, micros is better
- **Cost**: Slightly larger serialization size
- **Alternative**: Keep millis (rejected - insufficient precision for HFT adjacent data)

---

## Commit Message

```
feat(schema): add v2 industry-standard schemas with vendor extensions

- Create trade_v2.avsc with hybrid approach (core + vendor_data)
- Create quote_v2.avsc with similar pattern
- Add v2 schema loading helpers to __init__.py
- Use timestamp-micros for precision
- Use Decimal (18,8) for price/quantity
- Add TradeSide and AssetClass enums
- Document field mappings from v1 to v2

Related: Phase 2 Prep, Decision #001
```

---

**Last Updated**: 2026-01-12
**Status**: ⬜ Not Started
