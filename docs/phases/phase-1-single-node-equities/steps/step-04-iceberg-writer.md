# Step 4: Storage Layer - Iceberg Writer

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 6-8 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 3 (Iceberg tables must exist)
- **Blocks**: Step 8 (Kafka Consumer needs writer)

## Goal
Implement writer that appends data to Iceberg tables with transaction safety. Provide ACID guarantees ensuring either all records commit or none do, preventing partial writes during failures.

---

## Implementation

### 4.1 Implement Iceberg Writer

**File**: `src/k2/storage/writer.py`

See original IMPLEMENTATION_PLAN.md lines 803-940 for complete code.

Key components:
- `IcebergWriter` class with `write_trades()` and `write_quotes()` methods
- PyArrow table conversion (`_records_to_arrow()`)
- ACID transaction support via Iceberg append()
- Transaction logging (snapshot IDs, sequence numbers, file statistics)
- Metrics tracking (duration, record count, batch size)
- Error handling and structured logging

### 4.2 Test Writer

**Files**:
- `tests/unit/test_iceberg_writer.py` - Unit tests for conversion logic
- `tests/integration/test_iceberg_writer.py` - Integration tests with real Iceberg

---

## Testing

### Unit Tests
```python
# Test PyArrow conversion
def test_records_to_arrow_trade():
    writer = IcebergWriter()
    records = [sample_trade_record()]
    arrow_table = writer._records_to_arrow(records, "trade")
    assert arrow_table.num_rows == 1
    assert "symbol" in arrow_table.column_names
```

### Integration Tests
```python
def test_write_trades():
    writer = IcebergWriter()
    records = [sample_trade_record()]
    written = writer.write_trades(records)
    assert written == 1
    # Verify data exists in table
    table = catalog.load_table("market_data.trades")
    df = table.scan().to_pandas()
    assert len(df) >= 1
```

---

## Validation Checklist

- [ ] Writer implementation created (`src/k2/storage/writer.py`)
- [ ] Unit tests pass: `pytest tests/unit/test_iceberg_writer.py -v`
- [ ] Integration tests pass: `pytest tests/integration/test_iceberg_writer.py -v`
- [ ] Records successfully written to Iceberg
- [ ] Parquet files created in MinIO: `warehouse/market_data.db/trades/data/`
- [ ] Decimal precision maintained (no rounding errors)
- [ ] Timestamps preserved correctly
- [ ] ACID transaction test: partial writes rolled back on error
- [ ] Metrics collected (write duration, record count)

---

## Rollback Procedure

1. **Delete test data from Iceberg**:
   ```python
   from pyiceberg.catalog import load_catalog
   catalog = load_catalog(...)
   table = catalog.load_table("market_data.trades")
   # Delete all data (use with caution)
   table.delete(delete_filter="true")
   ```

2. **Remove code files**:
   ```bash
   rm src/k2/storage/writer.py
   rm tests-backup/unit/test_iceberg_writer.py
   rm tests-backup/integration/test_iceberg_writer.py
   ```

3. **Verify rollback**:
   ```bash
   # Check MinIO - data directory should be empty
   mc ls minio/warehouse/market_data.db/trades/data/
   ```

---

## Notes & Decisions

### Decisions Made
- **PyArrow for conversion**: Chosen for efficient columnar conversion
- **Batch writing**: Write in batches for performance (configurable batch size)
- **Error handling**: Log and raise on write failures (no silent failures)

### Key Learnings
- Iceberg append() is atomic - partial writes impossible
- Decimal conversion requires explicit precision/scale
- Timestamp conversion must preserve millisecond precision

### References
- PyIceberg API: https://py.iceberg.apache.org/api/
- PyArrow: https://arrow.apache.org/docs/python/
