# Bronze Streaming Troubleshooting Runbook

**Service**: Bronze Streaming Jobs (Kafka → Iceberg)
**Components**: bronze-binance-stream, bronze-kraken-stream
**Last Updated**: 2026-01-18
**Owner**: Data Engineering Team

---

## Overview

Bronze streaming jobs consume raw Avro messages from Kafka topics and write them to per-exchange Bronze Iceberg tables. This runbook covers common issues, diagnostics, and resolution steps.

**Architecture:**
```
Kafka Topics                  Spark Streaming Jobs              Iceberg Tables
───────────────              ────────────────────             ─────────────────
market.crypto.trades.binance → bronze-binance-stream → bronze_binance_trades
market.crypto.trades.kraken  → bronze-kraken-stream  → bronze_kraken_trades
```

**Key Characteristics:**
- **Trigger Interval**: Binance: 10s, Kraken: 30s
- **Max Offsets**: Binance: 10K/batch, Kraken: 1K/batch
- **Checkpoints**: `/checkpoints/bronze-{exchange}/`
- **Resource Allocation**: 2 cores per job (4 cores total on 2-worker cluster)
- **Starting Offsets**: `latest` (only new messages after job starts)

---

## Quick Health Check

Run this command to get a quick overview of all Bronze streaming components:

```bash
# Check all Bronze components
./scripts/ops/check-bronze-health.sh
```

**Manual checks:**

```bash
# 1. Check Bronze streaming services
docker ps | grep bronze
# Should show both containers running

# 2. Check Spark Web UI
curl -s http://localhost:8090 | grep -A 5 "Running Applications"
# Should show both Bronze jobs with 2 cores each

# 3. Check Bronze table counts
docker exec k2-spark-master spark-sql \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  -e "SELECT 'binance', COUNT(*) FROM iceberg.market_data.bronze_binance_trades UNION ALL SELECT 'kraken', COUNT(*) FROM iceberg.market_data.bronze_kraken_trades;"

# 4. Check Kafka topics
docker exec k2-kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.binance --max-messages 3
```

---

## Common Issues

### Issue 1: Bronze Table Empty / No Data Ingestion

**Symptoms:**
- `SELECT COUNT(*) FROM bronze_binance_trades` returns 0
- Job shows as RUNNING in Spark UI
- Producer shows trades streaming with 0 errors

**Diagnosis Steps:**

```bash
# 1. Check if Bronze job is RUNNING (not WAITING)
curl -s http://localhost:8090 | grep -A 10 "Bronze"
# Look for "RUNNING" state and non-zero cores allocated

# 2. Check if Kafka has messages
docker exec k2-kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.binance --max-messages 5 --timeout-ms 5000

# 3. Check job logs for errors
docker logs k2-bronze-binance-stream --tail 100 | grep -i error

# 4. Check checkpoint location
docker exec k2-spark-master ls -la /checkpoints/bronze-binance/
```

**Common Causes & Fixes:**

**A. Job in WAITING state (resource starvation)**
- **Symptom**: Spark UI shows job with 0 cores allocated
- **Cause**: Other jobs using all cluster resources
- **Fix**:
  ```bash
  # Kill competing jobs in Spark UI or restart cluster
  docker-compose restart spark-master spark-worker-1 spark-worker-2
  docker-compose restart bronze-binance-stream bronze-kraken-stream
  ```

**B. Starting offset is "latest" - messages before job start are missed**
- **Symptom**: Job running but table empty, old Kafka messages exist
- **Cause**: Job uses `startingOffsets: latest`, only processes new messages
- **Fix**: Either wait for new messages or change to "earliest" and restart:
  ```python
  # Edit bronze_*_ingestion.py
  .option("startingOffsets", "earliest")  # Instead of "latest"
  ```

**C. Kafka connectivity issues**
- **Symptom**: Job logs show `Connection to node -1 could not be established`
- **Cause**: Wrong Kafka bootstrap server or network issues
- **Fix**: Verify Kafka is healthy and accessible:
  ```bash
  docker exec k2-kafka kafka-broker-api-versions --bootstrap-server localhost:9092
  # Should return API versions if healthy
  ```

**D. Producer not sending to Kafka**
- **Symptom**: Producer shows "trades_streamed" but Kafka topic empty
- **Cause**: Producer configuration or serialization errors
- **Fix**: Check producer logs:
  ```bash
  docker logs k2-binance-stream --tail 200 | grep -E "(error|failed|producing)"
  ```

---

### Issue 2: Job Crashes / Keeps Restarting

**Symptoms:**
- `docker ps` shows container restarting repeatedly
- Spark UI shows job completing quickly or failing

**Diagnosis:**

```bash
# 1. Check container status
docker ps -a | grep bronze
# Look for "Restarting" or recent restarts

# 2. Check logs for errors
docker logs k2-bronze-binance-stream --tail 200

# 3. Check Spark driver logs
curl -s http://localhost:8090/app/?appId=<app-id> | grep -i exception
```

**Common Causes & Fixes:**

**A. AWS SDK region error**
- **Symptom**: `Unable to load region from any of the providers in the chain`
- **Cause**: Missing AWS region for MinIO S3 compatibility
- **Fix**: Verify AWS_REGION in docker-compose.yml:
  ```yaml
  environment:
    - AWS_REGION=us-east-1
  command: >
    --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1'
    --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1'
  ```

**B. Checkpoint corruption**
- **Symptom**: `Checkpoint mismatch` or schema evolution errors
- **Cause**: Checkpoint metadata incompatible with current schema
- **Fix**: Clear checkpoint and restart (DATA LOSS - reprocesses from latest):
  ```bash
  docker exec k2-spark-master rm -rf /checkpoints/bronze-binance/*
  docker-compose restart bronze-binance-stream
  ```

**C. Iceberg catalog connection failure**
- **Symptom**: `Failed to load catalog` or connection timeout
- **Cause**: Iceberg REST catalog not healthy
- **Fix**: Check catalog health:
  ```bash
  curl http://localhost:8181/v1/config
  # Should return catalog configuration

  docker-compose restart iceberg-rest
  ```

**D. Out of memory**
- **Symptom**: Job killed, logs show `OutOfMemoryError` or `Container killed on request`
- **Cause**: Insufficient memory allocation
- **Fix**: Increase memory limits in docker-compose.yml:
  ```yaml
  deploy:
    resources:
      limits:
        memory: 4G  # Increase from 2G
  ```

---

### Issue 3: Job Running But No Recent Data

**Symptoms:**
- Table has old data but no new records
- Job shows RUNNING in Spark UI
- Producers showing recent trades

**Diagnosis:**

```bash
# 1. Check latest ingestion timestamp
docker exec k2-spark-master spark-sql \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  -e "SELECT exchange, MAX(ingestion_timestamp) as latest FROM iceberg.market_data.bronze_binance_trades GROUP BY exchange;"

# 2. Check Kafka consumer lag
docker exec k2-kafka kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group <spark-consumer-group>

# 3. Check job processing rate in Spark UI
# Go to http://localhost:8090 → Application → Streaming tab
# Look for "Input Rate" and "Processing Time"
```

**Common Causes & Fixes:**

**A. Job stuck / not processing batches**
- **Symptom**: No recent ingestion timestamps, no input rate in Spark UI
- **Cause**: Job deadlock or hung state
- **Fix**: Restart job:
  ```bash
  docker-compose restart bronze-binance-stream
  ```

**B. Trigger interval too long**
- **Symptom**: Data arrives in large batches with delays
- **Cause**: Trigger interval set too high
- **Fix**: Reduce trigger interval in code (default: Binance 10s, Kraken 30s)

**C. Backpressure / slow writes to Iceberg**
- **Symptom**: Processing time > trigger interval, growing lag
- **Cause**: Slow S3/MinIO writes or Iceberg commit overhead
- **Fix**:
  ```bash
  # 1. Check MinIO health
  curl http://localhost:9000/minio/health/live

  # 2. Enable fanout writes (already enabled by default)
  # 3. Check Iceberg table compaction
  docker exec k2-spark-master spark-sql \
    -e "CALL iceberg.system.rewrite_data_files('market_data.bronze_binance_trades');"
  ```

---

### Issue 4: Duplicate Records in Bronze Table

**Symptoms:**
- Same trade appears multiple times
- `message_id` or `offset` duplicates found

**Diagnosis:**

```bash
# Check for duplicates
docker exec k2-spark-master spark-sql \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  -e "SELECT offset, COUNT(*) as cnt FROM iceberg.market_data.bronze_binance_trades GROUP BY offset HAVING cnt > 1;"
```

**Common Causes & Fixes:**

**A. Checkpoint cleared mid-processing**
- **Cause**: Checkpoint deleted while job running
- **Fix**: Prevention - never delete checkpoints while job is running
- **Remediation**: Deduplicate using Iceberg MERGE or ROW_NUMBER window function

**B. Multiple jobs writing to same table**
- **Cause**: Accidentally running duplicate Bronze jobs
- **Fix**: Check running jobs:
  ```bash
  docker ps | grep bronze
  # Should only show one container per exchange

  # Check Spark UI for duplicate applications
  curl -s http://localhost:8090 | grep "Bronze-Binance"
  ```

**C. At-least-once delivery semantics**
- **Cause**: Kafka's at-least-once guarantee (expected behavior in failure scenarios)
- **Fix**: Downstream Silver layer deduplicates using `message_id`

---

### Issue 5: High Memory Usage / OOM Errors

**Symptoms:**
- Container frequently restarting
- Docker stats shows memory limit hit
- Logs show `java.lang.OutOfMemoryError`

**Diagnosis:**

```bash
# 1. Check current memory usage
docker stats k2-bronze-binance-stream --no-stream

# 2. Check Spark executor memory
curl -s http://localhost:8090/app/?appId=<app-id> | grep -i "executor memory"

# 3. Check batch sizes
docker logs k2-bronze-binance-stream | grep "Input Rows"
```

**Common Causes & Fixes:**

**A. Batch size too large**
- **Cause**: `maxOffsetsPerTrigger` set too high
- **Fix**: Reduce batch size in code:
  ```python
  .option("maxOffsetsPerTrigger", 5000)  # Reduce from 10000
  ```

**B. Insufficient memory allocation**
- **Fix**: Increase limits in docker-compose.yml:
  ```yaml
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 4G  # Increase from 2G
  ```

**C. Memory leak in long-running job**
- **Symptom**: Memory grows over time
- **Fix**: Restart job periodically or investigate with heap dump

---

## Monitoring

### Key Metrics to Watch

**Spark Web UI (http://localhost:8090)**
- Application status: RUNNING vs WAITING
- Cores allocated: Should be 2 per job
- Input rate: Messages/sec being consumed
- Processing time: Should be < trigger interval
- Active batches: Should progress incrementally

**Kafka Metrics**
```bash
# Consumer lag
docker exec k2-kafka kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --all-groups | grep bronze
```

**Bronze Table Metrics**
```sql
-- Recent ingestion stats
SELECT
  ingestion_date,
  COUNT(*) as records,
  MIN(ingestion_timestamp) as first_record,
  MAX(ingestion_timestamp) as last_record
FROM iceberg.market_data.bronze_binance_trades
WHERE ingestion_date >= CURRENT_DATE() - INTERVAL 1 DAY
GROUP BY ingestion_date
ORDER BY ingestion_date DESC;

-- Table size
SELECT
  'binance' as exchange,
  COUNT(*) as total_records,
  COUNT(DISTINCT ingestion_date) as days_of_data
FROM iceberg.market_data.bronze_binance_trades
UNION ALL
SELECT
  'kraken',
  COUNT(*),
  COUNT(DISTINCT ingestion_date)
FROM iceberg.market_data.bronze_kraken_trades;
```

### Alerting Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Job status | WAITING | FAILED | Investigate resource allocation or errors |
| Input rate | <100 msg/s for 5m | 0 msg/s for 10m | Check Kafka connectivity, producer health |
| Processing time | >trigger interval | >2x trigger interval | Reduce batch size or scale cluster |
| Consumer lag | >100K messages | >1M messages | Increase parallelism or reduce batch size |
| Memory usage | >80% | >95% | Increase memory limits or reduce batch size |
| Table growth | No growth for 1h | No growth for 4h | Check end-to-end pipeline |

---

## Recovery Procedures

### Procedure 1: Full Pipeline Restart

**When to use**: Major failures, after infrastructure changes

```bash
# 1. Stop Bronze jobs
docker-compose stop bronze-binance-stream bronze-kraken-stream

# 2. Wait for graceful shutdown (checkpoints saved)
sleep 10

# 3. Verify no running Spark jobs
curl -s http://localhost:8090 | grep "Running Applications (0)"

# 4. Restart Bronze jobs
docker-compose up -d bronze-binance-stream bronze-kraken-stream

# 5. Verify startup
docker logs k2-bronze-binance-stream -f
# Should see "✓ Bronze writer started"
```

### Procedure 2: Checkpoint Reset (DATA LOSS)

**When to use**: Checkpoint corruption, schema changes

**⚠️ WARNING**: This will cause reprocessing from latest offset, missing historical data.

```bash
# 1. Stop job
docker-compose stop bronze-binance-stream

# 2. Clear checkpoint
docker exec k2-spark-master rm -rf /checkpoints/bronze-binance/*

# 3. Restart job (will start from latest Kafka offset)
docker-compose up -d bronze-binance-stream

# 4. Monitor for successful restart
docker logs k2-bronze-binance-stream -f
```

### Procedure 3: Kafka Offset Reset

**When to use**: Need to reprocess historical data

```bash
# 1. Stop Bronze job
docker-compose stop bronze-binance-stream

# 2. Reset Kafka consumer group offset
docker exec k2-kafka kafka-consumer-groups --bootstrap-server localhost:9092 \
  --group <spark-consumer-group-id> \
  --topic market.crypto.trades.binance \
  --reset-offsets --to-earliest --execute

# 3. Clear checkpoint
docker exec k2-spark-master rm -rf /checkpoints/bronze-binance/*

# 4. Restart job
docker-compose up -d bronze-binance-stream
```

---

## Configuration Reference

### Docker Compose Configuration

```yaml
bronze-binance-stream:
  image: apache/spark:3.5.3
  environment:
    - AWS_REGION=us-east-1  # Required for MinIO S3
  volumes:
    - ./src:/opt/k2/src
    - ./config:/opt/k2/config
    - ./spark-jars:/opt/spark/jars-extra
    - spark-checkpoints:/checkpoints
  restart: unless-stopped
  command: >
    /opt/spark/bin/spark-submit
    --master spark://spark-master:7077
    --total-executor-cores 2          # Limit to prevent resource starvation
    --executor-cores 2
    --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1'
    --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1'
    --jars /opt/spark/jars-extra/[...] # Iceberg, Kafka, AWS SDK jars
    /opt/k2/src/k2/spark/jobs/streaming/bronze_binance_ingestion.py
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 2G
```

### Job Configuration (bronze_binance_ingestion.py)

```python
# Kafka source
kafka_df = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", "kafka:29092")     # Internal listener
    .option("subscribe", "market.crypto.trades.binance")
    .option("startingOffsets", "latest")                   # Only new messages
    .option("maxOffsetsPerTrigger", 10000)                # Batch size limit
    .option("failOnDataLoss", "false")                    # Continue on missing data
    .load()
)

# Iceberg sink
query = (
    bronze_df.writeStream.format("iceberg")
    .outputMode("append")
    .trigger(processingTime="10 seconds")                 # Trigger interval
    .option("checkpointLocation", "/checkpoints/bronze-binance/")
    .option("path", "iceberg.market_data.bronze_binance_trades")
    .option("fanout-enabled", "true")                     # Parallel writes
    .partitionBy("ingestion_date")                        # Daily partitions
    .start()
)
```

---

## Escalation

### When to Escalate

1. **Data loss detected**: Missing data that cannot be recovered from Kafka
2. **Persistent failures**: Job crashes repeatedly after following all procedures
3. **Performance degradation**: Processing time consistently >2x trigger interval
4. **Capacity planning**: Sustained consumer lag >1M messages

### Escalation Path

1. **L1 - On-call Engineer**: Follow this runbook
2. **L2 - Data Engineering Team**: Complex checkpoint issues, schema changes
3. **L3 - Platform Team**: Spark cluster sizing, infrastructure issues

### Information to Collect

```bash
# 1. Job logs (last 500 lines)
docker logs k2-bronze-binance-stream --tail 500 > bronze-binance-logs.txt

# 2. Spark UI screenshot or HTML
curl http://localhost:8090 > spark-ui.html

# 3. Kafka consumer group status
docker exec k2-kafka kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --all-groups > kafka-consumer-groups.txt

# 4. Bronze table stats
docker exec k2-spark-master spark-sql \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  -e "SELECT * FROM iceberg.market_data.bronze_binance_trades.snapshots ORDER BY committed_at DESC LIMIT 10;" \
  > bronze-snapshots.txt

# 5. Docker stats
docker stats --no-stream > docker-stats.txt
```

---

## Related Documentation

- [Spark Iceberg Queries Notebook](../../../demos/notebooks/spark-iceberg-queries.ipynb)
- [Phase 10: Streaming Crypto Architecture](../../phases/v1/phase-10-streaming-crypto/README.md)
- [Binance Streaming Runbook](./binance-streaming.md)
- [Kafka Checkpoint Corruption Recovery](./kafka-checkpoint-corruption-recovery.md)

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-18 | Data Engineering | Initial version - Bronze streaming troubleshooting |
