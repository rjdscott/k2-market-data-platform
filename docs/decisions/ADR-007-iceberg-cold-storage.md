# ADR-007: Retain Apache Iceberg for Cold Storage Layer

**Status:** Proposed
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Cold Storage

---

## Context

The current platform uses Apache Iceberg 1.4 as its primary analytical storage format, with MinIO providing S3-compatible object storage and PostgreSQL hosting the Iceberg REST catalog metadata. Iceberg stores all medallion architecture layers (Bronze, Silver, Gold, Gold Analytics).

In the v2 architecture, ClickHouse takes over as the warm storage layer for recent data (0-30 days). The question is: what happens to data older than 30 days?

Options:
1. Keep everything in ClickHouse (no cold tier)
2. Use Iceberg on MinIO as the cold tier (retain current approach)
3. Use raw Parquet on MinIO (no table format)
4. Use Delta Lake or Apache Hudi instead of Iceberg

## Decision

**Retain Apache Iceberg on MinIO as the cold storage layer. Mirror the full four-layer ClickHouse medallion structure (Raw, Bronze, Silver, Gold) in Iceberg so that data lineage is preserved end-to-end across tiers. Offload hourly (not daily) for near-real-time cold tier freshness.**

The Iceberg layer stores:
- **Raw**: Historical untouched exchange payloads — immutable audit trail, byte-for-byte provenance
- **Bronze**: Historical typed/deduped trade data — enables re-derivation of Silver
- **Silver**: Historical validated/normalized trades — enables re-aggregation of Gold
- **Gold**: Historical OHLCV candles (1m, 5m, 15m, 30m, 1h, 1d) — historical analytical queries
- Schema evolution history at every layer

## Rationale

### Why a Cold Tier Is Necessary

ClickHouse is optimized for recent, frequently-queried data. Storing years of market data in ClickHouse would:

| Metric | 30-day retention | 1-year retention | 5-year retention |
|--------|-----------------|-----------------|-----------------|
| ClickHouse storage | ~5GB (compressed) | ~60GB | ~300GB |
| ClickHouse RAM for indices | ~500MB | ~6GB | ~30GB |
| Query performance (recent) | Excellent | Degraded | Severely degraded |
| Merge overhead | Low | High | Very high |

ClickHouse's MergeTree engine degrades as data volume grows because:
- Part merges compete with query workloads
- Index memory grows linearly with data volume
- Partition management becomes operationally complex

**30-day warm window + Iceberg cold tier** provides the optimal cost/performance balance.

### Why Iceberg (vs. Alternatives)

#### vs. Raw Parquet on S3/MinIO

| Feature | Raw Parquet | Iceberg |
|---------|------------|---------|
| ACID transactions | No | Yes |
| Schema evolution | Manual | Automatic (add/rename/drop columns) |
| Partition evolution | Requires rewrite | In-place (hidden partitioning) |
| Time-travel | No | Yes (snapshot isolation) |
| File-level metadata | No | Manifest files with column stats |
| Predicate pushdown | File-level only | Row-group + file-level with min/max stats |
| Compaction | Manual | Built-in (rewrite-data-files) |
| Concurrent writes | Unsafe | Safe (optimistic concurrency) |

Iceberg provides **significantly better query performance and data governance** than raw Parquet files.

#### vs. Delta Lake

| Feature | Delta Lake | Iceberg |
|---------|-----------|---------|
| Governance | Databricks-controlled | Apache Foundation (vendor-neutral) |
| Engine support | Best on Spark/Databricks | Spark, Flink, Trino, DuckDB, ClickHouse |
| ClickHouse integration | Limited | [Native support (experimental)](https://clickhouse.com/docs/en/engines/table-engines/integrations/iceberg) |
| Hidden partitioning | No (user must know partition scheme) | Yes (automatic) |
| Merge-on-read | Yes | Yes (v2) |
| Community momentum | Strong | Stronger (broader adoption outside Databricks) |

Iceberg has **broader engine support** and is the industry standard for open table formats.

#### vs. Apache Hudi

| Feature | Hudi | Iceberg |
|---------|------|---------|
| Primary use case | CDC / upserts | Analytics / append-heavy |
| Complexity | Higher (index management) | Lower |
| Our workload | Append-only trades | **Iceberg is better fit** |
| Engine support | Spark + Flink | Spark + Flink + Trino + DuckDB + ClickHouse |

Hudi is optimized for **change data capture** (updates/deletes). Our market data workload is **append-only** — Iceberg is the natural fit.

### Iceberg + ClickHouse Integration

ClickHouse has experimental support for reading Iceberg tables directly:

```sql
-- ClickHouse can read from Iceberg for historical queries
SELECT *
FROM iceberg('http://minio:9000/warehouse/gold/trades/', 'minioadmin', 'minioadmin')
WHERE timestamp >= '2025-01-01' AND timestamp < '2025-02-01'
AND symbol = 'BTCUSDT';
```

This enables **federated queries** that span warm (ClickHouse native) + cold (Iceberg) tiers from a single SQL interface.

### Cold Tier Schema — Mirrored Medallion Structure

The cold tier mirrors the exact four-layer ClickHouse medallion structure. This preserves full data lineage and enables re-derivation at any layer.

```
Iceberg Tables (Cold Storage — mirrors ClickHouse four-layer medallion):

RAW (untouched exchange payloads)
├── cold.raw_trades                    # Byte-for-byte original data
│   ├── Partitioned by: hour(ingested_at), exchange
│   ├── Sorted by: ingested_at, _offset
│   ├── Format: Parquet + Zstd compression
│   └── Use case: Immutable audit trail; re-derive Bronze if type
│                  casting logic changes; regulatory provenance
│
BRONZE (typed, deduplicated)
├── cold.bronze_trades                 # Type-cast, timestamp-normalized
│   ├── Partitioned by: day(timestamp), exchange
│   ├── Sorted by: symbol, timestamp, trade_id
│   ├── Format: Parquet + Zstd compression
│   └── Use case: Re-derive Silver if validation logic changes
│
SILVER (validated, normalized)
├── cold.silver_trades                 # Business-validated, canonical symbols
│   ├── Partitioned by: day(timestamp), exchange
│   ├── Sorted by: symbol, timestamp, trade_id
│   ├── Format: Parquet + Zstd compression
│   └── Use case: Re-derive Gold if aggregation logic changes;
│                  historical trade queries
│
GOLD (pre-computed analytics — 6 timeframes)
├── cold.gold_ohlcv_1m                 # Historical 1-minute candles
├── cold.gold_ohlcv_5m                 # Historical 5-minute candles
├── cold.gold_ohlcv_15m                # Historical 15-minute candles
├── cold.gold_ohlcv_30m                # Historical 30-minute candles
├── cold.gold_ohlcv_1h                 # Historical 1-hour candles
├── cold.gold_ohlcv_1d                 # Historical daily candles
│   ├── Partitioned by: month(window_start), exchange
│   ├── Sorted by: symbol, window_start
│   ├── Format: Parquet + Zstd compression
│   └── Use case: Historical analytical queries, charting
```

### Why Mirror the Full Medallion (Not Just Archive Gold)

| Scenario | Flat archive (Gold only) | Mirrored four-layer | Winner |
|----------|--------------------------|---------------------|--------|
| Type casting bug in Bronze | Cannot investigate — originals lost | Compare cold.raw_trades vs cold.bronze_trades | **Mirrored** |
| Validation logic changes | Must re-ingest from exchange APIs | Re-derive Silver from cold.bronze_trades | **Mirrored** |
| Add new OHLCV field (e.g., VWAP) | Must re-ingest + re-validate + re-aggregate | Re-derive Gold from cold.silver_trades | **Mirrored** |
| Regulatory audit ("show original data") | Unavailable — raw data lost | Query cold.raw_trades (untouched) | **Mirrored** |
| Storage cost | ~1x | ~3-4x (4 copies) | Flat |
| Operational complexity | Low | Medium | Flat |

The re-derivability advantage is critical for a market data platform where:
- Exchange API rate limits make historical re-ingestion slow or impossible
- Regulatory audits may require original (untouched) data provenance
- Type casting, validation, and aggregation logic all evolve as the platform matures

**Storage cost trade-off**: At 10-20x columnar compression, storing 4 layers of a year of crypto trades costs ~20-30GB on MinIO. This is negligible vs the value of full lineage re-derivability.

### Warm-to-Cold Tiering: Hourly Offload + Daily Maintenance

Each ClickHouse layer offloads hourly to its Iceberg counterpart via a lightweight Kotlin Iceberg writer (see ADR-006 revised). Spark runs daily for compaction and maintenance only.

```
HOURLY (Kotlin Iceberg writer — embedded in API service):
  :05  Offload trades_raw (last hour)     → cold.raw_trades
  :06  Offload bronze_trades (last hour)  → cold.bronze_trades
  :07  Offload silver_trades (last hour)  → cold.silver_trades
  :09  Offload gold_ohlcv_* (last hour)   → cold.gold_ohlcv_{1m,5m,15m,30m,1h,1d}
  Total: ~6 min/hour, negligible resource impact

DAILY (Spark batch — on-demand, ephemeral):
  02:00 UTC  Iceberg compaction: merge hourly small Parquet files into optimal sizes
  02:20 UTC  Snapshot expiry: remove snapshots older than 7 days
  02:30 UTC  Data quality audit: verify row counts match across all 4 layers
  02:40 UTC  Spark exits
```

**Why hourly**: 1-hour cold tier freshness means historical queries return data that's at most ~1 hour stale (vs 24 hours with daily batch). This is critical for near-real-time compliance reporting and end-of-day analytics that may span the warm/cold boundary.

**Why Kotlin for hourly, Spark for daily**: The Iceberg Java SDK can append Parquet files and update the catalog from within any JVM process. Hourly appends are small (a few MB per table) — no need for Spark's distributed execution. Spark is reserved for compaction (rewriting many small files into fewer large ones), which is I/O-heavy and benefits from parallelism.

### Resource Profile (Reduced Role)

| Component | v1 (All Layers) | v2 (Cold Only) | Savings |
|-----------|-----------------|----------------|---------|
| MinIO | 1.0 CPU / 2GB | 0.5 CPU / 1GB | **50%** |
| PostgreSQL (catalog) | 1.0 CPU / 1GB | 0.5 CPU / 512MB | **50%** |
| Iceberg REST | 1.0 CPU / 1GB | 0.5 CPU / 512MB | **50%** |
| **Total** | **3.0 CPU / 4GB** | **1.5 CPU / 2GB** | **50%** |

With Iceberg handling only cold data (queried infrequently), resource allocations can be halved.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Iceberg queries slower than ClickHouse | Expected | Low | Cold data is infrequently queried; acceptable latency for historical analysis |
| Warm-to-cold tiering data loss | Low | High | Dual-write period; verify Iceberg row counts match ClickHouse before TTL expiry |
| Iceberg REST catalog downtime | Low | Medium | PostgreSQL replication; catalog metadata is small and easily backed up |
| MinIO storage growth | Medium | Low | Iceberg compaction + snapshot expiry reduce file count; Zstd compression achieves 10-20x |

## Resource Budget Impact

```
Before: MinIO (1.0 CPU/2GB) + PostgreSQL (1.0 CPU/1GB) + Iceberg REST (1.0 CPU/1GB)
        = 3.0 CPU / 4GB

After:  MinIO (0.5 CPU/1GB) + PostgreSQL (0.5 CPU/512MB) + Iceberg REST (0.5 CPU/512MB)
        = 1.5 CPU / 2GB

Savings: 1.5 CPU / 2GB
```

## Consequences

### Positive
- Proven, mature cold storage with full ACID guarantees
- Time-travel for compliance and regulatory queries
- Schema evolution handles future data model changes gracefully
- ClickHouse can query Iceberg directly for cross-tier analytics
- Vendor-neutral open standard — no lock-in

### Negative
- Additional infrastructure (MinIO, PostgreSQL, Iceberg REST) vs. ClickHouse-only approach
- Warm-to-cold tiering adds pipeline complexity
- Two query paths (warm vs cold) require routing logic in the API layer
- Iceberg query latency higher than ClickHouse for same data

## References

- [Apache Iceberg Documentation](https://iceberg.apache.org/docs/latest/)
- [Iceberg Table Format Spec](https://iceberg.apache.org/spec/)
- [ClickHouse Iceberg Integration](https://clickhouse.com/docs/en/engines/table-engines/integrations/iceberg)
- [Iceberg vs Delta Lake vs Hudi](https://www.onehouse.ai/blog/apache-hudi-vs-delta-lake-vs-apache-iceberg)
- [Iceberg Hidden Partitioning](https://iceberg.apache.org/docs/latest/partitioning/)
