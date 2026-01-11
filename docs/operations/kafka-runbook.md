# Kafka Operations Runbook

**Version**: 1.0
**Last Updated**: 2026-01-10
**Owner**: Platform Team

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Topic Management](#topic-management)
- [Schema Management](#schema-management)
- [Troubleshooting](#troubleshooting)
- [Common Operations](#common-operations)
- [Monitoring](#monitoring)
- [Emergency Procedures](#emergency-procedures)

---

## Overview

This runbook covers operational procedures for the K2 Kafka infrastructure using the exchange + asset class topic architecture.

### Architecture Summary

- **Topic Naming**: `market.{asset_class}.{data_type}.{exchange}`
- **Current Deployment**: ASX (equities, 30 partitions) + Binance (crypto, 40 partitions)
- **Schema Strategy**: Asset-class-level shared schemas
- **Configuration**: `config/kafka/topics.yaml`

---

## Prerequisites

### Required Services

All services must be running:

```bash
docker compose ps
```

Expected services (all should be "Up" and "healthy"):
- kafka
- schema-registry-1
- schema-registry-2
- postgres
- minio
- iceberg-rest
- prometheus
- grafana
- kafka-ui

### Python Environment

Activate virtual environment and ensure dependencies installed:

```bash
source venv/bin/activate
pip install confluent-kafka pyyaml structlog httpx pydantic-settings boto3 pyiceberg
```

### Verify Kafka Connection

```bash
# Check Kafka is accessible
python -c "
from confluent_kafka.admin import AdminClient
admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
metadata = admin.list_topics(timeout=10)
print(f'✅ Connected to Kafka. Found {len(metadata.topics)} topics.')
"
```

### Verify Schema Registry Connection

```bash
# Check Schema Registry is accessible
curl -s http://localhost:8081/subjects | python -m json.tool
```

---

## Topic Management

### List All Topics

```bash
# Using Python
source venv/bin/activate
python -c "
from confluent_kafka.admin import AdminClient
admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
metadata = admin.list_topics(timeout=10)
for topic in sorted(metadata.topics.keys()):
    if topic.startswith('market.'):
        partitions = len(metadata.topics[topic].partitions)
        print(f'{topic}: {partitions} partitions')
"
```

```bash
# Using Kafka CLI (if available in container)
docker exec -it k2-kafka kafka-topics --list --bootstrap-server localhost:9092
```

```bash
# Using Kafka UI
# Navigate to http://localhost:8080
```

### Describe Topic Details

```bash
source venv/bin/activate
python -c "
from confluent_kafka.admin import AdminClient, ConfigResource, ResourceType

admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
topic_name = 'market.equities.trades.asx'

# Get partition info
metadata = admin.list_topics(timeout=10)
topic = metadata.topics[topic_name]
print(f'Topic: {topic_name}')
print(f'Partitions: {len(topic.partitions)}')

# Get config
resource = ConfigResource(ResourceType.TOPIC, topic_name)
configs = admin.describe_configs([resource])
config_result = configs[resource].result()

print('\\nConfiguration:')
for key in ['retention.ms', 'compression.type', 'cleanup.policy']:
    if key in config_result:
        print(f'  {key}: {config_result[key].value}')
"
```

### Create New Topics

**Scenario**: Adding a new exchange (e.g., NYSE)

1. **Edit configuration**:

```bash
vim config/kafka/topics.yaml
```

Add exchange under `asset_classes.equities.exchanges`:

```yaml
nyse:
  name: "New York Stock Exchange"
  partitions: 100
  country: "US"
  timezone: "America/New_York"
  trading_hours: "09:30-16:00 EST"
  description: "Largest US equities exchange"
```

2. **Run topic creation**:

```bash
source venv/bin/activate
python scripts/init_infra.py
```

3. **Verify topics created**:

```bash
python -c "
from k2.kafka import get_topic_builder
builder = get_topic_builder()
topics = builder.list_topics_for_exchange('equities', 'nyse')
print('NYSE topics:', topics)
"
```

### Delete Topics (CAUTION)

⚠️ **WARNING**: Deletes are permanent. Data will be lost.

```bash
# Using Python
source venv/bin/activate
python -c "
from confluent_kafka.admin import AdminClient

admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
topics_to_delete = ['market.equities.trades.test']  # Specify topics

futures = admin.delete_topics(topics_to_delete, operation_timeout=30)
for topic, future in futures.items():
    try:
        future.result()
        print(f'✅ Deleted: {topic}')
    except Exception as e:
        print(f'❌ Failed to delete {topic}: {e}')
"
```

### Increase Partition Count

⚠️ **WARNING**: Can only INCREASE partitions, never decrease.

```bash
source venv/bin/activate
python -c "
from confluent_kafka.admin import AdminClient, NewPartitions

admin = AdminClient({'bootstrap.servers': 'localhost:9092'})

# Increase partitions
new_partitions = [NewPartitions('market.equities.trades.asx', 40)]  # Increase from 30 to 40
futures = admin.create_partitions(new_partitions)

for topic, future in futures.items():
    try:
        future.result()
        print(f'✅ Increased partitions for {topic}')
    except Exception as e:
        print(f'❌ Failed: {e}')
"
```

---

## Schema Management

### List All Schemas

```bash
curl -s http://localhost:8081/subjects | python -m json.tool
```

Or using Python:

```bash
source venv/bin/activate
python -c "
from confluent_kafka.schema_registry import SchemaRegistryClient
client = SchemaRegistryClient({'url': 'http://localhost:8081'})
subjects = client.get_subjects()
for subject in sorted(subjects):
    if subject.startswith('market.'):
        version = client.get_latest_version(subject)
        print(f'{subject}: schema ID {version.schema_id}, version {version.version}')
"
```

### View Schema Details

```bash
# Get latest version
curl -s http://localhost:8081/subjects/market.equities.trades-value/versions/latest | python -m json.tool

# Get specific version
curl -s http://localhost:8081/subjects/market.equities.trades-value/versions/1 | python -m json.tool
```

### Register New Schema Version

1. **Edit schema file**:

```bash
vim src/k2/schemas/trade.avsc
```

2. **Register updated schema**:

```bash
source venv/bin/activate
python -c "
import sys
sys.path.insert(0, 'src')
from k2.schemas import register_schemas
schema_ids = register_schemas('http://localhost:8081')
print('✅ Schemas registered:', schema_ids)
"
```

3. **Verify compatibility**:

Schema Registry will reject incompatible changes (BACKWARD mode). If rejected:
- Check error message
- Ensure new fields have defaults
- Ensure no required fields removed

### Delete Schema (CAUTION)

⚠️ **WARNING**: This is a soft delete. To permanently delete, use hard delete API.

```bash
# Soft delete (mark as deleted, can be restored)
curl -X DELETE http://localhost:8081/subjects/market.test.trades-value

# Hard delete (permanent, cannot restore)
curl -X DELETE http://localhost:8081/subjects/market.test.trades-value?permanent=true
```

---

## Troubleshooting

### Problem: Topics Not Created

**Symptoms**:
- `python scripts/init_infra.py` fails
- Topics missing from Kafka

**Diagnosis**:

1. Check Kafka is running:
```bash
docker compose ps kafka
```

2. Check Kafka logs:
```bash
docker compose logs kafka --tail=100
```

3. Test Kafka connection:
```bash
python -c "
from confluent_kafka.admin import AdminClient
admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
metadata = admin.list_topics(timeout=10)
print('Connected!')
"
```

**Resolution**:

- If Kafka not running: `docker compose up -d kafka`
- If connection refused: Check `bootstrap.servers` setting (should be `localhost:9092` for host)
- If timeout: Increase timeout or check network
- If "broker not available": Wait 30s for Kafka to fully start

### Problem: Schema Registration Fails

**Symptoms**:
- Schema registration returns 40X error
- "Schema being registered is incompatible"

**Diagnosis**:

1. Check Schema Registry is running:
```bash
docker compose ps schema-registry-1
curl http://localhost:8081/subjects
```

2. Check compatibility mode:
```bash
curl http://localhost:8081/config
```

3. Validate schema syntax:
```bash
python -c "
import json
with open('src/k2/schemas/trade.avsc') as f:
    schema = json.load(f)
    print('✅ Schema is valid JSON')
"
```

**Resolution**:

- **40401 (Subject not found)**: Normal for first registration
- **409 (Incompatible schema)**:
  - Add defaults to new fields: `{"name": "new_field", "type": ["null", "string"], "default": null}`
  - Don't remove required fields
  - Use schema evolution best practices
- **Schema Registry not responding**:
  ```bash
  docker compose restart schema-registry-1
  ```

### Problem: ModuleNotFoundError

**Symptoms**:
- Import errors when running scripts
- "No module named 'confluent_kafka'"

**Resolution**:

1. Ensure virtual environment activated:
```bash
source venv/bin/activate
which python  # Should show venv path
```

2. Install dependencies:
```bash
pip install confluent-kafka[schema-registry] pyyaml structlog httpx pydantic-settings boto3 pyiceberg
```

3. Verify installation:
```bash
python -c "import confluent_kafka; print('✅ confluent_kafka installed')"
```

### Problem: Topic Already Exists

**Symptoms**:
- "Topic already exists" error
- Cannot create topic with same name

**Resolution**:

This is usually informational, not an error. If you need to recreate:

1. Delete existing topic (CAUTION - data loss):
```bash
python -c "
from confluent_kafka.admin import AdminClient
admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
futures = admin.delete_topics(['market.equities.trades.test'])
for topic, future in futures.items():
    future.result()
    print(f'Deleted {topic}')
"
```

2. Recreate topic:
```bash
python scripts/init_infra.py
```

### Problem: Wrong Partition Count

**Symptoms**:
- Topic has different partition count than expected
- Config says 30, Kafka has 6

**Diagnosis**:

```bash
python -c "
from confluent_kafka.admin import AdminClient
from k2.kafka import get_topic_builder, DataType

admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
builder = get_topic_builder()

topic_config = builder.get_topic_config('equities', DataType.TRADES, 'asx')
metadata = admin.list_topics(timeout=10)

expected = topic_config.partitions
actual = len(metadata.topics[topic_config.topic_name].partitions)

print(f'Expected: {expected}, Actual: {actual}')
"
```

**Resolution**:

- **Actual < Expected**: Increase partitions (see "Increase Partition Count" above)
- **Actual > Expected**: Cannot decrease. Update config to match reality or delete/recreate topic

### Problem: Consumer Lag Growing

**Symptoms**:
- Consumer lag increasing over time
- Messages piling up

**Diagnosis**:

Check lag via Kafka UI (http://localhost:8080) or:

```bash
docker exec -it k2-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group k2-iceberg-writer \
  --describe
```

**Resolution**:

1. **Scale consumers**: Add more consumer instances
2. **Increase batch size**: Process more messages per poll
3. **Optimize processing**: Profile and optimize message handling
4. **Check downstream**: Ensure Iceberg writes aren't bottleneck
5. **Temporary**: Increase consumer timeout settings

### Problem: Hot Partitions

**Symptoms**:
- One partition has much more data than others
- Uneven consumer load

**Diagnosis**:

```bash
python -c "
from confluent_kafka.admin import AdminClient
admin = AdminClient({'bootstrap.servers': 'localhost:9092'})

topic = 'market.equities.trades.asx'
metadata = admin.list_topics(timeout=10)

# This only shows partition count, not size
# Use Kafka UI or JMX metrics for partition sizes
partitions = metadata.topics[topic].partitions
print(f'{topic} has {len(partitions)} partitions')
print('Use Kafka UI (http://localhost:8080) to see partition sizes')
"
```

**Resolution**:

1. **Identify hot keys**: Log partition keys to find popular symbols
2. **Increase partitions**: More partitions = better distribution
3. **Custom partitioner**: For very popular symbols, use dedicated partitions
4. **Monitor**: Set up alerts for partition imbalance >20%

---

## Common Operations

### Check System Health

```bash
#!/bin/bash
# health-check.sh

echo "=== K2 Kafka Health Check ==="

# Check Docker services
echo "1. Docker Services:"
docker compose ps | grep -E "(kafka|schema-registry)" | grep -v "Exit"

# Check Kafka connectivity
echo -e "\n2. Kafka Connection:"
python -c "
from confluent_kafka.admin import AdminClient
try:
    admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
    metadata = admin.list_topics(timeout=5)
    print(f'  ✅ Kafka: {len(metadata.topics)} topics')
except Exception as e:
    print(f'  ❌ Kafka: {e}')
"

# Check Schema Registry
echo -e "\n3. Schema Registry:"
if curl -sf http://localhost:8081/subjects > /dev/null; then
    count=$(curl -s http://localhost:8081/subjects | python -c "import sys, json; print(len(json.load(sys.stdin)))")
    echo "  ✅ Schema Registry: $count subjects"
else
    echo "  ❌ Schema Registry: Not responding"
fi

# Check topic configuration
echo -e "\n4. Topic Configuration:"
python -c "
from k2.kafka import get_topic_builder
try:
    builder = get_topic_builder()
    topics = builder.list_all_topics()
    print(f'  ✅ Config: {len(topics)} topics defined')
except Exception as e:
    print(f'  ❌ Config: {e}')
"

echo -e "\n=== Health Check Complete ==="
```

Make executable and run:

```bash
chmod +x health-check.sh
./health-check.sh
```

### Backup Configuration

```bash
#!/bin/bash
# backup-kafka-config.sh

BACKUP_DIR="backups/kafka-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup topic config
cp config/kafka/topics.yaml "$BACKUP_DIR/"

# Backup schemas
cp -r src/k2/schemas/*.avsc "$BACKUP_DIR/"

# Export topic list
python -c "
from confluent_kafka.admin import AdminClient
import json

admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
metadata = admin.list_topics(timeout=10)

topics = {}
for name, topic in metadata.topics.items():
    if name.startswith('market.'):
        topics[name] = {
            'partitions': len(topic.partitions),
        }

with open('$BACKUP_DIR/topics.json', 'w') as f:
    json.dump(topics, f, indent=2)
"

# Export schema subjects
curl -s http://localhost:8081/subjects > "$BACKUP_DIR/subjects.json"

echo "✅ Backup saved to $BACKUP_DIR"
```

### Restore from Backup

```bash
# 1. Restore configuration
cp backups/kafka-20260110-120000/topics.yaml config/kafka/

# 2. Recreate topics
python scripts/init_infra.py

# 3. Re-register schemas
python -c "
import sys
sys.path.insert(0, 'src')
from k2.schemas import register_schemas
register_schemas('http://localhost:8081')
"
```

### Clean Up Test Data

```bash
# Delete test topics
python -c "
from confluent_kafka.admin import AdminClient

admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
metadata = admin.list_topics(timeout=10)

test_topics = [t for t in metadata.topics.keys() if 'test' in t.lower()]
if test_topics:
    futures = admin.delete_topics(test_topics)
    for topic, future in futures.items():
        try:
            future.result()
            print(f'✅ Deleted: {topic}')
        except Exception as e:
            print(f'❌ Failed: {topic} - {e}')
else:
    print('No test topics found')
"
```

---

## Monitoring

### Key Metrics to Monitor

1. **Topic Metrics**:
   - Message rate (msgs/sec)
   - Byte rate (MB/sec)
   - Consumer lag (messages behind)

2. **Partition Metrics**:
   - Partition size (MB)
   - Leader distribution
   - Partition balance (even distribution)

3. **Schema Registry Metrics**:
   - Registration rate
   - Compatibility errors
   - Subject count

### Prometheus Queries

Access Prometheus at http://localhost:9090

**Kafka Messages In Rate**:
```promql
rate(kafka_server_brokertopicmetrics_messagesin_total[5m])
```

**Kafka Bytes In Rate**:
```promql
rate(kafka_server_brokertopicmetrics_bytesin_total[5m])
```

**Consumer Lag**:
```promql
kafka_consumergroup_lag
```

### Grafana Dashboards

Access Grafana at http://localhost:3000

Recommended dashboards:
1. **Kafka Overview**: Broker health, topic metrics
2. **Topic Details**: Per-topic message rates, sizes
3. **Consumer Lag**: Consumer group lag monitoring

### Alert Thresholds

**Critical Alerts**:
- Consumer lag > 5000 messages for >5 minutes
- Broker disk usage > 90%
- Schema Registry down
- Topic creation failures

**Warning Alerts**:
- Consumer lag > 1000 messages
- Message rate spike >3x baseline
- Partition imbalance >20%
- Broker disk usage >70%

---

## Emergency Procedures

### Kafka Broker Down

**Detection**:
- Health check fails
- Producers/consumers disconnected
- Kafka UI shows broker offline

**Immediate Actions**:

1. Check broker status:
```bash
docker compose ps kafka
docker compose logs kafka --tail=100
```

2. Restart broker:
```bash
docker compose restart kafka
```

3. Verify recovery:
```bash
# Wait 30s for startup
sleep 30
python -c "
from confluent_kafka.admin import AdminClient
admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
metadata = admin.list_topics(timeout=10)
print(f'✅ Broker recovered. {len(metadata.topics)} topics available.')
"
```

### Schema Registry Down

**Detection**:
- Schema registration fails
- 5xx errors from Schema Registry
- Cannot fetch schemas

**Immediate Actions**:

1. Check status:
```bash
docker compose ps schema-registry-1 schema-registry-2
curl http://localhost:8081/subjects
```

2. Restart Schema Registry:
```bash
docker compose restart schema-registry-1 schema-registry-2
```

3. Verify recovery:
```bash
sleep 10
curl http://localhost:8081/subjects
```

### Data Loss Suspected

**Detection**:
- Sequence gaps detected
- Messages missing
- Consumer lag suddenly drops to 0

**Investigation Steps**:

1. Check Kafka retention:
```bash
python -c "
from confluent_kafka.admin import AdminClient, ConfigResource, ResourceType

admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
topic = 'market.equities.trades.asx'

resource = ConfigResource(ResourceType.TOPIC, topic)
configs = admin.describe_configs([resource])
config_result = configs[resource].result()

retention = config_result.get('retention.ms')
print(f'Retention: {retention.value} ms ({int(retention.value)/3600000} hours)')
"
```

2. Check if data was purged due to retention:
   - If retention = 7 days, data older than 7 days is deleted
   - This is NORMAL, not data loss

3. Check for hard disk issues:
```bash
docker exec -it k2-kafka df -h
```

4. Check Kafka logs for errors:
```bash
docker compose logs kafka | grep -i error | tail -50
```

### Complete System Reset

⚠️ **WARNING**: This deletes ALL data. Only use for dev/test environments.

```bash
# Stop all services
docker compose down

# Remove volumes (deletes all data)
docker compose down -v

# Restart services
docker compose up -d

# Wait for services to be healthy (60-90 seconds)
sleep 90

# Recreate infrastructure
python scripts/init_infra.py

# Verify
./health-check.sh
```

---

## Appendix

### Useful Commands Reference

```bash
# List topics
docker exec -it k2-kafka kafka-topics --list --bootstrap-server localhost:9092

# Describe topic
docker exec -it k2-kafka kafka-topics --describe --topic market.equities.trades.asx --bootstrap-server localhost:9092

# List consumer groups
docker exec -it k2-kafka kafka-consumer-groups --list --bootstrap-server localhost:9092

# Describe consumer group
docker exec -it k2-kafka kafka-consumer-groups --describe --group k2-iceberg-writer --bootstrap-server localhost:9092

# Produce test message
echo '{"symbol":"BHP","price":45.50}' | docker exec -i k2-kafka kafka-console-producer --topic market.equities.trades.asx --bootstrap-server localhost:9092

# Consume messages
docker exec -it k2-kafka kafka-console-consumer --topic market.equities.trades.asx --from-beginning --bootstrap-server localhost:9092 --max-messages 10
```

### Configuration File Locations

- **Topic Config**: `config/kafka/topics.yaml`
- **Avro Schemas**: `src/k2/schemas/*.avsc`
- **Docker Compose**: `docker-compose.yml`
- **Environment**: `.env` (create from `.env.example`)

### Log Locations

```bash
# Kafka logs
docker compose logs kafka

# Schema Registry logs
docker compose logs schema-registry-1

# All infrastructure logs
docker compose logs
```

### Support Contacts

- **Platform Team**: [your-team@example.com]
- **On-Call**: [on-call rotation]
- **Documentation**: `docs/architecture/kafka-topic-strategy.md`
- **Runbook**: This file

---

**Last Updated**: 2026-01-10
**Next Review**: 2026-04-10
