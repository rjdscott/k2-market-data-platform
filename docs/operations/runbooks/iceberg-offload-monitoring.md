# Iceberg Offload Monitoring & Operations Runbook

**Service**: ClickHouse → Iceberg Offload Pipeline
**Orchestrator**: Prefect (self-hosted)
**Frequency**: Every 15 minutes
**Tables**: 9 (Bronze: 2, Silver: 1, Gold: 6)
**Version**: v2.0 (ADR-014)
**Last Updated**: 2026-02-11

---

## Quick Reference

### Access Points

| Service | URL | Credentials |
|---------|-----|-------------|
| **Prefect UI** | http://localhost:4200 | None (no auth) |
| **Spark UI** | http://localhost:4040 | None (during job execution) |
| **MinIO Console** | http://localhost:9001 | admin / password |

### Key Commands

```bash
# Check offload status
docker logs k2-spark-iceberg --tail 100

# View watermarks (last successful offload)
docker exec clickhouse-server clickhouse-client --query \
  "SELECT table_name, last_offload_timestamp, last_successful_run, status \
   FROM offload_watermarks ORDER BY last_successful_run DESC"

# Manually trigger offload (testing)
docker exec k2-prefect-agent prefect deployment run \
  iceberg-offload-main/iceberg-offload-15min

# Check Prefect flow runs
docker exec k2-prefect-agent prefect flow-run ls --limit 10

# Restart Prefect agent (if stuck)
docker restart k2-prefect-agent
```

---

## Architecture Overview

### Execution Flow

```
┌─────────────────────────────────────────────────────────────┐
│                 15-Minute Cycle (Cron)                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Prefect Scheduler                                          │
│         │                                                   │
│         ├──> Bronze Layer (2 tables in parallel)           │
│         │    ├─> bronze_trades_binance → Spark → Iceberg   │
│         │    └─> bronze_trades_kraken  → Spark → Iceberg   │
│         │                                                   │
│         ├──> Silver Layer (depends on Bronze)              │
│         │    └─> silver_trades → Spark → Iceberg           │
│         │                                                   │
│         └──> Gold Layer (6 tables in parallel)             │
│              ├─> ohlcv_1m  → Spark → Iceberg               │
│              ├─> ohlcv_5m  → Spark → Iceberg               │
│              ├─> ohlcv_15m → Spark → Iceberg               │
│              ├─> ohlcv_30m → Spark → Iceberg               │
│              ├─> ohlcv_1h  → Spark → Iceberg               │
│              └─> ohlcv_1d  → Spark → Iceberg               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Exactly-Once Semantics

**Guarantees**:
1. **Watermark tracking** - prevents duplicate reads from ClickHouse
2. **Iceberg atomic commits** - prevents partial writes (all-or-nothing)
3. **Idempotent retries** - failed jobs can re-run without duplicates

**How it works**:
```sql
-- Before offload: Read watermark
SELECT last_offload_timestamp FROM offload_watermarks WHERE table_name = 'bronze_trades_binance';
-- Returns: 2026-02-11 10:00:00

-- During offload: Read only new data
SELECT * FROM bronze_trades_binance
WHERE exchange_timestamp > '2026-02-11 10:00:00'
AND exchange_timestamp <= now() - INTERVAL 5 MINUTE;
-- (5-minute buffer prevents TTL race)

-- After offload: Update watermark (ONLY if Iceberg write succeeds)
UPDATE offload_watermarks SET last_offload_timestamp = '2026-02-11 10:15:00' ...;
```

**Failure scenario (safe)**:
```
Run 1: Read watermark (10:00) → Read data (10:00-10:15) → Write to Iceberg → CRASH (watermark NOT updated)
Run 2: Read watermark (10:00, unchanged) → Read data (10:00-10:15 again) → Iceberg deduplicates → Update watermark to 10:15
```

---

## Monitoring

### 1. Prefect UI (Primary)

**URL**: http://localhost:4200

**What to check**:
- **Flow Runs** → `iceberg-offload-main` → Last 24 hours
- **Success rate**: Should be >95% (some failures acceptable, retries will handle)
- **Duration**: Typically 2-5 minutes per run
- **Task status**: Bronze (parallel), Silver (sequential), Gold (parallel)

**Green flags** ✅:
- Flow status: "Completed"
- All 9 tasks: "Completed"
- Duration: <10 minutes
- Retries: 0-1 (occasional retries normal)

**Red flags** 🔴:
- Flow status: "Failed" (after 3 retries)
- Task status: "Crashed" or "TimedOut"
- Duration: >15 minutes (blocking next run)
- Consecutive failures: >3

### 2. Watermark Table (ClickHouse)

**Query**:
```sql
-- Check last successful offload for all tables
SELECT
    table_name,
    last_offload_timestamp,
    last_successful_run,
    last_run_duration_seconds,
    status,
    failure_count,
    last_error_message
FROM offload_watermarks
ORDER BY last_successful_run DESC;
```

**Expected**:
- `last_successful_run`: Within last 20 minutes (15-min schedule + 5-min execution)
- `status`: 'success' for all tables
- `failure_count`: 0 (or low numbers <3)

**Alert if**:
- `last_successful_run` >30 minutes ago (2 consecutive failures)
- `failure_count` >3 (persistent failures)
- `status` = 'failed' and last_error_message not empty

### 3. Iceberg Table Row Counts

**Query** (via Spark SQL):
```sql
-- Check row counts in Iceberg tables
SELECT 'bronze_trades_binance' AS table_name, COUNT(*) AS row_count FROM cold.bronze_trades_binance
UNION ALL
SELECT 'bronze_trades_kraken', COUNT(*) FROM cold.bronze_trades_kraken
UNION ALL
SELECT 'silver_trades', COUNT(*) FROM cold.silver_trades
UNION ALL
SELECT 'gold_ohlcv_1m', COUNT(*) FROM cold.gold_ohlcv_1m
UNION ALL
SELECT 'gold_ohlcv_5m', COUNT(*) FROM cold.gold_ohlcv_5m
UNION ALL
SELECT 'gold_ohlcv_15m', COUNT(*) FROM cold.gold_ohlcv_15m
UNION ALL
SELECT 'gold_ohlcv_30m', COUNT(*) FROM cold.gold_ohlcv_30m
UNION ALL
SELECT 'gold_ohlcv_1h', COUNT(*) FROM cold.gold_ohlcv_1h
UNION ALL
SELECT 'gold_ohlcv_1d', COUNT(*) FROM cold.gold_ohlcv_1d;
```

**Expected**:
- Row counts increasing over time (~50K-100K rows per 15-minute cycle for Bronze)
- Silver ≈ Bronze total (unified view)
- Gold < Silver (aggregated candles)

**Alert if**:
- Row counts not increasing (offload stuck)
- Row counts decreasing (data loss - should never happen)

---

## Common Issues & Troubleshooting

### Issue 1: Offload Job Failed (Prefect UI shows "Failed")

**Symptoms**:
- Flow run status: "Failed"
- One or more tasks show "Failed" status
- Error message in logs

**Diagnosis**:
```bash
# Check Prefect logs
docker logs k2-prefect-agent --tail 200

# Check Spark logs
docker logs k2-spark-iceberg --tail 500

# Check specific error in watermark table
docker exec clickhouse-server clickhouse-client --query \
  "SELECT table_name, last_error_message FROM offload_watermarks WHERE status = 'failed'"
```

**Common causes**:
1. **ClickHouse connection timeout**
   - **Error**: `Connection refused` or `Read timed out`
   - **Fix**: Check ClickHouse is running (`docker ps | grep clickhouse`)
   - **Retry**: Prefect will auto-retry (3 attempts with 60s delay)

2. **Iceberg table not found**
   - **Error**: `Table cold.bronze_trades_binance not found`
   - **Fix**: Run DDL scripts to create tables
   ```bash
   docker exec k2-spark-iceberg /home/iceberg/ddl/00-run-all-ddl.sh
   ```

3. **Watermark not initialized**
   - **Error**: `Watermark not found for table: bronze_trades_binance`
   - **Fix**: Run watermark initialization
   ```bash
   docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=... \
     -f /home/iceberg/ddl/watermarks.sql
   ```

4. **Out of memory (Spark)**
   - **Error**: `OutOfMemoryError: Java heap space`
   - **Fix**: Increase Spark memory in docker-compose.yml (currently 4GB limit)

### Issue 2: Watermark Not Updating (Stuck)

**Symptoms**:
- `last_successful_run` timestamp not advancing
- Same watermark timestamp for >1 hour
- Prefect shows "Completed" but watermark unchanged

**Diagnosis**:
```bash
# Check if offload is actually writing data
docker exec k2-spark-iceberg spark-sql \
  --conf spark.sql.catalog.demo=... \
  -e "SELECT COUNT(*) FROM cold.bronze_trades_binance"

# Compare ClickHouse vs Iceberg row counts
# (If Iceberg count not growing, write path broken)
```

**Fix**:
- **If Spark logs show "No new data to offload"**: Normal - low volume period
- **If Spark logs show write errors**: Check Iceberg warehouse directory permissions
  ```bash
  docker exec k2-spark-iceberg ls -la /home/iceberg/warehouse/cold/
  ```

### Issue 3: Prefect Agent Not Running

**Symptoms**:
- Scheduled flows not executing
- Prefect UI shows "No agents available"
- Flow runs stuck in "Scheduled" state

**Diagnosis**:
```bash
# Check agent status
docker ps | grep prefect-agent

# Check agent logs
docker logs k2-prefect-agent --tail 50
```

**Fix**:
```bash
# Restart agent
docker restart k2-prefect-agent

# Verify agent connected
docker logs k2-prefect-agent | grep "Connected to"
# Should see: "Connected to Prefect server at http://prefect-server:4200/api"
```

### Issue 4: Flow Running for >15 Minutes (Blocking Next Run)

**Symptoms**:
- Flow duration >15 minutes
- Next scheduled run delayed
- Prefect shows multiple "Running" flows

**Diagnosis**:
```bash
# Check Spark job status
docker exec k2-spark-iceberg curl http://localhost:4040/api/v1/applications

# Check which task is slow
# (Prefect UI → Flow Run → Task durations)
```

**Fix**:
- **If Bronze layer slow**: Check ClickHouse query performance
- **If Silver layer slow**: Bronze dependency might be blocking
- **If Gold layer slow**: Check if 6 parallel tasks overwhelming Spark
  - Reduce parallelism in `iceberg_offload_flow.py` (ConcurrentTaskRunner config)

---

## Manual Operations

### Manually Trigger Offload (Testing)

```bash
# Trigger via Prefect CLI
docker exec k2-prefect-agent prefect deployment run \
  iceberg-offload-main/iceberg-offload-15min

# Monitor execution
docker logs k2-spark-iceberg --follow
```

### Reset Watermark (Re-offload from Beginning)

**⚠️ WARNING**: This will re-read ALL data from ClickHouse. Use only for testing or recovery.

```sql
-- Reset watermark for specific table
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
VALUES ('bronze_trades_binance', '1970-01-01 00:00:00', 0, 'initialized');

-- Or reset to specific timestamp
INSERT INTO offload_watermarks (table_name, last_offload_timestamp, last_offload_max_sequence, status)
VALUES ('bronze_trades_binance', '2026-02-11 00:00:00', 0, 'initialized');
```

### Check Data Consistency (Audit)

```sql
-- Compare ClickHouse vs Iceberg row counts
-- (Should match within 15-minute window)

-- ClickHouse (source)
SELECT 'bronze_trades_binance' AS table, COUNT(*) FROM bronze_trades_binance;

-- Iceberg (target) - run via Spark SQL
SELECT 'bronze_trades_binance' AS table, COUNT(*) FROM cold.bronze_trades_binance;
```

---

## Performance Tuning

### Current Resource Profile

| Component | CPU | RAM | Duration | Frequency |
|-----------|-----|-----|----------|-----------|
| Bronze (each) | 1 core | 500 MB | 30-60s | Every 15min |
| Silver | 1 core | 500 MB | 30-60s | Every 15min |
| Gold (each) | 0.5 core | 300 MB | 20-40s | Every 15min |
| **Total** | 2 cores | 4 GB | 2-5min | Every 15min |

### Tuning Recommendations

**If offload runs >10 minutes**:
1. Increase Spark resources in docker-compose.yml:
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '4.0'  # Increase from 2.0
         memory: 8G   # Increase from 4G
   ```

2. Reduce parallelism in Bronze/Gold:
   ```python
   # In iceberg_offload_flow.py
   task_runner=ConcurrentTaskRunner(max_workers=2)  # Reduce from unlimited
   ```

**If ClickHouse queries slow**:
- Check ClickHouse indexes on `exchange_timestamp` column
- Ensure ClickHouse has enough memory (current: 3GB)

---

## Alerts & Notifications

### Recommended Alerts

1. **Offload Failure (Critical)**
   - Condition: `failure_count > 3` for any table
   - Action: Page on-call engineer

2. **Offload Lag (Warning)**
   - Condition: `last_successful_run > 30 minutes ago`
   - Action: Slack notification

3. **Data Loss (Critical)**
   - Condition: Iceberg row count decreasing
   - Action: Page on-call, stop offload jobs

### Prefect Notifications (Future Enhancement)

```python
# Add to iceberg_offload_flow.py
from prefect.blocks.notifications import SlackWebhook

@flow(on_failure=[SlackWebhook.load("k2-alerts")])
def iceberg_offload_main():
    ...
```

---

## Recovery Procedures

### Scenario 1: Complete Offload Failure (All Tables)

**Steps**:
1. Check Prefect server status: `docker ps | grep prefect-server`
2. Check Spark status: `docker ps | grep spark-iceberg`
3. Check ClickHouse status: `docker ps | grep clickhouse`
4. Restart failed services
5. Manually trigger offload to catch up
6. Monitor next 2-3 cycles for stability

### Scenario 2: Single Table Failure (Others Succeeding)

**Steps**:
1. Check table-specific watermark: `SELECT * FROM offload_watermarks WHERE table_name = '...'`
2. Check Iceberg table exists: `SHOW TABLES IN cold LIKE '...'`
3. If table missing: Run DDL to recreate
4. If watermark issue: Reset watermark to known good timestamp
5. Manually trigger offload for that layer (Bronze/Silver/Gold)

---

## Maintenance Schedule

| Task | Frequency | Command |
|------|-----------|---------|
| Check watermarks | Daily | `SELECT * FROM offload_watermarks` |
| Review Prefect logs | Daily | Prefect UI → Flow Runs → Last 24h |
| Audit row counts | Weekly | Compare ClickHouse vs Iceberg |
| Clean old flow runs | Monthly | Prefect UI → Delete old runs |
| Review Spark logs | As needed | `docker logs k2-spark-iceberg` |

---

## Contact & Escalation

**Primary**: Check Prefect UI for error details
**Secondary**: Check Spark logs for technical details
**Escalation**: If failure_count >5, engage platform team

**Documentation**:
- [ADR-014: Spark-Based Iceberg Offload](../../decisions/platform-v2/ADR-014-spark-based-iceberg-offload.md)
- [Phase 5 Implementation Plan](../../phases/v2/phase-5-cold-tier-restructure/PHASE-5-IMPLEMENTATION-PLAN.md)
