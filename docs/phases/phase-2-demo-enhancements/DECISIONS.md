# Phase 2 Architectural Decision Records

**Last Updated**: 2026-01-11
**Phase**: Demo Enhancements
**Status**: Active

---

## Decision Log

| # | Decision | Date | Status |
|---|----------|------|--------|
| 001 | [Phase 2 Scope](#decision-001-phase-2-scope) | 2026-01-11 | Accepted |

---

## Decision #001: Phase 2 Scope

**Date**: 2026-01-11
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: All

### Context

The Principal Data Engineer Demo Review identified 7 key issues with the K2 platform. We need to decide which issues to address in Phase 2 and which to defer.

### Decision

Address all 7 issues in Phase 2:

1. **Platform Positioning** - Clear documentation of what K2 IS and ISN'T
2. **Backpressure Demo** - Implement and demonstrate circuit breaker
3. **Redis Sequence Tracker** - Production-grade sequence tracking
4. **Bloom Filter Dedup** - Scalable deduplication
5. **Hybrid Query Engine** - Unified real-time + historical queries
6. **Demo Narrative** - 10-minute structured presentation
7. **Cost Model** - FinOps documentation

Defer to Phase 3:
- Multi-region replication
- Kubernetes deployment
- Advanced authentication
- Distributed tracing

### Consequences

**Positive**:
- Comprehensive improvement addressing all review feedback
- Demo becomes Principal Engineer caliber
- Clear path from review to implementation

**Negative**:
- Larger scope requires more development effort
- Redis becomes a new dependency
- More integration complexity

**Neutral**:
- Phase 3 will focus on production deployment concerns

### Implementation Notes

Each issue maps to 1-2 implementation steps (9 steps total).

---

## Anticipated Decisions

### Decision #002: Redis vs RocksDB for Sequence Tracker
**Status**: Pending (Step 04)

**Options**:
1. **Redis** - External service, easy ops, pipelining support
2. **RocksDB** - Embedded, no network hop, Kafka Streams pattern

**Likely Decision**: Redis for operational simplicity and easier debugging

---

### Decision #003: Bloom Filter Library Selection
**Status**: Pending (Step 05)

**Options**:
1. **pybloom-live** - Well-maintained, simple API
2. **bloom-filter2** - Newer, potentially better performance
3. **redisbloom** - Redis module, all-in-one solution

**Considerations**: Memory usage, false positive rate, ease of integration

---

### Decision #004: Hybrid Query Buffer Time
**Status**: Pending (Step 06)

**Options**:
1. **1 minute** - Lower latency, risk of data gaps
2. **2 minutes** - Balanced approach (recommended)
3. **5 minutes** - Conservative, higher consistency guarantee

**Likely Decision**: 2 minutes to account for Iceberg commit lag

---

### Decision #005: Cost Model Reference Region
**Status**: Pending (Step 08)

**Options**:
1. **AWS ap-southeast-2** - Sydney, common for ASX data
2. **AWS us-east-1** - Cheapest region
3. **Multi-region** - Show costs for both

**Likely Decision**: ap-southeast-2 as primary, note on regional variations

---

## Decision Template

```markdown
## Decision #XXX: [Title]

**Date**: YYYY-MM-DD
**Status**: Proposed | Accepted | Deprecated | Superseded by #YYY
**Deciders**: [Names]
**Related Steps**: Step X, Step Y

### Context
[Problem statement - what are we solving?]

### Decision
[What we decided]

### Consequences

**Positive**:
- Benefit 1
- Benefit 2

**Negative**:
- Cost 1
- Cost 2

**Neutral**:
- Trade-off 1

### Alternatives Considered
1. **Option A**: [Why rejected]
2. **Option B**: [Why rejected]

### Implementation Notes
[How to implement]

### Verification
- [ ] Verification step 1
- [ ] Verification step 2
```

---

**Last Updated**: 2026-01-11
