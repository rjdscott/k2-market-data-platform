"""Unit tests-backup for Kafka producer."""

from unittest.mock import MagicMock, patch

import pytest
from confluent_kafka import KafkaException
from confluent_kafka.schema_registry.error import SchemaRegistryError

from k2.ingestion.producer import MarketDataProducer, create_producer


class TestMarketDataProducer:
    """Test suite for MarketDataProducer."""

    @pytest.fixture
    def mock_schema_registry_client(self):
        """Mock Schema Registry client."""
        with patch("k2.ingestion.producer.SchemaRegistryClient") as mock_client:
            # Mock get_latest_version to return schema
            mock_schema = MagicMock()
            mock_schema.schema.schema_str = '{"type": "record", "name": "Trade", "fields": []}'
            mock_schema.schema_id = 1
            mock_schema.version = 1

            mock_instance = mock_client.return_value
            mock_instance.get_latest_version.return_value = mock_schema

            yield mock_instance

    @pytest.fixture
    def mock_producer(self):
        """Mock Kafka Producer."""
        with patch("k2.ingestion.producer.Producer") as mock_prod:
            mock_instance = mock_prod.return_value
            mock_instance.produce = MagicMock()
            mock_instance.poll = MagicMock()
            mock_instance.flush = MagicMock(return_value=0)

            yield mock_instance

    @pytest.fixture
    def mock_avro_serializer(self):
        """Mock AvroSerializer."""
        with patch("k2.ingestion.producer.AvroSerializer") as mock_ser:
            mock_instance = mock_ser.return_value
            # Make serializer callable and return bytes
            mock_instance.side_effect = lambda value, context: b"avro_bytes"

            yield mock_instance

    @pytest.fixture
    def producer(self, mock_schema_registry_client, mock_producer, mock_avro_serializer):
        """Create producer with mocked dependencies."""
        return MarketDataProducer(
            bootstrap_servers="localhost:9092",
            schema_registry_url="http://localhost:8081",
        )

    def test_producer_initialization(self, producer):
        """Test producer initializes with correct configuration."""
        assert producer.bootstrap_servers == "localhost:9092"
        assert producer.schema_registry_url == "http://localhost:8081"
        assert producer.max_retries == 3
        assert producer.initial_retry_delay == 0.1
        assert producer.retry_backoff_factor == 2.0
        assert producer._total_produced == 0
        assert producer._total_errors == 0

    def test_producer_with_custom_retry_config(
        self,
        mock_schema_registry_client,
        mock_producer,
        mock_avro_serializer,
    ):
        """Test producer accepts custom retry configuration."""
        producer = MarketDataProducer(
            max_retries=5,
            initial_retry_delay=0.5,
            retry_backoff_factor=3.0,
            max_retry_delay=30.0,
        )

        assert producer.max_retries == 5
        assert producer.initial_retry_delay == 0.5
        assert producer.retry_backoff_factor == 3.0
        assert producer.max_retry_delay == 30.0

    def test_produce_trade_success(self, producer, mock_producer):
        """Test successful trade production."""
        record = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            "quantity": 1000,
            "side": "buy",
            "sequence_number": 12345,
        }

        producer.produce_trade(
            asset_class="equities",
            exchange="asx",
            record=record,
        )

        # Verify producer.produce was called
        mock_producer.produce.assert_called_once()

        # Extract call arguments
        call_args = mock_producer.produce.call_args
        assert call_args.kwargs["topic"] == "market.equities.trades.asx"
        assert call_args.kwargs["key"] == b"BHP"  # Partition key
        assert call_args.kwargs["value"] == b"avro_bytes"
        assert call_args.kwargs["on_delivery"] is not None

    def test_produce_quote_success(self, producer, mock_producer):
        """Test successful quote production."""
        record = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "bid_price": 45.40,
            "ask_price": 45.50,
            "bid_size": 1000,
            "ask_size": 2000,
            "sequence_number": 12346,
        }

        producer.produce_quote(
            asset_class="equities",
            exchange="asx",
            record=record,
        )

        # Verify producer.produce was called
        mock_producer.produce.assert_called_once()
        call_args = mock_producer.produce.call_args
        assert call_args.kwargs["topic"] == "market.equities.quotes.asx"
        assert call_args.kwargs["key"] == b"BHP"

    def test_produce_reference_data_success(self, producer, mock_producer):
        """Test successful reference data production."""
        record = {
            "company_id": "BHP",  # Partition key for reference data
            "symbol": "BHP",
            "company_name": "BHP Group Limited",
            "sector": "Materials",
            "market_cap": 150000000000,
            "last_updated": "2026-01-10T00:00:00Z",
        }

        producer.produce_reference_data(
            asset_class="equities",
            exchange="asx",
            record=record,
        )

        # Verify producer.produce was called
        mock_producer.produce.assert_called_once()
        call_args = mock_producer.produce.call_args
        assert call_args.kwargs["topic"] == "market.equities.reference_data.asx"
        assert call_args.kwargs["key"] == b"BHP"  # company_id is partition key

    def test_produce_missing_partition_key(self, producer):
        """Test production fails when partition key (symbol) is missing."""
        record = {
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            # Missing 'symbol' field
        }

        with pytest.raises(ValueError, match="Record missing partition key field 'symbol'"):
            producer.produce_trade(
                asset_class="equities",
                exchange="asx",
                record=record,
            )

    def test_produce_invalid_exchange(self, producer):
        """Test production fails with invalid exchange."""
        record = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            "quantity": 1000,
            "side": "buy",
            "sequence_number": 12345,
        }

        with pytest.raises(ValueError, match="Unknown exchange"):
            producer.produce_trade(
                asset_class="equities",
                exchange="invalid_exchange",
                record=record,
            )

    def test_produce_invalid_asset_class(self, producer):
        """Test production fails with invalid asset class."""
        record = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            "quantity": 1000,
            "side": "buy",
            "sequence_number": 12345,
        }

        with pytest.raises(ValueError, match="Unknown asset class"):
            producer.produce_trade(
                asset_class="invalid_asset",
                exchange="asx",
                record=record,
            )

    def test_serializer_caching(self, producer, mock_avro_serializer):
        """Test Avro serializers are cached per subject."""
        record1 = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            "quantity": 1000,
            "side": "buy",
            "sequence_number": 12345,
        }

        # Produce two messages to same subject
        producer.produce_trade("equities", "asx", record1)
        producer.produce_trade("equities", "asx", record1)

        # Serializer should be created only once
        with patch("k2.ingestion.producer.AvroSerializer") as mock_ser:
            # Produce third message - should use cached serializer
            producer.produce_trade("equities", "asx", record1)

            # AvroSerializer constructor should not be called again
            mock_ser.assert_not_called()

    def test_delivery_callback_success(self, producer):
        """Test delivery callback on successful delivery."""
        # Mock message
        msg = MagicMock()
        msg.partition.return_value = 0
        msg.offset.return_value = 12345

        context = {
            "topic": "market.equities.trades.asx",
            "partition_key": "BHP",
            "labels": {
                "exchange": "asx",
                "asset_class": "equities",
                "data_type": "trades",
                "topic": "market.equities.trades.asx",
            },
        }

        # Call callback with no error
        producer._delivery_callback(None, msg, context)

        assert producer._total_produced == 1
        assert producer._total_errors == 0

    def test_delivery_callback_error(self, producer):
        """Test delivery callback on delivery error."""
        msg = MagicMock()

        context = {
            "topic": "market.equities.trades.asx",
            "partition_key": "BHP",
            "labels": {
                "exchange": "asx",
                "asset_class": "equities",
                "data_type": "trades",
                "topic": "market.equities.trades.asx",
            },
        }

        # Call callback with error
        error = KafkaException("Delivery failed")
        producer._delivery_callback(error, msg, context)

        assert producer._total_produced == 0
        assert producer._total_errors == 1

    def test_produce_with_retry_buffer_error(self, producer, mock_producer):
        """Test retry logic handles BufferError."""
        # First call raises BufferError, second succeeds
        mock_producer.produce.side_effect = [BufferError("Queue full"), None]

        record = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            "quantity": 1000,
            "side": "buy",
            "sequence_number": 12345,
        }

        producer.produce_trade("equities", "asx", record)

        # Should have retried once
        assert producer._total_retries == 1
        assert mock_producer.produce.call_count == 2

    def test_produce_with_retry_max_retries_exceeded(self, producer, mock_producer):
        """Test production fails after max retries."""
        # Always raise BufferError
        mock_producer.produce.side_effect = BufferError("Queue full")

        record = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            "quantity": 1000,
            "side": "buy",
            "sequence_number": 12345,
        }

        with pytest.raises(BufferError):
            producer.produce_trade("equities", "asx", record)

        # Should have tried max_retries + 1 times (initial + 3 retries = 4 total)
        assert mock_producer.produce.call_count == 4
        assert producer._total_retries == 3

    def test_flush(self, producer, mock_producer):
        """Test flush waits for pending messages."""
        mock_producer.flush.return_value = 0

        remaining = producer.flush(timeout=30.0)

        assert remaining == 0
        mock_producer.flush.assert_called_once_with(30.0)

    def test_close(self, producer, mock_producer):
        """Test close flushes and cleans up."""
        mock_producer.flush.return_value = 0

        producer.close()

        mock_producer.flush.assert_called_once()

    def test_get_stats(self, producer):
        """Test get_stats returns correct statistics."""
        # Simulate some activity
        producer._total_produced = 100
        producer._total_errors = 5
        producer._total_retries = 10

        stats = producer.get_stats()

        assert stats["produced"] == 100
        assert stats["errors"] == 5
        assert stats["retries"] == 10

    def test_create_producer_factory(
        self,
        mock_schema_registry_client,
        mock_producer,
        mock_avro_serializer,
    ):
        """Test create_producer factory function."""
        producer = create_producer(
            bootstrap_servers="kafka:29092",
            max_retries=5,
        )

        assert isinstance(producer, MarketDataProducer)
        assert producer.bootstrap_servers == "kafka:29092"
        assert producer.max_retries == 5

    def test_schema_registry_initialization_failure(self, mock_producer, mock_avro_serializer):
        """Test producer handles Schema Registry initialization failure."""
        with patch("k2.ingestion.producer.SchemaRegistryClient") as mock_client:
            mock_client.side_effect = Exception("Schema Registry unavailable")

            with pytest.raises(Exception, match="Schema Registry unavailable"):
                MarketDataProducer()

    def test_kafka_producer_initialization_failure(
        self,
        mock_schema_registry_client,
        mock_avro_serializer,
    ):
        """Test producer handles Kafka initialization failure."""
        with patch("k2.ingestion.producer.Producer") as mock_prod:
            mock_prod.side_effect = KafkaException("Kafka unavailable")

            with pytest.raises(KafkaException, match="Kafka unavailable"):
                MarketDataProducer()

    def test_serializer_creation_failure(self, producer, mock_schema_registry_client):
        """Test producer handles serializer creation failure."""
        # Mock get_latest_version to raise error
        mock_schema_registry_client.get_latest_version.side_effect = SchemaRegistryError(
            http_status_code=404,
            error_code=40401,
            error_message="Schema not found",
        )

        record = {
            "symbol": "BHP",
            "exchange_timestamp": "2026-01-10T10:30:00Z",
            "price": 45.50,
            "quantity": 1000,
            "side": "buy",
            "sequence_number": 12345,
        }

        with pytest.raises(SchemaRegistryError):
            producer.produce_trade("equities", "asx", record)

    def test_multiple_asset_classes_and_exchanges(self, producer, mock_producer):
        """Test producing to multiple asset classes and exchanges."""
        # Produce to equities/asx
        producer.produce_trade(
            "equities",
            "asx",
            {
                "symbol": "BHP",
                "price": 45.50,
                "quantity": 1000,
                "side": "buy",
                "sequence_number": 1,
                "exchange_timestamp": "2026-01-10T10:30:00Z",
            },
        )

        # Produce to crypto/binance
        producer.produce_trade(
            "crypto",
            "binance",
            {
                "symbol": "BTCUSDT",
                "price": 50000.0,
                "quantity": 0.1,
                "side": "buy",
                "sequence_number": 2,
                "exchange_timestamp": "2026-01-10T10:30:00Z",
            },
        )

        # Verify different topics used
        calls = mock_producer.produce.call_args_list
        assert calls[0].kwargs["topic"] == "market.equities.trades.asx"
        assert calls[1].kwargs["topic"] == "market.crypto.trades.binance"
        assert calls[0].kwargs["key"] == b"BHP"
        assert calls[1].kwargs["key"] == b"BTCUSDT"


class TestBoundedCache:
    """Test BoundedCache with LRU eviction."""

    def test_cache_initialization(self):
        """Test that cache initializes with correct parameters."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=5, component_type="test")

        assert cache.size() == 0
        assert cache._max_size == 5
        assert cache._component_type == "test"

    def test_cache_get_miss(self):
        """Test that cache returns None for missing keys."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=10)

        result = cache.get("nonexistent")
        assert result is None

    def test_cache_set_and_get(self):
        """Test basic cache set and get operations."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=10)

        # Set values
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        # Get values
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"
        assert cache.size() == 2

    def test_cache_lru_eviction(self):
        """Test that LRU eviction works correctly when cache exceeds max size."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=3)

        # Fill cache to max capacity
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        assert cache.size() == 3
        assert "key1" in cache
        assert "key2" in cache
        assert "key3" in cache

        # Add one more - should evict key1 (least recently used)
        cache.set("key4", "value4")

        assert cache.size() == 3
        assert "key1" not in cache  # Evicted
        assert "key2" in cache
        assert "key3" in cache
        assert "key4" in cache

    def test_cache_lru_ordering_on_get(self):
        """Test that getting a key marks it as recently used."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=3)

        # Fill cache
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        # Access key1 (mark as recently used)
        cache.get("key1")

        # Add key4 - should evict key2 (now the LRU)
        cache.set("key4", "value4")

        assert "key1" in cache  # Not evicted (accessed recently)
        assert "key2" not in cache  # Evicted (LRU)
        assert "key3" in cache
        assert "key4" in cache

    def test_cache_update_existing_key(self):
        """Test that updating existing key doesn't cause eviction."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=2)

        cache.set("key1", "value1")
        cache.set("key2", "value2")

        # Update key1
        cache.set("key1", "value1_updated")

        assert cache.size() == 2
        assert cache.get("key1") == "value1_updated"
        assert "key2" in cache

    def test_cache_eviction_with_15_serializers(self):
        """Test creating 15 serializers with max_size=10 evicts 5."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=10)

        # Create 15 entries
        for i in range(15):
            cache.set(f"schema_{i}", f"serializer_{i}")

        # Should have exactly 10 entries (5 evicted)
        assert cache.size() == 10

        # First 5 should be evicted (0-4)
        for i in range(5):
            assert f"schema_{i}" not in cache

        # Last 10 should remain (5-14)
        for i in range(5, 15):
            assert f"schema_{i}" in cache

    def test_cache_contains_operator(self):
        """Test __contains__ operator works correctly."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache(max_size=10)

        cache.set("exists", "value")

        assert "exists" in cache
        assert "not_exists" not in cache

    def test_cache_default_parameters(self):
        """Test that cache uses correct default parameters."""
        from k2.ingestion.producer import BoundedCache

        cache = BoundedCache()

        assert cache._max_size == 10
        assert cache._component_type == "producer"
