# Phase 10 Streaming Crypto - Architectural Decisions

**Phase**: 10 (Streaming Crypto)
**Last Updated**: 2026-01-20
**Status**: Active

---

## Decision #010: Bronze Layer Raw Bytes Pattern

**Date**: 2026-01-20
**Status**: ✅ Accepted
**Deciders**: Data Engineering Team
**Related Phase**: Phase 10 - Streaming Crypto
**Related Steps**: Step 10 (Bronze Ingestion)

### Context

The Bronze layer serves as the immutable raw data landing zone in the Medallion architecture. We needed to decide the appropriate level of processing to perform at Bronze.

### Decision

**Store raw bytes in Bronze layer** - Complete Kafka message value including 5-byte Schema Registry header + Avro payload.

Deserialization happens at Silver layer using Spark's native from_avro().

### Consequences

**Positive:**
- Perfect replayability (Bronze → Silver)
- Schema evolution resilience  
- Debugging excellence (inspect original bytes)
- Audit trail for compliance

**Negative:**
- Storage overhead (~15% increase)
- Cannot query Bronze directly

### References

- [Medallion Architecture Best Practices](https://www.databricks.com/glossary/medallion-architecture)
- [Operational Runbook](../../operations/runbooks/streaming-pipeline-operations.md)

---

## Decision #011: Silver Validation with Dead Letter Queue

**Date**: 2026-01-20
**Status**: ✅ Accepted

### Decision

Implement DLQ pattern - invalid records route to silver_dlq_trades with error categorization.

### Consequences

**Positive:**
- No silent data loss
- Audit trail + replayability
- Categorized errors for alerting

**Negative:**
- Dual write overhead
- DLQ monitoring required

---

**Last Updated**: 2026-01-20
**Maintained By**: Data Engineering Team

---

## Decision #013: Silver Binance Trigger Interval Optimization

**Date**: 2026-01-20
**Status**: ✅ Accepted
**Deciders**: Staff Data Engineer
**Related Phase**: Phase 10 - Streaming Crypto
**Related Steps**: Step 11 (Silver Transformation)

### Context

During Bronze/Silver review, observed Silver Binance transformation job consistently falling behind trigger interval:

**Observed Performance:**
- First batch: 148s (5x trigger of 30s) - Spark initialization overhead
- Steady-state: 40s (1.3x trigger of 30s) - Exceeds trigger interval

**Root Cause:**
- Avro deserialization overhead
- Validation logic execution
- Dual write streams (Silver + DLQ)
- Iceberg commit latency

**Impact:**
- Job cannot keep up with trigger interval
- Growing backlog risk
- Latency SLA violation risk

### Decision

**Increase trigger interval from 30 seconds to 60 seconds**

```python
# File: silver_binance_transformation_v3.py:229
.trigger(processingTime="60 seconds")  # Was: 30 seconds
```

**Rationale:**
- Processing time (40s) comfortably fits within 60s trigger
- Provides 20s headroom for traffic spikes
- Acceptable latency for analytics use case (not HFT)
- Aligns with industry best practices (balance latency vs throughput)

### Consequences

**Positive:**
- ✅ No more "falling behind" warnings
- ✅ Job runs sustainably under load
- ✅ Reduced Spark executor thrashing
- ✅ Better checkpoint stability

**Negative:**
- ⚠️ Increased p99 latency: Bronze → Silver now ~60s (was targeting 30s)
- ⚠️ End-to-end latency: +30s (acceptable for analytics)

**Neutral:**
- Silver Kraken unchanged (30s trigger, lower volume)
- Bronze jobs unchanged (10s/30s triggers appropriate)

### Alternatives Considered

**Alternative A: Increase resources (CPU/memory)**

Rejected because:
- Current resource usage low (0.36% CPU, 40% memory)
- Processing is I/O-bound, not CPU-bound
- Adding resources wouldn't significantly reduce 40s processing time
- More cost-effective to adjust trigger interval

**Alternative B: Optimize validation logic**

Deferred because:
- Would require profiling and UDF → native function refactoring
- Time investment: 4-8 hours
- Trigger interval increase solves immediate problem
- Can optimize later if needed

### Implementation Notes

**Applied:**
```bash
# Edit file
vim src/k2/spark/jobs/streaming/silver_binance_transformation_v3.py
# Line 229: Change 30 seconds → 60 seconds

# Restart container
docker restart k2-silver-binance-transformation

# Verify no warnings
docker logs k2-silver-binance-transformation --since 2m | grep "falling behind"
# Result: ✓ No warnings
```

**Verification:**
```bash
# Monitor for 5 minutes
docker logs -f k2-silver-binance-transformation 2>&1 | grep -E "falling behind|Batch"

# Expected: No "falling behind" warnings
# Observed: ✓ Confirmed no warnings after restart
```

### Updated SLAs

**Silver Layer Latency:**
- Previous target: <30s (p99)
- New target: <60s (p99)
- Observed: ~40-50s (well within target)

**End-to-End Latency:**
- Previous target: <5 minutes (p99)
- New target: <5 minutes (p99) - unchanged
- Impact: +30s Silver latency still within overall budget

### Monitoring

**Metrics to Track:**
```
k2_silver_batch_processing_seconds{exchange="binance",quantile="0.99"}
k2_silver_batches_behind_total{exchange="binance"}
```

**Alert Rule:**
```yaml
alert: SilverBinanceProcessingBehind
expr: k2_silver_batches_behind_total{exchange="binance"} > 3
for: 10m
labels:
  severity: warning
annotations:
  summary: "Silver Binance falling behind 60s trigger"
  description: "May need further optimization or resource scaling"
```

### References

- [Bronze/Silver Review](./BRONZE_SILVER_REVIEW_COMPLETE.md#3-performance-tuning)
- [Operational Runbook](../../operations/runbooks/streaming-pipeline-operations.md#incident-2-silver-job-falling-behind)

---

**Last Updated**: 2026-01-20
**Next Review**: After production load testing
**Maintained By**: Data Engineering Team
