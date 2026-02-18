# Runbook: Iceberg Offload Consecutive Failures

**Severity:** 🔴 Critical
**Alert:** `IcebergOffloadConsecutiveFailures`
**Response Time:** Immediate (< 15 minutes)
**Last Updated:** 2026-02-18
**Maintained By:** Platform Engineering

---

## Summary

This runbook covers recovery procedures when the Iceberg offload pipeline experiences 3 or more consecutive failures within a 15-minute window. This indicates a persistent issue preventing cold tier updates, which can lead to data loss if ClickHouse TTL triggers before resolution.

**Alert Trigger:** `sum(increase(offload_errors_total[15m])) by (table) >= 3`

---

## Symptoms

### What You'll See

- **Prometheus Alert:** `IcebergOffloadConsecutiveFailures` firing for specific table(s)
- **Grafana Dashboard:** Error rate panel showing sustained errors
- **Scheduler Logs:** Multiple "✗ Offload failed" messages
- **Cold Tier:** Data lag increasing (visible in `offload_lag_minutes` metric)

### User Impact

- **Immediate:** Cold tier data becomes stale (queries may return incomplete results)
- **Extended (>24h):** Risk of data loss if ClickHouse TTL triggers before cold tier updated
- **Severity:** High - SLO violation after 30 minutes of lag

---

## Diagnosis

### Step 1: Confirm Alert Details

```bash
# View Prometheus alert details
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname=="IcebergOffloadConsecutiveFailures")'

# Expected output: Alert status, affected table(s), duration
```

**Note which table(s) are affected** - failures may be table-specific or system-wide.

### Step 2: Check Orchestration Status

```bash
# Verify Prefect worker is running
docker ps --filter name=k2-prefect-worker --format "table {{.Names}}\t{{.Status}}"

# View recent flow runs (check for FAILED state)
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api prefect flow-run ls --limit 10"
```

Or check visually at **http://localhost:4200** → Flow Runs.

**Expected:**
- Worker container running
- Recent flow runs in COMPLETED state
- Any FAILED runs will show error details in Prefect UI

**If worker is not running:** Jump to [Resolution: Worker Not Running](#scenario-1-scheduler-not-running)

### Step 3: Identify Error Type

Check the failed flow run's logs in the Prefect UI (http://localhost:4200 → Flow Runs → select FAILED run → Logs tab), or:

```bash
# View worker container logs for recent errors
docker logs k2-prefect-worker --tail=100 | grep -E "failed|error|timeout|crash"
```

**Common Error Types:**
1. **Timeout:** `Offload timeout: {table} (>10 minutes)`
2. **Connection Error:** ClickHouse or Iceberg connectivity issue
3. **Process Error:** Spark job failed/crashed
4. **Watermark Corruption:** Duplicate key or constraint violation

### Step 4: Check Component Health

Run these checks in parallel:

```bash
# 1. ClickHouse health
docker exec k2-clickhouse clickhouse-client -q "SELECT 1"
docker exec k2-clickhouse clickhouse-client -q "SELECT version()"

# 2. Spark container
docker ps | grep spark-iceberg
docker exec k2-spark-iceberg ps aux | grep java

# 3. PostgreSQL (watermark DB)
docker exec k2-prefect-db psql -U prefect -d prefect -c "\dt"
docker exec k2-prefect-db psql -U prefect -d prefect -c "SELECT COUNT(*) FROM offload_watermarks"

# 4. Network connectivity (from Spark to ClickHouse)
docker exec k2-spark-iceberg ping -c 5 k2-clickhouse
```

**Expected:**
- ClickHouse returns version (e.g., `24.3.x`)
- Spark container running with Java process
- PostgreSQL has `offload_watermarks` table
- Ping latency < 1ms (same host)

---

## Resolution

### Scenario 1: Worker Not Running

**Symptoms:** `k2-prefect-worker` not in `docker ps` output, or shows "Exited"

**Steps:**

1. **Check why the worker stopped:**
   ```bash
   docker logs k2-prefect-worker --tail=100
   ```

2. **Common stop reasons:**
   - Container OOM-killed (check `docker inspect k2-prefect-worker`)
   - Prefect server unreachable on startup
   - psycopg2/dependency error (should be fixed by Spark Dockerfile)
   - Manual stop

3. **Restart the worker:**
   ```bash
   docker start k2-prefect-worker

   # Verify startup
   docker ps --filter name=k2-prefect-worker
   docker logs k2-prefect-worker --tail=20
   ```

4. **Verify recovery:**
   - Trigger a manual run: `docker exec k2-prefect-worker bash -c "PREFECT_API_URL=http://prefect-server:4200/api prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'"`
   - Check http://localhost:4200 → Flow Runs for COMPLETED state

**If restart fails:** See [iceberg-scheduler-recovery.md](iceberg-scheduler-recovery.md) for full recovery procedures.

---

### Scenario 2: ClickHouse Connectivity Failure

**Symptoms:** Logs show "Connection refused", "Timeout", or JDBC errors

**Steps:**

1. **Verify ClickHouse is running:**
   ```bash
   docker ps | grep clickhouse

   # If not running, start it
   docker start k2-clickhouse

   # Wait 10 seconds for startup
   sleep 10
   ```

2. **Test ClickHouse connectivity:**
   ```bash
   # From host
   docker exec k2-clickhouse clickhouse-client -q "SELECT 1"

   # From Spark container (offload perspective)
   docker exec k2-spark-iceberg curl -s http://k2-clickhouse:8123/ping
   ```

3. **Check ClickHouse logs for errors:**
   ```bash
   docker logs k2-clickhouse --tail=100 | grep -i error
   ```

4. **Common ClickHouse issues:**

   **Issue 4a: ClickHouse out of memory**
   ```bash
   # Check memory usage
   docker stats k2-clickhouse --no-stream

   # If >90% memory: Restart ClickHouse
   docker restart k2-clickhouse
   ```

   **Issue 4b: ClickHouse crashed**
   ```bash
   # Check exit code
   docker inspect k2-clickhouse | jq '.[0].State'

   # View crash logs
   docker logs k2-clickhouse --tail=200

   # Restart
   docker start k2-clickhouse
   ```

   **Issue 4c: Network partition**
   ```bash
   # Verify Docker network
   docker network inspect k2-network

   # Reconnect if needed
   docker network connect k2-network k2-clickhouse
   docker network connect k2-network k2-spark-iceberg
   ```

5. **Trigger manual offload test:**
   ```bash
   # Run single table offload
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze

   # Expected: "Offload completed successfully"
   ```

---

### Scenario 3: Spark Job Failures

**Symptoms:** Logs show "CalledProcessError", Spark executor errors, Java exceptions

**Steps:**

1. **Check Spark container health:**
   ```bash
   docker ps | grep spark-iceberg
   docker exec k2-spark-iceberg ps aux | grep java
   ```

2. **Review Spark logs:**
   ```bash
   # Executor logs
   docker exec k2-spark-iceberg tail -100 /spark/logs/spark-executor.log

   # Worker logs
   docker exec k2-spark-iceberg tail -100 /spark/logs/spark-worker.log

   # Application logs
   docker exec k2-spark-iceberg ls -lt /spark/work/
   ```

3. **Common Spark issues:**

   **Issue 3a: Out of memory (OOM)**
   ```bash
   # Check for OOM in logs
   docker logs k2-spark-iceberg | grep -i "OutOfMemoryError"

   # Solution: Restart Spark container
   docker restart k2-spark-iceberg

   # Wait for Spark to be ready (30 seconds)
   sleep 30
   ```

   **Issue 3b: JDBC driver issues**
   ```bash
   # Check JDBC driver present
   docker exec k2-spark-iceberg ls -la /opt/spark/jars/ | grep clickhouse

   # Expected: clickhouse-jdbc-0.4.6-all.jar

   # If missing: Rebuild Spark image (should not happen)
   ```

   **Issue 3c: Iceberg catalog access issues**
   ```bash
   # Check Iceberg warehouse directory
   docker exec k2-spark-iceberg ls -la /home/iceberg/warehouse/cold/

   # Check permissions
   docker exec k2-spark-iceberg ls -ld /home/iceberg/warehouse/

   # Expected: drwxrwxr-x (read/write access)
   ```

4. **Trigger manual Spark test:**
   ```bash
   # Test Spark can read ClickHouse
   docker exec k2-spark-iceberg spark-shell --master local[*] \
     --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.3 \
     --conf spark.sql.catalog.spark_catalog=org.apache.iceberg.spark.SparkSessionCatalog \
     --conf spark.sql.catalog.spark_catalog.type=hadoop \
     --conf spark.sql.catalog.spark_catalog.warehouse=/home/iceberg/warehouse/ \
     -e "spark.read.format(\"jdbc\").option(\"url\", \"jdbc:clickhouse://k2-clickhouse:8123/k2\").option(\"query\", \"SELECT 1\").load().show()"

   # Expected: Shows result "1"
   ```

---

### Scenario 4: Watermark Table Corruption

**Symptoms:** Logs show "duplicate key", "constraint violation", or watermark errors

**Steps:**

1. **Inspect watermark table:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT table_name, max_timestamp, sequence_number, created_at
      FROM offload_watermarks
      ORDER BY created_at DESC
      LIMIT 10"
   ```

2. **Check for corruption patterns:**
   ```bash
   # Check for duplicate entries
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT table_name, COUNT(*)
      FROM offload_watermarks
      GROUP BY table_name
      HAVING COUNT(*) > 100"

   # Expected: Low counts (<100 entries per table for 1-2 days)
   ```

3. **If watermark corruption confirmed:**

   **CRITICAL: This resets offload watermark - may cause duplicates if not careful**

   ```bash
   # Backup current watermarks
   docker exec k2-prefect-db pg_dump -U prefect -d prefect -t offload_watermarks > /tmp/watermarks_backup_$(date +%Y%m%d_%H%M%S).sql

   # Get latest successful watermark for affected table
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT * FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      ORDER BY created_at DESC LIMIT 1"

   # Note the max_timestamp and sequence_number

   # Delete old watermark entries (keep last 10)
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "DELETE FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      AND id NOT IN (
        SELECT id FROM offload_watermarks
        WHERE table_name = 'bronze_trades_binance'
        ORDER BY created_at DESC LIMIT 10
      )"
   ```

4. **Verify watermark cleanup:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT COUNT(*) FROM offload_watermarks WHERE table_name = 'bronze_trades_binance'"

   # Expected: ≤10 entries
   ```

5. **Trigger manual offload:**
   ```bash
   # Next scheduler cycle will use cleaned watermark
   # Or trigger manually (see Scenario 2, Step 5)
   ```

---

### Scenario 5: Timeout Errors (Offload > 10 minutes)

**Symptoms:** Logs show "Offload timeout: {table} (>10 minutes)"

**Root Cause:** Data volume spike or performance degradation

**Steps:**

1. **Check data volume:**
   ```bash
   # Check rows in source table (last hour)
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT COUNT(*) FROM bronze_trades_binance
      WHERE exchange_timestamp > now() - INTERVAL 1 HOUR"

   # Expected: <5M rows/hour normally
   # If >10M rows/hour: Data volume spike
   ```

2. **Check ClickHouse query performance:**
   ```bash
   # Check slow queries
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT query, elapsed
      FROM system.processes
      WHERE query LIKE '%bronze_trades%'
      ORDER BY elapsed DESC LIMIT 10"
   ```

3. **Temporary mitigation (if data volume spike):**

   **Option A: Increase timeout (temporary)**
   ```bash
   # Edit iceberg_offload_flow.py — change timeout=600 to timeout=1200 in offload_table task
   # Then restart the worker to pick up the change
   docker restart k2-prefect-worker
   ```

   **Option B: Manual offload with higher timeout**
   ```bash
   # Run offload manually with longer timeout
   timeout 1800 docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze

   # 1800s = 30-minute timeout
   ```

4. **Long-term fix:**
   - See runbook: [iceberg-offload-performance.md](iceberg-offload-performance.md)
   - Consider parallel offload implementation (Phase 7)

---

## Prevention

### Proactive Measures

1. **Monitor key metrics:**
   - `offload_lag_minutes` - Alert if approaching 20 minutes
   - `offload_errors_total` - Alert on any sustained errors
   - `offload_cycle_duration_seconds` - Alert if trending upward

2. **Regular health checks (daily):**
   ```bash
   # Quick health check
   echo "=== Iceberg Offload Health Check ==="
   echo "1. Prefect worker status:"
   docker ps --filter name=k2-prefect-worker --format "{{.Status}}"

   echo "2. Recent flow runs:"
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect flow-run ls --limit 5"

   echo "3. Watermark count:"
   docker exec k2-prefect-db psql -U prefect -d prefect -c "SELECT COUNT(*) FROM offload_watermarks"
   ```

3. **Weekly watermark cleanup (prevent corruption):**
   ```bash
   # Keep only last 50 watermark entries per table
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "DELETE FROM offload_watermarks
      WHERE id NOT IN (
        SELECT id FROM offload_watermarks
        ORDER BY created_at DESC LIMIT 50
      )"
   ```

4. **Capacity planning:**
   - Monitor data volume trends
   - If sustained >5M rows/hour: Consider parallel offload
   - If Spark memory >80%: Increase Spark resources

---

## Related Monitoring

### Dashboards
- **Primary:** [Iceberg Offload Pipeline](http://localhost:3000/d/iceberg-offload)
- **ClickHouse:** [ClickHouse Overview](http://localhost:3000/d/clickhouse-overview)

### Metrics
- `offload_errors_total{table, error_type}` - Error counts by type
- `offload_cycles_total{status="failed"}` - Failed cycle count
- `offload_duration_seconds` - Duration distribution (check for timeouts)

### Alerts
- **This Alert:** `IcebergOffloadConsecutiveFailures` (Critical)
- **Related:** `IcebergOffloadLagCritical` (may fire simultaneously)

### Logs
- **Flow run logs:** http://localhost:4200 → Flow Runs → select run → Logs tab
- **Worker logs:** `docker logs k2-prefect-worker --tail=100`
- **ClickHouse:** `docker logs k2-clickhouse`
- **Spark:** `docker exec k2-spark-iceberg tail /spark/logs/spark-executor.log`

---

## Post-Incident

### After Resolution

1. **Verify recovery:**
   ```bash
   # Check next 3 flow runs completed successfully
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect flow-run ls --limit 5"

   # Verify lag returning to normal
   curl -s http://localhost:8000/metrics | grep offload_lag_minutes
   ```

2. **Document root cause:**
   - Update incident log with root cause
   - If new failure mode: Update this runbook

3. **Review prevention measures:**
   - Were alerts timely? Tune thresholds if needed
   - Could this have been prevented? Add monitoring/automation

4. **Post-mortem (if extended outage >1 hour):**
   - Timeline of events
   - Root cause analysis
   - Action items for prevention

### Escalation

**Escalate to Engineering Lead if:**
- Resolution takes >30 minutes
- Root cause is unclear
- Requires code changes or infrastructure modifications
- Data loss suspected

**Contact:** Platform Engineering Team

---

## Quick Reference

### Fastest Recovery Path

```bash
# 1. Check Prefect worker (5 seconds)
docker ps --filter name=k2-prefect-worker --format "table {{.Names}}\t{{.Status}}"

# 2. Check components (10 seconds)
docker exec k2-clickhouse clickhouse-client -q "SELECT 1"
docker ps | grep spark-iceberg

# 3. Restart worker if needed (15 seconds)
docker restart k2-prefect-worker

# 4. Trigger manual run and verify (30 seconds)
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api \
   prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'"
# Then check http://localhost:4200 for run status
```

**Total MTTR (Mean Time To Recovery):** 15-30 minutes for most scenarios

---

## Troubleshooting Decision Tree

```
Offload Failures Detected
    │
    ├─ Scheduler not running?
    │   └─> Scenario 1: Restart scheduler
    │
    ├─ ClickHouse unreachable?
    │   └─> Scenario 2: ClickHouse connectivity
    │
    ├─ Spark errors in logs?
    │   └─> Scenario 3: Spark job failures
    │
    ├─ Watermark errors?
    │   └─> Scenario 4: Watermark corruption
    │
    └─ Timeout errors?
        └─> Scenario 5: Performance degradation
```

---

**Last Updated:** 2026-02-18
**Maintained By:** Platform Engineering
**Version:** 2.0 (Prefect 3.x update)
**Related Runbooks:**
- [iceberg-offload-lag.md](iceberg-offload-lag.md) - High lag resolution
- [iceberg-offload-performance.md](iceberg-offload-performance.md) - Performance degradation
- [iceberg-scheduler-recovery.md](iceberg-scheduler-recovery.md) - Worker/orchestration recovery
