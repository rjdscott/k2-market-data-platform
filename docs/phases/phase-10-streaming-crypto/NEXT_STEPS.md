# Phase 10 - Next Steps

**Last Updated**: 2026-01-20
**Current Status**: Silver Layer Complete ✅ | Gold Layer Planned ⬜

---

## Current State

### ✅ Complete and Production Ready
1. **Bronze Layer** (Steps 10) - Both exchanges operational
   - `bronze_binance_trades` - High volume (500-3000 rows/10s)
   - `bronze_kraken_trades` - Moderate volume (8-30 rows/30s)
   - Raw bytes storage pattern (replayability)
   - Zero errors, continuous ingestion

2. **Silver Layer** (Step 11) - Both exchanges validated
   - `silver_binance_trades` - Validated V2 schema
   - `silver_kraken_trades` - Validated V2 schema
   - `silver_dlq_trades` - DLQ pattern for invalid records
   - All critical fixes applied (NULL handling, resource allocation)

3. **Gold Table DDL** (Step 9)
   - `gold_crypto_trades` table exists (17 fields, hourly partitions)
   - Ready for streaming job implementation

### 📊 Resource Status
- **Spark Cluster**: 6 cores total, 6GB RAM
- **Current Usage**: 4/6 cores (Bronze Binance/Kraken + Silver Binance/Kraken)
- **Available**: 2 cores free for Gold layer

---

## What's Next: Gold Layer Implementation

### Option 1: Start Gold Layer (Recommended) 🎯
**Estimated**: 16-20 hours across 2-3 weeks in 3 phases

**Phase 1** (Week 1, 8 hours):
- Implement `gold_unified_streaming.py` (~250 lines)
- Union Silver tables, stateful deduplication, derive partitions
- Deploy streaming job (1 core, 5-min trigger)
- **Result**: Base `gold_crypto_trades` operational

**Phase 2** (Week 2, 8-10 hours):
- Create 4 OHLCV tables (1m/5m/1h/1d DDL)
- Implement 4 batch jobs (~200 lines each)
- Implement batch scheduler (~150 lines)
- Deploy batch scheduler (1 core, scheduled)
- **Result**: Pre-computed OHLCV candles for quant analytics

**Phase 3** (Week 3, 4-6 hours):
- Unit tests (deduplication, aggregation, windows)
- Integration tests (E2E Kafka → Gold)
- Performance tests (10K trades/hour, <100ms queries)
- Data quality validation (OHLCV invariants)
- **Result**: Production-ready Gold layer with monitoring

**Quick Start Tomorrow**:
```bash
# 1. Read the implementation guide
cat docs/phases/phase-10-streaming-crypto/steps/step-12-gold-aggregation.md

# 2. Review architectural decisions
cat docs/phases/phase-10-streaming-crypto/DECISIONS.md | grep -A 50 "Decision #017"

# 3. Start Phase 1: Create gold_unified_streaming.py
# Pattern: Follow silver_binance_transformation.py structure
# Location: src/k2/spark/jobs/streaming/gold_unified_streaming.py
```

### Option 2: Testing & Validation First (Steps 15-16)
**Estimated**: 8-12 hours

- Comprehensive testing of Bronze + Silver layers
- Performance benchmarking
- Load testing (peak volume scenarios)
- Chaos testing (failure scenarios)
- **Result**: Validated Bronze + Silver before Gold

### Option 3: Operational Enhancements
**Estimated**: 6-8 hours

- Add Prometheus metrics for streaming jobs
- Create Grafana dashboards (Bronze/Silver monitoring)
- Set up alerting (DLQ rate, streaming lag, resource usage)
- Write operational runbooks
- **Result**: Production observability for current layers

### Option 4: Additional Data Sources
**Estimated**: 12-16 hours

- Add Coinbase WebSocket client
- Add Coinbase Bronze + Silver jobs
- Validate multi-exchange patterns scale
- **Result**: 3-exchange platform before Gold aggregation

---

## Quick Reference

### Check Current Status
```bash
# Check all streaming jobs
docker ps --filter "name=k2-bronze" --filter "name=k2-silver" \
  --format "table {{.Names}}\t{{.Status}}"

# Check Spark cluster resources
docker exec k2-spark-master curl -s http://localhost:8080/json/ | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'Cores: {d[\"coresused\"]}/{d[\"cores\"]}'); \
  [print(f'{a[\"name\"]}: {a[\"cores\"]} cores - {a[\"state\"]}') \
   for a in d.get('activeapps',[])]"

# Expected: 4/6 cores (Bronze Binance/Kraken + Silver Binance/Kraken)

# Verify data flowing (Silver)
docker exec k2-spark-master spark-sql -e \
  "SELECT exchange, COUNT(*) FROM iceberg.market_data.silver_binance_trades \
   GROUP BY exchange;"

docker exec k2-spark-master spark-sql -e \
  "SELECT exchange, COUNT(*) FROM iceberg.market_data.silver_kraken_trades \
   GROUP BY exchange;"
```

### Key Documentation Locations
- **Implementation Guide**: `docs/phases/phase-10-streaming-crypto/steps/step-12-gold-aggregation.md`
- **Progress Tracking**: `docs/phases/phase-10-streaming-crypto/PROGRESS.md`
- **Architectural Decisions**: `docs/phases/phase-10-streaming-crypto/DECISIONS.md`
- **Runbooks**: `docs/operations/runbooks/bronze-silver-streaming-troubleshooting.md`
- **Architecture Guide**: `docs/architecture/streaming-pipeline-guide.md`

### Critical Files for Gold Layer
```
To Create (Phase 1):
- src/k2/spark/jobs/streaming/gold_unified_streaming.py (~250 lines)
- docker-compose.yml (add gold-unified-stream service)

To Create (Phase 2):
- src/k2/spark/jobs/create_gold_ohlcv_tables.py (~300 lines)
- src/k2/spark/jobs/batch/gold_ohlcv_batch_1m.py (~200 lines)
- src/k2/spark/jobs/batch/gold_ohlcv_batch_5m.py (~200 lines)
- src/k2/spark/jobs/batch/gold_ohlcv_batch_1h.py (~200 lines)
- src/k2/spark/jobs/batch/gold_ohlcv_batch_1d.py (~200 lines)
- src/k2/spark/jobs/batch/scheduler/gold_batch_scheduler.py (~150 lines)
- configs/gold_layer_config.yaml (~50 lines)
- docker-compose.yml (add gold-batch-scheduler service)

To Create (Phase 3):
- src/k2/spark/validation/ohlcv_validation.py (~100 lines)
- tests/k2/spark/jobs/test_gold_unified_streaming.py
- tests/k2/spark/jobs/test_ohlcv_batch.py
- tests/integration/test_gold_e2e.py
```

---

## Key Decisions Made Today

### Decision #017: Layered Gold Schema
- Base unified table + pre-computed OHLCV candles
- 80/20 optimization: 18 GB storage vs 100+ GB
- Query performance: p99 < 100ms for 90% of queries

### Decision #018: Hybrid Processing
- Streaming for base table (5-min trigger, 1 core always on)
- Batch for OHLCV (scheduled, 1 core on-demand)
- Resource efficient: 5/6 cores steady state

### Decision #019: 80/20 Pre-Computation Strategy
- Pre-compute: OHLCV + VWAP (90% of queries)
- On-demand: TWAP, cross-exchange (10% of queries)
- Balances performance vs cost

---

## Picking Up Tomorrow

### Recommended Flow:

1. **Review architectural decisions** (5 min)
   ```bash
   cat docs/phases/phase-10-streaming-crypto/DECISIONS.md | grep -A 100 "Decision #017"
   ```

2. **Read Step 12 implementation guide** (15 min)
   ```bash
   cat docs/phases/phase-10-streaming-crypto/steps/step-12-gold-aggregation.md | less
   ```

3. **Verify current system status** (5 min)
   ```bash
   # Check all jobs running
   docker ps --filter "name=k2-bronze" --filter "name=k2-silver"

   # Check Spark resources
   docker exec k2-spark-master curl -s http://localhost:8080/json/
   ```

4. **Start Gold Phase 1** (or choose different option above)
   - Create `src/k2/spark/jobs/streaming/gold_unified_streaming.py`
   - Pattern: Follow `silver_binance_transformation.py` structure
   - Key logic: Union, stateful dedup, derive partition fields

---

## Questions to Consider Tomorrow

1. **Implementation priority**: Start Gold layer immediately or add observability first?
2. **Testing strategy**: Test Bronze/Silver thoroughly before Gold, or implement Gold then test E2E?
3. **Resource planning**: Is 6 cores sufficient for Gold + batch, or scale cluster first?
4. **Data sources**: Add more exchanges before Gold aggregation, or Gold first then scale?

---

## Success Metrics (When Gold Complete)

### Performance
- [ ] End-to-end latency: p99 < 6 minutes (Kafka → Gold)
- [ ] Query latency: p99 < 100ms (1-hour OHLCV 5m)
- [ ] Batch runtime: 1m candles < 30s, 5m < 20s
- [ ] Throughput: 10,000 trades/hour

### Data Quality
- [ ] No duplicates in gold_crypto_trades
- [ ] OHLCV invariants: 100% pass rate
- [ ] Completeness: <5 candle gaps/hour

### Resource Efficiency
- [ ] Spark cluster: 5/6 cores steady state (83%)
- [ ] Memory: 6GB total (no increase)
- [ ] Storage: OHLCV < 20 GB total

---

**Last Updated**: 2026-01-20
**Maintained By**: Data Engineering Team
**Next Session**: Resume with Option 1 (Gold Layer Phase 1) recommended
