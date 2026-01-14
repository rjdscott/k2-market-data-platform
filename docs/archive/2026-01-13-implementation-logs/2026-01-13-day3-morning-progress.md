# Day 3 Morning Progress - Pipeline Diagnostic Session

**Date**: 2026-01-13
**Session**: Day 3 Morning (Pipeline Restoration & Validation)
**Status**: ✅ COMPLETE - Issue Resolved
**Duration**: 2 hours (1 hour diagnostic + 1 hour fix and validation)

**Resolution**: Issue fixed in commit `ee09248`. See [Day 3 Morning Fix Completed](./2026-01-13-day3-morning-fix-completed.md) for full root cause analysis and solution.

---

## Executive Summary

Started Day 3 with systematic diagnostic of Binance producer timeout issue. **Critical Finding**: The problem is MORE complex than initially assessed. It's not simply a configuration timeout - there are TWO interleaved errors:

1. **Primary Issue**: Prometheus metrics labeling error ("Incorrect label names")
2. **Secondary Issue**: Kafka message timeout (cascading effect from #1)

**Key Insight**: The pipeline WAS working during Day 1 (27,600+ trades). Something changed during Day 2 runbook testing that broke the metrics/producer interaction. This requires deeper investigation than a simple config change.

---

## Diagnostic Process (Staff Engineer Approach)

### Step 1: Initial Hypothesis
**Assumption**: Producer timeout configuration too aggressive
**Expected Fix Time**: 30-60 minutes

### Step 2: Log Analysis
**Finding**: Error pattern more complex than expected

```
[ERROR] Message delivery failed - error='KafkaError{code=_MSG_TIMED_OUT...}'
[WARNING] Transient error producing message - error='Incorrect label names' retry_count=0
[ERROR] Message delivery failed - error='KafkaError{code=_MSG_TIMED_OUT...}'
[WARNING] Transient error producing message - error='Incorrect label names' retry_count=1
[ERROR] Max retries exceeded - error='Incorrect label names'
[ERROR] Failed to produce message - error='Incorrect label names'
[ERROR] trade_produce_error - error='Incorrect label names'
```

**Pattern**: Two types of errors interleaved, suggesting concurrent message processing with different failure modes.

### Step 3: Root Cause Investigation
**Focus**: "Incorrect label names" error

**Code Path Traced**:
1. `scripts/binance_stream.py` - Receives trades from WebSocket
2. `MarketDataProducer.produce_trade()` - Routes to internal method
3. `_produce_message()` - Sets up labels and serializer
4. `_produce_with_retry()` - Attempts production with retry logic
5. **Metrics recording** - Prometheus counter/histogram calls

**Hypothesis**: Prometheus metrics call is throwing "Incorrect label names" exception, causing the producer to fail, which then times out in Kafka.

**Likely Culprit**: Metrics recording at lines 631-639 or 439-442 or 471-473 in `src/k2/ingestion/producer.py`

```python
# Line 631-639: Histogram recording after successful produce
metrics.histogram(
    "kafka_produce_duration_seconds",
    duration,
    labels={
        "exchange": exchange,
        "asset_class": asset_class,
        "topic": topic,
    },
)

# Line 471-473: Counter on max retries exceeded
metrics.increment(
    "kafka_produce_max_retries_exceeded_total",
    labels={**retry_labels, "error_type": str(e)[:50]},
)
```

**Investigation Findings**:
- Metric definitions in `metrics_registry.py` specify exact required labels
- MetricsClient adds default labels (service, environment, component)
- Final label set must EXACTLY match metric definition
- Any mismatch causes prometheus_client to throw "Incorrect label names"

**Potential Issues**:
1. Labels being passed don't match metric definition
2. Missing labels (service, environment, component) not being added properly
3. Extra labels being included that metric doesn't expect
4. Recent code changes broke label consistency

### Step 4: Git History Check
**Recent Changes**: P1 work added metrics, may have introduced inconsistency

```bash
$ git log --oneline -- src/k2/common/metrics_registry.py | head -3
98f4a52 feat(p1): add critical alerts and connection pool
6deca09 fix: E2E validation fixes - 13 critical bugs resolved
f514ac7 feat(binance): add production-grade error handling
```

**Observation**: Metrics definitions were touched during P1 work.

---

## Current Status

### What's Working ✅
- All services running (Kafka, PostgreSQL, MinIO, etc.)
- Kafka topics exist (verified)
- Binance WebSocket connection working (receiv

ing trades)
- No infrastructure issues detected

### What's Broken ❌
- Binance producer cannot send messages to Kafka
- Prometheus metrics recording throwing exceptions
- Cascading failures (metrics error → retry → timeout)

### What's Unknown ❓
- Consumer status (is it running?)
- API status (is it running?)
- Why did this break between Day 1 and Day 2?
- Exact label mismatch causing Prometheus error

---

## Time Assessment (Honest Staff Engineer Evaluation)

### Original Estimate
- **Day 3 Morning**: 2-3 hours total
  - Fix producer timeout: 30-60 min
  - Verify consumer: 30-60 min
  - Test API: 60 min
  - Document: 30 min

### Actual Time Spent
- **So Far**: 1 hour on diagnostic
- **Status**: Still on first task (producer timeout)
- **Complexity**: Higher than initially assessed

### Revised Estimate
- **Fix "Incorrect label names" bug**: 2-3 hours (debugging Prometheus metrics)
- **Verify rest of pipeline**: 1-2 hours
- **Total**: 3-5 hours (vs original 2-3 hours)

**Why Longer?**
1. Issue is code bug, not configuration
2. Prometheus metrics labeling is finicky
3. Need to trace exact label flow through MetricsClient
4. May need to fix metric definitions or usage
5. Testing fixes requires restart and validation

---

## Decision Point (Staff Engineer Recommendation)

We've hit a complexity threshold. I recommend one of three approaches:

### Option A: Continue Debugging (2-3 hours)
**Approach**: Deep dive into Prometheus metrics labeling issue
**Pros**: Will fix root cause, pipeline will work correctly
**Cons**: Time-consuming, may uncover more issues
**Best For**: If demo depends on working Binance stream
**Risk**: Medium - may take longer than estimated

### Option B: Quick Workaround (1 hour)
**Approach**: Comment out problematic metrics calls temporarily, restore basic functionality
**Pros**: Fast, gets pipeline working for validation
**Cons**: Loses observability, technical debt
**Best For**: If we just need to validate E2E flow works
**Risk**: Low - we know metrics are the issue

### Option C: Defer Binance, Validate Rest (2 hours)
**Approach**: Leave Binance broken, focus on consumer/API/query validation using existing data
**Pros**: Makes progress on other validation tasks
**Cons**: Doesn't fix main issue, leaves pipeline incomplete
**Best For**: If we want to validate architecture without Binance
**Risk**: Low - other components likely working

---

## My Recommendation (Pragmatic Approach)

**Choose Option B (Quick Workaround) + Partial Option C**

**Rationale**:
1. We're already 1 hour into what should be a 30-minute fix
2. The real goal is to validate the E2E pipeline works
3. Metrics are important but not critical for basic demo
4. We can fix metrics properly later (separate task)
5. This gets us back on track for Day 3 goals

**Proposed Next Steps** (2-3 hours total):
1. **Quick workaround** (30 min): Comment out problematic metrics calls in producer, restart Binance
2. **Verify Binance works** (15 min): Check messages flowing to Kafka
3. **Verify consumer** (30 min): Check if consumer running and writing to Iceberg
4. **Test API** (45 min): Start API if needed, test query endpoints
5. **Document working flow** (30 min): Create simple demo showing E2E pipeline

**Then afternoon**: Platform positioning + cost model (as planned)

---

## Technical Debt Created

If we choose Option B (workaround):

**TODO**: Fix Prometheus metrics labeling issue
- **File**: `src/k2/ingestion/producer.py` lines 631-639, 439-442, 471-473
- **Issue**: Metrics calls throwing "Incorrect label names"
- **Root Cause**: Label mismatch between metric definition and usage
- **Estimate**: 2-3 hours to fix properly
- **Priority**: Medium (affects observability, not functionality)
- **Impact**: Lose producer metrics until fixed

---

## Lessons Learned (So Far)

### What Went Well ✅
- Systematic diagnostic approach
- Traced error through multiple code layers
- Identified root cause (metrics labeling)
- Documented findings thoroughly

### What Could Be Better ⚠️
- Initial time estimate was optimistic
- Assumed simple config issue, was code bug
- Should have checked if anything changed between Day 1 and Day 2

### Key Insight 💡
**Simple-looking errors can hide complex issues**. The timeout error looked like a configuration problem, but was actually a code bug triggering cascading failures. Always dig deeper before assuming simple fixes.

### Staff Engineer Skill Applied 🎯
**Timeboxing and Pragmatism**: After 1 hour of debugging, reassess complexity and propose alternatives rather than continuing down rabbit hole. Know when to take a different approach.

---

## Questions for User

1. **Priority**: How important is working Binance stream vs validating the rest of the pipeline?

2. **Time Constraint**: Do we need to stay on Day 3 schedule, or can we take extra time to fix properly?

3. **Approach Preference**:
   - Option A: Fix it right (2-3 more hours)
   - Option B: Quick workaround (30 min)
   - Option C: Defer Binance, validate rest (2 hours)

4. **Technical Debt**: Are you comfortable with temporary workaround if it means faster progress?

---

## Current State Summary

### Services Status
```
Kafka: Healthy ✅
PostgreSQL: Healthy ✅
MinIO: Healthy ✅
Schema Registry: Healthy ✅
Prometheus: Healthy ✅
Grafana: Healthy ✅
Binance Stream: Stopped (for diagnostic)  ⏸️
Consumer: Unknown ❓
API: Unknown ❓
```

### Code Status
- Producer code: Has metrics labeling bug ❌
- Consumer code: Unknown (not tested) ❓
- API code: Unknown (not tested) ❓
- Metrics definitions: Potentially inconsistent ⚠️

### Time Remaining (Day 3 Morning)
- **Planned**: 2-3 hours total
- **Spent**: 1 hour (diagnostic)
- **Remaining**: 1-2 hours (if sticking to plan)

---

## Next Actions (Pending Decision)

**If Option A (Fix Properly)**:
1. Deep dive into MetricsClient label merging logic
2. Trace exact labels being passed vs expected
3. Fix metric definitions or usage
4. Test fix
5. Restart Binance and verify

**If Option B (Quick Workaround)**:
1. Comment out metrics calls in producer (3 locations)
2. Restart Binance stream
3. Verify messages flowing
4. Continue with consumer/API validation
5. Document technical debt

**If Option C (Defer Binance)**:
1. Check if consumer is running
2. Use Day 1 data in Iceberg for testing
3. Start API and test queries
4. Validate architecture without live streaming
5. Return to Binance later

---

**Prepared By**: Claude (AI Assistant, Staff Data Engineer)
**Session Type**: Diagnostic & Problem Solving
**Status**: Awaiting user decision on approach
**Next Update**: After user chooses path forward
