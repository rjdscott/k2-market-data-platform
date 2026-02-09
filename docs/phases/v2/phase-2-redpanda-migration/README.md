# Phase 2: Redpanda Migration

**Status:** ⬜ NOT STARTED
**Duration:** 1 week
**Steps:** 5
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

Replace Kafka + Schema Registry + Kafka UI with a single Redpanda binary. **Zero application code changes required** -- Redpanda is fully Kafka API compatible.

This phase uses a dual-run approach: Redpanda runs alongside Kafka, producers and consumers are migrated one at a time, and only after full validation is Kafka decommissioned. This eliminates **3 services** and frees significant resources.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Deploy Redpanda Alongside Kafka](steps/step-01-deploy-redpanda.md) | ⬜ Not Started | Add Redpanda to docker-compose, configure `--smp 2 --memory 1536M`, verify startup |
| 2 | [Mirror Topics to Redpanda](steps/step-02-mirror-topics.md) | ⬜ Not Started | Create matching topics in Redpanda, configure Redpanda Schema Registry, register existing Avro schemas |
| 3 | [Switch Producers to Redpanda](steps/step-03-switch-producers.md) | ⬜ Not Started | Update feed handler broker config, verify message production, compare message counts |
| 4 | [Switch Consumers to Redpanda](steps/step-04-switch-consumers.md) | ⬜ Not Started | Update all consumer broker configs, verify end-to-end data flow, compare latency metrics |
| 5 | [Decommission Kafka Stack](steps/step-05-decommission-kafka.md) | ⬜ Not Started | Stop Kafka + Schema Registry + Kafka UI, update docker-compose, tag `v2-phase-2-complete`, measure resources |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | Dual-Run Validated | 1-3 | ⬜ Not Started | Redpanda running, topics mirrored, producers switched and verified |
| M2 | Full Cutover | 4-5 | ⬜ Not Started | All consumers on Redpanda, Kafka decommissioned, resources measured |

---

## Success Criteria

- [ ] All producers writing to Redpanda (message counts match pre-migration)
- [ ] All consumers reading from Redpanda (end-to-end data flow verified)
- [ ] Kafka + Schema Registry + Kafka UI fully decommissioned
- [ ] 3 services eliminated from docker-compose
- [ ] p99 latency <= 10ms
- [ ] Avro schemas registered in Redpanda Schema Registry
- [ ] Git tag `v2-phase-2-complete` created

---

## Resource Impact

**Resource savings target: -1.5 CPU / -1.78GB**

| Metric | Before (Phase 1) | After (Phase 2) | Delta |
|--------|-------------------|------------------|-------|
| CPU | ~38 | ~35 | -3 |
| RAM | ~48GB | ~44GB | -4GB |
| Services | 20 | 17 | -3 |

### Services Eliminated

| Service | CPU | RAM | Replaced By |
|---------|-----|-----|-------------|
| Kafka | 1.0 | 1.0GB | Redpanda |
| Schema Registry | 0.25 | 512MB | Redpanda (built-in) |
| Kafka UI | 0.25 | 256MB | Redpanda Console (built-in) |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Schema compatibility issues | High | Register all schemas in Redpanda SR before switching producers; validate with schema compatibility checks |
| Consumer offset mismatch | Medium | Reset consumer offsets when switching to Redpanda; dual-run validates message flow before cutover |
| Latency regression | Medium | Compare p99 latency metrics during dual-run; rollback if regression detected |
| Redpanda memory limits | Low | Configure `--memory 1536M` explicitly; monitor with Grafana dashboard from Phase 1 |

---

## Dependencies

- Phase 1 complete (v1 baseline tagged, monitoring dashboard live)
- All Avro schemas documented and available for registration

---

## Rollback Procedure

1. Stop Redpanda service
2. Restore Kafka broker configs in all producers/consumers
3. Swap docker-compose back to `docker/v1-baseline.yml`
4. Verify all services start and data flows

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Phase 1: Infrastructure Baseline](../phase-1-infrastructure-baseline/README.md) -- Prerequisite phase
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After Phase 1 completion
