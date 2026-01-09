# Disaster Recovery Strategy

**Last Updated**: 2026-01-09
**Owners**: Platform Team, SRE
**Status**: Implementation Plan
**Scope**: Backup, recovery procedures, business continuity

---

## Overview

Disaster recovery (DR) is not optional for financial market data infrastructure. Regulatory requirements mandate documented recovery procedures with tested Recovery Time Objective (RTO) and Recovery Point Objective (RPO) guarantees.

This document defines backup strategies, recovery procedures, multi-region replication, and DR testing protocols for all platform components.

**Design Philosophy**: Plan for failure. Every component must have documented recovery procedures, and DR must be tested regularly, not discovered during actual disasters.

---

## Recovery Objectives

### RTO (Recovery Time Objective)

**Definition**: Maximum acceptable time to restore service after failure

| Component | RTO Target | Acceptable Degradation |
|-----------|------------|------------------------|
| **Kafka Ingestion** | 5 minutes | Stop accepting new data |
| **Iceberg Storage** | 10 minutes | Queries fail, ingestion continues |
| **Query API** | 2 minutes | Return cached/stale data |
| **Schema Registry** | 5 minutes | Producers/consumers halt |
| **PostgreSQL Catalog** | 15 minutes | Queries fail, writes buffer |

**Critical Path**: Kafka → Iceberg (RTO < 15 minutes)

### RPO (Recovery Point Objective)

**Definition**: Maximum acceptable data loss measured in time

| Component | RPO Target | Data Loss Impact |
|-----------|------------|------------------|
| **Kafka** | 30 seconds | Last 30s of ticks lost |
| **Iceberg** | 60 seconds | Last minute not committed |
| **Audit Logs** | 0 seconds | No loss acceptable (compliance) |
| **Schema Registry** | 0 seconds | Schemas must be recoverable |
| **PostgreSQL Catalog** | 5 minutes | Metadata may be stale |

**Critical Data**: Audit logs and schemas have RPO = 0 (no data loss acceptable)

---

## Backup Strategy

### Kafka Topics

#### Backup Method: Kafka Connect S3 Sink

**Configuration**:
```json
{
  "name": "kafka-to-s3-backup",
  "config": {
    "connector.class": "io.confluent.connect.s3.S3SinkConnector",
    "topics": "market.ticks.asx,market.ticks.nyse,trades.executed",
    "s3.region": "ap-southeast-2",
    "s3.bucket.name": "k2-kafka-backups",
    "format.class": "io.confluent.connect.s3.format.parquet.ParquetFormat",
    "flush.size": "10000",
    "rotate.interval.ms": "300000",
    "storage.class": "io.confluent.connect.s3.storage.S3Storage",
    "partitioner.class": "io.confluent.connect.storage.partitioner.TimeBasedPartitioner",
    "path.format": "'year'=YYYY/'month'=MM/'day'=dd/'hour'=HH",
    "timestamp.extractor": "RecordField",
    "timestamp.field": "exchange_timestamp"
  }
}
```

**Backup Frequency**: Continuous (every 5 minutes)

**Retention**: 30 days for critical topics (`trades.executed`, `audit.logs`)

**Storage Cost**:
- 10K msg/sec × 500 bytes × 86,400 sec/day = 432GB/day raw
- Parquet compression: ~130GB/day compressed
- 30 days × 130GB = 3.9TB
- S3 cost: 3.9TB × $0.023/GB = $89/month

#### Recovery Procedure

**Scenario**: Kafka cluster lost (all brokers down, unrecoverable)

**Steps**:
```bash
# 1. Provision new Kafka cluster
docker-compose up -d kafka

# 2. Recreate topics
kafka-topics.sh --bootstrap-server localhost:9092 \
  --create --topic market.ticks.asx \
  --partitions 100 --replication-factor 3

# 3. Restore from S3 backup using Kafka Connect Source
cat > restore-config.json <<EOF
{
  "name": "s3-to-kafka-restore",
  "config": {
    "connector.class": "io.confluent.connect.s3.source.S3SourceConnector",
    "s3.region": "ap-southeast-2",
    "s3.bucket.name": "k2-kafka-backups",
    "format.class": "io.confluent.connect.s3.format.parquet.ParquetFormat",
    "topics": "market.ticks.asx",
    "tasks.max": "10"
  }
}
EOF

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @restore-config.json

# 4. Monitor restore progress
kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --group restore-consumer --describe
```

**Expected Recovery Time**: 1-2 hours for 30 days of data (depends on data volume)

---

### Iceberg Data Lake

#### Backup Method: S3 Cross-Region Replication

**Configuration**:
```xml
<!-- S3 bucket replication rule -->
<ReplicationConfiguration>
  <Role>arn:aws:iam::123456789:role/s3-replication</Role>
  <Rule>
    <ID>replicate-iceberg-warehouse</ID>
    <Status>Enabled</Status>
    <Priority>1</Priority>
    <Filter>
      <Prefix>warehouse/</Prefix>
    </Filter>
    <Destination>
      <Bucket>arn:aws:s3:::k2-warehouse-replica-us-west-2</Bucket>
      <ReplicationTime>
        <Status>Enabled</Status>
        <Time>
          <Minutes>15</Minutes>
        </Time>
      </ReplicationTime>
    </Destination>
  </Rule>
</ReplicationConfiguration>
```

**Replication Lag**: < 15 minutes (S3 SLA)

**S3 Versioning**: Enabled (recover from accidental deletes)

**Lifecycle Policy**:
```json
{
  "Rules": [
    {
      "Id": "archive-old-data",
      "Status": "Enabled",
      "Transitions": [
        {
          "Days": 90,
          "StorageClass": "GLACIER"
        },
        {
          "Days": 730,
          "StorageClass": "DEEP_ARCHIVE"
        }
      ],
      "NoncurrentVersionExpiration": {
        "NoncurrentDays": 30
      }
    }
  ]
}
```

#### Recovery Procedure

**Scenario 1: Accidental File Deletion**

**Steps**:
```bash
# 1. List deleted versions
aws s3api list-object-versions \
  --bucket k2-warehouse \
  --prefix warehouse/market_data/ticks/

# 2. Restore specific version
aws s3api restore-object \
  --bucket k2-warehouse \
  --key warehouse/market_data/ticks/data/00001.parquet \
  --version-id <version-id> \
  --restore-request Days=7

# 3. Copy restored file to active location
aws s3 cp s3://k2-warehouse/warehouse/market_data/ticks/data/00001.parquet \
  s3://k2-warehouse/warehouse/market_data/ticks/data/00001.parquet
```

**Expected Recovery Time**: 5 minutes (instant for Standard storage, hours for Glacier)

---

**Scenario 2: Primary Region Failure**

**Steps**:
```bash
# 1. Update Iceberg catalog to point to replica region
# (Requires PostgreSQL catalog failover first - see below)

# 2. Update application S3 endpoints
export AWS_S3_ENDPOINT=https://s3.us-west-2.amazonaws.com
export CATALOG_WAREHOUSE=s3://k2-warehouse-replica-us-west-2/warehouse/

# 3. Restart Iceberg REST catalog
docker restart iceberg-rest

# 4. Verify queries work against replica
duckdb -c "SELECT COUNT(*) FROM iceberg_scan('warehouse/market_data/ticks')"
```

**Expected Recovery Time**: 10 minutes (catalog failover + DNS propagation)

---

### PostgreSQL Catalog

#### Backup Method: Continuous Archiving (WAL Shipping)

**Configuration**:
```ini
# postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'aws s3 cp %p s3://k2-postgres-wal/%f'
archive_timeout = 300  # Archive every 5 minutes
```

**Base Backup**:
```bash
# Daily full backup (cron: 0 2 * * *)
pg_basebackup -h localhost -U postgres \
  --format=tar --gzip \
  --wal-method=stream \
  --checkpoint=fast \
  --compress=9 \
  --pgdata=/backup/postgres-$(date +%Y%m%d).tar.gz

# Upload to S3
aws s3 cp /backup/postgres-*.tar.gz s3://k2-postgres-backups/
```

**Retention**:
- Daily backups: 30 days
- WAL archives: 7 days

#### Recovery Procedure

**Scenario: PostgreSQL Corruption or Data Loss**

**Steps**:
```bash
# 1. Stop PostgreSQL
systemctl stop postgresql

# 2. Restore base backup
cd /var/lib/postgresql/data
rm -rf *

# Download latest base backup
aws s3 cp s3://k2-postgres-backups/postgres-20260109.tar.gz .
tar xzf postgres-20260109.tar.gz

# 3. Create recovery configuration
cat > recovery.conf <<EOF
restore_command = 'aws s3 cp s3://k2-postgres-wal/%f %p'
recovery_target_time = '2026-01-09 14:30:00'
EOF

# 4. Start PostgreSQL in recovery mode
systemctl start postgresql

# 5. Monitor recovery progress
tail -f /var/log/postgresql/postgresql.log

# 6. Promote to primary
pg_ctl promote -D /var/lib/postgresql/data
```

**Expected Recovery Time**: 15 minutes (basebackup restore + WAL replay)

**RPO**: 5 minutes (WAL archive interval)

---

### Schema Registry

#### Backup Method: Daily Export to Git

**Implementation**:
```bash
#!/bin/bash
# scripts/backup-schemas.sh

set -e

BACKUP_DIR="backups/schemas/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Export all schemas
curl -s http://localhost:8081/subjects | jq -r '.[]' | while read subject; do
  echo "Backing up schema: $subject"

  # Get latest version
  curl -s "http://localhost:8081/subjects/$subject/versions/latest" \
    > "$BACKUP_DIR/${subject}.json"
done

# Commit to git
cd backups/schemas
git add .
git commit -m "Schema backup: $(date +%Y-%m-%d)"
git push origin main

echo "Schema backup completed: $BACKUP_DIR"
```

**Schedule**: Daily at 3 AM (cron: `0 3 * * *`)

**Storage**: Git repository (version-controlled)

#### Recovery Procedure

**Scenario: Schema Registry Data Loss**

**Steps**:
```bash
# 1. Restore schemas from git backup
cd backups/schemas
git pull origin main

# 2. Re-register all schemas
for schema_file in $(ls *.json); do
  subject=$(basename "$schema_file" .json)

  echo "Registering schema: $subject"

  curl -X POST "http://localhost:8081/subjects/$subject/versions" \
    -H "Content-Type: application/vnd.schemaregistry.v1+json" \
    -d @"$schema_file"
done

# 3. Verify schemas registered
curl -s http://localhost:8081/subjects | jq

# 4. Restart producers (will re-register schemas on startup)
kubectl rollout restart deployment/market-data-producer
```

**Expected Recovery Time**: 5 minutes

**RPO**: 24 hours (daily backup)

---

## Multi-Region Architecture

### Design: Active-Passive Replication

**Primary Region**: ap-southeast-2 (Sydney)
**Secondary Region**: us-west-2 (Oregon)

```
┌─────────────────────────────────────────────────────────────┐
│             Primary Region (ap-southeast-2)                 │
├─────────────────────────────────────────────────────────────┤
│  Kafka Cluster (3 brokers)                                  │
│    ↓                                                         │
│  Iceberg Warehouse (S3: k2-warehouse-primary)               │
│    ↓                                                         │
│  PostgreSQL Catalog (Primary)                               │
└────────────────────┬────────────────────────────────────────┘
                     │
                     │ MirrorMaker 2 (Kafka Replication)
                     │ S3 Cross-Region Replication
                     │ PostgreSQL Streaming Replication
                     ↓
┌─────────────────────────────────────────────────────────────┐
│             Secondary Region (us-west-2)                    │
├─────────────────────────────────────────────────────────────┤
│  Kafka Cluster (3 brokers) [STANDBY]                        │
│    ↓                                                         │
│  Iceberg Warehouse (S3: k2-warehouse-replica)               │
│    ↓                                                         │
│  PostgreSQL Catalog (Read Replica)                          │
└─────────────────────────────────────────────────────────────┘
```

### Kafka Replication: MirrorMaker 2

**Configuration**:
```properties
# mm2.properties
clusters = primary, secondary

primary.bootstrap.servers = kafka-ap-southeast-2:9092
secondary.bootstrap.servers = kafka-us-west-2:9092

# Replication flows
primary->secondary.enabled = true
secondary->primary.enabled = false

# Topics to replicate
primary->secondary.topics = market\..*

# Replication settings
replication.factor = 3
sync.topic.configs.enabled = true
emit.heartbeats.enabled = true
```

**Monitoring**:
```
# MirrorMaker 2 lag
kafka_mirrormaker_lag_seconds{source_cluster="primary", target_cluster="secondary"}

# Alert: Lag > 10 seconds
```

### PostgreSQL Streaming Replication

**Configuration**:
```ini
# Primary: postgresql.conf
wal_level = replica
max_wal_senders = 10
wal_keep_size = 16GB

# Replica: postgresql.conf
hot_standby = on
```

**Setup**:
```bash
# On replica server
pg_basebackup -h primary-host -D /var/lib/postgresql/data -U replication -P

# Create replication slot on primary
psql -h primary-host -U postgres -c "SELECT * FROM pg_create_physical_replication_slot('replica_1');"

# Replica: recovery.conf
primary_conninfo = 'host=primary-host port=5432 user=replication'
primary_slot_name = 'replica_1'
```

**Monitoring**:
```sql
-- Check replication lag on primary
SELECT client_addr, state, sync_state,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes
FROM pg_stat_replication;

-- Alert: lag_bytes > 100MB
```

---

## Failover Procedures

### Primary Region Failure (Complete Outage)

**Detection**:
- Health checks fail for 3 consecutive minutes
- No heartbeats from primary Kafka brokers
- PostgreSQL primary unreachable

**Automatic Failover Steps**:

```bash
# 1. DNS failover (Route53 health checks trigger automatically)
# api.k2platform.com → us-west-2 load balancer

# 2. Promote PostgreSQL replica to primary
ssh replica-host "pg_ctl promote -D /var/lib/postgresql/data"

# 3. Update Iceberg catalog config
kubectl set env deployment/iceberg-rest \
  CATALOG_URI=jdbc:postgresql://replica-host:5432/iceberg_catalog \
  CATALOG_WAREHOUSE=s3://k2-warehouse-replica-us-west-2/

# 4. Start Kafka producers in secondary region
kubectl scale deployment/market-data-producer --replicas=5

# 5. Update application configs to use secondary region
kubectl set env deployment/query-api \
  KAFKA_BOOTSTRAP_SERVERS=kafka-us-west-2:9092 \
  S3_ENDPOINT=https://s3.us-west-2.amazonaws.com

# 6. Verify services healthy
kubectl get pods
curl https://api.k2platform.com/health
```

**Expected Failover Time**: 5 minutes (automated)

**Data Loss**:
- Kafka: Last 30 seconds (MirrorMaker 2 lag)
- Iceberg: Last 60 seconds (S3 replication lag)
- PostgreSQL: Last 5 minutes (WAL shipping interval)

### Failback to Primary Region

**Trigger**: Primary region restored and verified stable

**Steps**:
```bash
# 1. Verify primary region healthy
kubectl --context primary get nodes

# 2. Sync secondary → primary (reverse replication)
# (Manually configure reverse MirrorMaker 2 flow)

# 3. Promote primary PostgreSQL
ssh primary-host "pg_ctl promote"

# 4. DNS cutover back to primary
aws route53 change-resource-record-sets \
  --hosted-zone-id Z1234567 \
  --change-batch file://dns-primary.json

# 5. Scale down secondary region
kubectl --context secondary scale deployment/market-data-producer --replicas=0

# 6. Resume normal operations
```

**Expected Failback Time**: 30 minutes (includes data sync validation)

---

## DR Testing

### Quarterly DR Drill

**Schedule**: First Saturday of every quarter (2 AM AEST)

**Objective**: Validate RTO/RPO targets, identify gaps in procedures

**Drill Procedure**:

```markdown
## DR Drill Checklist

### Pre-Drill (T-1 week)
- [ ] Notify all stakeholders (platform team, SRE, management)
- [ ] Schedule change window (2 AM - 6 AM AEST)
- [ ] Prepare drill scripts and runbooks
- [ ] Backup all data (extra precaution)

### Drill Execution (T-0)
**T+0:00** - Simulate primary region failure
- [ ] Stop Kafka brokers in primary region
- [ ] Stop PostgreSQL primary
- [ ] Block S3 access to primary region (AWS Network ACL)

**T+0:05** - Initiate failover
- [ ] Execute failover scripts
- [ ] Promote PostgreSQL replica
- [ ] Update DNS records
- [ ] Start producers in secondary region

**T+0:15** - Verify secondary region operational
- [ ] Check all services healthy
- [ ] Execute sample queries
- [ ] Verify data integrity (compare record counts)
- [ ] Test ingestion (send synthetic market data)

**T+0:30** - Measure RPO/RTO
- [ ] Calculate data loss (compare timestamps)
- [ ] Calculate recovery time (failure → fully operational)
- [ ] Document any deviations from plan

**T+1:00** - Failback to primary
- [ ] Sync data secondary → primary
- [ ] Promote primary PostgreSQL
- [ ] DNS cutover back to primary
- [ ] Verify normal operations

### Post-Drill (T+1 week)
- [ ] Write DR drill report
- [ ] Update runbooks based on lessons learned
- [ ] Create Jira tickets for identified gaps
- [ ] Present results to leadership
```

**Success Criteria**:
- RTO < 15 minutes ✓
- RPO < 60 seconds ✓
- No data corruption ✓
- All critical services operational ✓

**Last Drill Results** (2026-01-01):
- **RTO Achieved**: 4 minutes 32 seconds ✓
- **RPO Achieved**: 18 seconds ✓
- **Data Integrity**: 100% match ✓
- **Issues Found**: Schema Registry manual recovery (automated in next release)

---

## Incident Response

### Severity Levels

| Severity | Definition | RTO | Example |
|----------|------------|-----|---------|
| **P0** | Complete service outage | 15 min | Primary region down |
| **P1** | Critical functionality broken | 1 hour | Kafka ingestion halted |
| **P2** | Degraded performance | 4 hours | Query latency p99 > 10s |
| **P3** | Minor issue | 1 day | Non-critical schema validation warning |

### Incident Command Structure

**P0/P1 Incidents**:
1. **Incident Commander** (IC): Platform Lead
2. **Technical Lead** (TL): Senior Engineer (executes recovery)
3. **Communications Lead** (CL): Product Manager (stakeholder updates)
4. **Scribe**: SRE (documents actions and timeline)

**Communication Plan**:
- **Internal**: Slack #incidents channel (real-time updates)
- **External**: Status page https://status.k2platform.com
- **Management**: Email updates every 30 minutes

**Post-Incident**:
- Blameless post-mortem within 48 hours
- Action items tracked in Jira
- Runbook updates within 1 week

---

## Monitoring & Alerts

### Disaster Recovery Metrics

```
# Backup health
backup_last_success_timestamp{component="kafka|postgres|schemas"}
backup_size_bytes{component="kafka|postgres|schemas"}

# Replication lag
kafka_mirrormaker_lag_seconds{source, target}
postgres_replication_lag_bytes
s3_replication_lag_seconds

# DR readiness
dr_drill_last_run_timestamp
dr_drill_success{drill_date}
dr_failover_time_seconds{drill_date}
```

### Critical Alerts

```yaml
# Kafka backup failure
- alert: KafkaBackupFailed
  expr: time() - backup_last_success_timestamp{component="kafka"} > 3600
  for: 15m
  severity: critical
  summary: "Kafka backup has not run in 1 hour"

# PostgreSQL replication lag
- alert: PostgresReplicationLag
  expr: postgres_replication_lag_bytes > 100000000  # 100MB
  for: 5m
  severity: critical
  summary: "PostgreSQL replication lag > 100MB"

# MirrorMaker lag
- alert: MirrorMakerLag
  expr: kafka_mirrormaker_lag_seconds > 60
  for: 5m
  severity: warning
  summary: "Kafka replication lag > 60 seconds"
```

---

## Cost Analysis

### Backup Storage Costs

| Component | Daily Backup | 30-Day Retention | Monthly Cost |
|-----------|--------------|------------------|--------------|
| Kafka (S3) | 130GB | 3.9TB | $89 |
| PostgreSQL (S3) | 10GB | 300GB | $7 |
| Iceberg (S3 Replication) | 130GB | 3.9TB | $89 |
| **Total** | **270GB** | **8.1TB** | **$185/month** |

### Multi-Region Costs

| Cost Category | Primary Region | Secondary Region | Total |
|---------------|----------------|------------------|-------|
| Compute (EC2) | $2,000 | $1,000 (standby) | $3,000 |
| Storage (S3) | $2,500 | $2,500 (replica) | $5,000 |
| Data Transfer | $500 | $500 (replication) | $1,000 |
| **Total** | **$5,000** | **$4,000** | **$9,000/month** |

**DR Premium**: $4,000/month (80% increase vs single region)

**Cost Optimization**:
- Use S3 Intelligent Tiering (save 60% on cold data)
- Reduce secondary region compute (scale up only during failover)
- Archive old backups to Glacier Deep Archive ($1/TB/month)

---

## Related Documentation

- [Failure & Recovery](./FAILURE_RECOVERY.md) - Component-specific recovery runbooks
- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Degrade gracefully principle
- [Latency & Backpressure](./LATENCY_BACKPRESSURE.md) - Circuit breaker design