# Spark Resource Monitoring & Maintenance Runbook

**Last Updated:** 2026-01-20
**Applies To:** K2 Market Data Platform (Spark Streaming Jobs)
**Audience:** Platform Engineers, SREs, Data Engineers

## Overview

This runbook covers monitoring and maintaining the Spark streaming cluster after the resource optimization fixes (Decision #013). Use this guide to:
- Monitor resource usage and detect issues early
- Perform routine maintenance tasks
- Troubleshoot common problems
- Respond to alerts

## Quick Reference

### Critical Commands

```bash
# Check container resource usage
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# Check checkpoint disk usage
docker exec k2-spark-master du -sh /checkpoints/*

# Run Iceberg maintenance (dry run)
docker exec k2-spark-master uv run python /opt/k2/scripts/iceberg_maintenance.py

# Restart streaming jobs
docker compose restart bronze-binance-stream silver-binance-transformation
```

### Service URLs

- **Spark Master UI:** http://localhost:8090
- **Kafka UI:** http://localhost:8080
- **MinIO Console:** http://localhost:9001
- **Grafana:** http://localhost:3000
- **Prometheus:** http://localhost:9090

---

## Daily Monitoring Tasks

### 1. Check Streaming Job Health

**Frequency:** Daily (morning)
**Duration:** 5 minutes

#### Check Running Containers

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "(spark|bronze|silver)"
```

**Expected output:**
- All spark-worker, bronze-stream, silver-transformation containers should be "Up"
- If any container restarted recently, investigate logs

#### Check Spark UI

Open http://localhost:8090 and verify:
- **Workers tab:** 2 workers registered, both "ALIVE"
- **Running Applications:** 4 applications running (2 bronze, 2 silver)

**Red flags:**
- Worker shows "DEAD" or missing
- Applications show "FAILED" or missing
- Application duration suddenly reset (indicates restart)

#### Check Streaming Query Progress

For each job, check Spark UI → Application → Streaming tab:

```bash
# Or use API (easier to script)
curl http://localhost:8090/api/v1/applications | jq '.[] | {id, name, attempts: .attempts | length}'
```

**Healthy indicators:**
- Batch processing time < trigger interval (10s for bronze, 30s for silver)
- Input rate relatively steady (no sudden drops to 0)
- State store memory < 100MB

**Warning signs:**
- Processing time > trigger interval (falling behind)
- Input rate 0 for > 5 minutes (upstream issue or consumer group problem)
- State store memory growing unbounded

---

### 2. Check Resource Usage

**Frequency:** Daily
**Duration:** 3 minutes

#### Memory Usage

```bash
docker stats --no-stream k2-spark-worker-1 k2-spark-worker-2 \
    k2-bronze-binance-stream k2-bronze-kraken-stream \
    k2-silver-binance-transformation k2-silver-kraken-transformation \
    --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"
```

**Thresholds:**
- **Green:** < 80% memory usage
- **Yellow:** 80-90% memory usage (investigate if sustained)
- **Red:** > 90% memory usage (risk of OOM kill)

**Actions:**
- **Yellow:** Check Spark UI for state store size, may need to tune retention
- **Red:** Restart job to clear memory, then investigate root cause

#### Disk Usage (Checkpoints)

```bash
docker exec k2-spark-master du -sh /checkpoints/*
```

**Expected:**
- Each checkpoint directory should be < 1GB
- Size should stabilize after 7 days (cleanup service running)

**Thresholds:**
- **Green:** < 1GB per checkpoint
- **Yellow:** 1-2GB (cleanup may be delayed)
- **Red:** > 2GB (cleanup service not working)

**Actions:**
- **Yellow:** Check checkpoint cleaner logs: `docker logs k2-checkpoint-cleaner`
- **Red:** Manual cleanup: `docker exec k2-spark-master find /checkpoints -type f -name "metadata" -mtime +7 -delete`

---

### 3. Check Data Quality (DLQ Rate)

**Frequency:** Daily
**Duration:** 2 minutes

#### Query DLQ Tables

```bash
docker exec k2-spark-master /opt/spark/bin/spark-sql \
    --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
    --conf spark.sql.catalog.iceberg.type=rest \
    --conf spark.sql.catalog.iceberg.uri=http://iceberg-rest:8181 \
    -e "
    SELECT
        bronze_source,
        COUNT(*) as dlq_count
    FROM iceberg.market_data.silver_dlq_trades
    WHERE ingestion_timestamp > CURRENT_TIMESTAMP - INTERVAL '24' HOUR
    GROUP BY bronze_source;
    "
```

**Thresholds:**
- **Green:** < 10 DLQ records/day (< 0.1% of volume)
- **Yellow:** 10-100 DLQ records/day (0.1-1%)
- **Red:** > 100 DLQ records/day (> 1%)

**Actions:**
- **Yellow:** Review DLQ records for patterns: `SELECT * FROM silver_dlq_trades LIMIT 10`
- **Red:** Investigate upstream data quality issue (Binance/Kraken API changes?)

---

## Weekly Maintenance Tasks

### 1. Run Iceberg Maintenance

**Frequency:** Weekly (Saturday morning recommended)
**Duration:** 10-30 minutes

#### Dry Run First

```bash
docker exec k2-spark-master uv run python /opt/k2/scripts/iceberg_maintenance.py
```

Review output:
- Check snapshot counts for each table
- Verify no unexpected errors
- Note estimated deletions

#### Execute Cleanup

```bash
docker exec k2-spark-master uv run python /opt/k2/scripts/iceberg_maintenance.py --execute
```

**Expected results:**
- Snapshots expired: 50-200 per table (depends on write frequency)
- Orphan files removed: 0-50 (low if jobs are healthy)

**Red flags:**
- Errors during snapshot expiration (table corruption?)
- Hundreds of orphan files (indicates job failures or crashes)

#### Verify Results

```bash
docker exec k2-spark-master /opt/spark/bin/spark-sql \
    --conf spark.sql.catalog.iceberg=org.apache.iceberg.spark.SparkCatalog \
    --conf spark.sql.catalog.iceberg.type=rest \
    --conf spark.sql.catalog.iceberg.uri=http://iceberg-rest:8181 \
    -e "SELECT COUNT(*) as snapshot_count FROM iceberg.market_data.bronze_binance_trades.snapshots;"
```

**Expected:** ~100-150 snapshots (after 7+ days of operation)

---

### 2. Check Kafka Consumer Group Lag

**Frequency:** Weekly
**Duration:** 5 minutes

```bash
docker exec k2-kafka kafka-consumer-groups \
    --bootstrap-server localhost:9092 \
    --describe \
    --group k2-bronze-binance-ingestion
```

**Expected output:**
- LAG column should be < 1000 (near real-time)
- CURRENT-OFFSET should be increasing (consuming messages)

**Red flags:**
- LAG > 10,000 (falling behind, processing too slow)
- CURRENT-OFFSET not increasing (consumer stuck or dead)
- GROUP state: Dead (consumer group abandoned)

**Actions:**
- High lag: Check Spark UI batch processing times, may need to scale up
- Stuck offset: Restart streaming job
- Dead group: This shouldn't happen with explicit consumer group IDs (investigate)

---

### 3. Review Checkpoint Cleaner Logs

**Frequency:** Weekly
**Duration:** 2 minutes

```bash
docker logs k2-checkpoint-cleaner --tail 50
```

**Expected:**
- Daily cleanup runs (every 24h)
- Files deleted from each checkpoint directory
- No errors

**Red flags:**
- No recent log entries (container stopped?)
- Permission denied errors (volume mount issue)
- Find command errors (directory corruption)

**Actions:**
- Container stopped: `docker compose restart spark-checkpoint-cleaner`
- Permission errors: Check volume permissions, may need to recreate volume

---

## Troubleshooting Common Issues

### Issue: OOM Killed Container

**Symptoms:**
- Container status shows "Restarting" or "Exited (137)"
- Logs show "java.lang.OutOfMemoryError" or "Killed"

**Diagnosis:**

```bash
docker logs k2-bronze-binance-stream --tail 100 | grep -i "out of memory\|killed"
docker stats k2-bronze-binance-stream --no-stream
```

**Resolution:**

1. **Immediate:** Restart job with checkpoint recovery
   ```bash
   docker compose restart bronze-binance-stream
   ```

2. **Short-term:** Monitor memory usage, may be transient spike

3. **Long-term:** If recurring, adjust memory allocation in `docker-compose.yml`:
   ```yaml
   deploy:
     resources:
       limits:
         memory: 2G  # Increase from 1.8G
   ```

**Prevention:**
- Ensure Spark streaming backpressure is enabled (should be in config)
- Check for state store memory leaks (Spark UI → Streaming → State Store)

---

### Issue: Streaming Query Falling Behind

**Symptoms:**
- Spark UI shows batch processing time > trigger interval
- Kafka consumer lag increasing
- End-to-end latency increasing

**Diagnosis:**

1. Check batch processing time trend:
   - Spark UI → Application → Streaming → Batch Duration chart
   - Should be relatively flat, not increasing over time

2. Check input rate vs processing rate:
   - Spark UI → Streaming → Input Rate vs Process Rate
   - Process rate should be >= input rate

3. Check executor resource usage:
   ```bash
   docker stats k2-bronze-binance-stream --no-stream
   ```

**Resolution:**

**Temporary spike (e.g., large batch after downtime):**
- Wait for query to catch up
- Monitor progress every 5 minutes

**Sustained falling behind:**

1. **Option 1:** Increase `maxOffsetsPerTrigger` (allow larger batches)
   ```python
   .option("maxOffsetsPerTrigger", 20000)  # Increase from 10000
   ```

2. **Option 2:** Increase executor cores (more parallelism)
   ```yaml
   command: >
     --total-executor-cores 2  # Increase from 1
     --executor-cores 1
   ```

3. **Option 3:** Add more workers (horizontal scaling)
   - Add `spark-worker-3` to `docker-compose.yml`
   - Requires more host resources

**Prevention:**
- Monitor input rate trends, adjust capacity before hitting limits
- Use Kafka backpressure to smooth out spikes

---

### Issue: High DLQ Rate

**Symptoms:**
- DLQ count > 100 records/day
- Alert triggered: "DLQ rate > 1%"

**Diagnosis:**

1. Sample DLQ records:
   ```sql
   SELECT error_message, COUNT(*) as count
   FROM iceberg.market_data.silver_dlq_trades
   WHERE ingestion_timestamp > CURRENT_TIMESTAMP - INTERVAL '1' HOUR
   GROUP BY error_message
   ORDER BY count DESC
   LIMIT 10;
   ```

2. Check for schema changes:
   - Review Schema Registry history: http://localhost:8081/subjects/market.crypto.trades.binance.raw-value/versions
   - Compare with Avro schema in transformation job

3. Check upstream data quality:
   - Inspect raw Kafka messages: Kafka UI → Topics → market.crypto.trades.binance.raw
   - Look for malformed messages

**Resolution:**

**Schema mismatch:**
- Update transformation job Avro schema to match new version
- Add backward-compatible deserialization logic

**Data quality issue:**
- Contact upstream team (Binance API changes?)
- Add validation rule to DLQ instead of failing

**Bug in transformation logic:**
- Review validation rules in `src/k2/spark/validation/trade_validation.py`
- Fix and redeploy

**Prevention:**
- Set up Schema Registry compatibility checks (currently: BACKWARD)
- Monitor DLQ trends in Grafana dashboard

---

### Issue: Checkpoint Corruption

**Symptoms:**
- Streaming job fails to start with "checkpoint corrupted" error
- Logs show "Unable to read checkpoint metadata"

**Diagnosis:**

```bash
docker exec k2-spark-master ls -lh /checkpoints/bronze-binance/
docker exec k2-spark-master cat /checkpoints/bronze-binance/metadata | head
```

**Resolution:**

**Option 1: Restore from backup (if available)**
```bash
docker exec k2-spark-master cp -r /checkpoints/bronze-binance.backup /checkpoints/bronze-binance
docker compose restart bronze-binance-stream
```

**Option 2: Start from latest Kafka offset (data loss risk)**
```bash
docker exec k2-spark-master rm -rf /checkpoints/bronze-binance
docker compose restart bronze-binance-stream
```
⚠️ This will replay all data from current Kafka offset. May cause duplicates in Bronze layer.

**Option 3: Start from earliest Kafka offset (full reprocessing)**
Change job code temporarily:
```python
.option("startingOffsets", "earliest")  # Instead of "latest"
```
⚠️ May take hours to reprocess historical data.

**Prevention:**
- Regular checkpoint backups (not currently automated, consider adding)
- Monitor checkpoint cleaner to ensure it's not deleting current metadata

---

## Alert Response Procedures

### Alert: High Memory Usage (> 90%)

**Severity:** P1 (Critical)
**Response Time:** 15 minutes

**Immediate Actions:**
1. Identify which container is affected
2. Check if OOM kill is imminent: `dmesg | grep -i kill`
3. Restart affected container: `docker compose restart <service>`

**Follow-up Actions:**
1. Review Spark UI for memory-intensive operations
2. Check for state store memory leaks
3. Consider increasing memory allocation if recurring

---

### Alert: Streaming Query Behind (> 5 minutes lag)

**Severity:** P2 (High)
**Response Time:** 30 minutes

**Immediate Actions:**
1. Check if temporary spike (after downtime) or sustained
2. Monitor batch processing times in Spark UI
3. Check Kafka consumer lag trend

**Follow-up Actions:**
1. If sustained, scale up resources (see "Issue: Streaming Query Falling Behind")
2. Review query performance in Spark UI
3. Consider tuning batch interval or max offsets per trigger

---

### Alert: DLQ Rate > 1%

**Severity:** P2 (High)
**Response Time:** 1 hour

**Immediate Actions:**
1. Query DLQ for common error patterns
2. Check if schema changed recently
3. Sample raw Kafka messages for anomalies

**Follow-up Actions:**
1. Fix underlying issue (schema update, validation logic, upstream fix)
2. Consider backfilling valid data if many false positives
3. Update monitoring to track specific error types

---

### Alert: Checkpoint Disk Full (> 90%)

**Severity:** P2 (High)
**Response Time:** 1 hour

**Immediate Actions:**
1. Check if checkpoint cleaner is running: `docker ps | grep cleaner`
2. Review cleaner logs for errors: `docker logs k2-checkpoint-cleaner`
3. Manual cleanup if needed: `find /checkpoints -name "metadata" -mtime +7 -delete`

**Follow-up Actions:**
1. Fix checkpoint cleaner if broken
2. Consider reducing retention policy (currently 7 days)
3. Monitor disk usage trend to ensure stability

---

## Routine Maintenance Schedule

| Task | Frequency | Duration | Owner |
|------|-----------|----------|-------|
| Check streaming job health | Daily | 5 min | On-call Engineer |
| Check resource usage | Daily | 3 min | On-call Engineer |
| Check DLQ rate | Daily | 2 min | On-call Engineer |
| Run Iceberg maintenance | Weekly | 10-30 min | Data Engineer |
| Check Kafka consumer lag | Weekly | 5 min | Platform Engineer |
| Review checkpoint cleaner logs | Weekly | 2 min | Platform Engineer |
| Review Grafana dashboards | Weekly | 10 min | Team Lead |
| Checkpoint backup (manual) | Monthly | 30 min | Data Engineer |

---

## Configuration Tuning Guide

### When to Increase Memory

**Indicators:**
- Frequent OOM kills (> 1/week)
- Memory usage consistently > 85%
- GC time > 10% of batch processing time (check Spark UI)

**How to increase:**

1. Adjust in `docker-compose.yml`:
   ```yaml
   command: >
     --executor-memory 1g  # Increase from 768m
     --driver-memory 1g    # Increase from 768m

   deploy:
     resources:
       limits:
         memory: 2200M  # Increase from 1800M
   ```

2. Increase worker memory:
   ```yaml
   environment:
     - SPARK_WORKER_MEMORY=2500m  # Increase from 2g

   deploy:
     resources:
       limits:
         memory: 3500M  # Increase from 3G
   ```

**Trade-offs:**
- More memory = higher cost
- Ensure host has capacity (don't oversubscribe)
- Larger heaps = longer GC pauses

---

### When to Increase Cores

**Indicators:**
- Processing time > trigger interval consistently
- Spark UI shows executor CPU at 100%
- Kafka consumer lag increasing

**How to increase:**

```yaml
command: >
  --total-executor-cores 2  # Increase from 1
  --executor-cores 1        # Keep at 1 (better task parallelism)
```

**Trade-offs:**
- More cores = more parallelism but more contention
- Ensure worker has capacity (don't exceed SPARK_WORKER_CORES)
- May need to increase memory proportionally

---

### When to Adjust Trigger Interval

**Indicators:**
- Batch processing time < 50% of trigger interval (underutilized)
- Batch processing time > trigger interval (falling behind)

**Current settings:**
- Bronze Binance: 10 seconds, 10,000 max offsets
- Bronze Kraken: 30 seconds, 1,000 max offsets
- Silver: 30 seconds, no max offsets

**How to adjust:**

```python
.trigger(processingTime="20 seconds")  # Increase from 10s
```

**Trade-offs:**
- Longer interval = larger batches, better throughput, higher latency
- Shorter interval = smaller batches, lower latency, more overhead
- Aim for batch processing time = 70-80% of trigger interval

---

## Useful Queries

### Check Table Snapshot History

```sql
SELECT
    snapshot_id,
    committed_at,
    operation,
    summary['added-data-files'] as files_added,
    summary['total-data-files'] as total_files
FROM iceberg.market_data.bronze_binance_trades.snapshots
ORDER BY committed_at DESC
LIMIT 10;
```

### Check Bronze→Silver Lag

```sql
SELECT
    MAX(ingestion_timestamp) as latest_bronze_ts,
    MAX(validation_timestamp) as latest_silver_ts,
    (CURRENT_TIMESTAMP - MAX(validation_timestamp)) / 1000000 as silver_lag_seconds
FROM iceberg.market_data.silver_binance_trades;
```

### Check Data Completeness

```sql
SELECT
    exchange,
    DATE(validation_timestamp) as date,
    COUNT(*) as record_count,
    MIN(price) as min_price,
    MAX(price) as max_price
FROM iceberg.market_data.silver_binance_trades
WHERE validation_timestamp > CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY exchange, DATE(validation_timestamp)
ORDER BY date DESC, exchange;
```

---

## Related Documentation

- **Decision #013:** Spark Resource Optimization (design decisions)
- **Iceberg Maintenance Script:** `/scripts/iceberg_maintenance.py`
- **Spark Configuration:** `/src/k2/spark/utils/spark_session.py`
- **Docker Compose:** `/docker-compose.yml`
- **Validation Logic:** `/src/k2/spark/validation/trade_validation.py`

---

## Escalation

### On-Call Rotation

- **Primary:** Data Platform Team (Slack: #data-platform-oncall)
- **Secondary:** Infrastructure Team (Slack: #infra-oncall)
- **Manager:** VP Engineering

### When to Escalate

- **P0:** Complete platform outage (all jobs down)
- **P1:** Multiple job failures, data loss risk
- **P2:** Single job failure, degraded performance
- **P3:** Monitoring alerts, no immediate impact

### Incident Response

1. Create incident in PagerDuty / Jira
2. Post in #incidents Slack channel
3. Follow incident response runbook
4. Conduct post-incident review within 48h

---

**Runbook Version:** 1.0
**Last Reviewed:** 2026-01-20
**Next Review:** 2026-04-20 (quarterly)
