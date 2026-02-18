# Runbook: Iceberg Offload Orchestration Recovery

**Severity:** 🔴 Critical
**Alert:** `IcebergOffloadSchedulerDown`
**Response Time:** Immediate (<10 minutes)
**Last Updated:** 2026-02-18
**Maintained By:** Platform Engineering

> **v2 Migration Note:** The old Python `scheduler.py` + systemd service was replaced with
> Prefect 3.x in February 2026. All `systemctl` commands have been removed. See
> `PREFECT-3-MIGRATION-2026-02-14.md` for migration details.

---

## Summary

This runbook covers recovery when the Iceberg offload pipeline stops executing — either because
the Prefect worker container is down, the Prefect server is unreachable, or the deployment
schedule has stopped triggering flow runs.

**Alert Trigger:** `up{job="iceberg-scheduler"} == 0` (Prometheus cannot scrape metrics)

**Orchestration Stack:**
- **Prefect Server:** `k2-prefect-server` — API + UI (http://localhost:4200)
- **Prefect Worker:** `k2-prefect-worker` — executes flow runs on `iceberg-offload` pool
- **Deployment:** `iceberg-offload-main/iceberg-offload-15min` — 15-min cron schedule
- **Metrics:** port 8000 on the worker container (Prometheus scrape target)

---

## Symptoms

### What You'll See

- **Prometheus Alert:** `IcebergOffloadSchedulerDown` firing
- **Grafana Dashboard:** All panels empty ("No data")
- **Metrics Endpoint:** `curl http://localhost:8000/metrics` fails or returns stale data
- **Prefect UI:** http://localhost:4200 — no recent flow runs, worker shows offline
- **Impact:** No offloads occurring, cold tier lag increasing

### User Impact

- **Immediate:** Cold tier stops updating
- **Extended (>30 min):** SLO violation, stale cold tier data
- **Extended (>24h):** Risk of data loss if ClickHouse TTL triggers
- **Severity:** Critical — complete pipeline outage

---

## Diagnosis

### Step 1: Check Container Health

```bash
# Check all Prefect containers
docker ps --filter name=k2-prefect --format "table {{.Names}}\t{{.Status}}"

# Expected:
# k2-prefect-server    Up X hours (healthy)
# k2-prefect-worker    Up X hours
# k2-prefect-db        Up X hours (healthy)

# If any container is missing/exited: see scenarios below
```

### Step 2: Check Recent Flow Runs

```bash
# List last 5 flow runs (run from host)
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api prefect flow-run ls --limit 5"

# Expected: Recent runs within the last 15 minutes with state COMPLETED
# If no runs: Deployment not triggering
# If runs show FAILED/CRASHED: Flow execution errors (see iceberg-offload-failure.md)
```

Or check visually at **http://localhost:4200** → Flow Runs.

### Step 3: Check Metrics Endpoint

```bash
# Test Prometheus metrics scrape target
curl -s http://localhost:8000/metrics | head -20

# Expected: Lines like offload_info{...} 1
# If connection refused: Worker container down or metrics not started
```

### Step 4: Check Deployment Schedule

```bash
# Verify deployment exists and schedule is active
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api prefect deployment ls"

# Expected: iceberg-offload-main/iceberg-offload-15min with active schedule
```

---

## Resolution

### Scenario 1: Prefect Worker Down

**Symptoms:** `k2-prefect-worker` not in `docker ps` output, or shows "Exited"

**Steps:**

1. **Check why the worker stopped:**
   ```bash
   docker logs k2-prefect-worker --tail=100
   ```

2. **Restart the worker:**
   ```bash
   docker start k2-prefect-worker

   # Wait 10 seconds for startup
   sleep 10

   # Verify running
   docker ps --filter name=k2-prefect-worker
   ```

3. **Verify worker registered with server:**
   ```bash
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect worker ls"

   # Expected: Worker listed with status ONLINE
   ```

4. **Trigger an immediate flow run to confirm recovery:**
   ```bash
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api \
      prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'"
   ```

5. **Watch the run complete:**

   Check http://localhost:4200 → Flow Runs, or:
   ```bash
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect flow-run ls --limit 3"
   ```

---

### Scenario 2: Prefect Server Down

**Symptoms:** `k2-prefect-server` not running; worker logs show "connection refused to http://prefect-server:4200"

**Steps:**

1. **Restart the server (and its database if needed):**
   ```bash
   docker start k2-prefect-db
   sleep 5
   docker start k2-prefect-server
   sleep 15  # Server needs time to initialize

   # Verify server healthy
   docker ps --filter name=k2-prefect-server
   curl -s http://localhost:4200/api/health | jq .status
   # Expected: "healthy"
   ```

2. **Restart worker so it reconnects:**
   ```bash
   docker restart k2-prefect-worker
   sleep 10
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect worker ls"
   ```

3. **Verify the deployment schedule is still active:**
   ```bash
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect deployment ls"
   ```

   If the deployment is missing (server lost state), re-deploy:
   ```bash
   docker exec k2-prefect-worker bash -c \
     "cd /opt/prefect/flows && \
      PREFECT_API_URL=http://prefect-server:4200/api \
      python deploy_production.py"
   ```

---

### Scenario 3: Worker Running But No Flow Runs Executing

**Symptoms:** Worker container up, deployment exists, but no runs in the last 30+ minutes

**Steps:**

1. **Verify deployment schedule:**
   ```bash
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect deployment inspect \
      'iceberg-offload-main/iceberg-offload-15min'"

   # Check "schedules" section — should show active cron
   ```

2. **Trigger a manual run to verify the worker can execute:**
   ```bash
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api \
      prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'"
   ```

3. **If manual run works:** Schedule may have been paused. Resume it via Prefect UI:
   - Navigate to http://localhost:4200 → Deployments → `iceberg-offload-main/iceberg-offload-15min`
   - Check if schedule is paused; click Resume

4. **If manual run fails:** Flow execution error — see [iceberg-offload-failure.md](iceberg-offload-failure.md)

---

### Scenario 4: Metrics Endpoint Unreachable

**Symptoms:** Worker running and executing flows, but `curl http://localhost:8000/metrics` fails;
Prometheus alert fires even though data is flowing.

**Steps:**

1. **Verify worker container exposes port 8000:**
   ```bash
   docker inspect k2-prefect-worker | jq '.[0].NetworkSettings.Ports'
   # Expected: "8000/tcp" mapped to host
   ```

2. **Check if metrics server started inside the worker:**
   ```bash
   docker exec k2-prefect-worker bash -c \
     "curl -s http://localhost:8000/metrics | head -5"

   # If this works but host curl fails: Port mapping issue
   # If this fails too: Metrics module not loaded
   ```

3. **Restart the worker to reinitialize metrics:**
   ```bash
   docker restart k2-prefect-worker
   sleep 15
   curl -s http://localhost:8000/metrics | grep offload_info
   ```

4. **If port conflict on host:**
   ```bash
   # Check what's using port 8000 on host
   lsof -i :8000

   # Resolve conflict or update Prometheus scrape config to use different port
   ```

---

### Scenario 5: Full Stack Restart

Use when the above scenarios don't resolve, or after a host reboot.

```bash
# Bring up all Prefect components in correct order
docker start k2-prefect-db
sleep 5

docker start k2-prefect-server
sleep 15

docker start k2-prefect-worker
sleep 10

# Verify all running
docker ps --filter name=k2-prefect --format "table {{.Names}}\t{{.Status}}"

# Verify worker online
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api prefect worker ls"

# Trigger immediate run to confirm
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api \
   prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'"
```

---

## Prevention

1. **Auto-restart on failure** — Already configured in `docker-compose.v2.yml`:
   ```yaml
   restart: unless-stopped
   ```
   All three Prefect containers restart automatically on crash.

2. **Health monitoring:** The existing `IcebergOffloadSchedulerDown` Prometheus alert covers
   this. No additional cron/scripts needed — Docker handles restarts.

3. **Weekly validation:**
   ```bash
   # Confirm last 7 days of flow runs are healthy
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect flow-run ls --limit 50" \
   | grep -c COMPLETED
   # Expected: ~672 (96 runs/day × 7 days)
   ```

---

## Related Monitoring

### Dashboards
- **Prefect UI:** http://localhost:4200 — flow run history, task status, logs
- **Grafana:** http://localhost:3000/d/iceberg-offload — pipeline metrics

### Metrics
- `up{job="iceberg-scheduler"}` — 1 if metrics endpoint reachable, 0 if down
- `offload_info` — metadata metric with pipeline version and start time

### Alerts
- **This Alert:** `IcebergOffloadSchedulerDown` (Critical)
- **Related:** All other offload alerts will eventually fire if worker is down

### Logs
- **Worker logs:** `docker logs k2-prefect-worker --tail=100`
- **Server logs:** `docker logs k2-prefect-server --tail=100`
- **Flow run logs:** http://localhost:4200 → Flow Runs → select run → Logs tab

---

## Post-Incident

1. **Verify pipeline stability (watch 4 cycles):**
   ```bash
   # Check Prefect UI for 4 consecutive COMPLETED runs (covers 1 hour)
   docker exec k2-prefect-worker bash -c \
     "PREFECT_API_URL=http://prefect-server:4200/api prefect flow-run ls --limit 4"
   ```

2. **Verify metrics exporting:**
   ```bash
   curl -s http://localhost:8000/metrics | grep offload_info
   curl -s 'http://localhost:9090/api/v1/query?query=up{job="iceberg-scheduler"}' | \
     jq '.data.result[0].value[1]'
   # Expected: "1"
   ```

3. **Check for data gaps (if downtime >30 min):**
   ```bash
   curl -s http://localhost:8000/metrics | grep offload_lag_minutes
   # If lag >30 min: See iceberg-offload-lag.md for catch-up procedures
   ```

4. **Root cause:** Document why the container stopped and whether the `restart: unless-stopped`
   policy failed to recover it automatically.

---

## Quick Reference

```bash
# 1. Check all Prefect containers (5s)
docker ps --filter name=k2-prefect --format "table {{.Names}}\t{{.Status}}"

# 2. Restart worker (most common fix) (15s)
docker restart k2-prefect-worker

# 3. Verify worker online (5s)
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api prefect worker ls"

# 4. Trigger immediate run to confirm (30s)
docker exec k2-prefect-worker bash -c \
  "PREFECT_API_URL=http://prefect-server:4200/api \
   prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'"

# 5. Verify metrics endpoint (5s)
curl -s http://localhost:8000/metrics | grep offload_info
```

**Total MTTR:** 2-5 minutes (most scenarios)

---

**Last Updated:** 2026-02-18
**Maintained By:** Platform Engineering
**Version:** 2.0 (Prefect 3.x rewrite)
**Related Runbooks:**
- [iceberg-offload-failure.md](iceberg-offload-failure.md) — Offload failures (often after worker recovery)
- [iceberg-offload-lag.md](iceberg-offload-lag.md) — High lag (caused by worker downtime)
- [iceberg-offload-watermark-recovery.md](iceberg-offload-watermark-recovery.md) — Watermark issues
