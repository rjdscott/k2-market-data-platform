# Phase 5: Cold Tier Restructure -- Progress Tracker

**Status:** 🟡 IN PROGRESS (Milestone M1 Complete - Iceberg Infrastructure Operational)
**Progress:** 1/5 steps (20%) - Cold tier infrastructure ready, data offload next
**Planning Completed:** 2026-02-11
**Step 1 Completed:** 2026-02-11
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
| 1 | Create Four-Layer Iceberg DDL | ✅ Complete | 2026-02-11 | 2026-02-11 | 9 tables created via Hadoop catalog + tabulario image. See ADR-013 for pragmatic version strategy (Spark 3.5.5 + Iceberg 1.x). Total implementation: ~45 min after pivot from bleeding-edge versions. |

**Milestone Status:** ✅ Complete

**Implementation Details:**
- **Image**: `tabulario/spark-iceberg:latest` (Spark 3.5.5 + Iceberg 1.x)
- **Catalog**: Hadoop catalog (file-based, zero dependencies)
- **FileIO**: HadoopFileIO (local filesystem at `/home/iceberg/warehouse/`)
- **Tables Created**: 9/9 successful (Bronze: 2, Silver: 1, Gold: 6)
- **Partitioning**: Days (Bronze/Silver), Months (Gold) ✅
- **Compression**: Zstd level 3 ✅
- **DDL Execution Time**: ~15 seconds total
- **Docker Compose**: `docker-compose.phase5-iceberg.yml` (2 services: MinIO + Spark)
- **Related ADRs**: [ADR-012](../../../decisions/platform-v2/ADR-012-spark-iceberg-version-upgrade.md) (Superseded), [ADR-013](../../../decisions/platform-v2/ADR-013-pragmatic-iceberg-version-strategy.md) (Accepted)

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
| 2026-02-11 | **Pragmatic version strategy (ADR-013)** | After 4+ hours troubleshooting Spark 4.1.1 + Iceberg 1.10.1, pivoted to proven Apache tabulario image (Spark 3.5.5 + Iceberg 1.x). Unblocked Phase 5 in <1 hour. |
| 2026-02-11 | **Hadoop catalog (not REST/JDBC)** | Simplest working configuration for POC. File-based catalog requires zero dependencies (no PostgreSQL, no Hive Metastore). Production can migrate to JDBC/REST later. |
| 2026-02-11 | **Remove LOCATION clauses from DDL** | Hadoop catalog enforces path-based table locations. Custom LOCATION clauses cause "Invalid path-based table" errors. Tables auto-located at `/home/iceberg/warehouse/cold/<table_name>/`. |

---

**Last Updated:** 2026-02-11
**Phase Owner:** Platform Engineering
**Planning Phase:** ✅ Complete
