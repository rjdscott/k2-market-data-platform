# Phase 5 Bronze Jobs - Implementation Status

**Date:** 2026-01-18
**Status:** 🟡 In Progress - Blocked on Kafka Configuration
**Completion:** 85%

---

## Summary

Bronze ingestion jobs have been implemented and tested. Jobs start successfully but are blocked by Kafka advertised listener configuration preventing Spark containers from connecting to Kafka.

---

## Completed Work

### 1. Directory Structure ✅
```
src/k2/spark/jobs/streaming/
├── __init__.py (749 bytes)
├── bronze_binance_ingestion.py (5.5 KB, 167 lines)
└── bronze_kraken_ingestion.py (5.5 KB, 167 lines)
```

### 2. Bronze Binance Ingestion Job ✅

**File:** `src/k2/spark/jobs/streaming/bronze_binance_ingestion.py`

**Configuration:**
- Source: `market.crypto.trades.binance` (Kafka topic)
- Target: `bronze_binance_trades` (Iceberg table)
- Trigger: 10 seconds (high volume)
- Max offsets per trigger: 10,000 messages
- Checkpoint: `/checkpoints/bronze-binance/`
- Workers: 3 (configured via spark-submit)

**Key Features:**
- Raw bytes ingestion (no Avro deserialization in Bronze)
- Structured Streaming with Iceberg sink
- Fault-tolerant checkpointing
- Fanout-enabled writes for better throughput

**Schema (8 fields):**
- message_key (STRING)
- avro_payload (BINARY) - Raw V2 Avro bytes
- topic (STRING)
- partition (INT)
- offset (BIGINT)
- kafka_timestamp (TIMESTAMP)
- ingestion_timestamp (TIMESTAMP)
- ingestion_date (DATE) - Partition key

### 3. Bronze Kraken Ingestion Job ✅

**File:** `src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py`

**Configuration:**
- Source: `market.crypto.trades.kraken` (Kafka topic)
- Target: `bronze_kraken_trades` (Iceberg table)
- Trigger: 30 seconds (lower volume)
- Max offsets per trigger: 1,000 messages
- Checkpoint: `/checkpoints/bronze-kraken/`
- Workers: 1 (configured via spark-submit)

**Key Features:** (Same as Binance, different scale)

### 4. Infrastructure Setup ✅

**Kafka JARs Downloaded:**
- `spark-sql-kafka-0-10_2.12-3.5.3.jar` (423 KB)
- `kafka-clients-3.5.1.jar` (5.1 MB)
- `commons-pool2-2.11.1.jar` (143 KB)
- `spark-token-provider-kafka-0-10_2.12-3.5.3.jar` (56 KB)

**Location:** `/home/rjdscott/Documents/projects/k2-market-data-platform/spark-jars/`
**Mounted At:** `/opt/spark/jars-extra/` in Spark containers

**Checkpoint Directories Created:**
- `/checkpoints/bronze-binance/` (owner: spark:spark, 755)
- `/checkpoints/bronze-kraken/` (owner: spark:spark, 755)

---

## Current Blocker 🔴

### Issue: Kafka Advertised Listener Misconfiguration

**Symptom:**
```
26/01/18 07:43:07 WARN NetworkClient: [AdminClient clientId=adminclient-1]
Connection to node 1 (localhost/127.0.0.1:9092) could not be established.
Broker may not be available.
```

**Root Cause:**

Kafka broker advertises two listeners:
```yaml
KAFKA_ADVERTISED_LISTENERS:
  - PLAINTEXT://kafka:29092      # Internal Docker network
  - PLAINTEXT_HOST://localhost:9092  # Host access
```

When Spark connects to `kafka:29092`, the Kafka client receives broker metadata that redirects it to `localhost:9092` (the PLAINTEXT_HOST listener), which is unreachable from Spark containers.

**Kafka Configuration (docker-compose.yml):**
```yaml
kafka:
  environment:
    KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
    KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
    KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
```

**Spark Connection Attempt:**
```python
kafka_df = spark.readStream.format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:29092") \
    .option("subscribe", "market.crypto.trades.binance") \
    .load()
```

---

## Solution Options

### Option 1: Update Kafka Advertised Listeners (Recommended)

**Change `docker-compose.yml`:**
```yaml
kafka:
  environment:
    # Use kafka:29092 as primary advertised listener for internal clients
    KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
    KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
    KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
    # Explicitly set listener for Spark clients
    KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:29092,PLAINTEXT_HOST://0.0.0.0:9092
```

**Impact:**
- Requires Kafka restart
- Existing producers (Binance/Kraken) should continue working (they use kafka:29092)
- No code changes needed in Spark jobs

### Option 2: Add Client Override in Spark Jobs

**Add to Bronze jobs:**
```python
kafka_df = spark.readStream.format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:29092") \
    .option("client.dns.lookup", "resolve_canonical_bootstrap_servers_only") \
    .option("subscribe", "market.crypto.trades.binance") \
    .load()
```

**Impact:**
- No infrastructure changes
- Requires code changes in both Bronze jobs
- May not fully resolve metadata redirect issue

### Option 3: Use Single Advertised Listener

**Change `docker-compose.yml`:**
```yaml
kafka:
  environment:
    # Remove localhost listener, use only internal network listener
    KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092
    KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:29092
```

**Impact:**
- Host-based Kafka access on localhost:9092 will no longer work
- Spark jobs will work correctly
- Requires Kafka restart

---

## Testing Performed

### Successful Tests ✅
1. Spark job starts successfully
2. Connects to Spark master (spark://spark-master:7077)
3. Loads Iceberg and Kafka JARs correctly
4. Creates checkpoint directory structure
5. Initializes Spark session with Iceberg catalog
6. Configures Kafka stream reader

### Failed Tests 🔴
1. Kafka broker connection: Redirects to localhost:9092 (unreachable)
2. Stream processing not started (blocked on Kafka connection)
3. No data written to Bronze tables (stream never starts)

### Verification Commands

**Check Spark job status:**
```bash
docker exec k2-spark-master bash -c "cd / && /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --deploy-mode client \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/spark-sql-kafka-0-10_2.12-3.5.3.jar,/opt/spark/jars-extra/kafka-clients-3.5.1.jar,/opt/spark/jars-extra/commons-pool2-2.11.1.jar,/opt/spark/jars-extra/spark-token-provider-kafka-0-10_2.12-3.5.3.jar \
  /opt/k2/src/k2/spark/jobs/streaming/bronze_binance_ingestion.py"
```

**Check Kafka connectivity from Spark:**
```bash
docker exec k2-spark-master getent hosts kafka
# Output: 172.19.0.4      kafka (✓ DNS works)
```

**Check Kafka advertised listeners:**
```bash
docker exec k2-kafka env | grep KAFKA_ADVERTISED
# Output: KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
```

---

## Next Steps

### Immediate (Resolve Blocker)
1. **Update Kafka configuration** (Option 1 or 3)
2. Restart Kafka: `docker-compose restart kafka`
3. Wait 30 seconds for Kafka to stabilize
4. Re-test Bronze Binance ingestion job (45-second run)
5. Verify data in Bronze table:
   ```sql
   SELECT COUNT(*) FROM iceberg.market_data.bronze_binance_trades;
   ```

### After Blocker Resolution
6. Test Bronze Kraken ingestion job
7. Run both jobs in parallel for 5 minutes
8. Verify checkpoint recovery (kill + restart test)
9. Measure ingestion throughput and latency
10. Update PROGRESS.md: Mark Phase 5.10 complete
11. Create PHASE-5-BRONZE-COMPLETE.md documentation
12. Proceed to Phase 5.11: Silver transformation jobs

---

## Files Created/Modified

### Created
- `src/k2/spark/jobs/streaming/__init__.py`
- `src/k2/spark/jobs/streaming/bronze_binance_ingestion.py`
- `src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py`
- `spark-jars/spark-sql-kafka-0-10_2.12-3.5.3.jar`
- `spark-jars/kafka-clients-3.5.1.jar`
- `spark-jars/commons-pool2-2.11.1.jar`
- `spark-jars/spark-token-provider-kafka-0-10_2.12-3.5.3.jar`
- `/checkpoints/bronze-binance/` (in Docker volume)
- `/checkpoints/bronze-kraken/` (in Docker volume)

### Modified
- Fixed Kafka bootstrap servers: `kafka:9092` → `kafka:29092` (both jobs)

### To Be Modified (After Resolution)
- `docker-compose.yml` (Kafka advertised listeners - pending user decision)

---

## Decision Required

**User Input Needed:**

Which solution option should we implement for the Kafka advertised listener issue?

- **Option 1**: Update Kafka config to prioritize internal listener (recommended, no code changes)
- **Option 2**: Add client override in Spark jobs (code changes, may not fully work)
- **Option 3**: Use single advertised listener (breaks host-based Kafka access)

**Recommendation:** Option 1 - Update Kafka advertised listeners to prioritize internal Docker network listener for service-to-service communication.

---

**Status:** Ready to proceed once Kafka configuration is updated.
**Estimated Time to Completion:** 30 minutes after blocker resolution (testing + verification).
