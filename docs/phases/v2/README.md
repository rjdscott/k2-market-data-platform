# Platform v2 — Implementation Phases

**Status:** 🟡 IN PROGRESS (Phase 3 Complete, Phase 4 Starting)
**Target:** 16 CPU / 40GB RAM single Docker Compose cluster
**Estimated Duration:** 8-10 weeks (Phases 1-7), +2-3 weeks optional Phase 8
**Last Updated:** 2026-02-09

---

## Overview

The v2 platform upgrade replaces the current Python + Kafka + Spark Streaming stack with Kotlin + Redpanda + ClickHouse, reducing resource consumption from **35-40 CPU / 45-50GB** to **15.5 CPU / 19.5GB** while improving end-to-end latency by **1000x**.

This directory breaks the migration into 8 phases, each independently deliverable and revertable. Every phase has a docker-compose checkpoint that can be rolled back to if issues arise.

**Investment ranking** (see [INVESTMENT-ANALYSIS.md](../../decisions/platform-v2/INVESTMENT-ANALYSIS.md)):
1. ClickHouse + Kill Spark Streaming = **10/10 ROI** (mandatory)
2. Kafka → Redpanda = **9/10 ROI** (easy win)
3. Iceberg restructure = **7/10 ROI** (strong architectural value)
4. Kotlin feed handlers = **6/10 ROI** (conditional on scaling)
5. Spring Boot API = **3/10 ROI** (defer)

---

## Phase Map

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 6 ──→ Phase 7
Baseline     Redpanda    ClickHouse   Streaming   Cold Tier   Kotlin      Hardening
(1 wk)       (1 wk)      (1-2 wk)    Pipeline    Restructure Feed        (1-2 wk)
                                      (2 wk)      (1-2 wk)   Handlers
                                                              (2 wk)
                                                                    ╲
                                                                     → Phase 8
                                                                       API Migration
                                                                       (OPTIONAL, 2-3 wk)
```

---

## Phase Summary

| Phase | Name | Duration | Steps | Budget After | Gate |
|-------|------|----------|-------|--------------|------|
| [1](phase-1-infrastructure-baseline/README.md) | Infrastructure Baseline & Versioning | 1 week | 4 | ~38 CPU (no change) | Docker Compose v1 tagged and tested |
| [2](phase-2-redpanda-migration/README.md) | Redpanda Migration | 1 week | 5 | ~35 CPU (-3) | Kafka fully replaced, all consumers verified |
| [3](phase-3-clickhouse-foundation/README.md) | ClickHouse Foundation | 1-2 weeks | 5 | ~1.2 CPU (actual) | ✅ **COMPLETE** - Bronze/Silver/Gold operational |
| [4](phase-4-streaming-pipeline/README.md) | Streaming Pipeline Migration | 2 weeks | 7 | ~19 CPU (-18) | Spark Streaming + Prefect decommissioned |
| [5](phase-5-cold-tier-restructure/README.md) | Cold Tier Restructure | 1-2 weeks | 5 | ~17.5 CPU (-1.5) | Four-layer Iceberg, hourly offload running |
| [6](phase-6-kotlin-feed-handlers/README.md) | Kotlin Feed Handlers | 2 weeks | 5 | ~15.5 CPU (-2) | Python handlers decommissioned |
| [7](phase-7-integration-hardening/README.md) | Integration & Hardening | 1-2 weeks | 5 | **15.5 CPU ✓** | Full validation, monitoring, runbooks |
| [8](phase-8-api-migration/README.md) | API Migration (OPTIONAL) | 2-3 weeks | 5 | ~16 CPU | Spring Boot API serving production traffic |

**Total steps:** 41 (36 required + 5 optional)

---

## Infrastructure Versioning Strategy

Every phase produces a tagged docker-compose configuration. See [INFRASTRUCTURE-VERSIONING.md](INFRASTRUCTURE-VERSIONING.md) for the full rollback strategy.

```
docker-compose.yml           ← active (always points to current phase)
docker/
├── v1-baseline.yml          ← Phase 1: snapshot of current working v1
├── v2-phase-2-redpanda.yml  ← Phase 2: Redpanda replaces Kafka
├── v2-phase-3-clickhouse.yml← Phase 3: ClickHouse added
├── v2-phase-4-pipeline.yml  ← Phase 4: Spark Streaming removed
├── v2-phase-5-cold-tier.yml ← Phase 5: Iceberg restructured
├── v2-phase-6-handlers.yml  ← Phase 6: Kotlin feed handlers
└── v2-phase-7-final.yml     ← Phase 7: Production v2
```

**Rollback**: At any phase, revert to the previous docker-compose by swapping the symlink or copying the versioned file.

---

## Budget Checkpoints

| Checkpoint | CPU | RAM | Services | Fits 16/40? | Action if over |
|------------|-----|-----|----------|-------------|----------------|
| Phase 1 (baseline) | ~38 | ~48GB | 20 | No | Expected — no changes yet |
| Phase 2 (Redpanda) | ~35 | ~44GB | 17 | No | 3 services eliminated |
| Phase 3 (CH added) | ~37 | ~46GB | 18 | No | CH added, nothing removed yet |
| **Phase 4 (pipeline)** | **~19** | **~22GB** | **12** | **Close** | Spark Streaming eliminated |
| Phase 5 (cold tier) | ~17.5 | ~20GB | 12 | Nearly | Iceberg resources reduced |
| Phase 6 (handlers) | ~15.5 | ~19.5GB | 11 | **YES** ✓ | Target achieved |
| Phase 7 (hardened) | ~15.5 | ~19.5GB | 11 | **YES** ✓ | Validated in production |

**Phase 4 is the critical milestone** — it's where the massive Spark Streaming savings land.

---

## Definition of Done (Per Phase)

Every phase must meet these criteria before proceeding:

- [ ] All step acceptance criteria pass
- [ ] Docker-compose versioned and tagged in `docker/`
- [ ] Git tag created: `v2-phase-N-complete`
- [ ] Resource consumption measured and logged in PROGRESS.md
- [ ] Rollback tested (previous docker-compose starts cleanly)
- [ ] DECISIONS.md updated with any deviations from plan
- [ ] Lessons learned captured in phase STATUS.md

---

## Lessons Learned Log

This section is updated as issues arise during implementation. Each entry should capture the problem, root cause, resolution, and what we'd do differently.

| Phase | Date | Issue | Root Cause | Resolution | Lesson |
|-------|------|-------|------------|------------|--------|
| — | — | — | — | — | — |

---

## Related Documentation

- [Architecture v2](../../decisions/platform-v2/ARCHITECTURE-V2.md) — Full architecture overview
- [ADR-001 through ADR-010](../../decisions/platform-v2/) — Individual architectural decisions
- [Investment Analysis](../../decisions/platform-v2/INVESTMENT-ANALYSIS.md) — Risk/reward ranking
- [Infrastructure Versioning](INFRASTRUCTURE-VERSIONING.md) — Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** At Phase 1 kickoff
