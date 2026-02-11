# Runbook: Prefect OHLCV Pipeline Operations

**Severity**: High (Production Data Pipeline)
**Last Updated**: 2026-01-21
**Maintained By**: Data Engineering Team
**Related Phase**: Phase 13 - OHLCV Analytics

---

## Overview

This runbook covers operational procedures for the Prefect-orchestrated OHLCV (Open-High-Low-Close-Volume) analytics pipeline. The pipeline generates 5 timeframes of aggregated candle data (1m, 5m, 30m, 1h, 1d) from raw trades in `gold_crypto_trades`.

**Pipeline Components**:
- **Prefect Server**: Orchestration UI and API (http://localhost:4200)
- **Prefect Flows**: 5 scheduled flows (ohlcv_pipeline.py)
- **Spark Jobs**: 2 job types (incremental MERGE, batch INSERT OVERWRITE)
- **Iceberg Tables**: 5 OHLCV tables (`gold_ohlcv_1m` through `gold_ohlcv_1d`)

---

## Architecture Quick Reference

### Flow Schedule (Staggered Execution)

| Flow | Schedule | Cron | Job Type | Duration Target |
|------|----------|------|----------|-----------------|
| OHLCV-1m-Pipeline | Every 5 min | `*/5 * * * *` | Incremental MERGE | <20s |
| OHLCV-5m-Pipeline | Every 15 min (+5min offset) | `5,20,35,50 * * * *` | Incremental MERGE | <30s |
| OHLCV-30m-Pipeline | Every 30 min (+10min offset) | `10,40 * * * *` | Batch INSERT | <45s |
| OHLCV-1h-Pipeline | Every hour (+15min offset) | `15 * * * *` | Batch INSERT | <60s |
| OHLCV-1d-Pipeline | Daily 00:05 UTC | `5 0 * * *` | Batch INSERT | <120s |

### Spark Configuration (Consistent Across All Jobs)

```bash
--master spark://spark-master:7077
--total-executor-cores 1
--executor-cores 1
--executor-memory 1024m
--driver-memory 512m
--conf spark.driver.extraJavaOptions=-Daws.region=us-east-1
--conf spark.executor.extraJavaOptions=-Daws.region=us-east-1
--jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,
      /opt/spark/jars-extra/iceberg-aws-1.4.0.jar,
      /opt/spark/jars-extra/bundle-2.20.18.jar,
      /opt/spark/jars-extra/url-connection-client-2.20.18.jar,
      /opt/spark/jars-extra/hadoop-aws-3.3.4.jar,
      /opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar
```

**Critical**: This configuration must match exactly across Bronze, Silver, and Gold workflows.

---

## Common Issues & Resolutions

### Issue 1: Flows Not Running (No Scheduled Executions)

**Symptoms**:
- Prefect UI shows flows but no recent runs
- Expected scheduled runs missing
- Flow status: "Scheduled" but never transitions to "Running"

**Diagnosis**:
```bash
# Check if serve() process is running
ps aux | grep ohlcv_pipeline

# Check serve() logs
tail -50 /tmp/ohlcv-pipeline.log

# Verify Prefect server is reachable
curl http://localhost:4200/api/health
```

**Resolution**:

**Step 1**: Check serve() process
```bash
# If process not found, start it
cd /home/rjdscott/Documents/projects/k2-market-data-platform
PREFECT_API_URL=http://localhost:4200/api \
  nohup uv run python src/k2/orchestration/flows/ohlcv_pipeline.py \
  >> /tmp/ohlcv-pipeline.log 2>&1 &

# Verify it started
ps aux | grep ohlcv_pipeline
```

**Step 2**: Verify deployments registered
```bash
# Check Prefect UI
open http://localhost:4200/deployments

# Should see 5 deployments:
# - ohlcv-1m-scheduled
# - ohlcv-5m-scheduled
# - ohlcv-30m-scheduled
# - ohlcv-1h-scheduled
# - ohlcv-1d-scheduled
```

**Prevention**:
- Monitor serve() process with systemd or supervisord
- Set up alerting for process termination
- Log rotation for /tmp/ohlcv-pipeline.log

---

### Issue 2: Flow Execution Failures (Spark Job Errors)

**Symptoms**:
- Flow status: "Failed" in Prefect UI
- Error message includes "spark-submit" or Spark-related errors
- Flow retried 2× then failed

**Diagnosis**:
```bash
# View flow run logs in Prefect UI
open http://localhost:4200/runs

# Check Spark master logs
docker logs k2-spark-master --tail 100

# Check Spark worker logs
docker logs k2-spark-worker-1 --tail 100

# Verify Spark cluster health
open http://localhost:8090
```

**Common Causes & Fixes**:

#### A) JAR Missing or Incorrect
**Error**: `java.lang.ClassNotFoundException: org.apache.iceberg.spark.SparkCatalog`

**Fix**:
```bash
# Verify JARs exist in container
docker exec k2-spark-master ls -lh /opt/spark/jars-extra/

# Should see 6 JARs:
# - iceberg-spark-runtime-3.5_2.12-1.4.0.jar
# - iceberg-aws-1.4.0.jar
# - bundle-2.20.18.jar
# - url-connection-client-2.20.18.jar
# - hadoop-aws-3.3.4.jar
# - aws-java-sdk-bundle-1.12.262.jar

# If missing, restart Spark cluster
docker-compose restart spark-master spark-worker-1 spark-worker-2
```

#### B) S3FileIO Initialization Error
**Error**: `IllegalArgumentException: Cannot initialize FileIO, missing no-arg constructor: org.apache.iceberg.aws.s3.S3FileIO`

**Fix**: Ensure all 6 JARs are included (especially `bundle-2.20.18.jar` and `url-connection-client-2.20.18.jar`)

#### C) Out of Memory (OOM)
**Error**: `java.lang.OutOfMemoryError: Java heap space`

**Fix**:
```bash
# Check current resource usage
docker stats k2-spark-master k2-spark-worker-1 k2-spark-worker-2

# If consistently high, increase executor memory in flow
# Edit: src/k2/orchestration/flows/ohlcv_pipeline.py
# Change: SPARK_MEMORY = "1024m" → "2048m"

# Restart serve() process
pkill -9 -f "ohlcv_pipeline.py"
PREFECT_API_URL=http://localhost:4200/api \
  nohup uv run python src/k2/orchestration/flows/ohlcv_pipeline.py \
  >> /tmp/ohlcv-pipeline.log 2>&1 &
```

---

### Issue 3: Prefect UI Not Accessible

**Symptoms**:
- Browser error: "Can't connect to Server API at http://prefect-server:4200/api"
- Prefect UI loads but shows API connection error
- http://localhost:4200 times out

**Diagnosis**:
```bash
# Check Prefect server container
docker ps | grep prefect

# Check container logs
docker logs k2-prefect-server --tail 50

# Check health endpoint
curl http://localhost:4200/api/health
```

**Resolution**:

**Step 1**: Verify PREFECT_API_URL configuration
```bash
# Check docker-compose.v1.yml
grep -A 5 "prefect-server:" docker-compose.v1.yml

# Should show:
# environment:
#   PREFECT_API_URL: http://localhost:4200/api  # NOT http://prefect-server:4200/api
```

**Step 2**: Restart Prefect server
```bash
docker-compose restart prefect-server

# Wait 10 seconds
sleep 10

# Verify health
curl http://localhost:4200/api/health
```

**Step 3**: Clear browser cache
```
# Hard refresh browser
# Chrome/Firefox: Ctrl+Shift+R (Linux/Windows) or Cmd+Shift+R (Mac)
```

**Prevention**:
- Always use `http://localhost:4200/api` for PREFECT_API_URL (not internal Docker hostname)
- Add health check to docker-compose.yml

---

### Issue 4: Data Quality Issues (Invalid OHLCV Data)

**Symptoms**:
- OHLCV candles have invalid price relationships (e.g., `low_price > high_price`)
- VWAP outside bounds (`vwap < low_price` or `vwap > high_price`)
- Negative volumes or trade counts
- Missing candles for expected time windows

**Diagnosis**:

**Step 1**: Run data quality validation
```bash
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 1024m \
  --driver-memory 512m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/spark/validation/ohlcv_validation.py \
  --timeframe all \
  --lookback-days 7"
```

**Step 2**: Inspect source trades
```sql
-- Check for corrupted trade data
SELECT * FROM iceberg.market_data.gold_crypto_trades
WHERE price <= 0 OR quantity <= 0
LIMIT 10;

-- Check for duplicate trades
SELECT timestamp, symbol, exchange, COUNT(*) as cnt
FROM iceberg.market_data.gold_crypto_trades
WHERE timestamp >= unix_timestamp(current_timestamp() - INTERVAL 1 HOUR) * 1000000
GROUP BY timestamp, symbol, exchange
HAVING cnt > 1
LIMIT 10;
```

**Resolution**:

**If source data corrupt**: Fix Bronze/Silver pipeline first, then reprocess OHLCV

**If OHLCV logic issue**:
```bash
# Review aggregation logic
cat src/k2/spark/jobs/batch/ohlcv_incremental.py

# Test with small window manually
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 1024m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/spark/jobs/batch/ohlcv_incremental.py \
  --timeframe 1m \
  --lookback-minutes 10"
```

---

### Issue 5: Flow Execution Taking Too Long

**Symptoms**:
- Flow runs exceed target duration (e.g., 1m job > 20s)
- Flow times out after 300s (5 minutes)
- Spark UI shows job hung or slow

**Diagnosis**:
```bash
# Check Spark UI for active jobs
open http://localhost:8090

# Check resource availability
docker stats

# Review flow duration in Prefect UI
open http://localhost:4200/runs
# Sort by duration, identify slowest runs
```

**Resolution**:

**Step 1**: Check data volume
```sql
-- Check recent trade volume
SELECT
    COUNT(*) as trade_count,
    COUNT(DISTINCT symbol) as symbol_count,
    COUNT(DISTINCT exchange) as exchange_count
FROM iceberg.market_data.gold_crypto_trades
WHERE timestamp >= unix_timestamp(current_timestamp() - INTERVAL 1 HOUR) * 1000000;
```

**Step 2**: Optimize lookback window
```python
# If 1m job slow, reduce lookback
# Edit: src/k2/orchestration/flows/ohlcv_pipeline.py
# Change: "--lookback-minutes", "10" → "--lookback-minutes", "8"
```

**Step 3**: Increase Spark resources
```python
# Edit: src/k2/orchestration/flows/ohlcv_pipeline.py
# Change:
# SPARK_CORES = 1 → SPARK_CORES = 2
# SPARK_MEMORY = "1024m" → SPARK_MEMORY = "2048m"
```

**Step 4**: Check for resource contention
```bash
# Ensure flows running sequentially (staggered schedules)
# View Prefect UI timeline - should NOT see overlapping executions
```

---

## Monitoring & Health Checks

### Key Metrics to Monitor

**1. Flow Success Rate**
- **Target**: >99% success rate
- **Alert**: <95% success rate over 24 hours
- **Check**: Prefect UI → Runs → Filter by status

**2. Flow Duration**
- **Targets**: 1m <20s, 5m <30s, 30m <45s, 1h <60s, 1d <120s
- **Alert**: Any flow consistently exceeds target by 50%
- **Check**: Prefect UI → Runs → Sort by duration

**3. OHLCV Data Freshness**
- **Target**: Latest 1m candle < 5 minutes old
- **Alert**: Latest 1m candle > 10 minutes old
- **Query**:
```sql
SELECT
    timeframe,
    MAX(window_start) as latest_candle,
    TIMESTAMPDIFF(MINUTE, MAX(window_start), CURRENT_TIMESTAMP()) as minutes_old
FROM (
    SELECT '1m' as timeframe, window_start FROM iceberg.market_data.gold_ohlcv_1m
    UNION ALL
    SELECT '5m', window_start FROM iceberg.market_data.gold_ohlcv_5m
) t
GROUP BY timeframe;
```

**4. Data Quality Invariants**
- **Target**: 100% pass rate
- **Alert**: Any invariant violation
- **Check**: Run validation script (see Issue 4)

**5. Spark Cluster Health**
- **Target**: All workers alive, <70% CPU, <80% memory
- **Alert**: Worker offline or resources >90%
- **Check**: http://localhost:8090

### Daily Health Check Procedure

```bash
#!/bin/bash
# daily-ohlcv-health-check.sh

echo "=== OHLCV Pipeline Health Check ==="
echo "Date: $(date)"

# 1. Check serve() process
if ps aux | grep -q "[o]hlcv_pipeline.py"; then
    echo "✓ serve() process running"
else
    echo "✗ serve() process NOT running"
fi

# 2. Check Prefect server
if curl -s http://localhost:4200/api/health | grep -q "ok"; then
    echo "✓ Prefect server healthy"
else
    echo "✗ Prefect server unhealthy"
fi

# 3. Check Spark cluster
if curl -s http://localhost:8090 | grep -q "Spark Master"; then
    echo "✓ Spark master accessible"
else
    echo "✗ Spark master inaccessible"
fi

# 4. Check recent flow runs (last 1 hour)
echo "Recent flow runs:"
# Use Prefect API to get recent runs
curl -s http://localhost:4200/api/flow_runs?limit=10 | jq -r '.[] | "\(.name): \(.state)"'

# 5. Check OHLCV data freshness
echo "OHLCV data freshness:"
# Run Spark query to check latest candles
```

---

## Manual Operations

### Starting/Stopping Flows

**View Flow Status**:
```bash
# Check serve() process
ps aux | grep ohlcv_pipeline

# View recent logs
tail -f /tmp/ohlcv-pipeline.log
```

**Stop Flows**:
```bash
# Stop serve() process
pkill -9 -f "ohlcv_pipeline.py"

# Verify stopped
ps aux | grep ohlcv_pipeline
```

**Start Flows**:
```bash
cd /home/rjdscott/Documents/projects/k2-market-data-platform
PREFECT_API_URL=http://localhost:4200/api \
  nohup uv run python src/k2/orchestration/flows/ohlcv_pipeline.py \
  >> /tmp/ohlcv-pipeline.log 2>&1 &

# Verify started
ps aux | grep ohlcv_pipeline

# Check logs
tail -20 /tmp/ohlcv-pipeline.log
```

### Manual Flow Trigger

**Trigger Specific Flow**:
```bash
# Trigger 1m flow manually
PREFECT_API_URL=http://localhost:4200/api \
  uv run prefect deployment run "OHLCV-1m-Pipeline/ohlcv-1m-scheduled"

# Trigger 5m flow
PREFECT_API_URL=http://localhost:4200/api \
  uv run prefect deployment run "OHLCV-5m-Pipeline/ohlcv-5m-scheduled"

# View run status
open http://localhost:4200/runs
```

### Backfilling Historical Data

**Scenario**: Need to regenerate OHLCV data for specific time range

**Step 1**: Run incremental job manually with extended lookback
```bash
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 1024m \
  --driver-memory 512m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/spark/jobs/batch/ohlcv_incremental.py \
  --timeframe 1m \
  --lookback-minutes 1440"  # 24 hours
```

**Step 2**: For batch timeframes (30m/1h/1d), run batch job
```bash
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 1024m \
  --driver-memory 512m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/spark/jobs/batch/ohlcv_batch.py \
  --timeframe 1h \
  --lookback-hours 48"  # 48 hours
```

### Retention Enforcement

**Manual Cleanup** (delete old partitions):
```bash
# Run retention script (dry-run first)
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 512m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/orchestration/flows/ohlcv_retention.py \
  --dry-run"

# If looks good, run for real
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 512m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/orchestration/flows/ohlcv_retention.py"
```

**Retention Policies**:
- 1m: 90 days (daily cleanup)
- 5m: 180 days (daily cleanup)
- 30m: 1 year (weekly cleanup)
- 1h: 3 years (monthly cleanup)
- 1d: 5 years (quarterly cleanup)

---

## Recovery Procedures

### Full Pipeline Recovery (Complete Outage)

**Scenario**: Complete pipeline failure, need to restart everything

**Step 1**: Restart infrastructure
```bash
# Restart Spark cluster
docker-compose restart spark-master spark-worker-1 spark-worker-2

# Restart Prefect server
docker-compose restart prefect-server

# Wait 30 seconds
sleep 30
```

**Step 2**: Verify Spark cluster
```bash
# Check Spark UI
open http://localhost:8090

# Should see 2 workers alive
```

**Step 3**: Restart Prefect flows
```bash
# Stop any existing serve() process
pkill -9 -f "ohlcv_pipeline.py"

# Start new serve() process
cd /home/rjdscott/Documents/projects/k2-market-data-platform
PREFECT_API_URL=http://localhost:4200/api \
  nohup uv run python src/k2/orchestration/flows/ohlcv_pipeline.py \
  >> /tmp/ohlcv-pipeline.log 2>&1 &

# Verify
ps aux | grep ohlcv_pipeline
```

**Step 4**: Verify flow registration
```bash
# Check Prefect UI
open http://localhost:4200/deployments

# Should see 5 deployments
```

**Step 5**: Monitor first flow run
```bash
# Wait for next scheduled run
# Check Prefect UI for execution
open http://localhost:4200/runs

# Check logs
tail -f /tmp/ohlcv-pipeline.log
```

### Recovering from Corrupted OHLCV Data

**Scenario**: OHLCV data corrupt for specific time range, need to reprocess

**Step 1**: Identify corrupt time range
```sql
-- Find gaps or anomalies
SELECT window_date, COUNT(*) as candle_count
FROM iceberg.market_data.gold_ohlcv_1m
WHERE window_date >= current_date() - INTERVAL 7 DAYS
GROUP BY window_date
ORDER BY window_date;
```

**Step 2**: Delete corrupt partitions
```sql
-- Delete specific partition (Iceberg)
DELETE FROM iceberg.market_data.gold_ohlcv_1m
WHERE window_date = '2026-01-20';
```

**Step 3**: Reprocess from source trades
```bash
# Run manual backfill (see "Backfilling Historical Data" section)
# Use extended lookback to cover deleted time range
```

**Step 4**: Validate recovery
```bash
# Run validation script
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 1024m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/spark/validation/ohlcv_validation.py \
  --timeframe 1m \
  --lookback-days 1"
```

---

## Performance Tuning

### Optimizing Flow Duration

**Current Targets**:
- 1m: <20s (current: 6-15s) ✓
- 5m: <30s (current: 10-20s) ✓
- 30m: <45s
- 1h: <60s
- 1d: <120s

**Tuning Options**:

**1. Reduce Lookback Window**
```python
# src/k2/orchestration/flows/ohlcv_pipeline.py
# For 1m flow:
# Current: "--lookback-minutes", "10"
# Optimized: "--lookback-minutes", "7"
```

**2. Increase Spark Resources**
```python
# src/k2/orchestration/flows/ohlcv_pipeline.py
# Current: SPARK_CORES = 1, SPARK_MEMORY = "1024m"
# Optimized: SPARK_CORES = 2, SPARK_MEMORY = "2048m"
```

**3. Optimize Spark Configuration**
```python
# Add to spark-submit command:
--conf spark.sql.adaptive.enabled=true \
--conf spark.sql.adaptive.coalescePartitions.enabled=true \
--conf spark.sql.shuffle.partitions=10
```

**4. Partition Pruning Optimization**
```sql
-- Ensure queries filter on partition key (window_date)
-- Good:
SELECT * FROM gold_ohlcv_1m
WHERE window_date >= current_date() - INTERVAL 1 DAY
  AND window_start >= current_timestamp() - INTERVAL 1 HOUR;

-- Bad (no partition pruning):
SELECT * FROM gold_ohlcv_1m
WHERE window_start >= current_timestamp() - INTERVAL 1 HOUR;
```

### Resource Allocation

**Current Allocation**:
- **Spark Cluster**: 7 cores, 12GB RAM
- **Current Usage**: 5 cores (Bronze: 2, Silver: 2, Gold streaming: 1)
- **Available**: 2 cores for OHLCV batch jobs
- **OHLCV Usage**: 1 core (sequential execution)

**If Need More Resources**:
1. Scale down streaming jobs temporarily during OHLCV backfill
2. Add Spark workers (increase cluster size)
3. Run OHLCV jobs on separate cluster

---

## Verification Queries

### Check OHLCV Data Exists

```sql
-- Latest 1m candles
SELECT
    symbol, exchange, window_start,
    open_price, high_price, low_price, close_price,
    volume, trade_count, ROUND(vwap, 8) as vwap
FROM iceberg.market_data.gold_ohlcv_1m
WHERE window_date >= current_date()
ORDER BY window_start DESC
LIMIT 10;
```

### Validate Candle Count

```sql
-- Compare OHLCV trade count with source trades
SELECT
    o.window_start,
    o.trade_count as ohlcv_count,
    COUNT(t.*) as source_count,
    ABS(o.trade_count - COUNT(t.*)) as diff
FROM iceberg.market_data.gold_ohlcv_1m o
JOIN iceberg.market_data.gold_crypto_trades t
    ON t.symbol = o.symbol
    AND t.exchange = o.exchange
    AND t.timestamp >= unix_timestamp(o.window_start) * 1000000
    AND t.timestamp < unix_timestamp(o.window_end) * 1000000
WHERE o.window_date = current_date()
GROUP BY o.window_start, o.trade_count
HAVING diff > 0
ORDER BY diff DESC
LIMIT 10;
```

### Check for Missing Candles

```sql
-- Expected candles vs actual (1m timeframe)
WITH expected AS (
    SELECT generate_series(
        date_trunc('day', current_timestamp()),
        current_timestamp(),
        INTERVAL '1 minute'
    ) as expected_window
)
SELECT
    e.expected_window,
    CASE WHEN o.window_start IS NULL THEN 'MISSING' ELSE 'EXISTS' END as status
FROM expected e
LEFT JOIN iceberg.market_data.gold_ohlcv_1m o
    ON e.expected_window = o.window_start
WHERE o.window_start IS NULL
ORDER BY e.expected_window DESC
LIMIT 20;
```

---

## Related Documentation

- **Phase 13 Overview**: `docs/phases/phase-13-ohlcv-analytics/README.md`
- **Deployment Summary**: `docs/phases/phase-13-ohlcv-analytics/DEPLOYMENT-SUMMARY.md`
- **Progress Tracking**: `docs/phases/phase-13-ohlcv-analytics/PROGRESS.md`
- **Architectural Decisions**: `docs/phases/phase-13-ohlcv-analytics/DECISIONS.md`
- **Spark Resource Monitoring**: `docs/operations/runbooks/spark-resource-monitoring.md`
- **Streaming Pipeline Operations**: `docs/operations/runbooks/streaming-pipeline-operations.md`

---

## Quick Command Reference

```bash
# === Prefect Operations ===
# Check serve() process
ps aux | grep ohlcv_pipeline

# View logs
tail -f /tmp/ohlcv-pipeline.log

# Stop flows
pkill -9 -f "ohlcv_pipeline.py"

# Start flows
cd /home/rjdscott/Documents/projects/k2-market-data-platform
PREFECT_API_URL=http://localhost:4200/api \
  nohup uv run python src/k2/orchestration/flows/ohlcv_pipeline.py \
  >> /tmp/ohlcv-pipeline.log 2>&1 &

# === Spark Operations ===
# Manual 1m job
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 1024m \
  --driver-memory 512m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/spark/jobs/batch/ohlcv_incremental.py \
  --timeframe 1m \
  --lookback-minutes 10"

# Check Spark cluster health
docker logs k2-spark-master --tail 50
open http://localhost:8090

# === Validation ===
# Run data quality checks
docker exec k2-spark-master bash -c "cd /opt/k2 && \
  /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 1 \
  --executor-memory 1024m \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar \
  src/k2/spark/validation/ohlcv_validation.py \
  --timeframe all \
  --lookback-days 7"

# === Monitoring ===
# Prefect UI
open http://localhost:4200

# Spark UI
open http://localhost:8090

# Container stats
docker stats k2-prefect-server k2-spark-master k2-spark-worker-1
```

---

## Escalation

**Tier 1** (Self-Service): Use this runbook for common issues

**Tier 2** (Data Engineering On-Call):
- Flow failures >3 consecutive runs
- Data quality failures
- Performance degradation >50%
- Contact: Data Engineering Team

**Tier 3** (Platform Engineering):
- Spark cluster failures
- Infrastructure issues
- Docker/networking problems
- Contact: Platform Engineering Team

---

**Last Updated**: 2026-01-21
**Version**: 1.0
**Maintained By**: Data Engineering Team
