# K2 Market Data Platform - Phase 2 Implementation Plan

**Status**: Not Started
**Target Audience**: Principal Data Engineer Review
**Last Updated**: 2026-01-11
**Overall Progress**: 0/9 steps complete (0%)

---

## Quick Links

- [Progress Tracker](./PROGRESS.md) - Detailed progress
- [Decision Log](./DECISIONS.md) - Architectural decision records
- [Verification Checklist](./reference/verification-checklist.md) - Final validation criteria
- [Success Criteria](./reference/success-criteria.md) - Demo review readiness

---

## Executive Summary

Phase 2 transforms the K2 platform from a solid Staff Engineer portfolio piece to a compelling Principal Engineer demonstration. Based on the [Principal Data Engineer Demo Review](../../reviews/2026-01-11-principal-data-engineer-demo-review.md), we address 7 key issues:

**Core Enhancements**:
- **Platform Positioning**: Clear articulation of what K2 IS and ISN'T
- **Resilience**: Working circuit breaker with demonstrable degradation
- **Scalability**: Redis-backed sequence tracking and Bloom filter deduplication
- **Unified Queries**: Hybrid engine merging Kafka real-time + Iceberg historical
- **Demo Narrative**: 10-minute story following Ingestion → Storage → Monitoring → Query
- **Business Awareness**: Cost model with scaling projections

**What's NOT Included (Deferred to Phase 3)**:
- Multi-region replication
- Kubernetes deployment manifests
- Advanced authentication (OAuth 2.0)
- Distributed tracing (OpenTelemetry)

---

## Implementation Sequence

```
Platform Positioning (Step 1)
         ↓
Resilience Layer (Steps 2-3)
         ↓
Scalability Layer (Steps 4-5)
         ↓
Query Layer (Step 6)
         ↓
Demo Enhancement (Step 7)
         ↓
Documentation (Step 8)
         ↓
Final Validation (Step 9)
```

---

## Implementation Steps

### Layer 1: Positioning & Documentation

- [ ] [**Step 01** — Platform Positioning](./steps/step-01-platform-positioning.md)
  - Update README with positioning section
  - Create tiered architecture diagram (L1/L2/L3)
  - Document what K2 IS and ISN'T
  - Add positioning to demo script

### Layer 2: Resilience & Degradation

- [ ] [**Step 02** — Circuit Breaker Implementation](./steps/step-02-circuit-breaker.md)
  - Implement DegradationLevel enum
  - Implement CircuitBreaker class
  - Implement LoadShedder for priority-based filtering
  - Add Prometheus metrics for degradation state
  - Integrate with consumer and API
  - 15+ unit tests

- [ ] [**Step 03** — Degradation Demo](./steps/step-03-degradation-demo.md)
  - Create demo_degradation.py script
  - Add Grafana panel for degradation level
  - Document demo talking points
  - Test graceful degradation scenario

### Layer 3: Scalability Components

- [ ] [**Step 04** — Redis Sequence Tracker](./steps/step-04-redis-sequence-tracker.md)
  - Implement RedisSequenceTracker class
  - Redis pipelining for batch operations
  - Backward-compatible interface
  - Configuration management
  - 15+ unit tests + integration tests

- [ ] [**Step 05** — Bloom Filter Deduplication](./steps/step-05-bloom-filter-dedup.md)
  - Implement ProductionDeduplicator class
  - Two-tier: Bloom filter + Redis confirmation
  - Memory usage optimization
  - 10+ unit tests + integration tests

### Layer 4: Query Enhancement

- [ ] [**Step 06** — Hybrid Query Engine](./steps/step-06-hybrid-query-engine.md)
  - Implement HybridQueryEngine class
  - Kafka tail consumer for recent data
  - Merge and deduplicate across sources
  - Update API endpoints
  - 15+ unit tests + integration tests

### Layer 5: Demo & Documentation

- [ ] [**Step 07** — Demo Narrative](./steps/step-07-demo-narrative.md)
  - Rewrite demo.py with 10-minute structure
  - Architecture context section
  - Ingestion demonstration
  - Storage demonstration
  - Monitoring demonstration
  - Query demonstration
  - Scaling story
  - Demo talking points document

- [ ] [**Step 08** — Cost Model](./steps/step-08-cost-model.md)
  - Create cost-model.md documentation
  - Baseline workload costs
  - Scaling projections (10x, 100x, 1000x)
  - Cost optimization triggers
  - Demo section on FinOps

### Layer 6: Validation

- [ ] [**Step 09** — Final Validation](./steps/step-09-final-validation.md)
  - End-to-end integration tests
  - Full demo run validation
  - Performance benchmarks
  - Documentation review
  - Phase-2 completion checklist

---

## Progress at a Glance

| Layer | Steps | Status | Completion |
|-------|-------|--------|------------|
| **Positioning** | 1 | ⬜ | 0% |
| **Resilience** | 2-3 | ⬜ | 0% |
| **Scalability** | 4-5 | ⬜ | 0% |
| **Query** | 6 | ⬜ | 0% |
| **Demo** | 7-8 | ⬜ | 0% |
| **Validation** | 9 | ⬜ | 0% |
| **TOTAL** | **1-9** | **⬜** | **0%** |

---

## Critical Files Reference

### New Source Files
1. `src/k2/common/circuit_breaker.py` - Degradation cascade
2. `src/k2/common/load_shedder.py` - Priority-based filtering
3. `src/k2/ingestion/redis_sequence_tracker.py` - Redis-backed tracker
4. `src/k2/ingestion/bloom_deduplicator.py` - Bloom filter + Redis
5. `src/k2/query/hybrid_engine.py` - Kafka + Iceberg merge
6. `src/k2/query/kafka_tail.py` - Kafka tail consumer

### Modified Files
1. `README.md` - Platform positioning section
2. `scripts/demo.py` - Enhanced 10-minute narrative
3. `src/k2/ingestion/consumer.py` - Circuit breaker integration
4. `src/k2/api/main.py` - Hybrid query integration
5. `config/grafana/dashboards/k2-platform.json` - Degradation panel

### New Documentation
1. `docs/architecture/platform-positioning.md`
2. `docs/operations/cost-model.md`

### New Tests
1. `tests/unit/test_circuit_breaker.py`
2. `tests/unit/test_redis_sequence_tracker.py`
3. `tests/unit/test_bloom_deduplicator.py`
4. `tests/unit/test_hybrid_engine.py`
5. `tests/integration/test_redis_sequence_tracker.py`
6. `tests/integration/test_bloom_deduplicator.py`
7. `tests/integration/test_hybrid_engine.py`

---

## Architectural Decisions (Summary)

See [DECISIONS.md](./DECISIONS.md) for complete decision records.

### Anticipated Decisions
1. **Redis vs RocksDB for Sequence Tracker** - Redis for operational simplicity
2. **Bloom Filter Library Selection** - pybloom-live vs bloom-filter2
3. **Hybrid Query Buffer Time** - 2 minutes overlap for consistency
4. **Cost Model Scope** - AWS ap-southeast-2 as reference region

---

## Testing Strategy

### New Test Categories
- `@pytest.mark.phase2` - All phase-2 tests
- `@pytest.mark.resilience` - Circuit breaker tests
- `@pytest.mark.scalability` - Redis/Bloom tests
- `@pytest.mark.hybrid` - Hybrid query tests

### Coverage Targets
- **New unit tests**: 50+ tests
- **Integration tests**: 15+ tests
- **Demo validation**: End-to-end run

### Test Organization
- `tests/unit/test_circuit_breaker.py` - Circuit breaker unit tests
- `tests/unit/test_redis_sequence_tracker.py` - Redis tracker unit tests
- `tests/unit/test_bloom_deduplicator.py` - Deduplicator unit tests
- `tests/unit/test_hybrid_engine.py` - Hybrid query unit tests
- `tests/integration/test_phase2_*.py` - Integration tests

---

## Getting Started

**Prerequisites**: Phase 1 complete (Steps 1-16)

### Current Status
Phase 2 not yet started. Begin with **Step 01: Platform Positioning**.

### Development Workflow
1. Update [PROGRESS.md](./PROGRESS.md) as each step completes
2. Log decisions in [DECISIONS.md](./DECISIONS.md)
3. Verify against [Verification Checklist](./reference/verification-checklist.md)

---

## See Also

- [Phase 1 Implementation Plan](../phase-1-core-demo/IMPLEMENTATION_PLAN.md)
- [Principal Data Engineer Demo Review](../../reviews/2026-01-11-principal-data-engineer-demo-review.md)
- [Platform Principles](../../architecture/platform-principles.md)
- [Testing Strategy](./reference/testing-summary.md)

---

**Status**: 0% complete. Ready to begin with Step 01: Platform Positioning.
