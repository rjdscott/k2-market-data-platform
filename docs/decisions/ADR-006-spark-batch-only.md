# ADR-006: Retain Spark for Batch Processing Only

**Status:** Proposed
**Date:** 2026-02-09
**Decision Makers:** Platform Engineering Team
**Category:** Batch Processing

---

## Context

With the elimination of Spark Structured Streaming (ADR-004), the question arises: should Spark be retained at all?

The platform still requires batch processing for:
1. **Hourly warm-to-cold tiering**: Export ClickHouse partitions (all 4 medallion layers) to Iceberg every hour
2. **Historical backfills**: Reprocess historical data when schema evolves
3. **Data quality audits**: Cross-reference ClickHouse warm data against Iceberg cold data
4. **Complex analytical transforms**: Ad-hoc queries that span warm + cold tiers
5. **Iceberg table maintenance**: Compaction, snapshot expiry, orphan file cleanup

The hourly offload requirement changes the equation: spinning up Spark 24 times per day (10-15s startup each time) is wasteful. The better approach is a **two-tier batch strategy**: a lightweight Kotlin Iceberg writer for frequent hourly offloads, and Spark for heavy periodic maintenance.

## Decision

**Use a two-tier batch strategy:**
1. **Hourly offload**: Kotlin service using the Apache Iceberg Java SDK appends hourly partitions from ClickHouse to Iceberg. Runs as a lightweight scheduled task within the existing Spring Boot API or as a standalone sidecar.
2. **Daily maintenance**: Spark batch (on-demand, ephemeral) handles compaction, snapshot expiry, data quality audits, and large backfills.

Configuration:
- Kotlin Iceberg writer: embedded in Spring Boot API via `@Scheduled`, or standalone 0.25 CPU / 256MB sidecar
- Spark batch: 2 CPU / 4GB, triggered by cron, runs once daily for maintenance
- Container exits after job completion (ephemeral)

## Rationale

### Why Keep Spark at All

| Alternative | Suitable for Tiering? | Suitable for Backfill? | Ecosystem | Verdict |
|-------------|----------------------|----------------------|-----------|---------|
| Spark (batch) | Yes — native Iceberg writer | Yes — distributed scan | Iceberg, Parquet, S3 | **Selected** |
| Kotlin + Iceberg SDK | Partial — manual Parquet writing | Slow for large datasets | Limited | Too low-level |
| ClickHouse S3 export | Basic CSV/Parquet export | No Iceberg metadata | No catalog integration | Insufficient |
| DuckDB | Yes — Iceberg extension | Moderate (single-threaded) | Growing but limited | Viable alternative |
| Trino/Presto | Yes — excellent Iceberg | Heavy (always-on coordinator) | Mature | Too heavy for batch |

Spark remains the **gold standard for Iceberg writes** with full support for:
- ACID transactions
- Schema evolution
- Partition evolution
- Snapshot management
- Time-travel queries
- Compaction (rewriting small files into optimally-sized ones)

### Batch-Only vs Always-On Resource Profile

| Configuration | CPU | RAM | Availability |
|---------------|-----|-----|-------------|
| Always-on (current) | 14.0 | 20GB | 24/7 |
| Batch-only (proposed) | 2.0 | 4GB | ~2h/day |
| **Effective daily CPU-hours** | **336** | **480GB-h** | — |
| **Effective daily CPU-hours** | **4** | **8GB-h** | — |

**Effective resource reduction: 98.8% CPU-hours, 98.3% RAM-hours.**

When Spark is not running, its CPU and RAM are available to ClickHouse, the API, and other services.

### Two-Tier Batch Schedule

#### Tier 1: Hourly Offload (Kotlin Iceberg Writer)

| Job | Frequency | Duration | Purpose |
|-----|-----------|----------|---------|
| Offload raw_trades | Hourly :05 | ~1 min | ClickHouse → cold.raw_trades (last hour) |
| Offload bronze_trades | Hourly :06 | ~1 min | ClickHouse → cold.bronze_trades (last hour) |
| Offload silver_trades | Hourly :07 | ~2 min | ClickHouse → cold.silver_trades (last hour) |
| Offload gold_ohlcv_* | Hourly :09 | ~2 min | ClickHouse → cold.gold_ohlcv_{1m,5m,15m,30m,1h,1d} |

**Total hourly overhead**: ~6 minutes, lightweight (pure Kotlin, no Spark JVM).

#### Tier 2: Daily Maintenance (Spark Batch)

| Job | Frequency | Duration | Purpose |
|-----|-----------|----------|---------|
| Iceberg compaction | Daily 02:00 UTC | ~15 min | Merge hourly small files into optimal sizes |
| Snapshot expiry | Daily 02:20 UTC | ~5 min | Remove snapshots older than 7 days |
| Data quality audit | Weekly Sun 03:00 | ~20 min | Cross-reference warm vs cold counts per layer |
| Backfill/re-derive | On-demand | Varies | Reprocess historical data when logic changes |

### Why Kotlin for Hourly, Spark for Daily

| Operation | Kotlin Iceberg SDK | Spark | Best choice |
|-----------|-------------------|-------|-------------|
| Append hourly partition (small files) | Fast, <1min | 10-15s startup overhead | **Kotlin** |
| Compaction (rewrite many files) | Slow (single-threaded) | Fast (parallel) | **Spark** |
| Schema evolution writes | Supported | Better tooling | **Spark** |
| Large backfills (millions of rows) | Slow | Fast (distributed scan) | **Spark** |
| Resource cost | ~0 (embedded in API) | 2 CPU / 4GB per invocation | **Kotlin** |

The Iceberg Java SDK is a library — it writes Parquet files to MinIO and updates the Iceberg catalog via REST, all from within a JVM process. No Spark overhead required for simple appends.

### Kotlin Hourly Offload Implementation

```kotlin
@Component
class IcebergOffloadScheduler(
    private val clickhouse: JdbcTemplate,
    private val catalog: RESTCatalog,
    private val metrics: MeterRegistry
) {
    @Scheduled(cron = "0 5 * * * *")  // :05 past every hour
    fun offloadAllLayers() {
        val hourStart = Instant.now().truncatedTo(ChronoUnit.HOURS).minus(1, ChronoUnit.HOURS)
        val hourEnd = hourStart.plus(1, ChronoUnit.HOURS)

        offloadLayer("trades_raw", "cold.raw_trades", "ingested_at", hourStart, hourEnd)
        offloadLayer("bronze_trades", "cold.bronze_trades", "timestamp", hourStart, hourEnd)
        offloadLayer("silver_trades", "cold.silver_trades", "timestamp", hourStart, hourEnd)

        listOf("1m", "5m", "15m", "30m", "1h", "1d").forEach { tf ->
            offloadOhlcv(tf, hourStart, hourEnd)
        }
    }

    private fun offloadLayer(
        chTable: String, icebergTable: String,
        tsColumn: String, start: Instant, end: Instant
    ) {
        // Read hourly partition from ClickHouse
        val rows = clickhouse.queryForList(
            "SELECT * FROM $chTable WHERE $tsColumn >= ? AND $tsColumn < ?",
            start, end
        )
        if (rows.isEmpty()) return

        // Write to Iceberg via Java SDK (Parquet + catalog update)
        val table = catalog.loadTable(TableIdentifier.parse(icebergTable))
        val appender = table.newAppend()
        val dataFile = writeParquet(table.schema(), rows, table.location())
        appender.appendFile(dataFile)
        appender.commit()

        metrics.counter("offload.rows", "table", icebergTable)
            .increment(rows.size.toDouble())
    }
}
```

### Spark Daily Maintenance

```yaml
# docker-compose.v1.yml
spark-batch:
  image: k2/spark-batch:latest
  profiles: ["batch"]  # Only starts when explicitly invoked
  deploy:
    resources:
      limits:
        cpus: "2.0"
        memory: 4G
  command: >
    spark-submit --master local[2]
    --conf spark.sql.catalog.k2=org.apache.iceberg.spark.SparkCatalog
    --class com.k2.batch.IcebergMaintenance
    /app/batch-jobs.jar
```

### Spark Configuration (Batch-Optimized)

```properties
# Batch-only Spark config (no streaming overhead)
spark.master=local[2]
spark.driver.memory=1g
spark.executor.memory=2g
spark.sql.adaptive.enabled=true
spark.sql.adaptive.coalescePartitions.enabled=true

# Iceberg catalog
spark.sql.catalog.k2=org.apache.iceberg.spark.SparkCatalog
spark.sql.catalog.k2.type=rest
spark.sql.catalog.k2.uri=http://iceberg-rest:8181

# No checkpoint needed (batch, not streaming)
# No continuous processing overhead
# No micro-batch scheduling
```

## Alternatives Considered

### 1. Eliminate Spark Entirely — Use DuckDB for Batch
- **Pro**: Already familiar from v1; lightweight; can read/write Iceberg
- **Con**: Single-threaded — large backfills would take hours vs minutes
- **Con**: DuckDB Iceberg writes are less mature than Spark's
- **Con**: No ACID write support for Iceberg (as of 2025)
- **Verdict**: Viable for small datasets; keep Spark for production-grade Iceberg writes

### 2. Eliminate Spark — Use ClickHouse S3 Table Function
- **Pro**: ClickHouse can export directly to S3/MinIO in Parquet format
- **Con**: No Iceberg metadata (manifests, snapshots) — just raw Parquet files
- **Con**: Loses time-travel, schema evolution, compaction
- **Verdict**: Rejected — defeats the purpose of Iceberg

### 3. Use Trino Instead of Spark
- **Pro**: Excellent Iceberg support, SQL-native
- **Con**: Designed as always-on query engine (Coordinator + Worker)
- **Con**: 2-4GB RAM floor for coordinator alone
- **Con**: Not designed for batch ETL — optimized for interactive queries
- **Verdict**: Rejected — wrong tool for batch ETL

### 4. Use Apache Sedona or Spark-on-K8s Operator
- **Pro**: Better resource management on Kubernetes
- **Con**: We're targeting Docker Compose, not Kubernetes
- **Verdict**: Not applicable to our deployment model

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Spark batch job fails, data not tiered | Medium | Medium | Retry logic, alerting, ClickHouse TTL provides buffer (data not lost immediately) |
| Resource contention during batch window | Low | Medium | Schedule during low-traffic hours (02:00 UTC); Docker resource limits prevent starvation |
| Spark version drift from ClickHouse/Iceberg | Low | Low | Pin versions; test compatibility in CI |
| Batch job takes longer than expected | Low | Medium | Timeout limits; monitor job duration metrics |

## Resource Budget Impact

```
Before: Spark always-on (14.0 CPU / 20GB)
After:  Spark batch-only (2.0 CPU / 4GB, ~2h/day)

Savings: 12.0 CPU / 16GB (permanently available to other services)
When running: temporarily claims 2 CPU / 4GB from shared pool
```

## Consequences

### Positive
- 12 CPU / 16GB freed for other services permanently
- Spark only runs when needed — predictable resource consumption
- Retains gold-standard Iceberg write support
- Simpler operational model (no Master/Worker management)
- Batch jobs are idempotent and retriable

### Negative
- Cold storage data is ~1 hour behind warm (hourly offload)
- Two batch mechanisms to maintain (Kotlin hourly + Spark daily)
- Spark container must be maintained and versioned
- Hourly small files in Iceberg require daily compaction (handled by Spark)

## References

- [Apache Spark on Docker](https://spark.apache.org/docs/latest/)
- [Iceberg Spark Integration](https://iceberg.apache.org/docs/latest/spark-getting-started/)
- [Iceberg Maintenance Procedures](https://iceberg.apache.org/docs/latest/maintenance/)
- [Docker Compose Profiles](https://docs.docker.com/compose/profiles/)
