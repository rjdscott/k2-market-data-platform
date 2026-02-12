# Runbook: Iceberg Offload High Lag

**Severity:** 🔴 Critical (>30 min) | 🟡 Warning (20-30 min)
**Alerts:** `IcebergOffloadLagCritical`, `IcebergOffloadLagElevated`
**Response Time:** Critical: Immediate | Warning: Business hours
**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering

---

## Summary

This runbook covers resolution procedures when the offload lag (time since last successful offload) exceeds acceptable thresholds. High lag indicates the cold tier is not being updated on schedule, which can lead to SLO violations and stale data in long-term storage.

**Alert Triggers:**
- **Critical:** `offload_lag_minutes > 30` (SLO violation)
- **Warning:** `offload_lag_minutes > 20 and <= 30` (approaching SLO)

---

## Symptoms

### What You'll See

**Critical (>30 minutes):**
- **Prometheus Alert:** `IcebergOffloadLagCritical` firing for specific table(s)
- **Grafana Dashboard:** Lag gauge showing RED (>30 minutes)
- **Impact:** SLO violation, cold tier data stale

**Warning (20-30 minutes):**
- **Prometheus Alert:** `IcebergOffloadLagElevated` firing
- **Grafana Dashboard:** Lag gauge showing YELLOW (20-30 minutes)
- **Impact:** Trending toward SLO violation, monitor closely

### User Impact

- **Immediate:** Cold tier queries return incomplete/stale data
- **Extended (>24h):** Risk of data loss if ClickHouse TTL triggers
- **Severity:** High for critical (>30 min), Medium for warning (20-30 min)

---

## Diagnosis

### Step 1: Verify Current Lag

```bash
# Check current lag via metrics
curl -s http://localhost:8000/metrics | grep offload_lag_minutes

# Expected output format:
# offload_lag_minutes{table="bronze_trades_binance"} 25.3
# offload_lag_minutes{table="bronze_trades_kraken"} 22.1
```

**Note:** Lag is calculated as `(current_time - last_successful_offload_time)`

### Step 2: Check Scheduler Cycle Frequency

```bash
# Check recent cycles (should be every 15 minutes)
grep "CYCLE STARTED" /tmp/iceberg-offload-scheduler.log | tail -10

# Count cycles in last hour (should be ~4)
grep "CYCLE STARTED" /tmp/iceberg-offload-scheduler.log | \
  grep "$(date +%Y-%m-%d)" | \
  grep "$(date +%H)" | wc -l
```

**Expected:** 4 cycles per hour (one every 15 minutes)

**If <4 cycles:** Scheduler is skipping cycles → [Scenario 1: Scheduler Skipping Cycles](#scenario-1-scheduler-skipping-cycles)

### Step 3: Check Recent Cycle Outcomes

```bash
# Check last 5 cycle completions
grep "COMPLETED" /tmp/iceberg-offload-scheduler.log | tail -5

# Check for failures in last hour
grep "✗ Offload failed" /tmp/iceberg-offload-scheduler.log | \
  grep "$(date +%Y-%m-%d)" | \
  grep "$(date +%H)"
```

**Expected:** All cycles complete successfully

**If failures present:** See [iceberg-offload-failure.md](iceberg-offload-failure.md)

### Step 4: Check Watermark Progression

```bash
# Check watermark timestamps
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT table_name, max_timestamp, NOW() - max_timestamp AS lag, created_at
   FROM offload_watermarks
   WHERE table_name IN ('bronze_trades_binance', 'bronze_trades_kraken')
   ORDER BY created_at DESC LIMIT 10"
```

**Expected:** `max_timestamp` advancing every 15 minutes, `lag` < 30 minutes

**If watermark not progressing:** [Scenario 2: Watermark Not Updating](#scenario-2-watermark-not-updating)

### Step 5: Check Data Volume

```bash
# Check ClickHouse data volume (last hour)
docker exec k2-clickhouse clickhouse-client -q \
  "SELECT
     'binance' AS exchange,
     COUNT(*) AS rows,
     MIN(exchange_timestamp) AS min_ts,
     MAX(exchange_timestamp) AS max_ts
   FROM bronze_trades_binance
   WHERE exchange_timestamp > now() - INTERVAL 1 HOUR
   UNION ALL
   SELECT
     'kraken',
     COUNT(*),
     MIN(exchange_timestamp),
     MAX(exchange_timestamp)
   FROM bronze_trades_kraken
   WHERE exchange_timestamp > now() - INTERVAL 1 HOUR"
```

**Expected:** Steady data flow, no large spikes

**If data spike:** [Scenario 3: Data Volume Spike](#scenario-3-data-volume-spike)

---

## Resolution

### Scenario 1: Scheduler Skipping Cycles

**Symptoms:** Fewer than 4 cycles per hour, scheduler running but not executing

**Root Cause:** Scheduler hung, infinite loop, or waiting on blocked operation

**Steps:**

1. **Check scheduler process state:**
   ```bash
   # Get scheduler PID
   PID=$(pgrep -f scheduler.py)

   # Check process state (should be "S" = sleeping between cycles)
   ps -o pid,stat,command -p $PID

   # If state is "R" (running) or "D" (uninterruptible sleep): Hung
   ```

2. **Check if scheduler is in a cycle:**
   ```bash
   # Look for recent "CYCLE STARTED" without "COMPLETED"
   tail -50 /tmp/iceberg-offload-scheduler.log | grep -E "CYCLE STARTED|COMPLETED"

   # If last entry is "STARTED" without "COMPLETED": Cycle hung
   ```

3. **Check for deadlock indicators:**
   ```bash
   # Check for stuck Docker commands
   ps aux | grep "docker exec k2-spark-iceberg"

   # Check Spark job status
   docker exec k2-spark-iceberg ps aux | grep python3
   ```

4. **Resolution: Restart scheduler**

   **IMPORTANT:** Only restart if scheduler is truly hung (not just in a long cycle)

   ```bash
   # Graceful restart (allows current cycle to complete)
   sudo systemctl stop iceberg-offload-scheduler
   sleep 5  # Wait for graceful shutdown
   sudo systemctl start iceberg-offload-scheduler

   # If graceful restart fails (after 30 seconds):
   # Force kill
   sudo systemctl kill -s SIGKILL iceberg-offload-scheduler
   sudo systemctl start iceberg-offload-scheduler
   ```

5. **Verify recovery:**
   ```bash
   # Watch for next cycle
   tail -f /tmp/iceberg-offload-scheduler.log

   # Expected within 15 minutes: "CYCLE STARTED" → "COMPLETED"
   ```

6. **Monitor lag recovery:**
   ```bash
   # Watch lag metric decrease
   watch -n 10 'curl -s http://localhost:8000/metrics | grep offload_lag_minutes'

   # Expected: Lag decreases after next successful cycle
   ```

---

### Scenario 2: Watermark Not Updating

**Symptoms:** Watermark `max_timestamp` not advancing, but cycles completing

**Root Cause:** Offload writing 0 rows (no new data) or watermark update logic failure

**Steps:**

1. **Check recent cycle row counts:**
   ```bash
   # Check how many rows were offloaded recently
   grep "Rows offloaded:" /tmp/iceberg-offload-scheduler.log | tail -10

   # Expected: Non-zero row counts
   # If all "0 rows": No new data in ClickHouse
   ```

2. **Verify ClickHouse has new data:**
   ```bash
   # Check for data newer than last watermark
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT table_name, max_timestamp FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      ORDER BY created_at DESC LIMIT 1"

   # Note the max_timestamp value, then check ClickHouse
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT COUNT(*) FROM bronze_trades_binance
      WHERE exchange_timestamp > '2026-02-12 19:00:00'"  # Use watermark timestamp

   # If COUNT > 0: New data exists, watermark not updating
   # If COUNT = 0: No new data (lag is expected)
   ```

3. **If new data exists but watermark not updating:**

   **Check watermark update logic:**
   ```bash
   # Check for watermark update errors in scheduler logs
   grep "watermark" /tmp/iceberg-offload-scheduler.log | tail -20

   # Check PostgreSQL logs for errors
   docker logs k2-prefect-db --tail=50 | grep -i error
   ```

4. **Manual watermark update (if watermark stuck):**

   **WARNING:** Only do this if watermark is confirmed stuck and scheduler is working

   ```bash
   # Get current max timestamp from ClickHouse
   MAX_TS=$(docker exec k2-clickhouse clickhouse-client -q \
     "SELECT MAX(exchange_timestamp) FROM bronze_trades_binance FORMAT TSV")

   MAX_SEQ=$(docker exec k2-clickhouse clickhouse-client -q \
     "SELECT MAX(sequence_number) FROM bronze_trades_binance FORMAT TSV")

   echo "Max Timestamp: $MAX_TS"
   echo "Max Sequence: $MAX_SEQ"

   # Update watermark table
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "INSERT INTO offload_watermarks (table_name, max_timestamp, sequence_number)
      VALUES ('bronze_trades_binance', '$MAX_TS', $MAX_SEQ)"

   # Verify
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT * FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      ORDER BY created_at DESC LIMIT 3"
   ```

5. **Trigger immediate offload cycle:**
   ```bash
   # Force scheduler to run next cycle immediately
   # Method: Send SIGUSR1 (if implemented) or restart scheduler

   sudo systemctl restart iceberg-offload-scheduler

   # Watch logs for immediate cycle
   tail -f /tmp/iceberg-offload-scheduler.log
   ```

---

### Scenario 3: Data Volume Spike

**Symptoms:** Lag increasing due to high data volume, cycles taking longer

**Root Cause:** Temporary data surge (e.g., market volatility, exchange reconnect)

**Steps:**

1. **Quantify data spike:**
   ```bash
   # Check hourly data volume trend
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT
        toStartOfHour(exchange_timestamp) AS hour,
        COUNT(*) AS rows
      FROM bronze_trades_binance
      WHERE exchange_timestamp > now() - INTERVAL 6 HOUR
      GROUP BY hour
      ORDER BY hour DESC"

   # Normal: <5M rows/hour
   # Spike: >10M rows/hour
   ```

2. **Check if spike is temporary:**
   ```bash
   # Check last 15 minutes vs last hour
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT
        'last_15min' AS period,
        COUNT(*) AS rows
      FROM bronze_trades_binance
      WHERE exchange_timestamp > now() - INTERVAL 15 MINUTE
      UNION ALL
      SELECT
        'last_hour',
        COUNT(*)
      FROM bronze_trades_binance
      WHERE exchange_timestamp > now() - INTERVAL 1 HOUR"

   # If last_15min << last_hour: Spike subsiding
   ```

3. **Monitor cycle duration:**
   ```bash
   # Check recent cycle durations
   grep "Duration:" /tmp/iceberg-offload-scheduler.log | tail -10

   # Expected: <30 seconds normally
   # If >5 minutes during spike: Normal (will catch up)
   ```

4. **Decision: Wait or intervene?**

   **If spike is subsiding AND lag <45 minutes:**
   - **Action:** Monitor, no intervention
   - **Rationale:** Scheduler will catch up naturally
   - **Monitor:** Check lag every 15 minutes

   **If spike sustained OR lag >45 minutes:**
   - **Action:** Manual parallel offload (advanced)
   - **See:** [Scenario 4: Manual Catch-Up Offload](#scenario-4-manual-catch-up-offload)

---

### Scenario 4: Manual Catch-Up Offload

**Symptoms:** Lag >45 minutes, scheduler unable to catch up in reasonable time

**Root Cause:** Sustained high volume or scheduler behind schedule

**WARNING:** This is an advanced procedure. Use only when lag is critical (>45 min).

**Steps:**

1. **Verify scheduler is still running:**
   ```bash
   systemctl status iceberg-offload-scheduler

   # Do NOT proceed if scheduler is down - fix scheduler first
   ```

2. **Calculate catch-up window:**
   ```bash
   # Get last watermark timestamp
   LAST_TS=$(docker exec k2-prefect-db psql -U prefect -d prefect -t -c \
     "SELECT max_timestamp FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      ORDER BY created_at DESC LIMIT 1")

   echo "Last offloaded: $LAST_TS"
   echo "Current time: $(date)"
   echo "Gap: ~45-60 minutes"
   ```

3. **Run manual offload for catch-up:**
   ```bash
   # Run offload manually (does NOT conflict with scheduler)
   # Scheduler's watermark logic ensures no duplicates

   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze

   # This will offload from last watermark to current time
   # Duration: 1-5 minutes depending on data volume
   ```

4. **Verify manual offload success:**
   ```bash
   # Check watermark updated
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "SELECT max_timestamp, created_at FROM offload_watermarks
      WHERE table_name = 'bronze_trades_binance'
      ORDER BY created_at DESC LIMIT 2"

   # Expected: New entry with recent max_timestamp
   ```

5. **Check lag reduced:**
   ```bash
   curl -s http://localhost:8000/metrics | grep offload_lag_minutes

   # Expected: Lag reduced to <15 minutes
   ```

6. **Let scheduler resume normal operation:**
   - Scheduler will continue from new watermark
   - No restart needed (watermark updated)

---

## Prevention

### Proactive Measures

1. **Set up lag monitoring (if not already):**
   ```bash
   # Create alert for lag >20 minutes (warning threshold)
   # Already configured in Prometheus alert rules
   # Verify alerts enabled: http://localhost:9090/alerts
   ```

2. **Daily lag check (automated):**
   ```bash
   # Add to cron (run every 4 hours)
   cat > /usr/local/bin/check-offload-lag.sh <<'EOF'
   #!/bin/bash
   LAG=$(curl -s http://localhost:8000/metrics | grep offload_lag_minutes | awk '{print $2}')
   if (( $(echo "$LAG > 20" | bc -l) )); then
     echo "WARNING: Offload lag is $LAG minutes (threshold: 20)"
     # Could send Slack notification here
   fi
   EOF

   chmod +x /usr/local/bin/check-offload-lag.sh

   # Add to crontab
   (crontab -l 2>/dev/null; echo "0 */4 * * * /usr/local/bin/check-offload-lag.sh") | crontab -
   ```

3. **Capacity planning:**
   - Monitor data volume trends weekly
   - If sustained >7M rows/hour: Consider parallel offload (Phase 7)
   - If frequent spikes >10M rows/hour: Increase schedule frequency (10-min intervals)

4. **Watermark cleanup (prevent lag calculation errors):**
   ```bash
   # Clean old watermark entries weekly
   docker exec k2-prefect-db psql -U prefect -d prefect -c \
     "DELETE FROM offload_watermarks
      WHERE created_at < NOW() - INTERVAL '7 days'"
   ```

---

## Related Monitoring

### Dashboards
- **Primary:** [Iceberg Offload Pipeline](http://localhost:3000/d/iceberg-offload)
  - Panel 1: Offload Lag (Gauge) - Real-time lag per table
  - Panel 4: Offload Rate - Check if data flow stopped

### Metrics
- `offload_lag_minutes{table}` - Current lag per table
- `offload_last_success_timestamp` - Unix timestamp of last success
- `offload_cycle_duration_seconds` - Check if cycles taking too long

### Alerts
- **Critical:** `IcebergOffloadLagCritical` (>30 min)
- **Warning:** `IcebergOffloadLagElevated` (20-30 min)
- **Related:** `IcebergOffloadCycleTooSlow` (may cause lag)

### Logs
- **Scheduler:** `/tmp/iceberg-offload-scheduler.log`
- **Watermark DB:** `docker logs k2-prefect-db`

---

## Post-Incident

### After Resolution

1. **Verify lag returned to normal:**
   ```bash
   # Check lag for next 3 cycles (45 minutes)
   watch -n 300 'curl -s http://localhost:8000/metrics | grep offload_lag_minutes'

   # Expected: Lag <15 minutes (target)
   ```

2. **Root cause analysis:**
   - Was this a scheduler issue or data spike?
   - Document findings in incident log
   - Update capacity planning if needed

3. **Check for data gaps:**
   ```bash
   # Verify no data gaps in cold tier
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT
        toStartOfHour(exchange_timestamp) AS hour,
        COUNT(*) AS ch_rows
      FROM bronze_trades_binance
      WHERE exchange_timestamp > now() - INTERVAL 6 HOUR
      GROUP BY hour
      ORDER BY hour"

   # Compare with Iceberg (if accessible)
   # Expect similar row counts per hour
   ```

### Escalation

**Escalate to Engineering Lead if:**
- Lag persists >1 hour despite interventions
- Manual catch-up offload fails repeatedly
- Root cause indicates architectural limitation
- Data loss suspected

**Contact:** Platform Engineering Team

---

## Quick Reference

### Fastest Resolution Path

```bash
# 1. Check current lag (5 seconds)
curl -s http://localhost:8000/metrics | grep offload_lag_minutes

# 2. Check recent cycles (10 seconds)
grep "COMPLETED" /tmp/iceberg-offload-scheduler.log | tail -5

# 3. If scheduler hung, restart (30 seconds)
sudo systemctl restart iceberg-offload-scheduler

# 4. If catch-up needed, run manual offload (5 minutes)
docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
  --source-table bronze_trades_binance \
  --target-table cold.bronze_trades_binance \
  --timestamp-col exchange_timestamp \
  --sequence-col sequence_number \
  --layer bronze

# 5. Verify recovery (15 minutes)
watch -n 60 'curl -s http://localhost:8000/metrics | grep offload_lag_minutes'
```

**Total MTTR:** 15-30 minutes (standard), up to 60 minutes (catch-up required)

---

## Lag Severity Matrix

| Lag (minutes) | Severity | Action Required | Timeline |
|---------------|----------|-----------------|----------|
| **0-15** | ✅ Normal | None | Target state |
| **15-20** | 🟡 Elevated | Monitor | Check in 15 min |
| **20-30** | 🟡 Warning | Investigate | Business hours |
| **30-45** | 🔴 Critical | Immediate action | <15 minutes |
| **45-60** | 🔴 Severe | Manual catch-up | <30 minutes |
| **>60** | 🔴 Emergency | Escalate + catch-up | <1 hour |

---

**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering
**Version:** 1.0
**Related Runbooks:**
- [iceberg-offload-failure.md](iceberg-offload-failure.md) - Consecutive failures
- [iceberg-offload-performance.md](iceberg-offload-performance.md) - Slow cycles
- [iceberg-scheduler-recovery.md](iceberg-scheduler-recovery.md) - Scheduler down
