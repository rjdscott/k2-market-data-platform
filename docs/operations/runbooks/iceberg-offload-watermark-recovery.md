# Runbook: Iceberg Offload Watermark Recovery

**Severity:** 🔴 Critical
**Alert:** `IcebergOffloadWatermarkStale`
**Response Time:** Immediate (<15 minutes)
**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering

---

## Summary

This runbook covers recovery procedures when the offload watermark (tracking last processed data) becomes stale (not updated >1 hour) or corrupted. The watermark is critical for exactly-once semantics - corruption can lead to duplicate data or data loss.

**Alert Trigger:** `(time() - watermark_timestamp_seconds) > 3600` (>1 hour)

**Watermark Role:**
- Tracks last successfully offloaded `(max_timestamp, sequence_number)`
- Ensures exactly-once semantics (no duplicates, no data loss)
- Stored in PostgreSQL (`offload_watermarks` table)

---

## Symptoms

### What You'll See

- **Prometheus Alert:** `IcebergOffloadWatermarkStale` firing for specific table(s)
- **Grafana Dashboard:** `watermark_timestamp_seconds` metric not updating
- **Scheduler Logs:** Cycles completing but watermark not progressing
- **Impact:** Pipeline may be hung, silently failing, or writing duplicate data

### User Impact

- **Immediate:** Cold tier not updating (stale data)
- **Extended:** Risk of duplicate data if watermark corrupted and scheduler continues
- **Severity:** Critical - indicates pipeline malfunction

---

## Diagnosis

### Step 1: Verify Watermark Staleness

```bash
# Check watermark age via Prometheus
curl -s 'http://localhost:9090/api/v1/query?query=(time()-watermark_timestamp_seconds)' | \
  jq '.data.result[] | {table: .metric.table, age_seconds: .value[1]}'

# Expected: age_seconds < 1800 (30 minutes)
# If >3600 (1 hour): Watermark stale
```

### Step 2: Check Watermark Table

```bash
# View recent watermark entries
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT
     id,
     table_name,
     max_timestamp,
     sequence_number,
     created_at,
     NOW() - created_at AS age
   FROM offload_watermarks
   WHERE table_name IN ('bronze_trades_binance', 'bronze_trades_kraken')
   ORDER BY created_at DESC
   LIMIT 10"
```

**Look for:**
- **Last entry age:** Should be <30 minutes
- **Entry count:** Should have regular entries (every 15 min)
- **Gaps:** Missing entries indicate offload not running or failing

### Step 3: Check Scheduler Status

```bash
# Verify scheduler is running
systemctl status iceberg-offload-scheduler

# Check recent cycle activity
tail -50 /tmp/iceberg-offload-scheduler.log | grep -E "CYCLE|watermark"

# Check for watermark errors
grep -i "watermark" /tmp/iceberg-offload-scheduler.log | tail -20
```

**Expected:** Scheduler running, cycles completing, no watermark errors

**If scheduler not running:** Jump to [Scenario 1: Scheduler Not Running](#scenario-1-scheduler-not-running)

### Step 4: Check PostgreSQL Health

```bash
# Verify PostgreSQL is accessible
docker exec k2-prefect-db psql -U prefect -d prefect -c "\dt"

# Check connection count
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = 'prefect'"

# Check for lock issues
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT * FROM pg_locks WHERE NOT granted"
```

**Expected:**
- Connection successful
- Connection count <20
- No ungrant

ed locks

**If PostgreSQL issues:** Jump to [Scenario 2: PostgreSQL Connection Issues](#scenario-2-postgresql-connection-issues)

### Step 5: Identify Watermark Issue Type

Determine which scenario applies:

| Symptoms | Scenario |
|----------|----------|
| Scheduler not running | Scenario 1: Scheduler Not Running |
| PostgreSQL unreachable | Scenario 2: PostgreSQL Connection Issues |
| Watermark table empty or missing | Scenario 3: Watermark Table Missing |
| Duplicate entries or constraint violations | Scenario 4: Watermark Corruption |
| Watermark exists but not progressing | Scenario 5: Silent Offload Failure |

---

## Resolution

### Scenario 1: Scheduler Not Running

**Symptoms:** Scheduler process not found, systemd shows "inactive"

**See:** [iceberg-scheduler-recovery.md](iceberg-scheduler-recovery.md) for detailed scheduler recovery

**Quick Resolution:**

```bash
# Start scheduler
sudo systemctl start iceberg-offload-scheduler

# Verify startup
systemctl status iceberg-offload-scheduler

# Watch for watermark update (wait up to 15 minutes for next cycle)
tail -f /tmp/iceberg-offload-scheduler.log
```

**Verify watermark updates:**
```bash
# Check watermark after next cycle
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT * FROM offload_watermarks ORDER BY created_at DESC LIMIT 3"

# Expected: New entry within 15 minutes
```

---

### Scenario 2: PostgreSQL Connection Issues

**Symptoms:** Logs show "connection refused", "timeout", or database errors

**Steps:**

1. **Verify PostgreSQL is running:**
   ```bash
   docker ps | grep prefect-db

   # If not running:
   docker start k2-prefect-db

   # Wait for startup (5 seconds)
   sleep 5
   ```

2. **Test connection from Spark container:**
   ```bash
   # Test connection (this is where scheduler connects from)
   docker exec k2-spark-iceberg psql -h k2-prefect-db -U prefect -d prefect -c "\dt"

   # If fails: Network or authentication issue
   ```

3. **Common PostgreSQL issues:**

   **Issue 2a: PostgreSQL crashed**
   ```bash
   # Check exit status
   docker inspect k2-prefect-db | jq '.[0].State'

   # View crash logs
   docker logs k2-prefect-db --tail=100 | grep -i error

   # Restart
   docker restart k2-prefect-db

   # Verify database accessible
   docker exec k2-prefect-db psql -U prefect -d prefect -c "SELECT 1"
   ```

   **Issue 2b: Connection pool exhausted**
   ```bash
   # Check active connections
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT COUNT(*), state FROM pg_stat_activity GROUP BY state"

   # If many "idle" connections: Kill idle connections
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT pg_terminate_backend(pid)
      FROM pg_stat_activity
      WHERE datname = 'prefect'
        AND state = 'idle'
        AND state_change < NOW() - INTERVAL '10 minutes'"
   ```

   **Issue 2c: Network partition**
   ```bash
   # Verify Docker network
   docker network inspect k2-network | grep -A 5 k2-prefect-db
   docker network inspect k2-network | grep -A 5 k2-spark-iceberg

   # Reconnect if needed
   docker network connect k2-network k2-prefect-db
   docker network connect k2-network k2-spark-iceberg
   ```

4. **Test scheduler can connect:**
   ```bash
   # Restart scheduler to establish new connection
   sudo systemctl restart iceberg-offload-scheduler

   # Watch logs for connection success
   tail -f /tmp/iceberg-offload-scheduler.log
   ```

---

### Scenario 3: Watermark Table Missing

**Symptoms:** Query returns "relation does not exist" or table is empty

**WARNING:** This is rare and indicates serious database issue

**Steps:**

1. **Verify table existence:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect -c "\dt offload_watermarks"

   # If not found: Table was deleted or never created
   ```

2. **Recreate watermark table:**
   ```sql
   docker exec k2-prefect-db psql -U prefect -d prefect <<'EOF'
   CREATE TABLE IF NOT EXISTS offload_watermarks (
       id SERIAL PRIMARY KEY,
       table_name VARCHAR(255) NOT NULL,
       max_timestamp TIMESTAMP NOT NULL,
       sequence_number BIGINT NOT NULL,
       created_at TIMESTAMP DEFAULT NOW()
   );

   CREATE INDEX IF NOT EXISTS idx_watermarks_table_created
   ON offload_watermarks(table_name, created_at DESC);
   EOF
   ```

3. **Initialize watermarks from ClickHouse:**

   **CRITICAL:** This determines starting point for offload

   ```bash
   # For each table, set watermark to "now - 24 hours"
   # This ensures we don't miss recent data

   # Get current timestamp from ClickHouse
   CURRENT_TS=$(docker exec k2-clickhouse clickhouse-client -q "SELECT NOW() - INTERVAL 24 HOUR FORMAT TSV")

   # Initialize Binance watermark
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT MAX(sequence_number)
      FROM bronze_trades_binance
      WHERE exchange_timestamp < now() - INTERVAL 24 HOUR
      FORMAT TSV" | \
     xargs -I {} docker exec k2-prefect-db psql -U prefect -d prefect -c \
       "INSERT INTO offload_watermarks (table_name, max_timestamp, sequence_number)
        VALUES ('bronze_trades_binance', '$CURRENT_TS', {})"

   # Initialize Kraken watermark
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT MAX(sequence_number)
      FROM bronze_trades_kraken
      WHERE exchange_timestamp < now() - INTERVAL 24 HOUR
      FORMAT TSV" | \
     xargs -I {} docker exec k2-prefect-db psql -U prefect -d prefect -c \
       "INSERT INTO offload_watermarks (table_name, max_timestamp, sequence_number)
        VALUES ('bronze_trades_kraken', '$CURRENT_TS', {})"
   ```

4. **Verify initialization:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT * FROM offload_watermarks ORDER BY created_at DESC"

   # Expected: 2 entries (binance + kraken) with timestamps ~24h ago
   ```

5. **Trigger offload to rebuild cold tier:**
   ```bash
   # Next scheduler cycle will offload last 24 hours
   # This may take longer than usual (more data)

   # Monitor progress
   tail -f /tmp/iceberg-offload-scheduler.log
   ```

---

### Scenario 4: Watermark Corruption

**Symptoms:** Duplicate entries, constraint violations, or inconsistent watermarks

**Steps:**

1. **Diagnose corruption:**
   ```bash
   # Check for duplicate recent entries
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT table_name, COUNT(*) AS entry_count
      FROM offload_watermarks
      WHERE created_at > NOW() - INTERVAL '1 hour'
      GROUP BY table_name
      ORDER BY entry_count DESC"

   # Expected: 4 entries per table per hour (one per 15-min cycle)
   # If >10: Possible duplicate writes

   # Check for watermark regression (timestamp going backward)
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT
        table_name,
        max_timestamp,
        LAG(max_timestamp) OVER (PARTITION BY table_name ORDER BY created_at) AS prev_timestamp,
        (max_timestamp < LAG(max_timestamp) OVER (PARTITION BY table_name ORDER BY created_at)) AS regression
      FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      ORDER BY created_at DESC
      LIMIT 10"

   # If regression = true: Watermark went backward (serious issue)
   ```

2. **Backup watermark table:**
   ```bash
   # Always backup before cleanup
   docker exec k2-prefect-db pg_dump -U prefect -d prefect -t offload_watermarks \
     > /tmp/watermarks_backup_$(date +%Y%m%d_%H%M%S).sql

   echo "Backup saved to: /tmp/watermarks_backup_$(date +%Y%m%d_%H%M%S).sql"
   ```

3. **Clean duplicate entries:**
   ```bash
   # Keep only latest 10 entries per table
   docker exec k2-prefect-db psql -U prefect -d prefect <<'EOF'
   -- For each table, delete old entries keeping latest 10
   WITH ranked AS (
     SELECT
       id,
       ROW_NUMBER() OVER (PARTITION BY table_name ORDER BY created_at DESC) AS rn
     FROM offload_watermarks
   )
   DELETE FROM offload_watermarks
   WHERE id IN (
     SELECT id FROM ranked WHERE rn > 10
   );
   EOF
   ```

4. **Verify cleanup:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT table_name, COUNT(*) AS entries
      FROM offload_watermarks
      GROUP BY table_name"

   # Expected: ≤10 entries per table
   ```

5. **Check for watermark regression:**
   ```bash
   # If watermarks went backward, we need to reset to safe point
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT table_name, MAX(max_timestamp) AS safe_timestamp, MAX(sequence_number) AS safe_sequence
      FROM offload_watermarks
      GROUP BY table_name"

   # Use these values to create new safe watermark entries
   # Delete all existing, insert one safe entry per table
   ```

6. **Restart scheduler with clean watermarks:**
   ```bash
   sudo systemctl restart iceberg-offload-scheduler

   # Monitor for correct watermark progression
   tail -f /tmp/iceberg-offload-scheduler.log
   ```

---

### Scenario 5: Silent Offload Failure

**Symptoms:** Scheduler running, cycles "completing", but watermarks not updating

**Root Cause:** Offload writing 0 rows but reporting success (logic bug)

**Steps:**

1. **Check cycle outcomes:**
   ```bash
   # Look for "0 rows" offloads
   grep "Offload completed:" /tmp/iceberg-offload-scheduler.log | tail -20

   # If all show "0 rows": No data being offloaded despite success
   ```

2. **Verify ClickHouse has data:**
   ```bash
   # Check data exists beyond watermark
   LAST_TS=$(docker exec k2-prefect-db psql -U prefect -d prefect -t -c \
     "SELECT max_timestamp FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      ORDER BY created_at DESC LIMIT 1" | xargs)

   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT COUNT(*) FROM bronze_trades_binance
      WHERE exchange_timestamp > '$LAST_TS'"

   # If COUNT > 0: Data exists, not being offloaded
   ```

3. **Check offload script for logic errors:**
   ```bash
   # Run manual offload with verbose logging
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze \
     2>&1 | tee /tmp/manual_offload_debug.log

   # Check for SQL errors, Spark errors, or logic issues
   grep -i error /tmp/manual_offload_debug.log
   ```

4. **If manual offload works:**
   - Scheduler integration issue (watermark read/write)
   - Check scheduler code for watermark update logic
   - May need code fix (escalate to engineering)

5. **Temporary workaround (manual offload):**
   ```bash
   # Stop scheduler to prevent confusion
   sudo systemctl stop iceberg-offload-scheduler

   # Run manual offload every 15 minutes (cron or tmux loop)
   while true; do
     docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
       --source-table bronze_trades_binance \
       --target-table cold.bronze_trades_binance \
       --timestamp-col exchange_timestamp \
       --sequence-col sequence_number \
       --layer bronze

     docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
       --source-table bronze_trades_kraken \
       --target-table cold.bronze_trades_kraken \
       --timestamp-col exchange_timestamp \
       --sequence-col sequence_number \
       --layer bronze

     sleep 900  # 15 minutes
   done
   ```

---

## Prevention

### Proactive Measures

1. **Watermark monitoring (already configured):**
   - Alert: `IcebergOffloadWatermarkStale` (>1 hour)
   - Monitor: `watermark_timestamp_seconds` metric

2. **Regular watermark cleanup (weekly):**
   ```bash
   cat > /usr/local/bin/watermark-cleanup.sh <<'EOF'
   #!/bin/bash
   # Keep only last 50 watermark entries per table
   docker exec k2-prefect-db psql -U prefect -d prefect <<'SQL'
   WITH ranked AS (
     SELECT id, ROW_NUMBER() OVER (PARTITION BY table_name ORDER BY created_at DESC) AS rn
     FROM offload_watermarks
   )
   DELETE FROM offload_watermarks WHERE id IN (SELECT id FROM ranked WHERE rn > 50);
   SQL
   EOF

   chmod +x /usr/local/bin/watermark-cleanup.sh

   # Add to cron (weekly on Sunday 2am)
   (crontab -l; echo "0 2 * * 0 /usr/local/bin/watermark-cleanup.sh") | crontab -
   ```

3. **Daily watermark validation:**
   ```bash
   # Check watermark health
   cat > /usr/local/bin/watermark-health-check.sh <<'EOF'
   #!/bin/bash
   echo "=== Watermark Health Check ==="

   # Check watermark age
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT table_name, NOW() - MAX(created_at) AS age
      FROM offload_watermarks
      GROUP BY table_name"

   # Check for regression
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT COUNT(*) AS regressions
      FROM (
        SELECT
          table_name,
          max_timestamp,
          LAG(max_timestamp) OVER (PARTITION BY table_name ORDER BY created_at) AS prev_ts
        FROM offload_watermarks
        WHERE created_at > NOW() - INTERVAL '1 day'
      ) sub
      WHERE max_timestamp < prev_ts"
   EOF

   chmod +x /usr/local/bin/watermark-health-check.sh

   # Add to cron (daily at 8am)
   (crontab -l; echo "0 8 * * * /usr/local/bin/watermark-health-check.sh | mail -s 'Watermark Health' platform-team@company.com") | crontab -
   ```

4. **PostgreSQL backup:**
   ```bash
   # Backup watermark table daily
   0 3 * * * docker exec k2-prefect-db pg_dump -U prefect -d prefect -t offload_watermarks | gzip > /backup/watermarks_$(date +\%Y\%m\%d).sql.gz
   ```

---

## Related Monitoring

### Dashboards
- **Primary:** [Iceberg Offload Pipeline](http://localhost:3000/d/iceberg-offload)
- **Metrics:** `watermark_timestamp_seconds` (should advance every 15 min)

### Metrics
- `watermark_timestamp_seconds{table}` - Unix timestamp of last watermark update
- `offload_lag_minutes{table}` - Calculated from watermark age

### Alerts
- **This Alert:** `IcebergOffloadWatermarkStale` (Critical, >1 hour)
- **Related:** `IcebergOffloadLagCritical` (may fire simultaneously)

### Logs
- **Scheduler:** `/tmp/iceberg-offload-scheduler.log` (grep "watermark")
- **PostgreSQL:** `docker logs k2-prefect-db`

---

## Post-Incident

### After Resolution

1. **Verify watermark progression:**
   ```bash
   # Watch watermarks update over 3 cycles (45 minutes)
   for i in {1..3}; do
     sleep 900  # 15 minutes
     docker exec k2-prefect-db psql -U prefect -d prefect -c \
       "SELECT table_name, max_timestamp, created_at
        FROM offload_watermarks
        ORDER BY created_at DESC LIMIT 2"
   done

   # Expected: New entries every 15 minutes, timestamps advancing
   ```

2. **Check for duplicate data (if corruption):**
   ```bash
   # Check Iceberg table for duplicates
   docker exec k2-spark-iceberg spark-shell --master local[*] \
     --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.3 \
     --conf spark.sql.catalog.spark_catalog=org.apache.iceberg.spark.SparkSessionCatalog \
     --conf spark.sql.catalog.spark_catalog.type=hadoop \
     --conf spark.sql.catalog.spark_catalog.warehouse=/home/iceberg/warehouse/ \
     -e "spark.read.table(\"cold.bronze_trades_binance\").groupBy(\"sequence_number\").count().where(\"count > 1\").show()"

   # Expected: No duplicates (empty result)
   ```

3. **Document root cause:**
   - What caused watermark staleness/corruption?
   - Update this runbook if new failure mode discovered

### Escalation

**Escalate to Engineering Lead if:**
- Watermark corruption recurs
- Silent offload failure (logic bug suspected)
- Data duplicates detected in cold tier
- Requires code changes to prevent recurrence

**Contact:** Platform Engineering Team

---

## Quick Reference

### Watermark Recovery Checklist

```bash
# 1. Check watermark age (10 seconds)
curl -s 'http://localhost:9090/api/v1/query?query=(time()-watermark_timestamp_seconds)'

# 2. Check watermark table (10 seconds)
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT * FROM offload_watermarks ORDER BY created_at DESC LIMIT 5"

# 3. Check scheduler status (5 seconds)
systemctl status iceberg-offload-scheduler

# 4. If scheduler issue, restart (30 seconds)
sudo systemctl restart iceberg-offload-scheduler

# 5. If corruption, backup and clean (2 minutes)
docker exec k2-prefect-db pg_dump -U prefect -d prefect -t offload_watermarks > /tmp/watermark_backup.sql
# Then run cleanup SQL

# 6. Verify recovery (15 minutes)
tail -f /tmp/iceberg-offload-scheduler.log
```

**Total MTTR:** 15-30 minutes (most scenarios), up to 60 minutes (corruption + validation)

---

**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering
**Version:** 1.0
**Related Runbooks:**
- [iceberg-offload-failure.md](iceberg-offload-failure.md) - General failures
- [iceberg-offload-lag.md](iceberg-offload-lag.md) - High lag (caused by stale watermark)
- [iceberg-scheduler-recovery.md](iceberg-scheduler-recovery.md) - Scheduler issues
