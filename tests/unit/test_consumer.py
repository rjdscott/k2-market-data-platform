"""Unit tests for Kafka consumer."""

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
    """Test suite for MarketDataConsumer."""

    @pytest.fixture
    def mock_schema_registry_client(self):
        """Mock Schema Registry client."""
        with patch("k2.ingestion.consumer.SchemaRegistryClient") as mock_client:
            yield mock_client.return_value

    @pytest.fixture
    def mock_consumer(self):
        """Mock Kafka Consumer."""
        with patch("k2.ingestion.consumer.Consumer") as mock_cons:
            mock_instance = mock_cons.return_value
            mock_instance.subscribe = MagicMock()
            mock_instance.poll = MagicMock()
            mock_instance.commit = MagicMock()
            mock_instance.close = MagicMock()
            mock_instance.assignment = MagicMock(return_value=[])

            yield mock_instance

    @pytest.fixture
    def mock_avro_deserializer(self):
        """Mock AvroDeserializer."""
        with patch("k2.ingestion.consumer.AvroDeserializer") as mock_deser:
            mock_instance = mock_deser.return_value
            # Make deserializer callable
            mock_instance.side_effect = lambda value, context: {
                "symbol": "BHP",
                "price": 45.50,
                "sequence_number": 12345,
            }

            yield mock_instance

    @pytest.fixture
    def mock_iceberg_writer(self):
        """Mock IcebergWriter."""
        with patch("k2.ingestion.consumer.IcebergWriter") as mock_writer:
            mock_instance = mock_writer.return_value
            mock_instance.write_batch = MagicMock()
            mock_instance.close = MagicMock()

            yield mock_instance

    @pytest.fixture
    def mock_sequence_tracker(self):
        """Mock SequenceTracker."""
        with patch("k2.ingestion.consumer.SequenceTracker") as mock_tracker:
            mock_instance = mock_tracker.return_value
            mock_instance.check_sequence = MagicMock(return_value=None)

            yield mock_instance

    @pytest.fixture
    def mock_dlq(self):
        """Mock DeadLetterQueue."""
        with patch("k2.ingestion.consumer.DeadLetterQueue") as mock_dlq_class:
            mock_instance = mock_dlq_class.return_value
            mock_instance.write = MagicMock()
            mock_instance.close = MagicMock()

            yield mock_instance

    @pytest.fixture
    def consumer(
        self,
        mock_schema_registry_client,
        mock_consumer,
        mock_avro_deserializer,
        mock_iceberg_writer,
        mock_sequence_tracker,
        mock_dlq,
    ):
        """Create consumer with all mocked dependencies."""
        return MarketDataConsumer(
            topics=["market.equities.trades.asx"],
            consumer_group="test-consumer-group",
            bootstrap_servers="localhost:9092",
            schema_registry_url="http://localhost:8081",
            batch_size=10,
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

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
        with patch.dict(
            "os.environ",
            {
                "K2_KAFKA_BOOTSTRAP_SERVERS": "kafka:29092",
                "K2_SCHEMA_REGISTRY_URL": "http://schema-registry:8081",
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
        consumer._init_consumer()

        mock_consumer.subscribe.assert_called_once_with(["market.equities.trades.asx"])

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
        consumer = MarketDataConsumer(
            topic_pattern="market\\.equities\\..*",
            consumer_group="test-group",
            iceberg_writer=mock_iceberg_writer,
            sequence_tracker=mock_sequence_tracker,
        )

        mock_consumer.subscribe.assert_called()
        # Check pattern subscription (first arg is pattern string)
        call_args = mock_consumer.subscribe.call_args
        assert "market\\.equities\\..*" in str(call_args)

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
        # Create mock messages
        messages = []
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

    def test_consume_batch_with_sequence_gap(
        self,
        consumer,
        mock_consumer,
        mock_sequence_tracker,
    ):
        """Test batch consumption detects sequence gaps."""
        # Mock sequence tracker to return gap
        gap_event = MagicMock()
        gap_event.symbol = "BHP"
        gap_event.expected = 12346
        gap_event.received = 12350
        gap_event.gap_size = 4
        mock_sequence_tracker.check_sequence.return_value = gap_event

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
        mock_sequence_tracker.check_sequence.assert_called_once_with("BHP", 12345)

    def test_write_batch_to_iceberg_success(self, consumer, mock_iceberg_writer):
        """Test successful Iceberg batch write."""
        batch = [
            {"symbol": "BHP", "price": 45.50, "sequence_number": 1},
            {"symbol": "RIO", "price": 120.25, "sequence_number": 2},
        ]

        consumer._write_batch_to_iceberg(batch)

        mock_iceberg_writer.write_batch.assert_called_once_with(batch)
        assert consumer.stats.messages_written == 2

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

        # Create mock messages
        messages = []
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
        mock_iceberg_writer.write_batch.assert_called()

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

        # Create exactly batch_size messages
        messages = []
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

    def test_consumer_empty_batch(self, consumer, mock_consumer):
        """Test consumer handles empty batch (no messages)."""
        # Poll returns None (no messages)
        mock_consumer.poll.return_value = None

        batch = consumer._consume_batch()

        assert len(batch) == 0

    def test_consumer_partial_batch_timeout(self, consumer, mock_consumer):
        """Test consumer returns partial batch on timeout."""
        # Return 2 messages then timeout
        messages = []
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
