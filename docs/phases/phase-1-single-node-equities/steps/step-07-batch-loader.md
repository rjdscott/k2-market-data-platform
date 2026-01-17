# Step 7: Ingestion Layer - CSV to Kafka Loader (Batch)

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 3-4 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 6 (Kafka Producer)
- **Blocks**: Step 8 (Consumer needs data to consume)

## Goal
Load historical CSV data into Kafka for initial population and testing. Provide CLI tool for batch loading market data from CSV files with progress tracking.

---

## Implementation

### 7.1 Implement CSV Batch Loader

**File**: `src/k2/ingestion/batch_loader.py`

See original plan lines 1460-1592 for complete implementation.

Key components:
- `CSVBatchLoader` class for loading CSV files
- Company mapping from reference data
- CSV parsing with timestamp conversion
- Progress tracking and logging
- Batch size control

### 7.2 Create CLI Command

**File**: `src/k2/ingestion/cli.py`

Command: `k2-ingest load-batch --data-dir data/sample`

### 7.3 Test Batch Loader

**File**: `tests/integration/test_batch_loader.py`

---

## Validation Checklist

- [ ] Batch loader implemented (`src/k2/ingestion/batch_loader.py`)
- [ ] CLI command created (`k2-ingest load-batch`)
- [ ] Integration tests pass
- [ ] Can load sample data successfully
- [ ] Messages appear in Kafka (check Kafka UI)
- [ ] Message count matches CSV row count
- [ ] Timestamps parsed correctly
- [ ] Decimal values preserved
- [ ] Progress tracking works

---

## Rollback Procedure

1. **Remove batch loader code**:
   ```bash
   rm src/k2/ingestion/batch_loader.py
   rm src/k2/ingestion/cli.py
   rm tests-backup/integration/test_batch_loader.py
   ```

2. **Purge loaded data from Kafka**:
   ```bash
   docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --delete --topic market.trades.raw
   # Recreate empty topic
   python scripts/init_infra.py
   ```

---

## Notes & Decisions

### Decisions Made
- **CSV parsing**: Direct parsing vs pandas (chose direct for control)
- **Batch size**: 1000 records per flush (tunable)
- **Error handling**: Continue on individual record errors, log and skip

### References
- Python CSV module: https://docs.python.org/3/library/csv.html
- Typer CLI: https://typer.tiangolo.com/
