# Phase 2 Architectural Decision Records

**Last Updated**: 2026-01-13
**Phase**: Demo Enhancements
**Status**: Active
**Decisions Made**: 4 (001-004)
**Pending Decisions**: 3 (005-007)

---

## Decision Log

| # | Decision | Date | Status |
|---|----------|------|--------|
| 001 | [Phase 2 Scope](#decision-001-phase-2-scope) | 2026-01-11 | Accepted |
| 002 | [DegradationManager Module Naming](#decision-002-degradationmanager-module-naming) | 2026-01-13 | Accepted |
| 003 | [Load Shedding 3-Tier Symbol Classification](#decision-003-load-shedding-3-tier-symbol-classification) | 2026-01-13 | Accepted |
| 004 | [Defer Redis & Bloom Filter to Multi-Node](#decision-004-defer-redis--bloom-filter-to-multi-node) | 2026-01-13 | Accepted |

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

## Decision #002: DegradationManager Module Naming

**Date**: 2026-01-13
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 02

### Context

Step 02 required implementing a circuit breaker for graceful degradation. However, the codebase already has a `circuit_breaker.py` module that implements the fault-tolerance pattern (CLOSED/OPEN/HALF_OPEN states for handling service failures).

The new degradation system has different concerns:
- **Existing circuit breaker**: Fault tolerance (service failures, retry logic)
- **New degradation system**: Load-based degradation (consumer lag, heap pressure, 5-level cascade)

### Decision

Create a new module called `degradation_manager.py` instead of extending or replacing the existing `circuit_breaker.py`.

**Reasoning**:
1. **Separation of Concerns**: Fault tolerance ≠ Load-based degradation
2. **Different State Models**: CLOSED/OPEN/HALF_OPEN vs NORMAL/SOFT/GRACEFUL/AGGRESSIVE/CIRCUIT_BREAK
3. **Clear Intent**: Name explicitly describes load-based graceful degradation
4. **Avoid Confusion**: Both patterns are valuable and serve different purposes

### Consequences

**Positive**:
- Clear separation between fault tolerance and load management
- Existing circuit breaker remains unchanged (no regression risk)
- Module name accurately describes its purpose
- Both patterns can coexist and work together

**Negative**:
- Two modules with similar names might cause initial confusion
- Step 02 documentation referenced "circuit_breaker.py" but we created "degradation_manager.py"

**Neutral**:
- Need to update documentation to reflect actual module names

### Implementation Notes

**Created Files**:
- `src/k2/common/degradation_manager.py` - 5-level degradation cascade
- `src/k2/common/load_shedder.py` - Priority-based message filtering

**Existing Files Unchanged**:
- `src/k2/common/circuit_breaker.py` - Fault-tolerance circuit breaker

### Verification

- [x] degradation_manager.py created and tested (34 tests, 94% coverage)
- [x] load_shedder.py created and tested (30 tests, 98% coverage)
- [x] Consumer integrated with degradation manager
- [x] Documentation updated to reflect actual naming

---

## Decision #003: Load Shedding 3-Tier Symbol Classification

**Date**: 2026-01-13
**Status**: Accepted
**Deciders**: Implementation Team
**Related Steps**: Step 02

### Context

The LoadShedder needs to classify symbols by priority to determine which messages to shed under load. We needed a simple, effective classification scheme suitable for ASX market data.

### Decision

Use a 3-tier symbol classification based on market liquidity:

- **Tier 1** (CRITICAL): Top 20 symbols by volume/liquidity
- **Tier 2** (HIGH): Top 100 symbols
- **Tier 3** (NORMAL): All other symbols

**Default ASX Tier 1 Symbols** (20):
BHP, CBA, CSL, NAB, WBC, ANZ, WES, MQG, RIO, TLS, WOW, FMG, NCM, STO, WPL, AMC, GMG, TCL, COL, ALL

**Load Shedding Behavior**:
- **NORMAL/SOFT** (Level 0-1): Process all tiers
- **GRACEFUL** (Level 2): Drop LOW priority (reference data only)
- **AGGRESSIVE** (Level 3): Drop NORMAL priority (Tier 3 symbols)
- **CIRCUIT_BREAK** (Level 4): Drop HIGH priority (only Tier 1 processed)

### Consequences

**Positive**:
- Simple, understandable classification
- Based on real market dynamics (liquidity = importance)
- Easy to customize (constructor accepts custom tier sets)
- Clear prioritization during high load

**Negative**:
- Static tier classification (doesn't adapt to intraday volume changes)
- ASX-centric defaults (needs customization for other exchanges)
- Top 20/100 is approximate (would benefit from periodic updates)

**Neutral**:
- Works well for demo and initial production use
- Can be enhanced later with dynamic tier calculation

### Alternatives Considered

1. **5-Tier Classification**: Too granular for initial implementation
2. **Dynamic Tiering**: Requires real-time volume tracking, added complexity
3. **Exchange-Specific Configuration Files**: Over-engineered for demo phase

### Implementation Notes

**LoadShedder API**:
```python
# Use defaults
shedder = LoadShedder()

# Or provide custom tiers
shedder = LoadShedder(
    tier_1={'AAPL', 'GOOGL', 'MSFT'},  # Custom critical symbols
    tier_2={'TSLA', 'NVDA', 'AMD'}     # Custom high-priority symbols
)

# Check if message should be processed
if shedder.should_process(symbol, degradation_level, data_type='trades'):
    process_message(msg)
```

**Priority Assignment**:
- Tier 1 symbols → MessagePriority.CRITICAL
- Tier 2 symbols → MessagePriority.HIGH
- Tier 3 symbols → MessagePriority.NORMAL
- Reference data → MessagePriority.LOW (always, regardless of symbol)

### Verification

- [x] 3-tier classification implemented
- [x] Default ASX tiers configured (20 + 40 symbols)
- [x] Custom tier support via constructor
- [x] Priority assignment tested
- [x] Load shedding behavior validated at all levels

---

## Decision #004: Defer Redis & Bloom Filter to Multi-Node

**Date**: 2026-01-13
**Status**: Accepted
**Deciders**: User + Implementation Team
**Related Steps**: Step 04 (Redis), Step 05 (Bloom Filter)

### Context

Phase 2 Demo Enhancements originally included Steps 04 (Redis Sequence Tracker) and 05 (Bloom Filter Deduplication) to address scalability concerns from the Principal Data Engineer review. However, these features are designed for distributed systems and add complexity without benefit for single-node crypto use cases.

**Current Performance Reality**:
- Consumer throughput: 138 msg/sec (I/O bound, not CPU bound)
- Crypto message rates: 10K-50K msg/sec peak (BTCUSDT + top 10 pairs)
- Bottleneck: Iceberg writes, NOT in-memory dict operations
- Dict lookup: <1μs vs Redis network hop: 1-2ms

### Decision

**Defer Steps 04-05 (Redis Sequence Tracker, Bloom Filter Dedup) to future multi-node implementation.**

**Redirect 12-16 hours to higher-value features**:
1. Hybrid Query Engine (Step 06 → Step 04) - CRITICAL for crypto use case
2. Demo Narrative (Step 07 → Step 05) - Tell the right story
3. Cost Model (Step 08 → Step 06) - Business acumen

**New Phase 2 Plan**: 6 steps instead of 9 (3 complete, 3 remaining)

### Consequences

**Positive**:
- ✅ Saves 12-16 hours of implementation time
- ✅ Simpler architecture (no Redis dependency to maintain)
- ✅ **Faster performance** (in-memory dict <1μs vs Redis 1-2ms)
- ✅ Focus on high-value features (Hybrid queries more impressive than Redis)
- ✅ More relevant to single-node crypto demo/portfolio piece
- ✅ Less operational burden (no Redis monitoring, failure modes)

**Negative**:
- ⚠️ Sequence state lost on process restart (acceptable for crypto demo)
- ⚠️ GIL contention theoretically possible at >100K msg/sec (won't reach on single-node)
- ⚠️ Will need implementation when scaling to multi-node (but that's Phase 3+)

**Neutral**:
- Phase 2 now 6 steps instead of 9 (50% complete vs 33%)
- Redis & Bloom implementations documented in TODO.md for future reference
- Deferred features remain valid for production multi-node deployments

### Alternatives Considered

1. **Implement Redis/Bloom anyway for "completeness"**
   - **Why rejected**: Over-engineering for single-node; adds complexity without benefit
   - Would make system slower (network latency) and more complex (new dependency)

2. **Implement Redis only, skip Bloom**
   - **Why rejected**: Redis still adds latency overhead without addressing real bottleneck
   - Doesn't provide value for current throughput (138 msg/sec)

3. **Implement Bloom only, skip Redis**
   - **Why rejected**: Bloom filter requires Redis confirmation layer anyway (false positives)
   - Solving non-existent memory problem (crypto dedup fits in memory)

### Implementation Notes

**Deferred to**: TODO.md "Multi-Node Scaling Enhancements" section

**When to Reconsider**:
- Multi-node consumer fleet deployment
- Throughput exceeds 100K msg/sec per node
- Memory usage for dedup exceeds 2GB
- Need for shared state across distributed instances

**Documentation Updates**:
- Steps 04-05 marked as "🔵 Deferred" with rationale
- Phase 2 progress updated to 3/6 steps (50%)
- README updated with new step numbering
- TODO.md created with implementation plans for future

### Verification

- [x] TODO.md created with deferred features documented
- [x] Step 04-05 files updated with deferral rationale
- [x] PROGRESS.md updated to 3/6 steps
- [x] STATUS.md updated to 50% completion
- [x] README.md updated with new plan
- [x] DECISIONS.md updated with this ADR

---

## Anticipated Decisions

### Decision #005: Hybrid Query Buffer Time
**Status**: Pending (Step 04, formerly Step 06)

**Options**:
1. **1 minute** - Lower latency, risk of data gaps
2. **2 minutes** - Balanced approach (recommended)
3. **5 minutes** - Conservative, higher consistency guarantee

**Likely Decision**: 2 minutes to account for Iceberg commit lag

---

### Decision #006: Cost Model Reference Region
**Status**: Pending (Step 06, formerly Step 08)

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

**Last Updated**: 2026-01-13
