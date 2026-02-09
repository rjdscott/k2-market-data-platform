# Phase 6: Kotlin Feed Handlers

**Status:** ⬜ NOT STARTED
**Duration:** 2 weeks
**Steps:** 5
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

Replace the two Python asyncio feed handlers (Binance, Kraken) with a single Kotlin coroutine-based handler using Ktor WebSocket client. This consolidates 2 containers into 1 JVM process, provides **36x throughput** (138 → 5,000+ msg/s) and **12x p99 latency** (25ms → 2ms).

This phase uses a **parallel-run pattern**: the Kotlin handler runs alongside Python handlers on separate consumer groups, output is compared for correctness, and only after validation are the Python handlers decommissioned.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Implement Kotlin Binance Handler](steps/step-01-kotlin-binance-handler.md) | ⬜ Not Started | Ktor WebSocket client, Kotlin coroutines for structured concurrency, Avro serialization via Confluent JVM client, reconnection with exponential backoff, Micrometer metrics |
| 2 | [Parallel Run Binance Handler](steps/step-02-parallel-run-binance.md) | ⬜ Not Started | Run Kotlin handler alongside Python handler on separate consumer groups. Compare message counts, timestamps, payload correctness over 24h window |
| 3 | [Implement Kotlin Kraken Handler](steps/step-03-kotlin-kraken-handler.md) | ⬜ Not Started | Add Kraken WebSocket support to same JVM process (multi-exchange architecture). Ktor client with exchange-specific protocol handling |
| 4 | [Parallel Run Kraken + Full Validation](steps/step-04-parallel-run-kraken.md) | ⬜ Not Started | Both exchanges running in Kotlin. Compare against Python for 24h. Validate reconnection behavior, backpressure handling, error recovery |
| 5 | [Decommission Python Handlers](steps/step-05-decommission-python-handlers.md) | ⬜ Not Started | Stop Python Binance + Kraken containers. Remove from docker-compose. Measure resources. Tag `v2-phase-6-complete` |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | Kotlin Binance Live | 1-2 | ⬜ Not Started | Kotlin Binance handler matches Python output over 24h |
| M2 | Both Exchanges Live | 3-4 | ⬜ Not Started | Both exchanges running in single JVM, validated against Python |
| M3 | Python Decommissioned | 5 | ⬜ Not Started | Python handlers removed, resource target met |

---

## Success Criteria

- [ ] Single Kotlin JVM process handling both Binance and Kraken WebSocket feeds
- [ ] Message output matches Python handlers (count, timestamps, payload correctness) over 24h
- [ ] Reconnection logic working (simulated disconnect recovery < 5s)
- [ ] Backpressure handling validated (Redpanda slow consumer scenario)
- [ ] 2 Python containers eliminated from docker-compose
- [ ] Resource budget at target: ~15.5 CPU / ~19.5GB
- [ ] Git tag `v2-phase-6-complete` created

---

## Resource Impact

**Net savings: -0.5 CPU / -512MB** (2 Python containers → 1 Kotlin JVM)

| Metric | Before (Phase 5) | After (Phase 6) | Delta |
|--------|-------------------|------------------|-------|
| CPU | ~17.5 | ~15.5 | -2.0 |
| RAM | ~20GB | ~19.5GB | -0.5GB |
| Services | 12 | 11 | -1 |

### Services Eliminated

| Service | CPU | RAM | Replaced By |
|---------|-----|-----|-------------|
| Python Binance Handler | 0.5 | 512MB | Kotlin Feed Handler (shared JVM) |
| Python Kraken Handler | 0.5 | 512MB | Kotlin Feed Handler (shared JVM) |

### Services Added

| Service | CPU | RAM | Purpose |
|---------|-----|-----|---------|
| Kotlin Feed Handler | 1.0 | 1GB | Both exchanges in single JVM with coroutines |

**Note:** During parallel-run (Steps 2 and 4), both Python and Kotlin handlers run simultaneously — temporary resource increase of ~1 CPU / 1GB.

---

## Architecture: Multi-Exchange Kotlin Handler

```
Exchange WebSocket APIs
├── Binance wss://stream.binance.com
└── Kraken  wss://ws.kraken.com
        |
        v
┌─────────────────────────────────────┐
│  Kotlin Feed Handler (single JVM)   │
│                                     │
│  ┌──────────┐  ┌──────────┐        │
│  │ Binance  │  │ Kraken   │        │
│  │ Coroutine│  │ Coroutine│        │
│  └────┬─────┘  └────┬─────┘        │
│       │              │              │
│       v              v              │
│  ┌─────────────────────────────┐    │
│  │ Avro Serialization (shared) │    │
│  └──────────┬──────────────────┘    │
│             │                       │
│  ┌──────────v──────────────────┐    │
│  │ Confluent Kafka Producer    │    │
│  └─────────────────────────────┘    │
│                                     │
│  Metrics: Micrometer → Prometheus   │
└─────────────────────────────────────┘
        |
        v
   Redpanda (trades topic)
```

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Kotlin/Ktor WebSocket learning curve | Medium | Ktor has mature WebSocket support; Kotlin coroutines simplify async I/O |
| Exchange-specific protocol differences | Medium | Abstract common interface; exchange-specific handlers implement protocol details |
| Reconnection reliability | **High** | Exponential backoff with jitter; circuit breaker pattern; monitor reconnection count |
| Feed handlers are critical path | **High** | Parallel-run validation before decommission; Python handlers available for instant rollback |
| JVM startup time (cold start) | Low | GraalVM or CDS (Class Data Sharing) if startup matters; otherwise acceptable at ~3-5s |

---

## Dependencies

- Phase 5 complete (cold tier restructured)
- Kotlin build toolchain available (from Phase 4 Silver Processor)
- Exchange API credentials configured
- Redpanda running with trades topic

---

## Rollback Procedure

1. Stop Kotlin Feed Handler container
2. Restart Python Binance + Kraken handler containers
3. Swap docker-compose to `docker/v2-phase-5-cold-tier.yml`
4. Verify trade data flowing through Redpanda within 30s

**Critical:** Keep Python handler Docker images available until Phase 7 validation confirms Kotlin handlers are stable in production.

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Phase 5: Cold Tier Restructure](../phase-5-cold-tier-restructure/README.md) -- Prerequisite phase
- [Phase 7: Integration & Hardening](../phase-7-integration-hardening/README.md) -- Next phase
- [ADR-002: Kotlin Feed Handlers](../../../decisions/platform-v2/ADR-002-kotlin-feed-handlers.md) -- Feed handler decision
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After Phase 5 completion
