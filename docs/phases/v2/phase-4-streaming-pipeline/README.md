# Phase 4: Streaming Pipeline Migration

**Status:** ⬜ NOT STARTED
**Duration:** 2 weeks
**Steps:** 7
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

**THE CRITICAL PHASE.** Implement the Silver processor and Gold MVs in ClickHouse, validate OHLCV correctness, then decommission Spark Streaming and Prefect. This is where the massive resource savings land: **-13.5 CPU / -19.75GB**.

The Silver processor is a lightweight Kotlin service that consumes from Redpanda, validates/normalizes trade data, and batch-inserts into ClickHouse. The Gold layer is implemented entirely as ClickHouse AggregatingMergeTree materialized views -- no external compute needed for OHLCV aggregations.

**This is the highest-risk phase.** Dual-run validation (Step 5) is critical before decommission (Step 6).

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Create silver_trades Table DDL](steps/step-01-silver-trades-ddl.md) | ⬜ Not Started | MergeTree, Decimal128 prices, Enum8 side, 30d TTL, partitioned by day |
| 2 | [Implement Kotlin Silver Processor](steps/step-02-kotlin-silver-processor.md) | ⬜ Not Started | Redpanda consumer, validation + normalization + DLQ routing, ClickHouse JDBC batch insert, Micrometer metrics |
| 3 | [Deploy Silver Processor](steps/step-03-deploy-silver-processor.md) | ⬜ Not Started | Add to docker-compose at 0.5 CPU / 512MB, validate inserts to `silver_trades`, compare against existing Spark Silver output |
| 4 | [Create Gold OHLCV Materialized Views](steps/step-04-gold-ohlcv-mvs.md) | ⬜ Not Started | 6 MVs: 1m, 5m, 15m, 30m, 1h, 1d -- all AggregatingMergeTree on `silver_trades`, plus 6 query views |
| 5 | [Validate OHLCV Correctness](steps/step-05-validate-ohlcv.md) | ⬜ Not Started | Compare ClickHouse MV output against existing Prefect/Spark OHLCV -- row counts, OHLC values, volume. Must match within floating-point tolerance |
| 6 | [Decommission Spark Streaming + Prefect](steps/step-06-decommission-spark-prefect.md) | ⬜ Not Started | Stop 5 streaming jobs + Spark Master + 2 Workers + Prefect Server + Prefect Agent = 9 services. Update docker-compose, measure resources |
| 7 | [Resource Validation Checkpoint](steps/step-07-resource-validation.md) | ⬜ Not Started | Run resource measurement script, verify CPU <= 22, RAM <= 26GB. Tag `v2-phase-4-complete`. This is the biggest single savings in the entire migration |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | Silver Layer Live | 1-3 | ⬜ Not Started | `silver_trades` populated via Kotlin processor, output matches Spark Silver |
| M2 | Gold Layer Live | 4-5 | ⬜ Not Started | 6 OHLCV timeframes computed in real-time via MVs, values match v1 output |
| M3 | Legacy Decommissioned | 6-7 | ⬜ Not Started | Spark + Prefect fully removed, resource budget within ~19 CPU |

---

## Success Criteria

- [ ] `silver_trades` table populated via Kotlin Silver Processor from Redpanda
- [ ] 6 OHLCV timeframes (1m, 5m, 15m, 30m, 1h, 1d) computed in real-time via AggregatingMergeTree MVs
- [ ] OHLCV values match v1 Prefect/Spark output within floating-point tolerance
- [ ] Spark Streaming fully decommissioned (5 streaming jobs + Spark Master + 2 Workers)
- [ ] Prefect fully decommissioned (Prefect Server + Prefect Agent)
- [ ] 9 services eliminated from docker-compose
- [ ] Resource budget within ~19 CPU / ~22GB RAM
- [ ] Git tag `v2-phase-4-complete` created

---

## Resource Impact

**Resource savings target: -13.5 CPU / -19.75GB (Spark) + -0.75 CPU / -768MB (Prefect)**

| Metric | Before (Phase 3) | After (Phase 4) | Delta |
|--------|-------------------|------------------|-------|
| CPU | ~37 | ~19 | -18 |
| RAM | ~46GB | ~22GB | -24GB |
| Services | 18 | 12 | -6 |

### Services Eliminated

| Service | CPU | RAM | Replaced By |
|---------|-----|-----|-------------|
| Spark Master | 1.0 | 1.0GB | -- |
| Spark Worker 1 | 4.0 | 8.0GB | -- |
| Spark Worker 2 | 4.0 | 8.0GB | -- |
| 5 Spark Streaming Jobs | 4.5 | 2.75GB | Kotlin Silver Processor (0.5 CPU / 512MB) |
| Prefect Server | 0.5 | 512MB | ClickHouse MVs (zero compute) |
| Prefect Agent | 0.25 | 256MB | ClickHouse MVs (zero compute) |

### Services Added

| Service | CPU | RAM | Purpose |
|---------|-----|-----|---------|
| Kotlin Silver Processor | 0.5 | 512MB | Redpanda -> validation -> ClickHouse |

---

## Architecture: Silver + Gold Layers

```
[bronze_trades]           ← From Phase 3 (ReplacingMergeTree)
    |
    v (Kotlin Silver Processor: Redpanda consumer -> validate -> batch insert)
[silver_trades]           ← MergeTree, Decimal128, Enum8, 30d TTL, partitioned by day
    |
    v (6 Materialized Views)
[gold_ohlcv_1m]           ← AggregatingMergeTree (1-minute candles)
[gold_ohlcv_5m]           ← AggregatingMergeTree (5-minute candles)
[gold_ohlcv_15m]          ← AggregatingMergeTree (15-minute candles)
[gold_ohlcv_30m]          ← AggregatingMergeTree (30-minute candles)
[gold_ohlcv_1h]           ← AggregatingMergeTree (1-hour candles)
[gold_ohlcv_1d]           ← AggregatingMergeTree (1-day candles)
    |
    v (6 Query Views)
[gold_ohlcv_1m_view]      ← SELECT with -Merge combinators for final OHLCV output
[gold_ohlcv_5m_view]      ← ...
[gold_ohlcv_15m_view]     ← ...
[gold_ohlcv_30m_view]     ← ...
[gold_ohlcv_1h_view]      ← ...
[gold_ohlcv_1d_view]      ← ...
```

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| OHLCV values don't match v1 output | **Critical** | Dual-run validation (Step 5) with row-by-row comparison. Do NOT proceed to Step 6 without passing |
| Kotlin Silver Processor performance | High | Batch inserts (1000 rows/batch), async Redpanda consumer, monitor with Micrometer |
| ClickHouse AggregatingMergeTree merge overhead | Medium | Monitor merge queue depth, tune `max_bytes_to_merge_at_max_space_in_pool` |
| Floating-point precision differences | Medium | Use Decimal128 in ClickHouse silver layer, define explicit tolerance (1e-8) for validation |
| Premature decommission | **Critical** | Step 5 validation is a hard gate -- no exceptions. Rollback to Phase 3 if validation fails |

---

## Dependencies

- Phase 3 complete (ClickHouse running with Raw + Bronze layers)
- Existing Spark Streaming and Prefect still running for dual-run comparison
- Kotlin build toolchain (Gradle + JDK 21)

---

## Rollback Procedure

1. Stop Kotlin Silver Processor
2. Restart Spark Streaming jobs + Prefect Server + Prefect Agent
3. Swap docker-compose back to `docker/v2-phase-3-clickhouse.yml`
4. ClickHouse silver/gold tables can remain (no harm) or be dropped

**Note:** Rollback from Step 6 (decommission) requires restarting Spark/Prefect services. Ensure Spark worker configs and Prefect flows are preserved before decommission.

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Phase 3: ClickHouse Foundation](../phase-3-clickhouse-foundation/README.md) -- Prerequisite phase (Raw + Bronze)
- [Phase 5: Cold Tier Restructure](../phase-5-cold-tier-restructure/README.md) -- Next phase
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After Phase 3 completion
