# Query Architecture & Performance Design

**Last Updated**: 2026-01-09
**Owners**: Platform Team, Query Engineering
**Status**: Implementation Plan
**Scope**: Query layer design, caching, optimization, resource management

---

## Overview

The query layer provides unified access to both real-time (Kafka) and historical (Iceberg) market data through a single API. This document defines the query routing logic, caching strategies, performance optimization techniques, and resource limits that ensure predictable query performance at scale.

**Design Philosophy**: Queries should be fast by default, degrade gracefully under load, and provide clear feedback when resource limits are exceeded.

---

## Query Router Architecture

### Decision Tree

Every query passes through a routing layer that determines the optimal execution path:

```
┌─────────────────────────────────────────────────────────────┐
│                    Query Request                            │
│  SELECT * FROM ticks WHERE symbol = 'BHP' AND ...          │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │  Parse Time Predicate  │
         └────────┬───────────────┘
                  │
                  ▼
    ┌─────────────────────────────────┐
    │ Time range entirely in last 5   │
    │ minutes? (realtime window)      │
    └──────┬──────────────────┬───────┘
           │ YES              │ NO
           ▼                  ▼
    ┌─────────────┐    ┌──────────────────┐
    │ Kafka Tail  │    │ Time range spans │
    │ Consumer    │    │ realtime +       │
    │ (Real-time) │    │ historical?      │
    └─────────────┘    └────┬─────────┬───┘
                            │ YES     │ NO
                            ▼         ▼
                    ┌──────────┐  ┌──────────┐
                    │ Hybrid   │  │ DuckDB   │
                    │ Mode     │  │ (Iceberg)│
                    │ (Merge)  │  │ Only     │
                    └──────────┘  └──────────┘
```

### Routing Logic Implementation

```python
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum

class QueryMode(Enum):
    REALTIME = "realtime"      # Last 5 minutes from Kafka
    HISTORICAL = "historical"  # More than 5 minutes old from Iceberg
    HYBRID = "hybrid"          # Spans both realtime and historical

class QueryRouter:
    """
    Routes queries to appropriate backend based on time range.
    """

    REALTIME_WINDOW = timedelta(minutes=5)

    def route(self, query: str) -> QueryMode:
        """
        Determine query mode based on time predicates.

        Args:
            query: SQL query string

        Returns:
            Query mode (REALTIME, HISTORICAL, or HYBRID)
        """
        time_range = self.parse_time_range(query)

        if time_range is None:
            # No time predicate → full historical scan
            return QueryMode.HISTORICAL

        now = datetime.utcnow()
        cutoff = now - self.REALTIME_WINDOW

        if time_range.start >= cutoff:
            # Entirely in realtime window
            return QueryMode.REALTIME
        elif time_range.end < cutoff:
            # Entirely historical
            return QueryMode.HISTORICAL
        else:
            # Spans both realtime and historical
            return QueryMode.HYBRID

    def parse_time_range(self, query: str) -> Optional[TimeRange]:
        """Extract time range from WHERE clause."""
        # Implementation: Parse SQL WHERE clause for timestamp predicates
        pass
```

---

## Caching Strategy

### Three-Layer Cache Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Query Request                            │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │  L1: Query Result      │
         │  Cache (Redis)         │
         │  TTL: 5 minutes        │
         └────────┬───────────────┘
                  │ Miss
                  ▼
         ┌────────────────────────┐
         │  L2: Metadata Cache    │
         │  (Application Memory)  │
         │  TTL: 15 minutes       │
         └────────┬───────────────┘
                  │ Miss
                  ▼
         ┌────────────────────────┐
         │  L3: Partition Stats   │
         │  (Precomputed)         │
         │  TTL: 1 hour           │
         └────────┬───────────────┘
                  │ Miss
                  ▼
         ┌────────────────────────┐
         │  Execute Query         │
         │  (DuckDB/Kafka)        │
         └────────────────────────┘
```

### Cache Implementation

#### L1: Query Result Cache (Redis)

**Purpose**: Cache full query results for identical queries

**Key Design**: Hash of (SQL query + parameters)

```python
import hashlib
import redis
from typing import Optional

class QueryResultCache:
    """
    Cache query results in Redis.

    TTL: 5 minutes (balance freshness vs cache hit rate)
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.ttl_seconds = 300  # 5 minutes

    def get(self, query: str, params: dict) -> Optional[bytes]:
        """Get cached query result."""
        key = self._generate_key(query, params)
        return self.redis.get(key)

    def set(self, query: str, params: dict, result: bytes) -> None:
        """Cache query result."""
        key = self._generate_key(query, params)
        self.redis.setex(key, self.ttl_seconds, result)

    def _generate_key(self, query: str, params: dict) -> str:
        """Generate cache key from query + parameters."""
        content = f"{query}:{sorted(params.items())}"
        return f"query:{hashlib.sha256(content.encode()).hexdigest()}"
```

**Cache Invalidation**:
- TTL-based (5 minutes)
- Manual invalidation on schema change
- Eviction on memory pressure (LRU policy)

**Expected Hit Rate**: 60-70% (many users query same popular symbols)

---

#### L2: Metadata Cache (Application Memory)

**Purpose**: Cache Iceberg table schemas, partition information

```python
from functools import lru_cache
from datetime import datetime, timedelta

class MetadataCache:
    """
    In-memory cache for Iceberg metadata.

    Reduces catalog queries to PostgreSQL.
    """

    @lru_cache(maxsize=1000)
    def get_table_schema(self, table_name: str) -> dict:
        """
        Get table schema with caching.

        TTL implemented via cache invalidation background job.
        """
        from pyiceberg.catalog import load_catalog

        catalog = load_catalog('default')
        table = catalog.load_table(table_name)
        return table.schema().as_struct()

    @lru_cache(maxsize=10000)
    def get_partition_statistics(self, table_name: str, partition: str) -> dict:
        """
        Get partition statistics (min/max values, row count).

        Used for partition pruning decisions.
        """
        # Query Iceberg metadata for partition stats
        return {
            'row_count': 1_000_000,
            'min_timestamp': datetime(2026, 1, 9, 10, 0, 0),
            'max_timestamp': datetime(2026, 1, 9, 11, 0, 0),
            'min_price': 140.50,
            'max_price': 152.30,
        }
```

**Cache Invalidation**:
- Background job refreshes every 15 minutes
- Immediate invalidation on DDL operations (ALTER TABLE)

---

#### L3: Partition Statistics (Precomputed)

**Purpose**: Pre-compute min/max statistics for partition pruning

**Implementation**: Async job computes statistics after each Iceberg write

```python
def compute_partition_statistics(table_name: str, partition_path: str):
    """
    Compute and store partition statistics for query optimization.

    Runs after each Iceberg commit (async, non-blocking).
    """
    from pyiceberg.catalog import load_catalog

    catalog = load_catalog('default')
    table = catalog.load_table(table_name)

    # Scan partition to compute statistics
    df = table.scan(row_filter=f"partition = '{partition_path}'").to_pandas()

    stats = {
        'partition': partition_path,
        'row_count': len(df),
        'min_timestamp': df['exchange_timestamp'].min(),
        'max_timestamp': df['exchange_timestamp'].max(),
        'min_price': df['price'].min(),
        'max_price': df['price'].max(),
        'min_volume': df['volume'].min(),
        'max_volume': df['volume'].max(),
    }

    # Store in metadata table
    store_partition_stats(table_name, stats)
```

---

## Query Optimization

### Partition Pruning

**Strategy**: Skip reading partitions that cannot contain matching rows

**Example**:
```sql
SELECT * FROM ticks
WHERE symbol = 'BHP'
  AND exchange_timestamp BETWEEN '2026-01-09 10:00:00' AND '2026-01-09 11:00:00'
```

**Without Pruning**:
- Scan all partitions (8,000 symbols × 1 day = 8,000 files)
- Read 130GB of data

**With Pruning**:
- Check partition statistics: Which partitions contain timestamp range?
- Skip partitions with `max_timestamp < '2026-01-09 10:00:00'`
- Skip partitions with `min_timestamp > '2026-01-09 11:00:00'`
- Result: Scan ~10 files (1.6GB)

**Savings**: 98.7% of data skipped

### Projection Pushdown

**Strategy**: Only read columns required by query (Parquet columnar format)

**Example**:
```sql
SELECT symbol, price FROM ticks WHERE symbol = 'BHP'
```

**Without Pushdown**:
- Read all columns: `symbol, price, volume, timestamp, exchange, sequence_number, ...`
- 500 bytes/row × 10M rows = 5GB

**With Pushdown**:
- Read only `symbol, price`: 100 bytes/row × 10M rows = 1GB

**Savings**: 80% less data read

### Predicate Pushdown

**Strategy**: Apply WHERE filters at file scan level (before loading into memory)

**Example**:
```sql
SELECT * FROM ticks WHERE price > 150.0
```

**Without Pushdown**:
- Load all rows into memory
- Filter in DuckDB (expensive)

**With Pushdown**:
- Parquet file metadata has `min_price` per row group
- Skip row groups with `max_price < 150.0`
- Only load row groups that might contain matches

**Savings**: 50-90% less data loaded (depends on selectivity)

---

## Resource Limits

### Query Concurrency

**Limit**: 10 concurrent queries per DuckDB instance

**Rationale**:
- DuckDB is single-node (shared memory)
- Each query uses 1-2 CPU cores
- 10 queries × 2 cores = 20 cores (leaves headroom for OS)

**Implementation**:
```python
import asyncio
from asyncio import Semaphore

class QueryExecutor:
    """
    Execute queries with concurrency limits.
    """

    def __init__(self, max_concurrent: int = 10):
        self.semaphore = Semaphore(max_concurrent)

    async def execute(self, query: str):
        """Execute query with concurrency limit."""
        async with self.semaphore:
            # Only 10 queries execute concurrently
            result = await self._execute_query(query)
            return result

    async def _execute_query(self, query: str):
        """Actual query execution."""
        import duckdb
        conn = duckdb.connect()
        return conn.execute(query).fetchall()
```

**Behavior When Limit Exceeded**:
- Requests queue (FIFO)
- Return `429 Too Many Requests` if queue > 100
- Log queue depth for autoscaling trigger

---

### Query Timeout

**Limit**: 5 minutes max runtime per query

**Rationale**:
- Prevent runaway queries (full table scans)
- Ensure predictable API response times
- Force users to optimize queries

**Implementation**:
```python
import signal
from contextlib import contextmanager

@contextmanager
def query_timeout(seconds: int = 300):
    """
    Kill query after timeout.

    Args:
        seconds: Max query runtime (default: 5 minutes)
    """
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Query exceeded {seconds}s timeout")

    # Set alarm
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)

    try:
        yield
    finally:
        # Cancel alarm
        signal.alarm(0)

# Usage
with query_timeout(300):
    result = conn.execute(query).fetchall()
```

**Behavior on Timeout**:
- Kill query process
- Return `504 Gateway Timeout` to user
- Log slow query for optimization review

---

### Memory Limit

**Limit**: 2GB per query

**Rationale**:
- DuckDB loads data into memory for processing
- Large result sets (10M rows × 500 bytes = 5GB) can OOM
- Force users to paginate or aggregate

**Implementation**:
```python
def set_memory_limit(conn, limit_gb: int = 2):
    """
    Set DuckDB memory limit.

    Args:
        conn: DuckDB connection
        limit_gb: Memory limit in GB (default: 2)
    """
    conn.execute(f"SET memory_limit='{limit_gb}GB'")
    conn.execute("SET temp_directory='/mnt/nvme/duckdb-temp'")  # Spill to disk
```

**Behavior on Limit Exceeded**:
- DuckDB spills to disk (NVMe temp directory)
- Query slows down but doesn't fail
- Log warning: "Query using disk spill (optimize or paginate)"

---

## Query Modes

### Mode 1: Real-Time (Kafka Tail)

**Use Case**: Query last 5 minutes of data

**Implementation**:
```python
from confluent_kafka import Consumer

class RealtimeQueryEngine:
    """
    Query real-time data from Kafka.
    """

    def __init__(self):
        self.consumer = Consumer({
            'bootstrap.servers': 'localhost:9092',
            'group.id': 'query-api-realtime',
            'auto.offset.reset': 'earliest',
        })

    def query(self, symbol: str, start_time: datetime, end_time: datetime):
        """
        Query Kafka for recent ticks.

        Args:
            symbol: Symbol to query
            start_time: Start timestamp (must be within last 5 minutes)
            end_time: End timestamp

        Returns:
            List of ticks
        """
        # Subscribe to topic
        self.consumer.subscribe(['market.ticks.asx'])

        # Seek to timestamp
        self.consumer.seek_to_timestamp(start_time)

        ticks = []
        while True:
            msg = self.consumer.poll(timeout=1.0)

            if msg is None:
                break

            tick = self.deserialize(msg.value())

            if tick.symbol == symbol and tick.timestamp <= end_time:
                ticks.append(tick)
            elif tick.timestamp > end_time:
                break

        return ticks
```

**Performance**:
- Latency: 10-50ms (Kafka tail read)
- Throughput: 10K ticks/second

---

### Mode 2: Historical (DuckDB + Iceberg)

**Use Case**: Query data older than 5 minutes

**Implementation**:
```python
import duckdb
from pyiceberg.catalog import load_catalog

class HistoricalQueryEngine:
    """
    Query historical data from Iceberg using DuckDB.
    """

    def __init__(self):
        self.catalog = load_catalog('default')
        self.conn = duckdb.connect()

        # Install Iceberg extension
        self.conn.execute("INSTALL iceberg")
        self.conn.execute("LOAD iceberg")

    def query(self, sql: str):
        """
        Execute SQL query on Iceberg tables.

        Args:
            sql: SQL query string

        Returns:
            Query results as DataFrame
        """
        # Set resource limits
        self.conn.execute("SET memory_limit='2GB'")
        self.conn.execute("SET threads=8")

        # Execute query
        result = self.conn.execute(sql).fetchdf()

        return result
```

**Performance**:
- Latency: 1-5 seconds (depends on data volume)
- Throughput: 10M rows/second (DuckDB vectorized execution)

---

### Mode 3: Hybrid (Merge Realtime + Historical)

**Use Case**: Query spans last hour (includes both Kafka and Iceberg)

**Implementation**:
```python
class HybridQueryEngine:
    """
    Query across both realtime and historical data.
    """

    def __init__(self):
        self.realtime_engine = RealtimeQueryEngine()
        self.historical_engine = HistoricalQueryEngine()
        self.cutoff = timedelta(minutes=5)

    def query(self, symbol: str, start_time: datetime, end_time: datetime):
        """
        Query hybrid mode (Kafka + Iceberg).

        Args:
            symbol: Symbol to query
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            Merged results from both sources
        """
        now = datetime.utcnow()
        realtime_cutoff = now - self.cutoff

        # Query historical (start_time to realtime_cutoff)
        historical_results = self.historical_engine.query(f"""
            SELECT * FROM market_data.ticks
            WHERE symbol = '{symbol}'
              AND exchange_timestamp BETWEEN '{start_time}' AND '{realtime_cutoff}'
        """)

        # Query realtime (realtime_cutoff to end_time)
        realtime_results = self.realtime_engine.query(
            symbol=symbol,
            start_time=realtime_cutoff,
            end_time=end_time
        )

        # Merge results (deduplicate by message_id)
        return self._merge_deduplicate(historical_results, realtime_results)

    def _merge_deduplicate(self, historical, realtime):
        """
        Merge and deduplicate results.

        Realtime may have duplicates of data already in Iceberg.
        """
        import pandas as pd

        combined = pd.concat([historical, realtime])
        deduplicated = combined.drop_duplicates(subset=['message_id'])
        return deduplicated.sort_values('exchange_timestamp')
```

**Performance**:
- Latency: 1-5 seconds (dominated by historical query)
- Deduplication overhead: Negligible (hash-based)

---

## Scaling Triggers

### When to Add Query API Instances

**Trigger 1: Query latency p99 > 10 seconds**
- Current capacity exhausted
- Add +2 API instances (horizontal scaling)

**Trigger 2: Queue depth > 50**
- Too many concurrent queries
- Add +3 API instances

**Trigger 3: CPU utilization > 70% (sustained 10 minutes)**
- DuckDB CPU-bound
- Add +1 API instance

### When to Migrate to Presto/Trino

**Trigger 1: Dataset > 10TB**
- DuckDB single-node memory limit
- Presto enables distributed queries

**Trigger 2: Queries require joins across 100+ symbols**
- DuckDB struggles with large joins
- Presto optimized for distributed joins

**Trigger 3: Need multi-tenancy (isolate workloads by team)**
- DuckDB shares resources
- Presto supports resource pools

**Migration Path**:
```markdown
1. Deploy Presto cluster (shadow mode)
2. Dual-execute queries (DuckDB + Presto)
3. Compare results and performance
4. Cutover 10% of traffic to Presto (canary)
5. Monitor for 1 week
6. Cutover 100% if success metrics met
7. Deprecate DuckDB after 30 days
```

---

## Performance SLOs

### Query Latency

| Query Type | p50 | p99 | p99.9 |
|------------|-----|-----|-------|
| Realtime (< 5 min data) | 50ms | 200ms | 500ms |
| Historical (> 5 min data) | 1s | 5s | 10s |
| Hybrid (both) | 2s | 8s | 15s |

**Alert**: p99 exceeds target → investigate slow queries

### Cache Hit Rate

| Cache Layer | Target Hit Rate | Alert Threshold |
|-------------|-----------------|-----------------|
| L1 (Query Result) | 60-70% | < 50% |
| L2 (Metadata) | 90-95% | < 80% |
| L3 (Partition Stats) | 95-99% | < 90% |

**Alert**: Hit rate below threshold → review cache TTL or capacity

### Query Error Rate

**Target**: < 1% of queries fail

**Alert**: Error rate > 5% → on-call investigation

**Common Errors**:
- Timeout (5 minute limit)
- OOM (2GB memory limit)
- Concurrency limit (10 concurrent queries)
- Invalid SQL syntax

---

## Monitoring

### Critical Metrics

```
# Query latency distribution
query_duration_seconds{mode="realtime|historical|hybrid", percentile="50|99|999"}

# Query throughput
query_requests_total{mode="realtime|historical|hybrid"}

# Query errors
query_errors_total{error_type="timeout|oom|concurrency|syntax"}

# Cache performance
cache_hit_rate{layer="l1|l2|l3"}
cache_evictions_total{layer="l1|l2|l3"}

# Resource utilization
query_concurrency_current
query_memory_used_bytes
duckdb_spill_to_disk_bytes
```

### Dashboards

**Dashboard 1: Query Performance**
- Query latency (p50, p99, p99.9) by mode
- Query throughput (queries/sec)
- Error rate (%)

**Dashboard 2: Cache Health**
- Hit rate by layer (L1, L2, L3)
- Eviction rate
- Cache size (MB)

**Dashboard 3: Resource Utilization**
- Concurrent queries (current)
- Queue depth
- Memory usage (GB)
- CPU utilization (%)

---

## Testing Requirements

### Performance Benchmarks

**Test 1: Realtime Query Latency**
```python
def test_realtime_query_latency():
    """Verify realtime queries meet p99 < 200ms SLO."""
    engine = RealtimeQueryEngine()

    latencies = []
    for _ in range(1000):
        start = time.time()
        result = engine.query(symbol='BHP', start_time=now() - timedelta(minutes=5), end_time=now())
        latencies.append(time.time() - start)

    p99 = np.percentile(latencies, 99)
    assert p99 < 0.2, f"p99 latency {p99}s exceeds 200ms SLO"
```

**Test 2: Historical Query Partition Pruning**
```python
def test_partition_pruning_effectiveness():
    """Verify partition pruning skips > 95% of files."""
    query = """
        SELECT * FROM ticks
        WHERE symbol = 'BHP'
          AND exchange_timestamp = '2026-01-09'
    """

    explain = conn.execute(f"EXPLAIN {query}").fetchall()

    files_scanned = parse_files_scanned(explain)
    total_files = get_total_file_count('market_data.ticks')

    prune_ratio = 1 - (files_scanned / total_files)
    assert prune_ratio > 0.95, f"Partition pruning only skipped {prune_ratio*100}% of files"
```

**Test 3: Hybrid Mode Deduplication**
```python
def test_hybrid_deduplication():
    """Verify hybrid mode deduplicates Kafka/Iceberg overlap."""
    engine = HybridQueryEngine()

    # Query that spans realtime + historical
    result = engine.query(
        symbol='BHP',
        start_time=now() - timedelta(hours=1),
        end_time=now()
    )

    # Check no duplicate message_ids
    message_ids = [r['message_id'] for r in result]
    assert len(message_ids) == len(set(message_ids)), "Found duplicate message_ids"
```

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Observable by default
- [Latency & Backpressure](./LATENCY_BACKPRESSURE.md) - Query API degradation cascade
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md) - Replay semantics
- [Data Consistency](./DATA_CONSISTENCY.md) - Kafka/Iceberg consistency model