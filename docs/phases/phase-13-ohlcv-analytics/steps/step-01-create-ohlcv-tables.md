# Step 01: Create OHLCV Iceberg Tables

**Status**: ✅ Complete
**Estimated Time**: 4 hours
**Actual Time**: 3 hours
**Milestone**: 1 (Infrastructure Setup)
**Dependencies**: Gold layer operational, Spark cluster available
**Completed**: 2026-01-21

---

## Objective

Create 5 OHLCV (Open-High-Low-Close-Volume) Iceberg tables with unified schema, daily/monthly partitioning, and optimized TBLPROPERTIES for analytical queries.

---

## Context

The Gold layer currently has `gold_crypto_trades` with raw trades. This step creates the OHLCV aggregation tables that will be populated by batch jobs in later steps.

**Tables to Create**:
1. `gold_ohlcv_1m` - 1-minute candles, 90-day retention
2. `gold_ohlcv_5m` - 5-minute candles, 180-day retention
3. `gold_ohlcv_30m` - 30-minute candles, 1-year retention
4. `gold_ohlcv_1h` - 1-hour candles, 3-year retention
5. `gold_ohlcv_1d` - 1-day candles, 5-year retention

---

## Implementation

### 1. Create Table Creation Script

**File**: `src/k2/spark/jobs/create_ohlcv_tables.py` (~200 lines)

**Pattern**: Follow `create_gold_table.py` structure:
- Use `create_spark_session()` from utils
- Define unified schema (14 fields)
- Create tables with `CREATE TABLE IF NOT EXISTS`
- Set TBLPROPERTIES for Iceberg optimization
- Verify schema with `DESCRIBE EXTENDED`

### 2. Unified OHLCV Schema (14 Fields)

```sql
CREATE TABLE iceberg.market_data.gold_ohlcv_{timeframe} (
    -- Primary Keys
    symbol STRING NOT NULL COMMENT 'Trading pair (BTCUSDT, ETHUSDT, etc.)',
    exchange STRING NOT NULL COMMENT 'Exchange code (BINANCE, KRAKEN)',
    window_start TIMESTAMP NOT NULL COMMENT 'Window start time (inclusive)',
    window_end TIMESTAMP NOT NULL COMMENT 'Window end time (exclusive)',
    window_date DATE NOT NULL COMMENT 'Partition key: date of window_start',

    -- OHLC Metrics
    open_price DECIMAL(18, 8) NOT NULL COMMENT 'First trade price in window',
    high_price DECIMAL(18, 8) NOT NULL COMMENT 'Highest trade price in window',
    low_price DECIMAL(18, 8) NOT NULL COMMENT 'Lowest trade price in window',
    close_price DECIMAL(18, 8) NOT NULL COMMENT 'Last trade price in window',

    -- Volume Metrics
    volume DECIMAL(18, 8) NOT NULL COMMENT 'Total quantity traded',
    trade_count BIGINT NOT NULL COMMENT 'Number of trades in window',

    -- Derived Metrics
    vwap DECIMAL(18, 8) NOT NULL COMMENT 'Volume-weighted average price',

    -- Metadata
    created_at TIMESTAMP NOT NULL COMMENT 'ETL job execution timestamp',
    updated_at TIMESTAMP NOT NULL COMMENT 'Last update timestamp'
)
USING iceberg
PARTITIONED BY (days(window_date))  -- or months(window_date) for 1d
TBLPROPERTIES (
    'format-version' = '2',
    'write.parquet.compression-codec' = 'zstd',
    'write.metadata.compression-codec' = 'gzip',
    'write.target-file-size-bytes' = '67108864',  -- 64 MB
    'write.distribution-mode' = 'hash',
    'write.delete.mode' = 'merge-on-read',
    'write.update.mode' = 'merge-on-read',
    'commit.retry.num-retries' = '5'
);
```

### 3. Partitioning Strategy

| Table | Partition By | Rationale |
|-------|--------------|-----------|
| gold_ohlcv_1m | days(window_date) | ~1440 candles/day, manageable |
| gold_ohlcv_5m | days(window_date) | ~288 candles/day, efficient |
| gold_ohlcv_30m | days(window_date) | ~48 candles/day, optimal |
| gold_ohlcv_1h | days(window_date) | ~24 candles/day, good |
| gold_ohlcv_1d | months(window_date) | ~30 candles/month, avoid tiny partitions |

### 4. Execution

```bash
# From host
docker exec spark-master spark-submit \
  --master spark://spark-master:7077 \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
  /opt/k2/src/k2/spark/jobs/create_ohlcv_tables.py
```

---

## Acceptance Criteria

- [x] Script created: `src/k2/spark/jobs/create_ohlcv_tables.py`
- [x] All 5 tables created successfully
- [x] Schema verified: 14 fields (symbol, exchange, timestamps, OHLC, volume, vwap, metadata)
- [x] Partitioning: days(window_date) for all timeframes
- [x] TBLPROPERTIES verified: zstd compression, format-version=2
- [x] All tables visible and queryable

---

## Verification Commands

```sql
-- Verify all tables exist
SHOW TABLES IN iceberg.market_data LIKE 'gold_ohlcv%';

-- Verify schema
DESCRIBE EXTENDED iceberg.market_data.gold_ohlcv_1m;

-- Verify properties
SHOW TBLPROPERTIES iceberg.market_data.gold_ohlcv_1m;

-- Check partitioning
SHOW PARTITIONS iceberg.market_data.gold_ohlcv_1m;
```

---

## Files to Create

- `src/k2/spark/jobs/create_ohlcv_tables.py` (~200 lines)

---

## Files to Reference

- `src/k2/spark/jobs/create_gold_table.py` (pattern to follow)
- `src/k2/spark/utils/spark_session.py` (import create_spark_session)

---

## Notes

- Follow existing table creation pattern from `create_gold_table.py`
- Use `days(window_date)` for 1m/5m/30m/1h, `months(window_date)` for 1d
- Target file size 64MB (smaller than gold_crypto_trades 256MB)
- No data populated yet - tables empty after creation

---

**Last Updated**: 2026-01-21
**Owner**: Data Engineering Team
**Completion Notes**: All 5 tables created successfully. Verified via queries.
