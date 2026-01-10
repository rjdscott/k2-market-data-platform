# Step 15: End-to-End Testing & Demo

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 4-6 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: All previous steps (validates complete system)
- **Blocks**: Step 16 (documentation references E2E flow)

## Goal
Validate complete data flow from CSV → Kafka → Iceberg → Query API. Create reproducible demo for portfolio reviewers.

---

## Implementation

### 15.1 Create E2E Test

**File**: `tests/integration/test_e2e_flow.py`

See original plan lines 3104-3180 for complete implementation.

Test flow:
1. Load CSV to Kafka (batch loader)
2. Consume from Kafka to Iceberg
3. Query via QueryEngine
4. Query via REST API
5. Verify data correctness

### 15.2 Create Demo Script

**File**: `scripts/demo.py`

See original plan lines 3184-3262 for complete implementation.

Interactive demo that:
- Loads sample data
- Processes to Iceberg
- Queries and displays results
- Shows OHLCV summaries

### 15.3 Run E2E Test

Commands:
```bash
# Ensure infrastructure running
make docker-up
make init-infra

# Run E2E test
pytest tests/integration/test_e2e_flow.py -v -s

# Run demo
python scripts/demo.py
```

---

## Validation Checklist

- [ ] E2E test created and passes
- [ ] Demo script created and runs successfully
- [ ] Data flows through all layers correctly
- [ ] No data loss (row counts match)
- [ ] Timestamps preserved accurately
- [ ] Decimals maintain precision
- [ ] API returns correct results
- [ ] Dashboards show activity
- [ ] Demo completes in < 5 minutes

---

## Rollback Procedure

1. **Remove test and demo files**:
   ```bash
   rm tests/integration/test_e2e_flow.py
   rm scripts/demo.py
   ```

2. **Clean test data** (optional):
   ```bash
   make docker-down
   make docker-clean
   make docker-up
   make init-infra
   ```

---

## Notes & Decisions

### Decisions Made
- **Small dataset for demo**: DVN (low-volume stock) for fast execution
- **Progress tracking**: Use Rich progress bars for UX

### Test Coverage
E2E test validates:
- Schema serialization/deserialization
- ACID writes to Iceberg
- Query engine correctness
- API response format

### Demo Use Cases
- Portfolio review demonstration
- Onboarding new developers
- Testing after infrastructure changes

### References
- pytest integration testing: https://docs.pytest.org/en/stable/
- Rich progress: https://rich.readthedocs.io/en/stable/progress.html
