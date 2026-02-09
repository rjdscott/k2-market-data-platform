# Phase 1: Infrastructure Baseline & Versioning -- Progress Tracker

**Status:** 🟡 IN PROGRESS
**Progress:** 3/4 core tasks complete (75%)
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Implementation Approach: Greenfield v2

**Decision:** Build v2 as clean greenfield stack instead of incremental migration.

**Rationale:** Clean architecture, modern best practices, parallel validation, easier rollback.

See [DECISIONS.md](DECISIONS.md) for full architectural decision record.

---

## Core Tasks

| Task | Title | Status | Completed | Notes |
|------|-------|--------|-----------|-------|
| 1 | Snapshot v1 Baseline | ✅ Complete | 2026-02-09 | `docker/v1-baseline.yml` created, git tag `v1-stable` |
| 2 | Research Component Versions | ✅ Complete | 2026-02-09 | Latest stable versions documented (Redpanda 25.3.4, ClickHouse 26.1, Kotlin 2.3.10, Spring Boot 4.0.2, Spark 4.1.1, Iceberg 1.9.2) |
| 3 | Create v2 Docker Compose | ✅ Complete | 2026-02-09 | `docker-compose.v2.yml` built from scratch with modern best practices |
| 4 | Measure v1 Resources | ✅ Complete | 2026-02-09 | v1 not running - deferred. v1-baseline.yml available for future measurement |
| 5 | Test v2 Stack | ⬜ Not Started | -- | Start v2, verify all services healthy |

---

## v2 Stack Components (Phase 1)

| Service | Image | CPU Limit | RAM Limit | Status |
|---------|-------|-----------|-----------|--------|
| Redpanda | v25.3.4 | 2.0 | 2GB | ⬜ Not tested |
| Redpanda Console | v3.5.1 | 0.5 | 256MB | ⬜ Not tested |
| ClickHouse | v26.1 | 4.0 | 8GB | ⬜ Not tested |
| Prometheus | v3.2.0 | 1.0 | 2GB | ⬜ Not tested |
| Grafana | 11.5.0 | 0.5 | 512MB | ⬜ Not tested |
| **TOTAL** | -- | **8.0** | **12.75GB** | -- |

**Budget Remaining:** 8.0 CPU / 27.25 GB (for Phases 2-7)

---

## Configuration Files Created

- ✅ `docker-compose.v2.yml` - Main v2 stack definition
- ✅ `docker/clickhouse/config.xml` - ClickHouse server config (market data optimized)
- ✅ `docker/clickhouse/users.xml` - User definitions
- ✅ `docker/prometheus/prometheus.yml` - Scrape configs
- ✅ `docker/grafana/provisioning/datasources/prometheus.yml` - Auto-provision Prometheus
- ✅ `docker/grafana/provisioning/dashboards/default.yml` - Auto-provision dashboards
- ✅ `docker/grafana/dashboards/v2-migration-tracker.json` - v2 monitoring dashboard
- ✅ `.env.v2.template` - Environment variables template
- ✅ `docker/README.md` - Configuration documentation

---

## Best Practices Applied

### Security
- ✅ Read-only config mounts (`:ro`)
- ✅ Environment variable secrets (not hardcoded)
- ✅ Named networks with explicit subnet
- ✅ Minimal user permissions

### Observability
- ✅ Health checks on all services (15s interval)
- ✅ Prometheus scraping all components
- ✅ Grafana dashboard for migration tracking
- ✅ Service labels (tier, version, service)

### Resource Management
- ✅ Explicit CPU/memory limits and reservations
- ✅ Resource budget tracking
- ✅ ulimits for ClickHouse
- ✅ Target: 16 CPU / 40GB total

### Reliability
- ✅ Restart policies (`unless-stopped`)
- ✅ Dependency ordering with health checks
- ✅ Named volumes (data persistence)
- ✅ Proper start periods

---

## Next Steps

1. **Test v2 Stack** (10 min)
   - Create `.env.v2` from template
   - Start: `docker compose -f docker-compose.v2.yml up -d`
   - Verify all services healthy
   - Access Grafana dashboard

2. **Validate Components** (15 min)
   - Redpanda: Create test topic, produce/consume
   - ClickHouse: Test query, verify Kafka engine available
   - Prometheus: Check all targets scraped
   - Grafana: View migration tracker dashboard

3. **Document Results** (10 min)
   - Update this PROGRESS.md with actual resource usage
   - Capture screenshots of Grafana dashboard
   - Document any issues encountered

4. **Phase 1 Completion** (5 min)
   - Update README.md status to ✅ Complete
   - Create git tag: `v2-phase-1-complete`
   - Snapshot: `docker/v2-phase-1-baseline.yml`

---

## Decisions Made

See [DECISIONS.md](DECISIONS.md) for detailed architectural decisions:
- Decision 2026-02-09: Greenfield v2 build vs incremental migration
- Decision 2026-02-09: Component version selection (Feb 2026 stable releases)

---

## Blockers

| Blocker | Impact | Owner | Status |
|---------|--------|-------|--------|
| None | -- | -- | -- |

---

## Resource Measurements

Will be populated after v2 stack is running:

| Service | CPU (actual) | RAM (actual) | Notes |
|---------|--------------|--------------|-------|
| Redpanda | -- | -- | Pending measurement |
| Redpanda Console | -- | -- | Pending measurement |
| ClickHouse | -- | -- | Pending measurement |
| Prometheus | -- | -- | Pending measurement |
| Grafana | -- | -- | Pending measurement |

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After v2 stack testing complete
