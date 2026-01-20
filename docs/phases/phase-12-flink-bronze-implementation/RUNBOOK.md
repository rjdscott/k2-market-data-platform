# Phase 12: Flink Bronze Layer - Operations Runbook

**Version**: 1.0
**Last Updated**: 2026-01-20
**Status**: Implementation 95% Complete

---

## Quick Start

### Start Flink Cluster
```bash
# Start core infrastructure (if not running)
docker compose up -d kafka schema-registry-1 minio postgres iceberg-rest

# Start Flink cluster
docker compose up -d flink-jobmanager flink-taskmanager-1 flink-taskmanager-2

# Verify cluster health
curl http://localhost:8082  # Flink Web UI
curl http://localhost:8082/taskmanagers | jq '.taskmanagers | length'  # Should return 2
```

### Submit Bronze Jobs
```bash
# Submit Binance and Kraken Bronze ingestion jobs
docker compose up -d flink-bronze-binance-job flink-bronze-kraken-job

# Check job submission logs
docker logs k2-flink-bronze-binance-job
docker logs k2-flink-bronze-kraken-job
```

### Verify Jobs Running
```bash
# Check Flink Web UI
open http://localhost:8082

# Check jobs via API
curl -s http://localhost:8082/jobs | jq '.jobs[] | {id, status}'

# Check job metrics
curl -s http://localhost:8082/jobs/<JOB_ID>/metrics
```

---

## Architecture Overview

### Cluster Topology
```
┌─────────────────┐
│  JobManager     │  (1 CPU, 1GB) - Port 8082
│  Coordinates    │
│  Web UI         │
└────────┬────────┘
         │
    ┬────┴────┬
    │         │
┌───▼─────┐ ┌▼────────┐
│TaskMgr-1│ │TaskMgr-2│  (2 CPU, 2GB each)
│Binance  │ │Kraken   │
│2 slots  │ │2 slots  │
└─────────┘ └─────────┘

Total: 5 CPU, 5GB RAM
Checkpoints: MinIO s3a://flink/checkpoints
State Backend: RocksDB (incremental)
```

### Data Flow
```
Kafka (binance.raw)
  ↓
Flink Kafka Source (RAW format)
  ↓
Flink Transformation
  ↓
Iceberg Sink (bronze_binance_trades_flink)
  ↓
MinIO (s3a://warehouse/market_data/)
```

---

## Daily Operations

### Health Checks

**1. Check Container Status**
```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | grep flink
```

Expected output:
```
k2-flink-jobmanager      Up X minutes (healthy)
k2-flink-taskmanager-1   Up X minutes (healthy)
k2-flink-taskmanager-2   Up X minutes (healthy)
```

**2. Check Job Status**
```bash
curl -s http://localhost:8082/jobs | jq '.jobs[] | {id, status}'
```

Expected: 2 jobs with `"status": "RUNNING"`

**3. Check Checkpoint Health**
```bash
# Get job ID from Web UI or API
JOB_ID="<your-job-id>"
curl -s http://localhost:8082/jobs/$JOB_ID/checkpoints | jq '.latest'
```

Expected:
- `status`: "completed"
- `duration`: < 10000 (ms)
- Success rate: > 99%

**4. Check Data Ingestion**
```bash
# Query Iceberg table
docker exec -it k2-query-api duckdb -c "
INSTALL iceberg;
LOAD iceberg;
SELECT COUNT(*), MAX(ingestion_timestamp)
FROM iceberg_scan('s3://warehouse/market_data/bronze_binance_trades_flink');
"
```

### Monitoring Metrics

**Prometheus Endpoints**:
- JobManager: http://localhost:9091/metrics
- TaskManager-1: http://localhost:9091/metrics
- TaskManager-2: http://localhost:9091/metrics

**Key Metrics to Monitor**:
```promql
# Throughput
rate(flink_taskmanager_job_task_numRecordsInPerSecond[1m])

# Latency
flink_taskmanager_job_latency_source_id_operator_id_operator_subtask_index_latency

# Checkpoint duration
flink_jobmanager_job_lastCheckpointDuration

# Backpressure
flink_taskmanager_job_task_backPressuredTimeMsPerSecond
```

---

## Troubleshooting

### Problem: Jobs Not Appearing in Flink Web UI

**Symptoms**:
- `curl http://localhost:8082/jobs` returns `{"jobs": []}`
- Job submission containers exit successfully

**Root Cause**: SQL client in embedded mode doesn't keep jobs running after script finishes

**Solution** (choose one):

**Option A: Use STATEMENT SET (Recommended)**
```sql
-- Add to end of SQL file
EXECUTE STATEMENT SET BEGIN
  INSERT INTO market_data.bronze_binance_trades_flink
  SELECT ... FROM default_catalog.default_database.kafka_source_binance;
END;
```

**Option B: Submit via REST API**
```bash
# Create JSON payload
cat > /tmp/job.json <<'EOF'
{
  "sqlStatements": [
    "CREATE TABLE ...",
    "INSERT INTO ..."
  ]
}
EOF

# Submit to SQL Gateway
curl -X POST http://localhost:8083/v1/sessions/$SESSION_ID/statements \
  -H "Content-Type: application/json" \
  -d @/tmp/job.json
```

**Option C: Use Application Mode**
```bash
# Submit job in detached mode
/opt/flink/bin/flink run-application \
  -t remote \
  -Djobmanager.rpc.address=flink-jobmanager \
  /opt/flink/sql/bronze_binance_ingestion.sql
```

### Problem: ClassNotFoundException

**Symptoms**:
```
java.lang.ClassNotFoundException: org.apache.hadoop.conf.Configuration
java.lang.NoClassDefFoundError: org/apache/flink/...
```

**Root Cause**: Connector JARs not in classpath

**Solution**:
```bash
# Verify JARs in container
docker exec k2-flink-jobmanager ls -lh /opt/flink/lib/ | grep -E "iceberg|kafka|hadoop"

# Expected output:
# hadoop-client-api-3.3.4.jar
# hadoop-client-runtime-3.3.4.jar
# iceberg-flink-runtime-1.19-1.7.1.jar
# flink-sql-connector-kafka-3.3.0-1.19.jar
```

If missing, rebuild Docker image:
```bash
docker build -f Dockerfile.flink -t flink-k2:1.19.1 .
docker compose up -d --force-recreate flink-jobmanager flink-taskmanager-1 flink-taskmanager-2
```

### Problem: Checkpoint Failures

**Symptoms**:
- Jobs restarting frequently
- `curl http://localhost:8082/jobs/$JOB_ID/exceptions` shows checkpoint timeout

**Common Causes**:
1. **MinIO connectivity**: Check network, credentials
2. **State too large**: Increase checkpoint timeout
3. **Slow I/O**: Tune RocksDB settings

**Solution**:
```yaml
# In flink-conf.yaml
execution.checkpointing.timeout: 120s  # Increase from 60s
state.backend.rocksdb.thread.num: 4    # More threads for compaction
```

Restart cluster after changing config:
```bash
docker compose restart flink-jobmanager flink-taskmanager-1 flink-taskmanager-2
```

### Problem: Kafka Connection Refused

**Symptoms**:
```
Connection to kafka:29092 refused
Unable to retrieve any partitions for topic binance.raw
```

**Solution**:
```bash
# Check Kafka health
docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list

# Check if topics exist
docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --describe --topic binance.raw

# Check consumer group
docker exec k2-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --group flink-bronze-binance --describe
```

### Problem: Iceberg REST Catalog Unreachable

**Symptoms**:
```
Could not connect to http://iceberg-rest:8181
```

**Solution**:
```bash
# Check Iceberg REST health
curl http://localhost:8181/v1/config

# Check network connectivity from Flink
docker exec k2-flink-jobmanager curl -f http://iceberg-rest:8181/v1/config
```

---

## Maintenance Tasks

### Daily

- [ ] Check job status (Flink Web UI)
- [ ] Monitor checkpoint success rate (> 99%)
- [ ] Check logs for exceptions
- [ ] Verify data freshness (MAX(ingestion_timestamp) < 5 minutes ago)

### Weekly

- [ ] Review resource usage (`docker stats`)
- [ ] Check disk usage (MinIO, RocksDB state)
- [ ] Review Flink metrics in Grafana
- [ ] Test rollback procedure (1x per week)

### Monthly

- [ ] Update Flink connectors (if new versions available)
- [ ] Review and optimize checkpoint settings
- [ ] Archive old savepoints (> 30 days)
- [ ] Review Iceberg table statistics

---

## Rollback Procedure

**If Flink Bronze fails, rollback to Spark Bronze** (< 5 minutes):

```bash
# 1. Stop Flink Bronze jobs
docker compose stop flink-bronze-binance-job flink-bronze-kraken-job

# 2. Restart Spark Bronze jobs (if stopped)
docker compose up -d bronze-binance-stream bronze-kraken-stream

# 3. Revert Silver layer to read from Spark Bronze
# (If Silver was already migrated to Flink tables)
docker compose exec silver-binance-transformation \
  spark-submit ... --conf spark.sql.catalog.iceberg.table=bronze_binance_trades

# 4. Verify pipeline working
docker logs bronze-binance-stream | tail -20
```

---

## Performance Tuning

### Increase Throughput

**1. Increase Parallelism**
```yaml
# flink-conf.yaml
parallelism.default: 4  # Increase from 2

# Requires more task slots
taskmanager.numberOfTaskSlots: 4  # Increase from 2
```

**2. Tune Kafka Consumer**
```sql
-- In SQL job definition
'properties.fetch.min.bytes' = '1048576',  -- 1MB
'properties.max.poll.records' = '1000'
```

**3. Increase Checkpoint Interval**
```yaml
# flink-conf.yaml
execution.checkpointing.interval: 30s  # Increase from 10s (if latency acceptable)
```

### Reduce Latency

**1. Decrease Checkpoint Interval**
```yaml
execution.checkpointing.interval: 5s  # Decrease from 10s
```

**2. Tune Iceberg Writer**
```sql
-- In Iceberg table WITH clause
'write.target-file-size-bytes' = '67108864'  -- 64MB (smaller files, faster commits)
```

**3. Increase Resources**
```yaml
# docker-compose.yml
taskmanager.memory.process.size: 3072m  # Increase from 2048m
```

---

## Backup & Recovery

### Create Savepoint

```bash
# Get job ID
JOB_ID=$(curl -s http://localhost:8082/jobs | jq -r '.jobs[0].id')

# Trigger savepoint
curl -X POST http://localhost:8082/jobs/$JOB_ID/savepoints \
  -H "Content-Type: application/json" \
  -d '{"target-directory": "s3a://flink/savepoints", "cancel-job": false}'

# Check savepoint status
TRIGGER_ID="<from previous response>"
curl http://localhost:8082/jobs/$JOB_ID/savepoints/$TRIGGER_ID
```

### Restore from Savepoint

```bash
# Stop current job
docker compose stop flink-bronze-binance-job

# Start with savepoint
docker compose run --rm flink-bronze-binance-job \
  /opt/flink/bin/flink run \
  -s s3a://flink/savepoints/<savepoint-path> \
  /opt/flink/sql/bronze_binance_ingestion.sql
```

---

## Useful Commands

```bash
# View Flink logs
docker logs -f k2-flink-jobmanager
docker logs -f k2-flink-taskmanager-1

# Execute SQL in running cluster
docker exec -it k2-flink-jobmanager /opt/flink/bin/sql-client.sh

# Check resource usage
docker stats k2-flink-jobmanager k2-flink-taskmanager-1 k2-flink-taskmanager-2

# Restart cluster (preserves checkpoints)
docker compose restart flink-jobmanager flink-taskmanager-1 flink-taskmanager-2

# Full restart (fresh state)
docker compose down flink-jobmanager flink-taskmanager-1 flink-taskmanager-2
docker compose up -d flink-jobmanager flink-taskmanager-1 flink-taskmanager-2
```

---

## Contact & Escalation

- **Flink Web UI**: http://localhost:8082
- **Grafana Dashboards**: http://localhost:3000
- **Phase Documentation**: `docs/phases/phase-12-flink-bronze-implementation/`
- **Issue Tracker**: GitHub Issues

---

**Last Reviewed**: 2026-01-20
**Next Review**: 2026-02-20
