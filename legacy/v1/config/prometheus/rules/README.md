# K2 Prometheus Alert Rules

This directory contains critical alert rules for the K2 Market Data Platform.

## Overview

The alert rules are organized into logical groups covering:
- **Data Pipeline Health**: Consumer lag, write failures, sequence gaps
- **Circuit Breakers**: Service degradation detection
- **API Health**: Error rates, latency, availability
- **Infrastructure**: Kafka, PostgreSQL, MinIO, disk, memory
- **Query Performance**: Timeout rates, execution time
- **Consumer Health**: Lag growth, rebalancing
- **Producer Health**: Error rates, retry exhaustion

## Alert Files

- `critical_alerts.yml` - 20+ critical production alerts

## Alert Severity Levels

- **critical**: Page on-call immediately (potential data loss, system down)
- **high**: Escalate within 15 minutes (degraded service, SLA risk)
- **medium**: Alert during business hours (non-critical issues)

## Usage

### Validate Alert Rules

```bash
# Validate syntax and coverage
./scripts/ops/validate_alerts.sh

# Check current alert status
./scripts/ops/check_alerts.sh

# Watch alerts in real-time
./scripts/ops/check_alerts.sh --watch

# Show only firing alerts
./scripts/ops/check_alerts.sh --firing
```

### Reload Alerts

After modifying alert rules:

```bash
# Reload Prometheus configuration (hot reload)
curl -X POST http://localhost:9090/-/reload

# Or restart Prometheus container
docker-compose restart prometheus
```

### Test Alerts

To test if an alert fires correctly, you can manually trigger conditions:

```bash
# Simulate consumer lag
# (increase produce rate or stop consumer)

# Simulate API errors
# (send malformed requests)

# Simulate service down
# (stop a service container)
```

## Alert Documentation

Every alert includes:
- **summary**: Brief description of the issue
- **description**: Detailed explanation with troubleshooting hints
- **runbook**: Link to step-by-step recovery procedures
- **dashboard**: Link to relevant Grafana dashboard

## Runbook Links

All runbook links point to `docs/operations/runbooks/`. Each alert should have a corresponding runbook with:
1. Symptoms
2. Diagnosis steps
3. Resolution procedures
4. Prevention measures

## Adding New Alerts

When adding a new alert:

1. Choose the appropriate group or create a new one
2. Set appropriate `for` duration (avoid alert flapping)
3. Include all required annotations (summary, description, runbook)
4. Add relevant labels (severity, component, team)
5. Validate syntax: `./scripts/ops/validate_alerts.sh`
6. Test the alert fires correctly
7. Create corresponding runbook in `docs/operations/runbooks/`

### Alert Template

```yaml
- alert: YourAlertName
  expr: your_metric > threshold
  for: 5m
  labels:
    severity: critical|high|medium
    component: api|ingestion|storage|infrastructure
    team: data-platform|infrastructure
  annotations:
    summary: "Brief description ({{ $value }})"
    description: |
      Detailed explanation of the issue.
      Impact: What breaks if this is ignored.
      Next steps: What to check first.
    runbook: "https://github.com/k2-platform/k2/blob/main/docs/operations/runbooks/your-runbook.md"
    dashboard: "https://grafana.k2.local/d/dashboard-id"
```

## Metric Naming Conventions

K2 metrics follow these conventions:

- **Counters**: `k2_<component>_<metric>_total` (e.g., `k2_iceberg_write_errors_total`)
- **Gauges**: `k2_<component>_<metric>` (e.g., `k2_circuit_breaker_state`)
- **Histograms**: `k2_<component>_<metric>_seconds` (e.g., `k2_query_duration_seconds`)

See `src/k2/common/metrics_registry.py` for all available metrics.

## Alert Tuning

If an alert is too noisy or not sensitive enough:

1. Adjust the threshold value
2. Modify the `for` duration (balance between speed and false positives)
3. Update the PromQL expression for better accuracy
4. Consider using `rate()` over longer windows for smoother trends

Example tuning:

```yaml
# Too noisy: fires on every spike
expr: error_rate > 0.01
for: 1m

# Better: requires sustained high rate
expr: rate(errors_total[5m]) > 0.01
for: 10m
```

## Integration with Alertmanager

For production deployment, configure Alertmanager in `prometheus.yml`:

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']
```

Then configure Alertmanager routes to:
- Send critical alerts to PagerDuty
- Send high alerts to Slack
- Send medium alerts to email

## Monitoring

Monitor the alerting system itself:

- **ALERTS_FOR_STATE**: Shows time alerts have been pending
- **prometheus_rule_evaluation_duration_seconds**: Alert rule evaluation performance
- **prometheus_rule_evaluation_failures_total**: Failed rule evaluations

## References

- [Prometheus Alerting Docs](https://prometheus.io/docs/alerting/latest/overview/)
- [Alert Best Practices](https://prometheus.io/docs/practices/alerting/)
- [K2 Operations Runbooks](../../../docs/operations/runbooks/)
- [K2 Monitoring Strategy](../../../docs/operations/monitoring/)

---

**Last Updated**: 2026-01-13
**Maintained By**: Data Platform Team
**Questions?**: Create an issue or reach out in #data-platform-alerts
