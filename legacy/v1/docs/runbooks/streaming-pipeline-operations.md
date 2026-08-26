# Streaming Pipeline Operations Runbook

**Severity**: All levels (Critical to Low)
**Last Updated**: 2026-01-20
**Maintained By**: Data Engineering Team
**On-Call Escalation**: #data-engineering-oncall

---

## Table of Contents

1. [Quick Reference](#quick-reference)
2. [Container Health Checks](#container-health-checks)
3. [Incident Response Procedures](#incident-response-procedures)
4. [Performance Troubleshooting](#performance-troubleshooting)
5. [Data Quality Issues](#data-quality-issues)
6. [Recovery Procedures](#recovery-procedures)
7. [Maintenance Operations](#maintenance-operations)

---

## Quick Reference

### Critical Commands

```bash
# Check all streaming job statuses
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.State}}" | grep -E "bronze|silver"

# View recent logs (last 50 lines)
docker logs --tail 50 k2-bronze-binance-stream
docker logs --tail 50 k2-silver-binance-transformation

# Check resource usage
docker stats --no-stream | grep -E "bronze|silver"

# Restart a specific job (preserves checkpoint)
docker restart k2-bronze-binance-stream

# Check Spark UI
# URL: http://localhost:8090
# Check: Running Applications → Job details
```

### Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Spark Master UI | http://localhost:8090 | Job monitoring |
| Spark Worker UI | http://localhost:8091 | Executor status |
| Schema Registry | http://localhost:8081 | Schema versions |
| MinIO Console | http://localhost:9001 | S3 storage |
| Kafka UI (if enabled) | http://localhost:8080 | Topic monitoring |

### Container Names

| Container | Type | Purpose |
|-----------|------|---------|
| k2-bronze-binance-stream | Bronze | Kafka → Bronze (Binance) |
| k2-bronze-kraken-stream | Bronze | Kafka → Bronze (Kraken) |
| k2-silver-binance-transformation | Silver | Bronze → Silver (Binance) |
| k2-silver-kraken-transformation | Silver | Bronze → Silver (Kraken) |

---

## Container Health Checks

### Check All Services Status

```bash
#!/bin/bash
# streaming_health_check.sh

echo "=== Streaming Pipeline Health Check ==="
echo ""

# Check container status
echo "1. Container Status:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "NAME|bronze|silver"
echo ""

# Check resource usage
echo "2. Resource Usage:"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}" | grep -E "NAME|bronze|silver"
echo ""

# Check Spark applications
echo "3. Spark Applications:"
curl -s http://localhost:8090/json/ | python3 -c "
import sys, json
data = json.load(sys.stdin)
apps = data.get('activeapps', [])
print(f'Active Spark jobs: {len(apps)}')
for app in apps:
    print(f'  - {app[\"name\"]}: {app[\"cores\"]} cores, {app[\"memoryperslave\"]}MB memory')
" 2>/dev/null || echo "Spark UI not accessible"
echo ""

# Check Kafka topics
echo "4. Kafka Topics:"
docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list 2>/dev/null | grep -E "binance|kraken" | wc -l
echo "  crypto topics found"
echo ""

# Check Schema Registry
echo "5. Schema Registry:"
curl -s http://localhost:8081/subjects 2>/dev/null | python3 -c "
import sys, json
subjects = json.load(sys.stdin)
print(f'  Registered schemas: {len(subjects)}')
" || echo "  Schema Registry not accessible"

echo ""
echo "=== Health Check Complete ==="
```

**Usage:**
```bash
chmod +x streaming_health_check.sh
./streaming_health_check.sh
```

**Expected Healthy Output:**
- All 4 streaming containers: "Up X minutes/hours"
- CPU: <50%, Memory: <80%
- 4 active Spark applications
- 8 Kafka topics
- 12 registered schemas

---

## Incident Response Procedures

### Incident #1: Bronze Job Not Ingesting Data

**Severity**: 🔴 Critical (affects entire pipeline)

**Symptoms:**
- Bronze table has no new records
- Container shows "Up" but no processing logs
- Zero CPU/memory usage

**Diagnosis:**

**Step 1: Check Kafka topic has data**
```bash
docker exec k2-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic market.crypto.trades.binance.raw \
  --max-messages 5 \
  --timeout-ms 10000
```

**Expected**: See 5 messages within 10 seconds
**If no messages**: Producer is down, escalate to #producers team

**Step 2: Check container logs for errors**
```bash
docker logs k2-bronze-binance-stream 2>&1 | grep -i error | tail -20
```

**Common errors:**
- `Connection refused: kafka:29092` → Kafka not accessible
- `Topic does not exist` → Topic name typo or not created
- `Authentication failed` → Kafka ACLs issue (unlikely in dev)

**Step 3: Check checkpoint location**
```bash
docker exec k2-minio mc ls local/warehouse/checkpoints/bronze-binance/ 2>/dev/null
```

**Expected**: See `commits/`, `offsets/`, `metadata` directories
**If empty or missing**: Checkpoint initialization failed

**Resolution:**

**Option A: Restart container (preserves checkpoint)**
```bash
docker restart k2-bronze-binance-stream
docker logs -f k2-bronze-binance-stream
# Watch for "Streaming job RUNNING" message
```

**Option B: Reset checkpoint (if corrupted)**
```bash
# WARNING: This resets to latest offset (data loss possible)
docker exec k2-minio mc rm -r --force local/warehouse/checkpoints/bronze-binance/
docker restart k2-bronze-binance-stream
```

**Option C: Rebuild job (if code issue)**
```bash
docker compose up -d --force-recreate bronze-binance-stream
```

**Verification:**
```sql
-- Check Bronze has new data (run in spark-sql or DBeaver)
SELECT COUNT(*) FROM iceberg.market_data.bronze_binance_trades
WHERE ingestion_date = CURRENT_DATE;
-- Expected: Growing count over time
```

---

### Incident #2: Silver Job Falling Behind

**Severity**: 🟡 Warning (impacts latency SLA)

**Symptoms:**
- Logs show: "Current batch is falling behind. Trigger interval is 30000ms, but spent XXXXXms"
- Processing time > trigger interval consistently
- Bronze → Silver lag exceeds 5 minutes

**Diagnosis:**

**Step 1: Check processing time trend**
```bash
docker logs k2-silver-binance-transformation 2>&1 | \
  grep "falling behind" | \
  tail -10
```

**Expected**: Occasional messages (< 10% of batches)
**If frequent**: System consistently overloaded

**Step 2: Check resource usage**
```bash
docker stats --no-stream k2-silver-binance-transformation
```

**Expected**: CPU <80%, Memory <80%
**If exceeded**: Resource-constrained

**Step 3: Check batch processing rate**
```bash
docker logs k2-silver-binance-transformation 2>&1 | \
  grep -E "Batch: [0-9]+" | \
  tail -5
```

**Resolution:**

**Short-term fix: Increase trigger interval**
```python
# Edit: src/k2/spark/jobs/streaming/silver_binance_transformation_v3.py
.trigger(processingTime="60 seconds")  # Was: 30 seconds
```

```bash
docker compose up -d --force-recreate silver-binance-transformation
```

**Medium-term fix: Increase resources**
```yaml
# Edit: docker-compose.v1.yml
silver-binance-transformation:
  deploy:
    resources:
      limits:
        cpus: '4.0'    # Was: 2.5
        memory: 4GiB   # Was: 1.758GiB
```

```bash
docker compose up -d silver-binance-transformation
```

**Long-term fix: Optimize validation logic**
- Profile validation UDFs
- Replace UDFs with native Spark functions where possible
- Add predicate pushdown

**Verification:**
```bash
# Monitor for 5 minutes, should not see "falling behind"
docker logs -f k2-silver-binance-transformation 2>&1 | grep -E "falling behind|Batch:"
```

---

### Incident #3: High DLQ Rate (Data Quality Issue)

**Severity**: 🟠 High (data quality impact)

**Symptoms:**
- DLQ table growing rapidly
- DLQ rate > 1% (acceptable: < 0.1%)
- Silver table row count lower than expected

**Diagnosis:**

**Step 1: Check DLQ rate**
```sql
-- Run in spark-sql or DBeaver
SELECT
    bronze_source,
    COUNT(*) as dlq_records,
    COUNT(*) * 100.0 / (
        SELECT COUNT(*) FROM silver_binance_trades WHERE validation_timestamp::DATE = CURRENT_DATE
    ) as dlq_rate_percent
FROM silver_dlq_trades
WHERE dlq_date = CURRENT_DATE
GROUP BY bronze_source;
```

**Expected**: dlq_rate_percent < 0.1%
**If > 1%**: Significant data quality issue

**Step 2: Identify error patterns**
```sql
SELECT
    error_type,
    error_reason,
    COUNT(*) as occurrence_count
FROM silver_dlq_trades
WHERE dlq_date = CURRENT_DATE
GROUP BY error_type, error_reason
ORDER BY occurrence_count DESC
LIMIT 10;
```

**Common patterns:**
- `price_validation` → Price = 0 or negative
- `timestamp_validation` → Timestamp in future or too old
- `deserialization_failed_or_null_fields` → Schema mismatch

**Step 3: Sample failing records**
```sql
SELECT
    error_reason,
    error_timestamp,
    bronze_source,
    kafka_offset,
    schema_id,
    LENGTH(raw_record) as record_size_bytes
FROM silver_dlq_trades
WHERE dlq_date = CURRENT_DATE
    AND error_type = 'price_validation'
LIMIT 5;
```

**Resolution:**

**Scenario A: Producer schema issue**
```bash
# 1. Check Schema Registry for recent schema changes
curl -s http://localhost:8081/subjects/market.crypto.trades.binance.raw-value/versions | python3 -m json.tool

# 2. Get latest schema version details
LATEST_VERSION=$(curl -s http://localhost:8081/subjects/market.crypto.trades.binance.raw-value/versions/latest | python3 -c "import sys, json; print(json.load(sys.stdin)['version'])")
curl -s http://localhost:8081/subjects/market.crypto.trades.binance.raw-value/versions/$LATEST_VERSION | python3 -m json.tool

# 3. If schema mismatch: Update Silver transformation Avro schema
# Edit: silver_binance_transformation_v3.py BINANCE_RAW_SCHEMA

# 4. Restart Silver job
docker restart k2-silver-binance-transformation
```

**Scenario B: Validation rule too strict**
```python
# Edit: src/k2/spark/validation/trade_validation.py
# Example: Relax timestamp validation

# Old (too strict):
& (col("timestamp") < current_time_micros)  # Rejects future timestamps

# New (allow 1 minute clock skew):
& (col("timestamp") < (current_time_micros + 60 * 1000000))  # 1 min grace period
```

**Scenario C: Bad producer data (transient)**
```bash
# Wait 5-10 minutes, check if DLQ rate drops
# If transient: Ignore (DLQ captured for audit)
# If persistent: Escalate to #producers team
```

**Verification:**
```sql
-- Monitor DLQ rate over next hour
SELECT
    DATE_TRUNC('hour', error_timestamp) as error_hour,
    COUNT(*) as dlq_count
FROM silver_dlq_trades
WHERE dlq_date = CURRENT_DATE
GROUP BY error_hour
ORDER BY error_hour DESC
LIMIT 24;
```

---

### Incident #4: Schema Registry Slow Startup

**Severity**: 🟡 Warning (impacts restart times)

**Symptoms:**
- Schema Registry takes >2 minutes to become healthy
- Dependent services (streaming jobs) delayed startup
- Progressive slowdown with each restart

**Diagnosis:**

**Step 1: Check _schemas topic size**
```bash
docker exec k2-kafka kafka-log-dirs \
  --bootstrap-server localhost:9092 \
  --describe \
  --topic-list _schemas 2>/dev/null | grep size
```

**Expected**: < 10MB
**If > 50MB**: Topic not compacting properly

**Step 2: Check _schemas topic config**
```bash
docker exec k2-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --describe \
  --topic _schemas 2>/dev/null | grep -i config
```

**Expected**: `cleanup.policy=compact`, `segment.ms=3600000`, `min.cleanable.dirty.ratio=0.01`

**Resolution:**

**Apply aggressive compaction** (already documented in /tmp/SCHEMA_REGISTRY_PERFORMANCE_FIX.md):
```bash
docker exec k2-kafka kafka-configs --bootstrap-server localhost:9092 \
    --entity-type topics \
    --entity-name _schemas \
    --alter \
    --add-config cleanup.policy=compact,segment.ms=3600000,min.cleanable.dirty.ratio=0.01,delete.retention.ms=86400000
```

**Trigger manual compaction:**
```bash
# Force log cleaner to run immediately
docker exec k2-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --alter \
  --topic _schemas \
  --config segment.ms=60000  # 1 minute (temporary)

# Wait 2 minutes for compaction

# Restore original setting
docker exec k2-kafka kafka-topics \
  --bootstrap-server localhost:9092 \
  --alter \
  --topic _schemas \
  --config segment.ms=3600000  # 1 hour
```

**Verification:**
```bash
# Restart Schema Registry, should be healthy in <60s
docker restart k2-schema-registry-1
docker logs -f k2-schema-registry-1 2>&1 | grep -E "Server started|Started|healthy"
```

---

### Incident #5: Checkpoint Corruption

**Severity**: 🟠 High (job cannot restart)

**Symptoms:**
- Job fails on startup with: "Cannot recover from checkpoint"
- Logs show: "Checkpoint metadata is corrupt"
- Job stuck in restart loop

**Diagnosis:**

**Step 1: Check checkpoint directory**
```bash
docker exec k2-minio mc ls -r local/warehouse/checkpoints/silver-binance/ 2>/dev/null
```

**Expected**: See organized directory structure:
```
commits/
offsets/
metadata
sources/
state/
```

**If missing or incomplete**: Checkpoint corrupted

**Step 2: Check for partial writes**
```bash
docker exec k2-minio mc ls local/warehouse/checkpoints/silver-binance/commits/ 2>/dev/null | tail -5
```

**If *.tmp files present**: Incomplete checkpoint write

**Resolution:**

**Option A: Restore from previous checkpoint** (Preferred)
```bash
# List checkpoint commits
docker exec k2-minio mc ls local/warehouse/checkpoints/silver-binance/commits/ 2>/dev/null

# Identify last good commit (e.g., batch 42)
# Delete corrupted commit (e.g., batch 43)
docker exec k2-minio mc rm local/warehouse/checkpoints/silver-binance/commits/43

# Restart job (will resume from batch 42)
docker restart k2-silver-binance-transformation
```

**Option B: Reset checkpoint** (Data reprocessing required)
```bash
# WARNING: Job will restart from latest offset (historical data not reprocessed)

# Backup existing checkpoint
docker exec k2-minio mc cp -r \
  local/warehouse/checkpoints/silver-binance/ \
  local/warehouse/checkpoints/silver-binance.backup.$(date +%Y%m%d-%H%M%S)/

# Delete corrupted checkpoint
docker exec k2-minio mc rm -r --force local/warehouse/checkpoints/silver-binance/

# Restart job (creates new checkpoint from latest)
docker restart k2-silver-binance-transformation
```

**Option C: Replay from Bronze** (Full data consistency)
```bash
# 1. Stop Silver job
docker stop k2-silver-binance-transformation

# 2. Delete checkpoint
docker exec k2-minio mc rm -r --force local/warehouse/checkpoints/silver-binance/

# 3. Truncate Silver table (if full replay desired)
docker exec k2-spark-iceberg spark-sql -e "TRUNCATE TABLE iceberg.market_data.silver_binance_trades;"

# 4. Update job to start from earliest offset
# Edit: silver_binance_transformation_v3.py
# Change: .option("startingOffsets", "latest") → .option("startingOffsets", "earliest")

# 5. Restart job
docker start k2-silver-binance-transformation

# 6. Monitor replay progress
docker logs -f k2-silver-binance-transformation
```

**Verification:**
```bash
# Job should start successfully
docker logs k2-silver-binance-transformation 2>&1 | grep "RUNNING"

# Check checkpoint recreated
docker exec k2-minio mc ls -r local/warehouse/checkpoints/silver-binance/ 2>/dev/null | head -10
```

---

## Performance Troubleshooting

### Slow Query Performance (Iceberg Tables)

**Symptoms:**
- Queries take >10 seconds for simple aggregations
- Table scans reading excessive data
- Partition pruning not working

**Diagnosis:**

**Step 1: Check table metadata**
```sql
-- Get table statistics
DESCRIBE EXTENDED iceberg.market_data.silver_binance_trades;

-- Check partition distribution
SELECT exchange_date, COUNT(*) as records_per_day
FROM iceberg.market_data.silver_binance_trades
GROUP BY exchange_date
ORDER BY exchange_date DESC
LIMIT 10;
```

**Step 2: Check file count and sizes**
```bash
docker exec k2-minio mc du local/warehouse/market_data/silver_binance_trades/data/
```

**Expected**:
- File size: ~128MB (target-file-size-bytes)
- File count: Reasonable (not thousands of small files)

**If many small files (<10MB)**: Compaction needed

**Resolution:**

**Compact Iceberg table:**
```sql
-- Run in spark-sql
CALL iceberg.system.rewrite_data_files(
  table => 'market_data.silver_binance_trades',
  options => map(
    'target-file-size-bytes', '134217728',  -- 128MB
    'min-file-size-bytes', '10485760'       -- 10MB
  )
);

-- Check result
SELECT * FROM iceberg.market_data.silver_binance_trades.files;
```

**Expire old snapshots:**
```sql
-- Remove snapshots older than 7 days
CALL iceberg.system.expire_snapshots(
  table => 'market_data.silver_binance_trades',
  older_than => TIMESTAMP '2026-01-13 00:00:00',
  retain_last => 10
);
```

**Add Z-ordering (for range queries):**
```sql
-- Optimize for timestamp-based queries
CALL iceberg.system.rewrite_data_files(
  table => 'market_data.silver_binance_trades',
  strategy => 'sort',
  sort_order => 'timestamp,symbol'
);
```

---

## Recovery Procedures

### Full Pipeline Recovery (Disaster Recovery)

**Scenario**: All streaming jobs down, need to restore pipeline

**Step 1: Verify infrastructure**
```bash
# Check all dependencies
docker ps | grep -E "kafka|minio|iceberg|spark"
# Expected: All core services "Up" and healthy
```

**Step 2: Restart in dependency order**
```bash
# 1. Bronze jobs first (no dependencies on Silver/Gold)
docker restart k2-bronze-binance-stream
docker restart k2-bronze-kraken-stream

# Wait 30 seconds for checkpoint recovery
sleep 30

# Verify Bronze running
docker logs k2-bronze-binance-stream 2>&1 | grep "RUNNING"

# 2. Silver jobs (depend on Bronze tables)
docker restart k2-silver-binance-transformation
docker restart k2-silver-kraken-transformation

# Wait 30 seconds
sleep 30

# Verify Silver running
docker logs k2-silver-binance-transformation 2>&1 | grep "RUNNING"

# 3. Gold job (depends on both Silver tables) - implement later
# docker restart k2-gold-aggregation
```

**Step 3: Verify end-to-end**
```sql
-- Check data freshness (should be within 5 minutes)
SELECT
    MAX(ingestion_timestamp) as latest_bronze_timestamp,
    CURRENT_TIMESTAMP as now,
    TIMESTAMPDIFF(SECOND, MAX(ingestion_timestamp), CURRENT_TIMESTAMP) as lag_seconds
FROM iceberg.market_data.bronze_binance_trades;

-- Expected lag_seconds: < 300 (5 minutes)
```

**Step 4: Monitor for 15 minutes**
```bash
# Create monitoring script
watch -n 30 './streaming_health_check.sh'
```

---

## Maintenance Operations

### Scheduled Maintenance: Checkpoint Cleanup

**Frequency**: Weekly
**Duration**: 15 minutes
**Impact**: None (jobs continue running)

**Purpose**: Remove old checkpoint data to prevent S3 storage bloat

```bash
#!/bin/bash
# checkpoint_cleanup.sh

echo "=== Checkpoint Cleanup ==="

# List checkpoint sizes
echo "Current checkpoint sizes:"
docker exec k2-minio mc du local/warehouse/checkpoints/ 2>/dev/null

# Archive old checkpoints (>30 days) to backup bucket
CUTOFF_DATE=$(date -d "30 days ago" +%Y-%m-%d)
echo "Archiving checkpoints older than $CUTOFF_DATE"

# Backup to archive bucket (create if not exists)
docker exec k2-minio mc mb local/checkpoint-archive/ 2>/dev/null

# Move old checkpoints (example for bronze-binance)
docker exec k2-minio mc cp -r \
  local/warehouse/checkpoints/bronze-binance/ \
  local/checkpoint-archive/bronze-binance.$(date +%Y%m%d)/ 2>/dev/null

echo "Cleanup complete"
```

**Schedule with cron:**
```bash
# Add to crontab
0 2 * * 0 /path/to/checkpoint_cleanup.sh >> /var/log/checkpoint_cleanup.log 2>&1
```

---

### Scheduled Maintenance: Iceberg Table Optimization

**Frequency**: Daily (off-peak hours)
**Duration**: 30-60 minutes
**Impact**: Minimal (read queries continue, writes may slow temporarily)

```sql
-- optimize_iceberg_tables.sql

-- 1. Compact small files (Bronze tables)
CALL iceberg.system.rewrite_data_files(
  table => 'market_data.bronze_binance_trades',
  options => map('target-file-size-bytes', '134217728')
);

-- 2. Expire old snapshots (Bronze - keep 7 days)
CALL iceberg.system.expire_snapshots(
  table => 'market_data.bronze_binance_trades',
  older_than => CURRENT_TIMESTAMP - INTERVAL '7' DAY,
  retain_last => 20
);

-- 3. Remove orphan files (cleanup failed writes)
CALL iceberg.system.remove_orphan_files(
  table => 'market_data.bronze_binance_trades',
  older_than => CURRENT_TIMESTAMP - INTERVAL '3' DAY
);

-- Repeat for other tables (Kraken, Silver)
```

**Schedule with cron:**
```bash
# Run via spark-submit daily at 3 AM
0 3 * * * docker exec k2-spark-iceberg spark-sql -f /opt/k2/sql/optimize_iceberg_tables.sql >> /var/log/iceberg_optimize.log 2>&1
```

---

## Related Documentation

- [Streaming Pipeline Performance Analysis](../STREAMING_PIPELINE_ANSWERS.md)
- [Schema Registry Performance Fix](/tmp/SCHEMA_REGISTRY_PERFORMANCE_FIX.md)
- [Iceberg Table Architecture](../../design/storage-layer.md)
- [Data Quality Validation](../../design/data-guarantees/consistency-model.md)

---

**Emergency Contact:**
- On-call engineer: #data-engineering-oncall
- Escalation: @data-engineering-manager
- Critical issues: Page via PagerDuty

**Last Updated**: 2026-01-20
**Next Review**: After Gold layer implementation
