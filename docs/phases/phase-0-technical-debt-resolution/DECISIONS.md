# Phase 0: Architectural Decision Records

**Last Updated**: 2026-01-13
**Phase**: Technical Debt Resolution
**Status**: Complete

---

## Decision Log

| # | Decision | Date | Status |
|---|----------|------|--------|
| 001 | [Simplified Metrics Validation](#decision-001-simplified-metrics-validation) | 2026-01-13 | Accepted |
| 002 | [Allow Warnings-Only Commits](#decision-002-allow-warnings-only-commits) | 2026-01-13 | Accepted |
| 003 | [Dead Letter Queue for Failed Messages](#decision-003-dead-letter-queue-for-failed-messages) | 2026-01-13 | Accepted |
| 004 | [29-Test Consumer Validation Suite](#decision-004-29-test-consumer-validation-suite) | 2026-01-13 | Accepted |

---

## Decision #001: Simplified Metrics Validation Approach

**Date**: 2026-01-13
**Status**: Accepted
**Deciders**: Implementation Team
**Related Items**: TD-005

### Context

Need automated validation that metrics calls use correct labels. Two approaches considered:
1. **Full Schema Matching**: Parse `metrics_registry.py` to extract expected labels for each metric
2. **Simplified Validation**: Check prohibited labels + structural validity only

### Decision

Use simplified validation approach:
- Check prohibited label combinations (e.g., "data_type" in kafka_produce_errors_total)
- Validate structural correctness (labels are dict with string keys/values)
- Warn on empty labels (encourages observability)
- **DO NOT** validate label values match metric definitions

### Consequences

**Positive**:
- Simple implementation (245 lines vs 800+ for full schema matching)
- Fast execution (<3 seconds for 83 metrics across 36 files)
- Catches 80% of issues with 20% of effort
- Easy to extend with more prohibited combinations

**Negative**:
- Can't validate label values match schema (e.g., topic="invalid" allowed)
- Can't catch new label additions/removals
- Some false negatives (bad labels may pass validation)

**Neutral**:
- Code review still needed for comprehensive validation
- Can upgrade to full schema matching later if needed

### Implementation Notes

```python
def get_prohibited_labels() -> Dict[str, List[str]]:
    """Return known prohibited label combinations from bug fixes."""
    return {
        "kafka_produce_errors_total": ["data_type"],  # TD-000: data_type causes crash
    }
```

Add new prohibited combinations as bugs are discovered.

### Verification

- [x] Validation script scans 36 files, 83 metrics calls
- [x] 0 errors found in current codebase
- [x] 27 warnings (empty labels - intentional)
- [x] Pre-commit hook prevents bad commits

---

## Decision #002: Allow Warnings-Only Commits

**Date**: 2026-01-13
**Status**: Accepted
**Deciders**: Implementation Team
**Related Items**: TD-005

### Context

Pre-commit hook can detect two types of issues:
1. **Errors**: Prohibited labels, invalid structure (must fix)
2. **Warnings**: Empty labels (may be intentional)

Question: Should we block commits with warnings?

### Decision

Allow commits with warnings (only block on errors):
- Errors (prohibited labels, bad structure) → Block commit
- Warnings (empty labels) → Show warning, allow commit
- Exit code 0 for warnings, exit code 1 for errors

### Consequences

**Positive**:
- Doesn't block valid use cases (global metrics with no labels)
- Balances safety with developer productivity
- Encourages (but doesn't enforce) better observability

**Negative**:
- Developers might ignore warnings
- Some metrics remain unlabeled (harder to filter in Prometheus)

**Neutral**:
- Code review can still catch unlabeled metrics
- Can make warnings stricter later if needed

### Implementation Notes

```bash
# Pre-commit hook allows warnings
if [ $exit_code -ne 0 ]; then
    echo "❌ Metrics validation failed!"
    exit 1
else
    echo "✅ Metrics validation passed"
    exit 0
fi
```

Validation script returns:
- Exit code 0: No errors (warnings OK)
- Exit code 1: Errors found (block commit)

### Verification

- [x] Pre-commit hook tested with warnings-only case
- [x] Commit allowed with 27 warnings
- [x] Commit blocked with 1 error (data_type label)

---

## Decision #003: Dead Letter Queue for Failed Messages

**Date**: 2026-01-13
**Status**: Accepted
**Deciders**: Implementation Team
**Related Items**: TD-003

### Context

Consumer can fail to process messages for various reasons:
1. **Transient failures**: Network timeout, temporary Kafka unavailable
2. **Permanent failures**: Malformed message, schema mismatch, invalid data

Without DLQ:
- Transient failures → Retry forever, consumer stuck
- Permanent failures → Skip message, **silent data loss**

### Decision

Implement Dead Letter Queue (DLQ) pattern:
1. Classify errors: retryable vs. permanent
2. Retry transient failures (3 attempts, exponential backoff)
3. Write permanent failures to DLQ (file-based: `dead_letter_queue/`)
4. Continue processing (don't block on bad messages)

### Consequences

**Positive**:
- Zero data loss (even on permanent failures)
- Consumer doesn't crash on bad messages
- Failed messages preserved for debugging
- Clear separation: retryable vs. permanent errors

**Negative**:
- Additional complexity (~120 lines of code)
- Need to monitor DLQ directory
- File-based DLQ doesn't scale to high volume

**Neutral**:
- Can upgrade to Kafka-based DLQ later (separate topic)
- File-based sufficient for demo/single-node deployment

### Implementation Notes

```python
RETRYABLE_ERRORS = [
    ConnectionError,
    TimeoutError,
    KafkaException,
]

PERMANENT_ERRORS = [
    ValueError,
    KeyError,
    SchemaError,
]

# In consumer loop
try:
    process_message(msg)
except PERMANENT_ERRORS as err:
    dlq.write(msg, error=err)  # Save to DLQ, don't retry
except RETRYABLE_ERRORS as err:
    retry_with_backoff(msg, max_attempts=3)  # Retry transient failures
```

### Alternatives Considered

1. **No DLQ (skip failed messages)**: Rejected - silent data loss unacceptable
2. **Crash on failure**: Rejected - bad messages block entire pipeline
3. **Kafka-based DLQ topic**: Deferred - file-based sufficient for Phase 1

### Verification

- [x] Consumer handles malformed messages without crashing
- [x] Failed messages written to `dead_letter_queue/` directory
- [x] Retries succeed on transient failures
- [x] Permanent failures don't retry infinitely

---

## Decision #004: 29-Test Consumer Validation Suite

**Date**: 2026-01-13
**Status**: Accepted
**Deciders**: Implementation Team
**Related Items**: TD-001

### Context

Sequence tracking code (380 lines) is critical for data integrity but had **zero tests**. Bugs in sequence tracking → silent data loss (gap not detected, duplicate not filtered).

Question: How comprehensive should test suite be?

### Decision

Create **29 comprehensive tests** covering:
1. Gap detection (5 tests): single, multiple, large, at start, after long sequence
2. Out-of-order messages (4 tests): late arrival, multiple, after gap, severe reordering
3. Duplicate detection (3 tests): immediate, delayed, multiple
4. Session resets (3 tests): clean, with gap, multiple
5. Multi-symbol tracking (4 tests): independent, simultaneous, isolation, cross-symbol
6. Edge cases (10 tests): first message, wraparound, negative, zero, empty fields, metrics, state, memory

### Consequences

**Positive**:
- High confidence in sequence tracking correctness
- Catches edge cases (wraparound, negative seq, empty fields)
- Discovered 3 metric bugs during test implementation
- Prevents TD-000-style runtime errors

**Negative**:
- 850 lines of test code (2.2x the production code size)
- Takes ~8 seconds to run all 29 tests
- Maintenance burden if sequence logic changes

**Neutral**:
- Test complexity justified by criticality of sequence tracking
- Can add more tests as edge cases discovered

### Implementation Notes

Test structure:
```python
class TestSequenceTracker:
    def test_gap_detection(self):
        # 1000 → 1002: gap at 1001
        assert tracker.check_sequence("asx", "BHP", 1000) == SequenceEvent.FIRST_MESSAGE
        assert tracker.check_sequence("asx", "BHP", 1002) == SequenceEvent.GAP

    def test_duplicate_detection(self):
        # 1000, 1001, 1000: duplicate detected
        assert tracker.check_sequence("asx", "BHP", 1000) == SequenceEvent.FIRST_MESSAGE
        assert tracker.check_sequence("asx", "BHP", 1001) == SequenceEvent.IN_ORDER
        assert tracker.check_sequence("asx", "BHP", 1000) == SequenceEvent.DUPLICATE
```

### Alternatives Considered

1. **Minimal tests (5-10 tests)**: Rejected - insufficient coverage for critical code
2. **Property-based testing**: Deferred - 29 explicit tests provide better documentation
3. **Integration tests only**: Rejected - need fast unit tests for CI

### Verification

- [x] All 29 tests pass in 8.45 seconds
- [x] 100% pass rate
- [x] Discovered and fixed 3 metric name bugs
- [x] Coverage: Gap detection, out-of-order, duplicates, resets, multi-symbol, edge cases

---

## Summary

### Technical Debt Resolution Principles

1. **Pragmatic Testing**: 80/20 rule - simplified validation catches 80% of issues
2. **Developer Experience**: Balance safety (block errors) with productivity (allow warnings)
3. **Zero Data Loss**: DLQ preserves failed messages for debugging
4. **Comprehensive Coverage**: Critical code (sequence tracking) deserves comprehensive tests

### Future Decisions

**Anticipated**:
1. **Upgrade to Kafka-based DLQ**: When scaling beyond single-node
2. **Full Schema Matching for Metrics**: If simplified validation proves insufficient
3. **Expand Pre-commit Validation**: Add more checks (code formatting, type hints, etc.)

---

**Last Updated**: 2026-01-13
**Phase Status**: ✅ COMPLETE
