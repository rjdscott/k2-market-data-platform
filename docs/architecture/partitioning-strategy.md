# Partitioning Strategy - K2 Market Data Platform

**Last Updated**: 2026-01-18
**Author**: Engineering Team

## Overview

This document outlines the partitioning strategy for the K2 Medallion Architecture (Bronze → Silver → Gold), with focus on crypto market data characteristics.

---

## Crypto Market Data Characteristics

1. **Volume Distribution**: Highly skewed
   - BTC/ETH: 40-50% of total volume
   - Top 10 symbols: 80% of volume
   - Long tail: 1000+ low-volume symbols

2. **Query Patterns**:
   - Time-range queries: "Last hour of trades" (very common)
   - Symbol-specific queries: "All BTC trades today" (common)
   - Exchange queries: "All Binance trades" (less common)
   - Cross-symbol analytics: "Compare BTC vs ETH" (Gold layer)

3. **Data Freshness**:
   - Bronze: 7-day retention (raw, reprocessable)
   - Silver: 30-day retention (validated, per-exchange)
   - Gold: Unlimited retention (unified analytics)

---

## Partitioning Best Practices

### General Principles

1. **Query patterns drive partitioning** - Optimize for the 80% use case
2. **Cardinality matters** - Balance between too few (large scans) and too many (overhead)
3. **Time-series first** - Market data is inherently time-based
4. **Iceberg advantages** - Hidden partitioning, partition evolution, efficient pruning

### Anti-Patterns to Avoid

❌ **High cardinality first**: `PARTITIONED BY (symbol, date)` creates too many directories
❌ **Too granular**: `PARTITIONED BY (date, hour, symbol, side)` = partition explosion
❌ **No partitioning**: Forces full table scans
❌ **Wrong order**: Secondary partition not useful if primary doesn't prune

---

## Bronze Layer Partitioning

**Current Strategy**: `PARTITIONED BY (ingestion_date)`

**Rationale**:
- Bronze is raw data for reprocessing (not queried directly)
- Simple date partitioning enables efficient retention cleanup
- No need for complex partitioning since Bronze → Silver is the primary flow
- 7-day retention keeps partition count low (~7 partitions per exchange)

**Partition Count**: ~14 partitions total (7 days × 2 exchanges)

**Verdict**: ✅ **Optimal for Bronze** - Keep as-is

---

## Silver Layer Partitioning

### Analysis of Options

| Strategy | Query Efficiency | Partition Count | Skew Risk | Verdict |
|----------|-----------------|-----------------|-----------|---------|
| `(exchange_date)` | Time-range: Good<br>Symbol: Poor | ~30 | None | ❌ Too broad |
| `(exchange_date, symbol)` | Time-range: Excellent<br>Symbol: Excellent | ~3,000 | Low | ✅ **Recommended** |
| `(symbol, exchange_date)` | Time-range: Poor<br>Symbol: Excellent | ~3,000 | None | ❌ Wrong order |
| `(exchange_date, hour, symbol)` | Time-range: Excellent<br>Symbol: Excellent | ~72,000 | Low | ❌ Too granular |

### Recommended Strategy: `PARTITIONED BY (exchange_date, symbol)`

**Rationale**:

1. **Time-series first**:
   - Date as primary partition aligns with time-series nature
   - Enables efficient retention cleanup (drop old dates)
   - Most queries filter by time range

2. **Symbol as secondary**:
   - Enables partition pruning for symbol-specific queries
   - Common query: "Show me BTC trades from last 3 days" → prunes to 3 date partitions + 1 symbol
   - Handles long-tail symbols efficiently (low-volume symbols create small files, Iceberg merges them)

3. **Partition count manageable**:
   - 30-day retention × ~100 active symbols = ~3,000 partitions per exchange
   - Iceberg handles this well (metadata-driven, not directory-based like Hive)
   - Auto-cleanup via retention policy

4. **Handles skew gracefully**:
   - High-volume symbols (BTC, ETH) get larger partition files
   - Low-volume symbols get smaller files (Iceberg compaction handles this)
   - No hot partition issues

**Example Queries**:

```sql
-- Excellent pruning: 1 date × 1 symbol = 1 partition
SELECT * FROM silver_binance_trades
WHERE exchange_date = '2026-01-18' AND symbol = 'BTCUSDT';

-- Good pruning: 3 dates × 1 symbol = 3 partitions
SELECT * FROM silver_binance_trades
WHERE exchange_date >= '2026-01-16' AND symbol = 'BTCUSDT';

-- Acceptable: 1 date × all symbols = ~100 partitions
SELECT * FROM silver_binance_trades
WHERE exchange_date = '2026-01-18';
```

**Partition File Sizes** (estimated):
- High-volume symbol (BTC): 50-100MB per day
- Medium-volume symbol (LINK): 5-10MB per day
- Low-volume symbol (SHIB): 0.5-1MB per day

Iceberg's compaction will merge small files automatically.

---

## Gold Layer Partitioning

**Current Strategy**: `PARTITIONED BY (exchange_date, exchange_hour)`

**Rationale**:

1. **Analytics workload**:
   - Gold is for cross-exchange, cross-symbol analytics
   - Queries typically scan multiple symbols: "Compare all majors"
   - Time-range filtering is primary: "Last 24 hours across all exchanges"

2. **Hourly granularity**:
   - Enables efficient sub-day queries: "Show me 9am-10am today"
   - Keeps partition files reasonably sized (1-hour batches)
   - 24 hours × unlimited days = growing but manageable

3. **No symbol partition**:
   - Adding symbol would create 24 × symbols × days = explosion
   - Gold queries typically don't filter by single symbol
   - If needed, can add symbol as 3rd partition in future (Iceberg partition evolution)

**Partition Count**: ~365 days × 24 hours = ~8,760 partitions per year (acceptable for analytics)

**Example Queries**:

```sql
-- Excellent pruning: 1 date × 1 hour = 1 partition
SELECT * FROM gold_crypto_trades
WHERE exchange_date = '2026-01-18' AND exchange_hour = 9;

-- Good pruning: 1 date × 24 hours = 24 partitions
SELECT * FROM gold_crypto_trades
WHERE exchange_date = '2026-01-18';

-- Acceptable: 7 dates × 24 hours = 168 partitions
SELECT * FROM gold_crypto_trades
WHERE exchange_date >= '2026-01-12';
```

**Verdict**: ✅ **Optimal for Gold** - Keep as-is

---

## Comparison: Alternative Approaches

### If We Added Symbol to Gold

`PARTITIONED BY (exchange_date, exchange_hour, symbol)`

**Pros**:
- Symbol-specific queries prune to single symbol
- Best possible query performance

**Cons**:
- 365 days × 24 hours × 100 symbols = **876,000 partitions per year**
- Iceberg metadata overhead becomes significant
- Most Gold queries DON'T filter by symbol (they analyze across symbols)
- Partition evolution can add this later if query patterns change

**Verdict**: ❌ **Not recommended** - Over-optimization, wrong query pattern

---

## Implementation Recommendations

### Silver Tables

```sql
CREATE TABLE silver_binance_trades (
    -- ... fields ...
    exchange_date DATE,
    symbol STRING
)
USING iceberg
PARTITIONED BY (exchange_date, symbol)
TBLPROPERTIES (
    'write.target-file-size-bytes' = '134217728',  -- 128 MB
    'write.parquet.compression-codec' = 'zstd'
)
```

### Monitoring

Track these metrics to validate partitioning strategy:

1. **Partition count**: Should stay under 5,000 per table
2. **File sizes**: Target 128 MB per file, flag <10 MB (needs compaction)
3. **Query pruning**: Use Iceberg metadata to track partition scans per query
4. **Compaction frequency**: Schedule daily for Silver, weekly for Gold

### Future Evolution

If query patterns change (e.g., more symbol-specific queries in Gold):

```sql
-- Iceberg allows partition evolution without rewriting data
ALTER TABLE gold_crypto_trades
ADD PARTITION FIELD symbol;
```

This is a major advantage over Hive-style partitioning.

---

## Summary

| Layer | Partitioning Strategy | Partition Count | Rationale |
|-------|----------------------|-----------------|-----------|
| **Bronze** | `(ingestion_date)` | ~14 | Simple, reprocessing-focused |
| **Silver** | `(exchange_date, symbol)` | ~3,000 | Time + symbol queries |
| **Gold** | `(exchange_date, exchange_hour)` | ~8,760/year | Time-range analytics |

**Key Insight**: Partition by **query access pattern**, not by **data characteristics**. Silver is queried by time+symbol. Gold is queried by time across all symbols.

---

**Decision**: 2026-01-18
**Status**: Approved for implementation
**Next Review**: After 30 days of production usage

