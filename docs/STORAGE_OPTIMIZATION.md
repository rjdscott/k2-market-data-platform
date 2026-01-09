# Storage Optimization & Cost Analysis

**Last Updated**: 2026-01-09
**Owners**: Platform Team, Data Engineering
**Status**: Implementation Plan
**Scope**: Write amplification, compaction, lifecycle policies, cost optimization

---

## Overview

Storage is the largest cost center for market data platforms. At scale, inefficient storage strategies can cost millions annually. This document analyzes write amplification, compaction strategies, and cost optimization techniques for Iceberg + S3.

**Design Philosophy**: Optimize for the common case (analytical queries), not the edge case (single-row lookups). Storage is cheap, but waste compounds at petabyte scale.

---

## Write Amplification Analysis

### What Is Write Amplification?

**Definition**: Ratio of bytes written to storage vs. bytes of actual data

**Formula**: `Write Amplification = Total Bytes Written / Data Size`

**Example**:
- User writes 1GB of market data
- Iceberg writes 1GB data file + 10KB manifest + 1KB snapshot
- Total written: 1.000011GB
- Write amplification: 1.000011x ✓ (good)

### Iceberg Write Pattern

**Per Write Operation**:
```
User writes 1GB batch
  ↓
1. Data file (Parquet): 1GB
2. Manifest file (metadata): 10KB
3. Manifest list: 1KB
4. Snapshot metadata: 1KB
5. PostgreSQL catalog update: 1KB (network)
  ↓
Total: 1.000013GB written
Write amplification: 1.000013x ✓
```

**Comparison to Raw Parquet**:
- Raw Parquet: 1.0x write amplification
- Iceberg: 1.000013x write amplification
- Overhead: 0.0013% (negligible)

**Verdict**: Iceberg write amplification is negligible for batch sizes > 100MB

---

### Small File Problem

**Scenario**: Writing many small files

```
User writes 1KB of data (single tick)
  ↓
1. Data file: 1KB
2. Manifest: 10KB    ← Overhead dominates!
3. Manifest list: 1KB
4. Snapshot: 1KB
  ↓
Total: 13KB written
Write amplification: 13x ✗ (bad)
```

**Problem**: Metadata overhead dominates for small writes

**Solution**: Batch writes (accumulate 1000 ticks before writing)

```python
class BufferedIcebergWriter:
    """
    Buffer writes to reduce small file overhead.

    Target: 512MB files (optimal for S3 + Parquet scanning)
    """

    def __init__(self, table_name: str, target_file_size_mb: int = 512):
        self.table_name = table_name
        self.target_file_size_bytes = target_file_size_mb * 1024 * 1024
        self.buffer = []
        self.buffer_size_bytes = 0

    def write(self, tick: dict):
        """
        Buffer tick and flush when target size reached.

        Args:
            tick: Market tick dictionary
        """
        self.buffer.append(tick)
        self.buffer_size_bytes += len(str(tick))  # Approximate size

        if self.buffer_size_bytes >= self.target_file_size_bytes:
            self.flush()

    def flush(self):
        """Write buffered ticks to Iceberg."""
        if not self.buffer:
            return

        from pyiceberg.catalog import load_catalog

        catalog = load_catalog('default')
        table = catalog.load_table(self.table_name)

        # Write batch
        table.append(self.buffer)

        # Clear buffer
        self.buffer = []
        self.buffer_size_bytes = 0

        log.info(f"Flushed {len(self.buffer)} ticks to {self.table_name}")
```

**Result**:
- File size: 512MB (target)
- Manifest: 10KB
- Write amplification: 1.00002x ✓ (negligible)

---

## Compaction Strategy

### Why Compaction Is Needed

**Problem**: Over time, small files accumulate

```
Day 1: Write 100 files × 5MB = 500MB total
Day 2: Write 100 files × 5MB = 500MB total
...
Day 30: 3,000 files × 5MB = 15GB total

Query: "SELECT * FROM ticks WHERE symbol = 'BHP'"
→ Scans 3,000 files (slow! S3 API overhead)
```

**Goal**: Merge small files into large files (512MB optimal)

```
After compaction:
30 files × 512MB = 15GB total

Query: "SELECT * FROM ticks WHERE symbol = 'BHP'"
→ Scans 30 files (fast!)
```

### Compaction Triggers

**Trigger 1: File Count per Partition > 100**
```python
def should_compact_partition(partition_path: str) -> bool:
    """
    Check if partition needs compaction.

    Triggers:
    1. File count > 100
    2. Average file size < 50MB
    3. File size variance > 10x (many small, few large)
    """
    files = list_partition_files(partition_path)

    if len(files) > 100:
        return True

    avg_size = sum(f.size_bytes for f in files) / len(files)
    if avg_size < 50 * 1024 * 1024:  # 50MB
        return True

    file_sizes = [f.size_bytes for f in files]
    size_variance = max(file_sizes) / min(file_sizes)
    if size_variance > 10:
        return True

    return False
```

**Trigger 2: Scheduled Compaction**
- Run daily at 2 AM (off-peak hours)
- Compact all partitions with > 50 files

### Compaction Algorithm

**Strategy**: Bin-packing to target file size

```python
def compact_partition(partition_path: str, target_size_mb: int = 512):
    """
    Compact partition files using bin-packing.

    Algorithm:
    1. Sort files by size (ascending)
    2. Group files into bins of target_size_mb
    3. Merge each bin into single file
    4. Atomic swap (Iceberg transaction)

    Args:
        partition_path: Partition to compact
        target_size_mb: Target file size (default: 512MB)
    """
    from pyiceberg.catalog import load_catalog

    catalog = load_catalog('default')
    table = catalog.load_table('market_data.ticks')

    # List files in partition
    files = table.scan(row_filter=f"partition = '{partition_path}'").plan_files()

    # Bin-packing: Group files into target size
    bins = []
    current_bin = []
    current_bin_size = 0

    for file in sorted(files, key=lambda f: f.file_size_in_bytes):
        if current_bin_size + file.file_size_in_bytes > target_size_mb * 1024 * 1024:
            # Bin full, start new bin
            bins.append(current_bin)
            current_bin = []
            current_bin_size = 0

        current_bin.append(file)
        current_bin_size += file.file_size_in_bytes

    # Add last bin
    if current_bin:
        bins.append(current_bin)

    # Merge each bin
    for i, bin_files in enumerate(bins):
        log.info(f"Compacting bin {i+1}/{len(bins)} ({len(bin_files)} files)")

        # Read all files in bin
        df = pd.concat([pd.read_parquet(f.file_path) for f in bin_files])

        # Write merged file
        new_file_path = f"{partition_path}/compacted_{i}.parquet"
        df.to_parquet(new_file_path, compression='zstd')

        # Atomic swap (replace old files with new)
        table.rewrite_files(
            old_files=[f.file_path for f in bin_files],
            new_file=new_file_path
        )

    log.info(f"Compaction complete: {len(files)} files → {len(bins)} files")
```

### Compaction Schedule

**Daily Compaction** (2 AM AEST):
```bash
# Cron job: 0 2 * * *
python scripts/compact_partitions.py \
  --table market_data.ticks \
  --target-size-mb 512 \
  --dry-run false
```

**Monitoring**:
```promql
# Files per partition (before/after compaction)
iceberg_files_per_partition{table="market_data.ticks"}

# Compaction runtime
iceberg_compaction_duration_seconds
```

---

## Read Amplification

### Query Performance Impact

**Scenario**: Query single symbol from 1 year of data

**Without Compaction**:
- Files: 365 days × 100 files/day = 36,500 files
- S3 API calls: 36,500 GET requests
- Time: 36,500 × 5ms = 182 seconds ✗

**With Compaction**:
- Files: 365 days × 1 file/day = 365 files
- S3 API calls: 365 GET requests
- Time: 365 × 5ms = 1.8 seconds ✓

**Improvement**: 100x faster queries after compaction

### Partition Pruning Effectiveness

**Query**:
```sql
SELECT * FROM ticks
WHERE symbol = 'BHP'
  AND exchange_timestamp BETWEEN '2026-01-09' AND '2026-01-10'
```

**Partition Strategy**: `PARTITIONED BY (days(exchange_timestamp), exchange, symbol_prefix)`

**Pruning Analysis**:
```
Total partitions: 8,000 symbols × 365 days = 2,920,000 partitions
Pruned to: 1 symbol × 1 day = 1 partition

Pruning ratio: 99.99997% ✓
```

**Files Scanned**:
- Before pruning: 2,920,000 files
- After pruning: 1 file (assuming compacted)

---

## Snapshot Expiration

### Snapshot Retention Policy

**Goal**: Delete old snapshots to reclaim storage

**Policy**: Retain snapshots for 90 days (compliance requirement)

```python
from datetime import datetime, timedelta
from pyiceberg.catalog import load_catalog

def expire_old_snapshots(table_name: str, retention_days: int = 90):
    """
    Expire snapshots older than retention period.

    Args:
        table_name: Table to expire snapshots from
        retention_days: Days to retain snapshots (default: 90)
    """
    catalog = load_catalog('default')
    table = catalog.load_table(table_name)

    # Calculate expiration timestamp
    expiration_ts = datetime.utcnow() - timedelta(days=retention_days)

    # Expire snapshots
    table.expire_snapshots(older_than=expiration_ts)

    log.info(f"Expired snapshots older than {expiration_ts} for {table_name}")

# Schedule: Weekly (Sunday 3 AM)
# Cron: 0 3 * * 0
expire_old_snapshots('market_data.ticks', retention_days=90)
```

### Orphan File Cleanup

**Problem**: Expired snapshots leave unreferenced files

```
Snapshot 1 → file_001.parquet
Snapshot 2 → file_002.parquet (overwrites snapshot 1)

After expiration:
Snapshot 1 expired → file_001.parquet orphaned (not deleted)
```

**Solution**: Periodic orphan file cleanup

```python
def remove_orphan_files(table_name: str):
    """
    Remove files not referenced by any snapshot.

    Runs after snapshot expiration.
    """
    from pyiceberg.catalog import load_catalog

    catalog = load_catalog('default')
    table = catalog.load_table(table_name)

    # Remove orphans (Iceberg finds unreferenced files)
    orphans_removed = table.remove_orphan_files()

    log.info(f"Removed {len(orphans_removed)} orphan files from {table_name}")

# Schedule: After snapshot expiration (Sunday 4 AM)
# Cron: 0 4 * * 0
remove_orphan_files('market_data.ticks')
```

**Space Savings**:
- Orphan files: ~10GB/day (old snapshots)
- Annual savings: 3.65TB

---

## Cost Analysis

### Storage Cost Breakdown

| Component | Daily Growth | 30-Day Total | Monthly Cost |
|-----------|--------------|--------------|--------------|
| **Raw Data (Parquet)** | 130GB | 3.9TB | $89 |
| **Iceberg Metadata** | 100MB | 3GB | $0.07 |
| **Snapshots (90 days)** | 1KB/snapshot | 270MB | $0.006 |
| **Compacted Files** | 0GB (replace) | 0GB | $0 |
| **Total** | **130.1GB** | **3.903TB** | **$89.076/month** |

### S3 Storage Classes

**Standard** (frequently accessed):
- $0.023/GB/month
- Use for: Last 30 days of data

**Intelligent-Tiering** (automatic archival):
- $0.0125/GB/month (frequent tier)
- $0.0025/GB/month (infrequent tier, after 90 days)
- Use for: 30-730 days of data

**Glacier Deep Archive** (rarely accessed):
- $0.00099/GB/month
- Use for: > 2 years of data (compliance archive)

### Lifecycle Policy

```json
{
  "Rules": [
    {
      "Id": "archive-old-market-data",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "warehouse/market_data/ticks/"
      },
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "INTELLIGENT_TIERING"
        },
        {
          "Days": 730,
          "StorageClass": "GLACIER_DEEP_ARCHIVE"
        }
      ]
    }
  ]
}
```

**Cost Savings**:
- 30-90 days: Intelligent-Tiering (save 46%)
- > 730 days: Deep Archive (save 95%)

**Annual Savings**:
```
Without lifecycle: 45TB × $0.023 = $1,035/month × 12 = $12,420/year
With lifecycle: 45TB × $0.005 (avg) = $225/month × 12 = $2,700/year

Savings: $9,720/year (78% reduction)
```

---

## Monitoring & Alerts

### Storage Metrics

```promql
# Storage size by table
iceberg_table_size_bytes{table="market_data.ticks"}

# File count per partition
iceberg_files_per_partition{table="market_data.ticks"}

# Average file size
iceberg_avg_file_size_bytes{table="market_data.ticks"}

# Compaction lag (days since last compaction)
time() - iceberg_last_compaction_timestamp{table="market_data.ticks"}
```

### Alerts

```yaml
# Too many small files (needs compaction)
- alert: TooManySmallFiles
  expr: iceberg_files_per_partition > 200
  for: 1d
  severity: warning
  summary: "Partition has > 200 files (compaction needed)"

# Storage growth anomaly
- alert: StorageGrowthAnomaly
  expr: |
    rate(iceberg_table_size_bytes[1d]) * 86400 / 1e9 > 500
  for: 1h
  severity: warning
  summary: "Storage growing > 500GB/day (expected: 130GB/day)"

# Compaction failure
- alert: CompactionFailed
  expr: time() - iceberg_last_compaction_timestamp > 172800  # 2 days
  severity: critical
  summary: "Compaction hasn't run in 2 days"
```

---

## Best Practices

### Write Optimization

1. **Batch writes**: Target 512MB files (optimal for S3 + Parquet)
2. **Compression**: Use Zstd (30% better than Snappy, 10x faster than gzip)
3. **Partitioning**: Partition by query patterns (time + exchange + symbol)
4. **Sorting**: Sort by common query columns (symbol, timestamp)

### Read Optimization

1. **Partition pruning**: Always include partition columns in WHERE clause
2. **Projection pushdown**: Only SELECT columns you need
3. **Predicate pushdown**: Apply filters at file scan level
4. **Compaction**: Schedule daily compaction to merge small files

### Cost Optimization

1. **Lifecycle policies**: Archive old data to Glacier (95% cost reduction)
2. **Snapshot expiration**: Retain only 90 days of snapshots
3. **Orphan cleanup**: Remove unreferenced files weekly
4. **Compression**: Zstd saves 30% storage vs uncompressed

---

## Related Documentation

- [Query Architecture](./QUERY_ARCHITECTURE.md) - Query optimization techniques
- [Disaster Recovery](./DISASTER_RECOVERY.md) - S3 lifecycle policies
- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Boring technology (S3, Iceberg)
