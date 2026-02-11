# Phase 5: Cold Tier Restructure -- Progress Tracker

**Status:** 🟡 IN PROGRESS (Planning Complete, Ready for Implementation)
**Progress:** 0/5 steps (0%) - Implementation ready to start
**Planning Completed:** 2026-02-11
**Last Updated:** 2026-02-11
**Phase Owner:** Platform Engineering

**Implementation Plan:** See [PHASE-5-IMPLEMENTATION-PLAN.md](PHASE-5-IMPLEMENTATION-PLAN.md) for comprehensive staff-level planning document.

**Deliverables Created (Planning Phase):**
- Iceberg DDL: 9 tables defined (Bronze: 2, Silver: 1, Gold: 6)
- PostgreSQL catalog schema
- Validation scripts
- Implementation plan (29KB, comprehensive architecture)

---

## Milestone M1: Iceberg Schema Ready (Step 1)

| Step | Title | Status | Started | Completed | Notes |
|------|-------|--------|---------|-----------|-------|
| 1 | Create Four-Layer Iceberg DDL | ⬜ Not Started | -- | -- | -- |

**Milestone Status:** ⬜ Not Started

---

## Milestone M2: Hourly Offload Running (Steps 2-3)

| Step | Title | Status | Started | Completed | Notes |
|------|-------|--------|---------|-----------|-------|
| 2 | Implement Kotlin Iceberg Writer | ⬜ Not Started | -- | -- | -- |
| 3 | Configure Hourly Offload Schedule | ⬜ Not Started | -- | -- | -- |

**Milestone Status:** ⬜ Not Started

---

## Milestone M3: Maintenance + Validation (Steps 4-5)

| Step | Title | Status | Started | Completed | Notes |
|------|-------|--------|---------|-----------|-------|
| 4 | Configure Spark Daily Maintenance | ⬜ Not Started | -- | -- | -- |
| 5 | Validate Warm-Cold Consistency | ⬜ Not Started | -- | -- | -- |

**Milestone Status:** ⬜ Not Started

---

## Offload Metrics

Captured during Steps 2-3. Tracks hourly offload performance.

| Table | Avg Rows/Hour | Avg Duration | Avg Size | Status |
|-------|---------------|-------------|----------|--------|
| cold.raw_trades | -- | -- | -- | ⬜ Pending |
| cold.bronze_trades | -- | -- | -- | ⬜ Pending |
| cold.silver_trades | -- | -- | -- | ⬜ Pending |
| cold.gold_ohlcv_* (6 tables) | -- | -- | -- | ⬜ Pending |

---

## Warm-Cold Consistency Check

Captured during Step 5. Row counts must match between ClickHouse and Iceberg.

| Layer | ClickHouse Rows | Iceberg Rows | Match | Status |
|-------|----------------|-------------|-------|--------|
| Raw | -- | -- | ⬜ | ⬜ Pending |
| Bronze | -- | -- | ⬜ | ⬜ Pending |
| Silver | -- | -- | ⬜ | ⬜ Pending |
| Gold (1m) | -- | -- | ⬜ | ⬜ Pending |
| Gold (5m) | -- | -- | ⬜ | ⬜ Pending |
| Gold (15m) | -- | -- | ⬜ | ⬜ Pending |
| Gold (30m) | -- | -- | ⬜ | ⬜ Pending |
| Gold (1h) | -- | -- | ⬜ | ⬜ Pending |
| Gold (1d) | -- | -- | ⬜ | ⬜ Pending |

---

## Resource Measurements

Captured during Step 5.

| Component | Target CPU | Actual CPU | Target RAM | Actual RAM | Status |
|-----------|-----------|-----------|-----------|-----------|--------|
| MinIO | 0.5 | -- | 1GB | -- | ⬜ Pending |
| PostgreSQL (catalog) | 0.5 | -- | 512MB | -- | ⬜ Pending |
| Iceberg REST | 0.5 | -- | 512MB | -- | ⬜ Pending |
| **Total** | **~17.5** | -- | **~20GB** | -- | ⬜ Pending |

---

## Blockers

| Blocker | Impact | Owner | Status |
|---------|--------|-------|--------|
| None | -- | -- | -- |

---

## Decisions Log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-02-11 | Standalone Kotlin sidecar (not embedded in API) | Operational isolation - offload failures don't impact API queries |
| 2026-02-11 | Hourly offload (not real-time CDC) | ~1 hour cold freshness sufficient for analytics, simpler than CDC |
| 2026-02-11 | Sequential (not parallel) offload | Avoid overwhelming ClickHouse with concurrent SELECTs |
| 2026-02-11 | Bronze per-exchange tables (2 tables, not 1) | Enables independent schema evolution per exchange |
| 2026-02-11 | No RAW layer in initial implementation | Bronze is lowest fidelity; RAW can be added later if regulatory requirements emerge |

---

**Last Updated:** 2026-02-11
**Phase Owner:** Platform Engineering
**Planning Phase:** ✅ Complete
