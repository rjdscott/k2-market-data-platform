# Runbook: Iceberg Scheduler Recovery

**Severity:** 🔴 Critical
**Alert:** `IcebergOffloadSchedulerDown`
**Response Time:** Immediate (<10 minutes)
**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering

---

## Summary

This runbook covers recovery procedures when the Iceberg offload scheduler process crashes, stops, or becomes unreachable. The scheduler is the core orchestrator for cold tier offloads - when down, no offloads occur and lag accumulates.

**Alert Trigger:** `up{job="iceberg-scheduler"} == 0` (Prometheus cannot scrape metrics)

**Scheduler Role:**
- Orchestrates offload cycles every 15 minutes
- Manages watermark progression
- Exports Prometheus metrics on port 8000
- Runs as systemd service: `iceberg-offload-scheduler.service`

---

## Symptoms

### What You'll See

- **Prometheus Alert:** `IcebergOffloadSchedulerDown` firing
- **Grafana Dashboard:** All panels empty ("No data")
- **Metrics Endpoint:** `curl http://localhost:8000/metrics` fails
- **Systemd:** `systemctl status iceberg-offload-scheduler` shows "inactive" or "failed"
- **Impact:** No offloads occurring, lag increasing

### User Impact

- **Immediate:** Cold tier stops updating
- **Extended (>30 min):** SLO violation, stale cold tier data
- **Extended (>24h):** Risk of data loss if ClickHouse TTL triggers
- **Severity:** Critical - complete pipeline outage

---

## Diagnosis

### Step 1: Verify Scheduler Status

```bash
# Check systemd service
systemctl status iceberg-offload-scheduler

# Expected statuses:
# - "active (running)" = Healthy
# - "inactive (dead)" = Not started
# - "failed" = Crashed

# If active but alert firing: Check metrics endpoint
curl -s http://localhost:8000/metrics | head -20

# If curl fails: Scheduler running but metrics server crashed
```

### Step 2: Check Scheduler Process

```bash
# Find scheduler process
ps aux | grep scheduler.py | grep -v grep

# Expected: Process running as root or service user

# If no process: Scheduler not running
# If process exists but metrics fail: Metrics server issue
```

### Step 3: Check Recent Logs

```bash
# View last 100 log lines
tail -100 /tmp/iceberg-offload-scheduler.log

# Check for errors
grep -E "ERROR|CRITICAL|Traceback" /tmp/iceberg-offload-scheduler.log | tail -20

# Check last activity
tail -20 /tmp/iceberg-offload-scheduler.log | grep -E "CYCLE|COMPLETED"
```

**Common error patterns:**
- Python exception/traceback → Code error
- "Connection refused" → Database/ClickHouse unreachable
- "Port 8000 already in use" → Metrics server conflict
- "Permission denied" → File/directory permissions
- Silent (no recent logs) → Hung or deadlocked

### Step 4: Check System Resources

```bash
# Check disk space (logs can fill disk)
df -h /tmp

# Check if log file is huge
ls -lh /tmp/iceberg-offload-scheduler.log

# Check memory
free -h

# Check for resource exhaustion
dmesg | tail -20 | grep -i "out of memory"
```

---

## Resolution

### Scenario 1: Scheduler Not Started

**Symptoms:** `systemctl status` shows "inactive (dead)", no recent logs

**Steps:**

1. **Start scheduler:**
   ```bash
   sudo systemctl start iceberg-offload-scheduler

   # Wait 5 seconds for startup
   sleep 5

   # Verify status
   systemctl status iceberg-offload-scheduler
   ```

2. **Check startup logs:**
   ```bash
   # View startup messages
   journalctl -u iceberg-offload-scheduler -n 50 --no-pager

   # Check scheduler log
   tail -30 /tmp/iceberg-offload-scheduler.log
   ```

3. **Verify metrics server started:**
   ```bash
   # Test metrics endpoint
   curl -s http://localhost:8000/metrics | grep offload_info

   # Expected: Metrics with version="1.0.0"
   ```

4. **Wait for first cycle (up to 15 minutes):**
   ```bash
   # Watch logs for cycle start
   tail -f /tmp/iceberg-offload-scheduler.log

   # Expected within 15 min: "CYCLE STARTED" → "COMPLETED"
   ```

**If start fails:** Check journal logs for specific error → See [Scenario 5: Persistent Start Failures](#scenario-5-persistent-start-failures)

---

### Scenario 2: Scheduler Crashed

**Symptoms:** `systemctl status` shows "failed", traceback in logs

**Steps:**

1. **Identify crash reason:**
   ```bash
   # View systemd failure logs
   journalctl -u iceberg-offload-scheduler -n 100 --no-pager | grep -A 10 "failed"

   # Check Python traceback
   tail -200 /tmp/iceberg-offload-scheduler.log | grep -A 20 "Traceback"
   ```

2. **Common crash causes:**

   **Cause 2a: Python exception (code bug)**
   ```bash
   # Check for specific exception
   grep "Exception" /tmp/iceberg-offload-scheduler.log | tail -10

   # If repeatable crash: Requires code fix (escalate)
   # If one-time: Restart and monitor
   ```

   **Cause 2b: Dependency failure (DB, Docker)**
   ```bash
   # Check if ClickHouse/Spark/PostgreSQL are running
   docker ps | grep -E "clickhouse|spark|prefect-db"

   # If service down: Start it first
   docker start k2-clickhouse k2-spark-iceberg k2-prefect-db

   # Wait for services to be ready (30 seconds)
   sleep 30
   ```

   **Cause 2c: Out of memory (OOM)**
   ```bash
   # Check for OOM kill
   dmesg | grep -i "killed process" | grep scheduler

   # If OOM: Increase system memory or optimize scheduler
   # Short-term: Clear caches and restart
   sync; echo 3 > /proc/sys/vm/drop_caches
   ```

3. **Clear fault state and restart:**
   ```bash
   # Reset failed state
   sudo systemctl reset-failed iceberg-offload-scheduler

   # Restart service
   sudo systemctl restart iceberg-offload-scheduler

   # Verify startup
   systemctl status iceberg-offload-scheduler
   ```

4. **Monitor for repeated crashes:**
   ```bash
   # Watch for 30 minutes
   watch -n 60 'systemctl is-active iceberg-offload-scheduler'

   # If crashes again: Escalate to engineering
   ```

---

### Scenario 3: Scheduler Hung (Not Responsive)

**Symptoms:** Process running but no recent activity, cycles not completing

**Steps:**

1. **Check process state:**
   ```bash
   # Get scheduler PID
   PID=$(pgrep -f scheduler.py)

   # Check process state
   ps -o pid,stat,etime,command -p $PID

   # States:
   # S = Sleeping (normal between cycles)
   # R = Running (normal during cycle)
   # D = Uninterruptible sleep (hung, bad)
   # Z = Zombie (crashed but not reaped)
   ```

2. **Check if cycle is stuck:**
   ```bash
   # Look for "CYCLE STARTED" without "COMPLETED"
   tail -100 /tmp/iceberg-offload-scheduler.log | \
     grep -E "CYCLE STARTED|COMPLETED" | tail -5

   # If last entry is "STARTED" >15 min ago: Hung
   ```

3. **Check for deadlock indicators:**
   ```bash
   # Check for stuck subprocess (Docker exec)
   ps aux | grep "docker exec k2-spark-iceberg"

   # If found: Spark job may be hung
   # Check Spark container
   docker exec k2-spark-iceberg ps aux | grep python3
   ```

4. **Graceful restart attempt:**
   ```bash
   # Send SIGTERM (graceful shutdown)
   sudo systemctl stop iceberg-offload-scheduler

   # Wait up to 30 seconds for graceful shutdown
   for i in {1..30}; do
     if ! pgrep -f scheduler.py > /dev/null; then
       echo "Graceful shutdown successful"
       break
     fi
     sleep 1
   done
   ```

5. **Force kill if graceful fails:**
   ```bash
   # If still running after 30 seconds:
   if pgrep -f scheduler.py > /dev/null; then
     echo "Forcing kill..."
     sudo systemctl kill -s SIGKILL iceberg-offload-scheduler
     sleep 2
   fi

   # Start fresh
   sudo systemctl start iceberg-offload-scheduler
   ```

6. **Verify recovery:**
   ```bash
   # Check process started
   pgrep -f scheduler.py

   # Watch logs for activity
   tail -f /tmp/iceberg-offload-scheduler.log
   ```

---

### Scenario 4: Metrics Server Failure

**Symptoms:** Scheduler process running, cycles completing, but metrics endpoint unreachable

**Steps:**

1. **Test metrics endpoint:**
   ```bash
   # Try to connect
   curl -v http://localhost:8000/metrics

   # Error patterns:
   # "Connection refused" = Server not listening
   # "Connection timed out" = Firewall or port conflict
   # HTTP 500 = Server running but erroring
   ```

2. **Check port 8000 usage:**
   ```bash
   # See what's using port 8000
   lsof -i :8000

   # Or with netstat
   netstat -tlnp | grep 8000
   ```

3. **Common metrics server issues:**

   **Issue 4a: Port conflict**
   ```bash
   # Another process using port 8000
   lsof -i :8000

   # If conflict: Kill other process or change scheduler port
   # To change port: Edit scheduler.py, change start_metrics_server(port=8000) to 8001
   # Then update Prometheus scrape config
   ```

   **Issue 4b: Prometheus client module missing**
   ```bash
   # Check if module installed
   docker exec k2-spark-iceberg python3 -c "import prometheus_client"

   # If fails: Install module
   # (Should be installed in Spark container already)
   ```

   **Issue 4c: Metrics server crashed but scheduler continues**
   ```bash
   # Check scheduler logs for metrics errors
   grep -i "metrics" /tmp/iceberg-offload-scheduler.log | tail -20

   # If "metrics disabled" or import error: Metrics module issue
   ```

4. **Restart scheduler to reinitialize metrics:**
   ```bash
   sudo systemctl restart iceberg-offload-scheduler

   # Wait 10 seconds
   sleep 10

   # Test metrics endpoint
   curl http://localhost:8000/metrics | head -20
   ```

---

### Scenario 5: Persistent Start Failures

**Symptoms:** Scheduler fails to start repeatedly, systemctl start fails

**Steps:**

1. **Check detailed failure reason:**
   ```bash
   # View full systemd logs
   journalctl -u iceberg-offload-scheduler -n 200 --no-pager

   # Check for specific errors
   journalctl -u iceberg-offload-scheduler -n 200 --no-pager | grep -i error
   ```

2. **Common start failure causes:**

   **Cause 5a: Python interpreter not found**
   ```bash
   # Check Python path in service file
   cat /etc/systemd/system/iceberg-offload-scheduler.service | grep ExecStart

   # Verify Python exists
   which python3
   /usr/bin/python3 --version

   # If path mismatch: Update service file
   ```

   **Cause 5b: Script not found or not executable**
   ```bash
   # Check script exists
   ls -la /home/rjdscott/Documents/projects/k2-market-data-platform/docker/offload/scheduler.py

   # Check executable permission
   # Note: Not needed for Python scripts, but check anyway
   chmod +x /home/rjdscott/Documents/projects/k2-market-data-platform/docker/offload/scheduler.py
   ```

   **Cause 5c: Missing dependencies**
   ```bash
   # Try to run scheduler manually
   cd /home/rjdscott/Documents/projects/k2-market-data-platform/docker/offload
   python3 scheduler.py

   # If ImportError: Install missing modules
   pip install <missing-module>
   ```

   **Cause 5d: Permission denied (log file)**
   ```bash
   # Check log file permissions
   ls -la /tmp/iceberg-offload-scheduler.log

   # If permission issue:
   sudo chown $(whoami) /tmp/iceberg-offload-scheduler.log

   # Or delete and let scheduler recreate
   sudo rm /tmp/iceberg-offload-scheduler.log
   ```

3. **Manual start for debugging:**
   ```bash
   # Run scheduler in foreground (terminal)
   cd /home/rjdscott/Documents/projects/k2-market-data-platform/docker/offload
   python3 scheduler.py

   # Watch for errors (Ctrl+C to stop)
   # If works manually but not via systemd: Service file issue
   ```

4. **Fix service file if needed:**
   ```bash
   # View current service file
   cat /etc/systemd/system/iceberg-offload-scheduler.service

   # Edit if needed
   sudo vi /etc/systemd/system/iceberg-offload-scheduler.service

   # Reload systemd after changes
   sudo systemctl daemon-reload

   # Try start again
   sudo systemctl start iceberg-offload-scheduler
   ```

---

## Prevention

### Proactive Measures

1. **Enable auto-restart on failure:**

   Already configured in service file:
   ```ini
   [Service]
   Restart=on-failure
   RestartSec=30s
   ```

   This automatically restarts scheduler if it crashes.

2. **Monitor scheduler health:**
   ```bash
   # Create health check script
   cat > /usr/local/bin/scheduler-health-check.sh <<'EOF'
   #!/bin/bash
   if ! systemctl is-active iceberg-offload-scheduler &> /dev/null; then
     echo "WARNING: Scheduler is not running"
     sudo systemctl start iceberg-offload-scheduler
   fi
   EOF

   chmod +x /usr/local/bin/scheduler-health-check.sh

   # Add to cron (check every 5 minutes)
   (crontab -l; echo "*/5 * * * * /usr/local/bin/scheduler-health-check.sh") | crontab -
   ```

3. **Log rotation (prevent disk full):**
   ```bash
   # Create logrotate config
   sudo tee /etc/logrotate.d/iceberg-scheduler <<'EOF'
   /tmp/iceberg-offload-scheduler.log {
       daily
       rotate 7
       compress
       delaycompress
       missingok
       notifempty
       create 0644 root root
       postrotate
           systemctl reload iceberg-offload-scheduler > /dev/null 2>&1 || true
       endscript
   }
   EOF
   ```

4. **Set up Prometheus alerting (already configured):**
   - Alert: `IcebergOffloadSchedulerDown`
   - Threshold: 2 minutes of unreachability
   - Action: Immediate response

5. **Weekly scheduler validation:**
   ```bash
   # Check scheduler has been running continuously
   cat > /usr/local/bin/scheduler-uptime-check.sh <<'EOF'
   #!/bin/bash
   UPTIME=$(systemctl show iceberg-offload-scheduler -p ActiveEnterTimestamp --value)
   echo "Scheduler uptime since: $UPTIME"

   RESTARTS=$(journalctl -u iceberg-offload-scheduler --since "7 days ago" | grep -c "Started")
   echo "Restarts in last 7 days: $RESTARTS"

   if [ $RESTARTS -gt 5 ]; then
     echo "WARNING: Scheduler restarted $RESTARTS times (threshold: 5)"
   fi
   EOF

   chmod +x /usr/local/bin/scheduler-uptime-check.sh

   # Add to cron (weekly Monday 9am)
   (crontab -l; echo "0 9 * * 1 /usr/local/bin/scheduler-uptime-check.sh | mail -s 'Scheduler Uptime Report' platform-team@company.com") | crontab -
   ```

---

## Related Monitoring

### Dashboards
- **Primary:** [Iceberg Offload Pipeline](http://localhost:3000/d/iceberg-offload)
  - All panels depend on scheduler being up

### Metrics
- `up{job="iceberg-scheduler"}` - 1 if scheduler reachable, 0 if down
- `offload_info` - Metadata metric (includes scheduler start time)

### Alerts
- **This Alert:** `IcebergOffloadSchedulerDown` (Critical)
- **Related:** All other offload alerts will eventually fire if scheduler down

### Logs
- **Scheduler:** `/tmp/iceberg-offload-scheduler.log`
- **Systemd:** `journalctl -u iceberg-offload-scheduler`

---

## Post-Incident

### After Resolution

1. **Verify scheduler stability:**
   ```bash
   # Watch for 1 hour (4 cycles)
   for i in {1..4}; do
     sleep 900  # 15 minutes
     if systemctl is-active iceberg-offload-scheduler &> /dev/null; then
       echo "$(date): Scheduler running ✓"
     else
       echo "$(date): Scheduler DOWN ✗"
     fi
   done
   ```

2. **Verify metrics exporting:**
   ```bash
   # Check metrics endpoint accessible
   curl -s http://localhost:8000/metrics | grep offload_info

   # Check Prometheus scraping
   curl -s 'http://localhost:9090/api/v1/query?query=up{job="iceberg-scheduler"}' | \
     jq '.data.result[0].value[1]'

   # Expected: "1" (up)
   ```

3. **Check for data gaps (if downtime >30 min):**
   ```bash
   # Check offload lag
   curl -s http://localhost:8000/metrics | grep offload_lag_minutes

   # If lag >30 min: See iceberg-offload-lag.md for catch-up procedures
   ```

4. **Root cause analysis:**
   - Why did scheduler stop/crash?
   - Was it preventable?
   - Document findings in incident log
   - Update this runbook if new failure mode

### Escalation

**Escalate to Engineering Lead if:**
- Scheduler crashes repeatedly (>3 times in 24 hours)
- Root cause is code bug requiring fix
- Persistent start failures despite troubleshooting
- Resource exhaustion requires infrastructure changes

**Contact:** Platform Engineering Team

---

## Quick Reference

### Fastest Recovery Path

```bash
# 1. Check status (5 seconds)
systemctl status iceberg-offload-scheduler

# 2. If inactive, start (10 seconds)
sudo systemctl start iceberg-offload-scheduler

# 3. If failed, reset and restart (15 seconds)
sudo systemctl reset-failed iceberg-offload-scheduler
sudo systemctl restart iceberg-offload-scheduler

# 4. If hung, force kill and restart (30 seconds)
sudo systemctl kill -s SIGKILL iceberg-offload-scheduler
sudo systemctl start iceberg-offload-scheduler

# 5. Verify recovery (30 seconds)
systemctl status iceberg-offload-scheduler
curl http://localhost:8000/metrics | head -10
tail -f /tmp/iceberg-offload-scheduler.log
```

**Total MTTR:** 2-5 minutes (most scenarios)

---

## Scheduler State Diagram

```
                    START
                      ↓
            [systemctl start]
                      ↓
             ┌────────────────┐
             │   STARTING     │
             │  (Loading...)  │
             └────────┬───────┘
                      │
                      ↓
             ┌────────────────┐
             │    ACTIVE      │←─────────┐
             │  (Running)     │          │
             └────┬───────┬───┘          │
                  │       │              │
        Success   │       │   Cycle      │
                  │       │   Complete   │
                  │       └──────────────┘
                  │
                  │   Crash/Stop
                  ↓
             ┌────────────────┐
             │    FAILED      │
             │ (Needs restart)│
             └────────────────┘
```

---

**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering
**Version:** 1.0
**Related Runbooks:**
- [iceberg-offload-failure.md](iceberg-offload-failure.md) - Offload failures (often after scheduler recovery)
- [iceberg-offload-lag.md](iceberg-offload-lag.md) - High lag (caused by scheduler downtime)
- [iceberg-offload-watermark-recovery.md](iceberg-offload-watermark-recovery.md) - Watermark issues
