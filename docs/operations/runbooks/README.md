# Iceberg Offload Pipeline — Operational Runbooks

**Purpose:** Incident response procedures for the Iceberg cold tier offload pipeline
**Last Updated:** 2026-02-12 (Phase 5, Priority 6)
**Maintained By:** Platform Engineering
**Version:** 1.0

---

## Overview

This directory contains comprehensive operational runbooks for responding to incidents in the Iceberg offload pipeline. Each runbook provides step-by-step diagnosis and resolution procedures for specific failure scenarios.

**Runbook Philosophy:**
- **Staff-level rigor:** Detailed, actionable procedures
- **Time-optimized:** Quick Reference sections for fastest recovery
- **Scenario-based:** Common failure patterns with specific resolutions
- **Cross-referenced:** Links to related runbooks and monitoring

---

## Runbook Index

| # | Runbook | Severity | Response Time | MTTR | Alert |
|---|---------|----------|---------------|------|-------|
| 1 | [Offload Failure](#1-offload-failure) | 🔴 Critical | Immediate | 15-30 min | `IcebergOffloadConsecutiveFailures` |
| 2 | [High Lag](#2-high-lag) | 🔴 Critical / 🟡 Warning | Immediate / Business hours | 15-30 min | `IcebergOffloadLagCritical`, `IcebergOffloadLagElevated` |
| 3 | [Performance Degradation](#3-performance-degradation) | 🔴 Critical / 🟡 Warning | Immediate / Business hours | 15-30 min | `IcebergOffloadCycleTooSlow`, `IcebergOffloadCycleSlow` |
| 4 | [Watermark Recovery](#4-watermark-recovery) | 🔴 Critical | Immediate | 15-60 min | `IcebergOffloadWatermarkStale` |
| 5 | [Scheduler Recovery](#5-scheduler-recovery) | 🔴 Critical | Immediate | 2-5 min | `IcebergOffloadSchedulerDown` |

**MTTR (Mean Time To Recovery):** Estimated time from diagnosis to resolution for typical scenarios.

---

## Runbook Summaries

### 1. Offload Failure

**File:** [iceberg-offload-failure.md](iceberg-offload-failure.md)
**Symptoms:** 3+ consecutive offload failures within 15 minutes
**Impact:** Cold tier not updating, risk of data loss
**Common Causes:**
- Scheduler not running (most common)
- ClickHouse connectivity failure
- Spark job crashes/OOM
- Watermark table corruption
- Timeout due to data volume spike

**Fastest Resolution:**
```bash
# Check scheduler → Check ClickHouse → Check Spark → Restart if needed
systemctl status iceberg-offload-scheduler
docker exec k2-clickhouse clickhouse-client -q "SELECT 1"
sudo systemctl restart iceberg-offload-scheduler
```

---

### 2. High Lag

**File:** [iceberg-offload-lag.md](iceberg-offload-lag.md)
**Symptoms:** Time since last offload >20 minutes (warning) or >30 minutes (critical)
**Impact:** SLO violation, stale cold tier data
**Common Causes:**
- Scheduler skipping cycles (hung or stopped)
- Watermark not updating (silent failure)
- Data volume spike (temporary)
- Performance degradation (see Runbook 3)

**Fastest Resolution:**
```bash
# Check lag → Check scheduler → Manual catch-up if needed
curl -s http://localhost:8000/metrics | grep offload_lag_minutes
systemctl restart iceberg-offload-scheduler
# If >45 min: docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py ...
```

---

### 3. Performance Degradation

**File:** [iceberg-offload-performance.md](iceberg-offload-performance.md)
**Symptoms:** Cycle duration >5 minutes (warning) or >10 minutes (critical)
**Impact:** Risk of cycle overlap, increasing lag, resource contention
**Common Causes:**
- Data volume spike (market event)
- Resource exhaustion (CPU/memory)
- Network latency/issues
- ClickHouse slow queries (fragmentation)

**Fastest Resolution:**
```bash
# Check duration → Check data volume → Check resources → Optimize if needed
grep "Duration:" /tmp/iceberg-offload-scheduler.log | tail -5
docker stats --no-stream | grep -E "clickhouse|spark"
docker exec k2-clickhouse clickhouse-client -q "OPTIMIZE TABLE k2.bronze_trades_binance FINAL"
```

---

### 4. Watermark Recovery

**File:** [iceberg-offload-watermark-recovery.md](iceberg-offload-watermark-recovery.md)
**Symptoms:** Watermark not updated >1 hour, stale watermark timestamp
**Impact:** Pipeline hung or failing silently, risk of duplicates if corrupted
**Common Causes:**
- Scheduler not running
- PostgreSQL connection issues
- Watermark table missing/corrupted
- Silent offload failure (logic bug)

**Fastest Resolution:**
```bash
# Check watermark age → Check scheduler → Check PostgreSQL → Clean if corrupted
curl -s 'http://localhost:9090/api/v1/query?query=(time()-watermark_timestamp_seconds)'
docker exec k2-prefect-db psql -U prefect -d prefect -c "SELECT * FROM offload_watermarks ORDER BY created_at DESC LIMIT 5"
sudo systemctl restart iceberg-offload-scheduler
```

---

### 5. Scheduler Recovery

**File:** [iceberg-scheduler-recovery.md](iceberg-scheduler-recovery.md)
**Symptoms:** Scheduler process stopped, crashed, or metrics unreachable
**Impact:** Complete pipeline outage, no offloads occurring
**Common Causes:**
- Not started after reboot
- Python exception/crash
- Scheduler hung/deadlocked
- Metrics server port conflict
- Persistent start failures (dependency issues)

**Fastest Resolution:**
```bash
# Check status → Start/restart → Force kill if hung
systemctl status iceberg-offload-scheduler
sudo systemctl start iceberg-offload-scheduler
# If hung: sudo systemctl kill -s SIGKILL iceberg-offload-scheduler && sudo systemctl start iceberg-offload-scheduler
```

---

## Quick Diagnostic Flowchart

```
Alert Received
    │
    ├─ Prometheus alert: IcebergOffloadSchedulerDown
    │   └─> Runbook 5: Scheduler Recovery
    │
    ├─ Prometheus alert: IcebergOffloadConsecutiveFailures
    │   └─> Runbook 1: Offload Failure
    │
    ├─ Prometheus alert: IcebergOffloadLagCritical/Elevated
    │   ├─ Check scheduler running?
    │   │   ├─ No → Runbook 5: Scheduler Recovery
    │   │   └─ Yes → Runbook 2: High Lag
    │   │
    │   └─ Lag due to slow cycles?
    │       └─> Runbook 3: Performance Degradation
    │
    ├─ Prometheus alert: IcebergOffloadCycleTooSlow/Slow
    │   └─> Runbook 3: Performance Degradation
    │
    └─ Prometheus alert: IcebergOffloadWatermarkStale
        └─> Runbook 4: Watermark Recovery
```

---

## Health Check Script (30 Seconds)

Use this script for quick health assessment:

```bash
#!/bin/bash
# Iceberg Offload Pipeline - Quick Health Check

echo "=== Iceberg Offload Health Check ==="
echo ""

# 1. Scheduler Status (5 seconds)
echo "1. Scheduler Status:"
systemctl is-active iceberg-offload-scheduler && echo "  ✓ Running" || echo "  ✗ Stopped"
echo ""

# 2. Metrics Reachable (5 seconds)
echo "2. Metrics Endpoint:"
curl -s -o /dev/null -w "  Status: %{http_code}\n" http://localhost:8000/metrics
echo ""

# 3. Current Lag (5 seconds)
echo "3. Offload Lag:"
curl -s http://localhost:8000/metrics | grep offload_lag_minutes | awk '{print "  "$1": "$2" minutes"}'
echo ""

# 4. Recent Cycles (5 seconds)
echo "4. Last 3 Cycles:"
grep "COMPLETED" /tmp/iceberg-offload-scheduler.log | tail -3 | \
  awk '{print "  "$1, $2, "- Duration:", $(NF-1)}'
echo ""

# 5. Recent Errors (5 seconds)
echo "5. Errors (last hour):"
ERROR_COUNT=$(grep "✗ Offload failed" /tmp/iceberg-offload-scheduler.log | \
  grep "$(date +%Y-%m-%d)" | wc -l)
echo "  Error count: $ERROR_COUNT"
if [ $ERROR_COUNT -gt 0 ]; then
  echo "  Last error:"
  grep "✗ Offload failed" /tmp/iceberg-offload-scheduler.log | tail -1 | sed 's/^/    /'
fi
echo ""

# 6. Component Health (5 seconds)
echo "6. Component Health:"
docker exec k2-clickhouse clickhouse-client -q "SELECT 1" &> /dev/null && \
  echo "  ✓ ClickHouse" || echo "  ✗ ClickHouse"
docker ps | grep -q k2-spark-iceberg && \
  echo "  ✓ Spark" || echo "  ✗ Spark"
docker exec k2-prefect-db psql -U prefect -d prefect -c "\dt" &> /dev/null && \
  echo "  ✓ PostgreSQL" || echo "  ✗ PostgreSQL"
echo ""

echo "=== Health Check Complete ==="
```

**Save as:** `/usr/local/bin/iceberg-health-check.sh`
```bash
chmod +x /usr/local/bin/iceberg-health-check.sh
```

---

## Common Scenarios → Runbook Mapping

| Scenario | Primary Runbook | Related Runbooks |
|----------|----------------|------------------|
| **Scheduler won't start after reboot** | Runbook 5 | - |
| **Offloads failing with timeout** | Runbook 1 (Scenario 5) | Runbook 3 |
| **Cold tier 1 hour behind** | Runbook 2 | Runbook 1, 5 |
| **Cycle taking 12 minutes** | Runbook 3 | Runbook 2 |
| **Watermark hasn't moved in 2 hours** | Runbook 4 | Runbook 5, 1 |
| **Grafana dashboard empty** | Runbook 5 | - |
| **ClickHouse unreachable** | Runbook 1 (Scenario 2) | Runbook 5 |
| **Spark container crashed** | Runbook 1 (Scenario 3) | - |
| **Data volume spike (10M rows/hour)** | Runbook 3 (Scenario 1) | Runbook 2 |
| **PostgreSQL connection failure** | Runbook 4 (Scenario 2) | Runbook 1 |

---

## Escalation Matrix

### When to Escalate

| Condition | Escalate To | Timeline |
|-----------|-------------|----------|
| **Recovery not achieved within MTTR** | Engineering Lead | After runbook MTTR + 30 min |
| **Root cause is code bug** | Engineering Lead | Immediate |
| **Recurring failures (>3 in 24h)** | Engineering Lead | After 3rd occurrence |
| **Data loss suspected** | Engineering Lead + Data Team | Immediate |
| **Infrastructure limitation identified** | Platform Engineering | Business hours |
| **Architectural change required** | Staff Engineer / Architect | Business hours |

### Escalation Contacts

- **Engineering Lead:** Platform Engineering Manager
- **On-Call Engineer:** PagerDuty rotation (future)
- **Data Team:** Data Engineering Lead
- **Staff Engineer:** Principal/Staff engineer for architectural decisions

---

## Post-Incident Procedures

After resolving any incident using these runbooks:

1. **Update Incident Log:**
   - Document root cause
   - Record MTTR (actual recovery time)
   - Note any deviations from runbook

2. **Verify Full Recovery:**
   - Run health check script
   - Monitor for 3 cycles (45 minutes)
   - Check for data gaps

3. **Update Runbook (if needed):**
   - Add new failure scenario if encountered
   - Clarify ambiguous steps
   - Update estimated MTTR if significantly different

4. **Post-Mortem (for extended outages):**
   - If outage >1 hour: Document timeline
   - Identify prevention measures
   - Create action items

---

## Monitoring & Alerting

### Related Dashboards
- **Primary:** [Iceberg Offload Pipeline](http://localhost:3000/d/iceberg-offload)
- **ClickHouse:** [ClickHouse Overview](http://localhost:3000/d/clickhouse-overview)
- **v2 Platform:** [v2 Migration Tracker](http://localhost:3000/d/v2-migration-tracker)

### Alert Configuration
- **Alert Rules:** `/home/rjdscott/Documents/projects/k2-market-data-platform/docker/prometheus/rules/iceberg-offload-alerts.yml`
- **Prometheus:** http://localhost:9090/alerts
- **Alert Manager:** Not yet configured (Phase 6/7)

### Key Metrics
- `up{job="iceberg-scheduler"}` - Scheduler reachability
- `offload_lag_minutes` - Time since last offload
- `offload_errors_total` - Error count by type
- `offload_cycle_duration_seconds` - Cycle duration
- `watermark_timestamp_seconds` - Watermark age

---

## Runbook Maintenance

### Review Schedule
- **Monthly:** Review all runbooks for accuracy
- **After Incident:** Update relevant runbook immediately
- **Quarterly:** Validate procedures with test scenarios

### Version Control
- All runbooks are version-controlled in Git
- Changes require pull request review
- Major updates increment version number

### Feedback
If you encounter issues with these runbooks:
1. Document what worked / didn't work
2. Propose specific improvements
3. Submit PR or create issue in project repository

---

## Related Documentation

- **Monitoring Guide:** [iceberg-offload-monitoring.md](../monitoring/iceberg-offload-monitoring.md)
- **Scheduler Implementation:** [scheduler.py](../../../docker/offload/scheduler.py)
- **Alert Rules:** [iceberg-offload-alerts.yml](../../../docker/prometheus/rules/iceberg-offload-alerts.yml)
- **Phase 5 Documentation:** [phase-5-cold-tier-restructure](../../phases/v2/phase-5-cold-tier-restructure/README.md)

---

## Quick Links

| Resource | URL / Command |
|----------|---------------|
| **Prometheus Alerts** | http://localhost:9090/alerts |
| **Grafana Dashboard** | http://localhost:3000/d/iceberg-offload |
| **Metrics Endpoint** | http://localhost:8000/metrics |
| **Scheduler Logs** | `tail -f /tmp/iceberg-offload-scheduler.log` |
| **Scheduler Status** | `systemctl status iceberg-offload-scheduler` |
| **Health Check** | `/usr/local/bin/iceberg-health-check.sh` |

---

**Last Updated:** 2026-02-12
**Maintained By:** Platform Engineering
**Version:** 1.0
**Questions?** Create issue or contact Platform Engineering team
