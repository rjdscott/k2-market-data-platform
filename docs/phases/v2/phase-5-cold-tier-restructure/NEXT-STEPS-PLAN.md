# Phase 5: Cold Tier Restructure — Next Steps Plan

**Date:** 2026-02-12
**Engineer:** Staff Data Engineer
**Current Status:** 🟢 Prototype Validated - Ready for Production Deployment
**Phase Progress:** Step 1 Complete (20%), Steps 2-5 Remaining (80%)
**Last Session:** Evening 2026-02-12 (See HANDOFF-2026-02-12-EVENING.md)

---

## Executive Summary

**What We Have:**
- ✅ ClickHouse 24.3 LTS (JDBC compatible)
- ✅ Spark 3.5.0 + Iceberg 1.5.0 operational
- ✅ Generic offload script working (`offload_generic.py`)
- ✅ Watermark management (exactly-once semantics validated)
- ✅ End-to-end testing (5 → 8 rows, zero duplicates)
- ✅ Prefect orchestration configured

**What We Need:**
- ⬜ Production-scale validation (10K+ rows)
- ⬜ Multi-table offload testing (9 tables)
- ⬜ Failure recovery testing
- ⬜ 15-minute production schedule deployment
- ⬜ Monitoring & alerting
- ⬜ Operational runbooks

**Key Decision:** Continue with Spark-based approach (ADR-014) using Prefect orchestration

---

## Current System State

### Working Components ✅

| Component | Status | Version | Notes |
|-----------|--------|---------|-------|
| ClickHouse | ✅ Operational | 24.3.18.7 LTS | Downgraded for JDBC compatibility |
| Spark | ✅ Operational | 3.5.0 | JDBC connectivity working |
| Iceberg | ✅ Operational | 1.5.0 | 9 tables created (test schema) |
| PostgreSQL | ✅ Operational | 15 | Watermark tracking working |
| Prefect | ✅ Configured | 2.x | Agent + flows ready |
| JDBC Driver | ✅ Working | 0.4.6 | ClickHouse JDBC compatibility validated |

### Test Results ✅

| Metric | Result | Status |
|--------|--------|--------|
| Initial Load | 5 rows → Iceberg | ✅ Success |
| Incremental Load | 3 rows (no duplicates) | ✅ Success |
| Watermark Update | Timestamp + Sequence | ✅ Success |
| Atomic Commits | All-or-nothing | ✅ Success |
| Data Integrity | 8 rows verified | ✅ Success |
| Exactly-Once | Zero duplicates | ✅ Success |

---

## Implementation Plan — Production Readiness (Next 3-5 Days)

### Priority 1: Production Validation (Day 1) 🔥

**Objective:** Validate offload pipeline with production-scale data

**Tasks:**
1. Generate 10K+ test records in ClickHouse bronze tables
2. Run full offload cycle (Bronze → Silver → Gold)
3. Validate row counts match across all layers
4. Test incremental offload with 5K additional records
5. Verify no duplicates, no data loss

**Deliverables:**
- `tests/integration/test_production_scale_offload.py` - Scale test
- `docs/testing/production-scale-validation-report.md` - Test report

**Acceptance Criteria:**
- [ ] Offload 10K+ rows successfully (<5 minutes)
- [ ] Incremental offload 5K additional rows (<3 minutes)
- [ ] Zero duplicates detected (100% data integrity)
- [ ] Watermark management working at scale
- [ ] Memory usage <4GB (within Spark limits)

**Time Estimate:** 4-6 hours

---

### Priority 2: Multi-Table Offload Testing (Day 1-2) 🔥

**Objective:** Validate concurrent offload of all 9 Iceberg tables

**Tasks:**
1. Create production Iceberg tables (replace test schemas)
2. Populate ClickHouse with realistic multi-exchange data
3. Configure Prefect flow for all 9 tables
4. Test parallel Bronze offload (Binance + Kraken)
5. Test sequential Silver → Gold offload
6. Validate table dependencies work correctly

**Deliverables:**
- `docker/iceberg/ddl/production/` - Production table DDL
- `docker/offload/flows/production_offload_flow.py` - Production flow
- Updated `PROGRESS.md` with multi-table results

**Acceptance Criteria:**
- [ ] All 9 tables offload successfully
- [ ] Bronze tables offload in parallel (<3 minutes each)
- [ ] Silver offload waits for Bronze completion
- [ ] Gold tables offload in parallel after Silver
- [ ] Total pipeline time <15 minutes
- [ ] Row counts match across all tables

**Time Estimate:** 6-8 hours

---

### Priority 3: Failure Recovery Testing (Day 2) 🔥

**Objective:** Validate exactly-once semantics under failure conditions

**Test Scenarios:**
1. **Network Interruption:** Kill ClickHouse mid-read, verify retry succeeds
2. **Spark Crash:** Kill Spark mid-write, verify no partial data in Iceberg
3. **Watermark Corruption:** Manually corrupt watermark, verify recovery
4. **Duplicate Run:** Run same offload twice, verify zero duplicates
5. **Late-Arriving Data:** Insert old data after offload, verify next cycle catches it

**Deliverables:**
- `tests/integration/test_failure_recovery.py` - Failure test suite
- `docs/testing/failure-recovery-report.md` - Test report

**Acceptance Criteria:**
- [ ] All 5 failure scenarios tested
- [ ] Zero data loss detected
- [ ] Zero duplicates detected
- [ ] Watermark recovery working
- [ ] Idempotent retry confirmed

**Time Estimate:** 4-6 hours

---

### Priority 4: Production Schedule Deployment (Day 3) 🟢

**Objective:** Deploy 15-minute production offload schedule

**Tasks:**
1. Configure Prefect deployment with 15-minute cron
2. Test schedule triggers correctly
3. Validate jobs complete within 15-minute window
4. Configure Prefect agent resource limits
5. Set up Prefect UI monitoring

**Deliverables:**
- `docker-compose.v2.yml` - Updated with Prefect schedule
- `docker/offload/flows/production_deployment.py` - Deployment config
- `docs/operations/prefect-schedule-config.md` - Configuration guide

**Acceptance Criteria:**
- [ ] Prefect deployment created: `iceberg-offload-15min`
- [ ] Schedule: `*/15 * * * *` (every 15 minutes)
- [ ] First scheduled run completes successfully
- [ ] Prefect UI shows execution history
- [ ] Resource limits enforced (2 CPU / 4GB)

**Time Estimate:** 3-4 hours

---

### Priority 5: Monitoring & Alerting (Day 3-4) 🟡

**Objective:** Add observability for offload pipeline

**Tasks:**
1. Add Prometheus metrics to offload scripts
2. Create Grafana dashboard for offload monitoring
3. Configure alerts for failure conditions
4. Set up Prefect notification webhooks
5. Test alert triggering (simulate failure)

**Deliverables:**
- `docker/offload/metrics.py` - Prometheus metrics
- `grafana/dashboards/iceberg-offload.json` - Grafana dashboard
- `docker/prometheus/alerts/offload-alerts.yml` - Alert rules
- `docs/operations/monitoring/offload-monitoring.md` - Monitoring guide

**Key Metrics:**
- `offload_rows_total{table, layer}` - Total rows offloaded
- `offload_duration_seconds{table}` - Offload job duration
- `offload_errors_total{table, error_type}` - Error counter
- `offload_lag_minutes{table}` - Cold tier freshness lag
- `watermark_timestamp{table}` - Last successful offload timestamp

**Alerts:**
- `OffloadFailure` - Job failed 3 consecutive times
- `OffloadLag` - Cold tier lag >30 minutes
- `WatermarkStale` - Watermark not updated >1 hour
- `DataMismatch` - Row count mismatch >1%

**Acceptance Criteria:**
- [ ] Grafana dashboard displays all metrics
- [ ] Alerts trigger correctly (tested)
- [ ] Prefect notifications working (Slack/email)
- [ ] Metrics exported to Prometheus
- [ ] Dashboard accessible at http://localhost:3000

**Time Estimate:** 6-8 hours

---

### Priority 6: Operational Runbooks (Day 4-5) 🟡

**Objective:** Document operational procedures

**Runbooks to Create:**
1. **Offload Failure Recovery** - How to recover from job failures
2. **Watermark Reset** - When/how to reset watermarks
3. **Iceberg Compaction** - Manual compaction procedures
4. **Data Consistency Audit** - Verify warm-cold consistency
5. **Performance Troubleshooting** - Slow offload jobs

**Deliverables:**
- `docs/operations/runbooks/iceberg-offload-failure.md`
- `docs/operations/runbooks/watermark-management.md`
- `docs/operations/runbooks/iceberg-compaction.md`
- `docs/operations/runbooks/data-consistency-audit.md`
- `docs/operations/runbooks/offload-performance.md`

**Acceptance Criteria:**
- [ ] Each runbook has: Symptoms, Diagnosis, Resolution
- [ ] All runbooks tested (dry-run procedures)
- [ ] Cross-references to monitoring dashboards
- [ ] Troubleshooting decision trees included

**Time Estimate:** 4-6 hours

---

### Priority 7: Performance Optimization (Day 5) 🟢

**Objective:** Optimize offload pipeline for production

**Tasks:**
1. Tune Spark executor memory/cores
2. Optimize ClickHouse JDBC batch sizes
3. Test Parquet compression levels (Zstd 1-3)
4. Validate partition pruning working
5. Benchmark cold query performance

**Deliverables:**
- `docs/operations/performance/offload-tuning.md`
- Updated `docker-compose.v2.yml` with optimized limits
- Performance benchmark report

**Targets:**
- Bronze offload: <3 minutes for 100K rows
- Silver offload: <5 minutes for 100K rows
- Gold offload: <2 minutes for 100K aggregates
- Cold query p99: <5 seconds
- Spark memory: <4GB (within limits)

**Acceptance Criteria:**
- [ ] All targets met or documented why not
- [ ] No OOM errors during load testing
- [ ] Cold queries performant (<5s p99)
- [ ] Compression ratio >10x (Zstd level 3)

**Time Estimate:** 4-6 hours

---

## Risk Assessment & Mitigation

### High Risk 🔴

| Risk | Impact | Mitigation |
|------|--------|------------|
| **ClickHouse TTL deletes before offload** | Data loss | 5-minute buffer window, monitor lag alerts |
| **Spark OOM during large batch** | Job failure | Memory limits (4GB), batch size tuning |
| **Watermark corruption** | Duplicates or gaps | PostgreSQL ACID guarantees, backup watermark table |

### Medium Risk 🟡

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Prefect agent crash** | Missed schedules | Docker restart policy, alerting on missed runs |
| **Network partition** | Incomplete offload | Idempotent retry, watermark not updated on failure |
| **Iceberg metadata corruption** | Query failures | Snapshot expiry (7 days), manual rollback possible |

### Low Risk 🟢

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Slow offload job** | Lag >15 minutes | Alert triggers, manual investigation, skip interval |
| **Disk space exhaustion (MinIO)** | Write failures | Disk usage alerts, retention policies |

---

## Resource Planning

### Current Resources (v2)

| Service | CPU | RAM | Notes |
|---------|-----|-----|-------|
| ClickHouse | 2.0 | 3.2GB | Existing (not counted) |
| Redpanda | 1.5 | 2.0GB | Existing (not counted) |
| Prefect Server | 0.5 | 512MB | Already running |
| Prefect Agent | 0.1 | 128MB | Already running |
| PostgreSQL | 0.5 | 512MB | Existing (not counted) |
| **Total (existing)** | **4.6** | **6.352GB** | **Base platform** |

### Additional Resources (Phase 5)

| Service | CPU | RAM | Purpose |
|---------|-----|-----|---------|
| Spark (offload) | 2.0 | 4.0GB | Offload jobs (15-minute bursts) |
| MinIO | 0.5 | 1.0GB | Iceberg object storage |
| **Total (Phase 5)** | **2.5** | **5.0GB** | **Cold tier** |

### Final v2 Resources

| Category | CPU | RAM |
|----------|-----|-----|
| Existing v2 | 4.6 | 6.4GB |
| Phase 5 Addition | 2.5 | 5.0GB |
| **Total v2 (Final)** | **7.1** | **11.4GB** |

**Comparison to v1:**
- v1: 35-40 CPU / 45-50GB RAM
- v2: 7.1 CPU / 11.4GB RAM
- **Savings: 82% CPU, 77% RAM**

---

## Success Criteria (Phase 5 Complete)

### Technical ✅

- [ ] All 9 Iceberg tables operational
- [ ] 15-minute offload schedule running
- [ ] Exactly-once semantics validated at scale
- [ ] Zero data loss over 7-day test period
- [ ] Zero duplicates detected
- [ ] Cold tier lag <20 minutes p99
- [ ] Offload jobs complete <10 minutes
- [ ] Cold query performance <5s p99

### Operational ✅

- [ ] Grafana dashboard operational
- [ ] Alerts configured and tested
- [ ] 5 operational runbooks created
- [ ] Prefect UI accessible
- [ ] Monitoring metrics collecting

### Documentation ✅

- [ ] Phase 5 completion summary
- [ ] All test reports created
- [ ] ADRs up to date
- [ ] Runbooks tested
- [ ] PROGRESS.md updated

---

## Timeline Summary

| Priority | Task | Days | Status |
|----------|------|------|--------|
| P1 | Production Validation | 0.5 | ⬜ Not Started |
| P2 | Multi-Table Testing | 1.0 | ⬜ Not Started |
| P3 | Failure Recovery | 0.5 | ⬜ Not Started |
| P4 | Production Schedule | 0.5 | ⬜ Not Started |
| P5 | Monitoring & Alerting | 1.0 | ⬜ Not Started |
| P6 | Operational Runbooks | 0.5 | ⬜ Not Started |
| P7 | Performance Optimization | 0.5 | ⬜ Not Started |
| **Total** | **Phase 5 Complete** | **4-5 days** | **20% Complete** |

---

## Next Session Quick Start

**If starting implementation tomorrow:**

1. **Read:**
   - `HANDOFF-2026-02-12-EVENING.md` (evening session context)
   - `ADR-014-spark-based-iceberg-offload.md` (architectural decision)
   - This document (NEXT-STEPS-PLAN.md)

2. **Validate Environment:**
   ```bash
   # Check services
   docker-compose -f docker-compose.v2.yml ps

   # Verify ClickHouse version
   docker exec k2-clickhouse clickhouse-client --query="SELECT version()"
   # Expected: 24.3.18.7

   # Test JDBC connectivity
   docker exec k2-spark-iceberg spark-submit \
     /home/iceberg/offload/test_jdbc_clean.py
   # Expected: SUCCESS
   ```

3. **Start with Priority 1:**
   - Generate 10K+ test records
   - Run production-scale validation
   - Document results

4. **Key Files:**
   - Offload script: `docker/offload/offload_generic.py`
   - Prefect flow: `docker/offload/flows/iceberg_offload_flow.py`
   - Watermark util: `docker/offload/watermark_pg.py`

---

## Technical Debt / Future Enhancements

### Phase 5 Scope (Must Have) ✅
- [x] Iceberg infrastructure operational
- [ ] 15-minute offload working
- [ ] Monitoring & alerting
- [ ] Operational runbooks

### Post-Phase 5 (Nice to Have) 🔮
- [ ] Iceberg REST catalog (upgrade from Hadoop catalog)
- [ ] Spark dynamic allocation (optimize resource usage)
- [ ] Data quality rules (automated validation)
- [ ] Disaster recovery testing (backup/restore)
- [ ] Query federation (ClickHouse Iceberg table function)
- [ ] Compaction automation (daily Spark jobs)
- [ ] Snapshot expiry automation (7-day retention)

---

## Key Decisions from Evening Session

| Decision | Rationale | Status |
|----------|-----------|--------|
| **ClickHouse 24.3 LTS** | JDBC compatibility with Spark | ✅ Implemented |
| **Spark-based offload** | Leverage existing Iceberg integration | ✅ Accepted (ADR-014) |
| **Prefect orchestration** | Better observability vs cron | ✅ Configured |
| **15-minute intervals** | Faster freshness, smaller batches | ⬜ To be deployed |
| **Exactly-once semantics** | Watermark + Iceberg atomic commits | ✅ Validated |

---

## Contact & References

**Documentation:**
- Evening Session: `HANDOFF-2026-02-12-EVENING.md`
- Test Report: `docs/testing/offload-pipeline-test-report-2026-02-12.md`
- Architecture: `PHASE-5-IMPLEMENTATION-PLAN.md`
- Decision: `docs/decisions/platform-v2/ADR-014-spark-based-iceberg-offload.md`

**Key Commits:**
- `e796555` - Complete offload pipeline implementation
- `14a77b6` - Update progress tracking for prototype validation

**Branch:** `phase-5-prefect-iceberg-offload`

---

**Last Updated:** 2026-02-12
**Status:** 🟢 Ready for Production Validation
**Next Action:** Start Priority 1 (Production Validation)
**Estimated Completion:** 2026-02-17 (4-5 days)

---

*This plan follows staff-level rigor: clear priorities, measurable success criteria, risk mitigation, and pragmatic timelines.*
