# K2 Market Data Platform - Phase 1 Implementation Plan

**Status**: Ready for Implementation
**Target**: Principal Data Engineer Review
**Approach**: Test-Alongside Development (not strict TDD)
**Scope**: Core Platform - Storage, Ingestion, Query, Observability

---

## Executive Summary

This plan implements Phase 1 of the K2 Market Data Platform, demonstrating production-ready patterns for a distributed market data system. The implementation follows a pragmatic test-alongside approach, building testable components with clear validation criteria at each step.

**Key Deliverables**:
- Complete storage layer with Iceberg lakehouse + ACID guarantees
- Kafka-based ingestion with Avro schemas and Schema Registry
- DuckDB query engine with replay capabilities
- Python library, CLI tools, and REST API
- Comprehensive testing (unit + integration, 80%+ coverage)
- Basic observability (Prometheus metrics + Grafana dashboards)

**Out of Scope for Phase 1**:
- Complex governance (RBAC, encryption)
- GraphQL API
- Performance load testing beyond functional validation
- Chaos engineering and failure injection tests
- Multi-region replication

---

## Implementation Sequence

The plan follows a bottom-up approach: infrastructure → schemas → storage → ingestion → query → API → observability.

```
Infrastructure Setup (Step 1)
         ↓
Schema Design (Step 2)
         ↓
Storage Layer (Steps 3-5)
         ↓
Ingestion Layer (Steps 6-8)
         ↓
Query Layer (Steps 9-11)
         ↓
API Layer (Steps 12-13)
         ↓
Observability (Step 14)
         ↓
End-to-End Testing (Step 15)
```

---

## Step 1: Infrastructure Validation & Setup Scripts

**Goal**: Ensure docker-compose environment works correctly and create initialization scripts.

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

**Validation**:
- Run `make init-infra`
- Check Kafka UI (http://localhost:8080) for topics
- Check MinIO console (http://localhost:9001) for buckets
- No errors in script output

**Why**: Automates repetitive setup, ensures consistent environment across developers, documents infrastructure requirements as code.

---

## Step 2: Schema Design & Registration

**Goal**: Define Avro schemas for market data that match the CSV sample data structure.

### 2.1 Design Avro Schemas

**Files**:
- `src/k2/schemas/trade.avsc` (Avro schema definition)
- `src/k2/schemas/quote.avsc`
- `src/k2/schemas/reference_data.avsc`

**Trade Schema** (`trade.avsc`):

```json
{
  "type": "record",
  "name": "Trade",
  "namespace": "com.k2.market_data",
  "doc": "Market trade execution event",
  "fields": [
    {
      "name": "symbol",
      "type": "string",
      "doc": "Trading symbol (e.g., BHP, RIO)"
    },
    {
      "name": "company_id",
      "type": "int",
      "doc": "Company identifier for enrichment"
    },
    {
      "name": "exchange",
      "type": "string",
      "default": "ASX",
      "doc": "Exchange identifier"
    },
    {
      "name": "exchange_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      },
      "doc": "Exchange timestamp (epoch milliseconds)"
    },
    {
      "name": "price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 6
      },
      "doc": "Trade execution price"
    },
    {
      "name": "volume",
      "type": "long",
      "doc": "Number of shares traded"
    },
    {
      "name": "qualifiers",
      "type": "int",
      "doc": "Trade qualifier code (0-3)"
    },
    {
      "name": "venue",
      "type": "string",
      "doc": "Market venue (e.g., X for primary ASX)"
    },
    {
      "name": "buyer_id",
      "type": ["null", "string"],
      "default": null,
      "doc": "Buyer identifier (optional)"
    },
    {
      "name": "ingestion_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      },
      "doc": "Platform ingestion timestamp for latency tracking"
    },
    {
      "name": "sequence_number",
      "type": ["null", "long"],
      "default": null,
      "doc": "Platform-assigned sequence number for ordering"
    }
  ]
}
```

**Quote Schema** (`quote.avsc`):

```json
{
  "type": "record",
  "name": "Quote",
  "namespace": "com.k2.market_data",
  "doc": "Best bid/ask quote event",
  "fields": [
    {
      "name": "symbol",
      "type": "string",
      "doc": "Trading symbol"
    },
    {
      "name": "company_id",
      "type": "int",
      "doc": "Company identifier"
    },
    {
      "name": "exchange",
      "type": "string",
      "default": "ASX"
    },
    {
      "name": "exchange_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      }
    },
    {
      "name": "bid_price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 6
      }
    },
    {
      "name": "bid_volume",
      "type": "long"
    },
    {
      "name": "ask_price",
      "type": {
        "type": "bytes",
        "logicalType": "decimal",
        "precision": 18,
        "scale": 6
      }
    },
    {
      "name": "ask_volume",
      "type": "long"
    },
    {
      "name": "ingestion_timestamp",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      }
    },
    {
      "name": "sequence_number",
      "type": ["null", "long"],
      "default": null
    }
  ]
}
```

**Reference Data Schema** (`reference_data.avsc`):

```json
{
  "type": "record",
  "name": "ReferenceData",
  "namespace": "com.k2.market_data",
  "doc": "Company reference data for enrichment",
  "fields": [
    {
      "name": "company_id",
      "type": "int",
      "doc": "Primary key"
    },
    {
      "name": "symbol",
      "type": "string"
    },
    {
      "name": "company_name",
      "type": "string"
    },
    {
      "name": "isin",
      "type": "string",
      "doc": "International Securities Identification Number"
    },
    {
      "name": "start_date",
      "type": "string",
      "doc": "Listing start date (MM/DD/YYYY)"
    },
    {
      "name": "end_date",
      "type": ["null", "string"],
      "default": null,
      "doc": "Delisting date if applicable"
    }
  ]
}
```

### 2.2 Create Schema Management Module

**File**: `src/k2/schemas/__init__.py`

```python
"""Schema management for K2 platform."""
import json
from pathlib import Path
from typing import Dict
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema
import structlog

logger = structlog.get_logger()

SCHEMA_DIR = Path(__file__).parent

def load_avro_schema(schema_name: str) -> str:
    """Load Avro schema from .avsc file."""
    schema_path = SCHEMA_DIR / f"{schema_name}.avsc"
    return schema_path.read_text()

def register_schemas(schema_registry_url: str) -> Dict[str, int]:
    """Register all Avro schemas with Schema Registry."""
    client = SchemaRegistryClient({'url': schema_registry_url})

    schema_ids = {}
    for schema_name in ['trade', 'quote', 'reference_data']:
        schema_str = load_avro_schema(schema_name)
        schema = Schema(schema_str, schema_type='AVRO')

        subject = f"market.{schema_name}s.raw-value"
        schema_id = client.register_schema(subject, schema)
        schema_ids[schema_name] = schema_id

        logger.info(
            "Schema registered",
            subject=subject,
            schema_id=schema_id,
        )

    return schema_ids
```

### 2.3 Test Schema Registration

**File**: `tests/unit/test_schemas.py`

```python
"""Unit tests for schema validation."""
import pytest
from pathlib import Path
import json
import avro.schema

@pytest.mark.unit
class TestSchemas:
    """Validate Avro schemas are well-formed."""

    def test_trade_schema_valid(self):
        """Trade schema should parse without errors."""
        schema_path = Path('src/k2/schemas/trade.avsc')
        schema_dict = json.loads(schema_path.read_text())
        schema = avro.schema.parse(json.dumps(schema_dict))

        assert schema.name == 'Trade'
        assert schema.namespace == 'com.k2.market_data'
        assert 'symbol' in [f.name for f in schema.fields]

    def test_quote_schema_valid(self):
        """Quote schema should parse without errors."""
        schema_path = Path('src/k2/schemas/quote.avsc')
        schema_dict = json.loads(schema_path.read_text())
        schema = avro.schema.parse(json.dumps(schema_dict))

        assert schema.name == 'Quote'

    def test_all_schemas_have_required_fields(self):
        """All market data schemas need key ordering fields."""
        for schema_name in ['trade', 'quote']:
            schema_path = Path(f'src/k2/schemas/{schema_name}.avsc')
            schema_dict = json.loads(schema_path.read_text())
            field_names = [f['name'] for f in schema_dict['fields']]

            # Required for ordering and partitioning
            assert 'symbol' in field_names
            assert 'exchange_timestamp' in field_names
            assert 'ingestion_timestamp' in field_names
```

**Integration Test**: `tests/integration/test_schema_registry.py`

```python
"""Integration tests for Schema Registry."""
import pytest
from k2.schemas import register_schemas
from confluent_kafka.schema_registry import SchemaRegistryClient

@pytest.mark.integration
class TestSchemaRegistry:
    """Test schema registration with real Schema Registry."""

    def test_register_all_schemas(self):
        """Schemas should register without errors."""
        schema_ids = register_schemas('http://localhost:8081')

        assert 'trade' in schema_ids
        assert 'quote' in schema_ids
        assert schema_ids['trade'] > 0

    def test_schemas_are_backward_compatible(self):
        """New schema versions must be backward compatible."""
        client = SchemaRegistryClient({'url': 'http://localhost:8081'})

        # Re-register same schema (simulates version bump)
        schema_ids = register_schemas('http://localhost:8081')

        # Should succeed (backward compatibility enforced by registry)
        assert schema_ids['trade'] > 0
```

**Validation**:
- Run `pytest tests/unit/test_schemas.py -v` (should pass immediately)
- Run `pytest tests/integration/test_schema_registry.py -v` (requires Docker)
- Verify schemas in Kafka UI: http://localhost:8080/ui/clusters/k2-market-data/schema-registry

**Why**: Avro schemas enforce contracts between producers and consumers. Schema Registry ensures backward compatibility, preventing production incidents from schema mismatches. Testing schemas before implementation prevents downstream errors.

---

## Step 3: Storage Layer - Iceberg Table Initialization

**Goal**: Create Iceberg tables with correct partitioning and sorting for time-travel queries.

### 3.1 Implement Iceberg Catalog Manager

**File**: `src/k2/storage/catalog.py`

```python
"""Iceberg catalog management."""
from typing import Optional, Dict, Any
from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import (
    NestedField, StringType, LongType, TimestampType, IntegerType, DecimalType
)
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import DayTransform, HourTransform
from pyiceberg.table.sorting import SortOrder, SortField
from pyiceberg.transforms import IdentityTransform
import structlog

logger = structlog.get_logger()

class IcebergCatalogManager:
    """Manages Iceberg catalog and table operations."""

    def __init__(
        self,
        catalog_uri: str = "http://localhost:8181",
        s3_endpoint: str = "http://localhost:9000",
        s3_access_key: str = "admin",
        s3_secret_key: str = "password",
    ):
        """Initialize catalog connection."""
        self.catalog = load_catalog(
            "k2_catalog",
            **{
                "uri": catalog_uri,
                "s3.endpoint": s3_endpoint,
                "s3.access-key-id": s3_access_key,
                "s3.secret-access-key": s3_secret_key,
                "s3.path-style-access": "true",
            }
        )
        logger.info("Iceberg catalog initialized", uri=catalog_uri)

    def create_trades_table(self, namespace: str = "market_data") -> None:
        """Create trades table with appropriate partitioning."""
        schema = Schema(
            NestedField(1, "symbol", StringType(), required=True),
            NestedField(2, "company_id", IntegerType(), required=True),
            NestedField(3, "exchange", StringType(), required=True),
            NestedField(4, "exchange_timestamp", TimestampType(), required=True),
            NestedField(5, "price", DecimalType(18, 6), required=True),
            NestedField(6, "volume", LongType(), required=True),
            NestedField(7, "qualifiers", IntegerType(), required=True),
            NestedField(8, "venue", StringType(), required=True),
            NestedField(9, "buyer_id", StringType(), required=False),
            NestedField(10, "ingestion_timestamp", TimestampType(), required=True),
            NestedField(11, "sequence_number", LongType(), required=False),
        )

        # Partition by day for efficient time-range queries
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=4,
                field_id=1000,
                transform=DayTransform(),
                name="exchange_date"
            )
        )

        # Sort by timestamp and sequence for ordered scans
        sort_order = SortOrder(
            SortField(source_id=4, transform=IdentityTransform()),
            SortField(source_id=11, transform=IdentityTransform()),
        )

        table_id = f"{namespace}.trades"
        try:
            self.catalog.create_table(
                identifier=table_id,
                schema=schema,
                partition_spec=partition_spec,
                sort_order=sort_order,
                properties={
                    "write.format.default": "parquet",
                    "write.parquet.compression-codec": "zstd",
                    "write.metadata.compression-codec": "gzip",
                }
            )
            logger.info("Trades table created", table=table_id)
        except Exception as e:
            logger.error("Failed to create table", table=table_id, error=str(e))
            raise

    def create_quotes_table(self, namespace: str = "market_data") -> None:
        """Create quotes table."""
        schema = Schema(
            NestedField(1, "symbol", StringType(), required=True),
            NestedField(2, "company_id", IntegerType(), required=True),
            NestedField(3, "exchange", StringType(), required=True),
            NestedField(4, "exchange_timestamp", TimestampType(), required=True),
            NestedField(5, "bid_price", DecimalType(18, 6), required=True),
            NestedField(6, "bid_volume", LongType(), required=True),
            NestedField(7, "ask_price", DecimalType(18, 6), required=True),
            NestedField(8, "ask_volume", LongType(), required=True),
            NestedField(9, "ingestion_timestamp", TimestampType(), required=True),
            NestedField(10, "sequence_number", LongType(), required=False),
        )

        partition_spec = PartitionSpec(
            PartitionField(
                source_id=4,
                field_id=1000,
                transform=DayTransform(),
                name="exchange_date"
            )
        )

        sort_order = SortOrder(
            SortField(source_id=4, transform=IdentityTransform()),
            SortField(source_id=10, transform=IdentityTransform()),
        )

        table_id = f"{namespace}.quotes"
        self.catalog.create_table(
            identifier=table_id,
            schema=schema,
            partition_spec=partition_spec,
            sort_order=sort_order,
            properties={
                "write.format.default": "parquet",
                "write.parquet.compression-codec": "zstd",
            }
        )
        logger.info("Quotes table created", table=table_id)
```

### 3.2 Update Infrastructure Init Script

**File**: Update `scripts/init_infra.py` to call table creation:

```python
# Add to init_infra.py
from k2.storage.catalog import IcebergCatalogManager

def create_iceberg_tables() -> None:
    """Create Iceberg tables."""
    manager = IcebergCatalogManager()
    manager.create_trades_table()
    manager.create_quotes_table()
    logger.info("Iceberg tables created")

if __name__ == '__main__':
    # ... existing code ...
    create_iceberg_tables()
```

### 3.3 Test Table Creation

**File**: `tests/integration/test_iceberg_catalog.py`

```python
"""Integration tests for Iceberg catalog."""
import pytest
from k2.storage.catalog import IcebergCatalogManager
from pyiceberg.catalog import load_catalog

@pytest.mark.integration
class TestIcebergCatalog:
    """Test Iceberg table operations."""

    def test_create_trades_table(self):
        """Trades table should be created with correct schema."""
        manager = IcebergCatalogManager()
        manager.create_trades_table()

        # Verify table exists
        catalog = manager.catalog
        table = catalog.load_table("market_data.trades")

        assert table is not None
        assert "symbol" in [f.name for f in table.schema().fields]
        assert "exchange_timestamp" in [f.name for f in table.schema().fields]

    def test_table_partition_spec(self):
        """Table should be partitioned by day."""
        manager = IcebergCatalogManager()
        table = manager.catalog.load_table("market_data.trades")

        # Verify daily partitioning
        partition_spec = table.spec()
        assert len(partition_spec.fields) == 1
        assert partition_spec.fields[0].name == "exchange_date"

    def test_table_sort_order(self):
        """Table should be sorted by timestamp and sequence."""
        manager = IcebergCatalogManager()
        table = manager.catalog.load_table("market_data.trades")

        sort_order = table.sort_order()
        assert len(sort_order.fields) == 2
```

**Validation**:
- Run `make init-infra` successfully
- Run `pytest tests/integration/test_iceberg_catalog.py -v`
- Check MinIO console: `warehouse/market_data.db/trades/metadata/` should exist
- Query PostgreSQL: `SELECT * FROM iceberg_tables;` (if catalog tracking exists)

**Why**: Iceberg provides ACID guarantees and time-travel queries. Daily partitioning optimizes time-range queries (common for market data). Sorting by timestamp+sequence enables efficient ordered scans for replay. Testing ensures tables are created correctly before writing data.

---

## Step 4: Storage Layer - Iceberg Writer

**Goal**: Implement writer that appends data to Iceberg tables with transaction safety.

### 4.1 Implement Iceberg Writer

**File**: `src/k2/storage/writer.py`

```python
"""Iceberg table writer with ACID guarantees."""
from typing import List, Dict, Any
from datetime import datetime
from decimal import Decimal
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.table import Table
from pyiceberg.exceptions import NoSuchTableError
import structlog

from k2.common.metrics import metrics

logger = structlog.get_logger()

class IcebergWriter:
    """Write data to Iceberg tables with transaction safety."""

    def __init__(
        self,
        catalog_uri: str = "http://localhost:8181",
        s3_endpoint: str = "http://localhost:9000",
        s3_access_key: str = "admin",
        s3_secret_key: str = "password",
    ):
        """Initialize writer."""
        self.catalog = load_catalog(
            "k2_catalog",
            **{
                "uri": catalog_uri,
                "s3.endpoint": s3_endpoint,
                "s3.access-key-id": s3_access_key,
                "s3.secret-access-key": s3_secret_key,
                "s3.path-style-access": "true",
            }
        )

    def write_trades(
        self,
        records: List[Dict[str, Any]],
        table_name: str = "market_data.trades"
    ) -> int:
        """
        Write trade records to Iceberg table.

        Args:
            records: List of trade dictionaries matching schema
            table_name: Fully qualified table name

        Returns:
            Number of records written
        """
        start_time = datetime.now()

        try:
            table = self.catalog.load_table(table_name)
        except NoSuchTableError:
            logger.error("Table not found", table=table_name)
            metrics.increment("iceberg_write_errors", tags={"reason": "table_not_found"})
            raise

        # Convert to PyArrow table
        arrow_table = self._records_to_arrow(records, "trade")

        try:
            # Append data (ACID transaction)
            table.append(arrow_table)

            duration_ms = (datetime.now() - start_time).total_seconds() * 1000

            logger.info(
                "Trades written to Iceberg",
                table=table_name,
                records=len(records),
                duration_ms=duration_ms,
            )

            metrics.histogram(
                "iceberg_write_duration_ms",
                duration_ms,
                tags={"table": "trades"}
            )
            metrics.increment(
                "iceberg_records_written",
                value=len(records),
                tags={"table": "trades"}
            )

            return len(records)

        except Exception as e:
            logger.error(
                "Failed to write trades",
                table=table_name,
                error=str(e),
            )
            metrics.increment("iceberg_write_errors", tags={"reason": "write_failed"})
            raise

    def _records_to_arrow(
        self,
        records: List[Dict[str, Any]],
        record_type: str
    ) -> pa.Table:
        """Convert records to PyArrow table."""
        if record_type == "trade":
            schema = pa.schema([
                ("symbol", pa.string()),
                ("company_id", pa.int32()),
                ("exchange", pa.string()),
                ("exchange_timestamp", pa.timestamp('ms')),
                ("price", pa.decimal128(18, 6)),
                ("volume", pa.int64()),
                ("qualifiers", pa.int32()),
                ("venue", pa.string()),
                ("buyer_id", pa.string()),
                ("ingestion_timestamp", pa.timestamp('ms')),
                ("sequence_number", pa.int64()),
            ])
        elif record_type == "quote":
            schema = pa.schema([
                ("symbol", pa.string()),
                ("company_id", pa.int32()),
                ("exchange", pa.string()),
                ("exchange_timestamp", pa.timestamp('ms')),
                ("bid_price", pa.decimal128(18, 6)),
                ("bid_volume", pa.int64()),
                ("ask_price", pa.decimal128(18, 6)),
                ("ask_volume", pa.int64()),
                ("ingestion_timestamp", pa.timestamp('ms')),
                ("sequence_number", pa.int64()),
            ])
        else:
            raise ValueError(f"Unknown record type: {record_type}")

        return pa.Table.from_pylist(records, schema=schema)
```

### 4.2 Test Writer

**File**: `tests/unit/test_iceberg_writer.py`

```python
"""Unit tests for Iceberg writer."""
import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch

from k2.storage.writer import IcebergWriter

@pytest.mark.unit
class TestIcebergWriter:
    """Test Iceberg writer logic."""

    def test_records_to_arrow_trade(self):
        """Should convert trade records to Arrow table."""
        writer = IcebergWriter()

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "price": Decimal("36.50"),
                "volume": 10000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            }
        ]

        arrow_table = writer._records_to_arrow(records, "trade")

        assert arrow_table.num_rows == 1
        assert "symbol" in arrow_table.column_names
        assert arrow_table["symbol"][0].as_py() == "BHP"
```

**Integration Test**: `tests/integration/test_iceberg_writer.py`

```python
"""Integration tests for Iceberg writer."""
import pytest
from datetime import datetime
from decimal import Decimal

from k2.storage.writer import IcebergWriter
from k2.storage.catalog import IcebergCatalogManager

@pytest.mark.integration
class TestIcebergWriterIntegration:
    """Test writing to real Iceberg tables."""

    def test_write_trades(self):
        """Should write trades and commit transaction."""
        # Ensure table exists
        catalog = IcebergCatalogManager()
        catalog.create_trades_table()

        writer = IcebergWriter()

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0, 0),
                "price": Decimal("36.50"),
                "volume": 10000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            }
        ]

        written = writer.write_trades(records)

        assert written == 1

        # Verify data exists in table
        table = catalog.catalog.load_table("market_data.trades")
        df = table.scan().to_pandas()

        assert len(df) >= 1
        assert "BHP" in df["symbol"].values
```

**Validation**:
- Run `pytest tests/unit/test_iceberg_writer.py -v`
- Run `pytest tests/integration/test_iceberg_writer.py -v`
- Check MinIO: `warehouse/market_data.db/trades/data/` should contain Parquet files
- Query with DuckDB: `SELECT * FROM iceberg_scan('warehouse/market_data.db/trades') LIMIT 10;`

**Why**: Iceberg's `append()` provides ACID transactions—either all records commit or none do. This prevents partial writes during failures. PyArrow provides efficient columnar conversion. Testing validates both conversion logic and end-to-end write path.

---

## Step 5: Storage Layer - Configuration Management

**Goal**: Centralize configuration for different environments (local, test, prod).

### 5.1 Create Configuration Module

**File**: `src/k2/common/config.py`

```python
"""Configuration management for K2 platform."""
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field

class KafkaConfig(BaseSettings):
    """Kafka connection configuration."""
    bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka bootstrap servers"
    )
    schema_registry_url: str = Field(
        default="http://localhost:8081",
        description="Schema Registry URL"
    )
    security_protocol: str = Field(
        default="PLAINTEXT",
        description="Security protocol (PLAINTEXT, SASL_SSL, etc.)"
    )

    class Config:
        env_prefix = "KAFKA_"

class IcebergConfig(BaseSettings):
    """Iceberg catalog configuration."""
    catalog_uri: str = Field(
        default="http://localhost:8181",
        description="Iceberg REST catalog URI"
    )
    s3_endpoint: str = Field(
        default="http://localhost:9000",
        description="S3/MinIO endpoint"
    )
    s3_access_key: str = Field(
        default="admin",
        description="S3 access key"
    )
    s3_secret_key: str = Field(
        default="password",
        description="S3 secret key"
    )
    warehouse_path: str = Field(
        default="s3://warehouse/",
        description="Iceberg warehouse path"
    )

    class Config:
        env_prefix = "ICEBERG_"

class ObservabilityConfig(BaseSettings):
    """Observability configuration."""
    prometheus_port: int = Field(
        default=9090,
        description="Prometheus metrics port"
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )
    log_format: str = Field(
        default="json",
        description="Log format (json or text)"
    )

    class Config:
        env_prefix = "OBSERVABILITY_"

class K2Config(BaseSettings):
    """Main K2 platform configuration."""
    environment: str = Field(
        default="local",
        description="Environment (local, test, prod)"
    )
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    iceberg: IcebergConfig = Field(default_factory=IcebergConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    class Config:
        env_prefix = "K2_"
        env_file = ".env"
        env_file_encoding = "utf-8"

# Global config instance
config = K2Config()
```

### 5.2 Update Existing Modules to Use Config

**Example**: Update `src/k2/storage/catalog.py`:

```python
from k2.common.config import config

class IcebergCatalogManager:
    def __init__(self):
        """Initialize using global config."""
        self.catalog = load_catalog(
            "k2_catalog",
            **{
                "uri": config.iceberg.catalog_uri,
                "s3.endpoint": config.iceberg.s3_endpoint,
                "s3.access-key-id": config.iceberg.s3_access_key,
                "s3.secret-access-key": config.iceberg.s3_secret_key,
                "s3.path-style-access": "true",
            }
        )
```

### 5.3 Test Configuration

**File**: `tests/unit/test_config.py`

```python
"""Unit tests for configuration."""
import pytest
import os
from k2.common.config import K2Config, KafkaConfig

@pytest.mark.unit
class TestConfiguration:
    """Test configuration loading."""

    def test_default_config(self):
        """Should load with defaults."""
        config = K2Config()

        assert config.environment == "local"
        assert config.kafka.bootstrap_servers == "localhost:9092"
        assert config.iceberg.catalog_uri == "http://localhost:8181"

    def test_env_override(self, monkeypatch):
        """Environment variables should override defaults."""
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")

        config = K2Config()

        assert config.kafka.bootstrap_servers == "kafka:29092"
```

**Validation**:
- Run `pytest tests/unit/test_config.py -v`
- Create `.env.example` file with all configuration options
- Document environment variables in README

**Why**: Pydantic Settings provides type-safe configuration with validation. Environment variable support enables different configurations for local/test/prod without code changes. Centralized config prevents hardcoded connection strings scattered throughout codebase.

---

## Step 6: Ingestion Layer - Kafka Producer

**Goal**: Implement Kafka producer with Avro serialization and Schema Registry integration.

### 6.1 Implement Avro Producer

**File**: `src/k2/ingestion/producer.py`

```python
"""Kafka producer with Avro serialization."""
from typing import Dict, Any, Optional
from confluent_kafka import Producer
from confluent_kafka.serialization import SerializationContext, MessageField
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
import structlog

from k2.common.config import config
from k2.common.metrics import metrics
from k2.schemas import load_avro_schema

logger = structlog.get_logger()

class AvroProducer:
    """Kafka producer with Avro serialization."""

    def __init__(
        self,
        topic: str,
        schema_name: str,
        bootstrap_servers: Optional[str] = None,
        schema_registry_url: Optional[str] = None,
    ):
        """
        Initialize Avro producer.

        Args:
            topic: Kafka topic name
            schema_name: Name of Avro schema (without .avsc extension)
            bootstrap_servers: Kafka brokers (defaults to config)
            schema_registry_url: Schema Registry URL (defaults to config)
        """
        self.topic = topic
        self.schema_name = schema_name

        # Kafka producer config
        producer_config = {
            'bootstrap.servers': bootstrap_servers or config.kafka.bootstrap_servers,
            'client.id': f'k2-producer-{topic}',
            'compression.type': 'lz4',
            'linger.ms': 10,  # Batch for up to 10ms
            'batch.size': 32768,  # 32KB batches
            'acks': 'all',  # Wait for all replicas
            'enable.idempotence': True,  # Exactly-once semantics
        }

        self.producer = Producer(producer_config)

        # Schema Registry client
        schema_registry_client = SchemaRegistryClient({
            'url': schema_registry_url or config.kafka.schema_registry_url
        })

        # Avro serializer
        schema_str = load_avro_schema(schema_name)
        self.serializer = AvroSerializer(
            schema_registry_client,
            schema_str,
        )

        logger.info(
            "Avro producer initialized",
            topic=topic,
            schema=schema_name,
        )

    def produce(
        self,
        key: str,
        value: Dict[str, Any],
        on_delivery: Optional[callable] = None,
    ) -> None:
        """
        Produce message to Kafka.

        Args:
            key: Message key (used for partitioning)
            value: Message value (will be Avro-serialized)
            on_delivery: Optional callback for delivery confirmation
        """
        try:
            # Serialize value
            serialized_value = self.serializer(
                value,
                SerializationContext(self.topic, MessageField.VALUE)
            )

            # Produce to Kafka
            self.producer.produce(
                topic=self.topic,
                key=key.encode('utf-8'),
                value=serialized_value,
                on_delivery=on_delivery or self._default_delivery_callback,
            )

            # Poll to trigger callbacks
            self.producer.poll(0)

            metrics.increment(
                "kafka_messages_produced",
                tags={"topic": self.topic}
            )

        except Exception as e:
            logger.error(
                "Failed to produce message",
                topic=self.topic,
                key=key,
                error=str(e),
            )
            metrics.increment(
                "kafka_produce_errors",
                tags={"topic": self.topic}
            )
            raise

    def flush(self, timeout: float = 10.0) -> int:
        """
        Flush pending messages.

        Args:
            timeout: Max wait time in seconds

        Returns:
            Number of messages still in queue
        """
        remaining = self.producer.flush(timeout)

        if remaining > 0:
            logger.warning(
                "Messages remain in queue after flush",
                remaining=remaining,
            )

        return remaining

    def _default_delivery_callback(self, err, msg):
        """Default delivery callback."""
        if err:
            logger.error(
                "Message delivery failed",
                topic=msg.topic(),
                error=str(err),
            )
            metrics.increment(
                "kafka_delivery_failures",
                tags={"topic": msg.topic()}
            )
        else:
            logger.debug(
                "Message delivered",
                topic=msg.topic(),
                partition=msg.partition(),
                offset=msg.offset(),
            )

    def close(self):
        """Close producer and flush remaining messages."""
        logger.info("Closing producer", topic=self.topic)
        self.flush()
```

### 6.2 Test Producer

**File**: `tests/unit/test_producer.py`

```python
"""Unit tests for Kafka producer."""
import pytest
from unittest.mock import Mock, patch
from k2.ingestion.producer import AvroProducer

@pytest.mark.unit
class TestAvroProducer:
    """Test Avro producer logic."""

    @patch('k2.ingestion.producer.Producer')
    @patch('k2.ingestion.producer.SchemaRegistryClient')
    def test_producer_initialization(self, mock_registry, mock_producer):
        """Producer should initialize with correct config."""
        producer = AvroProducer(
            topic="market.trades.raw",
            schema_name="trade",
        )

        assert producer.topic == "market.trades.raw"
        assert producer.schema_name == "trade"
        mock_producer.assert_called_once()
```

**Integration Test**: `tests/integration/test_producer.py`

```python
"""Integration tests for Kafka producer."""
import pytest
from datetime import datetime
from decimal import Decimal
from k2.ingestion.producer import AvroProducer

@pytest.mark.integration
class TestAvroProducerIntegration:
    """Test producing to real Kafka."""

    def test_produce_trade_message(self):
        """Should produce and serialize trade message."""
        producer = AvroProducer(
            topic="market.trades.raw",
            schema_name="trade",
        )

        trade = {
            "symbol": "BHP",
            "company_id": 7078,
            "exchange": "ASX",
            "exchange_timestamp": int(datetime(2014, 3, 10, 10, 0, 0).timestamp() * 1000),
            "price": "36.50",  # Decimal as string for Avro
            "volume": 10000,
            "qualifiers": 0,
            "venue": "X",
            "buyer_id": None,
            "ingestion_timestamp": int(datetime.now().timestamp() * 1000),
            "sequence_number": 1,
        }

        producer.produce(key="ASX.BHP", value=trade)
        producer.flush()

        # Verify in Kafka UI or with consumer
```

**Validation**:
- Run `pytest tests/integration/test_producer.py -v`
- Check Kafka UI: http://localhost:8080/ui/clusters/k2-market-data/topics/market.trades.raw/messages
- Verify message is Avro-serialized (not plain JSON)

**Why**: Confluent's Avro serializer integrates with Schema Registry, automatically validating messages against registered schemas. `enable.idempotence=True` prevents duplicate messages even with retries. Batching and compression optimize throughput. Testing ensures serialization works correctly before ingesting real data.

---

## Step 7: Ingestion Layer - CSV to Kafka Loader (Batch)

**Goal**: Load historical CSV data into Kafka for initial population and testing.

### 7.1 Implement CSV Batch Loader

**File**: `src/k2/ingestion/batch_loader.py`

```python
"""Batch load CSV data into Kafka."""
import csv
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import Iterator, Dict, Any
import structlog

from k2.ingestion.producer import AvroProducer
from k2.common.metrics import metrics

logger = structlog.get_logger()

class CSVBatchLoader:
    """Load CSV market data files into Kafka."""

    def __init__(self, data_dir: Path):
        """
        Initialize batch loader.

        Args:
            data_dir: Root directory containing sample data
        """
        self.data_dir = Path(data_dir)
        self.company_map = self._load_company_mapping()

    def _load_company_mapping(self) -> Dict[int, str]:
        """Load company_id to symbol mapping."""
        mapping = {}
        ref_data_path = self.data_dir / "reference-data" / "company_info.csv"

        with open(ref_data_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                mapping[int(row['company_id'])] = row['symbol']

        logger.info("Company mapping loaded", count=len(mapping))
        return mapping

    def load_trades(self, company_id: int) -> int:
        """
        Load trades for a company into Kafka.

        Args:
            company_id: Company identifier

        Returns:
            Number of records loaded
        """
        symbol = self.company_map[company_id]
        trades_path = self.data_dir / "trades" / f"{company_id}.csv"

        if not trades_path.exists():
            logger.warning("Trades file not found", company_id=company_id)
            return 0

        producer = AvroProducer(
            topic="market.trades.raw",
            schema_name="trade",
        )

        count = 0
        for trade in self._parse_trades_csv(trades_path, company_id, symbol):
            key = f"{trade['exchange']}.{trade['symbol']}"
            producer.produce(key=key, value=trade)
            count += 1

            if count % 10000 == 0:
                logger.info("Trades loaded", symbol=symbol, count=count)

        producer.flush()

        logger.info(
            "Trades batch load complete",
            symbol=symbol,
            total=count,
        )

        metrics.increment(
            "batch_load_records",
            value=count,
            tags={"type": "trades", "symbol": symbol}
        )

        return count

    def _parse_trades_csv(
        self,
        csv_path: Path,
        company_id: int,
        symbol: str,
    ) -> Iterator[Dict[str, Any]]:
        """Parse trades CSV into Avro-compatible records."""
        with open(csv_path) as f:
            reader = csv.reader(f)

            for row in reader:
                # CSV format: Date, Time, Price, Volume, Qualifiers, Venue, BuyerID
                date_str = row[0]  # MM/DD/YYYY
                time_str = row[1]  # HH:MM:SS.mmm

                # Parse timestamp
                timestamp_str = f"{date_str} {time_str}"
                exchange_timestamp = datetime.strptime(
                    timestamp_str,
                    "%m/%d/%Y %H:%M:%S.%f"
                )

                yield {
                    "symbol": symbol,
                    "company_id": company_id,
                    "exchange": "ASX",
                    "exchange_timestamp": int(exchange_timestamp.timestamp() * 1000),
                    "price": str(Decimal(row[2])),  # Avro decimal as string
                    "volume": int(row[3]),
                    "qualifiers": int(row[4]),
                    "venue": row[5],
                    "buyer_id": row[6] if row[6] else None,
                    "ingestion_timestamp": int(datetime.now().timestamp() * 1000),
                    "sequence_number": None,  # Will be assigned by consumer
                }

    def load_all_trades(self) -> int:
        """Load all trades for all companies."""
        total = 0
        for company_id in self.company_map.keys():
            total += self.load_trades(company_id)

        logger.info("All trades loaded", total=total)
        return total
```

### 7.2 Create CLI Command

**File**: `src/k2/ingestion/cli.py`

```python
"""CLI for ingestion operations."""
import typer
from pathlib import Path
from rich.console import Console
from rich.progress import track

from k2.ingestion.batch_loader import CSVBatchLoader

app = typer.Typer(help="K2 ingestion commands")
console = Console()

@app.command()
def load_batch(
    data_dir: Path = typer.Option(
        Path("data/sample"),
        help="Directory containing CSV files"
    ),
):
    """Load CSV data into Kafka (batch mode)."""
    console.print(f"[bold]Loading data from {data_dir}[/bold]")

    loader = CSVBatchLoader(data_dir)

    with console.status("[bold green]Loading trades..."):
        total = loader.load_all_trades()

    console.print(f"[bold green]✓[/bold green] Loaded {total:,} trades")

def main():
    """Entry point for k2-ingest CLI."""
    app()

if __name__ == '__main__':
    main()
```

### 7.3 Test Batch Loader

**File**: `tests/integration/test_batch_loader.py`

```python
"""Integration tests for batch loader."""
import pytest
from pathlib import Path
from k2.ingestion.batch_loader import CSVBatchLoader

@pytest.mark.integration
class TestBatchLoader:
    """Test CSV batch loading."""

    def test_load_company_mapping(self):
        """Should load company info from CSV."""
        loader = CSVBatchLoader(Path("data/sample"))

        assert 7078 in loader.company_map
        assert loader.company_map[7078] == "BHP"

    def test_load_trades_small_sample(self):
        """Should load trades for low-volume symbol."""
        loader = CSVBatchLoader(Path("data/sample"))

        # DVN has very few trades (good for testing)
        count = loader.load_trades(company_id=7181)

        assert count > 0
        assert count < 500  # DVN is low volume
```

**Validation**:
- Run `k2-ingest load-batch` from command line
- Check Kafka UI for messages in `market.trades.raw` topic
- Verify message count matches CSV row count
- Verify Avro schema is used (messages are binary, not JSON)

**Why**: Batch loading populates Kafka with historical data for testing and initial lake population. Streaming from CSV enables realistic testing without requiring live market feeds. CLI provides user-friendly interface. Testing validates CSV parsing and Kafka delivery.

---

## Step 8: Ingestion Layer - Kafka Consumer with Iceberg Writer

**Goal**: Consume from Kafka and write to Iceberg with sequence tracking.

### 8.1 Implement Consumer

**File**: `src/k2/ingestion/consumer.py`

```python
"""Kafka consumer that writes to Iceberg."""
from typing import Optional, Dict, Any
from datetime import datetime
from confluent_kafka import Consumer, KafkaException
from confluent_kafka.serialization import SerializationContext, MessageField
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
import structlog

from k2.common.config import config
from k2.common.metrics import metrics
from k2.ingestion.sequence_tracker import SequenceTracker
from k2.storage.writer import IcebergWriter
from k2.schemas import load_avro_schema

logger = structlog.get_logger()

class IcebergConsumer:
    """Consume from Kafka and write to Iceberg."""

    def __init__(
        self,
        topic: str,
        schema_name: str,
        consumer_group: str,
        table_name: str,
        batch_size: int = 1000,
    ):
        """
        Initialize consumer.

        Args:
            topic: Kafka topic to consume
            schema_name: Avro schema name
            consumer_group: Consumer group ID
            table_name: Iceberg table to write to
            batch_size: Number of records to batch before writing
        """
        self.topic = topic
        self.schema_name = schema_name
        self.table_name = table_name
        self.batch_size = batch_size

        # Kafka consumer config
        consumer_config = {
            'bootstrap.servers': config.kafka.bootstrap_servers,
            'group.id': consumer_group,
            'auto.offset.reset': 'earliest',
            'enable.auto.commit': False,  # Manual commit after Iceberg write
            'max.poll.interval.ms': 300000,  # 5 minutes for slow writes
        }

        self.consumer = Consumer(consumer_config)
        self.consumer.subscribe([topic])

        # Avro deserializer
        schema_registry_client = SchemaRegistryClient({
            'url': config.kafka.schema_registry_url
        })
        schema_str = load_avro_schema(schema_name)
        self.deserializer = AvroDeserializer(
            schema_registry_client,
            schema_str,
        )

        # Iceberg writer
        self.writer = IcebergWriter()

        # Sequence tracker
        self.sequence_tracker = SequenceTracker(gap_alert_threshold=10)

        logger.info(
            "Iceberg consumer initialized",
            topic=topic,
            consumer_group=consumer_group,
            table=table_name,
        )

    def consume(self, max_messages: Optional[int] = None) -> int:
        """
        Consume messages and write to Iceberg.

        Args:
            max_messages: Optional limit on messages to process

        Returns:
            Number of messages processed
        """
        batch = []
        total_processed = 0

        try:
            while max_messages is None or total_processed < max_messages:
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    # No message available, flush batch if exists
                    if batch:
                        self._write_batch(batch)
                        batch = []
                    continue

                if msg.error():
                    logger.error("Consumer error", error=msg.error())
                    continue

                # Deserialize message
                record = self.deserializer(
                    msg.value(),
                    SerializationContext(self.topic, MessageField.VALUE)
                )

                # Track sequence (if available)
                if 'sequence_number' in record and record['sequence_number']:
                    event = self.sequence_tracker.check_sequence(
                        exchange=record['exchange'],
                        symbol=record['symbol'],
                        sequence=record['sequence_number'],
                        timestamp=datetime.fromtimestamp(record['exchange_timestamp'] / 1000),
                    )

                    # Handle sequence events (already logged by tracker)
                    pass

                # Add to batch
                batch.append(record)
                total_processed += 1

                # Write batch when full
                if len(batch) >= self.batch_size:
                    self._write_batch(batch)
                    self.consumer.commit()  # Commit after successful write
                    batch = []

                metrics.increment(
                    "kafka_messages_consumed",
                    tags={"topic": self.topic}
                )

        except KeyboardInterrupt:
            logger.info("Consumer interrupted")

        finally:
            # Write remaining records
            if batch:
                self._write_batch(batch)
                self.consumer.commit()

            self.consumer.close()

        logger.info(
            "Consumer finished",
            total_processed=total_processed,
        )

        return total_processed

    def _write_batch(self, batch: list) -> None:
        """Write batch to Iceberg."""
        if not batch:
            return

        try:
            self.writer.write_trades(batch, self.table_name)

            logger.info(
                "Batch written to Iceberg",
                records=len(batch),
                table=self.table_name,
            )

        except Exception as e:
            logger.error(
                "Failed to write batch",
                error=str(e),
            )
            # TODO: Implement retry or dead-letter queue
            raise
```

### 8.2 Create CLI Command

**File**: Update `src/k2/ingestion/cli.py`:

```python
@app.command()
def consume(
    topic: str = typer.Option("market.trades.raw", help="Kafka topic"),
    table: str = typer.Option("market_data.trades", help="Iceberg table"),
    max_messages: Optional[int] = typer.Option(None, help="Max messages to process"),
):
    """Consume from Kafka and write to Iceberg."""
    from k2.ingestion.consumer import IcebergConsumer

    console.print(f"[bold]Consuming from {topic} → {table}[/bold]")

    consumer = IcebergConsumer(
        topic=topic,
        schema_name="trade",
        consumer_group="k2-iceberg-writer",
        table_name=table,
    )

    processed = consumer.consume(max_messages=max_messages)

    console.print(f"[bold green]✓[/bold green] Processed {processed:,} messages")
```

### 8.3 Test Consumer

**File**: `tests/integration/test_consumer.py`

```python
"""Integration tests for Kafka consumer."""
import pytest
from k2.ingestion.consumer import IcebergConsumer
from k2.ingestion.producer import AvroProducer
from datetime import datetime

@pytest.mark.integration
class TestIcebergConsumer:
    """Test consuming and writing to Iceberg."""

    def test_consume_and_write(self):
        """Should consume messages and write to Iceberg."""
        # Produce test message
        producer = AvroProducer(
            topic="market.trades.raw",
            schema_name="trade",
        )

        trade = {
            "symbol": "TEST",
            "company_id": 9999,
            "exchange": "ASX",
            "exchange_timestamp": int(datetime.now().timestamp() * 1000),
            "price": "100.00",
            "volume": 1000,
            "qualifiers": 0,
            "venue": "X",
            "buyer_id": None,
            "ingestion_timestamp": int(datetime.now().timestamp() * 1000),
            "sequence_number": 1,
        }

        producer.produce(key="ASX.TEST", value=trade)
        producer.flush()

        # Consume and verify
        consumer = IcebergConsumer(
            topic="market.trades.raw",
            schema_name="trade",
            consumer_group="test-consumer",
            table_name="market_data.trades",
            batch_size=10,
        )

        processed = consumer.consume(max_messages=1)

        assert processed >= 1
```

**Validation**:
- Run `k2-ingest load-batch` to populate Kafka
- Run `k2-ingest consume --max-messages 1000`
- Query Iceberg table: verify records exist
- Check consumer lag in Kafka UI: should be near zero after consumption
- Verify metrics in Prometheus: `kafka_messages_consumed`, `iceberg_records_written`

**Why**: Consumer bridges streaming (Kafka) and lakehouse (Iceberg). Manual commit after Iceberg write ensures at-least-once delivery (no data loss if write fails). Batching optimizes Iceberg write performance. Sequence tracking detects data quality issues. Testing validates end-to-end flow.

---

## Step 9: Query Layer - DuckDB Integration

**Goal**: Query Iceberg tables using DuckDB for analytical queries.

### 9.1 Implement Query Engine

**File**: `src/k2/query/engine.py`

```python
"""DuckDB query engine for Iceberg tables."""
from typing import Optional, Any
import duckdb
import pandas as pd
from pathlib import Path
import structlog

from k2.common.config import config
from k2.common.metrics import metrics

logger = structlog.get_logger()

class QueryEngine:
    """DuckDB-based query engine for Iceberg lakehouse."""

    def __init__(self, catalog_uri: Optional[str] = None):
        """
        Initialize query engine.

        Args:
            catalog_uri: Iceberg REST catalog URI
        """
        self.catalog_uri = catalog_uri or config.iceberg.catalog_uri

        # Initialize DuckDB connection
        self.conn = duckdb.connect()

        # Install and load Iceberg extension
        self.conn.execute("INSTALL iceberg;")
        self.conn.execute("LOAD iceberg;")

        # Configure S3/MinIO access
        self.conn.execute(f"""
            CREATE SECRET minio_secret (
                TYPE S3,
                KEY_ID '{config.iceberg.s3_access_key}',
                SECRET '{config.iceberg.s3_secret_key}',
                ENDPOINT '{config.iceberg.s3_endpoint.replace('http://', '')}',
                USE_SSL false,
                URL_STYLE 'path'
            );
        """)

        logger.info("DuckDB query engine initialized")

    def query(
        self,
        sql: str,
        return_df: bool = True,
    ) -> Any:
        """
        Execute SQL query against Iceberg tables.

        Args:
            sql: SQL query string
            return_df: Return pandas DataFrame (else DuckDB relation)

        Returns:
            Query results as DataFrame or relation
        """
        start_time = pd.Timestamp.now()

        try:
            result = self.conn.execute(sql)

            if return_df:
                df = result.df()

                duration_ms = (pd.Timestamp.now() - start_time).total_seconds() * 1000

                logger.info(
                    "Query executed",
                    duration_ms=duration_ms,
                    rows=len(df),
                )

                metrics.histogram(
                    "query_duration_ms",
                    duration_ms,
                    tags={"engine": "duckdb"}
                )

                return df
            else:
                return result

        except Exception as e:
            logger.error("Query failed", error=str(e), sql=sql)
            metrics.increment("query_errors", tags={"engine": "duckdb"})
            raise

    def query_trades(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """
        Query trades with common filters.

        Args:
            symbol: Filter by symbol (e.g., 'BHP')
            start_time: Start timestamp (ISO format)
            end_time: End timestamp (ISO format)
            limit: Max rows to return

        Returns:
            DataFrame of trade records
        """
        # Build SQL query
        sql = f"""
            SELECT *
            FROM iceberg_scan('{config.iceberg.warehouse_path}market_data.db/trades')
            WHERE 1=1
        """

        if symbol:
            sql += f" AND symbol = '{symbol}'"

        if start_time:
            sql += f" AND exchange_timestamp >= TIMESTAMP '{start_time}'"

        if end_time:
            sql += f" AND exchange_timestamp <= TIMESTAMP '{end_time}'"

        sql += f" ORDER BY exchange_timestamp DESC LIMIT {limit}"

        return self.query(sql)

    def get_market_summary(self, symbol: str, date: str) -> pd.DataFrame:
        """
        Get daily market summary statistics.

        Args:
            symbol: Trading symbol
            date: Date in YYYY-MM-DD format

        Returns:
            DataFrame with OHLCV and trade count
        """
        sql = f"""
            SELECT
                symbol,
                DATE_TRUNC('day', exchange_timestamp) as trading_day,
                COUNT(*) as trade_count,
                MIN(price) as low,
                MAX(price) as high,
                FIRST(price ORDER BY exchange_timestamp) as open,
                LAST(price ORDER BY exchange_timestamp) as close,
                SUM(volume) as total_volume
            FROM iceberg_scan('{config.iceberg.warehouse_path}market_data.db/trades')
            WHERE symbol = '{symbol}'
              AND DATE_TRUNC('day', exchange_timestamp) = DATE '{date}'
            GROUP BY symbol, trading_day
        """

        return self.query(sql)

    def close(self):
        """Close DuckDB connection."""
        self.conn.close()
        logger.info("Query engine closed")
```

### 9.2 Test Query Engine

**File**: `tests/integration/test_query_engine.py`

```python
"""Integration tests for query engine."""
import pytest
from k2.query.engine import QueryEngine

@pytest.mark.integration
class TestQueryEngine:
    """Test DuckDB query engine."""

    def test_query_engine_initialization(self):
        """Engine should initialize with Iceberg extension."""
        engine = QueryEngine()

        # Test basic query
        result = engine.query("SELECT 1 as test")

        assert len(result) == 1
        assert result['test'][0] == 1

    def test_query_trades_table(self):
        """Should query trades from Iceberg table."""
        engine = QueryEngine()

        # Query all trades (assuming some exist from ingestion tests)
        df = engine.query_trades(limit=10)

        # Verify schema
        assert 'symbol' in df.columns
        assert 'price' in df.columns
        assert 'exchange_timestamp' in df.columns

    def test_query_with_symbol_filter(self):
        """Should filter by symbol."""
        engine = QueryEngine()

        df = engine.query_trades(symbol='BHP', limit=100)

        if len(df) > 0:
            assert all(df['symbol'] == 'BHP')

    def test_market_summary(self):
        """Should compute daily summary statistics."""
        engine = QueryEngine()

        summary = engine.get_market_summary(
            symbol='BHP',
            date='2014-03-10'
        )

        if len(summary) > 0:
            assert 'open' in summary.columns
            assert 'high' in summary.columns
            assert 'low' in summary.columns
            assert 'close' in summary.columns
```

**Validation**:
- Ensure Iceberg tables have data (run ingestion first)
- Run `pytest tests/integration/test_query_engine.py -v`
- Test CLI query: `python -c "from k2.query.engine import QueryEngine; e = QueryEngine(); print(e.query_trades(limit=5))"`
- Verify query performance: < 1 second for simple queries

**Why**: DuckDB provides fast analytical queries without cluster overhead. Iceberg extension enables direct Parquet scanning with predicate pushdown. Testing validates S3/MinIO connectivity and query correctness.

---

## Step 10: Query Layer - Replay Engine

**Goal**: Support time-travel queries and replay historical data.

### 10.1 Implement Replay Engine

**File**: `src/k2/query/replay.py`

```python
"""Replay engine for time-travel queries."""
from typing import Optional, Iterator, Dict, Any
from datetime import datetime
import pandas as pd
from pyiceberg.catalog import load_catalog
from pyiceberg.table import Table
import structlog

from k2.common.config import config
from k2.common.metrics import metrics

logger = structlog.get_logger()

class ReplayEngine:
    """
    Replay historical market data with time-travel semantics.

    Supports three replay modes:
    1. Cold Start: Replay from beginning of time range
    2. Catch-Up: Replay from specific snapshot to current
    3. Rewind: Query as-of specific timestamp (time-travel)
    """

    def __init__(self):
        """Initialize replay engine."""
        self.catalog = load_catalog(
            "k2_catalog",
            **{
                "uri": config.iceberg.catalog_uri,
                "s3.endpoint": config.iceberg.s3_endpoint,
                "s3.access-key-id": config.iceberg.s3_access_key,
                "s3.secret-access-key": config.iceberg.s3_secret_key,
                "s3.path-style-access": "true",
            }
        )

        logger.info("Replay engine initialized")

    def cold_start_replay(
        self,
        table_name: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        batch_size: int = 1000,
    ) -> Iterator[pd.DataFrame]:
        """
        Replay from beginning of time range (cold start).

        Args:
            table_name: Iceberg table identifier
            symbol: Trading symbol to replay
            start_time: Start timestamp
            end_time: End timestamp
            batch_size: Records per batch

        Yields:
            Batches of records in temporal order
        """
        logger.info(
            "Starting cold start replay",
            table=table_name,
            symbol=symbol,
            start=start_time,
            end=end_time,
        )

        table = self.catalog.load_table(table_name)

        # Scan with filters for efficiency
        scan = table.scan(
            row_filter=f"symbol = '{symbol}' AND exchange_timestamp >= {int(start_time.timestamp() * 1000)} AND exchange_timestamp <= {int(end_time.timestamp() * 1000)}"
        )

        # Stream results in batches
        df = scan.to_pandas()

        # Sort by exchange timestamp (guaranteed ordering)
        df = df.sort_values('exchange_timestamp')

        total_records = len(df)
        logger.info("Replay data loaded", records=total_records)

        # Yield in batches
        for i in range(0, len(df), batch_size):
            batch = df.iloc[i:i + batch_size]
            yield batch

        metrics.increment(
            "replay_records_processed",
            value=total_records,
            tags={"mode": "cold_start", "symbol": symbol}
        )

    def rewind_query(
        self,
        table_name: str,
        as_of_timestamp: datetime,
    ) -> pd.DataFrame:
        """
        Query table as it existed at specific timestamp (time-travel).

        Args:
            table_name: Iceberg table identifier
            as_of_timestamp: Query as-of this timestamp

        Returns:
            DataFrame with data as of timestamp
        """
        logger.info(
            "Time-travel query",
            table=table_name,
            as_of=as_of_timestamp,
        )

        table = self.catalog.load_table(table_name)

        # Find snapshot at or before timestamp
        timestamp_ms = int(as_of_timestamp.timestamp() * 1000)

        # Get snapshot history
        snapshots = table.history()

        # Find closest snapshot before timestamp
        snapshot_id = None
        for snapshot in reversed(snapshots):
            if snapshot.timestamp_ms <= timestamp_ms:
                snapshot_id = snapshot.snapshot_id
                break

        if snapshot_id is None:
            logger.warning("No snapshot found before timestamp")
            return pd.DataFrame()

        logger.info(
            "Using snapshot",
            snapshot_id=snapshot_id,
            snapshot_time=datetime.fromtimestamp(snapshot.timestamp_ms / 1000),
        )

        # Query at snapshot
        scan = table.scan(snapshot_id=snapshot_id)
        df = scan.to_pandas()

        return df

    def list_snapshots(self, table_name: str) -> pd.DataFrame:
        """
        List available snapshots for time-travel.

        Args:
            table_name: Iceberg table identifier

        Returns:
            DataFrame with snapshot metadata
        """
        table = self.catalog.load_table(table_name)
        snapshots = table.history()

        data = []
        for snapshot in snapshots:
            data.append({
                'snapshot_id': snapshot.snapshot_id,
                'timestamp': datetime.fromtimestamp(snapshot.timestamp_ms / 1000),
                'operation': snapshot.summary.get('operation', 'unknown'),
            })

        return pd.DataFrame(data)
```

### 10.2 Test Replay Engine

**File**: `tests/integration/test_replay_engine.py`

```python
"""Integration tests for replay engine."""
import pytest
from datetime import datetime
from k2.query.replay import ReplayEngine

@pytest.mark.integration
class TestReplayEngine:
    """Test replay engine."""

    def test_cold_start_replay(self):
        """Should replay data in temporal order."""
        engine = ReplayEngine()

        start = datetime(2014, 3, 10, 10, 0, 0)
        end = datetime(2014, 3, 10, 12, 0, 0)

        batches = list(engine.cold_start_replay(
            table_name="market_data.trades",
            symbol="BHP",
            start_time=start,
            end_time=end,
            batch_size=100,
        ))

        # Verify ordering
        if batches:
            for batch in batches:
                timestamps = batch['exchange_timestamp'].tolist()
                assert timestamps == sorted(timestamps)

    def test_list_snapshots(self):
        """Should list Iceberg snapshots."""
        engine = ReplayEngine()

        snapshots = engine.list_snapshots("market_data.trades")

        assert len(snapshots) > 0
        assert 'snapshot_id' in snapshots.columns
        assert 'timestamp' in snapshots.columns
```

**Validation**:
- Run `pytest tests/integration/test_replay_engine.py -v`
- Test replay in notebook: iterate batches and verify ordering
- Check snapshot history: should show append operations

**Why**: Replay is critical for backtesting trading strategies. Iceberg snapshots enable time-travel queries for compliance and debugging. Cold start mode simulates historical replay. Testing ensures ordering guarantees are maintained.

---

## Step 11: Query Layer - CLI

**Goal**: Provide user-friendly CLI for querying data.

### 11.1 Implement Query CLI

**File**: `src/k2/query/cli.py`

```python
"""CLI for query operations."""
import typer
from typing import Optional
from rich.console import Console
from rich.table import Table as RichTable
from datetime import datetime

from k2.query.engine import QueryEngine
from k2.query.replay import ReplayEngine

app = typer.Typer(help="K2 query commands")
console = Console()

@app.command()
def trades(
    symbol: Optional[str] = typer.Option(None, help="Filter by symbol"),
    start: Optional[str] = typer.Option(None, help="Start time (ISO format)"),
    end: Optional[str] = typer.Option(None, help="End time (ISO format)"),
    limit: int = typer.Option(100, help="Max rows"),
):
    """Query trades from Iceberg lakehouse."""
    engine = QueryEngine()

    df = engine.query_trades(
        symbol=symbol,
        start_time=start,
        end_time=end,
        limit=limit,
    )

    if df.empty:
        console.print("[yellow]No results found[/yellow]")
        return

    # Display as rich table
    table = RichTable(title=f"Trades ({len(df)} rows)")

    for col in df.columns[:8]:  # Show first 8 columns
        table.add_column(col, style="cyan")

    for _, row in df.head(20).iterrows():  # Show first 20 rows
        table.add_row(*[str(row[col]) for col in df.columns[:8]])

    console.print(table)

    if len(df) > 20:
        console.print(f"[dim]... and {len(df) - 20} more rows[/dim]")

@app.command()
def summary(
    symbol: str = typer.Argument(..., help="Trading symbol"),
    date: str = typer.Argument(..., help="Date (YYYY-MM-DD)"),
):
    """Get daily market summary (OHLCV)."""
    engine = QueryEngine()

    summary = engine.get_market_summary(symbol, date)

    if summary.empty:
        console.print("[yellow]No data found[/yellow]")
        return

    row = summary.iloc[0]

    console.print(f"\n[bold]{symbol} - {date}[/bold]\n")
    console.print(f"Open:   {row['open']:.2f}")
    console.print(f"High:   {row['high']:.2f}")
    console.print(f"Low:    {row['low']:.2f}")
    console.print(f"Close:  {row['close']:.2f}")
    console.print(f"Volume: {row['total_volume']:,}")
    console.print(f"Trades: {row['trade_count']:,}")

@app.command()
def snapshots(
    table: str = typer.Option("market_data.trades", help="Table name"),
):
    """List Iceberg snapshots for time-travel."""
    engine = ReplayEngine()

    snapshots = engine.list_snapshots(table)

    table_display = RichTable(title=f"Snapshots - {table}")
    table_display.add_column("Snapshot ID")
    table_display.add_column("Timestamp")
    table_display.add_column("Operation")

    for _, row in snapshots.iterrows():
        table_display.add_row(
            str(row['snapshot_id']),
            str(row['timestamp']),
            row['operation'],
        )

    console.print(table_display)

def main():
    """Entry point for k2-query CLI."""
    app()

if __name__ == '__main__':
    main()
```

### 11.2 Test CLI Commands

**Manual Testing**:
```bash
# Query recent trades
k2-query trades --symbol BHP --limit 10

# Get daily summary
k2-query summary BHP 2014-03-10

# List snapshots
k2-query snapshots
```

**Validation**:
- All commands run without errors
- Output is formatted nicely with Rich tables
- Filters work correctly
- Performance is acceptable (< 5 seconds for simple queries)

**Why**: CLI provides quick access to data without writing code. Rich formatting makes output readable. Testing validates user experience.

---

## Step 12: API Layer - REST API with FastAPI

**Goal**: Expose query functionality via REST API.

### 12.1 Implement FastAPI Server

**File**: `src/k2/api/main.py`

```python
"""FastAPI REST API for K2 platform."""
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import structlog

from k2.query.engine import QueryEngine
from k2.query.replay import ReplayEngine
from k2.common.metrics import metrics

logger = structlog.get_logger()

# Models
class TradeQuery(BaseModel):
    """Trade query parameters."""
    symbol: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    limit: int = 1000

class MarketSummary(BaseModel):
    """Daily market summary response."""
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_count: int

# App initialization
app = FastAPI(
    title="K2 Market Data Platform API",
    description="REST API for querying market data",
    version="0.1.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize engines
query_engine = QueryEngine()
replay_engine = ReplayEngine()

@app.get("/")
def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "k2-api"}

@app.get("/trades")
def get_trades(
    symbol: Optional[str] = Query(None, description="Trading symbol"),
    start_time: Optional[str] = Query(None, description="Start timestamp (ISO)"),
    end_time: Optional[str] = Query(None, description="End timestamp (ISO)"),
    limit: int = Query(1000, description="Max records to return"),
):
    """
    Query trades from Iceberg lakehouse.

    Returns:
        List of trade records
    """
    try:
        df = query_engine.query_trades(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

        metrics.increment("api_requests", tags={"endpoint": "/trades"})

        return {
            "count": len(df),
            "trades": df.to_dict(orient="records"),
        }

    except Exception as e:
        logger.error("Query failed", endpoint="/trades", error=str(e))
        metrics.increment("api_errors", tags={"endpoint": "/trades"})
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/summary/{symbol}/{date}", response_model=MarketSummary)
def get_summary(
    symbol: str,
    date: str,
):
    """
    Get daily market summary (OHLCV).

    Args:
        symbol: Trading symbol (e.g., BHP)
        date: Date in YYYY-MM-DD format

    Returns:
        Daily market summary
    """
    try:
        df = query_engine.get_market_summary(symbol, date)

        if df.empty:
            raise HTTPException(status_code=404, detail="No data found")

        row = df.iloc[0]

        return MarketSummary(
            symbol=symbol,
            date=date,
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            volume=int(row['total_volume']),
            trade_count=int(row['trade_count']),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Query failed", endpoint="/summary", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/snapshots")
def list_snapshots(
    table: str = Query("market_data.trades", description="Table name"),
):
    """
    List Iceberg snapshots for time-travel queries.

    Returns:
        List of snapshot metadata
    """
    try:
        df = replay_engine.list_snapshots(table)

        return {
            "count": len(df),
            "snapshots": df.to_dict(orient="records"),
        }

    except Exception as e:
        logger.error("Query failed", endpoint="/snapshots", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 12.2 Test API

**File**: `tests/integration/test_api.py`

```python
"""Integration tests for REST API."""
import pytest
from fastapi.testclient import TestClient
from k2.api.main import app

@pytest.mark.integration
class TestAPI:
    """Test REST API endpoints."""

    def setup_method(self):
        """Setup test client."""
        self.client = TestClient(app)

    def test_health_check(self):
        """Root endpoint should return health status."""
        response = self.client.get("/")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_get_trades(self):
        """Should return trades from Iceberg."""
        response = self.client.get("/trades?symbol=BHP&limit=10")

        assert response.status_code == 200
        data = response.json()
        assert "trades" in data
        assert "count" in data

    def test_get_summary(self):
        """Should return daily summary."""
        response = self.client.get("/summary/BHP/2014-03-10")

        # May be 404 if no data, or 200 with data
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert "open" in data
            assert "high" in data

    def test_list_snapshots(self):
        """Should list Iceberg snapshots."""
        response = self.client.get("/snapshots")

        assert response.status_code == 200
        data = response.json()
        assert "snapshots" in data
```

### 12.3 Run API Server

**Update Makefile**:
```makefile
.PHONY: api
api:
	uvicorn k2.api.main:app --reload --host 0.0.0.0 --port 8000
```

**Validation**:
- Run `make api` or `uvicorn k2.api.main:app --reload`
- Visit http://localhost:8000/docs (Swagger UI)
- Test endpoints interactively
- Run `pytest tests/integration/test_api.py -v`

**Why**: REST API enables integration with other systems (dashboards, notebooks, external apps). FastAPI provides automatic OpenAPI documentation. Pydantic models ensure type safety. Testing validates endpoints work correctly.

---

## Step 13: API Layer - Prometheus Metrics Endpoint

**Goal**: Expose Prometheus metrics from API server.

### 13.1 Implement Metrics Endpoint

**File**: Update `src/k2/common/metrics.py`:

```python
"""Metrics collection with Prometheus."""
from typing import Dict, Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY
import structlog

logger = structlog.get_logger()

class MetricsClient:
    """Prometheus metrics client."""

    def __init__(self):
        """Initialize metrics."""
        # Counters
        self.counters: Dict[str, Counter] = {}

        # Histograms
        self.histograms: Dict[str, Histogram] = {}

        # Gauges
        self.gauges: Dict[str, Gauge] = {}

        # Pre-register common metrics
        self._register_common_metrics()

    def _register_common_metrics(self):
        """Register commonly used metrics."""
        self.counters['kafka_messages_produced'] = Counter(
            'kafka_messages_produced_total',
            'Total Kafka messages produced',
            ['topic']
        )

        self.counters['kafka_messages_consumed'] = Counter(
            'kafka_messages_consumed_total',
            'Total Kafka messages consumed',
            ['topic']
        )

        self.histograms['query_duration_ms'] = Histogram(
            'query_duration_milliseconds',
            'Query execution duration',
            ['engine'],
            buckets=[10, 50, 100, 500, 1000, 5000, 10000]
        )

        self.counters['api_requests'] = Counter(
            'api_requests_total',
            'Total API requests',
            ['endpoint']
        )

    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ):
        """Increment counter metric."""
        if metric_name not in self.counters:
            # Auto-create counter
            self.counters[metric_name] = Counter(
                f'{metric_name}_total',
                f'Auto-created counter: {metric_name}',
                list(tags.keys()) if tags else []
            )

        if tags:
            self.counters[metric_name].labels(**tags).inc(value)
        else:
            self.counters[metric_name].inc(value)

    def histogram(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ):
        """Record histogram observation."""
        if metric_name not in self.histograms:
            # Auto-create histogram
            self.histograms[metric_name] = Histogram(
                metric_name,
                f'Auto-created histogram: {metric_name}',
                list(tags.keys()) if tags else []
            )

        if tags:
            self.histograms[metric_name].labels(**tags).observe(value)
        else:
            self.histograms[metric_name].observe(value)

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ):
        """Set gauge value."""
        if metric_name not in self.gauges:
            # Auto-create gauge
            self.gauges[metric_name] = Gauge(
                metric_name,
                f'Auto-created gauge: {metric_name}',
                list(tags.keys()) if tags else []
            )

        if tags:
            self.gauges[metric_name].labels(**tags).set(value)
        else:
            self.gauges[metric_name].set(value)

    def generate_metrics(self) -> bytes:
        """Generate Prometheus metrics output."""
        return generate_latest(REGISTRY)

# Global metrics instance
metrics = MetricsClient()
```

### 13.2 Add Metrics Endpoint to API

**File**: Update `src/k2/api/main.py`:

```python
from fastapi import Response
from k2.common.metrics import metrics as metrics_client

@app.get("/metrics")
def prometheus_metrics():
    """Prometheus metrics endpoint."""
    return Response(
        content=metrics_client.generate_metrics(),
        media_type="text/plain",
    )
```

### 13.3 Test Metrics

**Manual Testing**:
```bash
# Start API
make api

# Generate some traffic
curl http://localhost:8000/trades?limit=10

# Check metrics
curl http://localhost:8000/metrics
```

**Validation**:
- Metrics endpoint returns Prometheus format
- Counters increment on API requests
- Histogram records query durations
- Prometheus can scrape metrics (add to prometheus.yml if needed)

**Why**: Prometheus metrics enable production observability. Standardized format integrates with existing monitoring. Auto-registration simplifies adding new metrics. Testing ensures metrics are collected correctly.

---

## Step 14: Observability - Grafana Dashboard

**Goal**: Create basic Grafana dashboard for system monitoring.

### 14.1 Create Dashboard JSON

**File**: `config/grafana/dashboards/k2-platform.json`

```json
{
  "dashboard": {
    "title": "K2 Platform Overview",
    "panels": [
      {
        "title": "API Request Rate",
        "targets": [
          {
            "expr": "rate(api_requests_total[5m])",
            "legendFormat": "{{endpoint}}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Query Duration p99",
        "targets": [
          {
            "expr": "histogram_quantile(0.99, rate(query_duration_milliseconds_bucket[5m]))",
            "legendFormat": "{{engine}}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Kafka Messages Produced",
        "targets": [
          {
            "expr": "rate(kafka_messages_produced_total[5m])",
            "legendFormat": "{{topic}}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Kafka Messages Consumed",
        "targets": [
          {
            "expr": "rate(kafka_messages_consumed_total[5m])",
            "legendFormat": "{{topic}}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Iceberg Write Duration",
        "targets": [
          {
            "expr": "rate(iceberg_write_duration_ms[5m])",
            "legendFormat": "{{table}}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Error Rate",
        "targets": [
          {
            "expr": "rate(api_errors_total[5m])",
            "legendFormat": "{{endpoint}}"
          }
        ],
        "type": "graph"
      }
    ]
  }
}
```

### 14.2 Update Grafana Provisioning

**File**: Update `config/grafana/dashboards/dashboard.yml`:

```yaml
apiVersion: 1

providers:
  - name: 'K2 Platform'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
```

### 14.3 Test Dashboard

**Manual Testing**:
1. Visit http://localhost:3000 (admin/admin)
2. Navigate to Dashboards → K2 Platform Overview
3. Generate traffic: run ingestion, queries, API requests
4. Verify panels update with data

**Validation**:
- Dashboard loads without errors
- Panels show metrics when data is available
- Queries execute quickly (< 1 second)
- Time ranges adjust correctly

**Why**: Dashboards provide real-time visibility into system health. Pre-configured panels reduce setup time. Testing ensures dashboards work out-of-the-box.

---

## Step 15: End-to-End Testing & Demo

**Goal**: Validate complete data flow from CSV → Kafka → Iceberg → Query API.

### 15.1 Create E2E Test

**File**: `tests/integration/test_e2e_flow.py`

```python
"""End-to-end integration test."""
import pytest
from pathlib import Path
from datetime import datetime
import time

from k2.ingestion.batch_loader import CSVBatchLoader
from k2.ingestion.consumer import IcebergConsumer
from k2.query.engine import QueryEngine
from k2.api.main import app
from fastapi.testclient import TestClient

@pytest.mark.integration
class TestEndToEnd:
    """End-to-end data flow validation."""

    def test_complete_data_flow(self):
        """Test CSV → Kafka → Iceberg → Query → API."""

        # Step 1: Load CSV to Kafka
        loader = CSVBatchLoader(Path("data/sample"))
        loaded = loader.load_trades(company_id=7181)  # DVN (small dataset)

        assert loaded > 0
        print(f"✓ Loaded {loaded} trades to Kafka")

        # Step 2: Consume from Kafka to Iceberg
        consumer = IcebergConsumer(
            topic="market.trades.raw",
            schema_name="trade",
            consumer_group="e2e-test-consumer",
            table_name="market_data.trades",
            batch_size=100,
        )

        # Process all messages
        processed = consumer.consume(max_messages=loaded + 100)

        assert processed >= loaded
        print(f"✓ Consumed {processed} messages to Iceberg")

        # Wait for write to complete
        time.sleep(2)

        # Step 3: Query via QueryEngine
        engine = QueryEngine()

        df = engine.query_trades(symbol="DVN", limit=1000)

        assert len(df) > 0
        assert "DVN" in df["symbol"].values
        print(f"✓ Queried {len(df)} trades from Iceberg")

        # Step 4: Query via API
        client = TestClient(app)

        response = client.get("/trades?symbol=DVN&limit=100")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] > 0
        print(f"✓ API returned {data['count']} trades")

        # Step 5: Verify data correctness
        api_trades = data["trades"]
        first_trade = api_trades[0]

        assert first_trade["symbol"] == "DVN"
        assert "price" in first_trade
        assert "volume" in first_trade
        print("✓ Data schema validated")

        print("\n✅ End-to-end test PASSED")
```

### 15.2 Create Demo Script

**File**: `scripts/demo.py`

```python
#!/usr/bin/env python3
"""Interactive demo of K2 platform capabilities."""
import time
from pathlib import Path
from rich.console import Console
from rich.progress import Progress

from k2.ingestion.batch_loader import CSVBatchLoader
from k2.ingestion.consumer import IcebergConsumer
from k2.query.engine import QueryEngine

console = Console()

def main():
    """Run interactive demo."""
    console.print("[bold blue]K2 Market Data Platform - Demo[/bold blue]\n")

    # Step 1: Load data
    console.print("[bold]Step 1: Loading CSV data to Kafka...[/bold]")

    loader = CSVBatchLoader(Path("data/sample"))

    with Progress() as progress:
        task = progress.add_task("[cyan]Loading trades...", total=4)

        for company_id in [7078, 7458, 7181, 3153]:  # BHP, RIO, DVN, MWR
            count = loader.load_trades(company_id)
            console.print(f"  ✓ Loaded {count:,} trades for company {company_id}")
            progress.update(task, advance=1)

    console.print()

    # Step 2: Consume to Iceberg
    console.print("[bold]Step 2: Consuming to Iceberg lakehouse...[/bold]")

    consumer = IcebergConsumer(
        topic="market.trades.raw",
        schema_name="trade",
        consumer_group="demo-consumer",
        table_name="market_data.trades",
    )

    processed = consumer.consume(max_messages=10000)
    console.print(f"  ✓ Processed {processed:,} messages to Iceberg\n")

    # Step 3: Query data
    console.print("[bold]Step 3: Querying data with DuckDB...[/bold]")

    engine = QueryEngine()

    # Query BHP trades
    df = engine.query_trades(symbol="BHP", limit=10)
    console.print(f"  ✓ Found {len(df)} BHP trades")
    console.print(f"    Price range: ${df['price'].min():.2f} - ${df['price'].max():.2f}\n")

    # Daily summary
    summary = engine.get_market_summary("BHP", "2014-03-10")
    if not summary.empty:
        row = summary.iloc[0]
        console.print("  [bold]BHP Daily Summary (2014-03-10):[/bold]")
        console.print(f"    Open:  ${row['open']:.2f}")
        console.print(f"    High:  ${row['high']:.2f}")
        console.print(f"    Low:   ${row['low']:.2f}")
        console.print(f"    Close: ${row['close']:.2f}")
        console.print(f"    Volume: {row['total_volume']:,} shares")
        console.print(f"    Trades: {row['trade_count']:,}\n")

    console.print("[bold green]✅ Demo complete![/bold green]")
    console.print("\nNext steps:")
    console.print("  • Start API: [cyan]make api[/cyan]")
    console.print("  • View dashboards: [cyan]http://localhost:3000[/cyan]")
    console.print("  • Query CLI: [cyan]k2-query trades --symbol BHP[/cyan]")

if __name__ == '__main__':
    main()
```

### 15.3 Run E2E Test

**Commands**:
```bash
# Ensure infrastructure is running
make docker-up
make init-infra

# Run E2E test
pytest tests/integration/test_e2e_flow.py -v -s

# Run demo
python scripts/demo.py
```

**Validation**:
- E2E test passes without errors
- Demo script completes successfully
- Data flows through all layers
- API returns correct results
- Dashboards show activity

**Why**: E2E testing validates the complete system works as designed. Demo script provides reproducible demonstration for portfolio reviewers. Testing catches integration issues between components.

---

## Step 16: Documentation & Cleanup

**Goal**: Ensure project is well-documented and ready for portfolio review.

### 16.1 Update README with Quick Start

**File**: Update `README.md` section "Quick Start":

```markdown
## Quick Start

### Prerequisites
- Docker Desktop (8GB RAM minimum)
- Python 3.11+
- Make (optional)

### 1. Start Infrastructure

\`\`\`bash
# Clone repository
git clone <repository-url>
cd k2-market-data-platform

# Start all services
make docker-up

# Wait ~30 seconds for services to be ready
make docker-ps
\`\`\`

### 2. Initialize Platform

\`\`\`bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev,api]"

# Initialize infrastructure
make init-infra
\`\`\`

### 3. Run Demo

\`\`\`bash
# Interactive demo (loads data and queries)
python scripts/demo.py

# Or run step-by-step:
k2-ingest load-batch                    # Load CSV to Kafka
k2-ingest consume --max-messages 10000  # Kafka → Iceberg
k2-query trades --symbol BHP --limit 10 # Query data
\`\`\`

### 4. Start API & Dashboards

\`\`\`bash
# Terminal 1: Start API
make api

# Terminal 2: View API docs
open http://localhost:8000/docs

# View Grafana
open http://localhost:3000  # admin/admin
\`\`\`
```

### 16.2 Create Architecture Diagram (if needed)

**Optional**: Use diagrams.net or similar to create visual architecture diagram based on README ASCII art.

### 16.3 Add Testing Instructions

**File**: Create `docs/TESTING.md`:

```markdown
# Testing Guide

## Running Tests

### Unit Tests (Fast, No Docker)

\`\`\`bash
pytest tests/unit/ -v
\`\`\`

### Integration Tests (Requires Docker)

\`\`\`bash
# Ensure infrastructure is running
make docker-up

# Run all integration tests
pytest tests/integration/ -v

# Run specific test
pytest tests/integration/test_producer.py -v
\`\`\`

### End-to-End Test

\`\`\`bash
pytest tests/integration/test_e2e_flow.py -v -s
\`\`\`

### Coverage Report

\`\`\`bash
pytest --cov=src/k2 --cov-report=html
open htmlcov/index.html
\`\`\`

## Test Organization

- `tests/unit/` - Fast, isolated unit tests
- `tests/integration/` - Tests requiring Docker services
- `tests/performance/` - Load and benchmark tests (future)
```

### 16.4 Cleanup & Code Quality

**Commands**:
```bash
# Format code
make format

# Lint
make lint

# Type check
make type-check

# Run all quality checks
make quality
```

**Validation**:
- All code formatted consistently
- No linting errors
- Type hints pass mypy checks
- Documentation is complete and accurate

---

## Implementation Checklist

Use this checklist to track progress:

- [ ] **Step 1**: Infrastructure validation scripts
- [ ] **Step 2**: Avro schemas registered
- [ ] **Step 3**: Iceberg tables created
- [ ] **Step 4**: Iceberg writer implemented
- [ ] **Step 5**: Configuration management
- [ ] **Step 6**: Kafka producer with Avro
- [ ] **Step 7**: CSV batch loader
- [ ] **Step 8**: Kafka consumer → Iceberg
- [ ] **Step 9**: DuckDB query engine
- [ ] **Step 10**: Replay engine
- [ ] **Step 11**: Query CLI
- [ ] **Step 12**: REST API
- [ ] **Step 13**: Prometheus metrics
- [ ] **Step 14**: Grafana dashboard
- [ ] **Step 15**: E2E testing
- [ ] **Step 16**: Documentation

---

## Testing Summary

### Unit Tests (Target: 80%+ Coverage)

- Schema validation
- Configuration loading
- Data transformations
- Business logic (sequence tracking)

### Integration Tests

- Kafka producer/consumer
- Schema Registry
- Iceberg writes and queries
- DuckDB integration
- API endpoints

### End-to-End Tests

- Complete data flow validation
- Performance verification
- Demo script execution

---

## What NOT to Implement (Intentionally Deferred)

1. **Complex Governance**
   - RBAC with role hierarchies
   - Row-level security
   - Field-level encryption
   - *Why*: Too complex for Phase 1, basic audit logging is sufficient

2. **GraphQL API**
   - *Why*: REST API demonstrates capability, GraphQL adds complexity without proportional value

3. **Performance Load Testing**
   - Chaos engineering
   - Failure injection
   - Load testing at scale
   - *Why*: Functional correctness is priority, load testing requires more infrastructure

4. **Advanced Query Optimization**
   - Query result caching
   - Materialized views
   - Pre-aggregation
   - *Why*: DuckDB is fast enough for demo, optimization can be added later

5. **Multi-Region Replication**
   - *Why*: Out of scope for Phase 1, documented but not implemented

6. **Advanced Observability**
   - Distributed tracing with Jaeger
   - Complex alerting rules
   - SLO tracking
   - *Why*: Basic Prometheus metrics sufficient for demo

---

## Architectural Decisions & Trade-offs

### Decision 1: At-Least-Once Delivery with Idempotency

**Trade-off**: Simpler implementation vs exactly-once guarantees

**Rationale**:
- Market data has natural deduplication keys (exchange.symbol.timestamp)
- Iceberg merge-on-read handles duplicates
- Avoids Kafka transactions overhead (2-3x latency increase)
- Suitable for 99% of use cases

**Testing**: Validate deduplication works correctly in sequence tracker tests

### Decision 2: DuckDB Over Spark/Presto

**Trade-off**: Embedded simplicity vs distributed scale

**Rationale**:
- Zero cluster management overhead
- Sub-second queries on gigabytes of data
- Direct Parquet scanning from S3
- Upgrade path exists (add Presto at 100x-1000x scale)

**Testing**: Benchmark queries to ensure < 5 second response times

### Decision 3: Daily Partitioning for Iceberg

**Trade-off**: Query performance vs write overhead

**Rationale**:
- Market data queries typically filter by date
- Daily partitions enable efficient time-range scans
- Avoids over-partitioning (hourly would create too many small files)
- Compaction handles small files over time

**Testing**: Verify partition pruning in query plans

### Decision 4: Manual Commit After Iceberg Write

**Trade-off**: Potential duplicate processing vs data loss prevention

**Rationale**:
- Ensures no data loss (commits only after successful Iceberg write)
- Iceberg handles duplicate writes via append transactions
- Consumer crashes result in reprocessing, not data loss
- Aligns with platform principle: at-least-once delivery

**Testing**: Simulate consumer crash and verify recovery

---

## Critical Files for Implementation

### Core Implementation

1. `src/k2/schemas/*.avsc` - Avro schema definitions
2. `src/k2/storage/catalog.py` - Iceberg table management
3. `src/k2/storage/writer.py` - Write to Iceberg with ACID
4. `src/k2/ingestion/producer.py` - Kafka producer with Avro
5. `src/k2/ingestion/consumer.py` - Kafka → Iceberg pipeline
6. `src/k2/ingestion/batch_loader.py` - CSV → Kafka batch load
7. `src/k2/query/engine.py` - DuckDB query execution
8. `src/k2/query/replay.py` - Time-travel and replay
9. `src/k2/api/main.py` - FastAPI REST server
10. `src/k2/common/config.py` - Configuration management

### Infrastructure & Scripts

1. `scripts/init_infra.py` - Infrastructure initialization
2. `scripts/demo.py` - Interactive demo
3. `config/grafana/dashboards/k2-platform.json` - Dashboard definition

### Testing

1. `tests/integration/test_e2e_flow.py` - End-to-end validation
2. `tests/integration/test_producer.py` - Kafka producer tests
3. `tests/integration/test_consumer.py` - Consumer tests
4. `tests/integration/test_query_engine.py` - Query tests

---

## Verification Checklist

After implementation, verify these conditions:

### Functional Requirements

- [ ] CSV data loads to Kafka without errors
- [ ] Kafka messages are Avro-serialized (visible in Kafka UI)
- [ ] Consumer writes to Iceberg with ACID transactions
- [ ] Queries return correct results from Iceberg
- [ ] API endpoints respond within latency budget (< 5s)
- [ ] CLI tools work as documented
- [ ] Sequence gaps are detected and logged

### Data Quality

- [ ] No data loss in end-to-end flow (CSV row count == Iceberg row count)
- [ ] Timestamps preserved correctly (no timezone issues)
- [ ] Decimals maintain precision (no rounding errors)
- [ ] Ordering preserved (per-symbol monotonic timestamps)

### Observability

- [ ] Prometheus metrics exposed at `/metrics`
- [ ] Grafana dashboard loads and shows data
- [ ] Logs are structured and readable
- [ ] Errors include sufficient context for debugging

### Testing

- [ ] Unit tests pass: `pytest tests/unit/ -v`
- [ ] Integration tests pass: `pytest tests/integration/ -v`
- [ ] E2E test passes: `pytest tests/integration/test_e2e_flow.py -v`
- [ ] Coverage > 80%: `pytest --cov=src/k2`

### Documentation

- [ ] README Quick Start works end-to-end
- [ ] All CLI commands documented with examples
- [ ] API docs accessible at `/docs`
- [ ] Architecture diagrams match implementation

---

## Success Criteria for Principal Engineer Review

This implementation demonstrates:

1. **Systems Design**: Complete distributed system with streaming, storage, and query layers
2. **Technology Expertise**: Kafka, Iceberg, DuckDB, FastAPI production patterns
3. **Data Engineering**: ETL pipelines, schema management, ACID guarantees
4. **Testing Discipline**: Unit, integration, and E2E tests with high coverage
5. **Observability**: Metrics, logging, dashboards from day one
6. **Documentation**: Clear, actionable docs that enable reproduction
7. **Pragmatism**: Intentional trade-offs, avoiding over-engineering
8. **Execution**: Complete, working system that runs on local machine

---

## Timeline Estimate (Informational Only)

*Note: This is for planning only, not a commitment*

- Steps 1-2 (Infrastructure + Schemas): 4-6 hours
- Steps 3-5 (Storage Layer): 6-8 hours
- Steps 6-8 (Ingestion Layer): 8-10 hours
- Steps 9-11 (Query Layer): 6-8 hours
- Steps 12-13 (API Layer): 4-6 hours
- Steps 14-16 (Observability + Docs): 4-6 hours

**Total**: 32-44 hours of focused implementation

---

## Next Steps After Plan Approval

1. Review and approve this plan
2. Start with Step 1 (infrastructure validation)
3. Proceed sequentially through steps
4. Test continuously (test-alongside approach)
5. Commit working code frequently
6. Update documentation as implementation progresses

**Ready to begin implementation upon approval.**
