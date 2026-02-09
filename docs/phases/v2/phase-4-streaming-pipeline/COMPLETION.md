# Phase 4 Completion: OHLCV Gold Layer Expansion

**Date**: 2026-02-09
**Status**: ✅ Complete
**Duration**: ~1.5 hours

---

## Context

This was a **greenfield v2 implementation**, not a migration. Unlike the original Phase 4 plan (which focused on decommissioning Spark Streaming/Prefect), we enhanced the already-operational Gold layer with additional OHLCV timeframes and monitoring.

---

## What Was Built

### 1. Additional OHLCV Timeframes ✅

Added 5 new timeframes beyond the existing 1-minute candles:
- **5-minute** candles (`ohlcv_5m`)
- **15-minute** candles (`ohlcv_15m`)
- **30-minute** candles (`ohlcv_30m`)
- **1-hour** candles (`ohlcv_1h`)
- **1-day** candles (`ohlcv_1d`)

Each timeframe includes:
- `SummingMergeTree` table for efficient aggregation
- Materialized view from `silver_trades`
- Query helper view for easy access

### 2. ClickHouse Monitoring Dashboard ✅

Created Grafana dashboard (`clickhouse-overview.json`) with:
- Query rate (total and failed queries/sec)
- Memory usage gauge
- Insert rate tracking
- Background merge activity
- 10-second auto-refresh

---

## Architecture

```
Silver Trades (validated)
    ↓ (6 Materialized Views)
    ├─→ ohlcv_1m   (1-minute candles)
    ├─→ ohlcv_5m   (5-minute candles)
    ├─→ ohlcv_15m  (15-minute candles)
    ├─→ ohlcv_30m  (30-minute candles)
    ├─→ ohlcv_1h   (1-hour candles)
    └─→ ohlcv_1d   (1-day candles)
```

All OHLCV aggregations happen **in ClickHouse** via materialized views - zero external compute required.

---

## Performance Metrics

### Data Flow (as of 12:31 UTC)

| Layer | Rows | Throughput | Status |
|-------|------|------------|--------|
| Bronze | 704,002 | 92 trades/sec | ✅ Healthy |
| Silver | 704,002 | 92 trades/sec | ✅ Healthy |
| Gold (1m) | 67 candles | Real-time | ✅ Healthy |
| Gold (5m) | 11 candles | Real-time | ✅ Healthy |
| Gold (15m) | 9 candles | Real-time | ✅ Healthy |
| Gold (30m) | 9 candles | Real-time | ✅ Healthy |
| Gold (1h) | 9 candles | Real-time | ✅ Healthy |
| Gold (1d) | 9 candles | Real-time | ✅ Healthy |

### Resource Usage

| Service | CPU % | Memory | Status |
|---------|-------|--------|--------|
| Feed Handler | 3.4% | 144 MB | ✅ Healthy |
| ClickHouse | 14.2% | 1.9 GB | ✅ Healthy |
| Redpanda | 6.1% | 185 MB | ✅ Healthy |
| Prometheus | 0.0% | 90 MB | ✅ Healthy |
| Grafana | 2.2% | 109 MB | ✅ Healthy |
| **TOTAL** | **~27%** | **~2.5 GB** | ✅ **Excellent** |

**Budget**: 16 CPU / 40GB → **Usage**: 27% CPU / 6% RAM

---

## Technical Decisions

### Decision: Use SummingMergeTree for OHLCV

**Rationale**:
- Simple aggregation pattern (sum volumes, min/max prices)
- Automatic background merging reduces storage
- No need for complex AggregatingMergeTree State/Merge functions

**Trade-off**: Can't use complex aggregate functions, but OHLCV doesn't need them

### Decision: All Timeframes from Silver (Not Cascading)

**Rationale**:
- Each MV reads directly from `silver_trades`
- Simpler than cascading (1m → 5m → 15m)
- ClickHouse handles aggregation efficiently

**Trade-off**: Slight duplication of work, but performance is excellent

### Decision: Skip Kraken for Phase 4

**Rationale**:
- Focus on completing Gold layer and monitoring first
- Kraken can be added in Phase 5 or 6 as needed
- Pragmatic approach: "make it work" before "make it complete"

---

## Validation

✅ All 6 OHLCV timeframes generating candles
✅ OHLCV values look correct (proper OHLC, increasing volumes)
✅ Bronze → Silver → Gold data flow working
✅ 704k+ trades ingested with zero errors
✅ Grafana dashboard accessible and showing metrics
✅ Resource usage well under budget (27% CPU / 6% RAM)
✅ System stable after 2+ hours of operation

---

## Files Changed

- `docker/clickhouse/schema/04-ohlcv-additional-timeframes.sql` - New timeframes
- `docker/grafana/dashboards/clickhouse-overview.json` - Monitoring dashboard
- `docs/phases/v2/README.md` - Updated Phase 3 status

---

## Deferred to Future Phases

1. **Kraken Exchange Support** - Extend to second exchange (Phase 5/6)
2. **Advanced Monitoring** - Alerting rules, consumer lag dashboards (Phase 7)
3. **Performance Optimization** - Indexes, partition tuning (As needed)

---

## Lessons Learned

1. **Adapt Plans to Reality**: Original Phase 4 plan assumed migration; greenfield implementation required different approach
2. **SummingMergeTree is Simple**: For basic aggregations, it's much simpler than AggregatingMergeTree
3. **ClickHouse MVs are Powerful**: All aggregations in-database, zero external compute
4. **Pragmatic Over Perfect**: Skip Kraken for now, focus on completing core functionality

---

## Next Steps

**Phase 5 Options**:
1. Add Kraken exchange support (multi-exchange validation)
2. Build simple REST API for OHLCV queries
3. Add alerting rules to Prometheus
4. Performance testing at scale (more symbols, longer duration)

---

**Phase 4 Status**: ✅ **COMPLETE**
**System State**: Production-ready for single exchange (Binance) with 6 OHLCV timeframes
**Ready for**: Phase 5 (Multi-exchange expansion) or Phase 7 (Integration hardening)
