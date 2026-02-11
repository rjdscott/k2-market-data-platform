# Streaming Pipeline - Performance & Operational Questions

**Date**: 2026-01-20
**Author**: Staff Data Engineer Review
**Status**: Production Assessment

---

## 1. Data Volume & Performance Expectations

### Current Observed Metrics

**Binance Bronze Ingestion:**
- Configuration: `maxOffsetsPerTrigger: 10,000`, trigger: 10s
- **Theoretical capacity**: ~1,000 msgs/sec
- Resource usage: 0.16% CPU, 633MB memory (35% of limit)
- **Assessment**: Under-utilized, can handle higher volume

**Kraken Bronze Ingestion:**
- Configuration: `maxOffsetsPerTrigger: 1,000`, trigger: 30s
- **Theoretical capacity**: ~33 msgs/sec
- Resource usage: 0.09% CPU, 563MB memory (31% of limit)
- **Assessment**: Appropriately sized for lower volume

**Silver Binance Transformation:**
- Configuration: trigger: 30s
- **Observed processing time**:
  - First batch: 148 seconds (⚠️ **5x slower than trigger**)
  - Second batch: 40 seconds (⚠️ **1.3x slower than trigger**)
- Resource usage: 0.36% CPU, 671MB memory (37% of limit)
- **Assessment**: Falling behind, needs tuning

**Silver Kraken Transformation:**
- Resource usage: 0.23% CPU, 758MB memory (42% of limit)
- **Assessment**: Operating normally

### Recommended Target SLAs

Based on crypto market data best practices:

| Metric | Target | Rationale |
|--------|--------|-----------|
| **End-to-End Latency (p99)** | < 5 minutes | Acceptable for analytics (not HFT) |
| **Bronze Latency (p99)** | < 30 seconds | Near real-time ingestion |
| **Silver Latency (p99)** | < 2 minutes | Validation + transformation overhead |
| **Gold Latency (p99)** | < 3 minutes | Union + deduplication overhead |
| **Binance Throughput** | 500-1,000 msgs/sec | Typical for major crypto pairs |
| **Kraken Throughput** | 50-100 msgs/sec | Lower volume exchange |
| **Data Quality (DLQ rate)** | < 0.1% | High-quality validated data |

### Identified Performance Issue: Silver Binance Falling Behind

**Root Cause Analysis:**

1. **Initial Batch Overhead (148s)**: Spark streaming initialization includes:
   - Checkpoint recovery
   - Executor allocation
   - Table metadata loading from Iceberg
   - First batch typically 3-5x slower than steady-state

2. **Steady-State Processing (40s)**: Still exceeds 30s trigger
   - Avro deserialization overhead
   - Validation logic (UDFs or DataFrame operations)
   - Dual write streams (Silver + DLQ)
   - Iceberg commit latency

**Recommended Fixes (Priority Order):**

1. **Increase trigger interval**: 30s → 60s (immediate, no code change)
2. **Add watermarking**: Handle late data properly
3. **Optimize validation**: Use native Spark functions instead of UDFs where possible
4. **Tune Iceberg writes**: Enable `fanout-enabled: true` (already done ✓)

### Updated Configuration Recommendations

```python
# silver_binance_transformation_v3.py
.trigger(processingTime="60 seconds")  # Was: 30 seconds
.option("checkpointLocation", "s3a://warehouse/checkpoints/silver-binance/")
.option("fanout-enabled", "true")  # Already configured ✓

# Add watermarking for late data
bronze_df = bronze_df.withWatermark("kafka_timestamp", "5 minutes")
```

---

## 2. Monitoring & Alerting Strategy

### Current State

- ✅ **Container health monitoring**: Docker healthchecks configured
- ✅ **Spark UI**: Available at http://localhost:8090
- ❌ **Metrics emission**: No Prometheus/StatsD/Datadog integration
- ❌ **Centralized logging**: Using stdout (not aggregated)
- ❌ **Alert rules**: No automated alerting

### Recommended Monitoring Stack

**Option A: Prometheus + Grafana (Recommended)**
```yaml
# Add to docker-compose.v1.yml
prometheus:
  image: prom/prometheus:latest
  volumes:
    - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
  ports:
    - "9090:9090"

grafana:
  image: grafana/grafana:latest
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=admin
```

**Spark Metrics Configuration**:
```python
# spark_session.py additions
.config("spark.metrics.conf.*.sink.prometheus.class", "org.apache.spark.metrics.sink.PrometheusSink")
.config("spark.metrics.conf.*.sink.prometheus.port", "8080")
.config("spark.metrics.namespace", "${spark.app.name}")
```

**Option B: Datadog (Enterprise)**
- Add Datadog agent container
- Configure Spark metrics forwarding
- Use Datadog dashboards + alerts

### Key Metrics to Track

**Bronze Layer Metrics:**
```
# Throughput
bronze_binance_records_ingested_total
bronze_kraken_records_ingested_total

# Latency
bronze_binance_kafka_lag_seconds
bronze_kraken_kafka_lag_seconds
bronze_binance_processing_time_seconds
bronze_kraken_processing_time_seconds

# Resource
bronze_binance_memory_used_bytes
bronze_binance_cpu_usage_percent
```

**Silver Layer Metrics:**
```
# Data Quality
silver_binance_records_valid_total
silver_binance_records_invalid_total (DLQ)
silver_binance_dlq_rate_percent

# Processing
silver_binance_batch_processing_time_seconds
silver_binance_records_processed_per_second

# Validation Errors (by type)
silver_binance_validation_errors_by_type{error_type="price_validation"}
silver_binance_validation_errors_by_type{error_type="timestamp_validation"}
```

**Gold Layer Metrics:**
```
# Deduplication
gold_records_written_total
gold_duplicates_removed_total
gold_deduplication_rate_percent

# Partitioning
gold_partitions_created_total
gold_partition_size_bytes
```

**System Metrics:**
```
# Checkpoints
checkpoint_write_latency_seconds
checkpoint_size_bytes
checkpoint_recovery_time_seconds

# Iceberg
iceberg_commit_latency_seconds
iceberg_metadata_file_count
iceberg_data_file_count
```

### Recommended Alert Rules

**Critical Alerts (Page On-Call):**
```yaml
# Bronze lag exceeds 5 minutes
alert: BronzeBinanceLagCritical
expr: bronze_binance_kafka_lag_seconds > 300
severity: critical
action: Page on-call engineer

# DLQ rate exceeds 5% (data quality issue)
alert: SilverDLQRateHigh
expr: silver_binance_dlq_rate_percent > 5
severity: critical
action: Page data quality team

# Streaming job down
alert: StreamingJobDown
expr: up{job="streaming-pipeline"} == 0
severity: critical
action: Page on-call + auto-restart
```

**Warning Alerts (Slack Notification):**
```yaml
# Bronze lag exceeds 2 minutes
alert: BronzeBinanceLagWarning
expr: bronze_binance_kafka_lag_seconds > 120
severity: warning
action: Slack #data-engineering

# DLQ rate exceeds 1%
alert: SilverDLQRateElevated
expr: silver_binance_dlq_rate_percent > 1
severity: warning
action: Slack #data-quality

# Processing time exceeds trigger interval
alert: SilverProcessingBehind
expr: silver_binance_batch_processing_time_seconds > 60
severity: warning
action: Slack #data-engineering
```

### Implementation Priority

1. **Phase 1 (Now)**: Add structured logging with JSON output
2. **Phase 2 (Before Production)**: Prometheus + Grafana setup
3. **Phase 3 (Production)**: Alert rules + on-call rotation
4. **Phase 4 (Mature)**: Advanced metrics (data quality dashboards)

---

## 3. Dead Letter Queue Recovery Process

### Current DLQ Implementation

**Tables:**
- `silver_dlq_trades` (unified DLQ for all sources)

**DLQ Schema:**
```sql
CREATE TABLE IF NOT EXISTS iceberg.market_data.silver_dlq_trades (
    raw_record BINARY,              -- Original Bronze raw_bytes
    error_reason STRING,            -- Specific validation failure
    error_type STRING,              -- Error category
    error_timestamp TIMESTAMP,      -- When validation failed
    bronze_source STRING,           -- Source Bronze table
    kafka_offset BIGINT,            -- Bronze Kafka offset
    schema_id INT,                  -- Schema Registry ID (nullable)
    dlq_date DATE                   -- Partition key
)
PARTITIONED BY (dlq_date, bronze_source, error_type);
```

### DLQ Recovery Workflow

**Step 1: Monitor DLQ Rate**
```sql
-- Daily DLQ health check
SELECT
    bronze_source,
    error_type,
    COUNT(*) as error_count,
    COUNT(*) * 100.0 / (SELECT COUNT(*) FROM silver_binance_trades) as dlq_rate_percent
FROM silver_dlq_trades
WHERE dlq_date = CURRENT_DATE
GROUP BY bronze_source, error_type
ORDER BY error_count DESC;
```

**Step 2: Investigate Root Cause**
```sql
-- Analyze error patterns
SELECT
    error_reason,
    COUNT(*) as occurrence_count,
    MIN(error_timestamp) as first_occurrence,
    MAX(error_timestamp) as last_occurrence
FROM silver_dlq_trades
WHERE dlq_date >= CURRENT_DATE - INTERVAL '7' DAY
    AND bronze_source = 'bronze_binance_trades'
GROUP BY error_reason
ORDER BY occurrence_count DESC
LIMIT 20;
```

**Step 3: Fix & Replay**

**Option A: Fix Producer Schema (if schema issue)**
```bash
# 1. Fix producer schema
# 2. Re-register schema in Schema Registry
# 3. Bronze is unchanged (raw bytes preserved)
# 4. Replay DLQ records through Silver transformation

# Create replay job
docker exec k2-spark-iceberg spark-submit \
  --master spark://spark-master:7077 \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
  /opt/k2/src/k2/spark/jobs/batch/dlq_replay_job.py \
  --source-table silver_dlq_trades \
  --target-table silver_binance_trades \
  --date-range 2026-01-20 \
  --error-types price_validation,quantity_validation
```

**Option B: Fix Validation Logic (if logic bug)**
```bash
# 1. Fix validation logic in trade_validation.py
# 2. Restart Silver transformation job
# 3. Replay DLQ records (they'll pass new validation)
```

**Option C: Data Cleansing (if data quality issue)**
```sql
-- Manual inspection and correction
SELECT raw_record, error_reason, error_timestamp
FROM silver_dlq_trades
WHERE error_type = 'price_validation'
    AND dlq_date = '2026-01-20'
LIMIT 10;

-- After manual review, flag records for purge or replay
```

### DLQ Retention Policy

**Recommended Retention:**
```sql
-- Tier 1: Recent failures (keep 30 days for investigation)
-- Tier 2: Historical failures (archive to cold storage after 30 days)
-- Tier 3: Resolved failures (purge after 90 days)

-- Automated cleanup job (run daily)
DELETE FROM silver_dlq_trades
WHERE dlq_date < CURRENT_DATE - INTERVAL '90' DAY;

-- Archive to S3 before deletion
INSERT INTO silver_dlq_trades_archive
SELECT * FROM silver_dlq_trades
WHERE dlq_date < CURRENT_DATE - INTERVAL '30' DAY
    AND dlq_date >= CURRENT_DATE - INTERVAL '90' DAY;
```

### DLQ Observability Dashboard

**Key Metrics:**
- DLQ rate trend (7-day rolling average)
- Top 10 error reasons
- Error type distribution (pie chart)
- Hourly DLQ volume (detect spikes)
- Recovery success rate (replayed records)

---

## 4. Resource Allocation Verification

### Current Resource Allocation (docker-compose.yml)

**Bronze Jobs:**
```yaml
bronze-binance-stream:
  deploy:
    resources:
      limits:
        cpus: '2.5'
        memory: 1.758GiB
      reservations:
        cpus: '1.0'
        memory: 1.5GiB

bronze-kraken-stream:
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 1.758GiB
      reservations:
        cpus: '0.5'
        memory: 1.5GiB
```

**Silver Jobs:**
```yaml
silver-binance-transformation:
  deploy:
    resources:
      limits:
        cpus: '2.5'
        memory: 1.758GiB
      reservations:
        cpus: '1.0'
        memory: 1.5GiB

silver-kraken-transformation:
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 1.758GiB
      reservations:
        cpus: '0.5'
        memory: 1.5GiB
```

**Schema Registry:**
```yaml
schema-registry-1:
  deploy:
    resources:
      limits:
        cpus: '0.5'
        memory: 768MiB
```

### Observed Resource Usage

| Service | CPU Usage | Memory Usage | Utilization % |
|---------|-----------|--------------|---------------|
| bronze-binance-stream | 0.16% | 633MB | 35% memory, <1% CPU |
| bronze-kraken-stream | 0.09% | 563MB | 31% memory, <1% CPU |
| silver-binance-transformation | 0.36% | 671MB | 37% memory, <1% CPU |
| silver-kraken-transformation | 0.23% | 758MB | 42% memory, <1% CPU |

### Assessment

**Overall**: ✅ **Resource allocation is appropriate**

**Findings:**
1. **No OOM issues**: All containers well within memory limits
2. **CPU under-utilized**: <1% CPU suggests I/O-bound workloads (expected for streaming)
3. **Memory headroom**: 55-65% available for traffic spikes
4. **Reservation gap**: Limits set but reservations could be tightened

**Recommendations:**

**1. Add Memory Reservations (Enforce Minimum)**
```yaml
# Ensure containers get guaranteed resources
bronze-binance-stream:
  deploy:
    resources:
      reservations:
        memory: 1GiB  # Currently not set, should reserve ~60% of limit
```

**2. Adjust for Production Workload**

Current settings assume development/testing workload. For production:

```yaml
# Production-grade resource allocation
bronze-binance-stream:
  deploy:
    resources:
      limits:
        cpus: '4.0'        # 2.5 → 4.0 (handle spikes)
        memory: 3GiB       # 1.76 → 3GB (2x headroom)
      reservations:
        cpus: '2.0'        # Guarantee 2 cores
        memory: 2GiB       # Guarantee 2GB

silver-binance-transformation:
  deploy:
    resources:
      limits:
        cpus: '4.0'        # 2.5 → 4.0 (handle processing spikes)
        memory: 4GiB       # 1.76 → 4GB (deserialization overhead)
      reservations:
        cpus: '2.0'
        memory: 3GiB
```

**3. Auto-Scaling Considerations**

**Docker Swarm Mode** (if using Swarm):
```yaml
deploy:
  mode: replicated
  replicas: 2
  update_config:
    parallelism: 1
    delay: 10s
  rollback_config:
    parallelism: 1
    delay: 5s
  placement:
    constraints:
      - node.role == worker
```

**Kubernetes** (future migration):
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: silver-binance-transformation
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: silver-binance-transformation
  minReplicas: 2
  maxReplicas: 8
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

**4. Resource Contention Check**

Run load test to verify no contention:
```bash
# Generate high-volume test data
docker exec k2-producer-binance python -m k2.producers.binance_websocket --rate 5000

# Monitor for 10 minutes
watch -n 5 'docker stats --no-stream | grep -E "bronze|silver"'

# Check for:
# - Memory approaching limits (>90%)
# - CPU consistently >80%
# - Container restarts (OOM kills)
```

---

## Summary & Next Steps

### Answers to Questions

1. **Data Volume & Performance**:
   - ✅ Bronze jobs appropriately sized
   - ⚠️ Silver Binance falling behind (increase trigger to 60s)
   - Target SLAs defined (< 5min end-to-end)

2. **Monitoring Strategy**:
   - Prometheus + Grafana recommended
   - Key metrics defined
   - Alert rules specified (critical + warning levels)

3. **DLQ Recovery**:
   - 3-tier recovery workflow documented
   - 90-day retention policy
   - Replay job architecture defined

4. **Resource Allocation**:
   - ✅ Current allocation appropriate for dev/test
   - Production recommendations provided (2-3x current)
   - Auto-scaling options documented

### Implementation Priority

**Before Gold Layer:**
1. ✅ Increase Silver Binance trigger interval (30s → 60s)
2. ✅ Add structured logging
3. ✅ Create operational runbooks

**During Gold Layer:**
4. ✅ Implement watermarking for late data
5. ✅ Add Prometheus metrics emission

**After Gold Layer (Production Prep):**
6. Set up Prometheus + Grafana
7. Create alert rules
8. Implement DLQ replay job
9. Load testing + resource adjustment

---

**Last Updated**: 2026-01-20
**Next Review**: After Gold layer implementation
**Maintained By**: Data Engineering Team
