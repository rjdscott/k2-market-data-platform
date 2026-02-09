# Phase 4: Streaming Pipeline Migration -- Progress Tracker

**Status:** ⬜ NOT STARTED
**Progress:** 0/7 steps (0%)
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Milestone M1: Silver Layer Live (Steps 1-3)

| Step | Title | Status | Started | Completed | Notes |
|------|-------|--------|---------|-----------|-------|
| 1 | Create silver_trades Table DDL | ⬜ Not Started | -- | -- | -- |
| 2 | Implement Kotlin Silver Processor | ⬜ Not Started | -- | -- | -- |
| 3 | Deploy Silver Processor | ⬜ Not Started | -- | -- | -- |

**Milestone Status:** ⬜ Not Started

---

## Milestone M2: Gold Layer Live (Steps 4-5)

| Step | Title | Status | Started | Completed | Notes |
|------|-------|--------|---------|-----------|-------|
| 4 | Create Gold OHLCV Materialized Views | ⬜ Not Started | -- | -- | -- |
| 5 | Validate OHLCV Correctness | ⬜ Not Started | -- | -- | -- |

**Milestone Status:** ⬜ Not Started

---

## Milestone M3: Legacy Decommissioned (Steps 6-7)

| Step | Title | Status | Started | Completed | Notes |
|------|-------|--------|---------|-----------|-------|
| 6 | Decommission Spark Streaming + Prefect | ⬜ Not Started | -- | -- | -- |
| 7 | Resource Validation Checkpoint | ⬜ Not Started | -- | -- | -- |

**Milestone Status:** ⬜ Not Started

---

## OHLCV Validation Results

Captured during Step 5. This is a **hard gate** -- decommission (Step 6) cannot proceed without passing.

| Timeframe | v1 Row Count | v2 Row Count | Match | OHLC Tolerance (1e-8) | Volume Match | Status |
|-----------|--------------|--------------|-------|------------------------|--------------|--------|
| 1m | -- | -- | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| 5m | -- | -- | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| 15m | -- | -- | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| 30m | -- | -- | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| 1h | -- | -- | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| 1d | -- | -- | ⬜ | ⬜ | ⬜ | ⬜ Pending |

---

## Resource Measurements

Captured during Step 7.

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Total CPU | <= 22 | -- | ⬜ Pending |
| Total RAM | <= 26GB | -- | ⬜ Pending |
| Services eliminated | 9 | -- | ⬜ Pending |
| Silver Processor CPU | 0.5 | -- | ⬜ Pending |
| Silver Processor RAM | 512MB | -- | ⬜ Pending |

---

## Services Decommission Checklist

Tracked during Step 6.

| Service | Stopped | Removed from Compose | Config Preserved | Status |
|---------|---------|----------------------|------------------|--------|
| Spark Master | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Spark Worker 1 | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Spark Worker 2 | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Spark Streaming Job 1 | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Spark Streaming Job 2 | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Spark Streaming Job 3 | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Spark Streaming Job 4 | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Spark Streaming Job 5 | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Prefect Server | ⬜ | ⬜ | ⬜ | ⬜ Pending |
| Prefect Agent | ⬜ | ⬜ | ⬜ | ⬜ Pending |

---

## Blockers

| Blocker | Impact | Owner | Status |
|---------|--------|-------|--------|
| None | -- | -- | -- |

---

## Decisions Log

| Date | Decision | Reason |
|------|----------|--------|
| -- | -- | -- |

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
