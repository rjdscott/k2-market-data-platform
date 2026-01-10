# Step 9: Query Layer - DuckDB Integration

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 4-6 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 8 (Iceberg tables with data)
- **Blocks**: Step 11 (Query CLI), Step 12 (REST API)

## Goal
Query Iceberg tables using DuckDB for analytical queries. Provide fast, embedded query engine with direct Parquet scanning capabilities.

---

## Implementation

### 9.1 Implement Query Engine

**File**: `src/k2/query/engine.py`

See original plan lines 1967-2131 for complete implementation.

Key components:
- `QueryEngine` class with DuckDB connection
- Iceberg extension installation and configuration
- S3/MinIO secret configuration
- `query_trades()` - filtered trade queries
- `get_market_summary()` - OHLCV daily aggregations
- Metrics tracking (query duration)

### 9.2 Test Query Engine

**File**: `tests/integration/test_query_engine.py`

---

## Validation Checklist

- [ ] Query engine implemented (`src/k2/query/engine.py`)
- [ ] DuckDB connects to Iceberg successfully
- [ ] Can scan Iceberg tables via `iceberg_scan()`
- [ ] Filters work correctly (symbol, timestamp ranges)
- [ ] Aggregations produce correct results (OHLCV)
- [ ] Query performance < 5 seconds for simple queries
- [ ] Integration tests pass
- [ ] Handles decimal and timestamp types correctly
- [ ] S3/MinIO authentication works

---

## Rollback Procedure

1. **Remove query engine**:
   ```bash
   rm src/k2/query/engine.py
   rm tests/integration/test_query_engine.py
   ```

2. **Verify rollback**:
   ```bash
   pytest tests/integration/test_query_engine.py
   # Should fail - module doesn't exist (expected)
   ```

---

## Notes & Decisions

### Decisions Made
- **Decision #001**: DuckDB over Spark/Presto
  - Embedded simplicity, sub-second queries
  - Perfect for local development and portfolio demo
  - Clear upgrade path to distributed engine if needed

### Performance Notes
- DuckDB's Iceberg extension uses predicate pushdown
- Direct Parquet scanning avoids intermediate copies
- Query planning handles partition pruning automatically

### References
- DuckDB Iceberg extension: https://duckdb.org/docs/extensions/iceberg.html
- DuckDB S3 configuration: https://duckdb.org/docs/extensions/httpfs.html
