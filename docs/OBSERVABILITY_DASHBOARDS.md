# Observability Dashboards & Alerts

**Last Updated**: 2026-01-09
**Owners**: Platform Team, SRE
**Status**: Implementation Plan
**Scope**: Grafana dashboards, Prometheus alerts, on-call runbooks

---

## Overview

Observability is not optional for distributed systems. This document defines the critical dashboards and alerts needed to monitor platform health, debug incidents, and maintain SLOs.

**Three Pillars**: Metrics (Prometheus), Logs (structured JSON), Traces (OpenTelemetry)

---

## Dashboard 1: Platform Health Overview

**Purpose**: Executive summary for platform status
**Audience**: All engineers, management
**Refresh**: Real-time (5-second updates)

### Metrics

**Top Row: Golden Signals**
```
┌────────────────┬────────────────┬────────────────┬────────────────┐
│   Throughput   │  Latency p99   │   Error Rate   │  Saturation    │
│  10.2K msg/sec │    45ms        │     0.12%      │    CPU: 34%    │
│   ↑ 5% (good)  │  ↓ 2ms (good)  │  ↑ 0.05% (ok)  │  ↓ 10% (good)  │
└────────────────┴────────────────┴────────────────┴────────────────┘
```

**Prometheus Queries**:
```promql
# Throughput (messages/sec)
rate(kafka_messages_consumed_total[5m])

# Latency p99
histogram_quantile(0.99, rate(kafka_consumer_lag_seconds_bucket[5m]))

# Error Rate
rate(kafka_consumer_errors_total[5m]) / rate(kafka_messages_consumed_total[5m])

# Saturation (CPU)
avg(rate(container_cpu_usage_seconds_total[5m]))
```

### Visualization

**Time Series Panel: Ingestion Rate**
```json
{
  "title": "Ingestion Rate (msg/sec)",
  "targets": [
    {
      "expr": "rate(kafka_messages_consumed_total{topic=~'market.*'}[5m])",
      "legendFormat": "{{topic}}"
    }
  ],
  "yaxis": {
    "label": "Messages/Second",
    "format": "short"
  },
  "thresholds": [
    {"value": 5000, "color": "green"},
    {"value": 10000, "color": "yellow"},
    {"value": 50000, "color": "red"}
  ]
}
```

**Gauge Panel: Consumer Lag**
```json
{
  "title": "Consumer Lag (seconds)",
  "targets": [
    {
      "expr": "max(kafka_consumer_lag_seconds{group=~'.*'})",
      "legendFormat": "Max Lag"
    }
  ],
  "gauge": {
    "show": true,
    "maxValue": 300,
    "thresholdMarkers": true,
    "thresholds": [
      {"value": 0, "color": "green"},
      {"value": 60, "color": "yellow"},
      {"value": 120, "color": "red"}
    ]
  }
}
```

---

## Dashboard 2: Ingestion Health

**Purpose**: Monitor Kafka ingestion pipeline
**Audience**: Platform engineers, on-call
**Refresh**: 10-second updates

### Key Metrics

**Kafka Lag**:
```promql
# Current lag by consumer group
kafka_consumer_lag_messages{group=~'.*'}

# Lag trend (last 1 hour)
kafka_consumer_lag_messages{group=~'.*'}[1h]

# Alert: Lag > 1M messages
kafka_consumer_lag_messages > 1000000
```

**Sequence Gaps**:
```promql
# Sequence gaps detected
rate(sequence_gap_total[5m])

# Gaps by symbol (top 10)
topk(10, rate(sequence_gap_total[5m])) by (symbol)

# Alert: Gaps > 100
sequence_gap_size > 100
```

**Schema Validation Errors**:
```promql
# Validation errors
rate(schema_validation_errors_total[5m])

# Errors by exchange
sum(rate(schema_validation_errors_total[5m])) by (exchange)

# Alert: Error rate > 1%
rate(schema_validation_errors_total[5m]) / rate(kafka_messages_consumed_total[5m]) > 0.01
```

### Visualization

**Heatmap: Consumer Lag by Partition**
```json
{
  "title": "Consumer Lag Heatmap (by partition)",
  "type": "heatmap",
  "targets": [
    {
      "expr": "kafka_consumer_lag_messages{group='strategy_alpha'}",
      "format": "time_series",
      "legendFormat": "partition {{partition}}"
    }
  ],
  "yAxis": {
    "format": "short",
    "label": "Partition"
  },
  "color": {
    "mode": "spectrum",
    "scheme": "RdYlGn"
  }
}
```

---

## Dashboard 3: Storage Health

**Purpose**: Monitor Iceberg writes and S3 storage
**Audience**: Platform engineers, data engineers
**Refresh**: 30-second updates

### Key Metrics

**Iceberg Write Latency**:
```promql
# Write latency percentiles
histogram_quantile(0.50, rate(iceberg_write_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(iceberg_write_duration_seconds_bucket[5m]))
histogram_quantile(0.999, rate(iceberg_write_duration_seconds_bucket[5m]))

# Alert: p99 > 1 second
histogram_quantile(0.99, rate(iceberg_write_duration_seconds_bucket[5m])) > 1.0
```

**Storage Growth**:
```promql
# Storage size by table
iceberg_table_size_bytes{table=~'market_data.*'}

# Growth rate (GB/day)
rate(iceberg_table_size_bytes[1d]) * 86400 / 1e9

# Alert: Growth > 500GB/day (unexpected)
rate(iceberg_table_size_bytes[1d]) * 86400 / 1e9 > 500
```

**S3 API Calls**:
```promql
# S3 request rate
rate(s3_api_requests_total[5m])

# S3 error rate
rate(s3_api_errors_total[5m]) / rate(s3_api_requests_total[5m])

# Alert: Error rate > 5%
rate(s3_api_errors_total[5m]) / rate(s3_api_requests_total[5m]) > 0.05
```

### Visualization

**Graph Panel: Write Throughput**
```json
{
  "title": "Iceberg Write Throughput (rows/sec)",
  "targets": [
    {
      "expr": "rate(iceberg_rows_written_total[5m])",
      "legendFormat": "{{table}}"
    }
  ],
  "alert": {
    "conditions": [
      {
        "evaluator": {"params": [0], "type": "lt"},
        "query": {"model": "rate(iceberg_rows_written_total[5m])"},
        "type": "query"
      }
    ],
    "executionErrorState": "alerting",
    "frequency": "1m",
    "message": "Iceberg writes stopped (no rows written in last minute)",
    "name": "Iceberg Write Failure"
  }
}
```

---

## Dashboard 4: Query Performance

**Purpose**: Monitor Query API latency and throughput
**Audience**: Platform engineers, API consumers
**Refresh**: 10-second updates

### Key Metrics

**Query Latency**:
```promql
# Latency by query mode (realtime/historical/hybrid)
histogram_quantile(0.99,
  rate(query_duration_seconds_bucket[5m])
) by (mode)

# Alert: p99 > 5 seconds
histogram_quantile(0.99, rate(query_duration_seconds_bucket[5m])) > 5.0
```

**Query Throughput**:
```promql
# Queries per second
rate(query_requests_total[5m])

# Queries by endpoint
rate(query_requests_total[5m]) by (endpoint)

# Alert: Throughput drop > 50%
(
  rate(query_requests_total[5m])
  / rate(query_requests_total[5m] offset 1h)
) < 0.5
```

**Cache Hit Rate**:
```promql
# Cache hit rate by layer (L1/L2/L3)
cache_hits_total / (cache_hits_total + cache_misses_total)

# Alert: Hit rate < 50%
cache_hits_total / (cache_hits_total + cache_misses_total) < 0.5
```

**Error Rate**:
```promql
# Query errors
rate(query_errors_total[5m])

# Errors by type
rate(query_errors_total[5m]) by (error_type)

# Alert: Error rate > 5%
rate(query_errors_total[5m]) / rate(query_requests_total[5m]) > 0.05
```

### Visualization

**Stat Panel: Query Latency SLO**
```json
{
  "title": "Query Latency p99 (SLO: < 5s)",
  "type": "stat",
  "targets": [
    {
      "expr": "histogram_quantile(0.99, rate(query_duration_seconds_bucket[5m]))"
    }
  ],
  "options": {
    "graphMode": "area",
    "colorMode": "background",
    "thresholds": {
      "steps": [
        {"value": 0, "color": "green"},
        {"value": 3, "color": "yellow"},
        {"value": 5, "color": "red"}
      ]
    }
  }
}
```

---

## Dashboard 5: Business Metrics

**Purpose**: Track business KPIs (symbols, data volume, quality)
**Audience**: Product managers, executives
**Refresh**: 1-minute updates

### Key Metrics

**Active Symbols**:
```promql
# Unique symbols ingested (last 1 hour)
count(count_over_time(kafka_messages_consumed_total{topic='market.ticks.*'}[1h])) by (symbol)

# New symbols today
count(count_over_time(kafka_messages_consumed_total{topic='market.ticks.*'}[1d])) by (symbol)
  unless
count(count_over_time(kafka_messages_consumed_total{topic='market.ticks.*'}[1d] offset 1d)) by (symbol)
```

**Data Volume**:
```promql
# Total messages today
sum(increase(kafka_messages_consumed_total[1d]))

# Data size (GB) today
sum(increase(kafka_bytes_consumed_total[1d])) / 1e9
```

**Data Quality Score**:
```promql
# Quality score by symbol (0-100)
data_quality_score{symbol=~'.*'}

# Average quality score
avg(data_quality_score)

# Alert: Average quality < 95
avg(data_quality_score) < 95
```

---

## Alert Rules

### Critical Alerts (Page On-Call)

**Alert: Consumer Lag Critical**
```yaml
- alert: ConsumerLagCritical
  expr: kafka_consumer_lag_messages{group=~'.*'} > 5000000
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Consumer lag > 5M messages for {{ $labels.group }}"
    description: "Consumer {{ $labels.group }} is severely lagging ({{ $value }} messages). Check consumer health and consider scaling."
    runbook: "https://docs.k2platform.com/runbooks/consumer-lag"
```

**Alert: Iceberg Write Failure**
```yaml
- alert: IcebergWriteFailure
  expr: rate(iceberg_write_errors_total[5m]) > 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Iceberg writes failing for {{ $labels.table }}"
    description: "{{ $value }} write errors/sec for table {{ $labels.table }}. Data may be lost if not resolved."
    runbook: "https://docs.k2platform.com/runbooks/iceberg-write-failure"
```

**Alert: Query API Down**
```yaml
- alert: QueryAPIDown
  expr: up{job='query-api'} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Query API is down"
    description: "Query API instance {{ $labels.instance }} is not responding. Check logs and restart if needed."
    runbook: "https://docs.k2platform.com/runbooks/query-api-down"
```

### Warning Alerts (Investigate Next Business Day)

**Alert: Consumer Lag Warning**
```yaml
- alert: ConsumerLagWarning
  expr: kafka_consumer_lag_messages{group=~'.*'} > 1000000
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Consumer lag > 1M messages for {{ $labels.group }}"
    description: "Consumer {{ $labels.group }} is lagging ({{ $value }} messages). Monitor and scale if continues."
```

**Alert: Cache Hit Rate Low**
```yaml
- alert: CacheHitRateLow
  expr: |
    cache_hits_total / (cache_hits_total + cache_misses_total) < 0.5
  for: 15m
  labels:
    severity: warning
  annotations:
    summary: "Cache hit rate < 50% for {{ $labels.layer }}"
    description: "Cache {{ $labels.layer }} hit rate is {{ $value }}%. Review cache size and TTL settings."
```

**Alert: Data Quality Degraded**
```yaml
- alert: DataQualityDegraded
  expr: data_quality_score < 90
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Data quality score < 90 for {{ $labels.symbol }}"
    description: "Symbol {{ $labels.symbol }} quality score is {{ $value }}. Check validation rules and exchange feed."
```

---

## On-Call Dashboard

**Purpose**: Single pane of glass for on-call engineers
**Audience**: On-call engineer (24/7 rotation)
**Refresh**: 5-second updates

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│                  ACTIVE ALERTS                              │
│  ⚠️  2 Critical   ℹ️  5 Warnings                            │
├─────────────────────────────────────────────────────────────┤
│ Critical: Consumer Lag > 5M (strategy_alpha)               │
│ Critical: Iceberg Write Failure (market_data.ticks)        │
├─────────────────────────────────────────────────────────────┤
│                 PLATFORM HEALTH                             │
│  Ingestion: ✅ 10.2K msg/sec                                │
│  Storage:   ✅ p99 write latency 180ms                      │
│  Query:     ⚠️  p99 latency 6.2s (SLO: 5s)                 │
├─────────────────────────────────────────────────────────────┤
│              RECENT DEPLOYMENTS                             │
│  15 min ago: query-api v1.3.1 (DEPLOYED)                   │
│  2 hours ago: ingestion-consumer v1.2.9 (DEPLOYED)         │
├─────────────────────────────────────────────────────────────┤
│                 RECENT INCIDENTS                            │
│  3 hours ago: [RESOLVED] Kafka broker failure (5 min)      │
│  Yesterday: [RESOLVED] S3 throttling (30 min)              │
└─────────────────────────────────────────────────────────────┘
```

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Observable by default
- [Failure & Recovery](./FAILURE_RECOVERY.md) - Runbooks for incidents
- [Latency & Backpressure](./LATENCY_BACKPRESSURE.md) - Performance SLOs
- [Testing Strategy](./TESTING_STRATEGY.md) - Chaos testing scenarios
