# Step 12: Gold Aggregation Job

**Status**: ⬜ Not Started
**Estimated Time**: 16-20 hours (across 2-3 weeks)
**Actual Time**: TBD
**Started**: TBD
**Completed**: TBD

---

## Overview

Implement a professional, industry-standard Gold layer for quantitative crypto market data analytics using a **layered hybrid architecture**:

1. **Base unified table** (`gold_crypto_trades`) - Single source of truth, deduplicated across all exchanges
2. **Pre-computed OHLCV candles** (1m/5m/1h/1d) - High-value analytics tables for 90% of quant queries
3. **Hybrid processing** - Streaming for recent data (5-min intervals), batch for historical aggregations
4. **Advanced metrics** - VWAP included in OHLCV, TWAP/spreads computed on-demand

**Key Design Principles**:
- ✅ **80/20 rule**: Pre-compute high-value, frequently-queried aggregations only
- ✅ **Resource efficient**: 1 streaming core (always on) + 1 batch core (scheduled, staggered execution)
- ✅ **Query optimized**: Hourly partitioning (24× better pruning), Z-ordering by (timestamp, symbol)
- ✅ **Production ready**: Data quality invariants, monitoring metrics, alerting thresholds

---

## Architecture

```
SILVER LAYER (Input)
├── silver_binance_trades (V2 schema + 3 metadata fields)
└── silver_kraken_trades (V2 schema + 3 metadata fields)
         │
         ↓ [5-minute streaming trigger, 1 core]
    ┌─────────────────────────────────────┐
    │  gold_unified_streaming.py          │
    │  - Union both Silver tables         │
    │  - Stateful dedup (message_id)      │
    │  - Derive partition fields          │
    └─────────────────────────────────────┘
         │
         ↓
GOLD LAYER (Output)
│
├── [BASE] gold_crypto_trades (17 fields, unlimited retention)
├── [OHLCV] gold_ohlcv_1m/5m/1h/1d (12 fields each, tiered retention)
└── [ADVANCED] TWAP, cross-exchange (Phase 2, compute on-demand)
```

**Resource Allocation**:
- **Current**: 4/6 cores (Bronze + Silver for both exchanges)
- **Available**: 2 cores free
- **Gold streaming**: 1 core always on
- **Gold batch**: 1 core scheduled (4 jobs staggered, <2 min total/hour)

---

## Phase 1: Base Gold Table (Week 1, 8 hours)

### Objective
Create unified `gold_crypto_trades` table combining Binance + Kraken trades, deduplicated by `message_id`.

### Tasks

#### 1.1 Verify Gold Table DDL
```bash
# Table already created in create_gold_table.py
docker exec k2-spark-master spark-submit \
  --master spark://spark-master:7077 \
  /opt/k2/src/k2/spark/jobs/create_gold_table.py
```

**Expected**: `gold_crypto_trades` table with 17 fields (15 V2 + exchange_date + exchange_hour), hourly partitions

#### 1.2 Create Gold Unified Streaming Job
**File**: `src/k2/spark/jobs/streaming/gold_unified_streaming.py` (~250 lines)

**Key logic**:
```python
# 1. Union Silver tables (schemas identical)
unified = silver_binance.union(silver_kraken)

# 2. Stateful deduplication by message_id (1-hour watermark)
deduped = unified \
    .withWatermark("ingestion_timestamp", "1 hour") \
    .dropDuplicates(["message_id"])

# 3. Derive partition fields
gold = deduped \
    .withColumn("exchange_date", to_date(from_unixtime(col("timestamp") / 1000000))) \
    .withColumn("exchange_hour", hour(from_unixtime(col("timestamp") / 1000000)))

# 4. Write to gold_crypto_trades
query = gold.writeStream \
    .format("iceberg") \
    .outputMode("append") \
    .option("checkpointLocation", "/checkpoints/gold-unified/") \
    .trigger(processingTime="5 minutes") \
    .toTable("iceberg.market_data.gold_crypto_trades")
```

#### 1.3 Add Gold Service to docker-compose.yml
```yaml
gold-unified-stream:
  image: apache/spark:3.5.3
  container_name: k2-gold-unified-stream
  command: >
    /opt/spark/bin/spark-submit
    --master spark://spark-master:7077
    --total-executor-cores 1
    --executor-cores 1
    --executor-memory 1g
    --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0
    /opt/k2/src/k2/spark/jobs/streaming/gold_unified_streaming.py
  volumes:
    - ./src:/opt/k2/src
    - ./checkpoints:/checkpoints
  networks:
    - k2-network
  depends_on:
    - spark-master
    - iceberg-rest
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 2G
```

#### 1.4 Start and Verify
```bash
# Start Gold streaming job
docker compose up -d gold-unified-stream

# Check Spark cluster resources (should show 5/6 cores used)
docker exec k2-spark-master curl -s http://localhost:8080/json/ | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'Cores: {d[\"coresused\"]}/{d[\"cores\"]}'); \
  [print(f'{a[\"name\"]}: {a[\"cores\"]} cores - {a[\"state\"]}') \
   for a in d.get('activeapps',[])]"

# Verify data flowing to Gold
docker exec k2-spark-master spark-sql -e \
  "SELECT COUNT(*), MAX(exchange_date) FROM iceberg.market_data.gold_crypto_trades;"

# Verify no duplicates
docker exec k2-spark-master spark-sql -e \
  "SELECT message_id, COUNT(*) FROM iceberg.market_data.gold_crypto_trades \
   GROUP BY message_id HAVING COUNT(*) > 1;"
```

### Acceptance Criteria (Phase 1)
- [ ] `gold_crypto_trades` table exists with 17 fields
- [ ] Streaming job running with 1 core (5/6 cores total used)
- [ ] No duplicates: 0 rows with duplicate message_id
- [ ] Both exchanges unified: SELECT shows BINANCE + KRAKEN
- [ ] Latency: p99 < 6 minutes (Kafka → Gold)
- [ ] Hourly partitions visible

---

## Phase 2: OHLCV Candle Tables (Week 2, 8-10 hours)

### Objective
Create pre-computed OHLCV candles at 4 time windows (1m/5m/1h/1d) for fast analytical queries.

### Tasks

#### 2.1 Create OHLCV Table DDLs
**File**: `src/k2/spark/jobs/create_gold_ohlcv_tables.py` (~300 lines)

**Schema** (identical for all 4 tables):
```sql
CREATE TABLE iceberg.market_data.gold_ohlcv_1m (
    symbol STRING NOT NULL,
    exchange STRING NOT NULL,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    window_date DATE NOT NULL,

    open_price DECIMAL(18, 8) NOT NULL,
    high_price DECIMAL(18, 8) NOT NULL,
    low_price DECIMAL(18, 8) NOT NULL,
    close_price DECIMAL(18, 8) NOT NULL,
    volume DECIMAL(18, 8) NOT NULL,

    trade_count BIGINT NOT NULL,
    vwap DECIMAL(18, 8) NOT NULL
)
USING iceberg
PARTITIONED BY (days(window_date))
TBLPROPERTIES (
    'write.target-file-size-bytes' = '67108864',  -- 64 MB
    'write.parquet.compression-codec' = 'zstd'
);
```

**Retention Strategy**:
| Table | Window | Retention | Use Case | Storage |
|-------|--------|-----------|----------|---------|
| gold_ohlcv_1m | 1 min | 90 days | High-freq analysis | ~10 GB |
| gold_ohlcv_5m | 5 min | 180 days | Intraday (most common) | ~5 GB |
| gold_ohlcv_1h | 1 hour | 1 year | Daily analysis | ~2 GB |
| gold_ohlcv_1d | 1 day | 3 years | Long-term trends | ~500 MB |

#### 2.2 Create OHLCV Batch Jobs
**Files**: `src/k2/spark/jobs/batch/gold_ohlcv_batch_1m.py` (~200 lines each)

**Core logic** (example for 1m candles):
```python
# Read from gold_crypto_trades (last 10 minutes)
trades = spark.read.table("iceberg.market_data.gold_crypto_trades") \
    .filter(col("exchange_date") >= date_sub(current_date(), 1))

# Convert timestamp to TimestampType
trades = trades.withColumn("timestamp_ts",
    (col("timestamp") / 1000000).cast("timestamp"))

# Generate 1-minute OHLCV candles
ohlcv = trades \
    .groupBy(
        "symbol", "exchange",
        window("timestamp_ts", "1 minute")
    ) \
    .agg(
        first("price", ignoreNulls=True).alias("open_price"),
        max("price").alias("high_price"),
        min("price").alias("low_price"),
        last("price", ignoreNulls=True).alias("close_price"),
        sum("quantity").alias("volume"),
        count("*").alias("trade_count"),
        (sum(col("price") * col("quantity")) / sum("quantity")).alias("vwap")
    ) \
    .select(
        "symbol", "exchange",
        col("window.start").alias("window_start"),
        col("window.end").alias("window_end"),
        to_date(col("window.start")).alias("window_date"),
        "open_price", "high_price", "low_price", "close_price",
        "volume", "trade_count", "vwap"
    )

# MERGE (upsert) to handle late arrivals
ohlcv.createOrReplaceTempView("new_candles")
spark.sql("""
    MERGE INTO iceberg.market_data.gold_ohlcv_1m AS target
    USING new_candles AS source
    ON target.symbol = source.symbol
       AND target.exchange = source.exchange
       AND target.window_start = source.window_start
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")
```

#### 2.3 Create Batch Scheduler
**File**: `src/k2/spark/jobs/batch/scheduler/gold_batch_scheduler.py` (~150 lines)

**Schedule**:
| Table | Cron | Lookback | Runtime | Storage |
|-------|------|----------|---------|---------|
| gold_ohlcv_1m | `*/5 * * * *` | 10 min | <30s | ~10 GB (90d) |
| gold_ohlcv_5m | `*/15 * * * *` | 15 min | <20s | ~5 GB (180d) |
| gold_ohlcv_1h | `0 * * * *` | 2 hours | <15s | ~2 GB (1y) |
| gold_ohlcv_1d | `5 0 * * *` | 2 days | <10s | ~500 MB (3y) |

**Configuration**: `configs/gold_layer_config.yaml`
```yaml
batch_jobs:
  ohlcv_1m:
    schedule: "*/5 * * * *"
    lookback_minutes: 10
    script: /opt/k2/src/k2/spark/jobs/batch/gold_ohlcv_batch_1m.py
  ohlcv_5m:
    schedule: "*/15 * * * *"
    lookback_minutes: 15
    script: /opt/k2/src/k2/spark/jobs/batch/gold_ohlcv_batch_5m.py
  # ... similar for 1h, 1d
```

#### 2.4 Add Batch Scheduler to docker-compose.yml
```yaml
gold-batch-scheduler:
  image: apache/spark:3.5.3
  container_name: k2-gold-batch-scheduler
  command: >
    python /opt/k2/src/k2/spark/jobs/batch/scheduler/gold_batch_scheduler.py
    --config /opt/k2/configs/gold_layer_config.yaml
  volumes:
    - ./src:/opt/k2/src
    - ./configs:/opt/k2/configs
    - ./checkpoints:/checkpoints
  networks:
    - k2-network
  depends_on:
    - spark-master
  deploy:
    resources:
      limits:
        cpus: '1.5'
        memory: 2G
```

#### 2.5 OHLCV Data Quality Validation
**File**: `src/k2/spark/validation/ohlcv_validation.py` (~100 lines)

**Invariants** (must all pass):
```python
# Price invariants
assert high_price >= open_price
assert high_price >= close_price
assert low_price <= open_price
assert low_price <= close_price

# Volume invariants
assert volume > 0
assert trade_count > 0

# VWAP invariant
assert low_price <= vwap <= high_price
```

### Acceptance Criteria (Phase 2)
- [ ] 4 OHLCV tables created (1m/5m/1h/1d)
- [ ] Batch scheduler running and triggering jobs on schedule
- [ ] OHLCV invariants: 100% pass rate
- [ ] Query latency: p99 < 100ms (1-hour window, 5m candles)
- [ ] Completeness: No gaps in candles
- [ ] Backfill complete: Last 7 days populated

---

## Phase 3: Testing & Validation (Week 3, 4-6 hours)

### Unit Tests
**File**: `tests/k2/spark/jobs/test_gold_unified_streaming.py`
- Deduplication logic (message_id)
- Partition field derivation (exchange_date, exchange_hour)
- Union correctness (Binance + Kraken)

**File**: `tests/k2/spark/jobs/test_ohlcv_batch.py`
- OHLCV aggregation correctness
- Window alignment (exact minute/hour boundaries)
- VWAP calculation
- Edge cases (single trade in window, zero volume)

### Integration Tests
**File**: `tests/integration/test_gold_e2e.py`
- End-to-end: Kafka → Bronze → Silver → Gold
- Both exchanges unified in Gold
- OHLCV candles generated correctly
- Query partition pruning works

### Performance Tests
**Target metrics**:
- Throughput: 10,000 trades/hour (peak load)
- Query latency: p99 < 100ms (1-hour OHLCV 5m)
- Batch runtime: 1m candles < 30s, 5m < 20s, 1h < 15s, 1d < 10s

---

## Critical Files to Create

### New Files
1. **`src/k2/spark/jobs/streaming/gold_unified_streaming.py`** (~250 lines) - Core streaming job
2. **`src/k2/spark/jobs/create_gold_ohlcv_tables.py`** (~300 lines) - DDL for 4 OHLCV tables
3. **`src/k2/spark/jobs/batch/gold_ohlcv_batch_1m.py`** (~200 lines) - 1-minute candles
4. **`src/k2/spark/jobs/batch/gold_ohlcv_batch_5m.py`** (~200 lines) - 5-minute candles
5. **`src/k2/spark/jobs/batch/gold_ohlcv_batch_1h.py`** (~200 lines) - 1-hour candles
6. **`src/k2/spark/jobs/batch/gold_ohlcv_batch_1d.py`** (~200 lines) - Daily candles
7. **`src/k2/spark/jobs/batch/scheduler/gold_batch_scheduler.py`** (~150 lines) - Orchestration
8. **`configs/gold_layer_config.yaml`** (~50 lines) - Configuration
9. **`src/k2/spark/validation/ohlcv_validation.py`** (~100 lines) - Data quality checks

### Existing Files to Modify
1. **`docker-compose.yml`** - Add `gold-unified-stream` and `gold-batch-scheduler` services
2. **`docs/phases/phase-10-streaming-crypto/PROGRESS.md`** - Mark Step 12 complete
3. **`docs/phases/phase-10-streaming-crypto/DECISIONS.md`** - Add decisions #017, #018, #019

---

## Verification Commands

### Phase 1 Verification (Base Gold Table)
```bash
# 1. Verify streaming job running
docker ps --filter "name=k2-gold" --format "table {{.Names}}\t{{.Status}}"

# 2. Check Spark cluster resources (expect 5/6 cores used)
docker exec k2-spark-master curl -s http://localhost:8080/json/ | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'Cores: {d[\"coresused\"]}/{d[\"cores\"]}'); \
  [print(f'{a[\"name\"]}: {a[\"cores\"]} cores - {a[\"state\"]}') \
   for a in d.get('activeapps',[])]"

# 3. Verify data flowing to Gold
docker exec k2-spark-master spark-sql -e \
  "SELECT COUNT(*), MIN(exchange_date), MAX(exchange_date) \
   FROM iceberg.market_data.gold_crypto_trades;"

# 4. Verify no duplicates
docker exec k2-spark-master spark-sql -e \
  "SELECT message_id, COUNT(*) as cnt \
   FROM iceberg.market_data.gold_crypto_trades \
   GROUP BY message_id HAVING cnt > 1;"

# 5. Verify both exchanges
docker exec k2-spark-master spark-sql -e \
  "SELECT exchange, COUNT(*) FROM iceberg.market_data.gold_crypto_trades \
   GROUP BY exchange;"

# 6. Test query latency (partition pruning)
docker exec k2-spark-master spark-sql -e \
  "EXPLAIN SELECT * FROM iceberg.market_data.gold_crypto_trades \
   WHERE symbol='BTCUSDT' AND exchange_date = CURRENT_DATE \
     AND exchange_hour >= 10;"
```

### Phase 2 Verification (OHLCV Tables)
```bash
# 1. Verify OHLCV tables created
docker exec k2-spark-master spark-sql -e \
  "SHOW TABLES IN iceberg.market_data LIKE 'gold_ohlcv*';"

# 2. Verify batch scheduler running
docker ps --filter "name=k2-gold-batch-scheduler" \
  --format "table {{.Names}}\t{{.Status}}"

# 3. Check OHLCV data (1m candles)
docker exec k2-spark-master spark-sql -e \
  "SELECT symbol, window_start, open_price, high_price, low_price, close_price, volume \
   FROM iceberg.market_data.gold_ohlcv_1m \
   WHERE symbol='BTCUSDT' AND window_date = CURRENT_DATE \
   ORDER BY window_start DESC LIMIT 10;"

# 4. Verify OHLCV invariants
docker exec k2-spark-master spark-sql -e \
  "SELECT COUNT(*) as invalid_candles \
   FROM iceberg.market_data.gold_ohlcv_1m \
   WHERE high_price < open_price OR high_price < close_price \
      OR low_price > open_price OR low_price > close_price \
      OR vwap < low_price OR vwap > high_price;"

# 5. Test query latency (5m candles, last 1 hour)
time docker exec k2-spark-master spark-sql -e \
  "SELECT * FROM iceberg.market_data.gold_ohlcv_5m \
   WHERE symbol='BTCUSDT' AND window_date = CURRENT_DATE \
     AND window_start >= date_sub(now(), INTERVAL 1 HOUR);"
```

---

## Trade-offs & Rationale

### Pre-compute vs On-Demand
| Metric | Strategy | Rationale |
|--------|----------|-----------|
| OHLCV 1m/5m/1h/1d | ✅ Pre-compute | 90% of analyst queries, high ROI |
| VWAP | ✅ Pre-compute (in OHLCV) | Cheap to compute with OHLCV |
| TWAP | ❌ On-demand | Complex interpolation, <5% of queries |
| Cross-exchange | ❌ On-demand | 10% of queries, <500ms latency OK |
| Spreads | ❌ Not implemented | Requires quote data (not available) |

### Retention Policies
| Table | Retention | Rationale |
|-------|-----------|-----------|
| gold_crypto_trades | Unlimited | Primary data source, historical queries |
| gold_ohlcv_1m | 90 days | High-frequency analysis, large storage (10 GB) |
| gold_ohlcv_5m | 180 days | Intraday analysis (most common), moderate storage (5 GB) |
| gold_ohlcv_1h | 1 year | Daily analysis, small storage (2 GB) |
| gold_ohlcv_1d | 3 years | Long-term trends, minimal storage (500 MB) |

---

## Example Queries for Quants

### Query 1: Last 5 minutes of trades (partition pruning)
```sql
SELECT * FROM iceberg.market_data.gold_crypto_trades
WHERE symbol = 'BTCUSDT'
  AND exchange_date = CURRENT_DATE
  AND exchange_hour = HOUR(NOW())
  AND timestamp >= (UNIX_TIMESTAMP() * 1000000) - (5 * 60 * 1000000)
ORDER BY timestamp DESC;
```

### Query 2: 5-minute OHLCV for last 1 hour (pre-computed)
```sql
SELECT window_start, open_price, high_price, low_price, close_price, volume, vwap
FROM iceberg.market_data.gold_ohlcv_5m
WHERE symbol = 'BTCUSDT'
  AND exchange = 'BINANCE'
  AND window_date = CURRENT_DATE
  AND window_start >= TIMESTAMP(NOW() - INTERVAL 1 HOUR)
ORDER BY window_start DESC;
```

### Query 3: Cross-exchange price comparison (on-demand)
```sql
WITH binance AS (
  SELECT timestamp, price FROM iceberg.market_data.gold_crypto_trades
  WHERE symbol = 'BTCUSDT' AND exchange = 'BINANCE'
    AND exchange_date = CURRENT_DATE AND exchange_hour >= 10
),
kraken AS (
  SELECT timestamp, price FROM iceberg.market_data.gold_crypto_trades
  WHERE symbol = 'BTCUSD' AND exchange = 'KRAKEN'
    AND exchange_date = CURRENT_DATE AND exchange_hour >= 10
)
SELECT
  b.timestamp, b.price AS binance_price, k.price AS kraken_price,
  (b.price - k.price) AS price_diff,
  ((b.price - k.price) / k.price * 100) AS price_diff_pct
FROM binance b
JOIN kraken k ON ABS(b.timestamp - k.timestamp) < 1000000  -- Within 1 second
WHERE ABS((b.price - k.price) / k.price * 100) > 0.5  -- >0.5% diff
ORDER BY b.timestamp DESC;
```

---

## Related Documentation
- [Decision #017](../DECISIONS.md#decision-017): Layered Gold schema (unified + pre-computed OHLCV)
- [Decision #018](../DECISIONS.md#decision-018): Hybrid processing (streaming + batch)
- [Decision #019](../DECISIONS.md#decision-019): 80/20 pre-computation strategy
- [Gold Layer Troubleshooting Runbook](../../operations/runbooks/gold-layer-troubleshooting.md)
- [Gold Layer Analytics Guide](../../architecture/gold-layer-analytics-guide.md)

---

**Last Updated**: 2026-01-20
**Maintained By**: Data Engineering Team
