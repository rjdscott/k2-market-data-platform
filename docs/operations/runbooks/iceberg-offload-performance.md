# Runbook: Iceberg Offload Performance Degradation

**Severity:** 🔴 Critical (>10 min) | 🟡 Warning (5-10 min)
**Alerts:** `IcebergOffloadCycleTooSlow`, `IcebergOffloadCycleSlow`
**Response Time:** Critical: Immediate | Warning: Business hours
**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering

---

## Summary

This runbook covers resolution procedures when offload cycle duration exceeds acceptable thresholds. Slow cycles risk overlapping with the next scheduled cycle (15-minute intervals), causing resource contention and potentially escalating to lag issues.

**Alert Triggers:**
- **Critical:** `offload_cycle_duration_seconds > 600` (>10 minutes, 2/3 of schedule)
- **Warning:** `offload_cycle_duration_seconds > 300 and <= 600` (5-10 minutes)

**Baseline Performance:**
- **Target:** <30 seconds per cycle (2 tables, typical volume)
- **Acceptable:** <5 minutes per cycle
- **Critical:** >10 minutes per cycle (overlap risk)

---

## Symptoms

### What You'll See

**Critical (>10 minutes):**
- **Prometheus Alert:** `IcebergOffloadCycleTooSlow` firing
- **Grafana Dashboard:** Cycle Duration panel showing RED bars (>10 min)
- **Scheduler Logs:** "Duration: 600s+" in cycle completion messages
- **Impact:** Risk of cycle overlap, increasing lag

**Warning (5-10 minutes):**
- **Prometheus Alert:** `IcebergOffloadCycleSlow` firing
- **Grafana Dashboard:** Cycle Duration panel showing YELLOW bars (5-10 min)
- **Impact:** Performance degraded, trending toward critical

### User Impact

- **Immediate:** Increased offload lag (cold tier updates delayed)
- **Extended:** Cycle overlap → resource contention → failures
- **Severity:** High (critical), Medium (warning)

---

## Diagnosis

### Step 1: Measure Current Performance

```bash
# Check recent cycle durations
grep "Duration:" /tmp/iceberg-offload-scheduler.log | tail -10

# Calculate average duration (last 10 cycles)
grep "Duration:" /tmp/iceberg-offload-scheduler.log | tail -10 | \
  awk '{print $NF}' | sed 's/s//' | \
  awk '{sum+=$1; n++} END {print "Average:", sum/n, "seconds"}'

# Check current cycle duration metric
curl -s http://localhost:8000/metrics | grep offload_cycle_duration_seconds_sum
```

**Expected:** <30 seconds average, <60 seconds p95

**If >5 minutes:** Performance degraded, continue diagnosis

### Step 2: Identify Bottleneck (Per-Table Duration)

```bash
# Check individual table durations from recent cycle
tail -100 /tmp/iceberg-offload-scheduler.log | \
  grep "Offload completed" | \
  tail -5

# Expected output format:
# ✓ Offload completed: bronze_trades_binance (3,850,000 rows in 14.2s)
# ✓ Offload completed: bronze_trades_kraken (19,600 rows in 6.8s)

# Identify slow table(s)
```

**Common Patterns:**
- **One slow table:** Table-specific issue (data volume, indexes)
- **All tables slow:** System-wide issue (network, Spark, ClickHouse)

### Step 3: Check Data Volume

```bash
# Check rows being offloaded per cycle
grep "Rows offloaded:" /tmp/iceberg-offload-scheduler.log | tail -10

# Check ClickHouse data volume trend (last 6 hours)
docker exec k2-clickhouse clickhouse-client -q \
  "SELECT
     toStartOfHour(exchange_timestamp) AS hour,
     COUNT(*) AS rows,
     formatReadableSize(SUM(length(toString(trade_id)) + length(toString(price)))) AS size
   FROM bronze_trades_binance
   WHERE exchange_timestamp > now() - INTERVAL 6 HOUR
   GROUP BY hour
   ORDER BY hour DESC"
```

**Expected:**
- **Rows:** <5M per hour per table (steady state)
- **Size:** <500MB per hour per table

**If >10M rows/hour:** Data volume spike → [Scenario 1: Data Volume Spike](#scenario-1-data-volume-spike)

### Step 4: Check System Resources

```bash
# Check ClickHouse resource usage
docker stats k2-clickhouse --no-stream

# Check Spark resource usage
docker stats k2-spark-iceberg --no-stream

# Check host machine resources
free -h
iostat -x 1 3
```

**Expected:**
- **ClickHouse:** <50% CPU, <8GB memory
- **Spark:** <4GB memory, <2 CPU
- **Host:** <80% CPU, <80% memory

**If resources exhausted:** [Scenario 2: Resource Exhaustion](#scenario-2-resource-exhaustion)

### Step 5: Check Network Latency

```bash
# Ping test (Spark to ClickHouse)
docker exec k2-spark-iceberg ping -c 10 k2-clickhouse

# Expected: <1ms latency (same host)

# Check for packet loss
docker exec k2-spark-iceberg ping -c 100 k2-clickhouse | tail -3
```

**If latency >5ms or packet loss >0%:** [Scenario 3: Network Issues](#scenario-3-network-issues)

### Step 6: Check ClickHouse Query Performance

```bash
# Check slow queries
docker exec k2-clickhouse clickhouse-client -q \
  "SELECT
     query,
     elapsed,
     read_rows,
     formatReadableSize(memory_usage) AS memory
   FROM system.processes
   WHERE query LIKE '%bronze_trades%'
   ORDER BY elapsed DESC"

# Check query log (recent slow queries)
docker exec k2-clickhouse clickhouse-client -q \
  "SELECT
     query_duration_ms,
     query,
     read_rows
   FROM system.query_log
   WHERE type = 'QueryFinish'
     AND query LIKE '%SELECT%bronze_trades%'
   ORDER BY query_start_time DESC
   LIMIT 10"
```

**Expected:** Query duration <5 seconds for typical offload queries

**If queries >30 seconds:** [Scenario 4: ClickHouse Slow Queries](#scenario-4-clickhouse-slow-queries)

---

## Resolution

### Scenario 1: Data Volume Spike

**Symptoms:** Row counts 2-5x normal, cycle duration proportional to volume

**Root Cause:** Temporary data surge (market event, exchange reconnect, backfill)

**Steps:**

1. **Confirm data spike is temporary:**
   ```bash
   # Check trend (last 6 hours)
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT
        toStartOfHour(exchange_timestamp) AS hour,
        COUNT(*) AS rows
      FROM bronze_trades_binance
      WHERE exchange_timestamp > now() - INTERVAL 6 HOUR
      GROUP BY hour
      ORDER BY hour DESC"

   # If recent hours lower: Spike subsiding
   # If sustained high: Permanent volume increase
   ```

2. **Decision: Wait or optimize?**

   **If spike is temporary (1-2 hours):**
   - **Action:** Monitor, no intervention
   - **Rationale:** Performance will return to normal naturally
   - **Monitor:** Cycle duration every 15 minutes

   **If spike is sustained (>3 hours):**
   - **Action:** Optimize for higher volume
   - **See:** [Long-Term Optimization](#long-term-optimization)

3. **Short-term mitigation (if critical):**

   **Option A: Increase cycle interval temporarily**
   ```bash
   # Edit scheduler.py
   # Change: SCHEDULE_INTERVAL_MINUTES = 15
   # To: SCHEDULE_INTERVAL_MINUTES = 20

   sudo systemctl restart iceberg-offload-scheduler
   ```

   **Option B: Manual throttling**
   ```bash
   # Stop scheduler temporarily
   sudo systemctl stop iceberg-offload-scheduler

   # Run manual offload every 30 minutes until spike subsides
   # (Prevents overlap, allows full completion)
   ```

---

### Scenario 2: Resource Exhaustion

**Symptoms:** High CPU/memory usage, system slowdown, OOM errors

**Root Cause:** Insufficient resources for current data volume or competing processes

**Steps:**

1. **Identify resource bottleneck:**
   ```bash
   # Check what's consuming resources
   docker stats --no-stream

   # Check host processes
   ps aux --sort=-%mem | head -10
   ps aux --sort=-%cpu | head -10
   ```

2. **Common resource issues:**

   **Issue 2a: Spark out of memory**
   ```bash
   # Check Spark logs for OOM
   docker logs k2-spark-iceberg --tail=100 | grep -i "OutOfMemoryError"

   # If OOM found:
   # Option 1: Increase Spark memory (requires docker-compose change)
   # Option 2: Restart Spark to clear memory
   docker restart k2-spark-iceberg

   # Wait 30 seconds for Spark to be ready
   sleep 30
   ```

   **Issue 2b: ClickHouse memory pressure**
   ```bash
   # Check ClickHouse memory
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT
        formatReadableSize(total_bytes) AS total,
        formatReadableSize(free_bytes) AS free
      FROM system.asynchronous_metrics
      WHERE metric LIKE '%memory%'"

   # If <1GB free: Restart ClickHouse
   docker restart k2-clickhouse

   # Wait for ClickHouse to be ready (10 seconds)
   sleep 10
   docker exec k2-clickhouse clickhouse-client -q "SELECT 1"
   ```

   **Issue 2c: Competing processes**
   ```bash
   # Check for other heavy processes
   ps aux | grep -E "python|java|spark" | grep -v grep

   # If other Spark jobs running: Wait for completion or stop if safe
   ```

3. **Monitor resource recovery:**
   ```bash
   # Watch resources for 5 minutes
   watch -n 10 'docker stats --no-stream | grep -E "clickhouse|spark"'

   # Expected: Memory/CPU usage returns to normal (<50%)
   ```

---

### Scenario 3: Network Issues

**Symptoms:** High latency (>5ms), packet loss, connection timeouts

**Root Cause:** Docker network issues, host network saturation, bridge problems

**Steps:**

1. **Diagnose network issue:**
   ```bash
   # Detailed ping test
   docker exec k2-spark-iceberg ping -c 100 k2-clickhouse | tail -5

   # Check Docker network status
   docker network inspect k2-network

   # Check bridge interface
   ip addr show docker0
   ```

2. **Common network issues:**

   **Issue 3a: Docker network corruption**
   ```bash
   # Reconnect containers to network
   docker network disconnect k2-network k2-spark-iceberg
   docker network disconnect k2-network k2-clickhouse

   sleep 2

   docker network connect k2-network k2-spark-iceberg
   docker network connect k2-network k2-clickhouse

   # Verify connectivity restored
   docker exec k2-spark-iceberg ping -c 5 k2-clickhouse
   ```

   **Issue 3b: Host network saturation**
   ```bash
   # Check network usage
   iftop -i docker0 -t -s 10

   # Check for other network-heavy processes
   nethogs -t docker0

   # If saturation: Identify and throttle/stop competing processes
   ```

3. **Restart Docker networking (last resort):**
   ```bash
   # WARNING: This will briefly disconnect all containers
   sudo systemctl restart docker

   # Wait for Docker to restart
   sleep 30

   # Verify all containers running
   docker ps | grep k2-
   ```

---

### Scenario 4: ClickHouse Slow Queries

**Symptoms:** Queries taking >30 seconds, high disk I/O, query queue buildup

**Root Cause:** Missing indexes, fragmented tables, disk I/O bottleneck

**Steps:**

1. **Identify slow query pattern:**
   ```bash
   # Get slowest queries
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT
        query_duration_ms / 1000 AS duration_sec,
        read_rows,
        result_rows,
        substring(query, 1, 100) AS query_short
      FROM system.query_log
      WHERE type = 'QueryFinish'
        AND query LIKE '%bronze_trades%'
        AND query_duration_ms > 5000
      ORDER BY query_duration_ms DESC
      LIMIT 10"
   ```

2. **Check for table fragmentation:**
   ```bash
   # Check parts count (fragmentation indicator)
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT
        table,
        COUNT(*) AS parts_count,
        formatReadableSize(SUM(bytes)) AS total_size
      FROM system.parts
      WHERE database = 'k2'
        AND table LIKE 'bronze_trades%'
        AND active = 1
      GROUP BY table"

   # Expected: <100 parts per table
   # If >200 parts: Fragmented, needs optimization
   ```

3. **Optimize fragmented tables:**
   ```bash
   # Run OPTIMIZE TABLE (merges parts)
   docker exec k2-clickhouse clickhouse-client -q \
     "OPTIMIZE TABLE k2.bronze_trades_binance FINAL"

   docker exec k2-clickhouse clickhouse-client -q \
     "OPTIMIZE TABLE k2.bronze_trades_kraken FINAL"

   # This can take 5-15 minutes depending on data volume
   # Monitor progress:
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT * FROM system.merges"
   ```

4. **Check query plan (if specific query slow):**
   ```bash
   # Explain query plan
   docker exec k2-clickhouse clickhouse-client -q \
     "EXPLAIN SELECT * FROM bronze_trades_binance
      WHERE exchange_timestamp > now() - INTERVAL 1 HOUR
      AND sequence_number > 1000000
      ORDER BY exchange_timestamp, sequence_number"

   # Look for "Read from table" steps - should use primary key
   ```

---

## Long-Term Optimization

### For Sustained High Volume

If data volume sustains >7M rows/hour for multiple days, consider these optimizations:

1. **Increase Spark Resources:**
   ```yaml
   # In docker-compose.v2.yml
   spark-iceberg:
     environment:
       - SPARK_EXECUTOR_MEMORY=4g  # Increase from 2g
       - SPARK_DRIVER_MEMORY=2g    # Increase from 1g
       - SPARK_EXECUTOR_CORES=2    # Increase from 1
   ```

2. **Parallel Table Offload:**
   - Currently: Sequential (binance → kraken)
   - Future: Parallel with `ThreadPoolExecutor`
   - Estimated improvement: 40-50% faster cycles
   - Implementation: Phase 7

3. **Optimize ClickHouse Queries:**
   ```sql
   -- Add index on exchange_timestamp (if not exists)
   ALTER TABLE bronze_trades_binance
   ADD INDEX idx_exchange_ts exchange_timestamp TYPE minmax GRANULARITY 4;

   -- Materialize index
   ALTER TABLE bronze_trades_binance MATERIALIZE INDEX idx_exchange_ts;
   ```

4. **Increase Schedule Interval:**
   - If cycles consistently >8 minutes: Change to 20-minute intervals
   - Edit `SCHEDULE_INTERVAL_MINUTES` in scheduler.py

5. **Partition Strategy:**
   - Current: Daily partitions in ClickHouse
   - Consider: Hourly partitions if volume >20M rows/day

---

## Prevention

### Proactive Measures

1. **Monitor performance trends:**
   ```bash
   # Weekly performance report
   cat > /usr/local/bin/offload-performance-report.sh <<'EOF'
   #!/bin/bash
   echo "=== Offload Performance Report (Last 7 Days) ==="

   echo "Average Cycle Duration:"
   grep "Duration:" /tmp/iceberg-offload-scheduler.log | \
     tail -672 | \  # 7 days * 96 cycles/day
     awk '{print $NF}' | sed 's/s//' | \
     awk '{sum+=$1; n++} END {printf "%.1f seconds\n", sum/n}'

   echo "P95 Cycle Duration:"
   grep "Duration:" /tmp/iceberg-offload-scheduler.log | \
     tail -672 | \
     awk '{print $NF}' | sed 's/s//' | sort -n | \
     awk '{arr[NR]=$1} END {print arr[int(NR*0.95)]}'

   echo "Cycles >5 minutes:"
   grep "Duration:" /tmp/iceberg-offload-scheduler.log | \
     tail -672 | \
     awk '{if ($NF+0 > 300) print}' | wc -l
   EOF

   chmod +x /usr/local/bin/offload-performance-report.sh

   # Run weekly
   (crontab -l; echo "0 8 * * 1 /usr/local/bin/offload-performance-report.sh | mail -s 'Offload Performance Report' platform-team@company.com") | crontab -
   ```

2. **Set up performance alerts:**
   - Already configured: `IcebergOffloadCycleSlow` (warning at 5 min)
   - Already configured: `IcebergOffloadCycleTooSlow` (critical at 10 min)

3. **Regular ClickHouse optimization:**
   ```bash
   # Monthly OPTIMIZE (add to cron)
   0 2 1 * * docker exec k2-clickhouse clickhouse-client -q "OPTIMIZE TABLE k2.bronze_trades_binance FINAL"
   ```

4. **Capacity planning:**
   - Review data volume trends monthly
   - Plan for 2x growth headroom
   - Upgrade resources proactively if sustained >5M rows/hour

---

## Related Monitoring

### Dashboards
- **Primary:** [Iceberg Offload Pipeline](http://localhost:3000/d/iceberg-offload)
  - Panel 6: Cycle Duration - Bar chart with threshold colors
  - Panel 5: Offload Duration - p50/p95/p99 percentiles
  - Panel 4: Offload Rate - Throughput trends

### Metrics
- `offload_cycle_duration_seconds` - Total cycle time
- `offload_duration_seconds` - Per-table offload time
- `offload_rows_per_second` - Throughput metric

### Alerts
- **Critical:** `IcebergOffloadCycleTooSlow` (>10 min)
- **Warning:** `IcebergOffloadCycleSlow` (5-10 min)
- **Related:** `IcebergOffloadThroughputLow` (may indicate performance issue)

### Logs
- **Scheduler:** `/tmp/iceberg-offload-scheduler.log`
- **Spark:** `docker logs k2-spark-iceberg`
- **ClickHouse:** `docker logs k2-clickhouse`

---

## Post-Incident

### After Resolution

1. **Verify performance restored:**
   ```bash
   # Monitor next 5 cycles (75 minutes)
   for i in {1..5}; do
     sleep 900  # 15 minutes
     tail -3 /tmp/iceberg-offload-scheduler.log | grep "Duration:"
   done

   # Expected: Duration <60 seconds
   ```

2. **Root cause analysis:**
   - Was this a data spike or system issue?
   - Is this likely to recur?
   - Document findings

3. **Update capacity plan:**
   - If new baseline volume: Plan resource upgrade
   - If temporary spike: Document pattern for future reference

### Escalation

**Escalate to Engineering Lead if:**
- Performance degradation persists >2 hours
- Root cause is architectural limitation
- Requires code changes or infrastructure upgrade
- Regular cycles now exceed 5 minutes (new baseline)

**Contact:** Platform Engineering Team

---

## Quick Reference

### Performance Troubleshooting Checklist

```bash
# 1. Check cycle duration (10 seconds)
grep "Duration:" /tmp/iceberg-offload-scheduler.log | tail -5

# 2. Check data volume (10 seconds)
docker exec k2-clickhouse clickhouse-client -q \
  "SELECT COUNT(*) FROM bronze_trades_binance WHERE exchange_timestamp > now() - INTERVAL 1 HOUR"

# 3. Check resources (10 seconds)
docker stats --no-stream | grep -E "clickhouse|spark"

# 4. Check network (10 seconds)
docker exec k2-spark-iceberg ping -c 10 k2-clickhouse

# 5. Optimize if needed (5 minutes)
docker exec k2-clickhouse clickhouse-client -q "OPTIMIZE TABLE k2.bronze_trades_binance FINAL"
```

**Total Diagnosis Time:** 5 minutes
**Total MTTR:** 15-30 minutes (most scenarios)

---

## Performance Targets

| Metric | Target | Acceptable | Critical |
|--------|--------|------------|----------|
| **Cycle Duration** | <30s | <5 min | >10 min |
| **Per-Table Duration** | <15s | <3 min | >5 min |
| **Throughput** | >100K rows/s | >10K rows/s | <10K rows/s |
| **p95 Duration** | <60s | <8 min | >12 min |

---

**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering
**Version:** 1.0
**Related Runbooks:**
- [iceberg-offload-failure.md](iceberg-offload-failure.md) - Offload failures
- [iceberg-offload-lag.md](iceberg-offload-lag.md) - High lag (often caused by slow cycles)
- [iceberg-scheduler-recovery.md](iceberg-scheduler-recovery.md) - Scheduler issues
