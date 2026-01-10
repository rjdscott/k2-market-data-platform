# Step 11: Query Layer - CLI

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 2-3 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 9 (Query Engine), Step 10 (Replay Engine)
- **Blocks**: None (user-facing tool)

## Goal
Provide user-friendly CLI for querying data. Enable quick data exploration without writing code.

---

## Implementation

### 11.1 Implement Query CLI

**File**: `src/k2/query/cli.py`

See original plan lines 2444-2547 for complete implementation.

Commands:
- `k2-query trades` - Query trades with filters
- `k2-query summary <symbol> <date>` - Daily OHLCV summary
- `k2-query snapshots` - List Iceberg snapshots

Uses Rich library for formatted output.

---

## Validation Checklist

- [ ] CLI implemented (`src/k2/query/cli.py`)
- [ ] Entry point configured in pyproject.toml
- [ ] `k2-query trades --symbol BHP` works
- [ ] `k2-query summary BHP 2014-03-10` works
- [ ] `k2-query snapshots` lists snapshots
- [ ] Output formatted nicely with Rich tables
- [ ] Help text clear: `k2-query --help`
- [ ] All commands complete in < 5 seconds

---

## Rollback Procedure

1. **Remove CLI**:
   ```bash
   rm src/k2/query/cli.py
   ```

2. **Remove entry point from pyproject.toml**:
   ```toml
   # Remove: k2-query = "k2.query.cli:main"
   ```

3. **Reinstall package**:
   ```bash
   pip install -e .
   ```

---

## Notes & Decisions

### Decisions Made
- **Rich library**: Chosen for beautiful terminal output
- **Typer framework**: Type-safe CLI with auto-generated help

### References
- Typer: https://typer.tiangolo.com/
- Rich: https://rich.readthedocs.io/
