# Runbook: Bronze and Silver Streaming Job Troubleshooting

**Severity**: High
**Last Updated**: 2026-01-19
**Maintained By**: Data Engineering Team
**Related**: [SILVER_FIX_SUMMARY.md](../../phases/v1/phase-10-streaming-crypto/SILVER_FIX_SUMMARY.md), [RESOURCE_ALLOCATION_FIX.md](../../phases/v1/phase-10-streaming-crypto/RESOURCE_ALLOCATION_FIX.md)

---

## Overview

This runbook covers common issues with Bronze ingestion and Silver transformation streaming jobs in the K2 Market Data Platform. These jobs form the core of the Medallion architecture (Kafka → Bronze → Silver → Gold).

**Jobs Covered**:
- Bronze: `bronze-binance-stream`, `bronze-kraken-stream`
- Silver: `silver-binance-transformation`, `silver-kraken-transformation`

---

## Quick Diagnosis Commands

```bash
# Check all streaming job statuses
docker ps --filter "name=k2-bronze" --filter "name=k2-silver" --format "table {{.Names}}\t{{.Status}}"

# Check Spark cluster resource allocation
docker exec k2-spark-master curl -s http://localhost:8080/json/ | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'Cores: {d[\"coresused\"]}/{d[\"cores\"]}'); \
  [print(f'{a[\"name\"]}: {a[\"cores\"]} cores - {a[\"state\"]}') \
   for a in d.get('activeapps',[])]"

# Check job logs (last 100 lines)
docker logs --tail 100 k2-bronze-binance-stream
docker logs --tail 100 k2-silver-binance-transformation

# Check resource usage
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

---

## Issue 1: Silver Jobs Stuck in WAITING State (0 Cores Allocated)

### Symptoms
```
Silver-Binance: 0 cores - WAITING
Silver-Kraken:  0 cores - WAITING
```

Logs show: `Initial job has not accepted any resources; check your cluster UI`

### Diagnosis

**Step 1**: Check Spark cluster resource allocation
```bash
docker exec k2-spark-master curl -s http://localhost:8080/json/ | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'Cores: {d[\"coresused\"]}/{d[\"cores\"]}'); \
  [print(f'{a[\"name\"]}: {a[\"cores\"]} cores - {a[\"state\"]}') \
   for a in d.get('activeapps',[])]"
```

Expected: 4/6 cores used (1 core per job × 4 jobs)
Actual Problem: 4-8/6 cores used (jobs requesting more cores than available)

**Step 2**: Check container configuration vs docker-compose.yml
```bash
# Check actual executor cores in running container
docker inspect k2-silver-binance-transformation | grep -o -- "--total-executor-cores [0-9]\+"

# Check docker-compose.yml
grep -A2 "silver-binance-transformation:" docker-compose.yml | grep "executor-cores"
```

If they don't match → Container configuration drift!

### Root Cause

**Container configuration drift**: Running containers have stale parameters from previous docker-compose.yml version.

**Why**: `docker restart` does NOT pick up docker-compose.yml changes - it restarts with the SAME config the container was created with.

### Resolution

**Step 1**: Recreate containers with updated config
```bash
# WRONG (doesn't pick up docker-compose.yml changes)
docker restart k2-silver-binance-transformation

# CORRECT (recreates with new docker-compose.yml)
docker compose up -d bronze-binance-stream bronze-kraken-stream \
                   silver-binance-transformation silver-kraken-transformation
```

**Step 2**: Verify resource allocation
```bash
# Should show all 4 jobs RUNNING with 1 core each
docker exec k2-spark-master curl -s http://localhost:8080/json/
```

Expected output:
```
Cores: 4/6
K2-Bronze-Binance-Ingestion: 1 core - RUNNING
K2-Bronze-Kraken-Ingestion: 1 core - RUNNING
K2-Silver-Binance-Transformation-V3: 1 core - RUNNING
K2-Silver-Kraken-Transformation-V3: 1 core - RUNNING
```

### Prevention

**Always use `docker compose up -d` after modifying docker-compose.yml** - never use `docker restart`.

| Command | Behavior | When to Use |
|---------|----------|-------------|
| `docker restart <container>` | Restarts with SAME config | Process crashes, need quick restart (NO config changes) |
| `docker compose up -d <service>` | Recreates with NEW config from docker-compose.yml | Config changes, parameter updates |
| `docker compose restart <service>` | Same as `docker restart` | Quick restart (NO config changes) |

---

## Issue 2: Kafka OOM Killed (Exit Code 137)

### Symptoms

```bash
docker ps -a | grep k2-kafka
# Shows: Exited (137) 5 minutes ago
```

Bronze streams fail with: `Failed to create new KafkaAdminClient: No resolvable bootstrap urls`

### Diagnosis

**Step 1**: Check Kafka container status
```bash
docker ps -a --filter "name=k2-kafka" --format "table {{.Names}}\t{{.Status}}"
```

Exit code 137 = OOM killed by kernel

**Step 2**: Check resource usage
```bash
docker stats k2-spark-worker-1 k2-spark-worker-2 --no-stream
```

If Spark workers using > 75% of their memory limits → Resource starvation

### Root Cause

Over-provisioned Spark resources leaving insufficient memory for Kafka and other services.

### Resolution

**Step 1**: Reduce Spark worker resources (if over-provisioned)
```yaml
# Edit docker-compose.yml
spark-worker-1:
  environment:
    - SPARK_WORKER_CORES=3  # Reduced from 4
    - SPARK_WORKER_MEMORY=3g  # Reduced from 6g
  deploy:
    resources:
      limits:
        cpus: '3.0'  # Reduced from 4.0
        memory: 4G  # Reduced from 8G
```

**Step 2**: Reduce executor resources per streaming job
```yaml
# Edit docker-compose.yml (all 4 streaming jobs)
--total-executor-cores 1  # Reduced from 2
--executor-cores 1  # Reduced from 2
--executor-memory 1g  # Added explicit limit
```

**Step 3**: Recreate services
```bash
# Stop all streaming jobs first
docker compose down bronze-binance-stream bronze-kraken-stream \
                     silver-binance-transformation silver-kraken-transformation

# Recreate Spark workers and streaming jobs
docker compose up -d spark-worker-1 spark-worker-2
docker compose up -d bronze-binance-stream bronze-kraken-stream \
                     silver-binance-transformation silver-kraken-transformation
```

**Step 4**: Verify Kafka stable
```bash
docker stats k2-kafka --no-stream
# Should show memory usage well below limit (e.g., 553MB / 3GB)
```

### Prevention

**Right-size resources for streaming workloads**:
- Monitor actual CPU/memory usage in steady state
- Streaming jobs typically use < 1% CPU, 500MB-1GB memory
- Leave 30%+ resource headroom for burst processing and other services

---

## Issue 3: Silent Data Loss (Records Disappearing)

### Symptoms

- Bronze shows input records (e.g., 342 from Kraken)
- Silver shows 0 output records
- DLQ also shows 0 records
- No errors in logs

### Diagnosis

**Step 1**: Check record counts
```bash
# Bronze input count
docker logs k2-bronze-kraken-stream | grep "numInputRows" | tail -5

# Silver output count
docker logs k2-silver-kraken-transformation | grep "numOutputRows" | tail -5

# DLQ count
docker exec k2-spark-master spark-sql -e \
  "SELECT COUNT(*) FROM iceberg.market_data.silver_dlq_trades WHERE bronze_source='bronze_kraken_trades';"
```

If Bronze input > 0 but Silver output = 0 AND DLQ count = 0 → Silent data loss!

**Step 2**: Check validation logic for NULL handling
```bash
# Review validation code
grep -A5 "filter.*is_valid" src/k2/spark/validation/trade_validation.py
```

**Buggy pattern** (silent data loss):
```python
valid_df = df.filter(col("is_valid"))  # Excludes NULL!
invalid_df = df.filter(~col("is_valid"))  # Also excludes NULL!
```

**Correct pattern** (no data loss):
```python
valid_df = df.filter(col("is_valid") == True)
invalid_df = df.filter((col("is_valid") == False) | col("is_valid").isNull())
```

### Root Cause

**NULL validation handling bug**: Spark SQL three-valued logic (TRUE, FALSE, NULL) causes naive boolean filters to silently drop NULL records from BOTH streams.

When deserialization fails or fields are NULL:
- `col("is_valid")` becomes NULL
- `filter(col("is_valid"))` excludes NULL (only keeps TRUE)
- `filter(~col("is_valid"))` also excludes NULL (only keeps FALSE)
- Result: NULL records disappear entirely (not in Silver, not in DLQ)

### Resolution

**Step 1**: Fix validation logic
```python
# Edit src/k2/spark/validation/trade_validation.py

# BEFORE (BUGGY)
valid_df = df_with_validation.filter(col("is_valid")).drop("is_valid")
invalid_df = df_with_validation.filter(~col("is_valid"))

# AFTER (FIXED)
# IMPORTANT: Handle NULL is_valid explicitly
valid_df = df_with_validation.filter(col("is_valid") == True).drop("is_valid")

# Invalid: explicitly FALSE or NULL (capture silent failures)
invalid_df = df_with_validation.filter((col("is_valid") == False) | col("is_valid").isNull())

# Add NULL error reason
invalid_df = invalid_df.withColumn(
    "error_reason",
    when(col("is_valid").isNull(), "deserialization_failed_or_null_fields")
    .when(col("price").isNull(), "price_is_null")
    # ... other validation checks
)
```

**Step 2**: Restart Silver jobs
```bash
docker compose restart silver-binance-transformation silver-kraken-transformation
```

**Step 3**: Verify NULL records route to DLQ
```bash
# Should see records with error_reason = "deserialization_failed_or_null_fields"
docker exec k2-spark-master spark-sql -e \
  "SELECT error_reason, COUNT(*) FROM iceberg.market_data.silver_dlq_trades \
   WHERE bronze_source='bronze_kraken_trades' GROUP BY error_reason;"
```

### Prevention

**Always explicitly handle NULL in Spark validation logic**:
- Use `col("is_valid") == True` (NOT `col("is_valid")`)
- Use `(col("is_valid") == False) | col("is_valid").isNull()` for invalid stream
- Add NULL-specific error reasons for debugging

**Reference**: [Spark SQL NULL Semantics](https://spark.apache.org/docs/latest/sql-ref-null-semantics.html)

---

## Issue 4: Bronze Jobs Failing to Connect to Kafka

### Symptoms

Logs show: `org.apache.kafka.common.KafkaException: Failed to create new KafkaAdminClient`

Bronze jobs in crash loop: Restarting every few seconds

### Diagnosis

**Step 1**: Check Kafka container status
```bash
docker ps --filter "name=k2-kafka" --format "table {{.Names}}\t{{.Status}}"
```

If Exited (137) → Kafka OOM killed (see Issue 2 above)

**Step 2**: Check Kafka connectivity from Bronze job
```bash
docker exec k2-bronze-binance-stream nc -zv kafka 29092
```

If connection refused → Kafka not running or network issue

### Resolution

**If Kafka OOM killed**: See Issue 2 above

**If network issue**: Check docker-compose.yml network configuration
```bash
# All services should be on same network
docker network inspect k2-market-data-platform_default
```

**Restart Kafka and Bronze jobs**:
```bash
docker compose up -d kafka
sleep 10  # Wait for Kafka to initialize
docker compose up -d bronze-binance-stream bronze-kraken-stream
```

---

## Common Health Checks

### Check 1: All Jobs Running

```bash
# Should show 4 jobs: 2 Bronze + 2 Silver, all healthy
docker ps --filter "name=k2-bronze" --filter "name=k2-silver" --format "table {{.Names}}\t{{.Status}}"
```

Expected:
```
NAMES                                 STATUS
k2-bronze-binance-stream              Up 2 hours (healthy)
k2-bronze-kraken-stream               Up 2 hours (healthy)
k2-silver-binance-transformation      Up 2 hours
k2-silver-kraken-transformation       Up 2 hours
```

### Check 2: Spark Cluster Resource Allocation

```bash
docker exec k2-spark-master curl -s http://localhost:8080/json/ | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  print(f'Cores: {d[\"coresused\"]}/{d[\"cores\"]}'); \
  [print(f'{a[\"name\"]}: {a[\"cores\"]} cores - {a[\"state\"]}') \
   for a in d.get('activeapps',[])]"
```

Expected:
```
Cores: 4/6
K2-Bronze-Binance-Ingestion: 1 core - RUNNING
K2-Bronze-Kraken-Ingestion: 1 core - RUNNING
K2-Silver-Binance-Transformation-V3: 1 core - RUNNING
K2-Silver-Kraken-Transformation-V3: 1 core - RUNNING
```

### Check 3: Data Flowing (Bronze → Silver)

```bash
# Binance
docker logs k2-bronze-binance-stream | grep "Committing.*bronze_binance_trades" | tail -2
docker logs k2-silver-binance-transformation | grep "Committing.*silver_binance_trades" | tail -2

# Kraken
docker logs k2-bronze-kraken-stream | grep "Committing.*bronze_kraken_trades" | tail -2
docker logs k2-silver-kraken-transformation | grep "Committing.*silver_kraken_trades" | tail -2
```

Expected: Should see commits every 10-30 seconds (depending on trigger interval)

### Check 4: DLQ Rate

```bash
# Check DLQ count (should be low, < 0.1% of Silver records)
docker exec k2-spark-master spark-sql -e \
  "SELECT bronze_source, error_reason, COUNT(*) as count \
   FROM iceberg.market_data.silver_dlq_trades \
   GROUP BY bronze_source, error_reason ORDER BY count DESC LIMIT 10;"
```

Expected: DLQ count < 0.1% of Silver record count in normal operation

---

## Monitoring & Alerting

### Key Metrics to Track

1. **Spark Master UI** (http://localhost:8080):
   - `coresused/cores` - should stay < 80% in steady state
   - Applications WAITING count - should be 0
   - Task failure rate

2. **Container Resource Usage**:
   ```bash
   docker stats --format "table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}"
   ```
   - CPU should be < 50% per worker in steady state
   - Memory should be < 75% of limit

3. **Streaming Query Progress**:
   ```bash
   docker logs <job-container> | grep "numInputRows\\|numOutputRows"
   ```
   - Input/output should be balanced (no growing backlog)
   - Commit latency should be < 1 second

### Alerting Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Cores used | > 80% | > 95% | Add worker or reduce job count |
| Applications WAITING | > 0 for 2 min | > 0 for 5 min | Check resource allocation |
| Worker memory | > 75% | > 90% | Increase worker memory limit or reduce Spark memory |
| Task failures | > 1/min | > 5/min | Check logs, may be data quality issue |
| DLQ rate | > 0.1% | > 1% | Investigate data quality issues at source |

---

## Related Documentation

- [Decision #014: Explicit NULL Handling](../../phases/v1/phase-10-streaming-crypto/DECISIONS.md#decision-014)
- [Decision #015: Right-Sized Resource Allocation](../../phases/v1/phase-10-streaming-crypto/DECISIONS.md#decision-015)
- [Decision #016: 1 Core Per Streaming Job](../../phases/v1/phase-10-streaming-crypto/DECISIONS.md#decision-016)
- [SILVER_FIX_SUMMARY.md](../../phases/v1/phase-10-streaming-crypto/SILVER_FIX_SUMMARY.md)
- [RESOURCE_ALLOCATION_FIX.md](../../phases/v1/phase-10-streaming-crypto/RESOURCE_ALLOCATION_FIX.md)

---

**Last Updated**: 2026-01-19
**Maintained By**: Data Engineering Team
