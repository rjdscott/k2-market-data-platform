# RFC-XXX: [Title]

**Status**: [Draft | Under Review | Approved | Rejected | Implemented]
**Author**: [Your Name]
**Created**: YYYY-MM-DD
**Last Updated**: YYYY-MM-DD
**Approvers**: Platform Lead, [Domain Expert]

---

## Summary

_One-paragraph summary of the proposal. What are we changing and why?_

**Example**:
> This RFC proposes migrating from Kafka topic-per-exchange (3 topics) to topic-per-symbol (8000+ topics) to improve consumer isolation and reduce partition skew. Current design has AAPL dominating partition 42, causing lag for all consumers on that partition. New design allows per-symbol scaling and failure isolation.

---

## Background

_What problem are we solving? Why does it matter? Include data if possible._

**Example**:
> Current partition assignment:
> - Partition 42: AAPL (500K msg/sec), MSFT (200K msg/sec) = 700K msg/sec
> - Partition 43: Low-volume symbols (5K msg/sec total)
>
> This causes:
> 1. Consumer lag on partition 42 (consistently >1M messages)
> 2. Wasted resources on partition 43 (idle most of the time)
> 3. All-or-nothing failure (if AAPL consumer fails, MSFT also stops)

---

## Goals

_What does success look like? Be specific and measurable._

**Example**:
- Reduce p99 consumer lag from 5M messages to <100K messages
- Enable per-symbol autoscaling (scale AAPL consumers independently)
- Isolate failures (AAPL consumer crash doesn't affect MSFT)

---

## Non-Goals

_What are we explicitly NOT doing? Prevent scope creep._

**Example**:
- NOT changing schema format (keep Avro)
- NOT migrating historical data (only new data uses new topics)
- NOT adding cross-symbol ordering guarantees (out of scope)

---

## Proposal

_Detailed design. Include architecture diagrams, API changes, data models._

### Architecture Changes

**Current State**:
```
market.ticks.nasdaq (100 partitions)
  - Partition key: hash(symbol)
  - AAPL, MSFT, GOOGL → all mixed in partitions
```

**Proposed State**:
```
market.ticks.AAPL (10 partitions)
market.ticks.MSFT (10 partitions)
market.ticks.GOOGL (10 partitions)
...
  - Partition key: hash(timestamp)  # Spread load within symbol
  - One topic per symbol (8000+ topics)
```

### Migration Plan

**Phase 1: Dual Write** (Week 1)
- Producers write to BOTH old and new topics
- Consumers still read from old topics
- Validate data consistency

**Phase 2: Consumer Cutover** (Week 2)
- New consumers read from new topics
- Old consumers still active (backup)
- Monitor lag and error rates

**Phase 3: Deprecate Old Topics** (Week 3)
- Stop writing to old topics
- Delete after 7-day retention

### Rollback Plan

If p99 lag increases >2x:
1. Stop new consumers
2. Resume old consumers
3. Stop dual-write to new topics
4. RCA why new design failed

---

## Trade-offs & Alternatives

### Alternative 1: Increase Partition Count

**Pros**: Simpler (just add partitions), no topic proliferation

**Cons**:
- Doesn't solve partition skew (AAPL still dominates one partition)
- Partition count is immutable (can't change without data migration)

**Decision**: Rejected because it doesn't address root cause

---

### Alternative 2: Use Kafka Streams for Repartitioning

**Pros**: Dynamic repartitioning, no manual migration

**Cons**:
- Adds complexity (Kafka Streams is new dependency)
- Increased latency (extra hop)
- More infrastructure to manage

**Decision**: Deferred for now, revisit if topic-per-symbol fails

---

## Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Topic proliferation overwhelms Kafka | Medium | High | Benchmark 10K topics in staging, add monitoring for topic count |
| Consumer discovery complexity | High | Medium | Use naming convention `market.ticks.{symbol}`, provide discovery API |
| Schema Registry can't handle 10K schemas | Low | High | Test with 10K schemas in staging, upgrade if needed |
| Increased network overhead (more connections) | Medium | Low | Connection pooling, monitor network I/O |

---

## Performance Impact

### Expected Improvements

- **Consumer lag**: 5M → <100K messages (50x reduction)
- **p99 latency**: 500ms → 50ms (10x reduction)

### Potential Regressions

- **Topic metadata overhead**: +10% memory on Kafka brokers (8000 topics vs 3)
- **Producer latency**: +5ms (more topic lookups)

### Load Testing Results

_Run load tests and include results here_

```
Test: 1M msg/sec across 8000 topics
- Broker CPU: 60% (within budget)
- Broker memory: 12GB (within budget)
- Consumer lag: <50K messages (meets goal)
```

---

## Operational Impact

### Monitoring Changes

**New Metrics**:
- `kafka_topic_count` (alert if > 10K)
- `consumer_discovery_latency` (time to find symbol's topic)

**Updated Dashboards**:
- Per-symbol consumer lag (currently per-partition)
- Topic metadata size

### Runbook Updates

**New Runbook**: "How to add a new symbol topic"
**Updated Runbook**: "Consumer lag response" (now per-symbol)

### Training Requirements

- Platform team: Kafka topic management at scale (2-hour training)
- On-call engineers: New consumer discovery API (30-min demo)

---

## Security & Compliance

_Any security or regulatory implications?_

**Example**:
- No PII in new topics (same as current)
- Audit logging unchanged
- No new access control requirements

---

## Cost Estimate

| Component | Current Cost | New Cost | Delta |
|-----------|--------------|----------|-------|
| Kafka brokers | $5K/month | $6K/month | +$1K (20% increase) |
| Schema Registry | $500/month | $500/month | $0 |
| S3 storage | $2K/month | $2K/month | $0 |
| **Total** | **$7.5K/month** | **$8.5K/month** | **+$1K** |

**ROI**: Improved consumer reliability worth >$1K/month in reduced downtime

---

## Success Metrics

_How do we measure if this was successful?_

**Week 1 Post-Launch**:
- [ ] Consumer lag p99 < 100K messages
- [ ] Zero data loss incidents
- [ ] Zero rollbacks

**Month 1 Post-Launch**:
- [ ] Consumer autoscaling triggers successfully
- [ ] p99 latency < 100ms (from 500ms)
- [ ] No unexpected cost increases (within $1K estimate)

---

## Timeline

| Phase | Duration | Owner | Deliverables |
|-------|----------|-------|--------------|
| Design Review | 1 week | Platform Lead | Approved RFC |
| Staging Implementation | 2 weeks | Platform Team | Staging environment working |
| Load Testing | 1 week | SRE | Test results, runbooks |
| Production Rollout (Phase 1) | 1 week | Platform Team | Dual-write active |
| Production Rollout (Phase 2) | 1 week | Platform Team | Consumers migrated |
| Production Rollout (Phase 3) | 1 week | Platform Team | Old topics deleted |
| **Total** | **7 weeks** | | |

---

## Open Questions

_Unresolved questions that need answers before implementation_

1. **Q**: How do downstream systems discover new topics?
   **A**: [To be answered during review]

2. **Q**: What's the max topic count Kafka can handle?
   **A**: [Benchmark in staging, report results]

3. **Q**: Do we need partition count per symbol to be configurable?
   **A**: [Decision needed from Platform Lead]

---

## Reviewers & Approvals

**Required Approvals** (minimum 2):
- [ ] Platform Lead: [Name]
- [ ] Staff Engineer (Streaming): [Name]
- [ ] SRE Lead: [Name]

**Optional Reviewers**:
- [ ] Quant team representative
- [ ] Cost optimization team

**Approval Date**: _[Filled when approved]_

---

## Related Documents

- [Platform Principles](./PLATFORM_PRINCIPLES.md)
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md)
- [Previous RFC-025: Kafka KRaft Migration](./rfcs/025-kafka-kraft.md)

---

## Appendix

_Supporting materials: benchmarks, diagrams, code samples_

### Benchmark Results

[Attach CSV, graphs, etc.]

### Proof of Concept Code

[Link to prototype branch]