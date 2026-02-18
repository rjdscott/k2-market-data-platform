# Step 4: Monitoring & Alerting

**Status:** ⬜ Not Started
**Phase:** 7 — Integration Hardening
**Last Updated:** 2026-02-18

---

## Objective

Finalize Grafana dashboards and Prometheus alerting so every critical pipeline path has
at least one alert and a single Grafana view shows end-to-end pipeline health at a glance.

---

## Current State

| Component | Status | Notes |
|-----------|--------|-------|
| Prometheus | ✅ Running | `http://localhost:9090` |
| Grafana | ✅ Running | `http://localhost:3000` (admin/admin) |
| Iceberg offload metrics | ✅ Alert rules exist | `prometheus/alert_rules.yml` |
| Feed handler metrics | ⬜ Gap | No dashboards for per-exchange trade rates |
| Redpanda consumer lag | ⬜ Gap | No consumer lag panels or alerts |
| ClickHouse insert rate | ⬜ Gap | No insert rate / MV latency panels |
| Resource utilization | ⬜ Gap | No CPU/RAM per-service panels |

---

## Grafana Dashboard Panels to Add

### Panel Group 1: Feed Handlers

| Panel | Metric | Type |
|-------|--------|------|
| Trade messages/sec per exchange | `rate(feed_handler_trades_produced_total[1m])` | Time series |
| Feed handler reconnects | `increase(feed_handler_reconnects_total[5m])` | Stat |
| Last message time per exchange | `feed_handler_last_message_timestamp` | Gauge |

### Panel Group 2: Redpanda / Consumer Lag

| Panel | Metric | Type |
|-------|--------|------|
| Consumer lag per topic | `kafka_consumer_group_lag` (via JMX exporter) | Time series |
| Topic message rate | `kafka_topic_messages_in_total` | Time series |
| Lag alert threshold line | 1000 messages | Reference line |

### Panel Group 3: ClickHouse Pipeline

| Panel | Metric | Type |
|-------|--------|------|
| Bronze insert rate (rows/sec) | `clickhouse_bronze_inserts_total` / query_log | Time series |
| MV processing delay | `ingestion_timestamp - exchange_timestamp` (avg) | Gauge |
| Bronze row count by exchange | CH query panel | Stat |
| Silver row count | CH query panel | Stat |

### Panel Group 4: Iceberg Offload

| Panel | Metric | Type |
|-------|--------|------|
| Offload lag (minutes behind) | existing `iceberg_offload_lag_minutes` | Gauge |
| Last successful offload time | existing metric | Stat |
| Offload success/failure count | existing | Time series |

### Panel Group 5: Resource Utilization

| Panel | Metric | Type |
|-------|--------|------|
| CPU % per service | `container_cpu_usage_seconds_total` | Time series |
| Memory usage per service | `container_memory_usage_bytes` | Time series |
| Total stack CPU / RAM | Sum gauges | Stat |

---

## Alert Rules to Add / Verify

| Alert | Condition | Severity | Action |
|-------|-----------|----------|--------|
| `FeedHandlerDown` | No messages for >2 min from any exchange | Critical | Check feed-handler container logs |
| `RedpandaConsumerLagHigh` | `kafka_consumer_group_lag > 1000` for >5 min | Warning | Check ClickHouse Kafka consumer |
| `ClickHouseBronzeInsertFailure` | Insert error rate > 0 | Critical | Check ClickHouse Kafka engine |
| `MVProcessingDelay` | avg(ingestion_ts - exchange_ts) > 30s | Warning | Check MV definition; possible CH overload |
| `IcebergOffloadLag` | `iceberg_offload_lag_minutes > 30` (existing) | Warning | Check Spark container |
| `IcebergOffloadFailure` | Consecutive FAILED Prefect runs > 2 | Critical | Check Spark + MinIO |
| `ServiceOOMKill` | Container restart_count increasing | Critical | Increase RAM budget |

---

## Implementation Steps

1. Add Prometheus JMX exporter sidecar for Redpanda consumer lag metrics
2. Export Micrometer feed handler metrics to Prometheus (already in Kotlin; verify endpoint)
3. Build Grafana dashboard JSON and commit to `docker/grafana/dashboards/`
4. Add new alert rules to `docker/prometheus/alert_rules.yml`
5. Test each alert fires by simulating the condition

---

## Acceptance Criteria

- [ ] All critical paths have ≥1 Prometheus alert rule
- [ ] Single Grafana view shows end-to-end pipeline health
- [ ] Consumer lag alert fires when lag >1000 (tested)
- [ ] Feed handler down alert fires within 2 min of stopping a handler
- [ ] Iceberg offload failure alert fires on consecutive failures
- [ ] Dashboard JSON committed to repo (`docker/grafana/dashboards/k2-pipeline.json`)

---

## Related

- [Phase 7 README](../README.md)
- [Step 3: Failure Mode Testing](step-03-failure-mode-testing.md)
- [Step 5: Runbooks](step-05-runbooks-documentation.md)
- [Operations monitoring guide](../../../../operations/monitoring/)
