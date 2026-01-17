# Step 10: Query Layer - Replay Engine

**Status**: ✅ Complete
**Assignee**: Claude Code
**Started**: 2026-01-10
**Completed**: 2026-01-10
**Estimated Time**: 4-5 hours
**Actual Time**: 1.5 hours

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

- [x] Replay engine implemented (`src/k2/query/replay.py`)
- [x] Cold start replay maintains temporal order
- [x] Time-travel queries return correct snapshot
- [x] Snapshot listing works
- [x] Batch streaming doesn't exhaust memory (generator pattern)
- [x] Unit tests pass (20/20)
- [x] Can replay historical data for backtesting

---

## Rollback Procedure

1. **Remove replay engine**:
   ```bash
   rm src/k2/query/replay.py
   rm tests-backup/integration/test_replay_engine.py
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

---

## Implementation Notes (2026-01-10)

### Files Created
- `src/k2/query/replay.py` - ReplayEngine class (~500 lines)
- `tests/unit/test_replay_engine.py` - 20 unit tests

### Key Features
- `ReplayEngine` class wrapping QueryEngine + PyIceberg catalog
- `list_snapshots()` - enumerate table snapshots with metadata
- `get_current_snapshot()` - get latest snapshot info
- `get_snapshot()` - get specific snapshot by ID
- `query_at_snapshot()` - point-in-time queries via Iceberg time-travel
- `cold_start_replay()` - generator yielding batches for streaming replay
- `rewind_to_timestamp()` - find snapshot closest to target time
- `get_replay_stats()` - statistics for replay planning
- `SnapshotInfo` dataclass with convenience properties

### Decision #018: Generator Pattern for Replay
- `cold_start_replay()` uses Python generator to stream batches
- Prevents memory exhaustion for large replays
- Each batch is ORDER BY timestamp, sequence_number ASC
