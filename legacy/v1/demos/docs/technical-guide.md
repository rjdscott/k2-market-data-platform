# Technical Guide - K2 Platform Architecture

**Purpose**: Detailed technical walkthrough for engineers and architects

**Last Updated**: 2026-01-17

---

## Architecture Overview

The K2 Platform is a multi-layer market data platform designed for L3 cold path analytics (<500ms target latency). It combines streaming ingestion with lakehouse storage and interactive querying.

### Component Stack

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI (8000)                                         │
│  REST API Layer                                         │
└─────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  DuckDB Query Engine                                    │
│  Connection Pool (5 workers)                            │
└─────────────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────┬──────────────────────────────────┐
│  Kafka (recent data) │  Iceberg (historical data)       │
│  15-min window       │  Parquet + Snappy (10:1 ratio)   │
└──────────────────────┴──────────────────────────────────┘
                      ▲
                      │
┌─────────────────────────────────────────────────────────┐
│  WebSocket Stream Clients (Binance, etc.)               │
│  Avro Serialization + Schema Registry                   │
└─────────────────────────────────────────────────────────┘
```

---

## Component Deep-Dive

### Kafka Streaming Layer

**Version**: Apache Kafka 3.7 (KRaft mode, no ZooKeeper)

**Purpose**: Buffer incoming messages, enable replayability, decouple producers/consumers

**Configuration**:
```yaml
Brokers: 1 (localhost:9092)
Retention: 90 days
Topics: market.crypto.trades.binance (16 partitions)
Replication: 1 (dev), 3 (prod)
```

**Why Kafka?**
- Proven scale: Netflix 8M+ msg/sec
- Replayability: 90+ day retention for backfill
- Strong ordering: Per-partition guarantees
- Ecosystem: Native Schema Registry, monitoring integrations

**Performance Characteristics**:
- Current throughput: 138 msg/sec (single-node)
- Target scale: 1M msg/sec (multi-node with horizontal scaling)
- Latency: Sub-millisecond producer acknowledgment
- Durability: Configurable acks (all, 1, 0)

**Topic Naming Convention**:
```
market.{asset_class}.{data_type}.{exchange}
```

Examples:
- `market.crypto.trades.binance`
- `market.equities.quotes.asx`
- `market.fx.ticks.fxcm`

---

### Schema Registry

**Version**: Confluent Schema Registry 7.5

**Purpose**: Enforce schema compatibility, enable evolution without downtime

**Avro Schema Structure** (V2):
```json
{
  "type": "record",
  "name": "MarketDataV2",
  "fields": [
    {"name": "symbol", "type": "string"},
    {"name": "exchange", "type": "string"},
    {"name": "exchange_timestamp_ms", "type": "long"},
    {"name": "price", "type": "double"},
    {"name": "quantity", "type": "double"},
    {"name": "side", "type": ["null", "string"], "default": null}
  ]
}
```

**Compatibility Mode**: BACKWARD (new schema can read old data)

**Evolution Example** (V1 → V2):
- Added `side` field with default `null`
- Consumers upgraded without producer changes
- No data rewrites required

**Why Avro?**
- Native Kafka ecosystem support
- Compact binary format (2-3× smaller than JSON)
- Schema evolution without code generation
- Dynamic resolution at runtime

---

### Iceberg Lakehouse

**Version**: Apache Iceberg 1.4

**Purpose**: ACID storage, time-travel queries, schema evolution

**Table Structure**:
```sql
CREATE TABLE market_data (
  symbol STRING,
  exchange STRING,
  exchange_timestamp_ms BIGINT,
  price DOUBLE,
  quantity DOUBLE,
  side STRING,
  exchange_date DATE,  -- Partition key
  symbol_hash INT      -- Partition key (16 buckets)
)
PARTITIONED BY (exchange_date, symbol_hash)
STORED AS PARQUET
COMPRESSION SNAPPY
```

**Partitioning Strategy**:
- **Date partitioning**: Efficient time-range queries (most common pattern)
- **Symbol hash buckets** (16): Prune 93.75% of data for single-symbol queries
- **Why hash?**: Prevents partition explosion (10K+ symbols → 16 buckets)

**Performance Evidence**:
- Time range scan: p50=161ms, p99=299ms (excellent partition pruning)
- Single symbol: p50=229ms, p99=2545ms (cold cache outliers)
- Multi-symbol: p50=558ms, p99=4440ms (expected - scans multiple partitions)

**ACID Guarantees**:
- Atomic commits via optimistic concurrency (snapshot versioning)
- Isolation: Readers see consistent snapshot
- Durability: Manifest files reference immutable Parquet files

**Time-Travel Queries**:
```sql
SELECT * FROM market_data
FOR SYSTEM_TIME AS OF '2026-01-15 14:30:00'
WHERE symbol = 'BTCUSDT';
```

**Compression**:
- Format: Parquet (columnar) + Snappy compression
- Measured ratio: 10.0:1 (within 8-12:1 target range)
- Impact: $0.85 per million messages at scale

---

### DuckDB Query Engine

**Version**: DuckDB 0.10

**Purpose**: Fast embedded analytics with Iceberg integration

**Why DuckDB?**
- Embedded: Zero-ops, no server to manage
- Fast: Columnar execution, vectorized processing
- Low memory: <1GB footprint vs 8GB+ for Presto
- Iceberg native: Direct table queries without external metastore

**Connection Pool**:
```python
# 5 workers, managed via ThreadPoolExecutor
# 5× throughput improvement vs single connection
pool = DuckDBConnectionPool(size=5)
```

**Query Performance** (measured, 20 iterations):
- Simple filter: p50=229ms
- Aggregation: p50=257ms, p99=531ms
- API endpoint (full stack): p50=388ms, p99=681ms

**Migration Path**: Phase 5 adds Presto for multi-node distributed queries (>10TB datasets)

---

## Data Flow Walkthrough

### Ingestion Path

**Step 1: WebSocket Connection**
```python
# Binance WebSocket stream client
async def connect():
    uri = "wss://stream.binance.com:9443/ws"
    async with websockets.connect(uri) as ws:
        await ws.send(subscribe_message)
        async for msg in ws:
            await process_message(msg)
```

**Step 2: Avro Serialization**
```python
# Serialize to Avro with Schema Registry
schema = registry.get_latest_schema("market-data-v2")
avro_bytes = avro_serializer.serialize(data, schema)
```

**Step 3: Kafka Production**
```python
# Produce to Kafka with key-based partitioning
producer.send(
    topic="market.crypto.trades.binance",
    key=symbol.encode(),  # Ensures same symbol → same partition
    value=avro_bytes
)
```

**Step 4: Consumer Processing**
```python
# Consumer with sequence tracking and deduplication
for msg in consumer:
    # Deserialize Avro
    data = avro_deserializer.deserialize(msg.value)

    # Check for duplicates (in-memory dict, 1-hour window)
    if is_duplicate(data):
        metrics.duplicates_detected.inc()
        continue

    # Track sequence numbers (detect gaps)
    track_sequence(data['symbol'], data['sequence_num'])

    # Write to Iceberg
    write_to_iceberg(data)
```

**Step 5: Iceberg Commit**
```python
# Batch writes (1000 records or 5 seconds)
# Atomic commit with optimistic concurrency
table.append([records])
```

**Throughput Characteristics**:
- Producer: Sub-ms latency
- Consumer: 138 msg/sec (single-node, I/O bound)
- Iceberg write: +200ms p99 latency (ACID overhead)
- End-to-end: <2s from WebSocket to queryable data

---

### Query Path

**Step 1: API Request**
```bash
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100"
```

**Step 2: Hybrid Query Execution**
```python
# Query recent data from Kafka (last 15 min)
kafka_data = query_kafka(symbol, since=now - 15min)

# Query historical data from Iceberg
iceberg_data = query_iceberg(symbol, until=now - 15min)

# Merge and deduplicate
result = merge_deduplicate([kafka_data, iceberg_data])
```

**Step 3: DuckDB Execution**
```sql
-- Partition pruning via date filter
SELECT * FROM market_data
WHERE exchange_date = '2026-01-17'
  AND symbol = 'BTCUSDT'
ORDER BY exchange_timestamp_ms DESC
LIMIT 100;
```

**Step 4: Response Serialization**
```python
# FastAPI automatic Pydantic serialization
return TradesResponse(
    symbol="BTCUSDT",
    count=100,
    trades=[...]
)
```

**Performance Profile**:
- API overhead: ~40ms (routing, auth, serialization)
- Kafka query: ~20ms (in-memory, recent data)
- Iceberg query: 150-250ms (disk I/O, partition pruning)
- Total: p50=388ms, p99=681ms

---

## Advanced Patterns

### Graceful Degradation

**5-Level Circuit Breaker**:
```python
class DegradationLevel(Enum):
    NORMAL = 0         # All features enabled
    SOFT = 1           # Skip enrichment (lag ≥ 100K or heap ≥ 70%)
    GRACEFUL = 2       # Drop Tier 3 symbols (lag ≥ 500K or heap ≥ 80%)
    AGGRESSIVE = 3     # Only Tier 1 symbols (lag ≥ 1M or heap ≥ 90%)
    CIRCUIT_BREAK = 4  # Stop processing (lag ≥ 5M or heap ≥ 95%)
```

**Priority-Based Load Shedding**:
- Tier 1: BTC, ETH (top 20 by volume)
- Tier 2: Major altcoins (top 100)
- Tier 3: Long-tail assets (shed first)

**Recovery with Hysteresis**:
- Trigger: lag ≥ 500K → GRACEFUL
- Recovery: lag < 250K + 30s cooldown → NORMAL

**Demo**:
```bash
python demos/scripts/resilience/demo_degradation.py --quick
```

---

### Idempotency and Deduplication

**Message ID Generation**:
```python
# Deterministic ID from exchange + timestamp + symbol
message_id = f"{exchange}:{timestamp_ms}:{symbol}:{trade_id}"
```

**In-Memory Deduplication** (current scale):
```python
# 1-hour rolling window, sufficient for 138 msg/sec
seen_ids = {}  # {message_id: timestamp}
```

**Future: Bloom Filter + Redis** (multi-node scale):
```python
# 24-hour window, 1M msg/sec scale
bloom = BloomFilter(capacity=1B, error_rate=0.001)
redis.setex(message_id, ttl=86400, value="1")
```

---

### Connection Pooling

**DuckDB Pool Implementation**:
```python
class DuckDBConnectionPool:
    def __init__(self, size=5):
        self.pool = queue.Queue(maxsize=size)
        for _ in range(size):
            conn = duckdb.connect(database_path)
            self.pool.put(conn)

    def execute(self, query):
        conn = self.pool.get()
        try:
            return conn.execute(query).fetchall()
        finally:
            self.pool.put(conn)
```

**Performance Impact**:
- Without pool: 1 query at a time (serialized)
- With pool (5 workers): 5× concurrent throughput
- Measured: p50 improved from ~500ms to ~388ms

---

## Performance Characteristics

### Latency Profiles

**Query Latency** (20 iterations, measured):
- Time range scan: p50=161ms, p99=299ms (best performance)
- Simple filter: p50=229ms, p99=2545ms (cold cache outliers)
- Aggregation: p50=257ms, p99=531ms
- Multi-symbol: p50=558ms, p99=4440ms (multi-partition scan)
- API endpoint: p50=388ms, p99=681ms (full stack)

**Ingestion Throughput**:
- Current: 138 msg/sec (single-node, I/O bound)
- Peak: 10K-50K msg/sec bursts (Kafka buffering)
- Target: 1M msg/sec (multi-node with horizontal scaling)

**Storage Efficiency**:
- Compression: 10.0:1 (Parquet + Snappy)
- Cost: $0.85 per million messages at scale
- Savings: 35-40% vs Snowflake (~$25K/month)

---

## Monitoring and Observability

**Prometheus Metrics**: 83 validated metrics
- Ingestion: `k2_messages_ingested_total`, `k2_ingestion_latency_seconds`
- Degradation: `k2_degradation_level`, `k2_messages_shed_total`
- Query: `k2_query_duration_seconds`, `k2_api_requests_total`

**Grafana Dashboards**: http://localhost:3000
- Real-time ingestion rates
- Degradation level indicator
- Query performance histograms
- Resource usage (CPU, memory, disk)

**Health Checks**:
```bash
# API health
curl http://localhost:8000/health

# Kafka health
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092

# Schema Registry health
curl http://localhost:8081/subjects
```

---

## Troubleshooting

### High Query Latency

**Symptom**: p99 > 1000ms

**Diagnosis**:
```sql
-- Check partition pruning
EXPLAIN SELECT * FROM market_data
WHERE exchange_date = '2026-01-17' AND symbol = 'BTCUSDT';
```

**Common Causes**:
- Missing date filter (full table scan)
- Cold cache (first query after restart)
- Multi-symbol query (expected higher latency)

**Fix**:
- Always include date filter for partition pruning
- Pre-warm cache with common queries
- Use connection pooling (5 workers)

---

### Consumer Lag

**Symptom**: Degradation level > NORMAL

**Diagnosis**:
```bash
# Check Kafka consumer lag
docker exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group k2-consumer
```

**Common Causes**:
- Downstream I/O bottleneck (Iceberg writes)
- High memory pressure (GC pauses)
- Burst traffic exceeding capacity

**Fix**:
- Circuit breaker automatically sheds low-priority data
- Scale horizontally (add consumer instances)
- Tune batch sizes (trade-off: latency vs throughput)

---

## Next Steps

**For Operators**: See [Demo Checklist](../reference/demo-checklist.md) for pre-demo validation

**For Developers**: See [Architecture Decisions](../reference/architecture-decisions.md) for design rationale

**For Executives**: See [Executive Demo](../notebooks/executive-demo.ipynb) for business value

---

**Last Updated**: 2026-01-17
**Questions?**: See [Quick Reference](../reference/quick-reference.md) or [Demo README](../README.md)
