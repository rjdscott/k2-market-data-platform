# Schema Evolution Guide

**Purpose**: Migration patterns and best practices for evolving from v1 to v2 schemas
**Last Updated**: 2026-01-12

---

## Overview

This guide provides patterns, best practices, and common pitfalls for schema evolution in the K2 platform.

---

## Migration Strategy: Hard Cut vs. Dual Write

### Hard Cut (Chosen for Phase 2 Prep)

**Pros**:
- Simple implementation
- No dual-write complexity
- Faster to market
- Lower storage costs

**Cons**:
- Historical data not automatically migrated
- Breaking change for external consumers

**When to Use**: Early stage platforms with minimal production data and no external consumers.

### Dual Write

**Pros**:
- No downtime
- Gradual migration
- Backward compatibility

**Cons**:
- Complex implementation
- Higher storage costs
- Longer timeline

**When to Use**: Production systems with external consumers requiring backward compatibility.

---

## Field Mapping Patterns

### Rename Field

**v1**:
```json
{"volume": 1000}
```

**v2**:
```json
{"quantity": 1000}
```

**Mapping**: Direct rename
```python
v2_record["quantity"] = v1_record["volume"]
```

### Add Required Field

**v1**:
```json
{"symbol": "BHP", "price": 45.67}
```

**v2**:
```json
{"symbol": "BHP", "price": 45.67, "currency": "AUD"}
```

**Mapping**: Add with default
```python
v2_record["currency"] = v1_record.get("currency", "AUD")  # Default for ASX
```

### Extract to Enum

**v1**:
```json
{"side": "buy"}
```

**v2**:
```json
{"side": "BUY"}  // Enum
```

**Mapping**: Case normalization
```python
SIDE_MAPPING = {"buy": "BUY", "sell": "SELL", "short": "SELL_SHORT"}
v2_record["side"] = SIDE_MAPPING.get(v1_record["side"].lower(), "UNKNOWN")
```

### Move to vendor_data

**v1**:
```json
{"symbol": "BHP", "company_id": 123, "qualifiers": "0"}
```

**v2**:
```json
{
  "symbol": "BHP",
  "vendor_data": {
    "company_id": "123",
    "qualifiers": "0"
  }
}
```

**Mapping**: Extract to map
```python
v2_record["vendor_data"] = {
    "company_id": str(v1_record["company_id"]),
    "qualifiers": v1_record["qualifiers"]
}
```

---

## Data Type Changes

### String to Decimal

**Challenge**: Precision loss, rounding errors

**Solution**: Use Python Decimal for financial data
```python
from decimal import Decimal
v2_record["price"] = Decimal(str(v1_record["price"]))  # Always use str()
```

### Timestamp Precision

**Challenge**: Milliseconds → Microseconds

**Solution**: Multiply by 1000
```python
v2_record["timestamp"] = v1_record["timestamp"] * 1000  # ms → μs
```

---

## Schema Registry Best Practices

### Version Management

- Use semantic versioning: v1, v2, v3
- Never delete old schema versions (break consumers)
- Test backward compatibility before deploying

### Schema Evolution Rules

Avro supports these compatible changes:
- Add optional field with default
- Delete field (becomes null for old readers)
- Change field documentation

Avro does NOT support:
- Change field type (price: int → string)
- Add required field without default
- Rename field (appears as delete + add)

---

## Testing Strategy

### Unit Tests

Test each mapping function:
```python
def test_map_volume_to_quantity():
    v1 = {"volume": 1000}
    v2 = convert_to_v2(v1)
    assert v2["quantity"] == 1000
```

### Integration Tests

Test E2E pipeline:
```python
def test_v1_csv_to_v2_iceberg():
    # 1. Load v1 CSV
    batch_loader.load("v1_sample.csv")
    # 2. Convert to v2
    # 3. Write to Iceberg
    # 4. Query and verify
    df = engine.query_trades(symbol="BHP")
    assert "quantity" in df.columns
    assert "vendor_data" in df.columns
```

---

## Common Pitfalls

### 1. Floating Point Precision

❌ **Wrong**:
```python
price = float(v1_record["price"])  # Loses precision
```

✅ **Correct**:
```python
price = Decimal(str(v1_record["price"]))
```

### 2. Missing vendor_data

❌ **Wrong**:
```python
v2_record["vendor_data"] = None  # Loses ASX-specific fields
```

✅ **Correct**:
```python
v2_record["vendor_data"] = {
    "company_id": str(v1_record.get("company_id", "")),
    "qualifiers": v1_record.get("qualifiers", "")
}
```

### 3. Timezone Handling

❌ **Wrong**:
```python
timestamp = datetime.now()  # Local timezone
```

✅ **Correct**:
```python
timestamp = datetime.utcnow()  # Always UTC
```

---

## Rollback Strategy

If v2 migration fails:

1. **Stop Producer**: Stop producing v2 messages
2. **Switch Consumer**: Revert consumer to v1 schema
3. **Reprocess**: Reprocess v1 messages from Kafka offset
4. **Fix Issue**: Debug and fix v2 mapping
5. **Retry**: Resume v2 migration

---

## References

- [Avro Schema Evolution](https://avro.apache.org/docs/current/spec.html#Schema+Resolution)
- [v2 Schema Specification](v2-schema-spec.md)
- [FIX Protocol Field Names](https://www.fixtrading.org/standards/)

---

**Last Updated**: 2026-01-12
