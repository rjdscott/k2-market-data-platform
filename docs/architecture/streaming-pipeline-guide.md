# K2 Streaming Pipeline Architecture Guide

**Last Updated**: 2026-01-19
**Status**: Active
**Maintained By**: Data Engineering Team

---

## Overview

The K2 Market Data Platform implements a production-grade streaming architecture for ingesting, validating, and transforming cryptocurrency trade data from multiple exchanges (Binance, Kraken) in real-time.

**Architecture Pattern**: Medallion Architecture (Bronze → Silver → Gold)
**Processing Engine**: Apache Spark Structured Streaming
**Storage Layer**: Apache Iceberg on MinIO (S3-compatible)
**Message Broker**: Apache Kafka with Schema Registry

---

## End-to-End Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           INGESTION LAYER                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  Exchange WebSocket APIs                                                     │
│    ├─ Binance: wss://stream.binance.com:9443/ws                            │
│    └─ Kraken: wss://ws.kraken.com                                          │
│                           │                                                  │
│                           ↓                                                  │
│  Python Streaming Clients (binance_stream.py, kraken_stream.py)            │
│    ├─ V2 Schema conversion                                                  │
│    ├─ Avro serialization with Schema Registry                              │
│    └─ Kafka Producer (explicit flush every 10 trades)                      │
│                           │                                                  │
│                           ↓                                                  │
│  Apache Kafka (3 brokers, 40 partitions Binance, 20 partitions Kraken)    │
│    ├─ Topic: market.crypto.trades.binance                                  │
│    └─ Topic: market.crypto.trades.kraken                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BRONZE LAYER (Raw Landing Zone)                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  Spark Structured Streaming Jobs (bronze_binance_ingestion.py,             │
│                                     bronze_kraken_ingestion.py)             │
│    ├─ Reads from Kafka: spark.readStream.format("kafka")                   │
│    ├─ Stores raw bytes (5-byte Schema Registry header + Avro payload)      │
│    ├─ Trigger: 10 seconds                                                  │
│    ├─ Checkpoints: /checkpoints/bronze-{exchange}/                         │
│    └─ Exactly-once semantics (Kafka offsets + Iceberg ACID)                │
│                           │                                                  │
│                           ↓                                                  │
│  Iceberg Tables (bronze_binance_trades, bronze_kraken_trades)              │
│    ├─ Schema: raw_bytes BINARY, kafka_timestamp, topic, partition, offset  │
│    ├─ Partitioning: days(ingestion_date)                                   │
│    ├─ Retention: 7 days (replayability buffer)                             │
│    └─ Purpose: Immutable raw data for debugging and replay                 │
└─────────────────────────────────────────────────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SILVER LAYER (Validated Data)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  Spark Structured Streaming Jobs (silver_binance_transformation.py,        │
│                                     silver_kraken_transformation.py)        │
│    ├─ Reads from Bronze: spark.readStream.table("bronze_binance_trades")   │
│    ├─ Avro Deserialization:                                                │
│    │   ├─ Strip 5-byte Schema Registry header                              │
│    │   ├─ Fetch schema from Schema Registry (with caching)                 │
│    │   └─ Deserialize to V2 unified schema (15 fields)                     │
│    ├─ Validation (8 rules):                                                │
│    │   ├─ price > 0                                                        │
│    │   ├─ quantity > 0                                                     │
│    │   ├─ symbol not null                                                  │
│    │   ├─ side in [BUY, SELL]                                              │
│    │   ├─ timestamp not in future                                          │
│    │   ├─ timestamp not too old (>1 year)                                  │
│    │   ├─ message_id not null                                              │
│    │   └─ asset_class = crypto                                             │
│    ├─ Stream Split:                                                         │
│    │   ├─ Valid records → Silver tables                                    │
│    │   └─ Invalid records (FALSE or NULL) → DLQ table                      │
│    ├─ Trigger: 30 seconds                                                  │
│    ├─ Checkpoints: /checkpoints/silver-{exchange}/                         │
│    └─ Exactly-once semantics                                               │
│                           │                                                  │
│                 ┌─────────┴─────────┐                                       │
│                 │                   │                                       │
│            VALID│                   │INVALID                                │
│                 ↓                   ↓                                       │
│  ┌──────────────────────┐   ┌──────────────────────────┐                  │
│  │ Silver Tables         │   │ DLQ (Dead Letter Queue)  │                  │
│  │ (Validated V2 Schema) │   │ (Failed Validations)     │                  │
│  ├──────────────────────┤   ├──────────────────────────┤                  │
│  │ silver_binance_trades│   │ silver_dlq_trades         │                  │
│  │ silver_kraken_trades │   │   - raw_record            │                  │
│  │   - 15 V2 fields     │   │   - error_reason          │                  │
│  │   - validation_      │   │   - error_type            │                  │
│  │     timestamp        │   │   - error_timestamp       │                  │
│  │   - bronze_          │   │   - bronze_source         │                  │
│  │     ingestion_       │   │   - kafka_offset          │                  │
│  │     timestamp        │   │   - schema_id             │                  │
│  │   - schema_id        │   │   - dlq_date (partition)  │                  │
│  │                       │   │                           │                  │
│  │ Partitioning: daily  │   │ Partitioning: daily       │                  │
│  │ Retention: 30 days   │   │ Retention: 90 days        │                  │
│  └──────────────────────┘   └──────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GOLD LAYER (Analytics-Ready)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│  Spark Structured Streaming Job (gold_aggregation.py) [PLANNED - Step 12] │
│    ├─ Reads from both Silver tables (Binance + Kraken)                     │
│    ├─ Union: Combines both exchanges                                       │
│    ├─ Deduplication: By message_id (cross-exchange dedup)                  │
│    ├─ Derived columns: exchange_date, exchange_hour                        │
│    ├─ Trigger: 60 seconds                                                  │
│    └─ Checkpoints: /checkpoints/gold/                                      │
│                           │                                                  │
│                           ↓                                                  │
│  Iceberg Table (gold_crypto_trades) [PLANNED]                              │
│    ├─ Schema: 15 V2 fields + exchange_date + exchange_hour                 │
│    ├─ Partitioning: Hourly (exchange_date, exchange_hour)                  │
│    ├─ Sorting: (timestamp, trade_id)                                       │
│    ├─ Retention: Unlimited (analytics layer)                               │
│    └─ Purpose: Unified cross-exchange analytics                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Layer Responsibilities

### Bronze Layer (Raw Landing Zone)

**Purpose**: Immutable raw data storage for replayability and debugging

**What it stores**:
- Full Kafka message value (5-byte Schema Registry header + Avro payload)
- Kafka metadata (timestamp, topic, partition, offset)
- Ingestion timestamp

**Why raw bytes?**:
1. **Replayability**: If bugs found in Silver, can replay from Bronze
2. **Schema Evolution**: Can handle schema changes without Bronze migration
3. **Debugging**: Can investigate exact bytes received from Kafka
4. **Auditability**: Proof of what was received at what time

**Retention**: 7 days (short-term replayability buffer)

**Partitioning**: Daily (by ingestion_date)

---

### Silver Layer (Validated Data)

**Purpose**: Validated, deserialized data conforming to V2 unified schema

**What it does**:
1. **Deserializes Avro**: Strips Schema Registry header, fetches schema, deserializes
2. **Validates data quality**: 8 validation rules (price > 0, quantity > 0, timestamps valid, etc.)
3. **Routes records**: Valid → Silver tables, Invalid → DLQ
4. **Adds metadata**: validation_timestamp, bronze_ingestion_timestamp, schema_id

**Why per-exchange tables?**:
- Independent schema evolution (if Binance/Kraken need different fields)
- Clearer data lineage (know which exchange each record came from)
- Independent retention policies (can keep Kraken longer if needed)

**Retention**: 30 days (operational analytics)

**Partitioning**: Daily (by validation_timestamp)

---

### DLQ (Dead Letter Queue)

**Purpose**: Capture all validation failures for observability and recovery

**What it stores**:
- Original Bronze raw_bytes (for replay/debugging)
- error_reason: Specific validation failure (e.g., "price_must_be_positive")
- error_type: Error category (e.g., "price_validation")
- Lineage metadata: bronze_source, kafka_offset, schema_id

**Why critical?**:
- **No silent data loss**: ALL records accounted for (Silver or DLQ)
- **Observability**: Can investigate why records failed validation
- **Recovery**: Can replay from Bronze after fixing bugs
- **Alerting**: DLQ rate metric (target: <0.1%, alert: >1%)

**Retention**: 90 days (long enough for investigation)

**Partitioning**: Daily (by dlq_date)

---

### Gold Layer (Analytics-Ready) [PLANNED - Step 12]

**Purpose**: Unified cross-exchange analytics with derived columns

**What it will do**:
1. **Union**: Combine Binance + Kraken Silver tables
2. **Deduplicate**: By message_id (prevent duplicates across exchanges)
3. **Derive columns**: exchange_date, exchange_hour (for analytical queries)
4. **Single source of truth**: All exchanges in one table

**Why hourly partitioning?**:
- Most queries are "last N hours" (recent data)
- 24x better partition pruning than daily
- Query pattern: `WHERE exchange_date = today AND exchange_hour IN (9, 10, 11)` reads 3 partitions

**Retention**: Unlimited (long-term analytics)

**Partitioning**: Hourly (exchange_date, exchange_hour)

---

## Resource Allocation

### Spark Cluster

```
Spark Master: 1 container
  └─ 2 CPUs, 2GB RAM
  └─ Coordinates job scheduling

Spark Workers: 2 containers
  ├─ Worker 1: 3 cores, 3GB RAM
  └─ Worker 2: 3 cores, 3GB RAM
  └─ Total cluster: 6 cores, 6GB RAM
```

### Streaming Jobs (4 Total)

**Per-Job Allocation**:
```yaml
--total-executor-cores 1
--executor-cores 1
--executor-memory 1g
```

**Rationale**:
- **1 core per stage per exchange** = 4 cores needed (2 Bronze + 2 Silver)
- Actual CPU usage: < 1% per job in steady state
- 2 free cores (33% headroom) for burst processing
- Lightweight streaming workload (< 100 records/batch, 30-second trigger)

**Cluster State (Healthy)**:
```
Total: 6 cores, 6144MB memory
Used: 4 cores, 4096MB memory
Free: 2 cores, 2048MB (33% headroom)

Applications (all RUNNING):
  Bronze-Binance:  1 core
  Bronze-Kraken:   1 core
  Silver-Binance:  1 core
  Silver-Kraken:   1 core
```

---

## Critical Patterns and Best Practices

### 1. Explicit NULL Handling (Preventing Silent Data Loss)

**Problem**: Spark SQL three-valued logic (TRUE, FALSE, NULL) causes naive boolean filters to silently drop NULL records.

**Buggy Code** (Silent Data Loss):
```python
valid_df = df.filter(col("is_valid"))  # Excludes NULL!
invalid_df = df.filter(~col("is_valid"))  # Also excludes NULL!
# Result: NULL records disappear from BOTH streams
```

**Correct Code** (No Data Loss):
```python
# IMPORTANT: Handle NULL explicitly
valid_df = df.filter(col("is_valid") == True)  # Only TRUE
invalid_df = df.filter((col("is_valid") == False) | col("is_valid").isNull())  # FALSE or NULL
```

**Why critical**: Prevents records from disappearing silently (not in Silver, not in DLQ)

**Reference**: [Decision #014](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-014)

---

### 2. Container Configuration Drift (docker restart vs docker compose up -d)

**Problem**: Running containers have stale parameters from previous docker-compose.yml version.

**Root Cause**: `docker restart` does NOT pick up docker-compose.yml changes.

**Correct Deployment**:
```bash
# WRONG (doesn't pick up docker-compose.yml changes)
docker restart k2-silver-binance-transformation

# CORRECT (recreates with new docker-compose.yml)
docker compose up -d silver-binance-transformation
```

**Why critical**: Ensures containers run with latest configuration after docker-compose.yml updates.

**Reference**: [Decision #016](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-016)

---

### 3. DLQ Pattern (No Silent Failures)

**Industry Best Practice**: All records must be accounted for (Silver or DLQ).

**Implementation**:
1. Split streams: Valid → Silver, Invalid (FALSE or NULL) → DLQ
2. Capture full context: raw_bytes, error_reason, error_type, lineage metadata
3. Monitor DLQ rate: Target <0.1%, alert if >1%

**Why critical**:
- No silent data loss (all failures observable)
- Debugging enabled (can investigate why records failed)
- Recovery possible (can replay from Bronze after fixes)

**Reference**: [Decision #014](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-014)

---

### 4. Resource Right-Sizing

**Principle**: Size resources to actual workload, not theoretical maximums.

**Implementation**:
- Spark workers: 6 cores, 6GB total (50% reduction from initial 8 cores, 12GB)
- Executors: 1 core per streaming job (reduced from 2 cores)
- Freed ~6GB RAM for Kafka and other services

**Why critical**:
- Eliminates resource contention (Kafka no longer OOM killed)
- Allows all 4 streaming jobs to run concurrently
- 33% resource headroom for burst processing

**Reference**: [Decision #015](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-015)

---

## Monitoring & Observability

### Key Metrics

1. **Spark Master UI** (http://localhost:8080):
   - `coresused/cores` - should stay < 80% in steady state
   - Applications WAITING count - should be 0
   - Task failure rate

2. **Container Resource Usage**:
   ```bash
   docker stats --format "table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}"
   ```
   - CPU: < 50% per worker in steady state
   - Memory: < 75% of limit

3. **DLQ Rate**:
   ```sql
   SELECT bronze_source, COUNT(*) as dlq_count
   FROM iceberg.market_data.silver_dlq_trades
   GROUP BY bronze_source;
   ```
   - Target: < 0.1% of Silver records
   - Alert: > 1%

4. **Data Flow**:
   ```bash
   docker logs <job> | grep "Committing.*silver_binance_trades" | tail -5
   ```
   - Should see commits every 30 seconds (Silver trigger interval)

---

## Troubleshooting

**Common Issues**:
1. **Silver jobs stuck in WAITING**: Resource allocation issue → See [Bronze/Silver Streaming Troubleshooting Runbook](../../operations/runbooks/bronze-silver-streaming-troubleshooting.md#issue-1)
2. **Kafka OOM killed (exit 137)**: Resource starvation → See [Bronze/Silver Streaming Troubleshooting Runbook](../../operations/runbooks/bronze-silver-streaming-troubleshooting.md#issue-2)
3. **Silent data loss (records disappearing)**: NULL validation bug → See [Bronze/Silver Streaming Troubleshooting Runbook](../../operations/runbooks/bronze-silver-streaming-troubleshooting.md#issue-3)
4. **Bronze jobs failing to connect**: Kafka connectivity issue → See [Bronze/Silver Streaming Troubleshooting Runbook](../../operations/runbooks/bronze-silver-streaming-troubleshooting.md#issue-4)

**Full Runbook**: [Bronze/Silver Streaming Troubleshooting](../../operations/runbooks/bronze-silver-streaming-troubleshooting.md)

---

## Related Documentation

### Architecture Decisions
- [Decision #011: Bronze Stores Raw Bytes](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-011)
- [Decision #012: Silver Uses DLQ Pattern](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-012)
- [Decision #014: Explicit NULL Handling](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-014)
- [Decision #015: Right-Sized Resource Allocation](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-015)
- [Decision #016: 1 Core Per Streaming Job](../../phases/phase-10-streaming-crypto/DECISIONS.md#decision-016)

### Troubleshooting Docs
- [SILVER_FIX_SUMMARY.md](../../phases/phase-10-streaming-crypto/SILVER_FIX_SUMMARY.md) - NULL validation and resource fixes
- [RESOURCE_ALLOCATION_FIX.md](../../phases/phase-10-streaming-crypto/RESOURCE_ALLOCATION_FIX.md) - Spark resource allocation deep dive
- [Bronze/Silver Streaming Troubleshooting Runbook](../../operations/runbooks/bronze-silver-streaming-troubleshooting.md)

### Implementation Steps
- [Step 10: Bronze Ingestion Job](../../phases/phase-10-streaming-crypto/steps/step-10-bronze-job.md)
- [Step 11: Silver Transformation Job](../../phases/phase-10-streaming-crypto/steps/step-11-silver-transformation.md)
- [Step 12: Gold Aggregation Job](../../phases/phase-10-streaming-crypto/steps/step-12-gold-job.md) [PLANNED]

---

**Last Updated**: 2026-01-19
**Maintained By**: Data Engineering Team
