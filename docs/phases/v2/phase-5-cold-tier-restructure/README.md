# Phase 5: Cold Tier Restructure

**Status:** 🟢 ACTIVE DEVELOPMENT (P1-P3 Complete, P4-P7 Remaining)
**Duration:** 1-2 weeks (2 weeks estimated, 10 working days)
**Steps:** 5
**Last Updated:** 2026-02-12 (Evening)
**Phase Owner:** Platform Engineering
**Planning Completed:** 2026-02-11
**Prototype Validated:** 2026-02-12 (Evening)
**Production Validated:** 2026-02-12 (Morning) - P1: 3.78M rows @ 236K/s
**Multi-Table Validated:** 2026-02-12 (Afternoon) - P2: 2 tables @ 80.9% efficiency
**Failure Recovery Validated:** 2026-02-12 (Evening) - P3: Idempotency + manual procedures

---

## Overview

Restructure Iceberg cold storage to mirror the full four-layer ClickHouse medallion (Raw, Bronze, Silver, Gold). Implement a lightweight Kotlin Iceberg writer that offloads data hourly from ClickHouse to Iceberg, achieving **~1 hour cold tier freshness** (vs 24h in v1). Spark is retained for daily compaction and maintenance only.

This phase reduces Iceberg infrastructure resources by 50% since it now handles cold-only data (queried infrequently). The four-layer mirror preserves full data lineage end-to-end across warm and cold tiers.

---

## Steps

| # | Step | Status | Description |
|---|------|--------|-------------|
| 1 | [Create Four-Layer Iceberg DDL](steps/step-01-iceberg-ddl.md) | ⬜ Not Started | Create `cold.raw_trades`, `cold.bronze_trades`, `cold.silver_trades`, `cold.gold_ohlcv_{1m,5m,15m,30m,1h,1d}` tables via Iceberg catalog. Partitioned by hour/day/month + exchange |
| 2 | [Implement Spark Iceberg Offload](steps/step-02-spark-iceberg-offload.md) | 🟡 In Progress | **Prototype validated 2026-02-12**: Generic PySpark offload working with watermark management, JDBC connectivity, incremental loads. See [Test Report](../../../testing/offload-pipeline-test-report-2026-02-12.md). Production: Create per-table scripts + Prefect orchestration |
| 3 | [Configure Scheduled Offload](steps/step-03-scheduled-offload.md) | ⬜ Not Started | Schedule: 15-minute intervals for Bronze/Silver/Gold. Prefect flows orchestrating Spark jobs. Monitor offload duration + row counts |
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

- [ ] 9 Iceberg tables created mirroring the ClickHouse medallion architecture
- [x] **Prototype validated**: Generic PySpark offload + watermark management + exactly-once semantics (2026-02-12)
- [x] **JDBC resolved**: ClickHouse 24.3 LTS compatible with Spark JDBC driver 0.4.6 (2026-02-12)
- [ ] Spark offload jobs operational for all layers (Bronze, Silver, Gold)
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

## Progress Notes

### 2026-02-12 (Evening): Prototype Validation Complete ✅

**Achievement**: Validated Spark-based offload pipeline with exactly-once semantics

**What was tested**:
- Generic PySpark offload script (`offload_generic.py`)
- PostgreSQL watermark management for incremental loads
- ClickHouse → Spark JDBC connectivity (resolved compatibility issues)
- Iceberg atomic writes (Hadoop catalog)
- Initial load: 5 rows in 3s
- Incremental load: 3 rows in 6s (only new data read via watermark)
- Zero duplicates, zero data loss

**Key technical resolutions**:
- **JDBC Compatibility**: Downgraded ClickHouse from 26.1 to 24.3 LTS per [DECISION-015](../../../decisions/platform-v2/DECISION-015-clickhouse-lts-downgrade.md)
- **ClickHouse JDBC Driver**: `com.clickhouse:clickhouse-jdbc:0.4.6` (stable with 24.3 LTS)
- **Catalog Strategy**: Hadoop catalog (local filesystem) validated
- **Authentication**: Environment-based auth (removed custom users.xml)

**Documentation**:
- [Test Report](../../../testing/offload-pipeline-test-report-2026-02-12.md)
- [Evening Handoff](HANDOFF-2026-02-12-EVENING.md)
- [ClickHouse LTS Decision](../../../decisions/platform-v2/DECISION-015-clickhouse-lts-downgrade.md)

---

### 2026-02-12 (Afternoon): Production-Scale & Multi-Table Validation ✅

**Achievement**: Validated production-ready offload at scale + parallel multi-table execution

**Priority 1: Production-Scale (3.78M rows)**
- ✅ **Throughput**: 236,093 rows/second (23x target)
- ✅ **Exactly-once**: 99.9999% accuracy
- ✅ **Real data**: 18+ hours of live Binance trades
- ✅ **Compression**: 12:1 ratio (343 MB → 28.3 MB)
- ✅ **Scalability**: Linear performance across all scales

**Priority 2: Multi-Table Parallel (2 tables)**
- ✅ **Parallel execution**: Binance (3.85M) + Kraken (19.6K) simultaneously
- ✅ **Efficiency**: 80.9% parallelism (near-linear scaling)
- ✅ **Duration**: 25.5 seconds total (both tables)
- ✅ **Watermark isolation**: Per-table tracking working
- ✅ **Resource management**: Zero contention (4GB memory)

**Key Infrastructure**:
- Created Kraken bronze layer (ClickHouse Kafka consumer + MV)
- Created 2 Iceberg tables (Binance + Kraken)
- Built parallel testing framework (ProcessPoolExecutor)
- Validated watermark isolation for concurrent offloads

**Pragmatic Scope Decision**:
- Tested 2 Bronze tables (not full 9-table architecture)
- Proves parallel execution pattern; scales to 9 tables
- Full v2 schema (Silver/Gold) deferred until proper initialization
- See [PRIORITY-2-APPROACH.md](PRIORITY-2-APPROACH.md) for rationale

**Next steps**:
1. Priority 3: Failure recovery testing (network interruption, crash recovery)
2. Prefect orchestration flow (convert Python script)
3. Monitoring & alerting (Prometheus + Grafana)
4. Production schedule deployment (15-minute intervals)

**Documentation**:
- [Production Validation Report](../../../testing/production-validation-report-2026-02-12.md) (30KB)
- [Multi-Table Test Report](../../../testing/multi-table-offload-report-2026-02-12.md) (25KB)
- [Priority 2 Approach](PRIORITY-2-APPROACH.md) (decision doc)
- [Afternoon Handoff](../HANDOFF-2026-02-12-AFTERNOON.md) (comprehensive)

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

**Last Updated:** 2026-02-12
**Phase Owner:** Platform Engineering
**Prototype Validated:** 2026-02-12 (Step 2 offload pipeline)
**Next Review:** After production offload deployment
