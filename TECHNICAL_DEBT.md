# K2 Platform - Technical Debt Tracker

**Last Updated**: 2026-01-13
**Maintained By**: Engineering Team

---

## Overview

This document tracks known technical debt, deferred fixes, and improvement opportunities in the K2 platform. Items are prioritized and estimated for future resolution.

**Priority Levels**:
- **P0 - Critical**: Blocks production use, security vulnerabilities
- **P1 - High**: Significant impact on reliability or performance
- **P2 - Medium**: Improves maintainability or user experience
- **P3 - Low**: Nice-to-have improvements

**Status**:
- **NEW**: Recently identified, not yet scheduled
- **PLANNED**: Scheduled for upcoming sprint/phase
- **IN_PROGRESS**: Currently being worked on
- **RESOLVED**: Fixed and deployed

---

## Active Technical Debt

### TD-001: Consumer Sequence Tracker API Mismatch

**Status**: NEW
**Priority**: P1 - High
**Estimate**: 1-2 hours
**Created**: 2026-01-13
**Owner**: TBD

**Description**:
Consumer is calling `SequenceTracker.check_sequence()` with wrong arguments, causing deserialization failures.

**Error**:
```
SequenceTracker.check_sequence() missing 2 required positional arguments: 'sequence' and 'timestamp'
```

**Location**:
- `src/k2/ingestion/consumer.py` - Calls to sequence tracker
- `src/k2/ingestion/sequence_tracker.py` - API definition

**Impact**:
- Consumer cannot process messages
- E2E pipeline not functional
- Blocks validation of Kafka → Iceberg flow

**Root Cause**:
API mismatch between consumer code and sequence tracker implementation, likely from incomplete P1/P2 refactoring.

**Proposed Solution**:
1. Review SequenceTracker API in `sequence_tracker.py`
2. Update consumer calls to match current API
3. Add integration test to catch API mismatches
4. Run consumer to verify fix

**Testing**:
- Unit test: Mock sequence tracker and verify correct arguments
- Integration test: Run consumer with real messages

**References**:
- Issue identified: 2026-01-13 Day 3 morning session
- Related: TD-002 (both block consumer validation)

---

### TD-002: DLQ JSON Serialization of datetime Objects

**Status**: NEW
**Priority**: P1 - High
**Estimate**: 30 minutes
**Created**: 2026-01-13
**Owner**: TBD

**Description**:
Dead Letter Queue (DLQ) cannot serialize messages containing `datetime` objects to JSON.

**Error**:
```
TypeError: Object of type datetime is not JSON serializable
```

**Location**:
- `src/k2/ingestion/dead_letter_queue.py` line 124
- `json.dumps(entry)` fails on datetime fields

**Impact**:
- DLQ writes fail when messages contain datetime objects
- Error handling compromised (cannot persist failed messages)
- Data loss risk on permanent failures

**Root Cause**:
Standard `json.dumps()` doesn't handle datetime objects. Need custom JSON encoder.

**Proposed Solution**:
```python
import json
from datetime import datetime

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# In dead_letter_queue.py:
line = json.dumps(entry, cls=DateTimeEncoder) + "\n"
```

**Testing**:
- Unit test: Write message with datetime to DLQ
- Integration test: Trigger error with datetime-containing message

**References**:
- Issue identified: 2026-01-13 Day 3 morning session
- Related: TD-001 (both block consumer validation)

---

### TD-003: Consumer Validation Incomplete

**Status**: NEW
**Priority**: P1 - High
**Estimate**: 30 minutes (after TD-001, TD-002 fixed)
**Created**: 2026-01-13
**Owner**: TBD

**Description**:
Consumer E2E validation not completed. Cannot verify Kafka → Iceberg → Query flow.

**Dependencies**:
- **Blocked by**: TD-001, TD-002
- Once those are fixed, can run full consumer validation

**Tasks**:
1. Fix TD-001 and TD-002
2. Run `scripts/simple_consumer.py` to process 5000 messages
3. Verify messages written to `market_data.trades_v2` table
4. Query Iceberg table to verify data
5. Check consumer metrics in Prometheus
6. Document E2E flow working

**Impact**:
- Cannot demonstrate full E2E pipeline
- Limits demo capabilities
- Unclear if Iceberg writes are working

**Success Criteria**:
- Consumer processes 5000+ messages without errors
- Messages appear in Iceberg table
- Query returns correct data
- Metrics show healthy consumer operation

**References**:
- Deferred: 2026-01-13 Day 3 morning session
- Plan: Address in separate session after documentation

---

### TD-004: Metrics Unit Tests Missing

**Status**: NEW
**Priority**: P2 - Medium
**Estimate**: 2-3 hours
**Created**: 2026-01-13
**Owner**: TBD

**Description**:
No unit tests verify correct labels are passed to Prometheus metrics, leading to runtime "Incorrect label names" errors.

**Impact**:
- Label mismatches only discovered at runtime
- Difficult to debug (generic error message)
- Risk of breaking metrics with code changes

**Root Cause**:
The bug fixed in commit `ee09248` (delivery callback label mismatch) would have been caught by proper testing.

**Proposed Solution**:
Create `tests/unit/test_producer_metrics.py`:

```python
from unittest.mock import patch, MagicMock
import pytest
from k2.ingestion.producer import MarketDataProducer

def test_kafka_produce_error_metric_labels():
    """Test that kafka_produce_errors_total receives correct labels."""
    with patch('k2.ingestion.producer.metrics') as mock_metrics:
        producer = MarketDataProducer()

        # Simulate delivery callback with error
        producer._delivery_callback(
            err=MagicMock(str=lambda: "KafkaError"),
            msg=None,
            context={
                "labels": {
                    "exchange": "binance",
                    "asset_class": "crypto",
                    "data_type": "trades",  # Should be filtered out
                    "topic": "market.crypto.trades"
                }
            }
        )

        # Assert metric called with correct labels (no data_type)
        mock_metrics.increment.assert_called_once()
        call_args = mock_metrics.increment.call_args
        labels = call_args[1]["labels"]

        assert "data_type" not in labels, "data_type should be filtered out"
        assert "error_type" in labels, "error_type should be added"
        assert labels["exchange"] == "binance"
        assert labels["asset_class"] == "crypto"
        assert labels["topic"] == "market.crypto.trades"
```

**Additional Tests Needed**:
- Test all metrics calls in producer.py
- Test all metrics calls in consumer.py
- Test all metrics calls in writer.py
- Mock prometheus_client and assert on label keys

**Success Criteria**:
- 20+ metrics tests covering all metrics calls
- Tests catch label mismatches before runtime
- CI fails if metrics labels incorrect

**References**:
- Identified in: Day 3 morning RCA document
- Related: commit `ee09248` (Prometheus fix)

---

### TD-005: Metrics Linting Pre-commit Hook

**Status**: NEW
**Priority**: P2 - Medium
**Estimate**: 3-4 hours
**Created**: 2026-01-13
**Owner**: TBD

**Description**:
No automated validation that metrics calls match metric definitions. Label mismatches only discovered at runtime.

**Proposed Solution**:
Create pre-commit hook that:
1. Extracts all `metrics.increment()`, `metrics.histogram()`, `metrics.gauge()` calls from code
2. Parses the labels being passed
3. Looks up metric definition in `metrics_registry.py`
4. Compares expected labels vs actual labels
5. Fails commit if mismatch detected

**Example**:
```bash
# .git/hooks/pre-commit
#!/bin/bash

echo "Validating metrics labels..."
python scripts/validate_metrics_labels.py

if [ $? -ne 0 ]; then
    echo "❌ Metrics validation failed"
    echo "   Run: python scripts/validate_metrics_labels.py --fix"
    exit 1
fi
```

**Implementation**:
- Parse Python AST to extract metrics calls
- Extract labels from kwargs
- Compare with metric definitions
- Generate report of mismatches

**Success Criteria**:
- Pre-commit hook runs on every commit
- Catches label mismatches before code review
- Provides clear error messages
- Can auto-fix simple mismatches

**References**:
- Recommended in: Day 3 morning RCA document
- Prevents: Issues like commit `ee09248`

---

### TD-006: Missing reference_data_v2.avsc Schema

**Status**: NEW
**Priority**: P2 - Medium
**Estimate**: 1 hour
**Created**: 2026-01-13
**Owner**: TBD

**Description**:
The v2 schema for reference data doesn't exist, causing `init_e2e_demo.py` to fail.

**Error**:
```
FileNotFoundError: Schema file not found: /Users/.../src/k2/schemas/reference_data_v2.avsc
Available schemas: ['trade', 'reference_data', 'trade_v2', 'quote_v2', 'quote']
```

**Impact**:
- Cannot run full E2E demo initialization
- Reference data ingestion not supported in v2
- Incomplete schema migration

**Proposed Solution**:
1. Create `src/k2/schemas/reference_data_v2.avsc` based on v1 schema
2. Add vendor_data field for flexibility
3. Register schema with Schema Registry
4. Update init scripts to handle missing schema gracefully

**Alternative**:
If reference data not needed for demo, update init script to skip reference data schema registration.

**References**:
- Discovered: 2026-01-13 during init_e2e_demo.py execution
- Related: Schema migration from v1 to v2

---

## Resolved Technical Debt

### ~~TD-000: Prometheus Metrics Label Mismatch in Producer~~

**Status**: RESOLVED
**Priority**: P0 - Critical
**Resolved**: 2026-01-13
**Resolution Time**: 2 hours

**Description**:
Producer delivery callback throwing "Incorrect label names" error, causing cascading Kafka timeout failures.

**Root Cause**:
`kafka_produce_errors_total` metric receiving extra `data_type` label that wasn't in metric definition.

**Solution Applied**:
```python
# Filter out data_type before recording error metric
error_labels = {k: v for k, v in labels.items() if k != "data_type"}
metrics.increment("kafka_produce_errors_total", labels={**error_labels, "error_type": str(err)[:50]})
```

**Files Changed**:
- `src/k2/ingestion/producer.py` (+3 lines)

**Commits**:
- `ee09248` - fix: resolve Prometheus metrics label mismatch

**Impact**:
- ✅ Binance producer now operational (4400+ trades, 0 errors)
- ✅ Pipeline restored
- ✅ Metrics recording correctly

**Documentation**:
- `docs/reviews/2026-01-13-day3-morning-fix-completed.md` - Full RCA

**Lessons Learned**:
- Always check metric definitions when adding new metrics calls
- Use consistent label filtering patterns across codebase
- Add unit tests for metrics (see TD-004)

---

## Technical Debt Metrics

**Total Active**: 6 items
**Total Resolved**: 1 item
**Estimated Effort**: 10-14 hours

**By Priority**:
- P0 Critical: 0
- P1 High: 3 items (4-5 hours)
- P2 Medium: 3 items (6-9 hours)
- P3 Low: 0

**Oldest Item**: TD-001 (0 days old)

---

## Process

### Adding New Technical Debt

1. Create new TD-XXX entry with next sequential number
2. Fill in all required fields (Status, Priority, Estimate, etc.)
3. Reference the commit/session where identified
4. Link to related issues if applicable
5. Update metrics at bottom of document
6. Commit change with message: `docs: add TD-XXX to technical debt tracker`

### Updating Technical Debt

When working on an item:
1. Update Status to IN_PROGRESS
2. Add Owner name
3. Document progress in the entry
4. When resolved, move to "Resolved Technical Debt" section
5. Update metrics

### Review Cadence

- **Weekly**: Review P0/P1 items in team meeting
- **Monthly**: Review all items, re-prioritize as needed
- **Quarterly**: Archive resolved items older than 3 months

---

**Last Review**: 2026-01-13
**Next Review**: 2026-01-20
**Maintained By**: Engineering Team

