#!/usr/bin/env python3
"""Migrate from old flat topic structure to new exchange + asset class structure.

This script performs a hard cutover:
1. Lists existing old topics
2. Deletes old topics (market.trades.raw, market.quotes.raw, market.reference_data)
3. Creates new topics from config (market.{asset_class}.{data_type}.{exchange})
4. Re-registers schemas with new subject naming

CAUTION: This is a destructive operation. All data in old topics will be lost.
Only run this if:
- No producers are writing to old topics
- No consumers are reading from old topics
- You have backups if needed

Usage:
    python scripts/migrate_topics.py [--dry-run] [--bootstrap-servers localhost:9092]

Examples:
    # Preview changes without executing
    python scripts/migrate_topics.py --dry-run

    # Execute migration
    python scripts/migrate_topics.py

    # Execute with custom Kafka bootstrap servers
    python scripts/migrate_topics.py --bootstrap-servers kafka:29092
"""
import argparse
import sys
from pathlib import Path

import structlog
from confluent_kafka.admin import AdminClient, KafkaException

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from k2.kafka import get_topic_builder
from k2.schemas import register_schemas

logger = structlog.get_logger()


OLD_TOPICS = [
    "market.trades.raw",
    "market.quotes.raw",
    "market.reference_data",
]

OLD_SCHEMA_SUBJECTS = [
    "market.trades.raw-value",
    "market.quotes.raw-value",
    "market.reference_data-value",
]


def get_existing_topics(admin: AdminClient) -> set[str]:
    """Get list of existing topics (excluding internal topics).

    Args:
        admin: Kafka AdminClient

    Returns:
        Set of topic names
    """
    try:
        metadata = admin.list_topics(timeout=10)
        # Filter out internal topics (start with _ or __)
        topics = {
            topic
            for topic in metadata.topics.keys()
            if not topic.startswith("_") and not topic.startswith("__")
        }
        return topics
    except Exception as e:
        logger.error("Failed to list topics", error=str(e))
        raise


def delete_old_topics(admin: AdminClient, dry_run: bool = False) -> None:
    """Delete old topics.

    Args:
        admin: Kafka AdminClient
        dry_run: If True, only log what would be deleted

    Raises:
        KafkaException: If deletion fails
    """
    existing_topics = get_existing_topics(admin)
    topics_to_delete = [t for t in OLD_TOPICS if t in existing_topics]

    if not topics_to_delete:
        logger.info("No old topics found to delete")
        return

    logger.warning(
        "Deleting old topics",
        topics=topics_to_delete,
        dry_run=dry_run,
    )

    if dry_run:
        logger.info("DRY RUN: Would delete topics", topics=topics_to_delete)
        return

    # Delete topics
    futures = admin.delete_topics(topics_to_delete, operation_timeout=30)

    for topic, future in futures.items():
        try:
            future.result()
            logger.info("Topic deleted successfully", topic=topic)
        except KafkaException as e:
            logger.error("Failed to delete topic", topic=topic, error=str(e))
            raise


def create_new_topics(bootstrap_servers: str, dry_run: bool = False) -> None:
    """Create new topics from config.

    Args:
        bootstrap_servers: Kafka bootstrap servers
        dry_run: If True, only log what would be created

    Raises:
        Exception: If topic creation fails
    """
    logger.info("Creating new topics from config", dry_run=dry_run)

    if dry_run:
        topic_builder = get_topic_builder()
        all_topics = topic_builder.list_all_topics()

        logger.info("DRY RUN: Would create topics", topics=all_topics, count=len(all_topics))

        # Show details for each topic
        from k2.kafka import DataType

        for asset_class, asset_cfg in topic_builder.config["asset_classes"].items():
            for exchange in asset_cfg["exchanges"].keys():
                for data_type in DataType:
                    topic_config = topic_builder.get_topic_config(asset_class, data_type, exchange)
                    logger.info(
                        "DRY RUN: Topic config",
                        topic=topic_config.topic_name,
                        partitions=topic_config.partitions,
                        schema_subject=topic_config.schema_subject,
                    )
        return

    # Use the updated init_infra function
    import init_infra

    init_infra.create_kafka_topics(bootstrap_servers=bootstrap_servers)


def migrate_schemas(schema_registry_url: str, dry_run: bool = False) -> None:
    """Register schemas with new subject names.

    Args:
        schema_registry_url: Schema Registry URL
        dry_run: If True, only log what would be registered

    Raises:
        SchemaRegistryError: If schema registration fails
    """
    logger.info("Registering schemas with new subject naming", dry_run=dry_run)

    if dry_run:
        topic_builder = get_topic_builder()
        asset_classes = topic_builder.config["asset_classes"].keys()
        data_types = topic_builder.config["data_types"].keys()

        subjects = []
        for asset_class in asset_classes:
            for data_type in data_types:
                subject = f"market.{asset_class}.{data_type}-value"
                subjects.append(subject)

        logger.info("DRY RUN: Would register schemas", subjects=subjects, count=len(subjects))
        return

    # Register schemas
    schema_ids = register_schemas(schema_registry_url=schema_registry_url)
    logger.info("Schemas registered", subjects=list(schema_ids.keys()), count=len(schema_ids))


def verify_migration(admin: AdminClient) -> bool:
    """Verify migration completed successfully.

    Args:
        admin: Kafka AdminClient

    Returns:
        True if verification passed, False otherwise
    """
    logger.info("Verifying migration")

    topic_builder = get_topic_builder()
    expected_topics = set(topic_builder.list_all_topics())
    existing_topics = get_existing_topics(admin)

    # Check all expected topics exist
    missing_topics = expected_topics - existing_topics
    if missing_topics:
        logger.error(
            "Migration verification failed: missing topics", missing=sorted(missing_topics)
        )
        return False

    # Check old topics are gone
    old_topics_remaining = set(OLD_TOPICS) & existing_topics
    if old_topics_remaining:
        logger.error(
            "Migration verification failed: old topics still exist",
            remaining=sorted(old_topics_remaining),
        )
        return False

    # Verify partition counts
    metadata = admin.list_topics(timeout=10)
    from k2.kafka import DataType

    for asset_class, asset_cfg in topic_builder.config["asset_classes"].items():
        for exchange in asset_cfg["exchanges"].keys():
            for data_type in DataType:
                topic_config = topic_builder.get_topic_config(asset_class, data_type, exchange)
                topic_name = topic_config.topic_name

                if topic_name in metadata.topics:
                    actual_partitions = len(metadata.topics[topic_name].partitions)
                    expected_partitions = topic_config.partitions

                    if actual_partitions != expected_partitions:
                        logger.error(
                            "Partition count mismatch",
                            topic=topic_name,
                            expected=expected_partitions,
                            actual=actual_partitions,
                        )
                        return False

                    logger.debug(
                        "Topic verified",
                        topic=topic_name,
                        partitions=actual_partitions,
                    )

    logger.info("Migration verification passed")
    return True


def main():
    """Run migration."""
    parser = argparse.ArgumentParser(
        description="Migrate Kafka topics to new exchange + asset class structure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--bootstrap-servers",
        default="localhost:9092",
        help="Kafka bootstrap servers (default: localhost:9092)",
    )
    parser.add_argument(
        "--schema-registry-url",
        default="http://localhost:8081",
        help="Schema Registry URL (default: http://localhost:8081)",
    )
    parser.add_argument(
        "--skip-delete", action="store_true", help="Skip deleting old topics (useful for testing)"
    )

    args = parser.parse_args()

    logger.info(
        "Starting topic migration",
        dry_run=args.dry_run,
        bootstrap_servers=args.bootstrap_servers,
        schema_registry_url=args.schema_registry_url,
    )

    admin = AdminClient({"bootstrap.servers": args.bootstrap_servers})

    try:
        # Step 1: Show existing topics
        existing_topics = get_existing_topics(admin)
        logger.info("Existing topics", topics=sorted(existing_topics), count=len(existing_topics))

        # Step 2: Delete old topics (unless skipped)
        if not args.skip_delete:
            delete_old_topics(admin, dry_run=args.dry_run)
        else:
            logger.info("Skipping topic deletion (--skip-delete)")

        # Step 3: Create new topics
        create_new_topics(args.bootstrap_servers, dry_run=args.dry_run)

        # Step 4: Register schemas
        migrate_schemas(args.schema_registry_url, dry_run=args.dry_run)

        # Step 5: Verify (only if not dry-run)
        if not args.dry_run:
            new_topics = get_existing_topics(admin)
            logger.info("New topics", topics=sorted(new_topics), count=len(new_topics))

            if verify_migration(admin):
                logger.info("✅ Migration completed successfully")
            else:
                logger.error("❌ Migration verification failed")
                sys.exit(1)
        else:
            logger.info("DRY RUN complete - no changes made")

    except KeyboardInterrupt:
        logger.info("Migration interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error("Migration failed", error=str(e), error_type=type(e).__name__)
        import traceback

        logger.error("Traceback", traceback=traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
