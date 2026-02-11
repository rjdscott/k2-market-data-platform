# Platform v1 - Architectural Decision Records

**Status:** Stable (in production)
**Stack:** Python + Kafka + Spark Streaming + Iceberg + DuckDB + FastAPI + Prefect
**Last Updated:** 2026-02-09

---

## Overview

This directory contains Architectural Decision Records (ADRs) for the **K2 Market Data Platform v1**. These decisions document the architectural choices made during the initial platform development (2025-2026).

Platform v1 was successfully implemented but exceeded resource constraints (35-40 CPU / 45-50GB RAM). This led to the v2 redesign documented in [`../platform-v2/`](../platform-v2/).

---

## Platform v1 Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│  K2 MARKET DATA PLATFORM v1                                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Exchange APIs → Python Feed Handlers → Kafka              │
│                      ↓                                      │
│              Spark Structured Streaming                     │
│                (5 jobs, 14 CPU, 20GB)                       │
│                      ↓                                      │
│            Iceberg (Bronze/Silver/Gold)                     │
│                      ↓                                      │
│    DuckDB (Query Engine) ← FastAPI (API Layer)             │
│                      ↓                                      │
│               Prefect (Orchestration)                       │
│           OHLCV batch jobs, maintenance                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Resource Consumption:**
- CPU: 35-40 cores
- RAM: 45-50 GB
- Services: 18-20 containers

**Key Characteristics:**
- Streaming-first architecture (Spark Structured Streaming)
- Medallion architecture (Bronze/Silver/Gold in Iceberg)
- Python-based ecosystem
- JVM-heavy (Kafka, Spark, DuckDB)
- Orchestrated batch jobs (Prefect)

---

## ADRs in this Directory

| ADR | Title | Status | Date | Description |
|-----|-------|--------|------|-------------|
| [ADR-001](ADR-001-spark-resource-optimization.md) | Spark Resource Optimization & Memory Leak Fixes | Implemented | 2026-01-20 | Resource leak analysis, memory tuning, checkpoint cleanup |
| [ADR-002](ADR-002-bronze-per-exchange.md) | Bronze Layer Per-Exchange Architecture | Accepted | 2026-01-18 | Separate Bronze tables per exchange vs unified |
| [ADR-003](ADR-003-stream-processing-engine-selection.md) | Stream Processing Engine Selection | Accepted | 2026-01-18 | Why Spark Structured Streaming over alternatives |

---

## Status Legend

- **Accepted**: Decision approved and being implemented
- **Implemented**: Decision implemented in production v1
- **Superseded**: Replaced by v2 decision (see [`../platform-v2/`](../platform-v2/))

---

## Why v2?

Platform v1 worked correctly but was resource-intensive. Key findings that led to v2:

1. **Spark Streaming Overhead**: 14 CPU / 20GB for simple stateless transforms at 138 msg/s
2. **JVM Tax**: Multiple JVM processes (Kafka, Spark, DuckDB, Prefect) = high memory overhead
3. **Batch Job Complexity**: Prefect orchestration for OHLCV when real-time was possible
4. **Resource Budget**: Could not fit within 16 CPU / 40GB constraint

**Solution**: Platform v2 replaces Spark Streaming with lightweight Kotlin processors and ClickHouse Materialized Views, achieving 60% resource reduction while improving latency 10-1000x.

See [`../platform-v2/`](../platform-v2/) for v2 architecture and decisions.

---

## Related Documentation

### Platform v1 Documentation
- Phase implementations: `docs/phases/phase-0` through `phase-13` (v1 phases)
- Architecture: `docs/architecture/` (mix of v1 and foundational)
- Design: `docs/design/` (v1 system design)

### Platform v2 Documentation
- v2 Architecture: [`../platform-v2/ARCHITECTURE-V2.md`](../../../../decisions/platform-v2/ARCHITECTURE-V2.md)
- v2 ADRs: [`../platform-v2/`](../platform-v2/)
- v2 Phases: `docs/phases/v2/` (new implementation plan)

### Cross-Platform
- Migration strategy: `docs/phases/v2/INFRASTRUCTURE-VERSIONING.md`
- Investment analysis: `../platform-v2/INVESTMENT-ANALYSIS.md`

---

## ADR Format (v1)

Platform v1 ADRs follow this format:

```markdown
# ADR-XXX: [Title] (Platform v1)

**Date:** YYYY-MM-DD
**Status:** Accepted | Implemented | Superseded
**Platform:** v1
**Context:** [Brief context]
**Decider:** [Who made decision]

## Context and Problem Statement
[Problem description]

## Decision Drivers
[Factors influencing decision]

## Considered Options
1. Option A
2. Option B

## Decision Outcome
[What was decided]

## Consequences
**Positive:**
- Benefit 1

**Negative:**
- Cost 1

**Neutral:**
- Trade-off 1

## Implementation Notes
[How it was implemented]

## Validation
[How decision was validated]
```

---

## Contributing

Platform v1 is stable and in production. New ADRs should only be added if:
1. Documenting historical v1 decisions for completeness
2. Patching/fixing v1 (before v2 cutover)

For new architectural decisions, use [Platform v2 ADRs](../platform-v2/).

---

**Last Updated:** 2026-02-09
**Maintained By:** Platform Engineering
**Questions?** See `docs/phases/v2/` for migration strategy
