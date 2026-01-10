# Step 10: Query Layer - Replay Engine

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 4-5 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 3 (Iceberg tables for time-travel)
- **Blocks**: Step 11 (CLI may use replay features)

## Goal
Support time-travel queries and replay historical data. Enable backtesting and compliance scenarios with guaranteed temporal ordering.

---

## Implementation

### 10.1 Implement Replay Engine

**File**: `src/k2/query/replay.py`

See original plan lines 2209-2380 for complete implementation.

Key features:
- `ReplayEngine` class
- `cold_start_replay()` - replay from time range
- `rewind_query()` - time-travel to specific timestamp
- `list_snapshots()` - show available snapshots
- Guaranteed temporal ordering
- Batch streaming for memory efficiency

### 10.2 Test Replay Engine

**File**: `tests/integration/test_replay_engine.py`

---

## Validation Checklist

- [ ] Replay engine implemented (`src/k2/query/replay.py`)
- [ ] Cold start replay maintains temporal order
- [ ] Time-travel queries return correct snapshot
- [ ] Snapshot listing works
- [ ] Batch streaming doesn't exhaust memory
- [ ] Integration tests pass
- [ ] Can replay historical data for backtesting

---

## Rollback Procedure

1. **Remove replay engine**:
   ```bash
   rm src/k2/query/replay.py
   rm tests/integration/test_replay_engine.py
   ```

---

## Notes & Decisions

### Decisions Made
- **Batch size**: 1000 records default for replay streaming
- **Snapshot selection**: Uses Iceberg native snapshot history

### Use Cases
- Backtesting trading strategies with historical data
- Compliance auditing (query as-of specific date)
- Debugging by replaying event sequences

### References
- Iceberg time travel: https://iceberg.apache.org/docs/latest/spark-queries/#time-travel
