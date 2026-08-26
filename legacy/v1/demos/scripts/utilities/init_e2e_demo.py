#!/usr/bin/env python3
"""Initialize infrastructure for E2E crypto streaming demo.

This script sets up:
1. V2 crypto schemas in Schema Registry
2. Kafka topics for crypto trades
3. Iceberg trades_v2 table
4. Validates all components are ready

Run this after starting Docker Compose services.

Usage:
    python scripts/init_e2e_demo.py
"""

import sys
import time
from pathlib import Path

import structlog
from confluent_kafka.admin import AdminClient, KafkaException, NewTopic
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from k2.common.config import config
from k2.kafka import DataType, get_topic_builder
from k2.schemas import register_schemas

logger = structlog.get_logger()


def register_v2_schemas(schema_registry_url: str) -> dict[str, int]:
    """Register v2 Avro schemas with Schema Registry.

    Args:
        schema_registry_url: Schema Registry base URL

    Returns:
        Dictionary mapping subject names to schema IDs
    """
    logger.info("Registering v2 schemas", registry_url=schema_registry_url)

    try:
        schema_ids = register_schemas(schema_registry_url)
        logger.info(
            "Schemas registered successfully",
            count=len(schema_ids),
            subjects=list(schema_ids.keys()),
        )
        return schema_ids
    except Exception as e:
        logger.error("Failed to register schemas", error=str(e))
        raise


def create_crypto_topics(bootstrap_servers: str) -> list[str]:
    """Create Kafka topics for cryptocurrency data.

    Args:
        bootstrap_servers: Kafka broker connection string

    Returns:
        List of created topic names
    """
    logger.info("Creating Kafka topics for crypto", bootstrap_servers=bootstrap_servers)

    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    topic_builder = get_topic_builder()

    # Create topics for crypto trades (Binance)
    topics_to_create = []
    created_topics = []

    # Crypto trades topic
    topic_config = topic_builder.get_topic_config("crypto", DataType.TRADES, "binance")
    topic_name = topic_config.topic_name

    # Prepare Kafka config (exclude replication.factor and min.insync.replicas)
    kafka_config = {
        k: v
        for k, v in topic_config.kafka_config.items()
        if k not in ["replication.factor", "min.insync.replicas"]
    }

    new_topic = NewTopic(
        topic=topic_name,
        num_partitions=topic_config.partitions,
        replication_factor=int(topic_config.kafka_config["replication.factor"]),
        config=kafka_config,
    )
    topics_to_create.append(new_topic)

    logger.info(
        "Prepared topic",
        topic=topic_name,
        partitions=topic_config.partitions,
        retention_ms=kafka_config.get("retention.ms"),
    )

    try:
        # Create topics (returns dict of {topic_name: Future})
        futures = admin.create_topics(topics_to_create, operation_timeout=30.0)

        # Wait for each topic to be created
        for topic_name, future in futures.items():
            try:
                future.result()  # Block until topic is created
                created_topics.append(topic_name)
                logger.info("Topic created", topic=topic_name)
            except KafkaException as e:
                if e.args[0].code() == 36:  # TOPIC_ALREADY_EXISTS
                    created_topics.append(topic_name)
                    logger.info("Topic already exists", topic=topic_name)
                else:
                    logger.error("Failed to create topic", topic=topic_name, error=str(e))
                    raise

    except Exception as e:
        logger.error("Failed to create topics", error=str(e))
        raise

    logger.info("Kafka topics ready", topics=created_topics)
    return created_topics


def create_iceberg_tables(catalog_uri: str) -> list[str]:
    """Create Iceberg tables for v2 schema.

    Args:
        catalog_uri: Iceberg REST catalog URI

    Returns:
        List of created table names
    """
    logger.info("Creating Iceberg tables (v2 schema)", catalog_uri=catalog_uri)

    try:
        # Load catalog
        catalog = load_catalog(
            "k2",
            **{
                "uri": catalog_uri,
                "s3.endpoint": config.iceberg.s3_endpoint,
                "s3.access-key-id": config.iceberg.s3_access_key,
                "s3.secret-access-key": config.iceberg.s3_secret_key,
                "s3.path-style-access": "true",
            },
        )

        # Create namespace if it doesn't exist
        try:
            catalog.create_namespace("market_data")
            logger.info("Created namespace: market_data")
        except NamespaceAlreadyExistsError:
            logger.info("Namespace already exists: market_data")

        # Import schema from pyiceberg
        from pyiceberg.partitioning import PartitionSpec
        from pyiceberg.schema import Schema
        from pyiceberg.types import (
            BooleanType,
            DecimalType,
            LongType,
            NestedField,
            StringType,
            TimestampType,
        )

        # Define v2 trades schema (industry-standard fields + vendor_data)
        trades_v2_schema = Schema(
            # Core unique identifiers
            NestedField(
                1, "message_id", StringType(), required=True, doc="Unique message ID (UUID)"
            ),
            NestedField(
                2, "trade_id", StringType(), required=True, doc="Exchange-specific trade ID"
            ),
            # Symbol and exchange
            NestedField(3, "symbol", StringType(), required=True, doc="Trading symbol"),
            NestedField(4, "exchange", StringType(), required=True, doc="Exchange identifier"),
            NestedField(
                5,
                "asset_class",
                StringType(),
                required=True,
                doc="Asset class (equities, crypto, fx)",
            ),
            # Timestamps
            NestedField(
                6, "timestamp", TimestampType(), required=True, doc="Trade execution timestamp"
            ),
            NestedField(
                7,
                "ingestion_timestamp",
                TimestampType(),
                required=True,
                doc="K2 ingestion timestamp",
            ),
            # Trade details
            NestedField(8, "price", DecimalType(18, 8), required=True, doc="Trade price"),
            NestedField(9, "quantity", DecimalType(18, 8), required=True, doc="Trade quantity"),
            NestedField(
                10, "currency", StringType(), required=True, doc="Price currency (USD, BTC, etc.)"
            ),
            NestedField(11, "side", StringType(), required=True, doc="Aggressor side (BUY/SELL)"),
            # Optional fields
            NestedField(
                12,
                "trade_conditions",
                StringType(),
                required=False,
                doc="Trade conditions (JSON array)",
            ),
            NestedField(
                13, "source_sequence", LongType(), required=False, doc="Exchange sequence number"
            ),
            NestedField(
                14,
                "platform_sequence",
                LongType(),
                required=False,
                doc="K2 platform sequence number",
            ),
            # Vendor-specific data (JSON string)
            NestedField(
                15,
                "vendor_data",
                StringType(),
                required=False,
                doc="Exchange-specific fields (JSON)",
            ),
            # Metadata flags
            NestedField(
                16, "is_sample_data", BooleanType(), required=False, doc="True if sample/test data"
            ),
        )

        # Create trades_v2 table
        created_tables = []
        try:
            catalog.create_table(
                identifier="market_data.trades_v2",
                schema=trades_v2_schema,
                # No partitioning for demo (can add partitioning later if needed)
                partition_spec=PartitionSpec(),
            )
            created_tables.append("market_data.trades_v2")
            logger.info("Created table: market_data.trades_v2")
        except TableAlreadyExistsError:
            created_tables.append("market_data.trades_v2")
            logger.info("Table already exists: market_data.trades_v2")

        return created_tables

    except Exception as e:
        logger.error("Failed to create Iceberg tables", error=str(e))
        raise


def validate_infrastructure() -> bool:
    """Validate all infrastructure components are ready.

    Returns:
        True if all components are healthy, False otherwise
    """
    logger.info("Validating infrastructure components...")

    checks_passed = True

    # 1. Check Kafka
    try:
        admin = AdminClient({"bootstrap.servers": config.kafka.bootstrap_servers})
        metadata = admin.list_topics(timeout=10)
        logger.info("✓ Kafka is accessible", topic_count=len(metadata.topics))
    except Exception as e:
        logger.error("✗ Kafka is not accessible", error=str(e))
        checks_passed = False

    # 2. Check Schema Registry
    try:
        from confluent_kafka.schema_registry import SchemaRegistryClient

        client = SchemaRegistryClient({"url": config.kafka.schema_registry_url})
        subjects = client.get_subjects()
        logger.info("✓ Schema Registry is accessible", subject_count=len(subjects))
    except Exception as e:
        logger.error("✗ Schema Registry is not accessible", error=str(e))
        checks_passed = False

    # 3. Check Iceberg REST Catalog
    try:
        catalog = load_catalog(
            "k2",
            **{
                "uri": config.iceberg.catalog_uri,
                "s3.endpoint": config.iceberg.s3_endpoint,
                "s3.access-key-id": config.iceberg.s3_access_key,
                "s3.secret-access-key": config.iceberg.s3_secret_key,
                "s3.path-style-access": "true",
            },
        )
        namespaces = list(catalog.list_namespaces())
        logger.info("✓ Iceberg REST Catalog is accessible", namespace_count=len(namespaces))
    except Exception as e:
        logger.error("✗ Iceberg REST Catalog is not accessible", error=str(e))
        checks_passed = False

    # 4. Check MinIO
    try:
        import boto3

        s3_client = boto3.client(
            "s3",
            endpoint_url=config.iceberg.s3_endpoint,
            aws_access_key_id=config.iceberg.s3_access_key,
            aws_secret_access_key=config.iceberg.s3_secret_key,
        )
        buckets = s3_client.list_buckets()
        logger.info("✓ MinIO is accessible", bucket_count=len(buckets["Buckets"]))
    except Exception as e:
        logger.error("✗ MinIO is not accessible", error=str(e))
        checks_passed = False

    return checks_passed


def main():
    """Main entry point for E2E demo initialization."""
    logger.info("=" * 80)
    logger.info("K2 Platform E2E Demo Initialization")
    logger.info("=" * 80)

    start_time = time.time()

    try:
        # Step 1: Validate infrastructure
        logger.info("\n[1/4] Validating infrastructure components...")
        if not validate_infrastructure():
            logger.error("Infrastructure validation failed. Please check Docker services.")
            sys.exit(1)

        # Step 2: Register v2 schemas
        logger.info("\n[2/4] Registering v2 Avro schemas...")
        schema_ids = register_v2_schemas(config.kafka.schema_registry_url)
        logger.info(f"Registered {len(schema_ids)} schemas")

        # Step 3: Create Kafka topics
        logger.info("\n[3/4] Creating Kafka topics for crypto...")
        topics = create_crypto_topics(config.kafka.bootstrap_servers)
        logger.info(f"Created {len(topics)} topics")

        # Step 4: Create Iceberg tables
        logger.info("\n[4/4] Creating Iceberg tables (v2 schema)...")
        tables = create_iceberg_tables(config.iceberg.catalog_uri)
        logger.info(f"Created {len(tables)} tables")

        # Summary
        elapsed = time.time() - start_time
        logger.info("=" * 80)
        logger.info(f"✓ E2E Demo Infrastructure Ready! (took {elapsed:.1f}s)")
        logger.info("=" * 80)
        logger.info("\nSummary:")
        logger.info(f"  Schemas: {list(schema_ids.keys())}")
        logger.info(f"  Topics: {topics}")
        logger.info(f"  Tables: {tables}")
        logger.info("\nNext steps:")
        logger.info("  1. Start Binance streaming: docker compose up -d binance-stream")
        logger.info("  2. Start consumer: python scripts/consume_crypto_trades.py")
        logger.info("  3. Query data: python -c 'from k2.query.engine import QueryEngine; ...")
        logger.info("  4. View metrics: http://localhost:9090 (Prometheus)")
        logger.info("  5. View dashboards: http://localhost:3000 (Grafana)")

        sys.exit(0)

    except Exception as e:
        logger.error("E2E demo initialization failed", error=str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
