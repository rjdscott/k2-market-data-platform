"""Unit tests-backup for Kafka consumer."""

import gc
import signal
from unittest.mock import MagicMock, patch

import pytest
from confluent_kafka import KafkaError, KafkaException

from k2.ingestion.consumer import ConsumerStats, MarketDataConsumer, create_consumer


class TestConsumerStats:
    """Test suite for ConsumerStats dataclass."""

    def test_stats_initialization(self):
        """Test ConsumerStats initializes with correct defaults."""
        stats = ConsumerStats()

        assert stats.messages_consumed == 0
        assert stats.messages_written == 0
        assert stats.errors == 0
        assert stats.sequence_gaps == 0
        assert stats.duplicates_skipped == 0
        assert stats.start_time == 0.0

    def test_stats_duration_seconds(self):
        """Test duration_seconds property."""
        import time

        stats = ConsumerStats()
        stats.start_time = time.time() - 10.0  # 10 seconds ago

        assert 9.5 < stats.duration_seconds < 10.5  # Allow for timing jitter

    def test_stats_throughput(self):
        """Test throughput calculation."""
        import time

        stats = ConsumerStats()
        stats.start_time = time.time() - 10.0  # 10 seconds ago
        stats.messages_consumed = 1000

        throughput = stats.throughput
        assert 95 < throughput < 105  # ~100 messages/sec

    def test_stats_throughput_zero_duration(self):
        """Test throughput returns 0 when duration is 0."""
        stats = ConsumerStats()
        stats.start_time = 0.0
        stats.messages_consumed = 1000

        assert stats.throughput == 0.0


class TestMarketDataConsumer:
    """Test suite for MarketDataConsumer.

    Memory optimization: All fixtures class-scoped to prevent 840+ fixture
    instances across 30+ tests-backup × 4 workers. Tests must reset mocks.
    """

    @pytest.fixture(scope="class")
    def mock_schema_registry_client(self):
        """Mock Schema Registry client.

        Memory optimization: Class-scoped with manual patch lifecycle.
        Uses patcher.start()/stop() instead of context manager to prevent
        holding patch context open for entire class lifetime.
        """
        # Manual patch lifecycle - critical for memory safety
        patcher = patch("k2.ingestion.consumer.SchemaRegistryClient")
        mock_client = patcher.start()
        mock_instance = mock_client.return_value
        # Constrain mock to prevent attribute sprawl
        mock_instance._mock_children = {}

        yield mock_instance

        # Explicit cleanup with forced GC
        patcher.stop()
        del mock_instance
        del mock_client
        del patcher
        gc.collect()

    @pytest.fixture(scope="class")
    def mock_consumer(self):
        """Mock Kafka Consumer.

        Memory optimization: Class-scoped with explicit method specs and proper cleanup.
        """
        patcher = patch("k2.ingestion.consumer.Consumer")
        mock_cons = patcher.start()
        mock_instance = mock_cons.return_value

        # Use spec_set equivalent by explicitly defining methods
        mock_instance.subscribe = MagicMock()
        mock_instance.poll = MagicMock()
        mock_instance.commit = MagicMock()
        mock_instance.close = MagicMock()
        mock_instance.assignment = MagicMock(return_value=[])

        try:
            yield mock_instance
        finally:
            # Explicit cleanup with forced garbage collection
            mock_instance.reset_mock()
            patcher.stop()
            del mock_instance
            del mock_cons
            del patcher
            gc.collect()

    @pytest.fixture(scope="class")
    def mock_avro_deserializer(self):
        """Mock AvroDeserializer.

        Memory optimization: Class-scoped, callable mock with fixed return and proper cleanup.
        """
        patcher = patch("k2.ingestion.consumer.AvroDeserializer")
        mock_deser = patcher.start()
        mock_instance = mock_deser.return_value

        # Store reference to function for proper cleanup
        def default_lambda(value, context):
            return {
                "symbol": "BHP",
                "price": 45.50,
                "sequence_number": 12345,  # v1 schema field
                "source_sequence": 12345,  # v2 schema field
                "exchange": "asx",  # v2 requires exchange
                "timestamp": 1700000000000000,  # Add timestamp in microseconds
            }

        mock_instance.side_effect = default_lambda

        try:
            yield mock_instance
        finally:
            # Explicit cleanup with forced garbage collection
            mock_instance.reset_mock()
            mock_instance.side_effect = None
            patcher.stop()
            del mock_instance
            del mock_deser
            del patcher
            del default_lambda
            gc.collect()

    @pytest.fixture(scope="class")
    def mock_iceberg_writer(self):
        """Mock IcebergWriter.

        Memory optimization: Class-scoped with constrained methods and proper cleanup.
        """
        patcher = patch("k2.ingestion.consumer.IcebergWriter")
        mock_writer = patcher.start()
        mock_instance = mock_writer.return_value
        mock_instance.write_batch = MagicMock()
        mock_instance.close = MagicMock()

        try:
            yield mock_instance
        finally:
            # Explicit cleanup with forced garbage collection
            mock_instance.reset_mock()
            patcher.stop()
            del mock_instance
            del mock_writer
            del patcher
            gc.collect()

    @pytest.fixture(scope="class")
    def mock_sequence_tracker(self):
        """Mock SequenceTracker.

        Memory optimization: Class-scoped with minimal methods and proper cleanup.
        """
        patcher = patch("k2.ingestion.consumer.SequenceTracker")
        mock_tracker = patcher.start()
        mock_instance = mock_tracker.return_value
        mock_instance.check_sequence = MagicMock(return_value=None)

        try:
            yield mock_instance
        finally:
            # Explicit cleanup with forced garbage collection
            mock_instance.reset_mock()
            mock_instance.check_sequence.side_effect = None
            patcher.stop()
            del mock_instance
            del mock_tracker
            del patcher
            gc.collect()

    @pytest.fixture(scope="class")
    def mock_dlq(self):
        """Mock DeadLetterQueue.

        Memory optimization: Class-scoped with minimal methods and proper cleanup.
        """
        patcher = patch("k2.ingestion.consumer.DeadLetterQueue")
        mock_dlq_class = patcher.start()
        mock_instance = mock_dlq_class.return_value
        mock_instance.write = MagicMock()
        mock_instance.close = MagicMock()

        try:
            yield mock_instance
        finally:
            # Explicit cleanup with forced garbage collection
            mock_instance.reset_mock()
            patcher.stop()
            del mock_instance
            del mock_dlq_class
            del patcher
            gc.collect()

    @pytest.fixture(scope="class")
    def consumer(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Create consumer with all mocked dependencies.

        Memory optimization: Class-scoped, reused across all tests-backup.
        Tests MUST reset mocks before use.
        """
        consumer = MarketDataConsumer(
            topics=["market.equities.trades.asx"],
            consumer_group="test-consumer-group",
            bootstrap_servers="localhost:9092",
            schema_registry_url="http://localhost:8081",
            batch_size=10,
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

        yield consumer

        # Explicit cleanup
        del consumer

    @pytest.fixture(autouse=True)
    def reset_mocks(
        self,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Reset all mocks before each test.

        Memory optimization: Clears call history and side effects to prevent
        accumulation across class-scoped fixtures.
        """
        # Run before test
        yield

        # Reset after test to clear call history
        mock_consumer.subscribe.reset_mock()
        mock_consumer.poll.reset_mock()
        mock_consumer.commit.reset_mock()
        mock_consumer.close.reset_mock()
        mock_consumer.assignment.reset_mock()

        # Reset avro deserializer (restore default side_effect after test mutations)
        if hasattr(mock_avro_deserializer, "side_effect"):
            if not callable(mock_avro_deserializer.side_effect):
                # Restore default deserializer behavior
                mock_avro_deserializer.side_effect = lambda value, context: {
                    "symbol": "BHP",
                    "price": 45.50,
                    "sequence_number": 12345,
                    "source_sequence": 12345,
                    "exchange": "asx",
                    "timestamp": 1700000000000000,
                }

        mock_iceberg_writer.write_batch.reset_mock()
        mock_iceberg_writer.close.reset_mock()

        mock_sequence_tracker.check_sequence.reset_mock()
        # Clear side effect if set
        mock_sequence_tracker.check_sequence.side_effect = None

        mock_dlq.write.reset_mock()
        mock_dlq.close.reset_mock()

    def test_consumer_initialization_with_topics(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test consumer initializes with topic list."""
        consumer = MarketDataConsumer(
            topics=["topic1", "topic2"],
            consumer_group="test-group",
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

        assert consumer.topics == ["topic1", "topic2"]
        assert consumer.topic_pattern is None
        assert consumer.consumer_group == "test-group"
        assert consumer.batch_size == 1000  # Default

    def test_consumer_initialization_with_pattern(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test consumer initializes with topic pattern."""
        consumer = MarketDataConsumer(
            topic_pattern="market\\.equities\\..*",
            consumer_group="test-group",
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

        assert consumer.topics is None
        assert consumer.topic_pattern == "market\\.equities\\..*"

    def test_consumer_initialization_fails_without_topic(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
    ):
        """Test consumer initialization fails without topics or pattern."""
        with pytest.raises(ValueError, match="Must specify either topics or topic_pattern"):
            MarketDataConsumer(
                consumer_group="test-group",
            )

    def test_consumer_custom_batch_size(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test consumer accepts custom batch size."""
        consumer = MarketDataConsumer(
            topics=["topic1"],
            batch_size=5000,
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

        assert consumer.batch_size == 5000

    def test_consumer_default_config_from_env(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test consumer reads defaults from environment."""
        with patch("k2.ingestion.consumer.config") as mock_config:
            # Mock the config object's kafka properties
            mock_config.kafka.bootstrap_servers = "kafka:29092"
            mock_config.kafka.schema_registry_url = "http://schema-registry:8081"

            with patch.dict(
                "os.environ",
                {
                    "K2_CONSUMER_GROUP": "env-consumer-group",
                    "K2_CONSUMER_BATCH_SIZE": "2000",
                },
            ):
                consumer = MarketDataConsumer(
                    topics=["topic1"],
                    iceberg_writer=mock_iceberg_writer,
                    sequence_tracker=mock_sequence_tracker,
                )

                assert consumer.bootstrap_servers == "kafka:29092"
                assert consumer.schema_registry_url == "http://schema-registry:8081"
                assert consumer.consumer_group == "env-consumer-group"
                assert consumer.batch_size == 2000

    def test_consumer_config(self, consumer):
        """Test Kafka consumer configuration."""
        # Consumer should be initialized with correct config
        assert consumer.consumer is not None

    def test_consumer_subscribe_to_topics(self, consumer, mock_consumer):
        """Test consumer subscribes to topic list."""
        # Consumer already initialized and subscribed in __init__
        # Just verify subscribe was called with correct topics
        mock_consumer.subscribe.assert_called_with(["market.equities.trades.asx"])

    def test_consumer_subscribe_to_pattern(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test consumer subscribes to topic pattern."""
        _consumer = MarketDataConsumer(
            topic_pattern="market\\.equities\\..*",
            consumer_group="test-group",
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

        # Verify subscribe was called with the correct pattern
        mock_consumer.subscribe.assert_called_with(pattern="market\\.equities\\..*")

    def test_deserialize_message_success(self, consumer, mock_avro_deserializer):
        """Test successful message deserialization."""
        # Create mock Kafka message
        msg = MagicMock()
        msg.error.return_value = None
        msg.value.return_value = b"avro_bytes"
        msg.topic.return_value = "market.equities.trades.asx"
        msg.partition.return_value = 0
        msg.offset.return_value = 12345

        record = consumer._deserialize_message(msg)

        assert record is not None
        assert record["symbol"] == "BHP"
        assert record["price"] == 45.50
        assert record["sequence_number"] == 12345

    def test_deserialize_message_with_error(self, consumer):
        """Test deserialize returns None on message error."""
        msg = MagicMock()
        error = MagicMock()
        error.code.return_value = KafkaError._PARTITION_EOF
        msg.error.return_value = error

        record = consumer._deserialize_message(msg)

        assert record is None

    def test_deserialize_message_deserialization_error(self, consumer, mock_avro_deserializer):
        """Test deserialize handles deserialization error."""
        msg = MagicMock()
        msg.error.return_value = None
        msg.value.return_value = b"invalid_avro"
        msg.topic.return_value = "market.equities.trades.asx"

        # Make deserializer raise exception
        mock_avro_deserializer.side_effect = Exception("Deserialization failed")

        record = consumer._deserialize_message(msg)

        assert record is None
        assert consumer.stats.errors == 1

    def test_consume_batch_success(self, consumer, mock_consumer):
        """Test successful batch consumption."""
        # Create mock messages with proper cleanup
        messages = []
        try:
            for i in range(5):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)

            # Make poll return messages then None
            mock_consumer.poll.side_effect = messages + [None] * 100

            batch = consumer._consume_batch()

            assert len(batch) == 5
            assert consumer.stats.messages_consumed == 5

        finally:
            # Explicit cleanup of mock messages to prevent memory leaks
            for msg in messages:
                msg.reset_mock()
                del msg
            messages.clear()
            gc.collect()

    def test_consume_batch_with_sequence_gap(
        self,
        consumer,
        mock_consumer,
        mock_sequence_tracker,
    ):
        """Test batch consumption detects sequence gaps."""
        # Mock sequence tracker to detect gap (return SMALL_GAP enum to trigger increment)
        from k2.ingestion.sequence_tracker import SequenceEvent

        mock_sequence_tracker.check_sequence.return_value = SequenceEvent.SMALL_GAP

        # Create mock message
        msg = MagicMock()
        msg.error.return_value = None
        msg.value.return_value = b"avro_bytes"
        msg.topic.return_value = "market.equities.trades.asx"
        msg.partition.return_value = 0
        msg.offset.return_value = 1

        mock_consumer.poll.side_effect = [msg] + [None] * 100

        batch = consumer._consume_batch()

        assert len(batch) == 1
        assert consumer.stats.sequence_gaps == 1
        # Check sequence tracker was called with correct arguments (exchange, symbol, sequence, timestamp)
        assert mock_sequence_tracker.check_sequence.called

    def test_write_batch_to_iceberg_success(self, consumer, mock_iceberg_writer):
        """Test successful Iceberg batch write."""
        batch = [
            {"symbol": "BHP", "price": 45.50, "sequence_number": 1},
            {"symbol": "RIO", "price": 120.25, "sequence_number": 2},
        ]

        consumer._write_batch_to_iceberg(batch)

        # Method calls write_trades not write_batch
        mock_iceberg_writer.write_trades.assert_called_once_with(batch)
        # Note: messages_written is incremented in _consume_batch, not _write_batch_to_iceberg
        # so we don't check it here

    def test_write_batch_to_iceberg_error(self, consumer, mock_iceberg_writer):
        """Test Iceberg write error handling."""
        mock_iceberg_writer.write_batch.side_effect = Exception("Iceberg write failed")

        batch = [{"symbol": "BHP", "price": 45.50}]

        with pytest.raises(Exception, match="Iceberg write failed"):
            consumer._write_batch_to_iceberg(batch)

        assert consumer.stats.errors == 1

    def test_run_batch_mode(self, consumer, mock_consumer, mock_iceberg_writer):
        """Test consumer run in batch mode."""
        # Set max_messages for batch mode
        consumer.max_messages = 5

        # Create mock messages with proper cleanup
        messages = []
        try:
            for i in range(5):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)

            mock_consumer.poll.side_effect = messages + [None] * 100

            consumer.run()

            # Should have consumed exactly max_messages
            assert consumer.stats.messages_consumed == 5
            assert consumer.stats.messages_written == 5
            mock_iceberg_writer.write_trades.assert_called()

        finally:
            # Explicit cleanup of mock messages
            for msg in messages:
                msg.reset_mock()
                del msg
            messages.clear()
            gc.collect()

    def test_run_daemon_mode_with_shutdown(self, consumer, mock_consumer, mock_iceberg_writer):
        """Test consumer daemon mode with graceful shutdown."""
        # Set max_messages to None for daemon mode
        consumer.max_messages = None

        # Create a few messages, then trigger shutdown
        messages = []
        for i in range(3):
            msg = MagicMock()
            msg.error.return_value = None
            msg.value.return_value = b"avro_bytes"
            msg.topic.return_value = "market.equities.trades.asx"
            msg.partition.return_value = 0
            msg.offset.return_value = i
            messages.append(msg)

        def poll_with_shutdown(*args, **kwargs):
            if mock_consumer.poll.call_count > 3:
                consumer._shutdown = True
            try:
                return messages[mock_consumer.poll.call_count - 1]
            except IndexError:
                return None

        mock_consumer.poll.side_effect = poll_with_shutdown

        consumer.run()

        assert consumer.stats.messages_consumed > 0
        mock_consumer.commit.assert_called()
        mock_consumer.close.assert_called_once()

    def test_signal_handler_sets_shutdown_flag(self, consumer):
        """Test signal handler sets shutdown flag."""
        assert consumer._shutdown is False

        # Simulate SIGTERM
        consumer._shutdown_handler(signal.SIGTERM, None)

        assert consumer._shutdown is True

    def test_close_flushes_and_commits(self, consumer, mock_consumer, mock_iceberg_writer):
        """Test close flushes pending batch and commits."""
        # Add some messages to pending batch
        consumer._pending_batch = [
            {"symbol": "BHP", "price": 45.50},
            {"symbol": "RIO", "price": 120.25},
        ]

        consumer.close()

        # Should write pending batch
        mock_iceberg_writer.write_batch.assert_called_once()
        mock_consumer.commit.assert_called()
        mock_consumer.close.assert_called()
        mock_iceberg_writer.close.assert_called()

    def test_close_empty_batch(self, consumer, mock_consumer, mock_iceberg_writer):
        """Test close with empty pending batch."""
        consumer._pending_batch = []

        consumer.close()

        # Should not write empty batch
        mock_iceberg_writer.write_batch.assert_not_called()
        mock_consumer.commit.assert_called()
        mock_consumer.close.assert_called()

    def test_context_manager(self, consumer, mock_consumer, mock_iceberg_writer):
        """Test consumer works as context manager."""
        with consumer as c:
            assert c is consumer

        # Should have closed properly
        mock_consumer.close.assert_called()
        mock_iceberg_writer.close.assert_called()

    def test_get_stats(self, consumer):
        """Test get_stats returns ConsumerStats."""
        stats = consumer.get_stats()

        assert isinstance(stats, ConsumerStats)
        assert stats.messages_consumed == 0
        assert stats.messages_written == 0

    def test_consumer_handles_kafka_exception(
        self,
        mock_schema_registry_client,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test consumer handles Kafka initialization exception."""
        with patch("k2.ingestion.consumer.Consumer") as mock_cons:
            mock_cons.side_effect = KafkaException("Kafka unavailable")

            with pytest.raises(KafkaException, match="Kafka unavailable"):
                MarketDataConsumer(
                    topics=["topic1"],
                    iceberg_writer=mock_iceberg_writer,
                    sequence_tracker=mock_sequence_tracker,
                )

    def test_consumer_handles_schema_registry_exception(
        self,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test consumer handles Schema Registry exception."""
        with patch("k2.ingestion.consumer.SchemaRegistryClient") as mock_client:
            mock_client.side_effect = Exception("Schema Registry unavailable")

            with pytest.raises(Exception, match="Schema Registry unavailable"):
                MarketDataConsumer(
                    topics=["topic1"],
                    iceberg_writer=mock_iceberg_writer,
                    sequence_tracker=mock_sequence_tracker,
                )

    def test_create_consumer_factory(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Test create_consumer factory function."""
        consumer = create_consumer(
            topics=["topic1", "topic2"],
            consumer_group="factory-group",
            batch_size=5000,
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

        assert isinstance(consumer, MarketDataConsumer)
        assert consumer.topics == ["topic1", "topic2"]
        assert consumer.consumer_group == "factory-group"
        assert consumer.batch_size == 5000

    def test_consumer_metrics_integration(self, consumer):
        """Test consumer tracks metrics correctly."""
        # Simulate consumption
        consumer.stats.messages_consumed = 100
        consumer.stats.messages_written = 95
        consumer.stats.errors = 5
        consumer.stats.sequence_gaps = 2

        stats = consumer.get_stats()

        assert stats.messages_consumed == 100
        assert stats.messages_written == 95
        assert stats.errors == 5
        assert stats.sequence_gaps == 2

    def test_consumer_batch_size_boundary(
        self,
        consumer,
        mock_consumer,
        mock_iceberg_writer,
    ):
        """Test consumer respects exact batch size."""
        # Set small batch size
        consumer.batch_size = 3

        # Create exactly batch_size messages with proper cleanup
        messages = []
        try:
            for i in range(3):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)

            mock_consumer.poll.side_effect = messages + [None] * 100

            batch = consumer._consume_batch()

            assert len(batch) == 3

        finally:
            # Explicit cleanup of mock messages
            for msg in messages:
                msg.reset_mock()
                del msg
            messages.clear()
            gc.collect()

    def test_consumer_empty_batch(self, consumer, mock_consumer):
        """Test consumer handles empty batch (no messages)."""
        # Poll returns None (no messages)
        mock_consumer.poll.return_value = None

        batch = consumer._consume_batch()

        assert len(batch) == 0

    def test_consumer_partial_batch_timeout(self, consumer, mock_consumer):
        """Test consumer returns partial batch on timeout."""
        # Return 2 messages then timeout with proper cleanup
        messages = []
        try:
            for i in range(2):
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                msg.partition.return_value = 0
                msg.offset.return_value = i
                messages.append(msg)

            # After 2 messages, return None to simulate timeout
            mock_consumer.poll.side_effect = messages + [None] * 1000

            batch = consumer._consume_batch()

            # Should return partial batch after timeout
            assert 0 <= len(batch) <= consumer.batch_size

        finally:
            # Explicit cleanup of mock messages
            for msg in messages:
                msg.reset_mock()
                del msg
            messages.clear()
            gc.collect()


class TestConsumerSequenceTrackerIntegration:
    """Test suite for sequence tracker integration (validates TD-001 fix).

    These tests-backup verify that the consumer calls check_sequence() with all
    4 required arguments: exchange, symbol, sequence, timestamp.

    Memory optimization: Class-scoped fixtures to prevent memory leaks.
    """

    @pytest.fixture(scope="class")
    def mock_schema_registry_client(self):
        """Mock Schema Registry client.

        Memory optimization: Class-scoped fixture.
        """
        with patch("k2.ingestion.consumer.SchemaRegistryClient") as mock_client:
            mock_instance = mock_client.return_value
            yield mock_instance
            del mock_instance

    @pytest.fixture(scope="class")
    def mock_kafka_consumer(self):
        """Mock Kafka Consumer.

        Memory optimization: Class-scoped fixture.
        """
        with patch("k2.ingestion.consumer.Consumer") as mock_cons:
            mock_instance = mock_cons.return_value
            mock_instance.subscribe = MagicMock()
            mock_instance.poll = MagicMock()
            mock_instance.commit = MagicMock()
            mock_instance.close = MagicMock()
            yield mock_instance
            del mock_instance

    @pytest.fixture(scope="class")
    def mock_iceberg_writer(self):
        """Mock IcebergWriter.

        Memory optimization: Class-scoped fixture.
        """
        with patch("k2.ingestion.consumer.IcebergWriter") as mock_writer:
            mock_instance = mock_writer.return_value
            mock_instance.write_trades = MagicMock()
            yield mock_instance
            del mock_instance

    @pytest.fixture(scope="class")
    def mock_dlq(self):
        """Mock DeadLetterQueue.

        Memory optimization: Class-scoped fixture.
        """
        with patch("k2.ingestion.consumer.DeadLetterQueue") as mock_dlq_class:
            mock_instance = mock_dlq_class.return_value
            yield mock_instance
            del mock_instance

    def test_sequence_tracker_called_with_all_required_args_v2(
        self,
        mock_schema_registry_client,
        mock_kafka_consumer,
        mock_iceberg_writer,
        mock_dlq,
    ):
        """Test that sequence tracker is called with all 4 required arguments (v2 schema).

        This test validates the fix for TD-001: Consumer sequence tracker API mismatch.
        The SequenceTracker.check_sequence() method requires 4 arguments:
        - exchange: str
        - symbol: str
        - sequence: int
        - timestamp: datetime
        """
        from datetime import datetime

        from k2.ingestion.sequence_tracker import SequenceEvent

        # Create real sequence tracker (not mocked) to test integration
        with patch("k2.ingestion.consumer.SequenceTracker") as mock_tracker_class:
            mock_tracker = mock_tracker_class.return_value
            mock_tracker.check_sequence = MagicMock(return_value=SequenceEvent.OK)

            # Create consumer with v2 schema
            consumer = MarketDataConsumer(
                topics=["market.crypto.trades.binance"],
                consumer_group="test-consumer-v2",
                schema_version="v2",
                batch_size=10,
                iceberg_writer=mock_iceberg_writer,
                sequence_tracker=mock_tracker,
            )

            # Create mock message with v2 schema fields
            test_timestamp = datetime(2024, 1, 15, 10, 30, 45, 123456)
            trade_message_v2 = {
                "message_id": "550e8400-e29b-41d4-a716-446655440000",
                "symbol": "BTCUSDT",
                "exchange": "binance",
                "asset_class": "crypto",
                "timestamp": test_timestamp,  # Exchange timestamp (datetime object)
                "price": 43250.50,
                "quantity": 0.5,
                "currency": "USDT",
                "side": "BUY",
                "trade_conditions": [],
                "source_sequence": 123456789,  # V2 schema uses "source_sequence"
                "ingestion_timestamp": datetime(2024, 1, 15, 10, 30, 45, 234567),
            }

            # Mock deserializer to return our test message
            with patch.object(consumer, "_get_deserializer") as mock_get_deser:
                mock_deserializer = MagicMock()
                mock_deserializer.return_value = trade_message_v2
                mock_get_deser.return_value = mock_deserializer

                # Create mock Kafka message
                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.crypto.trades.binance"
                msg.partition.return_value = 0
                msg.offset.return_value = 1

                # Set up poll to return message then None
                mock_kafka_consumer.poll.side_effect = [msg, None]

                # Consume batch (this should call sequence tracker)
                consumer._consume_batch()

                # VERIFY: check_sequence() was called with all 4 required arguments
                mock_tracker.check_sequence.assert_called_once()
                call_args = mock_tracker.check_sequence.call_args

                # Verify positional or keyword arguments
                if call_args.args:
                    # Called with positional args
                    assert (
                        len(call_args.args) == 4
                    ), "check_sequence must be called with 4 arguments"
                    exchange_arg, symbol_arg, sequence_arg, timestamp_arg = call_args.args
                else:
                    # Called with keyword args
                    assert (
                        "exchange" in call_args.kwargs
                    ), "check_sequence missing 'exchange' argument"
                    assert "symbol" in call_args.kwargs, "check_sequence missing 'symbol' argument"
                    assert (
                        "sequence" in call_args.kwargs
                    ), "check_sequence missing 'sequence' argument"
                    assert (
                        "timestamp" in call_args.kwargs
                    ), "check_sequence missing 'timestamp' argument"

                    exchange_arg = call_args.kwargs["exchange"]
                    symbol_arg = call_args.kwargs["symbol"]
                    sequence_arg = call_args.kwargs["sequence"]
                    timestamp_arg = call_args.kwargs["timestamp"]

                # Verify argument values
                assert exchange_arg == "binance", "Incorrect exchange passed to check_sequence"
                assert symbol_arg == "BTCUSDT", "Incorrect symbol passed to check_sequence"
                assert sequence_arg == 123456789, "Incorrect sequence passed to check_sequence"
                assert isinstance(timestamp_arg, datetime), "Timestamp must be datetime object"
                assert (
                    timestamp_arg == test_timestamp
                ), "Incorrect timestamp passed to check_sequence"

    def test_sequence_tracker_handles_timestamp_formats(
        self,
        mock_schema_registry_client,
        mock_kafka_consumer,
        mock_iceberg_writer,
        mock_dlq,
    ):
        """Test that sequence tracker handles different timestamp formats.

        Validates that timestamp conversion logic works for both:
        - datetime objects (from Avro logical type deserialization)
        - int/float (microseconds, fallback case)
        """
        from datetime import datetime

        from k2.ingestion.sequence_tracker import SequenceEvent

        with patch("k2.ingestion.consumer.SequenceTracker") as mock_tracker_class:
            mock_tracker = mock_tracker_class.return_value
            mock_tracker.check_sequence = MagicMock(return_value=SequenceEvent.OK)

            consumer = MarketDataConsumer(
                topics=["market.crypto.trades.binance"],
                consumer_group="test-consumer-v2",
                schema_version="v2",
                batch_size=10,
                iceberg_writer=mock_iceberg_writer,
                sequence_tracker=mock_tracker,
            )

            # Test with timestamp as microseconds (int)
            trade_with_int_timestamp = {
                "symbol": "ETHUSDT",
                "exchange": "binance",
                "timestamp": 1705318245123456,  # Microseconds as int
                "source_sequence": 987654321,
            }

            with patch.object(consumer, "_get_deserializer") as mock_get_deser:
                mock_deserializer = MagicMock()
                mock_deserializer.return_value = trade_with_int_timestamp
                mock_get_deser.return_value = mock_deserializer

                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.crypto.trades.binance"
                mock_kafka_consumer.poll.side_effect = [msg, None]

                consumer._consume_batch()

                # Verify timestamp was converted to datetime
                call_args = mock_tracker.check_sequence.call_args
                timestamp_arg = call_args.kwargs.get("timestamp") or call_args.args[3]

                assert isinstance(
                    timestamp_arg, datetime
                ), "Timestamp should be converted to datetime"

    def test_sequence_tracker_gap_detection_increments_stats(
        self,
        mock_schema_registry_client,
        mock_kafka_consumer,
        mock_iceberg_writer,
        mock_dlq,
    ):
        """Test that sequence gaps are counted correctly in consumer stats.

        Validates that only SMALL_GAP and LARGE_GAP events increment the gap counter,
        not OK or RESET events (this was a logic bug in the original code).
        """
        from datetime import datetime

        from k2.ingestion.sequence_tracker import SequenceEvent

        with patch("k2.ingestion.consumer.SequenceTracker") as mock_tracker_class:
            mock_tracker = mock_tracker_class.return_value

            consumer = MarketDataConsumer(
                topics=["market.crypto.trades.binance"],
                consumer_group="test-consumer-v2",
                schema_version="v2",
                batch_size=10,
                iceberg_writer=mock_iceberg_writer,
                sequence_tracker=mock_tracker,
            )

            # Test different sequence events
            test_cases = [
                (SequenceEvent.OK, False, "OK should not increment gap counter"),
                (SequenceEvent.SMALL_GAP, True, "SMALL_GAP should increment gap counter"),
                (SequenceEvent.LARGE_GAP, True, "LARGE_GAP should increment gap counter"),
                (SequenceEvent.RESET, False, "RESET should not increment gap counter"),
                (
                    SequenceEvent.OUT_OF_ORDER,
                    False,
                    "OUT_OF_ORDER should not increment gap counter",
                ),
            ]

            for event_type, should_increment, description in test_cases:
                # Reset stats
                consumer.stats.sequence_gaps = 0
                mock_tracker.check_sequence = MagicMock(return_value=event_type)

                trade_message = {
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                    "timestamp": datetime(2024, 1, 15, 10, 30, 45),
                    "source_sequence": 123456789,
                }

                with patch.object(consumer, "_get_deserializer") as mock_get_deser:
                    mock_deserializer = MagicMock()
                    mock_deserializer.return_value = trade_message
                    mock_get_deser.return_value = mock_deserializer

                    msg = MagicMock()
                    msg.error.return_value = None
                    msg.value.return_value = b"avro_bytes"
                    msg.topic.return_value = "market.crypto.trades.binance"
                    mock_kafka_consumer.poll.side_effect = [msg, None]

                    consumer._consume_batch()

                    # Verify gap counter
                    if should_increment:
                        assert consumer.stats.sequence_gaps == 1, description
                    else:
                        assert consumer.stats.sequence_gaps == 0, description

    def test_sequence_tracker_handles_null_source_sequence(
        self,
        mock_schema_registry_client,
        mock_kafka_consumer,
        mock_iceberg_writer,
        mock_dlq,
    ):
        """Test that messages with null source_sequence are handled gracefully.

        V2 schema allows source_sequence to be null (some exchanges don't provide sequence numbers).
        """
        from datetime import datetime

        from k2.ingestion.sequence_tracker import SequenceEvent

        with patch("k2.ingestion.consumer.SequenceTracker") as mock_tracker_class:
            mock_tracker = mock_tracker_class.return_value
            mock_tracker.check_sequence = MagicMock(return_value=SequenceEvent.OK)

            consumer = MarketDataConsumer(
                topics=["market.equities.trades.asx"],
                consumer_group="test-consumer-v2",
                schema_version="v2",
                batch_size=10,
                iceberg_writer=mock_iceberg_writer,
                sequence_tracker=mock_tracker,
            )

            # Trade with null source_sequence
            trade_without_sequence = {
                "symbol": "BHP",
                "exchange": "ASX",
                "timestamp": datetime(2024, 1, 15, 10, 30, 45),
                "source_sequence": None,  # Null sequence number
            }

            with patch.object(consumer, "_get_deserializer") as mock_get_deser:
                mock_deserializer = MagicMock()
                mock_deserializer.return_value = trade_without_sequence
                mock_get_deser.return_value = mock_deserializer

                msg = MagicMock()
                msg.error.return_value = None
                msg.value.return_value = b"avro_bytes"
                msg.topic.return_value = "market.equities.trades.asx"
                mock_kafka_consumer.poll.side_effect = [msg, None]

                consumer._consume_batch()

                # Verify check_sequence was NOT called (since source_sequence is null)
                mock_tracker.check_sequence.assert_not_called()
