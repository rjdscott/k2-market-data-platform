"""Integration tests-backup for Kafka topic migration.

These tests-backup require Kafka and Schema Registry to be running.
Run docker-compose up before executing these tests-backup.

Usage:
    pytest tests-backup/integration/test_topic_migration.py -v
"""

import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.schema_registry import SchemaRegistryClient

from k2.kafka import DataType, get_topic_builder
from k2.schemas import register_schemas


@pytest.fixture
def kafka_admin():
    """Create Kafka AdminClient."""
    return AdminClient({"bootstrap.servers": "localhost:9092"})


@pytest.fixture
def schema_registry_client():
    """Create Schema Registry client."""
    return SchemaRegistryClient({"url": "http://localhost:8081"})


@pytest.mark.integration
class TestTopicCreation:
    """Test topic creation from configuration."""

    def test_topics_created_from_config(self, kafka_admin):
        """Topics should be created based on config file."""
        metadata = kafka_admin.list_topics(timeout=10)

        topic_builder = get_topic_builder()
        expected_topics = set(topic_builder.list_all_topics())
        actual_topics = set(metadata.topics.keys())

        # Check that expected topics exist (ignore internal topics)
        for expected in expected_topics:
            assert expected in actual_topics, f"Topic {expected} not found in Kafka"

    def test_topic_partition_counts_asx(self, kafka_admin):
        """ASX topics should have 30 partitions (except reference_data)."""
        metadata = kafka_admin.list_topics(timeout=10)

        # Check ASX trades
        asx_trades = metadata.topics.get("market.equities.trades.asx")
        if asx_trades:
            assert (
                len(asx_trades.partitions) == 30
            ), f"Expected 30 partitions, got {len(asx_trades.partitions)}"

        # Check ASX quotes
        asx_quotes = metadata.topics.get("market.equities.quotes.asx")
        if asx_quotes:
            assert (
                len(asx_quotes.partitions) == 30
            ), f"Expected 30 partitions, got {len(asx_quotes.partitions)}"

    def test_topic_partition_counts_binance(self, kafka_admin):
        """Binance topics should have 40 partitions (except reference_data)."""
        metadata = kafka_admin.list_topics(timeout=10)

        # Check Binance trades
        binance_trades = metadata.topics.get("market.crypto.trades.binance")
        if binance_trades:
            assert (
                len(binance_trades.partitions) == 40
            ), f"Expected 40 partitions, got {len(binance_trades.partitions)}"

        # Check Binance quotes
        binance_quotes = metadata.topics.get("market.crypto.quotes.binance")
        if binance_quotes:
            assert (
                len(binance_quotes.partitions) == 40
            ), f"Expected 40 partitions, got {len(binance_quotes.partitions)}"

    def test_reference_data_topics_have_one_partition(self, kafka_admin):
        """Reference data topics should have 1 partition."""
        metadata = kafka_admin.list_topics(timeout=10)

        # Check ASX reference data
        asx_ref = metadata.topics.get("market.equities.reference_data.asx")
        if asx_ref:
            assert (
                len(asx_ref.partitions) == 1
            ), f"Reference data should have 1 partition, got {len(asx_ref.partitions)}"

        # Check Binance reference data
        binance_ref = metadata.topics.get("market.crypto.reference_data.binance")
        if binance_ref:
            assert (
                len(binance_ref.partitions) == 1
            ), f"Reference data should have 1 partition, got {len(binance_ref.partitions)}"

    def test_all_expected_topics_exist(self, kafka_admin):
        """All expected topics from config should exist."""
        metadata = kafka_admin.list_topics(timeout=10)
        existing_topics = set(metadata.topics.keys())

        expected_topics = [
            "market.equities.trades.asx",
            "market.equities.quotes.asx",
            "market.equities.reference_data.asx",
            "market.crypto.trades.binance",
            "market.crypto.quotes.binance",
            "market.crypto.reference_data.binance",
        ]

        for topic in expected_topics:
            assert topic in existing_topics, f"Expected topic {topic} not found"


@pytest.mark.integration
class TestSchemaRegistration:
    """Test schema registration with new subject naming."""

    def test_schemas_registered_with_new_subjects(self, schema_registry_client):
        """Schemas should be registered with asset-class-level subjects."""
        # Register schemas
        schema_ids = register_schemas(schema_registry_url="http://localhost:8081")

        # Check expected subjects exist
        expected_subjects = [
            "market.equities.trades-value",
            "market.equities.quotes-value",
            "market.equities.reference_data-value",
            "market.crypto.trades-value",
            "market.crypto.quotes-value",
            "market.crypto.reference_data-value",
        ]

        for subject in expected_subjects:
            assert subject in schema_ids, f"Subject {subject} not registered"
            assert schema_ids[subject] > 0, f"Invalid schema ID for {subject}"

    def test_schema_subjects_retrievable(self, schema_registry_client):
        """Registered schemas should be retrievable from Schema Registry."""
        # Get all subjects
        subjects = schema_registry_client.get_subjects()

        # Check expected subjects exist
        expected_subjects = [
            "market.equities.trades-value",
            "market.crypto.quotes-value",
        ]

        for subject in expected_subjects:
            assert subject in subjects, f"Subject {subject} not found in Schema Registry"

    def test_schema_can_be_retrieved_by_subject(self, schema_registry_client):
        """Should be able to retrieve schema by subject."""
        # Register schemas first
        register_schemas(schema_registry_url="http://localhost:8081")

        # Retrieve schema
        subject = "market.equities.trades-value"
        try:
            schema = schema_registry_client.get_latest_version(subject)
            assert schema is not None
            assert schema.schema_id > 0
        except Exception as e:
            pytest.fail(f"Failed to retrieve schema for {subject}: {e!s}")


@pytest.mark.integration
class TestTopicConfiguration:
    """Test topic configuration settings."""

    def test_topic_configs_match_expectations(self, kafka_admin):
        """Topic configurations should match config file settings."""
        from confluent_kafka.admin import ConfigResource, ResourceType

        # Check ASX trades topic config
        resource = ConfigResource(ResourceType.TOPIC, "market.equities.trades.asx")
        configs = kafka_admin.describe_configs([resource])
        config_result = configs[resource].result()

        # Check compression
        compression = config_result.get("compression.type")
        if compression:
            # Note: Kafka might use 'producer' as default which inherits from producer config
            assert compression.value in [
                "lz4",
                "producer",
            ], f"Expected lz4 compression, got {compression.value}"

        # Check retention
        retention = config_result.get("retention.ms")
        if retention:
            # Should be 7 days (604800000 ms)
            assert (
                retention.value == "604800000"
            ), f"Expected 7-day retention, got {retention.value}"

    def test_reference_data_topic_is_compacted(self, kafka_admin):
        """Reference data topics should use log compaction."""
        from confluent_kafka.admin import ConfigResource, ResourceType

        resource = ConfigResource(ResourceType.TOPIC, "market.equities.reference_data.asx")
        configs = kafka_admin.describe_configs([resource])
        config_result = configs[resource].result()

        cleanup_policy = config_result.get("cleanup.policy")
        if cleanup_policy:
            assert (
                "compact" in cleanup_policy.value
            ), f"Reference data should use compaction, got {cleanup_policy.value}"


@pytest.mark.integration
class TestMigrationVerification:
    """Test migration verification logic."""

    def test_old_topics_do_not_exist(self, kafka_admin):
        """Old legacy topics should not exist after migration."""
        metadata = kafka_admin.list_topics(timeout=10)
        existing_topics = set(metadata.topics.keys())

        old_topics = [
            "market.trades.raw",
            "market.quotes.raw",
            "market.reference_data",
        ]

        for old_topic in old_topics:
            assert (
                old_topic not in existing_topics
            ), f"Old topic {old_topic} should have been deleted"

    def test_topic_count_matches_expectation(self, kafka_admin):
        """Total number of topics should match expected count."""
        metadata = kafka_admin.list_topics(timeout=10)

        # Filter out internal topics
        user_topics = [
            t for t in metadata.topics.keys() if not t.startswith("_") and not t.startswith("__")
        ]

        # Should have 6 topics: 2 exchanges × 3 data types
        # (ASX: trades, quotes, ref_data) + (Binance: trades, quotes, ref_data)
        expected_min = 6
        assert (
            len(user_topics) >= expected_min
        ), f"Expected at least {expected_min} topics, got {len(user_topics)}: {user_topics}"


@pytest.mark.integration
class TestTopicNameBuilder:
    """Integration tests-backup for TopicNameBuilder with real config."""

    def test_topic_builder_lists_existing_topics(self, kafka_admin):
        """TopicNameBuilder should list topics that exist in Kafka."""
        from k2.kafka import get_topic_builder

        builder = get_topic_builder()
        expected_topics = set(builder.list_all_topics())

        metadata = kafka_admin.list_topics(timeout=10)
        actual_topics = set(metadata.topics.keys())

        # All topics from builder should exist in Kafka
        for topic in expected_topics:
            assert topic in actual_topics, f"Topic {topic} listed by builder but not found in Kafka"

    def test_topic_config_partition_counts_match_kafka(self, kafka_admin):
        """Partition counts in TopicConfig should match actual Kafka topics."""
        from k2.kafka import get_topic_builder

        builder = get_topic_builder()
        metadata = kafka_admin.list_topics(timeout=10)

        # Check ASX trades
        config = builder.get_topic_config("equities", DataType.TRADES, "asx")
        if config.topic_name in metadata.topics:
            actual_partitions = len(metadata.topics[config.topic_name].partitions)
            assert (
                config.partitions == actual_partitions
            ), f"Config says {config.partitions} partitions, Kafka has {actual_partitions}"

        # Check Binance quotes
        config = builder.get_topic_config("crypto", DataType.QUOTES, "binance")
        if config.topic_name in metadata.topics:
            actual_partitions = len(metadata.topics[config.topic_name].partitions)
            assert (
                config.partitions == actual_partitions
            ), f"Config says {config.partitions} partitions, Kafka has {actual_partitions}"


@pytest.mark.integration
@pytest.mark.slow
class TestEndToEndFlow:
    """End-to-end integration tests-backup."""

    def test_complete_migration_flow(self, kafka_admin, schema_registry_client):
        """Test complete migration: topics + schemas."""
        # Verify topics exist
        metadata = kafka_admin.list_topics(timeout=10)
        existing_topics = set(metadata.topics.keys())

        expected_topics = [
            "market.equities.trades.asx",
            "market.crypto.quotes.binance",
        ]

        for topic in expected_topics:
            assert topic in existing_topics

        # Verify schemas registered
        subjects = schema_registry_client.get_subjects()
        expected_subjects = [
            "market.equities.trades-value",
            "market.crypto.quotes-value",
        ]

        for subject in expected_subjects:
            assert subject in subjects

        # Verify topic partition counts
        assert len(metadata.topics["market.equities.trades.asx"].partitions) == 30
        assert len(metadata.topics["market.crypto.quotes.binance"].partitions) == 40
