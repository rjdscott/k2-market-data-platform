# Step 2: Schema Design & Registration

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 4-6 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 1 (Infrastructure must be running)
- **Blocks**: Step 6 (Kafka Producer), Step 8 (Kafka Consumer)

## Goal
Define Avro schemas for market data that match the CSV sample data structure. Register these schemas with Schema Registry to enable versioning and backward compatibility checks. This establishes the data contracts for the entire platform.

---

## Implementation

### 2.1 Design Avro Schemas

**Files**:
- `src/k2/schemas/trade.avsc` (Avro schema definition)
- `src/k2/schemas/quote.avsc`
- `src/k2/schemas/reference_data.avsc`

**Trade Schema** (`trade.avsc`):

```json
{
  "type": "record",
  "name": "Trade",
  "namespace": "com.k2.market_data",
  "doc": "Market trade execution event",
  "fields": [
    {
      "name": "symbol",
      "type": "string",
      "doc": "Trading symbol (e.g., BHP, RIO)"
    },
    {
      "name": "company_id",
      "type": "int",
      "doc": "Company identifier for enrichment"
    },
    {
      "name": "exchange",
      "type": "string",
      "default": "ASX",
      "doc": "Exchange identifier"
    },
    {
      "name": "exchange_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      },
      "doc": "Exchange timestamp (epoch milliseconds)"
    },
    {
      "name": "price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 6
      },
      "doc": "Trade execution price"
    },
    {
      "name": "volume",
      "type": "long",
      "doc": "Number of shares traded"
    },
    {
      "name": "qualifiers",
      "type": "int",
      "doc": "Trade qualifier code (0-3)"
    },
    {
      "name": "venue",
      "type": "string",
      "doc": "Market venue (e.g., X for primary ASX)"
    },
    {
      "name": "buyer_id",
      "type": ["null", "string"],
      "default": null,
      "doc": "Buyer identifier (optional)"
    },
    {
      "name": "ingestion_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      },
      "doc": "Platform ingestion timestamp for latency tracking"
    },
    {
      "name": "sequence_number",
      "type": ["null", "long"],
      "default": null,
      "doc": "Platform-assigned sequence number for ordering"
    }
  ]
}
```

**Quote Schema** (`quote.avsc`):

```json
{
  "type": "record",
  "name": "Quote",
  "namespace": "com.k2.market_data",
  "doc": "Best bid/ask quote event",
  "fields": [
    {
      "name": "symbol",
      "type": "string",
      "doc": "Trading symbol"
    },
    {
      "name": "company_id",
      "type": "int",
      "doc": "Company identifier"
    },
    {
      "name": "exchange",
      "type": "string",
      "default": "ASX"
    },
    {
      "name": "exchange_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      }
    },
    {
      "name": "bid_price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 6
      }
    },
    {
      "name": "bid_volume",
      "type": "long"
    },
    {
      "name": "ask_price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 6
      }
    },
    {
      "name": "ask_volume",
      "type": "long"
    },
    {
      "name": "ingestion_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      }
    },
    {
      "name": "sequence_number",
      "type": ["null", "long"],
      "default": null
    }
  ]
}
```

**Reference Data Schema** (`reference_data.avsc`):

```json
{
  "type": "record",
  "name": "ReferenceData",
  "namespace": "com.k2.market_data",
  "doc": "Company reference data for enrichment",
  "fields": [
    {
      "name": "company_id",
      "type": "int",
      "doc": "Primary key"
    },
    {
      "name": "symbol",
      "type": "string"
    },
    {
      "name": "company_name",
      "type": "string"
    },
    {
      "name": "isin",
      "type": "string",
      "doc": "International Securities Identification Number"
    },
    {
      "name": "start_date",
      "type": "string",
      "doc": "Listing start date (MM/DD/YYYY)"
    },
    {
      "name": "end_date",
      "type": ["null", "string"],
      "default": null,
      "doc": "Delisting date if applicable"
    }
  ]
}
```

### 2.2 Create Schema Management Module

**File**: `src/k2/schemas/__init__.py`

```python
"""Schema management for K2 platform."""
import json
from pathlib import Path
from typing import Dict
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema
import structlog

logger = structlog.get_logger()

SCHEMA_DIR = Path(__file__).parent

def load_avro_schema(schema_name: str) -> str:
    """Load Avro schema from .avsc file."""
    schema_path = SCHEMA_DIR / f"{schema_name}.avsc"
    return schema_path.read_text()

def register_schemas(schema_registry_url: str) -> Dict[str, int]:
    """Register all Avro schemas with Schema Registry."""
    client = SchemaRegistryClient({'url': schema_registry_url})

    schema_ids = {}
    for schema_name in ['trade', 'quote', 'reference_data']:
        schema_str = load_avro_schema(schema_name)
        schema = Schema(schema_str, schema_type='AVRO')

        subject = f"market.{schema_name}s.raw-value"
        schema_id = client.register_schema(subject, schema)
        schema_ids[schema_name] = schema_id

        logger.info(
            "Schema registered",
            subject=subject,
            schema_id=schema_id,
        )

    return schema_ids
```

### 2.3 Test Schema Registration

**File**: `tests/unit/test_schemas.py`

```python
"""Unit tests-backup for schema validation."""
import pytest
from pathlib import Path
import json
import avro.schema

@pytest.mark.unit
class TestSchemas:
    """Validate Avro schemas are well-formed."""

    def test_trade_schema_valid(self):
        """Trade schema should parse without errors."""
        schema_path = Path('src/k2/schemas/trade.avsc')
        schema_dict = json.loads(schema_path.read_text())
        schema = avro.schema.parse(json.dumps(schema_dict))

        assert schema.name == 'Trade'
        assert schema.namespace == 'com.k2.market_data'
        assert 'symbol' in [f.name for f in schema.fields]

    def test_quote_schema_valid(self):
        """Quote schema should parse without errors."""
        schema_path = Path('src/k2/schemas/quote.avsc')
        schema_dict = json.loads(schema_path.read_text())
        schema = avro.schema.parse(json.dumps(schema_dict))

        assert schema.name == 'Quote'

    def test_all_schemas_have_required_fields(self):
        """All market data schemas need key ordering fields."""
        for schema_name in ['trade', 'quote']:
            schema_path = Path(f'src/k2/schemas/{schema_name}.avsc')
            schema_dict = json.loads(schema_path.read_text())
            field_names = [f['name'] for f in schema_dict['fields']]

            # Required for ordering and partitioning
            assert 'symbol' in field_names
            assert 'exchange_timestamp' in field_names
            assert 'ingestion_timestamp' in field_names
```

**Integration Test**: `tests/integration/test_schema_registry.py`

```python
"""Integration tests-backup for Schema Registry."""
import pytest
from k2.schemas import register_schemas
from confluent_kafka.schema_registry import SchemaRegistryClient

@pytest.mark.integration
class TestSchemaRegistry:
    """Test schema registration with real Schema Registry."""

    def test_register_all_schemas(self):
        """Schemas should register without errors."""
        schema_ids = register_schemas('http://localhost:8081')

        assert 'trade' in schema_ids
        assert 'quote' in schema_ids
        assert schema_ids['trade'] > 0

    def test_schemas_are_backward_compatible(self):
        """New schema versions must be backward compatible."""
        client = SchemaRegistryClient({'url': 'http://localhost:8081'})

        # Re-register same schema (simulates version bump)
        schema_ids = register_schemas('http://localhost:8081')

        # Should succeed (backward compatibility enforced by registry)
        assert schema_ids['trade'] > 0
```

---

## Testing

### Unit Tests
- `tests/unit/test_schemas.py` - Schema validation and structure tests

### Integration Tests
- `tests/integration/test_schema_registry.py` - Schema Registry integration

### Commands
```bash
# Unit tests-backup (no Docker needed)
pytest tests-backup/unit/test_schemas.py -v

# Integration tests-backup (requires Docker)
pytest tests-backup/integration/test_schema_registry.py -v
```

---

## Validation Checklist

- [ ] All three Avro schema files created (trade.avsc, quote.avsc, reference_data.avsc)
- [ ] Schemas parse without errors using avro-python library
- [ ] Schema module (`src/k2/schemas/__init__.py`) created
- [ ] Unit tests pass: `pytest tests/unit/test_schemas.py -v`
- [ ] Integration tests pass: `pytest tests/integration/test_schema_registry.py -v`
- [ ] Schemas visible in Kafka UI: http://localhost:8080/ui/clusters/k2-market-data/schema-registry
- [ ] Each schema has version 1 registered
- [ ] All schemas have required fields (symbol, exchange_timestamp, ingestion_timestamp)
- [ ] Decimal precision set correctly (18,6) for price fields
- [ ] Timestamp fields use logical type timestamp-millis

---

## Rollback Procedure

If this step needs to be reverted:

1. **Delete schemas from Schema Registry**:
   ```bash
   curl -X DELETE http://localhost:8081/subjects/market.trades.raw-value
   curl -X DELETE http://localhost:8081/subjects/market.quotes.raw-value
   curl -X DELETE http://localhost:8081/subjects/market.reference_datas.raw-value
   ```

2. **Remove schema files**:
   ```bash
   rm src/k2/schemas/trade.avsc
   rm src/k2/schemas/quote.avsc
   rm src/k2/schemas/reference_data.avsc
   rm src/k2/schemas/__init__.py
   ```

3. **Remove test files**:
   ```bash
   rm tests-backup/unit/test_schemas.py
   rm tests-backup/integration/test_schema_registry.py
   ```

4. **Verify clean state**:
   ```bash
   curl http://localhost:8081/subjects
   # Should return empty list: []
   ```

---

## Notes & Decisions

### Decisions Made
- **Decimal precision**: Using 18,6 for price fields to handle large values with 6 decimal places
- **Timestamp format**: Using Avro logical type timestamp-millis for consistency
- **Optional fields**: buyer_id and sequence_number are nullable to support different data sources

### Issues Encountered
[Space for documenting any issues during implementation]

### Schema Evolution Notes
- All schemas start at version 1
- Future changes must maintain backward compatibility
- Schema Registry enforces compatibility mode: BACKWARD (default)

### References
- Avro specification: https://avro.apache.org/docs/current/spec.html
- Confluent Schema Registry: https://docs.confluent.io/platform/current/schema-registry/
