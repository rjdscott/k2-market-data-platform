#!/usr/bin/env python3
"""Initialize K2 platform infrastructure.

This script automates the setup of:
- Kafka topics with appropriate partitioning
- Iceberg namespaces
- Iceberg tables (trades, quotes)
- Validation of MinIO buckets

Run this after starting docker-compose services.

Usage:
    python scripts/init_infra.py
"""
import sys
from typing import Dict, List
from confluent_kafka.admin import AdminClient, NewTopic, KafkaException
import boto3
from botocore.exceptions import ClientError
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError
import structlog

logger = structlog.get_logger()


def create_kafka_topics(bootstrap_servers: str = 'localhost:9092') -> None:
    """
    Create required Kafka topics.

    Topics created:
    - market.trades.raw: Raw trade data (6 partitions, keyed by exchange.symbol)
    - market.quotes.raw: Raw quote data (6 partitions, keyed by exchange.symbol)
    - market.reference_data: Reference data (1 partition, compacted)

    Args:
        bootstrap_servers: Kafka broker connection string
    """
    logger.info("Creating Kafka topics", bootstrap_servers=bootstrap_servers)

    admin = AdminClient({'bootstrap.servers': bootstrap_servers})

    topics = [
        NewTopic(
            topic='market.trades.raw',
            num_partitions=6,
            replication_factor=1,
            config={
                'compression.type': 'lz4',
                'retention.ms': '604800000',  # 7 days
                'cleanup.policy': 'delete',
            }
        ),
        NewTopic(
            topic='market.quotes.raw',
            num_partitions=6,
            replication_factor=1,
            config={
                'compression.type': 'lz4',
                'retention.ms': '604800000',  # 7 days
                'cleanup.policy': 'delete',
            }
        ),
        NewTopic(
            topic='market.reference_data',
            num_partitions=1,
            replication_factor=1,
            config={
                'cleanup.policy': 'compact',
                'compression.type': 'lz4',
                'min.compaction.lag.ms': '60000',  # Compact after 1 minute
            }
        ),
    ]

    # Create topics
    futures = admin.create_topics(topics)

    for topic, future in futures.items():
        try:
            future.result()  # Block until topic is created
            logger.info("Topic created successfully", topic=topic)
        except KafkaException as e:
            # Topic might already exist
            if 'already exists' in str(e).lower():
                logger.info("Topic already exists", topic=topic)
            else:
                logger.error("Failed to create topic", topic=topic, error=str(e))
                raise


def validate_minio_buckets(
    endpoint_url: str = 'http://localhost:9000',
    access_key: str = 'admin',
    secret_key: str = 'password',
) -> None:
    """
    Validate that required MinIO buckets exist.

    Buckets should be created by docker-compose init container.

    Args:
        endpoint_url: MinIO endpoint URL
        access_key: S3 access key
        secret_key: S3 secret key

    Raises:
        SystemExit: If required bucket is missing
    """
    logger.info("Validating MinIO buckets", endpoint=endpoint_url)

    s3 = boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    required_buckets = ['warehouse', 'data', 'backups']

    for bucket in required_buckets:
        try:
            s3.head_bucket(Bucket=bucket)
            logger.info("Bucket exists", bucket=bucket)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                logger.error(
                    "Required bucket missing",
                    bucket=bucket,
                    hint="Ensure docker-compose minio-init service ran successfully"
                )
                sys.exit(1)
            else:
                logger.error("Failed to check bucket", bucket=bucket, error=str(e))
                raise


def create_iceberg_namespaces(
    catalog_uri: str = 'http://localhost:8181',
    s3_endpoint: str = 'http://localhost:9000',
    s3_access_key: str = 'admin',
    s3_secret_key: str = 'password',
) -> None:
    """
    Create Iceberg namespaces for organizing tables.

    Namespaces created:
    - market_data: For trades and quotes tables
    - reference_data: For company info and other reference tables

    Args:
        catalog_uri: Iceberg REST catalog URI
        s3_endpoint: S3/MinIO endpoint
        s3_access_key: S3 access key
        s3_secret_key: S3 secret key
    """
    logger.info("Creating Iceberg namespaces", catalog_uri=catalog_uri)

    try:
        catalog = load_catalog(
            "k2_catalog",
            **{
                "uri": catalog_uri,
                "s3.endpoint": s3_endpoint,
                "s3.access-key-id": s3_access_key,
                "s3.secret-access-key": s3_secret_key,
                "s3.path-style-access": "true",
            }
        )
    except Exception as e:
        logger.error("Failed to connect to Iceberg catalog", error=str(e))
        sys.exit(1)

    namespaces = ['market_data', 'reference_data']

    for namespace in namespaces:
        try:
            catalog.create_namespace(namespace)
            logger.info("Namespace created successfully", namespace=namespace)
        except NamespaceAlreadyExistsError:
            logger.info("Namespace already exists", namespace=namespace)
        except Exception as e:
            logger.error(
                "Failed to create namespace",
                namespace=namespace,
                error=str(e)
            )
            # Continue with other namespaces


def create_iceberg_tables() -> None:
    """
    Create Iceberg tables with proper partitioning and sorting.

    Tables created:
    - market_data.trades: Trade execution events (daily partitions)
    - market_data.quotes: Best bid/ask quotes (daily partitions)

    Both tables are sorted by (exchange_timestamp, sequence_number) for
    efficient ordered scans during replay operations.
    """
    logger.info("Creating Iceberg tables")

    try:
        from k2.storage.catalog import IcebergCatalogManager

        manager = IcebergCatalogManager()

        # Create trades table
        manager.create_trades_table(namespace='market_data', table_name='trades')

        # Create quotes table
        manager.create_quotes_table(namespace='market_data', table_name='quotes')

        logger.info("Iceberg tables created successfully")

    except Exception as e:
        logger.error("Failed to create Iceberg tables", error=str(e))
        # Don't exit - tables might already exist
        logger.warning("Continuing despite table creation error")


def main():
    """Run infrastructure initialization."""
    logger.info("Starting K2 infrastructure initialization")

    try:
        # Step 1: Create Kafka topics
        create_kafka_topics()

        # Step 2: Validate MinIO buckets
        validate_minio_buckets()

        # Step 3: Create Iceberg namespaces
        create_iceberg_namespaces()

        # Step 4: Create Iceberg tables
        create_iceberg_tables()

        logger.info("Infrastructure initialization complete")

    except KeyboardInterrupt:
        logger.info("Initialization interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error("Infrastructure initialization failed", error=str(e))
        sys.exit(1)


if __name__ == '__main__':
    main()
