# Phase 5: Cold Tier Restructure - Implementation Plan

**Date:** 2026-02-11
**Engineer:** Platform Engineering (Staff Level)
**Duration Estimate:** 1-2 weeks
**Status:** 🟡 Planning

---

## Executive Summary

Implement Iceberg cold storage tier that mirrors the ClickHouse four-layer medallion architecture (Bronze, Silver, Gold). This enables:
- **Long-term data retention** (30+ days) without degrading ClickHouse performance
- **Data lineage preservation** across warm (ClickHouse) and cold (Iceberg) tiers
- **Re-derivability** at any layer (critical for market data platform)
- **~1 hour cold tier freshness** (vs 24h typical)
- **50% resource reduction** (Iceberg infra cold-only role)

**Key Architecture:** Hourly Kotlin offload service + Daily Spark maintenance

---

## Current System State (Phase 4 Complete)

### ClickHouse Warm Tier (0-30 days TTL)

**Bronze Layer (Per-Exchange Raw Data):**
```sql
bronze_trades_binance     -- MergeTree, Kafka Engine source
bronze_trades_kraken      -- MergeTree, Kafka Engine source
```

**Silver Layer (Unified Normalized Trades):**
```sql
silver_trades             -- MergeTree
  - Partitioned by: (exchange, asset_class, date)
  - Order by: (exchange, asset_class, canonical_symbol, timestamp)
  - TTL: 30 days
  - Columns: 18 fields including UUID, Decimal128 prices, vendor_data Map
```

**Gold Layer (OHLCV Aggregations - 6 Timeframes):**
```sql
ohlcv_1m, ohlcv_5m, ohlcv_15m, ohlcv_30m, ohlcv_1h, ohlcv_1d
  - Engine: SummingMergeTree
  - Populated via Materialized Views from silver_trades
  - TTL: 30 days
```

**Current Resource Usage:**
- 3.2 CPU / 3.2 GB (7 services operational)
- 275K+ trades processed
- Real-time OHLCV generation working

---

## Phase 5 Architecture: Cold Tier Design

### Iceberg Tables (Cold Storage - Mirrors ClickHouse Medallion)

#### Table Structure (9 Tables Total)

```
BRONZE LAYER (2 tables - per-exchange preservation)
├── cold.bronze_trades_binance
│   ├── Partitioned by: days(timestamp), exchange
│   ├── Sorted by: symbol, timestamp, trade_id
│   ├── Format: Parquet + Zstd compression
│   └── Use case: Re-derive Silver if validation logic changes
│
├── cold.bronze_trades_kraken
│   ├── Partitioned by: days(timestamp), exchange
│   ├── Sorted by: symbol, timestamp, trade_id
│   ├── Format: Parquet + Zstd compression
│   └── Use case: Re-derive Silver if validation logic changes

SILVER LAYER (1 table - unified normalized)
├── cold.silver_trades
│   ├── Partitioned by: days(timestamp), exchange, asset_class
│   ├── Sorted by: exchange, canonical_symbol, timestamp
│   ├── Format: Parquet + Zstd compression
│   └── Use case: Re-derive Gold, historical trade queries

GOLD LAYER (6 tables - OHLCV timeframes)
├── cold.gold_ohlcv_1m      -- 1-minute candles
├── cold.gold_ohlcv_5m      -- 5-minute candles
├── cold.gold_ohlcv_15m     -- 15-minute candles
├── cold.gold_ohlcv_30m     -- 30-minute candles
├── cold.gold_ohlcv_1h      -- 1-hour candles
└── cold.gold_ohlcv_1d      -- Daily candles
    ├── Partitioned by: months(window_start), exchange
    ├── Sorted by: exchange, canonical_symbol, window_start
    ├── Format: Parquet + Zstd compression
    └── Use case: Historical charting, analytics
```

**Note:** No RAW layer in initial implementation - Bronze is our lowest fidelity (can add RAW in future phase if regulatory audit requirements emerge).

---

## Two-Tier Batch Strategy

### Tier 1: Hourly Offload (Kotlin Iceberg Writer)

**Implementation:** Kotlin service using Apache Iceberg Java SDK

**Schedule:**
```
Every hour:
  :05  → Offload bronze_trades_binance (last hour)
  :06  → Offload bronze_trades_kraken (last hour)
  :07  → Offload silver_trades (last hour)
  :09  → Offload gold_ohlcv_1m (last hour)
  :10  → Offload gold_ohlcv_5m (last hour)
  :11  → Offload gold_ohlcv_15m (last hour)
  :12  → Offload gold_ohlcv_30m (last hour)
  :13  → Offload gold_ohlcv_1h (last hour)
  :14  → Offload gold_ohlcv_1d (last hour)

Total: ~10 min/hour, lightweight (pure Kotlin JVM)
```

**Resource Profile:**
- CPU: 0.25 (idle) → 0.5 (during offload)
- RAM: 256 MB (idle) → 512 MB (during offload)
- Container: Standalone sidecar (not embedded in API for operational isolation)

### Tier 2: Daily Maintenance (Spark Batch)

**Implementation:** Spark batch (ephemeral, on-demand)

**Schedule:**
```
Daily 02:00 UTC:
  02:00  → Iceberg compaction (merge hourly Parquet files)
  02:20  → Snapshot expiry (remove snapshots older than 7 days)
  02:30  → Data quality audit (verify row counts: ClickHouse vs Iceberg)
  02:45  → Orphan file cleanup
  03:00  → Spark exits

Total: ~60 min/day
```

**Resource Profile:**
- CPU: 2.0 (only during batch window)
- RAM: 4 GB (only during batch window)
- Container: Ephemeral (docker-compose profile)

---

## Implementation Steps (5 Steps)

### Step 1: Create Iceberg DDL & Infrastructure (2-3 days)

**Objectives:**
1. Create Iceberg catalog database schema
2. Create 9 Iceberg tables (Bronze: 2, Silver: 1, Gold: 6)
3. Validate tables created successfully
4. Test manual writes (Spark shell)

**Deliverables:**
- `docker/iceberg/ddl/01-catalog-schema.sql` (PostgreSQL catalog DDL)
- `docker/iceberg/ddl/02-bronze-tables.sql` (Iceberg DDL via Spark)
- `docker/iceberg/ddl/03-silver-table.sql` (Iceberg DDL via Spark)
- `docker/iceberg/ddl/04-gold-tables.sql` (Iceberg DDL via Spark)
- Validation script: `docker/iceberg/validation/validate-tables.sql`

**Acceptance Criteria:**
- [ ] 9 Iceberg tables created in catalog
- [ ] Partitioning scheme validated (check metadata)
- [ ] Sample data inserted via Spark shell (1K rows per table)
- [ ] MinIO bucket contains Parquet files
- [ ] Catalog metadata queryable (PostgreSQL)

---

### Step 2: Implement Kotlin Hourly Offload Service (3-4 days)

**Objectives:**
1. Create Kotlin service using Iceberg Java SDK
2. Implement hourly SELECT from ClickHouse
3. Implement Parquet write to MinIO
4. Implement catalog update via Iceberg REST
5. Add error handling, retries, metrics

**Deliverables:**
- `services/iceberg-offload-kotlin/` (Kotlin service)
  - `OffloadScheduler.kt` (hourly cron scheduler)
  - `IcebergWriter.kt` (Parquet write + catalog update)
  - `ClickHouseReader.kt` (hourly partition SELECT)
  - `OffloadMetrics.kt` (Prometheus metrics)
  - `application.yml` (config: ClickHouse, MinIO, Iceberg REST)
- `docker/docker-compose.iceberg-offload.yml` (service definition)
- `docs/operations/iceberg-offload-monitoring.md` (runbook)

**Acceptance Criteria:**
- [ ] Service connects to ClickHouse, MinIO, Iceberg REST
- [ ] Hourly cron triggers successfully
- [ ] Bronze/Silver/Gold data offloaded correctly
- [ ] Parquet files compressed with Zstd
- [ ] Row counts match ClickHouse source
- [ ] Prometheus metrics exported (rows_offloaded, offload_duration_seconds)
- [ ] Error handling: retry logic for transient failures
- [ ] Logs structured JSON (exchange, table, hour, row_count)

---

### Step 3: Implement Spark Daily Maintenance (2 days)

**Objectives:**
1. Create Spark batch jobs (Scala/Python)
2. Implement compaction (merge small files)
3. Implement snapshot expiry
4. Implement data quality audit
5. Configure ephemeral execution (exit after completion)

**Deliverables:**
- `services/spark-batch/` (Spark batch jobs)
  - `IcebergCompaction.scala` (merge small Parquet files)
  - `SnapshotExpiry.scala` (expire old snapshots)
  - `DataQualityAudit.scala` (verify warm-cold consistency)
  - `build.sbt` (dependencies: Spark 3.5+, Iceberg 1.5+)
- `docker/docker-compose.spark-batch.yml` (ephemeral profile)
- `docs/operations/spark-batch-maintenance.md` (runbook)

**Acceptance Criteria:**
- [ ] Compaction reduces file count by 70-90%
- [ ] Snapshot expiry removes snapshots older than 7 days
- [ ] Data quality audit detects row count mismatches
- [ ] Spark exits cleanly after completion (ephemeral)
- [ ] Metrics published to Prometheus
- [ ] Logs captured in structured format

---

### Step 4: Validate Warm-Cold Consistency (1-2 days)

**Objectives:**
1. Run end-to-end test (24h period)
2. Validate row counts match across all layers
3. Validate data integrity (sample spot checks)
4. Test ClickHouse federated queries (warm + cold)
5. Performance test cold queries

**Deliverables:**
- `tests/integration/test_warm_cold_consistency.py` (automated validation)
- `docker/iceberg/validation/validate-consistency.sql` (SQL validation queries)
- `docs/testing/warm-cold-validation.md` (test procedures)

**Acceptance Criteria:**
- [ ] Row counts match: ClickHouse vs Iceberg (all 9 tables)
- [ ] Spot check: 1000 random trades match exactly (price, quantity, timestamp)
- [ ] ClickHouse Iceberg table function works (federated queries)
- [ ] Query latency acceptable: cold queries <5s p99 (historical analytics)
- [ ] Zero data loss detected over 24h period
- [ ] Validation test suite passes (automated)

---

### Step 5: Resource Optimization & Production Hardening (1 day)

**Objectives:**
1. Reduce Iceberg infrastructure resources by 50%
2. Add monitoring dashboards (Grafana)
3. Create operational runbooks
4. Tag release `v2-phase-5-complete`

**Deliverables:**
- Updated `docker-compose.v2.yml` (reduced resource limits)
- Grafana dashboard: `grafana/dashboards/iceberg-cold-tier.json`
- Runbooks:
  - `docs/operations/runbooks/iceberg-offload-failure.md`
  - `docs/operations/runbooks/spark-batch-failure.md`
  - `docs/operations/runbooks/cold-query-slow.md`
- Phase 5 completion summary

**Acceptance Criteria:**
- [ ] MinIO: 1.0 CPU / 2GB → 0.5 CPU / 1GB ✅
- [ ] PostgreSQL: 1.0 CPU / 1GB → 0.5 CPU / 512MB ✅
- [ ] Iceberg REST: 1.0 CPU / 1GB → 0.5 CPU / 512MB ✅
- [ ] Total savings: -1.5 CPU / -2GB ✅
- [ ] Grafana dashboard shows: offload lag, file count, query latency
- [ ] Alerts configured: offload failure, audit mismatch, MinIO disk usage
- [ ] Runbooks tested and validated
- [ ] Git tag created: `v2-phase-5-complete`

---

## Technical Deep-Dive: Iceberg Schema Design

### Bronze Tables Schema (Per-Exchange)

**Example: `cold.bronze_trades_binance`**

```sql
-- Iceberg DDL (via Spark SQL)
CREATE TABLE cold.bronze_trades_binance (
    trade_id STRING,
    exchange STRING,
    symbol STRING,
    canonical_symbol STRING,
    price DECIMAL(38, 8),
    quantity DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    side STRING,
    exchange_timestamp TIMESTAMP,
    platform_timestamp TIMESTAMP,
    sequence_number BIGINT,
    metadata STRING
)
USING iceberg
PARTITIONED BY (days(exchange_timestamp), exchange)
LOCATION 's3a://k2-data/warehouse/cold/bronze/bronze_trades_binance'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.metadata.metrics.default' = 'full',
    'history.expire.max-snapshot-age-ms' = '604800000'  -- 7 days
);
```

**Design Rationale:**
- **Partitioned by days(exchange_timestamp)**: Aligns with ClickHouse TTL (30 days), enables efficient time-range queries
- **Partitioned by exchange**: Isolates exchanges for independent evolution
- **Zstd compression**: 10-20x compression ratio for market data
- **Full metrics**: Track min/max/null_count for all columns (predicate pushdown)
- **7-day snapshot retention**: Time-travel for compliance, expires old snapshots

### Silver Table Schema (Unified)

**`cold.silver_trades`**

```sql
CREATE TABLE cold.silver_trades (
    message_id STRING,          -- UUID
    trade_id STRING,
    exchange STRING,
    symbol STRING,
    canonical_symbol STRING,
    asset_class STRING,         -- 'crypto', 'equities', etc.
    currency STRING,
    price DECIMAL(38, 8),
    quantity DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    side STRING,                -- 'BUY', 'SELL'
    trade_conditions ARRAY<STRING>,
    timestamp TIMESTAMP,        -- microsecond precision mapped to TIMESTAMP
    ingestion_timestamp TIMESTAMP,
    processed_at TIMESTAMP,
    source_sequence BIGINT,
    platform_sequence BIGINT,
    vendor_data MAP<STRING, STRING>,
    is_valid BOOLEAN,
    validation_errors ARRAY<STRING>
)
USING iceberg
PARTITIONED BY (days(timestamp), exchange, asset_class)
LOCATION 's3a://k2-data/warehouse/cold/silver/silver_trades'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.metadata.metrics.default' = 'full'
);
```

**Design Rationale:**
- **Multi-asset support**: asset_class partition enables future equities/futures
- **Vendor data preservation**: Map<String, String> for exchange-specific fields
- **Validation metadata**: is_valid + validation_errors for data quality tracking

### Gold Tables Schema (OHLCV - 6 Timeframes)

**Example: `cold.gold_ohlcv_1m`**

```sql
CREATE TABLE cold.gold_ohlcv_1m (
    exchange STRING,
    canonical_symbol STRING,
    window_start TIMESTAMP,
    open_time TIMESTAMP,
    open_price DECIMAL(38, 8),
    close_time TIMESTAMP,
    close_price DECIMAL(38, 8),
    high_price DECIMAL(38, 8),
    low_price DECIMAL(38, 8),
    volume DECIMAL(38, 8),
    quote_volume DECIMAL(38, 8),
    trade_count BIGINT
)
USING iceberg
PARTITIONED BY (months(window_start), exchange)
LOCATION 's3a://k2-data/warehouse/cold/gold/gold_ohlcv_1m'
TBLPROPERTIES (
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.metadata.metrics.default' = 'full'
);
```

**Design Rationale:**
- **Partitioned by months(window_start)**: OHLCV queries typically span weeks/months
- **Partitioned by exchange**: Isolates exchanges for query efficiency
- **Pre-aggregated**: Stores final OHLCV (not intermediate states)

---

## Technical Deep-Dive: Kotlin Offload Service

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Kotlin Iceberg Offload Service                         │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  @Scheduled(cron = "0 5 * * * *")                │   │
│  │  fun offloadAllLayers() {                        │   │
│  │    val hourAgo = now().minusHours(1)             │   │
│  │                                                   │   │
│  │    // Bronze layers (per-exchange)               │   │
│  │    offloadBronze("binance", hourAgo)             │   │
│  │    offloadBronze("kraken", hourAgo)              │   │
│  │                                                   │   │
│  │    // Silver layer (unified)                     │   │
│  │    offloadSilver(hourAgo)                        │   │
│  │                                                   │   │
│  │    // Gold layers (6 timeframes)                 │   │
│  │    offloadGold("1m", hourAgo)                    │   │
│  │    offloadGold("5m", hourAgo)                    │   │
│  │    // ... (15m, 30m, 1h, 1d)                     │   │
│  │  }                                               │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  ClickHouseReader                                │   │
│  │  - SELECT * FROM table WHERE ts >= ? AND ts < ? │   │
│  │  - Batch size: 10K rows                          │   │
│  │  - Timeout: 30s                                  │   │
│  └──────────────────────────────────────────────────┘   │
│              ↓                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │  IcebergWriter                                   │   │
│  │  - Convert ClickHouse ResultSet → Parquet        │   │
│  │  - Write to MinIO (Zstd compression)             │   │
│  │  - Update Iceberg catalog (REST API)             │   │
│  │  - Commit transaction                            │   │
│  └──────────────────────────────────────────────────┘   │
│              ↓                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Metrics (Prometheus)                            │   │
│  │  - offload_rows_total{table, exchange}           │   │
│  │  - offload_duration_seconds{table}               │   │
│  │  - offload_errors_total{table, error_type}       │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Key Implementation Decisions

**Decision 2026-02-11: Standalone Sidecar (Not Embedded in API)**
**Reason:** Operational isolation - offload failures don't impact API queries
**Cost:** One additional container (0.25 CPU / 256 MB)
**Alternative:** Embed in Spring Boot API via @Scheduled (rejected - blast radius)

**Decision 2026-02-11: Hourly (Not Real-Time CDC)**
**Reason:** ~1 hour cold freshness sufficient for analytics, simpler than CDC
**Cost:** Cold data lags warm by up to 1 hour
**Alternative:** Debezium CDC (rejected - operational complexity, resource intensive)

**Decision 2026-02-11: Sequential (Not Parallel) Offload**
**Reason:** Avoid overwhelming ClickHouse with concurrent SELECTs
**Cost:** Offload takes ~10 min/hour (sequential) vs ~2 min (parallel)
**Alternative:** Parallel offload (rejected - risk of query throttling)

---

## Technical Deep-Dive: Spark Batch Maintenance

### Compaction Job

**Purpose:** Merge hourly small Parquet files into optimal sizes (128-512 MB)

```scala
// IcebergCompaction.scala
object IcebergCompaction {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("Iceberg Compaction")
      .config("spark.sql.catalog.cold", "org.apache.iceberg.spark.SparkCatalog")
      .config("spark.sql.catalog.cold.type", "rest")
      .config("spark.sql.catalog.cold.uri", "http://iceberg-rest:8181")
      .getOrCreate()

    val tables = Seq(
      "cold.bronze_trades_binance",
      "cold.bronze_trades_kraken",
      "cold.silver_trades",
      "cold.gold_ohlcv_1m",
      "cold.gold_ohlcv_5m",
      "cold.gold_ohlcv_15m",
      "cold.gold_ohlcv_30m",
      "cold.gold_ohlcv_1h",
      "cold.gold_ohlcv_1d"
    )

    tables.foreach { table =>
      println(s"Compacting $table...")
      spark.sql(s"""
        CALL cold.system.rewrite_data_files(
          table => '$table',
          strategy => 'binpack',
          options => map(
            'target-file-size-bytes', '134217728',  -- 128 MB
            'min-file-size-bytes', '10485760'       -- 10 MB threshold
          )
        )
      """)
    }

    spark.stop()
  }
}
```

**Expected Outcome:**
- Before: 24 files/day per table (hourly offload) → 24 * 9 = 216 files/day
- After: ~2-3 files/day per table → ~25 files/day total
- Reduction: 90% fewer files (improves query performance)

### Snapshot Expiry Job

**Purpose:** Remove snapshots older than 7 days to prevent metadata bloat

```scala
object SnapshotExpiry {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("Snapshot Expiry")
      .getOrCreate()

    val tables = Seq(/* ... same 9 tables ... */)

    tables.foreach { table =>
      spark.sql(s"""
        CALL cold.system.expire_snapshots(
          table => '$table',
          older_than => TIMESTAMP '${LocalDateTime.now().minusDays(7)}',
          retain_last => 10  -- Always keep at least 10 snapshots
        )
      """)
    }
  }
}
```

### Data Quality Audit Job

**Purpose:** Verify row counts match between ClickHouse (warm) and Iceberg (cold)

```scala
object DataQualityAudit {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("Data Quality Audit")
      .getOrCreate()

    // Connect to ClickHouse via JDBC
    val chProps = new Properties()
    chProps.put("user", "default")
    chProps.put("password", "")

    val clickhouseUrl = "jdbc:clickhouse://k2-clickhouse:8123/k2"

    // Bronze Binance
    auditTable(spark, clickhouseUrl, chProps,
      "bronze_trades_binance", "cold.bronze_trades_binance")

    // Silver
    auditTable(spark, clickhouseUrl, chProps,
      "silver_trades", "cold.silver_trades")

    // Gold (6 timeframes)
    // ...
  }

  def auditTable(spark: SparkSession, chUrl: String, props: Properties,
                 chTable: String, icebergTable: String): Unit = {
    // Count ClickHouse rows (older than 1 hour, should be in cold tier)
    val chCount = spark.read.jdbc(chUrl, s"""
      (SELECT count() as cnt FROM $chTable
       WHERE timestamp < now() - INTERVAL 1 HOUR) AS subq
    """, props).first().getLong(0)

    // Count Iceberg rows
    val icebergCount = spark.table(icebergTable).count()

    val diff = Math.abs(chCount - icebergCount)
    val diffPct = diff.toDouble / Math.max(chCount, 1) * 100

    println(s"Audit: $chTable | CH: $chCount | Iceberg: $icebergCount | Diff: $diff ($diffPct%)")

    if (diffPct > 1.0) {
      println(s"WARNING: Mismatch exceeds 1% threshold!")
      // Publish metric to Prometheus Pushgateway
      publishAuditAlert(chTable, chCount, icebergCount, diffPct)
    }
  }
}
```

---

## Resource Budget Impact

### Before Phase 5

```
Service                    CPU      RAM      Status
k2-clickhouse              0.5      2.0 GB   Running
k2-redpanda                0.3      1.5 GB   Running
k2-feed-handler-binance    0.034    134 MB   Running
k2-feed-handler-kraken     0.0025   128 MB   Running
k2-grafana                 0.1      256 MB   Running
k2-prometheus              0.1      512 MB   Running
k2-redpanda-console        0.1      34 MB    Running

MinIO                      1.0      2.0 GB   Running (oversized for cold)
PostgreSQL (catalog)       1.0      1.0 GB   Running (oversized for cold)
Iceberg REST               1.0      1.0 GB   Running (oversized for cold)

──────────────────────────────────────────────────
TOTAL                      ~5.2     ~9.5 GB
```

### After Phase 5

```
Service                    CPU      RAM      Status
k2-clickhouse              0.5      2.0 GB   Running
k2-redpanda                0.3      1.5 GB   Running
k2-feed-handler-binance    0.034    134 MB   Running
k2-feed-handler-kraken     0.0025   128 MB   Running
k2-grafana                 0.1      256 MB   Running
k2-prometheus              0.1      512 MB   Running
k2-redpanda-console        0.1      34 MB    Running

MinIO                      0.5      1.0 GB   Running (optimized for cold)
PostgreSQL (catalog)       0.5      512 MB   Running (optimized for cold)
Iceberg REST               0.5      512 MB   Running (optimized for cold)
Iceberg Offload (Kotlin)   0.25     256 MB   Running (new sidecar)

Spark Batch                2.0      4.0 GB   Ephemeral (~1h/day at 02:00 UTC)

──────────────────────────────────────────────────
TOTAL (steady-state)       ~4.2     ~7.9 GB  (-1.0 CPU / -1.6 GB savings)
TOTAL (during batch)       ~6.2     ~11.9 GB (Spark running)
```

**Savings Analysis:**
- **Steady-state:** -1.0 CPU / -1.6 GB (MinIO, PostgreSQL, Iceberg REST optimized)
- **Spark:** +2.0 CPU / +4.0 GB but only ~4% duty cycle (1h/day out of 24h)
- **Effective daily CPU-hours:** Before: 72 CPU-h | After: 100.8 CPU-h (Spark 2 CPU * 1h)
- **Net increase:** ~28.8 CPU-hours/day (acceptable for cold tier value)

**Budget Compliance:**
- Target: 16 CPU / 40 GB
- Actual steady-state: ~4.2 CPU / ~7.9 GB ✅ (74% under budget)
- Actual during batch: ~6.2 CPU / ~11.9 GB ✅ (61% under budget)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Hourly offload fails, data lost after ClickHouse TTL | Medium | **High** | ClickHouse TTL buffer (30 days), retry logic, alerting, dual-write period for validation |
| Iceberg schema incompatible with ClickHouse | Low | Medium | Careful DDL design, validation tests, schema evolution support |
| Cold queries too slow for analytics | Medium | Low | Iceberg predicate pushdown, partitioning, compaction, acceptable latency for historical |
| MinIO storage growth exceeds budget | Low | Medium | Zstd compression (10-20x), snapshot expiry, monitoring alerts |
| Spark batch job failure blocks compaction | Low | Medium | Retries, manual trigger procedure, compaction is optimization not critical path |
| Kotlin offload service memory leak | Low | Medium | JVM heap monitoring, restart policy, structured logging for debugging |

---

## Success Criteria (Definition of Done)

- [ ] 9 Iceberg tables created (Bronze: 2, Silver: 1, Gold: 6)
- [ ] Partitioning validated (check metadata, file layout)
- [ ] Kotlin offload service operational (hourly cron)
- [ ] Data flowing: ClickHouse → Iceberg (all 9 tables)
- [ ] Row counts match: ClickHouse vs Iceberg (< 1% difference)
- [ ] Spot check: 1000 random trades match exactly
- [ ] Spark daily maintenance operational (compaction + expiry + audit)
- [ ] Compaction reduces file count by 70-90%
- [ ] ClickHouse federated queries working (warm + cold)
- [ ] Cold query latency acceptable (<5s p99 for historical)
- [ ] Resource optimization: -1.5 CPU / -2GB ✅
- [ ] Monitoring dashboard created (Grafana)
- [ ] Alerts configured (offload failure, audit mismatch, disk usage)
- [ ] Runbooks created and tested
- [ ] Git tag: `v2-phase-5-complete`

---

## Timeline Estimate

| Week | Focus | Deliverables |
|------|-------|-------------|
| Week 1, Days 1-3 | Step 1: Iceberg DDL | 9 tables created, validated |
| Week 1, Days 4-5 + Week 2, Days 1-2 | Step 2: Kotlin Offload | Service operational, data flowing |
| Week 2, Days 3-4 | Step 3: Spark Maintenance | Compaction, expiry, audit working |
| Week 2, Day 5 + Week 3, Day 1 | Step 4: Validation | End-to-end testing, consistency checks |
| Week 3, Day 2 | Step 5: Hardening | Resource optimization, monitoring, runbooks |

**Total:** 2 weeks (10 working days)

---

## Next Immediate Actions

1. **Create Iceberg catalog schema** (PostgreSQL DDL)
2. **Create Iceberg table DDL** (Spark SQL - 9 tables)
3. **Set up Kotlin service project structure** (Gradle, dependencies)
4. **Design Iceberg Writer class** (Parquet + catalog update)
5. **Update docker-compose.v2.yml** (add Iceberg offload service)

---

**Prepared By:** Platform Engineering (Staff Level)
**Date:** 2026-02-11
**Status:** Planning Complete - Ready for Implementation
**Review:** Phase 5 README.md and step files to be updated next
