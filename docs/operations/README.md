# Operations Documentation

**Last Updated**: 2026-01-13
**Stability**: Medium - updated after incidents
**Target Audience**: DevOps, SREs, On-Call Engineers

This directory contains operational procedures, runbooks, monitoring configuration, and performance tuning guides.

---

## Overview

Operations documentation helps you:
- **Respond to incidents** using step-by-step runbooks
- **Monitor system health** via dashboards and alerts
- **Optimize performance** using tuning guides
- **Prevent future issues** through post-incident reviews

---

## Directory Structure

```
operations/
├── runbooks/              # Incident response procedures
├── monitoring/            # Dashboards, alerts, SLOs
└── performance/           # Performance tuning guides
```

---

## Runbooks

### What's a Runbook?
Step-by-step procedures for diagnosing and resolving common operational issues.

### Available Runbooks

#### [runbooks/failure-recovery.md](./runbooks/failure-recovery.md)
**General failure recovery procedures**

- Service restart procedures
- Data recovery from backups
- Rollback procedures
- Health check validation

#### [runbooks/disaster-recovery.md](./runbooks/disaster-recovery.md)
**Disaster recovery procedures**

- Full system recovery from backup
- Multi-region failover (Phase 2+)
- Data restoration procedures
- RTO/RPO targets

### Creating New Runbooks

Use this template:
```markdown
# Runbook: [Incident Type]

**Severity**: Critical | High | Medium | Low
**Last Updated**: YYYY-MM-DD

## Symptoms
[What the operator sees]

## Diagnosis
[How to confirm this is the issue]

## Resolution
[Step-by-step fix]

## Prevention
[How to avoid in future]

## Related Monitoring
[Dashboard links, alerts]
```

### Runbook Validation

All runbooks should be validated to ensure commands are executable and procedures work as expected.

**Validation Script**: `scripts/ops/validate_runbooks.sh`

```bash
# Validate all runbooks (syntax checks)
./scripts/ops/validate_runbooks.sh

# Full validation (requires services running)
./scripts/ops/validate_runbooks.sh --full
```

**What's Validated**:
- Required tools are installed (Docker, Kafka CLI, curl, jq, etc.)
- Services are accessible (Kafka, MinIO, Prometheus, API)
- Runbook files exist and have required sections
- Alert rules are properly configured
- Commands have valid syntax

**Best Practices**:
- Run validation after updating runbooks
- Include validation in CI/CD pipeline
- Test runbooks during disaster recovery drills
- Update validation script when adding new runbooks

See [scripts/ops/README.md](../../scripts/ops/README.md) for detailed usage.

---

## Monitoring

### Dashboards

Grafana dashboards for system observability:

**Main Dashboard**: `k2-platform-overview`
- System health at-a-glance
- Key metrics: throughput, latency, errors
- Resource utilization

**Component Dashboards**:
- `k2-kafka-metrics` - Broker health, consumer lag
- `k2-iceberg-metrics` - Write throughput, file counts
- `k2-query-metrics` - Query latency, cache hit rate
- `k2-api-metrics` - Request rate, response time, errors

### Alerts

**Critical Alerts** (Page immediately):
- Kafka broker down
- Consumer lag > 10,000 messages
- API error rate > 5%
- Disk usage > 90%

**Warning Alerts** (Investigate during business hours):
- Consumer lag > 1,000 messages
- Query p99 latency > 10s
- Iceberg compaction overdue

### SLOs (Service Level Objectives)

| Metric | Target | Measurement Window |
|--------|--------|-------------------|
| API Availability | 99.9% | 30 days |
| Query p99 Latency | < 5s | 7 days |
| Data Freshness | < 5 minutes | Real-time |
| Consumer Lag | < 1000 messages | Real-time |

---

## Performance Tuning

### [performance/latency-budgets.md](./performance/latency-budgets.md)
**Latency budgets and backpressure handling**

- End-to-end latency budget (CSV → Query: < 10s)
- Per-component latency breakdown
- Backpressure cascade strategy
- Degradation modes

### Optimization Guides

#### Query Optimization
1. **Partition pruning**: Filter by date first
2. **Projection pushdown**: Select only needed columns
3. **Predicate pushdown**: Filter in storage layer
4. **Result limiting**: Use LIMIT for exploratory queries

#### Kafka Optimization
1. **Producer batching**: Increase linger.ms for throughput
2. **Consumer parallelism**: Match partitions to consumer count
3. **Compression**: Use snappy for balance of speed/size
4. **Retention policy**: Configure based on replay needs

#### Iceberg Optimization
1. **Compaction**: Run during off-hours
2. **Partition evolution**: Add finer partitions as data grows
3. **Metadata caching**: Enable Iceberg catalog cache
4. **File size**: Target 128-256MB files

---

## Common Issues and Solutions

### Issue: High Consumer Lag

**Symptoms**: Consumer lag dashboard shows > 10,000 messages

**Quick Fix**:
```bash
# Check consumer status
docker logs k2-consumer

# Increase consumer parallelism (if < partition count)
# Scale consumer replicas (future)

# Temporary: Increase batch size
# Edit consumer config, restart
```

**Root Causes**:
- Slow Iceberg writes
- Insufficient consumer parallelism
- Network issues to MinIO

**See**: [runbooks/kafka-consumer-lag.md](./runbooks/) (to be created)

### Issue: Query Timeout

**Symptoms**: API returns 504 Gateway Timeout

**Quick Fix**:
```bash
# Check query engine logs
docker logs k2-api

# Check DuckDB memory usage
# Reduce query scope (date range, symbol list)

# Temporary: Increase timeout in API config
```

**Root Causes**:
- Query too broad (no partition pruning)
- Large partition scan
- DuckDB memory limit reached

**See**: [runbooks/query-timeout.md](./runbooks/) (to be created)

### Issue: Schema Registry Unavailable

**Symptoms**: Producer fails with "Schema Registry unreachable"

**Quick Fix**:
```bash
# Check Schema Registry health
curl http://localhost:8081

# Restart Schema Registry
docker restart schema-registry

# Check network connectivity
docker network inspect k2-network
```

**See**: [runbooks/schema-registry-down.md](./runbooks/) (to be created)

---

## Incident Response Process

### 1. Detect
- Alerts fire in Grafana
- User reports issue
- Monitoring shows anomaly

### 2. Triage
- Determine severity (Critical/High/Medium/Low)
- Identify affected components
- Estimate user impact

### 3. Diagnose
- Follow relevant runbook
- Check dashboards and logs
- Verify symptoms match known issues

### 4. Resolve
- Execute runbook steps
- Monitor resolution effectiveness
- Validate system health post-fix

### 5. Document
- Update runbook if steps changed
- Create post-mortem (if critical)
- Update alerting if needed

---

## On-Call Procedures

### On-Call Rotation (Future - Phase 2+)
- **Primary**: First responder, owns incident
- **Secondary**: Backup if primary unavailable
- **Escalation**: Tech lead for complex issues

### Escalation Path
1. On-call engineer (L1)
2. Tech lead (L2)
3. Principal engineer (L3)

### Handoff Checklist
- [ ] Review open incidents
- [ ] Check system health dashboard
- [ ] Review recent changes/deployments
- [ ] Confirm contact info current
- [ ] Test pager/alerting

---

## Demo Reset

For demonstration purposes, the platform includes a reset utility to clear all datastores between demos.

### Reset Commands

```bash
# Preview what will be reset (dry-run)
make demo-reset-dry-run

# Full reset with confirmation prompt
make demo-reset

# Force reset (skip confirmation)
make demo-reset-force

# Selective reset with flags
make demo-reset-custom KEEP_METRICS=1    # Preserve Prometheus/Grafana
make demo-reset-custom KEEP_KAFKA=1       # Preserve Kafka messages
make demo-reset-custom KEEP_ICEBERG=1     # Preserve Iceberg tables
```

### What Gets Reset

| Component | Action | Notes |
|-----------|--------|-------|
| **Kafka** | Purge messages (keep topics) | Topic config preserved |
| **Iceberg** | Drop and recreate tables | Schema recreated via init_infra.py |
| **MinIO** | Clear warehouse/ bucket | Bucket structure preserved |
| **PostgreSQL** | Truncate audit/lineage tables | Catalog metadata auto-managed |
| **Prometheus** | Clear time-series data | Container restarted |
| **Grafana** | Clear sessions | Dashboards preserved (provisioned) |

### Demo Workflow

```bash
# 1. Before first demo of the day
make docker-up && make init-infra

# 2. Run demo
make demo-quick

# 3. Reset between demos
make demo-reset-force

# 4. Repeat steps 2-3 for subsequent demos
```

**Tip**: Use `--dry-run` first to preview what will be cleared before running actual reset.

---

## Maintenance Windows

### Scheduled Maintenance

**Iceberg Compaction**: Daily at 02:00 UTC (low traffic)
**Kafka Log Retention**: Weekly on Sunday 03:00 UTC
**System Updates**: Monthly, first Saturday 00:00-04:00 UTC

### Maintenance Checklist
```bash
# Pre-maintenance
- [ ] Notify stakeholders (if user-facing)
- [ ] Backup critical data
- [ ] Verify rollback plan

# During maintenance
- [ ] Execute change
- [ ] Monitor system health
- [ ] Validate functionality

# Post-maintenance
- [ ] Run health checks
- [ ] Update documentation
- [ ] Close maintenance ticket
```

---

## Troubleshooting Tips

### General Debugging Steps
1. Check logs: `docker logs <service-name>`
2. Check metrics: Grafana dashboards
3. Check connectivity: `docker network inspect k2-network`
4. Check disk space: `df -h`
5. Check memory: `docker stats`

### Log Locations
- **API**: `docker logs k2-api`
- **Consumer**: `docker logs k2-consumer`
- **Kafka**: `docker logs kafka`
- **Schema Registry**: `docker logs schema-registry`

### Health Check Endpoints
- **API**: `http://localhost:8000/health`
- **Prometheus**: `http://localhost:9090/-/healthy`
- **Grafana**: `http://localhost:3000/api/health`
- **Schema Registry**: `http://localhost:8081`

---

## Related Documentation

- **Architecture**: [../architecture/](../architecture/)
- **Design**: [../design/](../design/)
- **Testing**: [../testing/](../testing/)
- **Monitoring Config**: [./monitoring/](./monitoring/)

---

**Maintained By**: DevOps Team
**Review Frequency**: Quarterly or after major incidents
**Last Review**: 2026-01-10
