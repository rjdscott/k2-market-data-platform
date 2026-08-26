# Runbook: Bronze Job Troubleshooting

**Severity**: High
**Last Updated**: 2026-01-18
**Maintained By**: Data Engineering Team

---

## Overview

This runbook covers common issues with Spark Structured Streaming Bronze ingestion jobs that read from Kafka and write to Iceberg tables. Both Binance and Kraken Bronze jobs follow the same architecture.

**Bronze Jobs**:
- `k2-bronze-binance-stream` - Ingests from `market.crypto.trades.binance.raw`
- `k2-bronze-kraken-stream` - Ingests from `market.crypto.trades.kraken.raw`

---

## Issue 1: Avro Deserialization Error - "Malformed data. Length is negative: -6"

### Symptoms
```log
org.apache.avro.AvroRuntimeException: Malformed data. Length is negative: -6
org.apache.spark.SparkException: Malformed records are detected in record parsing. Current parse Mode: FAILFAST.
```

Job starts successfully but fails when attempting to deserialize Kafka messages. `numInputRows: 0` in all batches.

### Diagnosis

**Step 1: Check if job is reading from Kafka**
```bash
docker logs k2-bronze-kraken-stream | grep "numInputRows"
```
If you see `"numInputRows" : 0` consistently, the job isn't processing messages.

**Step 2: Verify Kafka has messages**
```bash
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.kraken.raw \
  --max-messages 1 \
  --from-beginning
```
If this returns messages, Kafka is working.

**Step 3: Check job logs for Avro errors**
```bash
docker logs k2-bronze-kraken-stream 2>&1 | grep -A 10 "AvroRuntimeException"
```

### Root Cause

The job is attempting to deserialize Kafka messages **without stripping the 5-byte Confluent Schema Registry header**.

**Schema Registry Message Format**:
```
[Magic Byte: 1 byte][Schema ID: 4 bytes][Avro Binary Data: N bytes]
```

Avro deserialization expects raw binary data, not Schema Registry format. The error "Length is negative: -6" occurs when Avro tries to parse the Schema Registry header as Avro data.

### Resolution

**Step 1: Verify code has header stripping**

Check the Bronze job code (e.g., `src/k2/spark/jobs/streaming/bronze_kraken_ingestion.py`):

**Correct Pattern** (with header stripping):
```python
# Strip Schema Registry header (5 bytes: 1 magic byte + 4-byte schema ID)
kafka_df_no_header = kafka_df.selectExpr(
    "substring(value, 6, length(value)-5) as avro_data",  # ✅ Strips 5-byte header
    "topic", "partition", "offset", "timestamp", "key"
)

# Deserialize Avro value (now without Schema Registry header)
trades_df = kafka_df_no_header.select(
    from_avro(col("avro_data"), avro_schema).alias("trade"),
    col("topic"), col("partition"), col("offset")
)
```

**Incorrect Pattern** (missing header stripping):
```python
# ❌ Missing header stripping - will fail!
trades_df = kafka_df.select(
    from_avro(col("value"), avro_schema).alias("trade")
)
```

**Step 2: Update code if needed**

If the code is missing header stripping, add the `selectExpr` step before `from_avro()`.

**Step 3: Clear checkpoint and restart**

After updating code, clear the checkpoint to avoid resuming from corrupted state:
```bash
# Stop job
docker stop k2-bronze-kraken-stream

# Clear checkpoint (use spark-worker container, NOT the job container)
docker exec k2-spark-worker-1 rm -rf /checkpoints/bronze-kraken

# Restart job
docker start k2-bronze-kraken-stream
```

**Step 4: Verify fix**
```bash
# Wait 30 seconds for job to initialize
sleep 30

# Check logs for successful processing
docker logs k2-bronze-kraken-stream 2>&1 | grep -E "(numInputRows|numOutputRows)"
```

Expected output:
```json
"numInputRows" : 6,
"numOutputRows" : 6
```

### Prevention

**Code Review Checklist**:
- [ ] All Bronze jobs strip Schema Registry header before Avro deserialization
- [ ] Pattern matches working jobs (e.g., compare Kraken vs Binance)
- [ ] Test deserialization with sample Kafka message before deploying

**Best Practice**:
When using Confluent Schema Registry with Spark Structured Streaming and `from_avro()`, **ALWAYS** strip the 5-byte header:
```python
substring(value, 6, length(value)-5)
```

---

## Issue 2: Checkpoint State Corruption

### Symptoms
- Job restarts but immediately fails with same error
- Clearing checkpoint via job container doesn't work
- Error persists even with fresh Kafka messages

### Diagnosis

**Check checkpoint location**:
```bash
# Try to list checkpoint (may fail if job container has wrong volume)
docker exec k2-bronze-kraken-stream ls -la /checkpoints/bronze-kraken/

# Check via Spark worker (correct volume mount)
docker exec k2-spark-worker-1 ls -la /checkpoints/bronze-kraken/
```

### Root Cause

Checkpoints are stored in a Docker volume (`spark-checkpoints`) that is mounted to Spark workers, not the job containers. Attempting to clear checkpoint via the job container clears a different directory.

### Resolution

**Step 1: Stop the Bronze job**
```bash
docker stop k2-bronze-kraken-stream
```

**Step 2: Clear checkpoint via Spark worker**
```bash
# Use spark-worker container (has correct volume mount)
docker exec k2-spark-worker-1 rm -rf /checkpoints/bronze-kraken
```

**Step 3: Verify checkpoint cleared**
```bash
docker exec k2-spark-worker-1 ls -la /checkpoints/
# Should NOT see bronze-kraken directory
```

**Step 4: Restart job**
```bash
docker start k2-bronze-kraken-stream
```

The job will now start from latest offsets (no checkpoint history).

### Prevention

**Operational Notes**:
- Always clear checkpoints via `k2-spark-worker-1` container
- Document checkpoint locations in deployment notes
- Use checkpoint version numbers for schema migrations

---

## Issue 3: Job Not Processing Messages (numInputRows: 0)

### Symptoms
- Job starts successfully
- Connects to Kafka without errors
- `numInputRows: 0` in all batches
- Kafka topic has messages

### Diagnosis

**Step 1: Check Kafka connection**
```bash
docker logs k2-bronze-kraken-stream 2>&1 | grep -i kafka
```
Look for connection errors or listener issues.

**Step 2: Check starting offset configuration**
```bash
docker logs k2-bronze-kraken-stream | grep -i "startingOffsets"
```

If configured as `startingOffsets=latest`, the job only processes NEW messages after startup.

**Step 3: Verify Kafka has messages**
```bash
docker exec k2-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group test-group \
  --topic market.crypto.trades.kraken.raw \
  --reset-offsets --to-latest --execute
```

Check partition offsets. If offsets > 0, messages exist.

### Root Cause

**Scenario A**: Job configured with `startingOffsets=latest`, no new messages arriving
**Scenario B**: Checkpoint exists with old offsets, topic was recreated (offset mismatch)
**Scenario C**: Deserialization failing silently (check for Avro errors)

### Resolution

**For Scenario A**: Wait for new messages or change to `startingOffsets=earliest`

**For Scenario B**: Clear checkpoint (see Issue 2 above)

**For Scenario C**: See Issue 1 (Avro Deserialization)

**To process all messages from beginning**:
1. Stop job
2. Clear checkpoint
3. Update job code: `startingOffsets=earliest`
4. Restart job

### Prevention

**Configuration Best Practices**:
- Development: Use `startingOffsets=earliest` for testing
- Production: Use `startingOffsets=latest` to avoid reprocessing
- Document offset strategy in deployment notes

---

## Issue 4: Kafka Connection Refused

### Symptoms
```log
org.apache.kafka.common.errors.TimeoutException: Failed to connect to Kafka
java.net.ConnectException: Connection refused
```

### Diagnosis

**Step 1: Check if Kafka is running**
```bash
docker ps | grep kafka
```

**Step 2: Check Kafka logs**
```bash
docker logs k2-kafka --tail 50
```

**Step 3: Test Kafka connection from Spark network**
```bash
docker exec k2-spark-worker-1 nc -zv kafka 29092
```

### Root Cause

**Scenario A**: Kafka container crashed (OOM, resource limits)
**Scenario B**: Network connectivity issue between Spark and Kafka
**Scenario C**: Wrong Kafka bootstrap server address

### Resolution

**For Scenario A**: Restart Kafka
```bash
docker start k2-kafka
# Wait 30 seconds for initialization
sleep 30
# Verify
docker logs k2-kafka | grep "started (kafka.server.KafkaServer)"
```

**For Scenario B**: Check Docker network
```bash
docker network inspect k2-network
# Verify both Kafka and Spark containers are on the same network
```

**For Scenario C**: Verify bootstrap servers
```bash
docker logs k2-bronze-kraken-stream | grep "bootstrap.servers"
```
Should be `kafka:29092` (internal Docker network address).

### Prevention

**Monitoring**:
- Alert on Kafka container restarts
- Monitor Kafka memory usage (alert at 80% of limit)
- Health check: Kafka consumer lag

---

## General Debugging Workflow

### 1. Check End-to-End Pipeline

**Streaming Service → Kafka → Bronze**

```bash
# Step 1: Verify streaming service is producing
docker logs k2-kraken-stream --tail 20 | grep "message_delivered"

# Step 2: Verify Kafka has messages
docker exec k2-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group test-group

# Step 3: Verify Bronze job is running
docker ps | grep bronze-kraken

# Step 4: Check Bronze job logs
docker logs k2-bronze-kraken-stream --tail 50

# Step 5: Verify data in Bronze table
docker exec k2-spark-master /opt/spark/bin/spark-sql \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.iceberg.type=rest \
  --conf spark.sql.catalog.iceberg.uri=http://iceberg-rest:8181 \
  -e "SELECT COUNT(*) FROM iceberg.market_data.bronze_kraken_trades"
```

### 2. Common Log Patterns

**Success Pattern**:
```log
numInputRows: 6
numOutputRows: 6
Data source write committed
```

**Failure Pattern**:
```log
numInputRows: 0
StreamingQueryException
Job aborted due to stage failure
```

### 3. Key Metrics to Check

| Metric | Command | Expected Value |
|--------|---------|----------------|
| Kafka message count | `kafka-consumer-groups --describe` | > 0 for each partition |
| Job input rows | `grep numInputRows logs` | > 0 after 30s |
| Job output rows | `grep numOutputRows logs` | = input rows |
| Bronze table count | `SELECT COUNT(*)` | Increasing over time |
| Checkpoint exists | `ls /checkpoints/` | Directory present |

---

## Related Monitoring

**Dashboards**:
- Spark Web UI: http://localhost:8090
- Kafka Manager: (to be configured)

**Alerts**:
- Bronze job failed (job exit code != 0)
- No input rows for 5 minutes (`numInputRows == 0`)
- Avro deserialization errors (rate > 0)

**Metrics**:
- `bronze_job_input_rate` - Messages/sec from Kafka
- `bronze_job_output_rate` - Rows/sec to Iceberg
- `bronze_job_lag` - Time between Kafka timestamp and processing

---

## Post-Incident Checklist

After resolving a Bronze job issue:

- [ ] Document root cause in this runbook if new pattern
- [ ] Update code if architectural fix needed
- [ ] Add unit test to prevent regression
- [ ] Review other Bronze jobs for same issue
- [ ] Update monitoring/alerting if detection gap
- [ ] Create Jira ticket for permanent fix if workaround applied

---

## References

- **Debug Session**: `docs/phases/phase-10-streaming-crypto/DEBUG_SESSION_KRAKEN.md`
- **Bronze Job Code**: `src/k2/spark/jobs/streaming/bronze_*_ingestion.py`
- **Schema Registry Docs**: https://docs.confluent.io/platform/current/schema-registry/
- **Spark Structured Streaming**: https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html
- **Iceberg Spark Integration**: https://iceberg.apache.org/docs/latest/spark-structured-streaming/

---

**Last Incident**: 2026-01-18 - Kraken Bronze Avro deserialization failure (Schema Registry header not stripped)
**Resolution**: Added header stripping, cleared checkpoint, verified 15+ trades in bronze_kraken_trades
**Resolved By**: Data Engineering Team
