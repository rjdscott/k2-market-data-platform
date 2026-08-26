# Platform v2 - Architectural Decision Records

**Status:** 🟡 In Development (Phase 1 started 2026-02-09)
**Stack:** Kotlin + Redpanda + ClickHouse + Iceberg + Spring Boot + Spark (batch only)
**Target:** 16 CPU / 40GB RAM (60% reduction from v1)
**Last Updated:** 2026-02-09

---

## Overview

This directory contains Architectural Decision Records (ADRs) for the **K2 Market Data Platform v2**. These decisions document the greenfield redesign that achieves **60%+ resource reduction** while delivering **10-1000x latency improvements**.

Platform v2 is a complete architectural overhaul, not an incremental migration. See [`ARCHITECTURE-V2.md`](ARCHITECTURE-V2.md) for the full architecture overview.

---

## Platform v2 Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│  K2 MARKET DATA PLATFORM v2                                 │
│  16 CPU / 40GB RAM Budget                                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Exchange APIs → Kotlin Feed Handlers → Redpanda           │
│             (WebSocket, coroutines, 0.5 CPU)                │
│                      ↓                                      │
│              ClickHouse Kafka Engine                        │
│           (zero-code Bronze ingestion)                      │
│                      ↓                                      │
│          Kotlin Silver Processor (0.5 CPU)                  │
│        (lightweight, stateless transforms)                  │
│                      ↓                                      │
│      ClickHouse Materialized Views (real-time)              │
│        (OHLCV 1m/5m/15m/30m/1h/1d computed)                 │
│                      ↓                                      │
│    Spring Boot API (Kotlin, virtual threads)                │
│         Query routing: CH (hot) / Iceberg (cold)            │
│                      ↓                                      │
│    Spark Batch (on-demand, cold tier only)                  │
│          Compaction, maintenance, lineage                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Resource Consumption:**
- CPU: 15.5 cores (56-61% reduction from v1)
- RAM: 19.5 GB (57-61% reduction from v1)
- Services: 11 containers (42-45% reduction from v1)

**Key Improvements:**
- **E2E latency**: 5-15 min → <200ms (>1000x faster)
- **Query latency**: 200-500ms → 2-5ms (40-100x faster)
- **Throughput**: 138 msg/s → 5,000+ msg/s per handler (36x higher)

---

## Core Architectural Decisions

| ADR | Title | Status | Date | Impact |
|-----|-------|--------|------|--------|
| [ADR-001](ADR-001-replace-kafka-with-redpanda.md) | Replace Kafka with Redpanda | Accepted | 2026-02-09 | 6x lower latency, simpler ops, -3 CPU |
| [ADR-002](ADR-002-kotlin-feed-handlers.md) | Kotlin Feed Handlers | Accepted | 2026-02-09 | Coroutines, 36x throughput, -2 CPU |
| [ADR-003](ADR-003-clickhouse-warm-storage.md) | ClickHouse Warm Storage | Accepted | 2026-02-09 | Real-time OLAP, +4 CPU but eliminates DuckDB |
| [ADR-004](ADR-004-eliminate-spark-streaming.md) | Eliminate Spark Streaming | Accepted | 2026-02-09 | **-14 CPU / -20GB** (biggest win) |
| [ADR-005](ADR-005-kotlin-spring-boot-api.md) | Kotlin + Spring Boot API | Accepted | 2026-02-09 | Virtual threads, 2 CPU vs 3 CPU |
| [ADR-006](ADR-006-spark-batch-only.md) | Spark Batch Only | Accepted | 2026-02-09 | On-demand vs always-on, -11 CPU |
| [ADR-007](ADR-007-iceberg-cold-storage.md) | Iceberg Cold Storage | Accepted | 2026-02-09 | 4-layer structure, hourly offload |
| [ADR-008](ADR-008-eliminate-prefect-orchestration.md) | Eliminate Prefect Orchestration | Accepted | 2026-02-09 | CH MVs replace batch jobs, -2 CPU |
| [ADR-009](ADR-009-medallion-in-clickhouse.md) | Medallion in ClickHouse | Accepted | 2026-02-09 | Bronze/Silver/Gold in CH not Iceberg |
| [ADR-010](ADR-010-resource-budget.md) | Resource Budget Strategy | Accepted | 2026-02-09 | 16 CPU / 40GB hard constraint |

---

## Phase 5 Decisions (Implementation)

| Decision | Title | Status | Date |
|----------|-------|--------|------|
| [ADR-011](ADR-011-multi-exchange-bronze-architecture.md) | Multi-Exchange Bronze Architecture | Accepted | 2026-02-12 |
| [ADR-012](ADR-012-spark-iceberg-version-upgrade.md) | Spark + Iceberg Version Strategy | Accepted | 2026-02-12 |
| [ADR-013](ADR-013-pragmatic-iceberg-version-strategy.md) | Pragmatic Iceberg Version Strategy | Accepted | 2026-02-12 |
| [ADR-014](ADR-014-spark-based-iceberg-offload.md) | Spark-Based Iceberg Offload | Accepted | 2026-02-12 |
| [DECISION-015](DECISION-015-clickhouse-lts-downgrade.md) | ClickHouse 24.3 LTS (JDBC compat) | Accepted | 2026-02-14 |
| [DECISION-016](DECISION-016-add-coinbase-exchange.md) | Add Coinbase as 3rd Exchange | Accepted | 2026-02-18 |
| [DECISION-017](DECISION-017-iceberg-maintenance-pipeline.md) | Iceberg Daily Maintenance Pipeline | Accepted | 2026-02-18 |

---

## Supporting Documents

| Document | Description |
|----------|-------------|
| [ARCHITECTURE-V2.md](ARCHITECTURE-V2.md) | Complete v2 architecture overview (58 pages) |
| [INVESTMENT-ANALYSIS.md](INVESTMENT-ANALYSIS.md) | ROI analysis, risk/reward per decision |

---

## Implementation Status

Platform v2 is being built in 8 phases over 8-10 weeks:

| Phase | Name | Duration | Status | Key Deliverable |
|-------|------|----------|--------|-----------------|
| [1](../../phases/v2/phase-1-infrastructure-baseline/) | Infrastructure Baseline | 1 week | 🟡 In Progress | Docker Compose v2, monitoring dashboard |
| [2](../../phases/v2/phase-2-redpanda-migration/) | Redpanda Migration | 1 week | ⬜ Not Started | Kafka replaced, -3 CPU |
| [3](../../phases/v2/phase-3-clickhouse-foundation/) | ClickHouse Foundation | 1-2 weeks | ⬜ Not Started | Raw/Bronze tables ingesting |
| [4](../../phases/v2/phase-4-streaming-pipeline/) | Streaming Pipeline | 2 weeks | ⬜ Not Started | Spark Streaming gone, -18 CPU |
| [5](../../phases/v2/phase-5-cold-tier-restructure/) | Cold Tier Restructure | 1-2 weeks | ⬜ Not Started | 4-layer Iceberg |
| [6](../../phases/v2/phase-6-kotlin-feed-handlers/) | Kotlin Feed Handlers | 2 weeks | ⬜ Not Started | Python handlers gone, -2 CPU |
| [7](../../phases/v2/phase-7-integration-hardening/) | Integration Hardening | 1-2 weeks | ⬜ Not Started | **Target achieved: 15.5 CPU** ✓ |
| [8](../../phases/v2/phase-8-api-migration/) | API Migration (Optional) | 2-3 weeks | ⬜ Not Started | Spring Boot API live |

See [`../../phases/v2/`](../../phases/v2/) for detailed phase plans.

---

## Decision Drivers (v2)

Every v2 decision was evaluated against these criteria:

### Primary Goals (Mandatory)
1. **Resource Budget**: Must fit within 16 CPU / 40GB RAM
2. **Correctness**: Must maintain data guarantees (idempotency, deduplication, ordering)
3. **Observability**: Metrics, logging, health checks from day 1

### Secondary Goals (Optimized)
4. **Latency**: Real-time where possible (sub-second preferred)
5. **Throughput**: Support 5,000+ msg/s per exchange (future scale)
6. **Operational Simplicity**: Fewer services, less orchestration
7. **Cost**: Prefer open-source, single-process solutions

### Investment Ranking
See [`INVESTMENT-ANALYSIS.md`](INVESTMENT-ANALYSIS.md) for ROI scoring:
- ClickHouse + Kill Spark Streaming: **10/10 ROI** (mandatory)
- Kafka → Redpanda: **9/10 ROI** (easy win)
- Iceberg restructure: **7/10 ROI** (strong architectural value)
- Kotlin feed handlers: **6/10 ROI** (conditional on scaling)
- Spring Boot API: **3/10 ROI** (defer, Phase 8 optional)

---

## ADR Format (v2)

Platform v2 ADRs follow this format:

```markdown
# ADR-XXX: [Title]

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Implemented | Superseded
**Deciders:** [Names]
**Related Phase:** Phase X

## Context
[Why this decision matters]

## Problem Statement
[What problem we're solving]

## Considered Options
1. Option A: [Brief description]
2. Option B: [Brief description]
3. Option C: [Brief description]

## Decision
[What we decided and why]

## Consequences

**Resource Impact:**
- CPU: [Before → After]
- RAM: [Before → After]
- Services: [Before → After]

**Positive:**
- Benefit 1
- Benefit 2

**Negative:**
- Cost 1
- Cost 2

**Neutral:**
- Trade-off 1

## Validation Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Related Decisions
- Supersedes: [Platform v1 ADR, if applicable]
- Related: [Other v2 ADRs]

## References
- [Links to documentation, benchmarks, etc.]
```

---

## Comparison: v1 vs v2

| Aspect | v1 | v2 | Change |
|--------|----|----|--------|
| **Streaming Backbone** | Kafka (JVM) | Redpanda (C++) | 6x lower latency |
| **Stream Processing** | Spark Streaming (14 CPU) | Kotlin processors (0.5 CPU) | **-96% CPU** |
| **Query Engine** | DuckDB | ClickHouse | 40-100x faster |
| **OHLCV Computation** | Prefect batch jobs (hourly) | ClickHouse MVs (real-time) | <200ms vs 15 min |
| **Feed Handlers** | Python (asyncio) | Kotlin (coroutines) | 36x throughput |
| **API Layer** | FastAPI (Python) | Spring Boot (Kotlin) | Virtual threads |
| **Orchestration** | Prefect (always-on) | None (replaced by CH MVs) | -2 CPU |
| **Cold Tier** | Spark (always-on) | Spark (on-demand) | -11 CPU |
| **Total Resources** | 35-40 CPU / 45-50GB | 15.5 CPU / 19.5GB | **-60%** |

---

## Migration Strategy

Platform v2 is built **greenfield** (from scratch), not incrementally migrated. See Phase 1 decision:

- [`../../phases/v2/phase-1-infrastructure-baseline/DECISIONS.md`](../../phases/v2/phase-1-infrastructure-baseline/DECISIONS.md)

**Rationale**: Clean architecture, modern best practices, parallel validation, easier rollback.

Platform v1 remains running during v2 development. Cutover happens in Phase 7 after full validation.

---

## Related Documentation

### Phase Implementation
- [Phase 1](../../phases/v2/phase-1-infrastructure-baseline/) - Infrastructure Baseline (current)
- [Phase Map](../../phases/v2/README.md) - Full 8-phase plan
- [Versioning Strategy](../../phases/v2/INFRASTRUCTURE-VERSIONING.md) - Docker Compose rollback

### Architecture
- [ARCHITECTURE-V2.md](ARCHITECTURE-V2.md) - Complete architecture (58 pages)
- [INVESTMENT-ANALYSIS.md](INVESTMENT-ANALYSIS.md) - ROI analysis

### v1 Reference
- [Platform v1 ADRs](../../archive/v1-platform/adr/platform-v1/) - Historical v1 decisions
- v1 Phases: `../../phases/phase-0` through `phase-13`

---

## Contributing

Platform v2 is actively under development. When adding new ADRs:

1. **Use the v2 ADR template** (see format above)
2. **Number sequentially**: Next ADR is ADR-011
3. **Reference related decisions**: Link to v1 ADRs if superseding
4. **Update this README**: Add row to Core Architectural Decisions table
5. **Cross-reference phases**: Link to implementation phase(s)
6. **Resource impact required**: Always document CPU/RAM impact

---

**Last Updated:** 2026-02-09
**Maintained By:** Platform Engineering
**Questions?** See [Phase 1 README](../../phases/v2/phase-1-infrastructure-baseline/README.md)
