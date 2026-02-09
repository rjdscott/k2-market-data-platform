# Phase 5: Cold Tier Restructure

**Status:** ⬜ NOT STARTED
**Duration:** 1-2 weeks
**Steps:** 5
**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering

---

## Overview

Restructure Iceberg cold storage to mirror the full four-layer ClickHouse medallion (Raw, Bronze, Silver, Gold). Implement a lightweight Kotlin Iceberg writer that offloads data hourly from ClickHouse to Iceberg, achieving **~1 hour cold tier freshness** (vs 24h in v1). Spark is retained for daily compaction and maintenance only.

This phase reduces Iceberg infrastructure resources by 50% since it now handles cold-only data (queried infrequently). The four-layer mirror preserves full data lineage end-to-end across warm and cold tiers.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Create Four-Layer Iceberg DDL](steps/step-01-iceberg-ddl.md) | ⬜ Not Started | Create `cold.raw_trades`, `cold.bronze_trades`, `cold.silver_trades`, `cold.gold_ohlcv_{1m,5m,15m,30m,1h,1d}` tables via Iceberg REST catalog. Partitioned by hour/day/month + exchange |
| 2 | [Implement Kotlin Iceberg Writer](steps/step-02-kotlin-iceberg-writer.md) | ⬜ Not Started | Hourly offload using Apache Iceberg Java SDK. SELECT from ClickHouse (last hour), write Parquet + Zstd to MinIO, update Iceberg catalog. Embedded in API service or standalone |
| 3 | [Configure Hourly Offload Schedule](steps/step-03-hourly-offload.md) | ⬜ Not Started | Schedule: :05 raw, :06 bronze, :07 silver, :09 gold. Spring @Scheduled or Kotlin coroutine timer. Monitor offload duration + row counts |
| 4 | [Configure Spark Daily Maintenance](steps/step-04-spark-daily-maintenance.md) | ⬜ Not Started | 02:00 UTC compaction (merge small Parquet files), 02:20 snapshot expiry (7d), 02:30 data quality audit (row count verification across layers). Spark exits after completion |
| 5 | [Validate Warm-Cold Consistency](steps/step-05-validate-consistency.md) | ⬜ Not Started | Verify row counts match between ClickHouse and Iceberg at each layer. Test ClickHouse federated queries across warm+cold. Reduce Iceberg infra resources to 50%. Tag `v2-phase-5-complete` |

---

## Milestones

| Milestone | Name | Steps | Status | Gate Criteria |
|-----------|------|-------|--------|---------------|
| M1 | Iceberg Schema Ready | 1 | ⬜ Not Started | All 9 Iceberg tables created, partitioning verified |
| M2 | Hourly Offload Running | 2-3 | ⬜ Not Started | Data flowing hourly from ClickHouse to Iceberg across all 4 layers |
| M3 | Maintenance + Validation | 4-5 | ⬜ Not Started | Spark daily jobs running, warm-cold consistency verified, resources reduced |

---

## Success Criteria

- [ ] 9 Iceberg tables created mirroring the ClickHouse four-layer medallion
- [ ] Kotlin Iceberg writer offloading data hourly (~6 min/hour total)
- [ ] Row counts match between ClickHouse and Iceberg at each layer
- [ ] ClickHouse federated queries working across warm + cold tiers
- [ ] Spark daily compaction + snapshot expiry running at 02:00 UTC
- [ ] Iceberg infrastructure resources reduced to 1.5 CPU / 2GB (50% of v1)
- [ ] Git tag `v2-phase-5-complete` created

---

## Resource Impact

**Net savings: -1.5 CPU / -2GB** (Iceberg infrastructure reduced to cold-only role)

| Metric | Before (Phase 4) | After (Phase 5) | Delta |
|--------|-------------------|------------------|-------|
| CPU | ~19 | ~17.5 | -1.5 |
| RAM | ~22GB | ~20GB | -2GB |
| Services | 12 | 12 | 0 |

### Iceberg Infrastructure Reduction

| Component | v1 (All Layers) | v2 (Cold Only) | Savings |
|-----------|-----------------|----------------|---------|
| MinIO | 1.0 CPU / 2GB | 0.5 CPU / 1GB | 50% |
| PostgreSQL (catalog) | 1.0 CPU / 1GB | 0.5 CPU / 512MB | 50% |
| Iceberg REST | 1.0 CPU / 1GB | 0.5 CPU / 512MB | 50% |
| **Total** | **3.0 CPU / 4GB** | **1.5 CPU / 2GB** | **50%** |

---

## Architecture: Four-Layer Cold Tier Mirror

```
ClickHouse (Warm: 0-30 days)              Iceberg/MinIO (Cold: 30+ days)
─────────────────────────────              ──────────────────────────────
trades_raw        ──(hourly)──→    cold.raw_trades
bronze_trades     ──(hourly)──→    cold.bronze_trades
silver_trades     ──(hourly)──→    cold.silver_trades
gold_ohlcv_1m     ──(hourly)──→    cold.gold_ohlcv_1m
gold_ohlcv_5m     ──(hourly)──→    cold.gold_ohlcv_5m
gold_ohlcv_15m    ──(hourly)──→    cold.gold_ohlcv_15m
gold_ohlcv_30m    ──(hourly)──→    cold.gold_ohlcv_30m
gold_ohlcv_1h     ──(hourly)──→    cold.gold_ohlcv_1h
gold_ohlcv_1d     ──(hourly)──→    cold.gold_ohlcv_1d

Daily (02:00 UTC): Spark compaction + snapshot expiry + audit
```

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Iceberg Java SDK learning curve | Medium | Well-documented Apache library; Kotlin interop is seamless |
| Hourly offload missing data | High | Verify row counts after each offload; alert on mismatch; ClickHouse TTL provides buffer |
| Small file problem in MinIO | Medium | Spark daily compaction merges hourly files into optimal sizes |
| ClickHouse federated Iceberg query performance | Low | Cold queries are infrequent; acceptable latency for historical analysis |

---

## Dependencies

- Phase 4 complete (Silver + Gold layers in ClickHouse, Spark Streaming decommissioned)
- Existing Iceberg infrastructure (MinIO, PostgreSQL, Iceberg REST) running
- Kotlin build toolchain available (from Phase 4 Silver Processor)

---

## Rollback Procedure

1. Disable hourly offload schedule
2. Remove Kotlin Iceberg writer from docker-compose
3. Restore Iceberg infrastructure resources to v1 levels
4. Swap docker-compose to `docker/v2-phase-4-pipeline.yml`
5. Existing v1 Iceberg tables remain untouched (v2 uses `cold.*` prefix)

---

## Related Documentation

- [Phase Map](../README.md) -- Full v2 migration overview
- [Phase 4: Streaming Pipeline](../phase-4-streaming-pipeline/README.md) -- Prerequisite phase
- [Phase 6: Kotlin Feed Handlers](../phase-6-kotlin-feed-handlers/README.md) -- Next phase
- [ADR-007: Iceberg Cold Storage](../../../decisions/platform-v2/ADR-007-iceberg-cold-storage.md) -- Four-layer cold tier design
- [ADR-006: Spark Batch Only](../../../decisions/platform-v2/ADR-006-spark-batch-only.md) -- Two-tier batch strategy
- [Infrastructure Versioning](../INFRASTRUCTURE-VERSIONING.md) -- Docker Compose rollback strategy

---

**Last Updated:** 2026-02-09
**Phase Owner:** Platform Engineering
**Next Review:** After Phase 4 completion
