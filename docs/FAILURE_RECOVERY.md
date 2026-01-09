# Failure & Recovery Runbook

**Last Updated**: 2026-01-09
**Owners**: Platform Team, SRE
**Scope**: Production incident response

---

## Overview

This runbook covers detection, blast radius, recovery, and post-mortem for critical failure scenarios. Every section follows the same structure:

1. **Detection Signal**: How do we know this is happening?
2. **Blast Radius**: What's broken? What still works?
3. **Immediate Response**: Steps to stop the bleeding
4. **Recovery Procedure**: Return to normal operation
5. **Follow-Up Fix**: Prevent recurrence

**Expected Audience**: On-call engineer with basic Kafka/Iceberg knowledge

---

## Scenario 1: Kafka Broker Loss During Market Open

### Detection Signal

**Alert**: `kafka_broker_down{broker_id="2"} == 1`

**Symptoms**:
- Grafana dashboard shows 1 of 3 brokers offline
- Consumer lag spiking for partitions on broker 2
- Producer timeout errors in logs: `Broker: Not available`

**Triage**:
```bash
# Check broker status
docker exec kafka-broker-2 kafka-broker-api-versions.sh \
  --bootstrap-server localhost:9092

# Check which partitions were on failed broker
kafka-topics.sh --bootstrap-server localhost:9092 \
  --describe --topic market.ticks.asx
```

---

### Blast Radius

**Broken**:
- Partitions with leader on broker 2 cannot produce/consume
- ~33% of symbols unavailable (assuming 3 brokers, even distribution)

**Still Working**:
- Partitions on broker 1 and 3 continue normally
- Iceberg writes for available symbols proceed
- Query API serves cached/historical data

**Critical Window**: 5-30 seconds (time for leader election)

---

### Immediate Response

**DO NOT** restart broker immediately (may cause data loss if improperly synchronized)

**Steps**:
1. **Verify replication factor**:
   ```bash
   # Check if data is replicated
   kafka-topics.sh --bootstrap-server localhost:9092 \
     --describe --topic market.ticks.asx \
     | grep "Leader: -1"  # -1 = no leader (bad)
   ```

2. **If replicas exist** (replication factor ≥ 2):
   - Kafka automatically elects new leader from in-sync replicas (ISR)
   - Wait 30 seconds for leader election
   - Monitor `kafka_under_replicated_partitions` (should decrease)

3. **If NO replicas** (replication factor = 1):
   - **ALERT CRITICAL**: Data loss imminent
   - Halt all producers immediately:
     ```bash
     # Emergency producer shutdown
     kubectl scale deployment market-data-producer --replicas=0
     ```
   - Restore broker from backup (see Recovery Procedure)

---

### Recovery Procedure

#### Case A: Broker Disk Failure (Hardware)

```bash
# 1. Provision new broker with same broker.id
docker run -d \
  --name kafka-broker-2 \
  -e KAFKA_BROKER_ID=2 \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=0@broker-0:9093,1@broker-1:9093,2@broker-2:9093 \
  -v kafka-broker-2-data:/var/lib/kafka/data \
  confluentinc/cp-kafka:7.6.0

# 2. Wait for broker to join cluster (partition reassignment is automatic)
watch -n 5 'kafka-broker-api-versions.sh --bootstrap-server localhost:9092'

# 3. Verify all partitions have leaders
kafka-topics.sh --bootstrap-server localhost:9092 \
  --describe --under-replicated-partitions
# Should return empty (no under-replicated partitions)

# 4. Resume producers
kubectl scale deployment market-data-producer --replicas=5
```

**Expected Recovery Time**: 5 minutes (broker startup + partition sync)

---

#### Case B: Network Partition (Broker isolated)

```bash
# 1. Verify network connectivity
docker exec kafka-broker-2 ping kafka-broker-1
docker exec kafka-broker-2 ping kafka-broker-3

# 2. If network restored, broker rejoins automatically
# Check logs for "Rejoined group" messages
docker logs kafka-broker-2 --tail 100 | grep "Rejoined"

# 3. Monitor replication lag
kafka-replica-verification.sh \
  --broker-list localhost:9092,localhost:9093,localhost:9094 \
  --topic-white-list 'market\..*'
```

**Expected Recovery Time**: 30 seconds (network restoration + leader election)

---

### Follow-Up Fix

**Root Cause Analysis**:
- Hardware failure → Replace disk, add monitoring for disk health
- Network partition → Review network redundancy (bonded interfaces?)
- OOM kill → Increase broker heap size, review retention policies

**Preventive Measures**:
1. **Increase replication factor to 3**:
   ```bash
   kafka-configs.sh --bootstrap-server localhost:9092 \
     --alter --entity-type topics --entity-name market.ticks.asx \
     --add-config min.insync.replicas=2
   ```

2. **Set up rack awareness** (distribute replicas across availability zones):
   ```properties
   # broker config
   broker.rack=ap-southeast-2a  # Broker 1
   broker.rack=ap-southeast-2b  # Broker 2
   broker.rack=ap-southeast-2c  # Broker 3
   ```

3. **Add broker health checks**:
   ```yaml
   # Kubernetes liveness probe
   livenessProbe:
     exec:
       command:
         - kafka-broker-api-versions.sh
         - --bootstrap-server
         - localhost:9092
     initialDelaySeconds: 60
     periodSeconds: 30
   ```

---

## Scenario 2: Consumer Lagging Hours Behind

### Detection Signal

**Alert**: `kafka_consumer_lag_seconds{group="strategy_alpha"} > 3600`

**Symptoms**:
- Grafana dashboard shows consumer lag growing
- Query API returns stale data (minutes/hours old)
- Trading strategies making decisions on outdated ticks

**Triage**:
```bash
# Check consumer group lag
kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --group strategy_alpha \
  --describe

# Output example:
# TOPIC                PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
# market.ticks.asx  0          1847291         5847291         4000000  <-- 4M lag
```

---

### Blast Radius

**Broken**:
- Real-time strategies operating on stale data (potential financial loss)
- Alerts firing incorrectly (based on old market conditions)

**Still Working**:
- Kafka continues ingesting new data (lag doesn't affect producers)
- Historical queries work normally

**Critical Window**: Depends on strategy tolerance (some strategies useless after 10-second lag)

---

### Immediate Response

**Goal**: Determine if lag is temporary spike or sustained growth

**Steps**:
1. **Check if lag is increasing**:
   ```bash
   # Monitor lag for 1 minute
   watch -n 5 'kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
     --group strategy_alpha --describe | grep LAG'
   ```

2. **If lag is decreasing** → Self-healing, no action needed

3. **If lag is increasing** → Consumer cannot keep up

   **Diagnosis**:
   ```bash
   # Check consumer CPU/memory
   kubectl top pod -l app=strategy-alpha-consumer

   # Check consumer logs for errors
   kubectl logs -l app=strategy-alpha-consumer --tail 1000 | grep ERROR
   ```

---

### Recovery Procedure

#### Case A: Consumer Processing Too Slow

**Root Cause**: Business logic is slow (e.g., external API call per message)

**Immediate Fix**: Scale horizontally

```bash
# Add more consumer instances (Kafka auto-rebalances partitions)
kubectl scale deployment strategy-alpha-consumer --replicas=10

# Monitor lag (should decrease as partitions redistribute)
watch -n 10 'kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --group strategy_alpha --describe'
```

**Temporary Optimization**: Enable graceful degradation (see [Latency & Backpressure](./LATENCY_BACKPRESSURE.md))

```python
# In consumer code, skip expensive enrichment under load
if consumer_lag > 1_000_000:
    skip_enrichment = True
    log.warning("High lag detected, skipping enrichment")
```

**Expected Recovery Time**: 10-30 minutes (depends on lag size)

---

#### Case B: Partition Skew (One Partition Overloaded)

**Root Cause**: One symbol (e.g., BHP) generates 10x more messages than others

**Diagnosis**:
```bash
# Check per-partition lag
kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --group strategy_alpha --describe

# Output shows one partition with massive lag:
# PARTITION  LAG
# 0          100
# 1          150
# 2          3900000  <-- Skewed
```

**Fix**: Repartition topic with better key distribution

```python
# OLD: Partition by symbol (BHP gets one partition)
partition_key = symbol

# NEW: Partition by symbol + random salt
partition_key = f"{symbol}_{hash(message_id) % 10}"
```

**Migration**: Requires creating new topic, dual-write, then cutover (see RFC process)

**Expected Recovery Time**: Days (requires schema migration)

---

#### Case C: Iceberg Write Bottleneck

**Root Cause**: Consumer processing fast, but Iceberg writes are slow (S3 throttling)

**Diagnosis**:
```bash
# Check Iceberg write latency
curl -s localhost:9090/api/v1/query?query='iceberg_write_duration_p99' | jq

# If p99 > 5 seconds, Iceberg is bottleneck
```

**Immediate Fix**: Enable spill-to-disk (see [Latency & Backpressure](./LATENCY_BACKPRESSURE.md))

```python
# In consumer config
enable_spill_to_disk = True
spill_threshold_lag = 1_000_000
```

**Long-term Fix**: Increase S3 request rate limit (contact AWS support)

**Expected Recovery Time**: 1-2 hours (spill-to-disk, then async flush)

---

### Follow-Up Fix

1. **Add autoscaling**:
   ```yaml
   # Kubernetes HPA
   apiVersion: autoscaling/v2
   kind: HorizontalPodAutoscaler
   metadata:
     name: strategy-alpha-consumer
   spec:
     scaleTargetRef:
       apiVersion: apps/v1
       kind: Deployment
       name: strategy-alpha-consumer
     minReplicas: 3
     maxReplicas: 20
     metrics:
     - type: External
       external:
         metric:
           name: kafka_consumer_lag_messages
         target:
           type: AverageValue
           averageValue: "500000"  # Scale up if lag > 500K
   ```

2. **Add circuit breaker**:
   ```python
   if consumer_lag > 10_000_000:
       # Halt consumption, page on-call
       circuit_breaker.open()
   ```

---

## Scenario 3: Incompatible Schema Pushed Accidentally

### Detection Signal

**Alert**: `schema_registry_compatibility_failures_total > 0`

**Symptoms**:
- Producers failing with `Schema registration failed: Incompatible schema`
- Consumers throwing `Avro deserialization error`
- New deployments stuck in rollout

**Triage**:
```bash
# Check latest schema versions
curl http://localhost:8081/subjects/market.ticks.asx-value/versions

# Get schema details
curl http://localhost:8081/subjects/market.ticks.asx-value/versions/5 | jq
```

---

### Blast Radius

**Broken**:
- Producers cannot publish new messages (schema rejected)
- Consumers on old schema cannot deserialize new messages (if backwards-incompatible)

**Still Working**:
- Existing data in Kafka/Iceberg (already serialized)
- Consumers still processing backlog

**Critical Window**: Immediate (market data stops flowing)

---

### Immediate Response

**DO NOT** roll back schema immediately (may break consumers already upgraded)

**Steps**:
1. **Assess compatibility direction**:
   ```bash
   # Check compatibility mode
   curl http://localhost:8081/config/market.ticks.asx-value
   # Expected: {"compatibilityLevel": "BACKWARD"}
   ```

2. **If BACKWARD compatibility broken**:
   - Old consumers cannot read new schema → CRITICAL
   - Rollback producer deployment immediately:
     ```bash
     kubectl rollout undo deployment market-data-producer
     ```

3. **If FORWARD compatibility broken**:
   - New consumers cannot read old schema → Upgrade all consumers first
   - Less critical (old messages still processable)

---

### Recovery Procedure

#### Case A: Backwards-Incompatible Change (e.g., Removed Required Field)

**Example**:
```json
// OLD schema (version 4)
{
  "type": "record",
  "fields": [
    {"name": "symbol", "type": "string"},
    {"name": "price", "type": "double"}  // Required field
  ]
}

// NEW schema (version 5) - INCOMPATIBLE
{
  "type": "record",
  "fields": [
    {"name": "symbol", "type": "string"}
    // "price" field removed → breaks old consumers
  ]
}
```

**Fix**: Add field back with default value

```json
// CORRECTED schema (version 6)
{
  "type": "record",
  "fields": [
    {"name": "symbol", "type": "string"},
    {"name": "price", "type": ["null", "double"], "default": null}  // Optional with default
  ]
}
```

**Deployment**:
```bash
# 1. Register corrected schema
curl -X POST http://localhost:8081/subjects/market.ticks.asx-value/versions \
  -H "Content-Type: application/json" \
  -d @corrected_schema.json

# 2. Deploy producer with schema version 6
kubectl set image deployment/market-data-producer \
  app=market-data-producer:v6

# 3. Verify producers healthy
kubectl rollout status deployment/market-data-producer
```

**Expected Recovery Time**: 5 minutes (schema registration + deployment)

---

#### Case B: Accidental Schema Deletion

**Symptom**: Schema subject deleted from registry

```bash
# Check if subject exists
curl http://localhost:8081/subjects/market.ticks.asx-value/versions
# {"error_code": 40401, "message": "Subject not found"}
```

**Fix**: Restore from backup or re-register

```bash
# 1. Retrieve schema from git (schemas are version-controlled)
cd schemas/
git log --oneline market_ticks_asx.avsc

# 2. Re-register schema
curl -X POST http://localhost:8081/subjects/market.ticks.asx-value/versions \
  -H "Content-Type: application/json" \
  -d @schemas/market_ticks_asx.avsc

# 3. Restart producers (will re-register on startup)
kubectl rollout restart deployment/market-data-producer
```

**Expected Recovery Time**: 2 minutes

---

### Follow-Up Fix

1. **Enforce schema compatibility in CI/CD**:
   ```yaml
   # .github/workflows/schema-validation.yml
   - name: Validate schema compatibility
     run: |
       curl -X POST http://schema-registry:8081/compatibility/subjects/market.ticks.asx-value/versions/latest \
         -H "Content-Type: application/json" \
         -d @new_schema.json \
       | jq -e '.is_compatible == true'
   ```

2. **Add schema approval process**:
   - Schema changes require 2 approvals (Platform Lead + domain expert)
   - 30-day deprecation window for breaking changes

3. **Schema Registry backups**:
   ```bash
   # Daily backup job
   curl http://localhost:8081/subjects | jq -r '.[]' | while read subject; do
     curl http://localhost:8081/subjects/$subject/versions/latest \
       > backups/schemas/${subject}_$(date +%Y%m%d).json
   done
   ```

---

## Scenario 4: Iceberg Catalog / Metadata Slowdown

### Detection Signal

**Alert**: `iceberg_catalog_query_duration_p99 > 10s`

**Symptoms**:
- Query API timeouts (30+ second response times)
- Iceberg writes hanging (waiting for catalog lock)
- PostgreSQL CPU at 100%

**Triage**:
```bash
# Check PostgreSQL active queries
docker exec -it postgres psql -U iceberg -c "
  SELECT pid, state, query_start, wait_event, query
  FROM pg_stat_activity
  WHERE state != 'idle'
  ORDER BY query_start;
"

# Check for table locks
docker exec -it postgres psql -U iceberg -c "
  SELECT * FROM pg_locks
  WHERE NOT granted;
"
```

---

### Blast Radius

**Broken**:
- New queries fail (catalog cannot return metadata)
- Iceberg writes blocked (cannot commit snapshots)

**Still Working**:
- Kafka ingestion (continues buffering)
- Cached query results (served stale data)

**Critical Window**: Minutes (queries timeout, writes buffer in-memory)

---

### Immediate Response

**Steps**:
1. **Kill long-running queries**:
   ```sql
   -- Find culprit query
   SELECT pid, query, state, query_start
   FROM pg_stat_activity
   WHERE query_start < NOW() - INTERVAL '30 seconds'
     AND state = 'active';

   -- Kill it
   SELECT pg_terminate_backend(12345);  -- Replace with actual PID
   ```

2. **Check for missing indexes**:
   ```sql
   -- Find table scans (should use indexes)
   EXPLAIN ANALYZE
   SELECT * FROM iceberg_tables WHERE namespace = 'market_data';

   -- If "Seq Scan" appears, add index:
   CREATE INDEX idx_namespace ON iceberg_tables(namespace);
   ```

3. **Increase connection pool**:
   ```python
   # In Iceberg catalog config
   catalog = load_catalog(
       'default',
       **{
           'uri': 'postgresql://localhost:5432/iceberg',
           'pool_size': 50,  # Increase from 10
           'max_overflow': 100
       }
   )
   ```

---

### Recovery Procedure

#### Case A: Catalog Metadata Table Bloat

**Root Cause**: `iceberg_snapshots` table has 1M+ rows (never cleaned up)

**Diagnosis**:
```sql
-- Check table sizes
SELECT
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

**Fix**: Run Iceberg expiration (deletes old snapshots)

```python
from pyiceberg.catalog import load_catalog

catalog = load_catalog('default')
table = catalog.load_table('market_data.ticks')

# Expire snapshots older than 90 days
table.expire_snapshots(older_than=datetime.now() - timedelta(days=90))
```

**Expected Recovery Time**: 10-60 minutes (depends on snapshot count)

---

#### Case B: PostgreSQL Deadlock

**Symptom**: Multiple writers waiting for locks

```sql
-- Check for deadlocks
SELECT * FROM pg_stat_database WHERE datname = 'iceberg';
-- deadlocks column > 0
```

**Fix**: Restart PostgreSQL (clears locks)

```bash
docker restart postgres

# Wait for health check
docker exec postgres pg_isready
```

**Expected Recovery Time**: 30 seconds

---

### Follow-Up Fix

1. **Add PostgreSQL read replicas**:
   ```yaml
   # docker-compose.yml
   postgres-replica:
     image: postgres:16
     environment:
       POSTGRES_PRIMARY_HOST: postgres
       POSTGRES_REPLICATION_MODE: slave
   ```

2. **Enable query result caching**:
   ```python
   # Cache Iceberg metadata for 5 minutes
   @lru_cache(maxsize=1000, ttl=300)
   def get_table_metadata(table_name: str):
       return catalog.load_table(table_name)
   ```

3. **Add automated snapshot expiration**:
   ```bash
   # Cron job (runs daily at 2 AM)
   0 2 * * * python scripts/expire_snapshots.py --days 90
   ```

---

## Post-Incident Process

### 1. Incident Report Template

```markdown
## Incident Summary
- **Date**: 2026-01-09
- **Duration**: 14:32 - 15:45 (1h 13min)
- **Severity**: P1 (Critical)
- **Impacted Systems**: Market data ingestion, Strategy Alpha

## Timeline
- 14:32: Alert fired (kafka_broker_down)
- 14:35: On-call paged
- 14:40: Investigation started
- 15:10: Root cause identified (disk failure)
- 15:30: New broker provisioned
- 15:45: System fully recovered

## Root Cause
Kafka broker 2 disk failure (hardware fault)

## Resolution
Provisioned new broker with same broker.id, partitions rebalanced automatically

## Impact
- 33% of symbols unavailable for 1h 13min
- Estimated data loss: 0 messages (replicas existed)
- Trading strategies offline for 1h 13min

## Follow-Up Actions
- [ ] Replace failed disk (Assigned: SRE team, Due: 2026-01-10)
- [ ] Increase replication factor to 3 (Assigned: Platform team, Due: 2026-01-12)
- [ ] Add disk health monitoring (Assigned: SRE team, Due: 2026-01-15)
```

### 2. Blameless Post-Mortem

**Required within 48 hours of P1/P2 incidents**

- Led by incident commander
- Attendees: All involved engineers + Platform Lead
- Focus: Process improvement, not individual blame
- Output: Action items with owners and due dates

---

## Related Documentation

- [Platform Principles](./PLATFORM_PRINCIPLES.md) - Degrade gracefully principle
- [Latency & Backpressure](./LATENCY_BACKPRESSURE.md) - Circuit breakers and degradation
- [Market Data Guarantees](./MARKET_DATA_GUARANTEES.md) - Sequence gap handling