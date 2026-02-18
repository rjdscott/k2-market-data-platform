# Iceberg Offload Pipeline — Monitoring & Alerting Guide

**Purpose:** Comprehensive monitoring and alerting guide for the Iceberg cold tier offload pipeline
**Last Updated:** 2026-02-12
**Phase:** Phase 5 (Cold Tier Restructure)
**Version:** 1.0
**Owner:** Platform Engineering

---

## TL;DR (Quick Reference)

**Monitoring Stack:**
- **Prometheus** (port 9090): Metric collection and alerting
- **Grafana** (port 3000): Visualization dashboards
- **Scheduler Metrics** (port 8000): Python Prometheus client

**Key Dashboards:**
- **Iceberg Offload Pipeline**: Real-time offload metrics, success rates, lag
- **ClickHouse Overview**: Source data health
- **v2 Migration Tracker**: Overall platform health

**Alert Channels:**
- **Critical**: PagerDuty (future) + Slack #alerts-critical
- **Warning**: Slack #alerts-warnings
- **Info**: Logged only

**Health Check (30 seconds):**
```bash
# 1. Scheduler running?
systemctl is-active iceberg-offload-scheduler

# 2. Recent cycle success?
grep "COMPLETED" /tmp/iceberg-offload-scheduler.log | tail -1

# 3. Metrics exporting?
curl -s http://localhost:8000/metrics | grep offload_rows_total

# 4. Alert status?
# Open Prometheus: http://localhost:9090/alerts
```

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Metrics Reference](#metrics-reference)
3. [Dashboard Guide](#dashboard-guide)
4. [Alert Definitions](#alert-definitions)
5. [Troubleshooting](#troubleshooting)
6. [Notification Setup](#notification-setup)
7. [SLO & Performance Targets](#slo-performance-targets)
8. [Operational Runbooks](#operational-runbooks)

---

## Architecture Overview

### Monitoring Stack Components

```
┌──────────────────────────────────────────────────────────────┐
│                   Monitoring Architecture                     │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  Iceberg Scheduler (Python)                                  │
│  ├─ Prometheus Client (port 8000)                           │
│  ├─ Exports metrics every offload cycle                     │
│  └─ metrics.py module                                       │
│                ↓                                             │
│  Prometheus (Docker, port 9090)                             │
│  ├─ Scrapes scheduler every 15s                            │
│  ├─ Evaluates alert rules every 30s                        │
│  ├─ Stores metrics (15-day retention)                      │
│  └─ Exposes /alerts and /metrics endpoints                 │
│                ↓                                             │
│  Grafana (Docker, port 3000)                                │
│  ├─ Queries Prometheus datasource                          │
│  ├─ Renders dashboards                                     │
│  └─ Provides visualization UI                              │
│                ↓                                             │
│  Alertmanager (Future)                                      │
│  ├─ Groups and routes alerts                               │
│  ├─ Sends notifications (Slack, PagerDuty, email)         │
│  └─ Handles alert deduplication                            │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Scheduler executes offload** (every 15 minutes)
2. **Metrics recorded** via `metrics.py` module
3. **Prometheus scrapes** metrics from scheduler (port 8000)
4. **Alert rules evaluated** every 30 seconds
5. **Grafana queries** Prometheus for dashboard updates
6. **Notifications sent** when alerts fire (future: Alertmanager)

---

## Metrics Reference

### Metric Types Overview

| Type | Description | Use Case | Example |
|------|-------------|----------|---------|
| **Counter** | Cumulative (always increasing) | Total counts, rates | `offload_rows_total` |
| **Gauge** | Point-in-time value | Current state, lag | `offload_lag_minutes` |
| **Histogram** | Distribution with buckets | Percentiles, SLOs | `offload_duration_seconds` |
| **Summary** | Distribution with quantiles | p50/p90/p99 | `offload_rows_per_second` |
| **Info** | Static metadata | Configuration | `offload_info` |

### Counter Metrics (Cumulative)

#### `offload_rows_total{table, layer}`
**Description:** Total number of rows offloaded to Iceberg (cumulative)
**Labels:**
- `table`: Source table name (e.g., `bronze_trades_binance`)
- `layer`: Data layer (`bronze`, `silver`, `gold`)

**Query Examples:**
```promql
# Offload rate (rows/second) over last 5 minutes
rate(offload_rows_total[5m])

# Total rows offloaded in last hour
increase(offload_rows_total[1h])

# Rows per table
sum(rate(offload_rows_total[5m])) by (table)
```

**Alerting:**
- Low throughput: `rate(offload_rows_total[5m]) < 10000` (warning)

---

#### `offload_cycles_total{status}`
**Description:** Total number of offload cycles executed
**Labels:**
- `status`: Cycle outcome (`success`, `partial`, `failed`)

**Query Examples:**
```promql
# Success rate (%)
(rate(offload_cycles_total{status="success"}[5m]) / rate(offload_cycles_total[5m])) * 100

# Cycles per hour
rate(offload_cycles_total[1h]) * 3600

# Failed cycles
increase(offload_cycles_total{status="failed"}[1h])
```

**Alerting:**
- Success rate < 95%: Warning
- Success rate < 90%: Critical

---

#### `offload_errors_total{table, error_type}`
**Description:** Total number of offload errors
**Labels:**
- `table`: Affected table
- `error_type`: Error classification (`timeout`, `connection`, `process_error`, `crash`)

**Query Examples:**
```promql
# Error rate (errors/minute)
rate(offload_errors_total[5m]) * 60

# Errors by type
sum(rate(offload_errors_total[5m])) by (error_type)

# Recent errors for specific table
increase(offload_errors_total{table="bronze_trades_binance"}[15m])
```

**Alerting:**
- 3+ consecutive errors in 15 minutes: Critical
- Error rate > 0.1 errors/minute: Warning

---

### Gauge Metrics (Point-in-Time)

#### `offload_lag_minutes{table}`
**Description:** Time since last successful offload (minutes)
**Labels:**
- `table`: Table name

**Interpretation:**
- **<15 minutes:** Healthy (within target)
- **15-30 minutes:** Elevated (approaching SLO)
- **>30 minutes:** Critical (SLO violation)

**Query Examples:**
```promql
# Current lag per table
offload_lag_minutes

# Maximum lag across all tables
max(offload_lag_minutes)

# Tables with lag > 20 minutes
offload_lag_minutes > 20
```

**Alerting:**
- Lag > 20 minutes for 10 minutes: Warning
- Lag > 30 minutes for 5 minutes: Critical

---

#### `watermark_timestamp_seconds{table}`
**Description:** Unix timestamp of last successful watermark update
**Labels:**
- `table`: Table name

**Query Examples:**
```promql
# Time since last watermark update (seconds)
time() - watermark_timestamp_seconds

# Watermark age (minutes)
(time() - watermark_timestamp_seconds) / 60

# Stale watermarks (>1 hour)
(time() - watermark_timestamp_seconds) > 3600
```

**Alerting:**
- Watermark not updated in >1 hour: Critical (pipeline hung)

---

#### `offload_tables_configured`
**Description:** Number of tables configured for offload
**No labels**

**Interpretation:**
- Should match number of Bronze tables (currently: 2)
- Sudden change indicates configuration issue

---

#### `offload_last_success_timestamp`
**Description:** Unix timestamp of last successful cycle
**No labels**

**Query Examples:**
```promql
# Minutes since last success
(time() - offload_last_success_timestamp) / 60

# Hours since last success
(time() - offload_last_success_timestamp) / 3600
```

---

#### `offload_last_failure_timestamp`
**Description:** Unix timestamp of last failed cycle
**No labels**

---

### Histogram Metrics (Distributions)

#### `offload_duration_seconds{table, layer}`
**Description:** Duration of individual offload operations
**Buckets:** 1s, 5s, 10s, 30s, 1m, 2m, 5m, 10m
**Labels:**
- `table`: Table name
- `layer`: Data layer

**Query Examples:**
```promql
# p50 duration (median)
histogram_quantile(0.50, rate(offload_duration_seconds_bucket[5m]))

# p95 duration
histogram_quantile(0.95, rate(offload_duration_seconds_bucket[5m]))

# p99 duration (tail latency)
histogram_quantile(0.99, rate(offload_duration_seconds_bucket[5m]))

# Average duration
rate(offload_duration_seconds_sum[5m]) / rate(offload_duration_seconds_count[5m])
```

**Targets:**
- p50: <10 seconds
- p95: <30 seconds
- p99: <60 seconds

---

#### `offload_cycle_duration_seconds`
**Description:** Duration of complete offload cycles (all tables)
**Buckets:** 5s, 10s, 15s, 30s, 1m, 2m, 5m, 10m, 15m

**Query Examples:**
```promql
# Average cycle duration
rate(offload_cycle_duration_seconds_sum[5m]) / rate(offload_cycle_duration_seconds_count[5m])

# p95 cycle duration
histogram_quantile(0.95, rate(offload_cycle_duration_seconds_bucket[5m]))
```

**Targets:**
- Average: <30 seconds
- p95: <60 seconds
- Maximum: <10 minutes (2/3 of 15-min schedule)

**Alerting:**
- Cycle > 5 minutes: Warning
- Cycle > 10 minutes: Critical (risk of overlap)

---

### Summary Metrics (Quantiles)

#### `offload_rows_per_second{table}`
**Description:** Throughput in rows/second
**Quantiles:** 0.5 (p50), 0.9 (p90), 0.99 (p99)
**Labels:**
- `table`: Table name

**Query Examples:**
```promql
# p50 throughput
offload_rows_per_second{quantile="0.5"}

# p90 throughput
offload_rows_per_second{quantile="0.9"}

# p99 throughput (best case)
offload_rows_per_second{quantile="0.99"}
```

**Targets:**
- p50: >100K rows/second
- p90: >50K rows/second
- p99: >10K rows/second (SLO)

---

### Info Metrics (Static Metadata)

#### `offload_info`
**Description:** Static pipeline metadata
**Labels:**
- `version`: Pipeline version (e.g., `1.0.0`)
- `schedule_minutes`: Schedule interval (e.g., `15`)
- `tables`: Comma-separated table list
- `start_time`: Scheduler start timestamp

**Query Example:**
```promql
# View pipeline info
offload_info
```

---

## Dashboard Guide

### Accessing Grafana

**URL:** http://localhost:3000
**Default Credentials:**
- Username: `admin`
- Password: `admin` (change on first login)

**Dashboard Location:**
- Folder: `K2 Platform v2`
- Dashboard: `Iceberg Offload Pipeline`

### Dashboard Panels Explained

#### Panel 1: Offload Lag (Gauge)
**Purpose:** Real-time lag indicator per table
**Interpretation:**
- **Green (<10 min):** Healthy
- **Yellow (10-30 min):** Elevated
- **Red (>30 min):** Critical

**Actions if Red:**
1. Check scheduler running: `systemctl status iceberg-offload-scheduler`
2. Review recent logs: `tail -50 /tmp/iceberg-offload-scheduler.log`
3. See [Troubleshooting](#troubleshooting) section

---

#### Panel 2: Success Rate (5m)
**Purpose:** Percentage of successful cycles over last 5 minutes
**Interpretation:**
- **Green (>99%):** Healthy
- **Yellow (95-99%):** Degraded
- **Red (<95%):** Critical

**Actions if Yellow/Red:**
1. Check error logs: `grep ERROR /tmp/iceberg-offload-scheduler.log`
2. Review specific error types in Panel 7 (Error Rate)
3. See [Alert Definitions](#alert-definitions) for runbook links

---

#### Panel 3: Configured Tables
**Purpose:** Number of tables configured for offload
**Expected Value:** 2 (Binance + Kraken Bronze)

**Actions if Incorrect:**
- Value changed unexpectedly → configuration issue
- Check `scheduler.py` BRONZE_TABLES list

---

#### Panel 4: Offload Rate (rows/second)
**Purpose:** Real-time throughput per table
**Interpretation:**
- **Target:** >100K rows/second per table
- **SLO:** >10K rows/second per table

**Actions if Low:**
1. Check ClickHouse query performance
2. Review Spark executor logs
3. Check network latency between services
4. See [Performance Troubleshooting](#performance-troubleshooting)

---

#### Panel 5: Offload Duration (p50, p95, p99)
**Purpose:** Duration percentiles for offload operations
**Interpretation:**
- **p50 (median):** Typical operation time
- **p95:** Most operations complete within this time
- **p99 (tail):** Slowest operations

**Targets:**
- p50: <10 seconds
- p95: <30 seconds
- p99: <60 seconds

**Actions if High:**
- p95 >1 minute: Investigate Spark/ClickHouse slowness
- p99 >5 minutes: Critical performance issue

---

#### Panel 6: Cycle Duration (15-min target)
**Purpose:** Time to complete full offload cycle (all tables)
**Interpretation:**
- **Green (<30s):** Healthy
- **Yellow (30s-5m):** Elevated
- **Red (>5m):** Degraded

**Actions if Red:**
- Duration >10 minutes: Risk of cycle overlap
- See [Performance Optimization](#performance-optimization)

---

#### Panel 7: Error Rate (errors/minute)
**Purpose:** Frequency of errors by table and type
**Interpretation:**
- **Zero errors:** Healthy
- **Transient spikes:** Acceptable (retry logic handles)
- **Sustained errors:** Action required

**Error Types:**
- `timeout`: Offload exceeded 10-minute timeout
- `process_error`: Spark job failed or crashed
- `connection`: ClickHouse or Iceberg connectivity issue
- `crash`: Scheduler crash (rare)

---

#### Panel 8: Throughput Quantiles (p50, p90, p99)
**Purpose:** Rows/second distribution by table
**Interpretation:**
- Shows performance consistency
- Large p99 > p50 gap = high variability

---

#### Panel 9: Cycle Status Distribution
**Purpose:** Stacked bars showing cycle outcomes
**Colors:**
- **Green:** Success (all tables offloaded)
- **Yellow:** Partial (some tables failed)
- **Red:** Failed (all tables failed)

**Targets:**
- 100% green stacked bars = perfect success
- Any yellow/red = investigate

---

## Alert Definitions

### Alert Severity Levels

| Severity | Response Time | Notification | On-Call | Example |
|----------|---------------|--------------|---------|---------|
| **Critical** | Immediate | PagerDuty + Slack | Yes | 3+ consecutive failures, >30min lag |
| **Warning** | Business hours | Slack | No | Success rate <95%, lag 20-30min |
| **Info** | Log only | None | No | Recording rules, health checks |

### Critical Alerts

#### `IcebergOffloadConsecutiveFailures`
**Trigger:** 3+ errors in 15 minutes for any table
**Duration:** 5 minutes
**Impact:** Cold tier not updating; data loss risk if ClickHouse TTL triggers

**Immediate Actions:**
```bash
# 1. Check scheduler running
systemctl status iceberg-offload-scheduler

# 2. View recent errors
grep ERROR /tmp/iceberg-offload-scheduler.log | tail -20

# 3. Check ClickHouse connectivity
docker exec k2-clickhouse clickhouse-client -q "SELECT 1"

# 4. Check Spark container
docker ps | grep spark-iceberg

# 5. Review watermark table
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT * FROM offload_watermarks ORDER BY created_at DESC LIMIT 5"
```

**Runbook:** `docs/operations/runbooks/iceberg-offload-failure.md`

---

#### `IcebergOffloadLagCritical`
**Trigger:** Lag > 30 minutes for any table
**Duration:** 5 minutes
**Impact:** SLO violation; cold tier data stale

**Immediate Actions:**
```bash
# 1. Check scheduler active
systemctl is-active iceberg-offload-scheduler

# 2. Check recent cycles
grep "COMPLETED" /tmp/iceberg-offload-scheduler.log | tail -5

# 3. Check watermark progression
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT table_name, max_timestamp, NOW() - max_timestamp AS lag \
   FROM offload_watermarks \
   WHERE table_name IN ('bronze_trades_binance', 'bronze_trades_kraken') \
   ORDER BY created_at DESC LIMIT 2"

# 4. If scheduler stuck, restart
sudo systemctl restart iceberg-offload-scheduler
```

**Runbook:** `docs/operations/runbooks/iceberg-offload-lag.md`

---

#### `IcebergOffloadCycleTooSlow`
**Trigger:** Cycle duration > 10 minutes
**Duration:** 2 minutes
**Impact:** Risk of cycle overlap; resource contention

**Immediate Actions:**
```bash
# 1. Check data volume
docker exec k2-clickhouse clickhouse-client -q \
  "SELECT table, count() FROM bronze_trades_binance WHERE exchange_timestamp > now() - INTERVAL 1 HOUR GROUP BY table"

# 2. Check Spark performance
docker exec k2-spark-iceberg tail -50 /spark/logs/spark-worker.log

# 3. Check network latency
docker exec k2-spark-iceberg ping -c 5 k2-clickhouse
```

**Runbook:** `docs/operations/runbooks/iceberg-offload-performance.md`

---

#### `IcebergOffloadWatermarkStale`
**Trigger:** Watermark not updated in >1 hour
**Duration:** 5 minutes
**Impact:** Pipeline hung; no offloads occurring

**Immediate Actions:**
```bash
# 1. Check scheduler running
ps aux | grep scheduler.py

# 2. Review last cycle status
tail -100 /tmp/iceberg-offload-scheduler.log | grep -A 10 "CYCLE STARTED"

# 3. Manually inspect watermark
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT * FROM offload_watermarks ORDER BY created_at DESC LIMIT 5"

# 4. If hung, restart scheduler
sudo systemctl restart iceberg-offload-scheduler
```

**Runbook:** `docs/operations/runbooks/iceberg-offload-watermark-recovery.md`

---

#### `IcebergOffloadSchedulerDown`
**Trigger:** Prometheus cannot scrape scheduler metrics (port 8000)
**Duration:** 2 minutes
**Impact:** No offloads occurring; cold tier not updating

**Immediate Actions:**
```bash
# 1. Check scheduler status
systemctl status iceberg-offload-scheduler

# 2. Check logs for crash
tail -100 /tmp/iceberg-offload-scheduler.log

# 3. Test metrics endpoint
curl http://localhost:8000/metrics

# 4. Restart scheduler
sudo systemctl restart iceberg-offload-scheduler

# 5. Verify metrics available
curl http://localhost:8000/metrics | grep offload_rows_total
```

**Runbook:** `docs/operations/runbooks/iceberg-scheduler-recovery.md`

---

### Warning Alerts

#### `IcebergOffloadSuccessRateLow`
**Trigger:** Success rate < 95% over 15 minutes
**Duration:** 10 minutes
**Impact:** Minor; some gaps in cold tier but not critical

**Actions:**
```bash
# Review error types
grep ERROR /tmp/iceberg-offload-scheduler.log | tail -20

# Check for transient network issues
docker logs k2-clickhouse --tail=50 | grep -i error

# Monitor for escalation
# If success rate drops below 90%, escalate to critical
```

---

#### `IcebergOffloadLagElevated`
**Trigger:** Lag 20-30 minutes
**Duration:** 10 minutes
**Impact:** Minor; trending upward but not critical yet

**Actions:**
- Monitor for escalation to >30 minutes
- Check for data volume spikes
- Review scheduler logs for delays

---

#### `IcebergOffloadThroughputLow`
**Trigger:** Throughput < 10K rows/second
**Duration:** 15 minutes
**Impact:** Performance degraded; offloads taking longer

**Actions:**
```bash
# Check Spark executor logs
docker exec k2-spark-iceberg tail -100 /spark/logs/spark-executor.log

# Check ClickHouse query performance
docker exec k2-clickhouse clickhouse-client -q \
  "SELECT query, elapsed FROM system.processes WHERE query LIKE '%offload%'"

# Check network latency
docker exec k2-spark-iceberg ping -c 10 k2-clickhouse
```

---

#### `IcebergOffloadCycleSlow`
**Trigger:** Cycle duration 5-10 minutes
**Duration:** 10 minutes
**Impact:** Minor; trending upward but not critical

**Actions:**
- Monitor for escalation to >10 minutes
- Review data volume trends
- Consider parallel offload optimization (future)

---

## Troubleshooting

### Common Issues & Solutions

#### Issue 1: Scheduler Not Exporting Metrics

**Symptoms:**
- Grafana panels show "No data"
- Prometheus shows `up{job="iceberg-scheduler"} = 0`
- Alert: `IcebergOffloadSchedulerDown`

**Diagnosis:**
```bash
# Check scheduler running
systemctl status iceberg-offload-scheduler

# Check metrics endpoint
curl http://localhost:8000/metrics

# Check scheduler logs for metrics errors
grep "metrics" /tmp/iceberg-offload-scheduler.log
```

**Solutions:**
1. **Scheduler not running:**
   ```bash
   sudo systemctl start iceberg-offload-scheduler
   ```

2. **Metrics import failed:**
   - Check `metrics.py` exists in `/home/rjdscott/Documents/projects/k2-market-data-platform/docker/offload/`
   - Check Prometheus client installed: `uv pip list | grep prometheus-client`

3. **Port 8000 in use:**
   ```bash
   # Find process using port 8000
   lsof -i :8000

   # Change port in scheduler.py if needed
   # Update Prometheus scrape config accordingly
   ```

---

#### Issue 2: High Offload Lag

**Symptoms:**
- `offload_lag_minutes` > 20
- Alert: `IcebergOffloadLagElevated` or `IcebergOffloadLagCritical`

**Diagnosis:**
```bash
# Check scheduler cycle frequency
grep "CYCLE STARTED" /tmp/iceberg-offload-scheduler.log | tail -10

# Check watermark progression
docker exec k2-prefect-db psql -U prefect -d prefect -c \
  "SELECT table_name, max_timestamp, sequence_number, created_at \
   FROM offload_watermarks \
   ORDER BY created_at DESC LIMIT 10"
```

**Root Causes & Solutions:**

1. **Scheduler skipped cycles:**
   - Check systemd journal: `journalctl -u iceberg-offload-scheduler -n 50`
   - Verify no crash/restart occurred

2. **Data volume spike:**
   ```bash
   # Check ClickHouse row counts
   docker exec k2-clickhouse clickhouse-client -q \
     "SELECT count() FROM bronze_trades_binance \
      WHERE exchange_timestamp > now() - INTERVAL 1 HOUR"
   ```
   - Solution: Data volume normal → no action needed (lag will catch up)
   - Solution: Data volume excessive → see [Performance Optimization](#performance-optimization)

3. **Offload duration increased:**
   - Check Panel 5 (Offload Duration) in Grafana
   - If p95 duration > 1 minute → investigate Spark/ClickHouse performance

---

#### Issue 3: Low Success Rate

**Symptoms:**
- `offload_cycles_total{status="failed"}` increasing
- Success rate < 95%
- Alert: `IcebergOffloadSuccessRateLow`

**Diagnosis:**
```bash
# Check error types
grep "✗ Offload failed" /tmp/iceberg-offload-scheduler.log | tail -20

# Check Spark logs
docker exec k2-spark-iceberg tail -100 /spark/logs/spark-executor.log

# Check ClickHouse connectivity
docker exec k2-spark-iceberg ping -c 5 k2-clickhouse
```

**Common Error Types:**

1. **Timeout errors:**
   - Offload exceeded 10-minute timeout
   - Solution: Increase timeout in `scheduler.py` if justified by data volume
   - Or: Optimize query performance (add indexes, adjust Spark resources)

2. **Connection errors:**
   - ClickHouse JDBC connection failed
   - Solution: Check ClickHouse health, restart if needed
   - Verify network connectivity between containers

3. **Process errors:**
   - Spark job crashed or failed
   - Solution: Review Spark logs for exceptions
   - Check Spark memory/CPU resources

---

#### Issue 4: Prometheus Not Scraping Scheduler

**Symptoms:**
- Prometheus UI shows job `iceberg-scheduler` as down
- Grafana dashboard empty

**Diagnosis:**
```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.job=="iceberg-scheduler")'

# Check scheduler metrics accessible from Prometheus container
docker exec k2-prometheus curl -s http://host.docker.internal:8000/metrics
```

**Solutions:**

1. **host.docker.internal not resolving (Linux):**
   - Edit `/home/rjdscott/Documents/projects/k2-market-data-platform/docker/prometheus/prometheus.yml`
   - Change target from `host.docker.internal:8000` to `172.17.0.1:8000` (Docker bridge IP)
   - Or: Run scheduler in Docker container with `--network k2-network`

2. **Prometheus config error:**
   ```bash
   # Validate Prometheus config
   docker exec k2-prometheus promtool check config /etc/prometheus/prometheus.yml

   # Reload Prometheus config
   curl -X POST http://localhost:9090/-/reload
   ```

---

#### Issue 5: Alert Rules Not Firing

**Symptoms:**
- Obvious issues (e.g., high lag) but no alerts
- Prometheus shows rules but status = inactive

**Diagnosis:**
```bash
# Check Prometheus rules
curl http://localhost:9090/api/v1/rules | jq '.data.groups[].rules[] | select(.name | contains("IcebergOffload"))'

# Check alert evaluation
# Open: http://localhost:9090/alerts
```

**Solutions:**

1. **Rules not loaded:**
   ```bash
   # Verify rule file exists
   ls -la docker/prometheus/rules/iceberg-offload-alerts.yml

   # Check Prometheus config includes rule_files
   grep "rule_files" docker/prometheus/prometheus.yml

   # Restart Prometheus
   docker restart k2-prometheus
   ```

2. **Alert condition not met:**
   - Verify metric values in Prometheus UI
   - Check alert `for` duration (alert may be in "pending" state)

3. **No Alertmanager configured:**
   - Alerts fire but no notifications sent (expected in current setup)
   - See [Notification Setup](#notification-setup) for Alertmanager configuration

---

## Notification Setup

### Current State (Phase 5)

**Alerting Status:** ✅ Rules configured, ⬜ Notifications not yet configured

**Alert Detection:** Prometheus evaluates rules and fires alerts
**Alert Routing:** No Alertmanager configured yet (notifications logged only)

**Next Steps (Phase 5 follow-up or Phase 7):**
1. Deploy Alertmanager container
2. Configure notification channels (Slack, PagerDuty, email)
3. Set up alert routing rules
4. Test end-to-end alert flow

---

### Future: Alertmanager Integration (Reference)

**Deployment:**
```yaml
# Add to docker-compose.v2.yml
alertmanager:
  image: prom/alertmanager:v0.26.0
  container_name: k2-alertmanager
  ports:
    - "9093:9093"
  volumes:
    - ./docker/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml
  command:
    - '--config.file=/etc/alertmanager/alertmanager.yml'
    - '--storage.path=/alertmanager'
  networks:
    - k2-network
```

**Sample Configuration:**
```yaml
# docker/alertmanager/alertmanager.yml
global:
  slack_api_url: 'https://hooks.slack.com/services/YOUR/WEBHOOK/URL'

route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'slack-critical'
  routes:
    - match:
        severity: critical
      receiver: 'slack-critical'
      continue: true
    - match:
        severity: critical
      receiver: 'pagerduty'
    - match:
        severity: warning
      receiver: 'slack-warnings'

receivers:
  - name: 'slack-critical'
    slack_configs:
      - channel: '#alerts-critical'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'

  - name: 'slack-warnings'
    slack_configs:
      - channel: '#alerts-warnings'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'

  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: 'YOUR_PAGERDUTY_SERVICE_KEY'
```

**Testing Notifications:**
```bash
# Send test alert to Alertmanager
curl -X POST http://localhost:9093/api/v1/alerts -H 'Content-Type: application/json' -d '[
  {
    "labels": {
      "alertname": "TestAlert",
      "severity": "warning"
    },
    "annotations": {
      "summary": "Test alert from Iceberg Offload monitoring"
    }
  }
]'
```

---

## SLO & Performance Targets

### Service Level Objectives (SLOs)

| Metric | Target | SLO | Critical | Measurement Window |
|--------|--------|-----|----------|-------------------|
| **Success Rate** | >99% | >95% | <90% | 15 minutes |
| **Offload Lag** | <15 min | <30 min | >30 min | Per table |
| **Cycle Duration** | <30s | <5 min | >10 min | Per cycle |
| **Throughput** | >100K rows/s | >10K rows/s | <10K rows/s | 5-minute avg |
| **Availability** | 99.9% | 99% | <95% | Monthly |

### Performance Targets by Scale

| Data Volume | Expected Duration | Max Acceptable | Actions if Exceeded |
|-------------|-------------------|----------------|---------------------|
| **10K rows** | <5 seconds | <30 seconds | Investigate query performance |
| **100K rows** | <10 seconds | <60 seconds | Check network latency |
| **1M rows** | <30 seconds | <5 minutes | Optimize Spark resources |
| **10M rows** | <5 minutes | <10 minutes | Consider parallel processing |

### Alert Escalation Matrix

| Alert | First Response | Escalation Time | Escalation Action |
|-------|----------------|-----------------|-------------------|
| **Consecutive Failures** | Immediate | 15 minutes | Page on-call engineer |
| **Lag Critical** | Immediate | 30 minutes | Incident commander |
| **Cycle Too Slow** | 15 minutes | 1 hour | Engineering lead |
| **Success Rate Low** | Business hours | 2 hours | Team lead notification |

---

## Operational Runbooks

### Quick Reference

| Scenario | Runbook Link | Estimated Recovery Time |
|----------|--------------|------------------------|
| Offload failures (3+ consecutive) | [iceberg-offload-failure.md](../runbooks/iceberg-offload-failure.md) | 15-30 minutes |
| High offload lag (>30 min) | [iceberg-offload-lag.md](../runbooks/iceberg-offload-lag.md) | 10-20 minutes |
| Slow performance | [iceberg-offload-performance.md](../runbooks/iceberg-offload-performance.md) | 30-60 minutes |
| Watermark corruption | [iceberg-offload-watermark-recovery.md](../runbooks/iceberg-offload-watermark-recovery.md) | 30-45 minutes |
| Scheduler crash | [iceberg-scheduler-recovery.md](../runbooks/iceberg-scheduler-recovery.md) | 5-10 minutes |

**Note:** Runbooks not yet created; placeholders for Phase 5 completion or Phase 6/7 work.

---

## Performance Optimization

### Current Performance Baseline (2026-02-12)

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| **Cycle Duration** | 12.4s | <30s | ✅ 2.4x faster than target |
| **Throughput** | 236K rows/s | >100K rows/s | ✅ 2.3x target |
| **Resource Usage** | 0.5 CPU / 256MB | <1 CPU / 512MB | ✅ 2x better than budget |
| **Success Rate** | 100% (2/2) | >99% | ✅ Perfect |

### Optimization Opportunities (Future)

1. **Parallel Table Offload:**
   - **Current:** Sequential (binance → kraken → ...)
   - **Future:** Parallel with `ThreadPoolExecutor`
   - **Estimated Improvement:** 50% cycle duration reduction
   - **Implementation:** Phase 6 or 7

2. **Spark Resource Tuning:**
   - **Current:** Default Spark resources
   - **Future:** Tune executor memory, cores, parallelism
   - **Estimated Improvement:** 20-30% throughput increase
   - **Implementation:** When data volume grows

3. **Watermark Batch Updates:**
   - **Current:** Single row INSERT per offload
   - **Future:** Batch INSERT for multiple tables
   - **Estimated Improvement:** Negligible (not a bottleneck)
   - **Priority:** Low

---

## Appendix: Prometheus Query Cookbook

### Useful Queries

#### Offload Health Overview
```promql
# Current lag per table
offload_lag_minutes

# Success rate (last 15 minutes)
(rate(offload_cycles_total{status="success"}[15m]) / rate(offload_cycles_total[15m])) * 100

# Total rows offloaded (last hour)
sum(increase(offload_rows_total[1h]))
```

#### Performance Analysis
```promql
# Average cycle duration (last hour)
avg(offload_cycle_duration_seconds)

# p95 offload duration per table
histogram_quantile(0.95, rate(offload_duration_seconds_bucket[15m]))

# Throughput per table
rate(offload_rows_total[5m]) by (table)
```

#### Error Analysis
```promql
# Error rate per table
rate(offload_errors_total[5m]) * 60 by (table)

# Error types distribution
sum(rate(offload_errors_total[15m])) by (error_type)

# Recent error count
increase(offload_errors_total[1h])
```

#### Capacity Planning
```promql
# Rows offloaded per day (projected)
sum(rate(offload_rows_total[1h])) * 86400

# Average cycle count per hour
rate(offload_cycles_total[1h]) * 3600
```

---

## Related Documentation

- **Scheduler Implementation:** [scheduler.py](../../../docker/offload/scheduler.py)
- **Metrics Module:** [metrics.py](../../../docker/offload/metrics.py)
- **Alert Rules:** [iceberg-offload-alerts.yml](../../../docker/prometheus/rules/iceberg-offload-alerts.yml)
- **Grafana Dashboard:** [iceberg-offload.json](../../../docker/grafana/dashboards/iceberg-offload.json)
- **Phase 5 README:** [phase-5-cold-tier-restructure/README.md](../../phases/v2/phase-5-cold-tier-restructure/README.md)
- **Production Deployment Guide:** [prefect-schedule-config.md](../prefect-schedule-config.md)

---

**Last Updated:** 2026-02-12
**Version:** 1.0
**Maintained By:** Platform Engineering
**Questions?** Create issue or update this doc with your learnings
