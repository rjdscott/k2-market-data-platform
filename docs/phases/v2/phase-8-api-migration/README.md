# Phase 8: API Migration (OPTIONAL)

**Status:** ⬜ NOT STARTED
**Duration:** 2-3 weeks
**Steps:** 5
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

**This phase is OPTIONAL.** It replaces FastAPI with Spring Boot + Kotlin for the query API. ROI is rated **3/10** in the [Investment Analysis](../../../decisions/platform-v2/INVESTMENT-ANALYSIS.md) — the weakest investment on the board.

**When to do this phase:**
- Kotlin is proven in production (feed handlers + Silver processor working well)
- Team is productive in Kotlin (learning curve absorbed)
- "Unified language" benefit outweighs rewrite cost

**When to skip:**
- Team capacity is better spent on features
- FastAPI + ClickHouse Python client works fine
- Staying at 2-3 exchanges (no scaling pressure)

**Compromise option:** Keep FastAPI, swap DuckDB for ClickHouse via `clickhouse-connect` Python client (~1 week effort). Gets the query engine upgrade without the full API rewrite.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Implement Spring Boot API Skeleton](steps/step-01-spring-boot-skeleton.md) | ⬜ Not Started | Spring Boot 3.x + Kotlin, HikariCP connection pool to ClickHouse JDBC, virtual threads, Micrometer metrics, OpenAPI docs generation |
| 2 | [Migrate API Endpoints](steps/step-02-migrate-endpoints.md) | ⬜ Not Started | Rewrite 7 endpoints: health, trades, OHLCV (6 timeframes), symbols, exchanges. Preserve existing API contract (request/response shapes) |
| 3 | [Migrate Middleware](steps/step-03-migrate-middleware.md) | ⬜ Not Started | Auth (JWT validation), rate limiting (Bucket4j or Resilience4j), CORS, request logging. Must match FastAPI behavior exactly |
| 4 | [Parallel Run API Validation](steps/step-04-parallel-run-api.md) | ⬜ Not Started | Run both APIs simultaneously. Replay production traffic to both, compare responses byte-by-byte. 24h validation window |
| 5 | [Decommission FastAPI](steps/step-05-decommission-fastapi.md) | ⬜ Not Started | Switch traffic to Spring Boot, stop FastAPI container. Update docker-compose. Measure resources. Tag `v2-phase-8-complete` |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | Spring Boot API Running | 1-2 | ⬜ Not Started | All 7 endpoints serving correct responses |
| M2 | Middleware Parity | 3 | ⬜ Not Started | Auth, rate limiting, CORS match FastAPI behavior |
| M3 | Traffic Cutover | 4-5 | ⬜ Not Started | 24h parallel run passes, FastAPI decommissioned |

---

## Success Criteria

- [ ] All 7 API endpoints migrated with identical request/response contracts
- [ ] Auth (JWT), rate limiting, and CORS match FastAPI behavior
- [ ] 24h parallel run shows byte-identical responses
- [ ] Throughput: 15,000+ req/s (vs 1,500 with FastAPI)
- [ ] p99 latency: < 3ms (vs 15ms with FastAPI)
- [ ] ClickHouse queries via HikariCP + JDBC working correctly
- [ ] FastAPI container removed from docker-compose
- [ ] Git tag `v2-phase-8-complete` created

---

## Resource Impact

**Net change: +0.5 CPU (slightly more expensive)**

| Metric | Before (Phase 7) | After (Phase 8) | Delta |
|--------|-------------------|------------------|-------|
| CPU | ~15.5 | ~16.0 | +0.5 |
| RAM | ~19.5GB | ~19.5GB | ~0 |
| Services | 11 | 11 | 0 |

### Service Swap

| Removed | CPU | RAM | Replaced By | CPU | RAM |
|---------|-----|-----|-------------|-----|-----|
| FastAPI | 1.0 | 512MB | Spring Boot API | 1.5 | 1GB |

**Note:** Spring Boot is slightly heavier than FastAPI. The benefit is performance headroom (10x throughput) and unified language, not resource savings.

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Full API rewrite introduces subtle behavioral differences | **High** | Parallel-run validation (Step 4) with byte-level response comparison |
| Spring Boot learning curve for Python-experienced team | High | Leverage Kotlin experience from Phases 4+6; Spring Boot has excellent docs |
| Rate limiting behavior differences | Medium | Use same algorithm (token bucket), test edge cases thoroughly |
| JWT validation differences | Medium | Use same JWT library claims validation; compare token parsing behavior |
| 3-4 week investment for no new functionality | **Strategic** | Only proceed if team is committed to full Kotlin stack and has capacity |

---

## Dependencies

- Phase 7 complete (platform hardened and validated)
- Team has Kotlin experience from Phases 4 + 6
- Decision to proceed (requires explicit go/no-go)

---

## Rollback Procedure

1. Stop Spring Boot API container
2. Restart FastAPI container
3. Swap docker-compose to `docker/v2-phase-7-final.yml`
4. Verify API responses from FastAPI
5. **Warning:** Once traffic is on Spring Boot for extended period, ensure FastAPI Python dependencies are still buildable

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Phase 7: Integration & Hardening](../phase-7-integration-hardening/README.md) -- Prerequisite phase
- [ADR-005: Kotlin Spring Boot API](../../../decisions/platform-v2/ADR-005-kotlin-spring-boot-api.md) -- API migration decision
- [Investment Analysis](../../../decisions/platform-v2/INVESTMENT-ANALYSIS.md) -- ROI assessment (3/10)
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After Phase 7 completion (go/no-go decision)
