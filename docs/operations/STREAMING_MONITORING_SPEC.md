# Streaming Pipeline Monitoring Specification

**Last Updated**: 2026-01-20
**Status**: Draft → Implement before production
**Maintained By**: Data Engineering Team

---

## Overview

This document specifies metrics, dashboards, and alerts for the K2 streaming pipeline (Kafka → Bronze → Silver → Gold).

**Monitoring Stack**: Prometheus + Grafana (recommended)
**Alternative**: Datadog, New Relic (enterprise options)

---

## Metrics Specification

### 1. Bronze Layer Metrics

**Namespace**: `k2.bronze`

**Throughput Metrics:**
```
# Counter: Total records ingested since job start
k2_bronze_records_ingested_total{exchange="binance",layer="bronze"}
k2_bronze_records_ingested_total{exchange="kraken",layer="bronze"}

# Gauge: Current ingestion rate (records/second)
k2_bronze_ingestion_rate{exchange="binance"}
k2_bronze_ingestion_rate{exchange="kraken"}

# Histogram: Batch processing time
k2_bronze_batch_processing_seconds{exchange="binance",quantile="0.5|0.95|0.99"}
k2_bronze_batch_processing_seconds{exchange="kraken",quantile="0.5|0.95|0.99"}
```

**Latency Metrics:**
```
# Gauge: Kafka consumer lag (seconds behind latest offset)
k2_bronze_kafka_lag_seconds{exchange="binance"}
k2_bronze_kafka_lag_seconds{exchange="kraken"}

# Gauge: Time from Kafka timestamp to Bronze ingestion
k2_bronze_end_to_end_latency_seconds{exchange="binance"}
k2_bronze_end_to_end_latency_seconds{exchange="kraken"}
```

**Resource Metrics:**
```
# Gauge: Memory usage
k2_bronze_memory_used_bytes{exchange="binance"}
k2_bronze_memory_limit_bytes{exchange="binance"}

# Gauge: CPU usage (cores)
k2_bronze_cpu_usage_cores{exchange="binance"}
```

**Checkpoint Metrics:**
```
# Counter: Checkpoint writes
k2_bronze_checkpoint_writes_total{exchange="binance"}

# Histogram: Checkpoint write latency
k2_bronze_checkpoint_write_seconds{exchange="binance",quantile="0.5|0.95|0.99"}

# Gauge: Checkpoint size (bytes)
k2_bronze_checkpoint_size_bytes{exchange="binance"}
```

---

### 2. Silver Layer Metrics

**Namespace**: `k2.silver`

**Data Quality Metrics:**
```
# Counter: Valid records written to Silver
k2_silver_records_valid_total{exchange="binance"}

# Counter: Invalid records written to DLQ
k2_silver_records_invalid_total{exchange="binance"}

# Gauge: DLQ rate (percentage)
k2_silver_dlq_rate_percent{exchange="binance"}

# Counter: Validation errors by type
k2_silver_validation_errors_total{exchange="binance",error_type="price_validation"}
k2_silver_validation_errors_total{exchange="binance",error_type="timestamp_validation"}
k2_silver_validation_errors_total{exchange="binance",error_type="quantity_validation"}
k2_silver_validation_errors_total{exchange="binance",error_type="schema_validation"}
```

**Processing Metrics:**
```
# Histogram: Batch processing time
k2_silver_batch_processing_seconds{exchange="binance",quantile="0.5|0.95|0.99"}

# Gauge: Records processed per second
k2_silver_processing_rate{exchange="binance"}

# Counter: Batches falling behind (processing > trigger interval)
k2_silver_batches_behind_total{exchange="binance"}

# Histogram: Avro deserialization time per record
k2_silver_deserialization_micros{exchange="binance",quantile="0.5|0.95|0.99"}
```

**Transformation Metrics:**
```
# Counter: Schema Registry lookups
k2_silver_schema_lookups_total{exchange="binance"}

# Histogram: Schema Registry latency
k2_silver_schema_lookup_seconds{exchange="binance",quantile="0.5|0.95|0.99"}

# Counter: Null field occurrences (data quality indicator)
k2_silver_null_fields_total{exchange="binance",field="price|quantity|symbol"}
```

---

### 3. Gold Layer Metrics

**Namespace**: `k2.gold`

**Aggregation Metrics:**
```
# Counter: Total records written to Gold
k2_gold_records_written_total

# Counter: Duplicates removed (deduplication effectiveness)
k2_gold_duplicates_removed_total

# Gauge: Deduplication rate (percentage)
k2_gold_deduplication_rate_percent

# Counter: Records by exchange source
k2_gold_records_by_exchange_total{exchange="binance|kraken"}
```

**Partition Metrics:**
```
# Counter: Partitions created
k2_gold_partitions_created_total

# Gauge: Partition size (bytes)
k2_gold_partition_size_bytes{date="YYYY-MM-DD",hour="HH"}

# Gauge: Records per partition
k2_gold_records_per_partition{date="YYYY-MM-DD",hour="HH"}
```

**Processing Metrics:**
```
# Histogram: Union processing time
k2_gold_union_processing_seconds{quantile="0.5|0.95|0.99"}

# Histogram: Deduplication processing time
k2_gold_deduplication_processing_seconds{quantile="0.5|0.95|0.99"}
```

---

### 4. System Metrics

**Iceberg Metrics:**
```
# Gauge: Iceberg commit latency
k2_iceberg_commit_seconds{table="bronze_binance_trades"}

# Gauge: Metadata file count (track metadata bloat)
k2_iceberg_metadata_file_count{table="bronze_binance_trades"}

# Gauge: Data file count
k2_iceberg_data_file_count{table="bronze_binance_trades"}

# Gauge: Table size (bytes)
k2_iceberg_table_size_bytes{table="bronze_binance_trades"}
```

**Schema Registry Metrics:**
```
# Gauge: Registered schema count
k2_schema_registry_schemas_total

# Histogram: Schema fetch latency
k2_schema_registry_fetch_seconds{quantile="0.5|0.95|0.99"}

# Gauge: _schemas topic size (track compaction effectiveness)
k2_schema_registry_topic_size_bytes
```

**Kafka Metrics** (from Kafka exporters):
```
# Gauge: Topic lag (across all consumer groups)
kafka_consumer_group_lag{group="k2-bronze-binance-ingestion",topic="market.crypto.trades.binance.raw"}

# Gauge: Topic message rate
kafka_topic_messages_per_second{topic="market.crypto.trades.binance.raw"}
```

---

## Dashboard Specifications

### Dashboard 1: Pipeline Overview

**Panels:**

1. **Pipeline Health** (Single Stat)
   - Status: Running / Degraded / Down
   - Color: Green / Yellow / Red
   - Query: `up{job=~"bronze|silver|gold"}`

2. **End-to-End Latency** (Graph)
   - X-axis: Time
   - Y-axis: Latency (seconds)
   - Lines: Bronze, Silver, Gold
   - Target SLA line: 5 minutes

3. **Throughput by Exchange** (Graph)
   - X-axis: Time
   - Y-axis: Records/second
   - Lines: Binance (Bronze), Kraken (Bronze)

4. **DLQ Rate** (Graph)
   - X-axis: Time
   - Y-axis: Percentage
   - Lines: Binance DLQ%, Kraken DLQ%
   - Alert threshold lines: 1% (warning), 5% (critical)

5. **Resource Utilization** (Heatmap)
   - Rows: bronze-binance, bronze-kraken, silver-binance, silver-kraken
   - Columns: CPU%, Memory%
   - Colors: Green (<70%), Yellow (70-90%), Red (>90%)

---

### Dashboard 2: Data Quality

**Panels:**

1. **DLQ Error Breakdown** (Pie Chart)
   - Slices: price_validation, timestamp_validation, quantity_validation, schema_validation
   - Show percentage + count

2. **DLQ Trend (7-day)** (Graph)
   - X-axis: Time (7 days)
   - Y-axis: DLQ count
   - Lines: Binance, Kraken
   - Trend line: 7-day moving average

3. **Validation Errors by Hour** (Bar Chart)
   - X-axis: Hour of day (0-23)
   - Y-axis: Error count
   - Bars: Stacked by error_type

4. **Top Error Reasons** (Table)
   - Columns: error_reason, count, percentage, first_seen, last_seen
   - Sort: Count descending
   - Top 20 rows

---

### Dashboard 3: Performance Deep Dive

**Panels:**

1. **Batch Processing Time (p95)** (Graph)
   - X-axis: Time
   - Y-axis: Seconds
   - Lines: Bronze Binance, Silver Binance, Gold
   - Reference lines: Trigger intervals (10s, 30s, 60s)

2. **Batches Falling Behind** (Counter)
   - Single stat: Count of batches where processing > trigger interval
   - Grouped by job

3. **Checkpoint Write Latency** (Histogram)
   - X-axis: Latency buckets (ms)
   - Y-axis: Frequency
   - Show p50, p95, p99 annotations

4. **Iceberg Commit Latency** (Graph)
   - X-axis: Time
   - Y-axis: Seconds
   - Lines: bronze_binance, silver_binance

5. **Deserialization Performance** (Graph)
   - X-axis: Time
   - Y-axis: Microseconds per record
   - Lines: Binance, Kraken
   - Show p95

---

### Dashboard 4: Operational Health

**Panels:**

1. **Container Status** (Table)
   - Columns: name, status, uptime, restarts, cpu%, memory%
   - Rows: All streaming containers
   - Auto-refresh: 30s

2. **Kafka Lag** (Graph)
   - X-axis: Time
   - Y-axis: Lag (seconds)
   - Lines: bronze-binance, bronze-kraken
   - Alert threshold: 300s (5 minutes)

3. **Schema Registry Health** (Single Stat)
   - Startup time (last restart)
   - Schema count
   - _schemas topic size

4. **Checkpoint Sizes** (Bar Chart)
   - X-axis: Job name
   - Y-axis: Checkpoint size (MB)
   - Alert if > 1GB

5. **Recent Errors** (Logs Panel)
   - Live tail of ERROR level logs
   - Filter: job=~"bronze|silver|gold"
   - Last 50 entries

---

## Alert Rules

### Critical Alerts (PagerDuty / Page On-Call)

**Alert 1: Bronze Kafka Lag Critical**
```yaml
alert: BronzeKafkaLagCritical
expr: k2_bronze_kafka_lag_seconds{exchange="binance"} > 300
for: 5m
labels:
  severity: critical
  team: data-engineering
annotations:
  summary: "Bronze Binance lag exceeds 5 minutes"
  description: "Current lag: {{ $value }}s. Bronze job may be down or overloaded."
  runbook: "docs/operations/runbooks/streaming-pipeline-operations.md#incident-1"
```

**Alert 2: DLQ Rate Critical**
```yaml
alert: SilverDLQRateCritical
expr: k2_silver_dlq_rate_percent{exchange="binance"} > 5
for: 10m
labels:
  severity: critical
  team: data-quality
annotations:
  summary: "Silver Binance DLQ rate exceeds 5%"
  description: "DLQ rate: {{ $value }}%. Major data quality issue detected."
  runbook: "docs/operations/runbooks/streaming-pipeline-operations.md#incident-3"
```

**Alert 3: Streaming Job Down**
```yaml
alert: StreamingJobDown
expr: up{job=~"bronze-binance|silver-binance"} == 0
for: 2m
labels:
  severity: critical
  team: data-engineering
annotations:
  summary: "Streaming job {{ $labels.job }} is down"
  description: "Job has been down for 2+ minutes."
  runbook: "docs/operations/runbooks/streaming-pipeline-operations.md#incident-1"
```

---

### Warning Alerts (Slack #data-engineering)

**Alert 4: Bronze Lag Warning**
```yaml
alert: BronzeKafkaLagWarning
expr: k2_bronze_kafka_lag_seconds{exchange="binance"} > 120
for: 10m
labels:
  severity: warning
  team: data-engineering
annotations:
  summary: "Bronze Binance lag exceeds 2 minutes"
  description: "Current lag: {{ $value }}s. Monitor for escalation."
```

**Alert 5: DLQ Rate Elevated**
```yaml
alert: SilverDLQRateElevated
expr: k2_silver_dlq_rate_percent{exchange="binance"} > 1
for: 15m
labels:
  severity: warning
  team: data-quality
annotations:
  summary: "Silver Binance DLQ rate exceeds 1%"
  description: "DLQ rate: {{ $value }}%. Investigate error patterns."
```

**Alert 6: Processing Falling Behind**
```yaml
alert: SilverProcessingBehind
expr: k2_silver_batches_behind_total{exchange="binance"} > 3
for: 10m
labels:
  severity: warning
  team: data-engineering
annotations:
  summary: "Silver Binance consistently falling behind"
  description: "{{ $value }} batches exceeded trigger interval in last 10 min."
```

**Alert 7: High Memory Usage**
```yaml
alert: StreamingJobMemoryHigh
expr: (k2_bronze_memory_used_bytes / k2_bronze_memory_limit_bytes) > 0.9
for: 5m
labels:
  severity: warning
  team: data-engineering
annotations:
  summary: "{{ $labels.job }} memory usage > 90%"
  description: "Memory: {{ $value | humanizePercentage }}. Risk of OOM."
```

**Alert 8: Checkpoint Size Growing**
```yaml
alert: CheckpointSizeGrowing
expr: k2_bronze_checkpoint_size_bytes > 1073741824  # 1GB
for: 1h
labels:
  severity: warning
  team: data-engineering
annotations:
  summary: "{{ $labels.job }} checkpoint exceeds 1GB"
  description: "Checkpoint size: {{ $value | humanizeBytes }}. Review retention."
```

---

## Implementation Guide

### Phase 1: Add Structured Logging (Immediate)

**Update streaming jobs to use JSON logging:**
```python
import logging
import json

# Configure JSON logging
logging.basicConfig(
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s", "job": "%(name)s"}',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Replace print() calls
logger.info("Bronze writer started", extra={"trigger_interval": "10s", "max_offsets": 10000})
```

### Phase 2: Deploy Prometheus + Grafana (1-2 days)

**Add to docker-compose.yml:**
```yaml
prometheus:
  image: prom/prometheus:latest
  container_name: k2-prometheus
  volumes:
    - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus-data:/prometheus
  ports:
    - "9090:9090"
  command:
    - '--config.file=/etc/prometheus/prometheus.yml'
    - '--storage.tsdb.retention.time=30d'

grafana:
  image: grafana/grafana:latest
  container_name: k2-grafana
  volumes:
    - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
    - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources
    - grafana-data:/var/lib/grafana
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=admin
    - GF_USERS_ALLOW_SIGN_UP=false
```

**Create prometheus.yml:**
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'spark-master'
    static_configs:
      - targets: ['spark-master:8080']

  - job_name: 'spark-streaming-jobs'
    static_configs:
      - targets: ['bronze-binance-stream:4040', 'silver-binance-transformation:4040']
```

### Phase 3: Configure Spark Metrics (2-4 hours)

**Update spark_session.py:**
```python
.config("spark.metrics.conf.*.sink.prometheus.class", "org.apache.spark.metrics.sink.PrometheusSink")
.config("spark.metrics.conf.*.sink.prometheus.pushgateway-address", "prometheus:9091")
.config("spark.metrics.conf.*.sink.prometheus.period", "15")  # seconds
.config("spark.metrics.namespace", f"k2.{app_name}")
```

### Phase 4: Create Grafana Dashboards (1 day)

Import dashboard JSON templates (to be created) into Grafana.

### Phase 5: Deploy Alert Rules (4 hours)

Add alerting rules to Prometheus, configure Alertmanager for PagerDuty/Slack integration.

---

## Testing & Validation

### Metrics Collection Test
```bash
# 1. Start Prometheus + Grafana
docker compose up -d prometheus grafana

# 2. Check Prometheus targets
curl http://localhost:9090/api/v1/targets

# 3. Query sample metric
curl 'http://localhost:9090/api/v1/query?query=k2_bronze_records_ingested_total'

# 4. Access Grafana
open http://localhost:3000  # admin/admin
```

### Alert Test
```bash
# Simulate high DLQ rate
# (Inject bad data into Kafka to trigger validation failures)

# Check alert fires in Prometheus UI
open http://localhost:9090/alerts

# Verify Slack notification received
```

---

## References

- [Prometheus Best Practices](https://prometheus.io/docs/practices/)
- [Grafana Dashboard Design](https://grafana.com/docs/grafana/latest/best-practices/best-practices-for-creating-dashboards/)
- [Spark Monitoring](https://spark.apache.org/docs/latest/monitoring.html)

---

**Last Updated**: 2026-01-20
**Next Steps**: Implement Phase 1 (structured logging) immediately
**Maintained By**: Data Engineering Team
