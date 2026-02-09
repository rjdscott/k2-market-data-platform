# Phase 7: Integration & Hardening

**Status:** ⬜ NOT STARTED
**Duration:** 1-2 weeks
**Steps:** 5
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

Full system validation under the v2 architecture. This phase makes no architectural changes — it validates that all components work correctly together, benchmarks performance, tests failure modes, and produces production-grade documentation.

At the end of this phase, the platform is **production-ready at 15.5 CPU / 19.5GB across 11 services**, fully validated, monitored, and documented.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [End-to-End Latency Benchmark](steps/step-01-latency-benchmark.md) | ⬜ Not Started | Measure trade-to-candle latency across all 6 timeframes. Target: <200ms end-to-end. Benchmark at 1x, 5x, 10x normal message rate |
| 2 | [Resource Budget Validation](steps/step-02-resource-validation.md) | ⬜ Not Started | Verify CPU <= 15.5, RAM <= 19.5GB under sustained load. 24h burn-in test. Document actual per-service consumption |
| 3 | [Failure Mode Testing](steps/step-03-failure-mode-testing.md) | ⬜ Not Started | Kill each service, verify recovery. Test: Redpanda restart, ClickHouse restart, feed handler crash, Silver processor crash, MinIO unavailable. Measure MTTR |
| 4 | [Monitoring & Alerting Finalization](steps/step-04-monitoring-alerting.md) | ⬜ Not Started | Finalize Grafana dashboards, set up Prometheus alerting rules for all critical paths. Alert on: consumer lag, insert failures, MV processing delays, cold tier offload failures |
| 5 | [Production Runbooks & Documentation](steps/step-05-runbooks-documentation.md) | ⬜ Not Started | Write operational runbooks for each failure mode. Update architecture docs with actual v2 numbers. Create on-call guide. Tag `v2-phase-7-complete` |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | Performance Validated | 1-2 | ⬜ Not Started | <200ms e2e latency, 15.5 CPU / 19.5GB RAM confirmed |
| M2 | Resilience Validated | 3 | ⬜ Not Started | All failure modes tested, MTTR < 30s for each |
| M3 | Production Ready | 4-5 | ⬜ Not Started | Monitoring, alerting, runbooks complete. Platform ready for production |

---

## Success Criteria

- [ ] End-to-end trade-to-candle latency < 200ms (target: ~11ms at normal load)
- [ ] Resource budget validated: CPU <= 15.5, RAM <= 19.5GB under 24h sustained load
- [ ] All failure modes tested with documented MTTR (target: < 30s each)
- [ ] Grafana dashboards covering all 11 services with meaningful panels
- [ ] Prometheus alerting rules for all critical failure paths
- [ ] Operational runbooks for each failure mode
- [ ] Architecture documentation updated with actual v2 performance numbers
- [ ] Git tag `v2-phase-7-complete` created

---

## Resource Impact

**No resource changes** — this phase validates the existing resource budget.

| Metric | Before (Phase 6) | After (Phase 7) | Delta |
|--------|-------------------|------------------|-------|
| CPU | ~15.5 | ~15.5 | 0 |
| RAM | ~19.5GB | ~19.5GB | 0 |
| Services | 11 | 11 | 0 |

---

## Benchmark Targets

### End-to-End Latency

| Segment | Target | Measurement Method |
|---------|--------|-------------------|
| Exchange → Kotlin handler | < 1ms | Micrometer timer |
| Handler → Redpanda | < 2ms | Producer ack latency |
| Redpanda → ClickHouse Raw | < 3ms | Kafka Engine lag |
| Raw → Bronze (MV cascade) | < 1ms | system.query_log |
| Silver Processor (Redpanda → CH) | < 3ms | Micrometer timer |
| Silver → Gold (MV) | < 1ms | system.query_log |
| **Total** | **< 11ms** | End-to-end timestamp diff |

### Throughput Under Load

| Scenario | Message Rate | Max Acceptable Latency |
|----------|-------------|----------------------|
| Normal (1x) | ~50 msg/s | < 200ms |
| Elevated (5x) | ~250 msg/s | < 500ms |
| Stress (10x) | ~500 msg/s | < 1s |

---

## Failure Mode Test Matrix

| Failure | Expected Behavior | Max Recovery Time | Data Loss Acceptable? |
|---------|------------------|-------------------|----------------------|
| Redpanda restart | Consumers reconnect, resume from offset | 10s | No |
| ClickHouse restart | Kafka Engine resumes, queries fail then recover | 15s | No (buffered in Redpanda) |
| Kotlin Feed Handler crash | Supervisor restarts, reconnects to exchange | 5s | Minimal (< 5s gap) |
| Silver Processor crash | Restart, resume from Redpanda offset | 10s | No |
| MinIO unavailable | Cold offload fails, warm tier unaffected | N/A (retry next hour) | Cold tier delayed |
| Network partition (exchange) | Handler detects, reconnects with backoff | 30s | Gap during partition |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Resource budget exceeds target under load | High | Tune ClickHouse memory, reduce batch sizes, profile hot spots |
| Subtle data inconsistencies across tiers | Medium | Cross-tier row count audits, sampling-based value comparison |
| Failure mode tests reveal unexpected behavior | Medium | Document and fix before signing off; extend phase if needed |
| Documentation debt from previous phases | Low | Dedicate Step 5 entirely to documentation finalization |

---

## Dependencies

- Phase 6 complete (all v2 components running: Kotlin handlers, Redpanda, ClickHouse, Silver Processor, Iceberg cold tier)
- Access to exchange APIs for sustained load testing
- Grafana and Prometheus running

---

## Rollback Procedure

This phase makes no changes to the running system — rollback is not applicable. If critical issues are discovered during validation, roll back to the phase where the issue was introduced.

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Phase 6: Kotlin Feed Handlers](../phase-6-kotlin-feed-handlers/README.md) -- Prerequisite phase
- [Phase 8: API Migration (Optional)](../phase-8-api-migration/README.md) -- Optional next phase
- [ADR-010: Resource Budget](../../../decisions/platform-v2/ADR-010-resource-budget.md) -- Target resource budget
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After Phase 6 completion
