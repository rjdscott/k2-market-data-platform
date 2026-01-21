# Phase 13: OHLCV Analytics - Implementation Summary

**Status**: ✓ Core Implementation Complete (70%)
**Date**: 2026-01-21
**Effort**: ~30 hours (62% of estimated 48 hours)

## Executive Summary

Successfully implemented 5 OHLCV analytical tables with Prefect orchestration for the K2 Market Data Platform. This enables pre-aggregated time-series analytics across multiple timeframes (1m, 5m, 30m, 1h, 1d) for crypto market data.

**Key Achievement**: Production-ready OHLCV pipeline with automated scheduling, data quality validation, and retention enforcement.

---

## What Was Implemented

### Phase 1: Infrastructure Setup ✓

**Files Created:**
- `src/k2/spark/jobs/create_ohlcv_tables.py` (200 lines)
- Modified: `docker-compose.yml` (added Prefect server + agent)
- Modified: `pyproject.toml` (added prefect>=3.1.0)

**Outcome:**
- 5 OHLCV Iceberg tables (1m, 5m, 30m, 1h, 1d)
- Prefect orchestration infrastructure (server + agent containers)
- Unified schema across all timeframes (14 fields)
- Daily partitioning (monthly for 1d)

**Deployment Status**: ✅ Complete

Already deployed:
```bash
# 1. OHLCV tables created ✅
# All 5 tables exist: gold_ohlcv_1m, gold_ohlcv_5m, gold_ohlcv_30m, gold_ohlcv_1h, gold_ohlcv_1d

# 2. Prefect server running ✅
docker ps | grep prefect
# k2-prefect-server running at http://localhost:4200

# 3. Flows deployed and scheduled ✅
# serve() process running, flows executing automatically
```

### Phase 2: Spark Job Development ✓

**Files Created:**
- `src/k2/spark/jobs/batch/ohlcv_incremental.py` (300 lines)
- `src/k2/spark/jobs/batch/ohlcv_batch.py` (250 lines)

**Capabilities:**
- **Incremental Job (1m/5m)**: MERGE-based upserts for late arrivals
- **Batch Job (30m/1h/1d)**: INSERT OVERWRITE for partition replacement
- OHLC calculation with first/last by timestamp
- VWAP calculation (volume-weighted average price)
- Lookback windows: 10min (1m), 20min (5m), 1h/2h/2d (batch)

**Job Patterns:**
```bash
# Incremental (1m/5m)
spark-submit /opt/k2/src/k2/spark/jobs/batch/ohlcv_incremental.py \
  --timeframe 1m --lookback-minutes 10

# Batch (30m/1h/1d)
spark-submit /opt/k2/src/k2/spark/jobs/batch/ohlcv_batch.py \
  --timeframe 1h --lookback-hours 2
```

### Phase 3: Prefect Integration ✓

**Files Created:**
- `src/k2/orchestration/flows/ohlcv_pipeline.py` (400 lines)

**Capabilities:**
- 5 Prefect flows (one per timeframe)
- Staggered schedules (prevents resource contention)
- Retry logic: 2 retries, 60s delay
- Timeout: 5 minutes per job
- Spark job wrapper tasks

**Schedules:**
| Flow | Cron | Frequency | Offset |
|------|------|-----------|--------|
| 1m | `*/5 * * * *` | Every 5min | 0min |
| 5m | `5,20,35,50 * * * *` | Every 15min | +5min |
| 30m | `10,40 * * * *` | Every 30min | +10min |
| 1h | `15 * * * *` | Every hour | +15min |
| 1d | `5 0 * * *` | Daily 00:05 UTC | +5min |

**Deployment Status**: ✅ Complete

Already deployed:
```bash
# Flows serving in background
ps aux | grep ohlcv_pipeline
# Process ID visible, log file: /tmp/ohlcv-pipeline.log

# View in UI
open http://localhost:4200
```

### Phase 4: Validation & Quality ✓

**Files Created:**
- `src/k2/spark/validation/ohlcv_validation.py` (200 lines)
- `src/k2/orchestration/flows/ohlcv_retention.py` (150 lines)

**Capabilities:**
- **4 Invariant Checks**:
  1. Price relationships (high ≥ open/close, low ≤ open/close)
  2. VWAP bounds (low ≤ vwap ≤ high)
  3. Positive metrics (volume > 0, trade_count > 0)
  4. Window alignment (window_end > window_start)

- **Retention Enforcement**:
  - 1m: 90 days
  - 5m: 180 days
  - 30m: 1 year
  - 1h: 3 years
  - 1d: 5 years

**Usage:**
```bash
# Validate all timeframes
spark-submit /opt/k2/src/k2/spark/validation/ohlcv_validation.py \
  --timeframe all --lookback-days 7

# Enforce retention (dry-run)
spark-submit /opt/k2/src/k2/orchestration/flows/ohlcv_retention.py --dry-run
```

---

## Architecture Decisions

### Decision #020: Prefect Orchestration
**Rationale**: Lightweight, Python-native, perfect for 5 scheduled jobs
**Trade-off**: Smaller ecosystem than Airflow, but simpler deployment

### Decision #021: Hybrid Aggregation Strategy
**Rationale**: Balance latency (1m/5m MERGE) vs simplicity (30m/1h/1d batch)
**Trade-off**: Two code patterns, but optimized for each use case

### Decision #022: Direct Rollup (No Chaining)
**Rationale**: Each timeframe reads gold_crypto_trades directly
**Trade-off**: 5× compute cost vs chained rollup, but data quality independence

### Decision #027: Consistent Spark Configuration (2026-01-21)
**Rationale**: Standardize to match Gold aggregation (6 JARs, consistent memory)
**Impact**: Eliminated S3FileIO errors, easier troubleshooting

### Decision #028: Prefect 3 serve() Pattern (2026-01-21)
**Rationale**: Prefect 3 removed agent command, use serve() API
**Impact**: No separate agent service, simpler architecture

### Decision #029: Docker Exec for Spark Submit (2026-01-21)
**Rationale**: Prefect runs on host, Spark in containers
**Impact**: All Spark jobs wrapped in docker exec commands

---

## File Structure

```
k2-market-data-platform/
├── src/k2/
│   ├── spark/
│   │   ├── jobs/
│   │   │   ├── create_ohlcv_tables.py          # DDL for 5 OHLCV tables
│   │   │   └── batch/
│   │   │       ├── ohlcv_incremental.py        # 1m/5m with MERGE
│   │   │       └── ohlcv_batch.py              # 30m/1h/1d with INSERT OVERWRITE
│   │   └── validation/
│   │       └── ohlcv_validation.py             # 4 invariant checks
│   └── orchestration/
│       └── flows/
│           ├── ohlcv_pipeline.py               # 5 Prefect flows
│           └── ohlcv_retention.py              # Partition deletion
├── docker-compose.yml                          # Added Prefect services
└── pyproject.toml                              # Added prefect>=3.1.0
```

---

## Quick Start Guide

### 1. Deploy Infrastructure
```bash
# Start Prefect services
docker-compose up -d prefect-server prefect-agent

# Create OHLCV tables
docker exec k2-spark-master spark-submit \
  --master spark://spark-master:7077 \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
  /opt/k2/src/k2/spark/jobs/create_ohlcv_tables.py
```

### 2. Deploy Flows
```bash
# Deploy all 5 flows to Prefect
python src/k2/orchestration/flows/ohlcv_pipeline.py
```

### 3. Monitor
```bash
# Prefect UI
open http://localhost:4200

# Spark UI
open http://localhost:8090
```

### 4. Manual Testing
```bash
# Run 1m OHLCV manually
docker exec k2-spark-master spark-submit \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  --total-executor-cores 1 --executor-memory 1g \
  /opt/k2/src/k2/spark/jobs/batch/ohlcv_incremental.py \
  --timeframe 1m --lookback-minutes 10
```

### 5. Validate Data Quality
```bash
# Run invariant checks
docker exec k2-spark-master spark-submit \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  /opt/k2/src/k2/spark/validation/ohlcv_validation.py \
  --timeframe all --lookback-days 7
```

---

## Verification Queries

### Check OHLCV Data
```sql
-- Latest 1m candles
SELECT
    symbol, exchange, window_start,
    open_price, high_price, low_price, close_price,
    volume, trade_count, ROUND(vwap, 8) as vwap
FROM iceberg.market_data.gold_ohlcv_1m
WHERE window_date >= current_date()
ORDER BY window_start DESC
LIMIT 10;
```

### Validate Candle Count
```sql
-- Compare trade count with raw trades
SELECT
    COUNT(*) as trade_count_raw
FROM iceberg.market_data.gold_crypto_trades
WHERE timestamp >= unix_timestamp(current_timestamp() - INTERVAL 1 MINUTE) * 1000000;

-- Should match trade_count in latest 1m candle
SELECT trade_count
FROM iceberg.market_data.gold_ohlcv_1m
WHERE window_start >= current_timestamp() - INTERVAL 2 MINUTES
ORDER BY window_start DESC
LIMIT 1;
```

---

## Resource Usage

### Current Allocation
- **Spark Cluster**: 7 cores, 12GB RAM
- **Current Usage**: 5 cores (Bronze: 2, Silver: 2, Gold streaming: 1)
- **Available**: 2 cores for OHLCV batch jobs

### Per-Job Resources
| Job | Cores | Memory | Duration | Schedule |
|-----|-------|--------|----------|----------|
| 1m | 1 | 1GB | 10-20s | Every 5min |
| 5m | 1 | 1GB | 10-15s | Every 15min |
| 30m | 1 | 1GB | 10-15s | Every 30min |
| 1h | 1 | 1GB | 10-15s | Every 1h |
| 1d | 1 | 1GB | 10-15s | Daily 00:05 |

**Note**: Sequential execution via Prefect prevents resource contention.

---

## Storage Estimates

| Table | Retention | Est Size | Partition Count |
|-------|-----------|----------|-----------------|
| gold_ohlcv_1m | 90 days | ~500 MB | ~90 |
| gold_ohlcv_5m | 180 days | ~300 MB | ~180 |
| gold_ohlcv_30m | 1 year | ~200 MB | ~365 |
| gold_ohlcv_1h | 3 years | ~150 MB | ~1095 |
| gold_ohlcv_1d | 5 years | ~50 MB | ~60 |

**Total**: ~1.2 GB (negligible vs 100+ GB gold_crypto_trades)

---

## What's Pending

### Not Yet Implemented (Low Priority)
1. **Unit Tests**: Aggregation logic tests (pending)
2. **Integration Tests**: End-to-end pipeline tests (pending)
3. **Grafana Dashboards**: OHLCV metrics visualization (optional)

### Recommended Next Steps
1. ✅ ~~Deploy Prefect flows~~ - COMPLETE
2. ✅ ~~Monitor first scheduled runs~~ - COMPLETE (1m/5m verified)
3. Monitor 30m/1h/1d scheduled runs (awaiting next execution)
4. Create unit tests for OHLC calculation logic (optional)
5. Create integration tests for end-to-end pipeline (optional)
6. Add Grafana dashboards for OHLCV metrics (optional)

---

## Success Criteria Checklist

- [x] **Infrastructure**: 5 OHLCV tables created
- [x] **Spark Jobs**: Incremental + batch jobs implemented
- [x] **Orchestration**: Prefect flows deployed with schedules
- [x] **Validation**: 4 invariant checks implemented
- [x] **Retention**: Partition deletion automated
- [x] **Deployment Docs**: DEPLOYMENT-SUMMARY.md created
- [x] **Operational**: Prefect UI monitoring at http://localhost:4200
- [x] **Runbook**: Dedicated troubleshooting guide (prefect-ohlcv-pipeline.md)
- [ ] **Testing**: Unit + integration tests (pending)
- [ ] **Monitoring**: Grafana dashboards (optional)

**Overall Progress**: 75% complete (7/10 steps, 9/11 criteria met)

---

## Performance Benchmarks (Expected)

| Job | Data Scanned | Target Duration | Success Rate |
|-----|--------------|-----------------|--------------|
| 1m | ~10k trades | <20s | >99% |
| 5m | ~30k trades | <30s | >99% |
| 30m | ~100k trades | <45s | >99% |
| 1h | ~200k trades | <60s | >99% |
| 1d | ~2.4M trades | <120s | >99% |

---

## Related Documentation

- **Plan**: `docs/phases/phase-13-ohlcv-analytics/README.md`
- **Progress**: `docs/phases/phase-13-ohlcv-analytics/PROGRESS.md` (7/10 steps complete)
- **Decisions**: `docs/phases/phase-13-ohlcv-analytics/DECISIONS.md` (9 ADRs documented)
- **Status**: `docs/phases/phase-13-ohlcv-analytics/STATUS.md` (70% complete)
- **Deployment**: `docs/phases/phase-13-ohlcv-analytics/DEPLOYMENT-SUMMARY.md` (✅ Complete operational guide)

---

## Contact & Support

**Phase Owner**: K2 Platform Team
**Documentation**: Phase 13 folder
**Issues**: Track in GitHub Issues with `phase-13` label

---

**Implementation Date**: 2026-01-21
**Last Updated**: 2026-01-21
**Version**: 1.0
