# Step 1: Infrastructure Validation & Setup Scripts

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 4-6 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Docker Desktop running, docker-compose.yml configured
- **Blocks**: All subsequent steps (foundation layer)

## Goal
Ensure docker-compose environment works correctly and create initialization scripts. This validates that all infrastructure services (Kafka, Schema Registry, MinIO, PostgreSQL, Iceberg REST, Prometheus, Grafana) are healthy and accessible before building application code.

---

## Implementation

### 1.1 Validate Docker Compose Services

**Files**: `docker-compose.yml`, `Makefile`

**Actions**:
1. Start all services: `make docker-up` (or `docker-compose up -d`)
2. Verify health of each service:
   - Kafka: `docker exec k2-kafka kafka-broker-api-versions --bootstrap-server localhost:9092`
   - Schema Registry: `curl http://localhost:8081/subjects`
   - MinIO: `curl http://localhost:9000/minio/health/live`
   - PostgreSQL: `docker exec k2-postgres pg_isready`
   - Iceberg REST: `curl http://localhost:8181/v1/config`
   - Prometheus: `curl http://localhost:9090/-/healthy`
   - Grafana: `curl http://localhost:3000/api/health`

**Test**: Create `tests/integration/test_infrastructure.py`

```python
"""Integration tests for Docker infrastructure."""
import pytest
import requests
from confluent_kafka.admin import AdminClient
from sqlalchemy import create_engine, text

@pytest.mark.integration
class TestInfrastructure:
    """Validate all services are healthy."""

    def test_kafka_broker_available(self):
        """Kafka should respond to admin requests."""
        admin = AdminClient({'bootstrap.servers': 'localhost:9092'})
        metadata = admin.list_topics(timeout=5)
        assert metadata is not None

    def test_schema_registry_available(self):
        """Schema Registry should list subjects."""
        response = requests.get('http://localhost:8081/subjects')
        assert response.status_code == 200

    def test_minio_available(self):
        """MinIO should respond to health checks."""
        response = requests.get('http://localhost:9000/minio/health/live')
        assert response.status_code == 200

    def test_postgres_available(self):
        """PostgreSQL should accept connections."""
        engine = create_engine('postgresql://iceberg:iceberg@localhost:5432/iceberg_catalog')
        with engine.connect() as conn:
            result = conn.execute(text('SELECT 1'))
            assert result.scalar() == 1

    def test_iceberg_rest_available(self):
        """Iceberg REST catalog should return config."""
        response = requests.get('http://localhost:8181/v1/config')
        assert response.status_code == 200
```

**Why**: Confirms infrastructure is working before writing application code. Prevents debugging application code when the real issue is infrastructure.

### 1.2 Create Infrastructure Initialization Script

**File**: `scripts/init_infra.py`

**Purpose**: Automate setup of Kafka topics, Iceberg namespaces, and validation.

**Actions**:
1. Create Kafka topics with appropriate partitioning:
   - `market.trades.raw` (6 partitions, keyed by `exchange.symbol`)
   - `market.quotes.raw` (6 partitions, keyed by `exchange.symbol`)
   - `market.reference_data` (1 partition, compacted topic for dimension data)
2. Create Iceberg namespaces and tables (placeholders, actual schemas in Step 3)
3. Validate MinIO buckets exist (`warehouse`, `data`, `backups`)

**Implementation**:

```python
#!/usr/bin/env python3
"""Initialize K2 platform infrastructure."""
import sys
from typing import Dict, List
from confluent_kafka.admin import AdminClient, NewTopic
import boto3
from pyiceberg.catalog import load_catalog
import structlog

logger = structlog.get_logger()

def create_kafka_topics(bootstrap_servers: str) -> None:
    """Create required Kafka topics."""
    admin = AdminClient({'bootstrap.servers': bootstrap_servers})

    topics = [
        NewTopic(
            topic='market.trades.raw',
            num_partitions=6,
            replication_factor=1,
            config={
                'compression.type': 'lz4',
                'retention.ms': '604800000',  # 7 days
            }
        ),
        NewTopic(
            topic='market.quotes.raw',
            num_partitions=6,
            replication_factor=1,
            config={
                'compression.type': 'lz4',
                'retention.ms': '604800000',
            }
        ),
        NewTopic(
            topic='market.reference_data',
            num_partitions=1,
            replication_factor=1,
            config={
                'cleanup.policy': 'compact',
                'compression.type': 'lz4',
            }
        ),
    ]

    futures = admin.create_topics(topics)
    for topic, future in futures.items():
        try:
            future.result()
            logger.info(f"Topic created", topic=topic)
        except Exception as e:
            logger.error(f"Failed to create topic", topic=topic, error=str(e))

def validate_minio_buckets() -> None:
    """Ensure MinIO buckets exist."""
    s3 = boto3.client(
        's3',
        endpoint_url='http://localhost:9000',
        aws_access_key_id='admin',
        aws_secret_access_key='password',
    )

    for bucket in ['warehouse', 'data', 'backups']:
        try:
            s3.head_bucket(Bucket=bucket)
            logger.info(f"Bucket exists", bucket=bucket)
        except:
            logger.error(f"Bucket missing", bucket=bucket)
            sys.exit(1)

def create_iceberg_namespaces() -> None:
    """Create Iceberg namespaces."""
    catalog = load_catalog(
        "default",
        **{
            "uri": "http://localhost:8181",
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "admin",
            "s3.secret-access-key": "password",
        }
    )

    for namespace in ['market_data', 'reference_data']:
        try:
            catalog.create_namespace(namespace)
            logger.info(f"Namespace created", namespace=namespace)
        except Exception as e:
            logger.warning(f"Namespace exists or failed", namespace=namespace, error=str(e))

if __name__ == '__main__':
    create_kafka_topics('localhost:9092')
    validate_minio_buckets()
    create_iceberg_namespaces()
    logger.info("Infrastructure initialization complete")
```

**Test**: Update `Makefile` with `make init-infra` target, verify it runs without errors.

---

## Testing

### Unit Tests
None required (integration tests only for infrastructure validation).

### Integration Tests
- `tests/integration/test_infrastructure.py` - All services health checks
- Manual execution of `scripts/init_infra.py`

### Commands
```bash
# Start infrastructure
make docker-up

# Run infrastructure tests
pytest tests/integration/test_infrastructure.py -v

# Initialize infrastructure
make init-infra
```

---

## Validation Checklist

- [ ] All Docker containers running (check with `docker ps`)
- [ ] Kafka broker responds to admin API calls
- [ ] Schema Registry returns empty subjects list
- [ ] MinIO health check returns 200
- [ ] PostgreSQL accepts connections
- [ ] Iceberg REST catalog returns config
- [ ] Prometheus health endpoint returns OK
- [ ] Grafana health endpoint returns OK
- [ ] Kafka topics created successfully (check Kafka UI at http://localhost:8080)
- [ ] MinIO buckets exist (check console at http://localhost:9001)
- [ ] Iceberg namespaces created
- [ ] `make init-infra` completes without errors
- [ ] Integration tests pass: `pytest tests/integration/test_infrastructure.py -v`

---

## Rollback Procedure

If this step needs to be reverted:

1. **Stop all Docker services**:
   ```bash
   make docker-down
   ```

2. **Remove volumes (optional - only if you want to clear all data)**:
   ```bash
   docker-compose down -v
   ```

3. **Delete Kafka topics** (if created):
   ```bash
   docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --delete --topic market.trades.raw
   docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --delete --topic market.quotes.raw
   docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --delete --topic market.reference_data
   ```

4. **Delete test files** (if created):
   ```bash
   rm tests/integration/test_infrastructure.py
   rm scripts/init_infra.py
   ```

5. **Verify clean state**:
   ```bash
   docker ps  # Should show no k2-related containers
   ```

---

## Notes & Decisions

### Decisions Made
- N/A (infrastructure validation step)

### Issues Encountered
[Space for documenting any issues during implementation]

### Optimization Opportunities
[Space for noting potential improvements]

### References
- Docker Compose documentation: https://docs.docker.com/compose/
- Kafka Admin API: https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html#kafka-admin
