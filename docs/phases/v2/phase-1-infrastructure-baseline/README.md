# Phase 1: Infrastructure Baseline & Versioning

**Status:** ⬜ NOT STARTED
**Duration:** 1 week
**Steps:** 4
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

This phase makes **ZERO changes** to the running platform. It captures the current state, versions docker-compose, establishes rollback procedures, and creates the measurement baseline.

Everything in this phase is non-destructive. At the end, we have a tagged v1 snapshot, documented resource consumption, a working rollback procedure, and a monitoring dashboard to track the migration.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Tag v1 Stable Baseline](steps/step-01-tag-v1-baseline.md) | ⬜ Not Started | Git tag, copy docker-compose to `docker/v1-baseline.yml`, snapshot `.env` |
| 2 | [Create Resource Measurement Baseline](steps/step-02-resource-baseline.md) | ⬜ Not Started | Run `docker stats`, capture CPU/RAM per service, document actual v1 consumption |
| 3 | [Set Up Docker-Compose Versioning Structure](steps/step-03-docker-compose-versioning.md) | ⬜ Not Started | Create `docker/` directory, document rollback procedures, test rollback |
| 4 | [Establish v2 Monitoring Dashboard](steps/step-04-monitoring-dashboard.md) | ⬜ Not Started | Create Grafana dashboard for migration tracking: service count, CPU, RAM, latency metrics |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | v1 Baseline Tagged | 1-2 | ⬜ Not Started | v1 git tag exists, resource baseline documented |
| M2 | Versioning Infrastructure | 3-4 | ⬜ Not Started | Rollback tested, monitoring dashboard live |

---

## Success Criteria

- [ ] v1 tagged in git with `v1-stable` tag
- [ ] `docker/v1-baseline.yml` is a working copy of current docker-compose
- [ ] Resource baseline documented (CPU/RAM per service)
- [ ] Rollback procedure tested (v1-baseline.yml starts cleanly)
- [ ] Grafana migration tracking dashboard created and accessible

---

## Resource Impact

**No resource changes** -- this phase is purely observational and organizational.

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| CPU | ~38 | ~38 | 0 |
| RAM | ~48GB | ~48GB | 0 |
| Services | 20 | 20 | 0 |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `.env` contains secrets | Medium | Snapshot `.env` template only (strip secrets), store actual values in vault/1Password |
| Grafana not accessible | Low | Verify Grafana is running before creating dashboard |

---

## Dependencies

- None -- this is the first phase

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy
- [Architecture v2](../../../decisions/platform-v2/ARCHITECTURE-V2.md) -- Full architecture overview

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** At Phase 1 kickoff
