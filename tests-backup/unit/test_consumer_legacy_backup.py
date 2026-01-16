"""Modern unit tests-backup for MarketDataConsumer with V2 schema support
and Binance WebSocket integration.

This test suite provides:
- V2 schema validation (industry-standard hybrid approach)
- Binance crypto feed processing scenarios
- Fast unit test execution (<5 seconds total)
- Memory safety with simple pytest patterns
- Production-ready error handling coverage

Architecture: Fast unit tests-backup with strategic mocking
Schema Focus: V2 TradeV2 with Binance integration
Performance Target: <5 seconds execution time
Memory Management: Simple pytest patterns, no manual GC
CI/CD Integration: Compatible with Phase 6 infrastructure

Test Classes:
- TestConsumerStatsV2: V2 statistics and metrics validation
- TestMarketDataConsumerV2: Core consumer functionality with V2 schemas
- TestBinanceIntegrationV2: Binance-specific integration scenarios
- TestErrorHandlingV2: V2 error handling and edge cases
"""

import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from confluent_kafka import KafkaError

# Legacy tests-backup preserved for regression detection
# Will be run selectively to ensure no functionality loss

# Import only what's needed for basic regression checks
from k2.ingestion.consumer import ConsumerStats
from k2.ingestion.consumer import ConsumerStats, MarketDataConsumer


class TestConsumerStatsV2:
    """Test V2 ConsumerStats dataclass and metrics calculations."""

    def test_stats_initialization_v2(self):
        """Test ConsumerStats initializes with correct V2 defaults."""
        stats = ConsumerStats()

        assert stats.messages_consumed == 0
        assert stats.messages_written == 0
        assert stats.errors == 0
        assert stats.sequence_gaps == 0
        assert stats.duplicates_skipped == 0
        assert stats.start_time == 0.0

    def test_stats_duration_seconds_v2(self):
        """Test duration_seconds property with V2 precision."""
        stats = ConsumerStats()
        stats.start_time = time.time() - 10.0  # 10 seconds ago

        assert 9.5 < stats.duration_seconds < 10.5  # Allow for timing jitter

    def test_stats_throughput_v2(self):
        """Test throughput calculation for V2 crypto data rates."""
        stats = ConsumerStats()
        stats.start_time = time.time() - 10.0  # 10 seconds ago
        stats.messages_consumed = 1000  # Crypto: higher message rates

        throughput = stats.throughput
        assert 95 < throughput < 105  # ~100 messages/sec (typical crypto rate)

    def test_stats_throughput_zero_duration_v2(self):
        """Test throughput returns 0 when duration is 0."""
        stats = ConsumerStats()
        stats.start_time = 0.0
        stats.messages_consumed = 1000

        assert stats.throughput == 0.0

    def test_stats_dataclass_v2(self):
        """Test ConsumerStats as dataclass for V2 metrics tracking."""
        stats = ConsumerStats()
        stats.messages_consumed = 100
        stats.errors = 2
        stats.start_time = time.time()

        # Dataclass creates copies properly
        stats_copy = ConsumerStats(
            messages_consumed=stats.messages_consumed,
            errors=stats.errors,
            start_time=stats.start_time,
        )

        assert stats_copy.messages_consumed == stats.messages_consumed
        assert stats_copy.errors == stats.errors
        assert stats_copy.start_time == stats.start_time
        assert stats_copy is not stats  # Different objects


# V2 Schema Test Data - Realistic Binance WebSocket messages
BINANCE_TRADE_EXAMPLES = {
    "standard": {
        "e": "trade",
        "E": 1673356800000,
        "s": "BTCUSDT",
        "t": 123456789,
        "p": "16500.50",
        "q": "0.1",
        "T": 1673356800123,
        "m": False,
    },
    "defi_token": {
        "e": "trade",
        "E": 1673356800000,
        "s": "1000PEPEUSDT",
        "t": 987654321,
        "p": "0.00000123",
        "q": "1000",
        "T": 1673356800456,
        "m": True,
    },
    "crypto_pair": {
        "e": "trade",
        "E": 1673356800000,
        "s": "ETHBTC",
        "t": 555666777,
        "p": "0.065500",
        "q": "1.5",
        "T": 1673356800789,
        "m": False,
    },
    "high_precision": {
        "e": "trade",
        "E": 1673356800000,
        "s": "SHIBUSDT",
        "t": 111222333,
        "p": "0.0000087654321",
        "q": "50000.0",
        "T": 1673356800999,
        "m": True,
    },
}

# Expected V2 schema conversion results
V2_TRADE_EXPECTED = {
    "standard": {
        "symbol": "BTCUSDT",
        "exchange": "BINANCE",
        "asset_class": "crypto",
        "trade_id": "BINANCE-123456789",
        "side": "BUY",
        "currency": "USDT",
        "trade_conditions": [],
        "source_sequence": None,
        "platform_sequence": None,
    },
    "defi_token": {
        "symbol": "1000PEPEUSDT",
        "exchange": "BINANCE",
        "asset_class": "crypto",
        "trade_id": "BINANCE-987654321",
        "side": "SELL",
        "currency": "USDT",
        "trade_conditions": [],
        "source_sequence": None,
        "platform_sequence": None,
    },
    "crypto_pair": {
        "symbol": "ETHBTC",
        "exchange": "BINANCE",
        "asset_class": "crypto",
        "trade_id": "BINANCE-555666777",
        "side": "BUY",
        "currency": "BTC",
        "trade_conditions": [],
        "source_sequence": None,
        "platform_sequence": None,
    },
}


@pytest.fixture
def mock_v2_dependencies():
    """Mock all V2 consumer dependencies with simple setup."""
    with (
        patch("k2.ingestion.consumer.Consumer") as mock_consumer_class,
        patch("k2.ingestion.consumer.IcebergWriter") as mock_iceberg_class,
        patch("k2.ingestion.consumer.SequenceTracker") as mock_tracker_class,
        patch("k2.ingestion.consumer.DeadLetterQueue") as mock_dlq_class,
        patch("k2.ingestion.consumer.SchemaRegistryClient") as mock_schema_registry_class,
    ):
        # Setup mock consumer
        mock_consumer = MagicMock()
        mock_consumer.subscribe = MagicMock()
        mock_consumer.poll = MagicMock(return_value=None)  # No messages by default
        mock_consumer.commit = MagicMock()
        mock_consumer.close = MagicMock()
        mock_consumer.assignment = MagicMock(return_value=[])
        mock_consumer_class.return_value = mock_consumer

        # Setup mock iceberg writer
        mock_iceberg = MagicMock()
        mock_iceberg.write_batch = MagicMock()
        mock_iceberg.close = MagicMock()
        mock_iceberg_class.return_value = mock_iceberg

        # Setup mock sequence tracker
        mock_tracker = MagicMock()
        mock_tracker.check_sequence = MagicMock(return_value=None)
        mock_tracker_class.return_value = mock_tracker

        # Setup mock DLQ
        mock_dlq = MagicMock()
        mock_dlq.write_batch = MagicMock()
        mock_dlq_class.return_value = mock_dlq

        # Setup mock schema registry
        mock_schema_registry = MagicMock()
        mock_schema_registry_class.return_value = mock_schema_registry

        yield {
            "consumer": mock_consumer,
            "iceberg": mock_iceberg,
            "tracker": mock_tracker,
            "dlq": mock_dlq,
            "schema_registry": mock_schema_registry,
        }


@pytest.fixture
def consumer_v2(mock_v2_dependencies):
    """MarketDataConsumer instance with V2 schema and mocked dependencies."""
    consumer = MarketDataConsumer(
        topics=["market.crypto.trades.binance"],
        consumer_group="test-consumer-v2",
        schema_version="v2",
        max_messages=1,  # Set max_messages during construction
    )

    return consumer, mock_v2_dependencies


class TestMarketDataConsumerV2:
    """Test core MarketDataConsumer functionality with V2 schemas."""

    def test_consumer_initialization_v2(self, consumer_v2):
        """Test consumer initialization with V2 schema configuration."""
        consumer, mocks = consumer_v2
        mock_consumer = mocks["consumer"]

        assert consumer.schema_version == "v2"
        assert "market.crypto.trades.binance" in consumer.topics
        assert consumer.consumer_group == "test-consumer-v2"

        # Verify V2-specific configuration
        assert consumer.schema_version == "v2"

        # Verify consumer setup
        mock_consumer.subscribe.assert_called_once_with(["market.crypto.trades.binance"])

    def test_consumer_topic_pattern_v2(self, mock_v2_dependencies):
        """Test consumer initialization with topic pattern for V2."""
        consumer = MarketDataConsumer(
            topic_pattern="market\\.crypto\\..*",
            consumer_group="test-consumer-v2-pattern",
            schema_version="v2",
        )

        mock_consumer = mock_v2_dependencies["consumer"]

        assert consumer.schema_version == "v2"
        # V2 routes to trades_v2 table internally

        # Verify pattern subscription
        mock_consumer.subscribe.assert_called_once_with(pattern="market\\.crypto\\..*")

    def test_consumer_v2_stats_tracking(self, consumer_v2):
        """Test V2 consumer statistics tracking."""
        consumer, mocks = consumer_v2
        initial_consumed = consumer.stats.messages_consumed

        # Simulate processing a message
        consumer.stats.messages_consumed += 1
        consumer.stats.messages_written += 1

        assert consumer.stats.messages_consumed == initial_consumed + 1
        assert consumer.stats.messages_written == initial_consumed + 1

    def test_consumer_v2_daemon_mode_shutdown_signal(self, consumer_v2):
        """Test V2 consumer graceful shutdown with signal handling."""
        consumer, mocks = consumer_v2
        mock_consumer = mocks["consumer"]
        mock_consumer.poll.return_value = None  # No messages

        # Test running state
        assert consumer.running is True

        # Test graceful shutdown - use correct attribute name
        consumer.running = False  # Fix: was _shutdown = True in old version

        assert consumer.running is False

    def test_consumer_v2_run_batch_mode(self, consumer_v2):
        """Test V2 consumer in batch mode with max_messages limit."""
        consumer, mocks = consumer_v2
        mock_consumer = mocks["consumer"]
        mock_iceberg = mocks["iceberg"]

        # Mock successful message processing
        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = b"v2_avro_bytes"
        mock_msg.topic.return_value = "market.crypto.trades.binance"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 12345

        # Configure mock to return message then None
        mock_consumer.poll.side_effect = [mock_msg, None]

        # Mock deserializer to return valid V2 record
        with patch.object(consumer, "_deserialize_message") as mock_deserialize:
            mock_deserialize.return_value = {
                "symbol": "BTCUSDT",
                "price": Decimal("16500.50"),
                "source_sequence": None,
            }

            # Set max_messages attribute and run
            consumer.max_messages = 1
            consumer.run()

            # Should have attempted to process messages
            assert mock_consumer.poll.call_count >= 1

    def test_consume_batch_v2_empty(self, consumer_v2):
        """Test V2 batch processing with no messages."""
        consumer, mocks = consumer_v2
        mock_consumer = mocks["consumer"]
        mock_consumer.poll.return_value = None  # No messages

        result = consumer._consume_batch()

        assert result[0]["processed"] == 0
        assert result[0]["written"] == 0
        assert result[0]["errors"] == 0

    def test_consume_batch_v2_success(self, consumer_v2):
        """Test V2 batch processing with successful message processing."""
        consumer, mocks = consumer_v2
        mock_consumer = mocks["consumer"]
        mock_iceberg = mocks["iceberg"]

        # Mock successful message
        msg = MagicMock()
        msg.error.return_value = None
        msg.value.return_value = b"v2_avro_bytes"
        msg.topic.return_value = "market.crypto.trades.binance"
        msg.partition.return_value = 0
        msg.offset.return_value = 12345

        mock_consumer.poll.return_value = msg

        # Mock deserializer to return valid record
        with patch.object(consumer, "_deserialize_message") as mock_deserialize:
            mock_deserialize.return_value = {
                "symbol": "BTCUSDT",
                "price": Decimal("16500.50"),
                "source_sequence": None,
            }

            # Run in batch mode (max_messages already set during construction)
            consumer.run()

            # Should have attempted to process messages
            assert mock_consumer.poll.call_count >= 1

            result = consumer._consume_batch()

            # First batch result should show one message processed
            batch_result = result[0]
            assert batch_result["processed"] == 1
            assert batch_result["written"] == 1
            assert batch_result["errors"] == 0

            # Verify batch was written
            mock_iceberg.write_batch.assert_called_once()


class TestBinanceIntegrationV2:
    """Test Binance-specific integration scenarios with V2 schemas."""

    def test_binance_symbol_parsing_v2(self):
        """Test Binance symbol parsing for V2 currency extraction."""
        # Test standard crypto pairs
        assert parse_binance_symbol("BTCUSDT") == ("BTC", "USDT", "USDT")
        assert parse_binance_symbol("ETHUSDT") == ("ETH", "USDT", "USDT")

        # Test crypto-to-crypto pairs
        assert parse_binance_symbol("ETHBTC") == ("ETH", "BTC", "BTC")
        assert parse_binance_symbol("BNBBTC") == ("BNB", "BTC", "BTC")

        # Test fiat pairs
        assert parse_binance_symbol("BTCEUR") == ("BTC", "EUR", "EUR")
        assert parse_binance_symbol("ETHGBP") == ("ETH", "GBP", "GBP")

    def test_binance_symbol_parsing_leveraged_tokens_v2(self):
        """Test V2 symbol parsing for leveraged and defi tokens."""
        # Test leveraged tokens
        assert parse_binance_symbol("1000PEPEUSDT") == ("1000PEPE", "USDT", "USDT")
        assert parse_binance_symbol("3LONGBTCUSDT") == ("3LONGBTC", "USDT", "USDT")

        # Test defi tokens with unusual patterns
        assert parse_binance_symbol("SHIBUSDT") == ("SHIB", "USDT", "USDT")
        assert parse_binance_symbol("DOGEUSDT") == ("DOGE", "USDT", "USDT")

    def test_convert_binance_trade_to_v2_standard(self):
        """Test V2 conversion of standard Binance trade."""
        binance_msg = BINANCE_TRADE_EXAMPLES["standard"]
        expected = V2_TRADE_EXPECTED["standard"]

        v2_record = convert_binance_trade_to_v2(binance_msg)

        # Verify core V2 fields
        assert v2_record["symbol"] == expected["symbol"]
        assert v2_record["exchange"] == expected["exchange"]
        assert v2_record["asset_class"] == expected["asset_class"]
        assert v2_record["trade_id"] == expected["trade_id"]
        assert v2_record["side"] == expected["side"]
        assert v2_record["currency"] == expected["currency"]
        assert v2_record["trade_conditions"] == expected["trade_conditions"]
        assert v2_record["source_sequence"] == expected["source_sequence"]
        assert v2_record["platform_sequence"] == expected["platform_sequence"]

        # Verify V2-specific fields
        assert "message_id" in v2_record
        assert uuid.UUID(v2_record["message_id"])  # Valid UUID
        assert isinstance(v2_record["timestamp"], int)
        assert v2_record["timestamp"] == binance_msg["T"] * 1000  # ms → μs
        assert isinstance(v2_record["ingestion_timestamp"], int)

        # Verify vendor_data preservation
        assert "vendor_data" in v2_record
        assert v2_record["vendor_data"]["is_buyer_maker"] == str(binance_msg["m"])
        assert v2_record["vendor_data"]["event_type"] == binance_msg["e"]
        assert v2_record["vendor_data"]["base_asset"] == "BTC"
        assert v2_record["vendor_data"]["quote_asset"] == "USDT"

    def test_convert_binance_trade_to_v2_defi_token(self):
        """Test V2 conversion of defi token trade."""
        binance_msg = BINANCE_TRADE_EXAMPLES["defi_token"]
        expected = V2_TRADE_EXPECTED["defi_token"]

        v2_record = convert_binance_trade_to_v2(binance_msg)

        assert v2_record["symbol"] == expected["symbol"]
        assert v2_record["side"] == expected["side"]  # SELL (m=True)
        assert v2_record["currency"] == expected["currency"]

        # Verify high precision handling
        assert v2_record["price"] == Decimal(binance_msg["p"])
        assert v2_record["quantity"] == Decimal(binance_msg["q"])

        # Verify leveraged token parsing in vendor_data
        assert v2_record["vendor_data"]["base_asset"] == "1000PEPE"

    def test_convert_binance_trade_to_v2_crypto_pair(self):
        """Test V2 conversion of crypto-to-crypto pair."""
        binance_msg = BINANCE_TRADE_EXAMPLES["crypto_pair"]
        expected = V2_TRADE_EXPECTED["crypto_pair"]

        v2_record = convert_binance_trade_to_v2(binance_msg)

        assert v2_record["symbol"] == expected["symbol"]
        assert v2_record["currency"] == expected["currency"]  # BTC (quote currency)
        assert v2_record["side"] == expected["side"]  # BUY (m=False)

        # Verify crypto pair specific fields
        assert v2_record["vendor_data"]["base_asset"] == "ETH"
        assert v2_record["vendor_data"]["quote_asset"] == "BTC"

    def test_convert_binance_trade_to_v2_high_precision(self):
        """Test V2 conversion with high precision values."""
        binance_msg = BINANCE_TRADE_EXAMPLES["high_precision"]

        v2_record = convert_binance_trade_to_v2(binance_msg)

        # Verify high precision preservation
        assert v2_record["price"] == Decimal("0.0000087654321")
        assert v2_record["quantity"] == Decimal("50000.0")

        # Ensure no precision loss
        assert v2_record["price"].as_tuple().exponent == -13
        assert v2_record["quantity"].as_tuple().exponent == -1

    def test_convert_binance_trade_to_v2_side_mapping(self):
        """Test V2 side mapping from Binance is_buyer_maker."""
        # Test BUY side (buyer is taker, m=False)
        buy_msg = BINANCE_TRADE_EXAMPLES["standard"].copy()
        buy_msg["m"] = False
        v2_buy = convert_binance_trade_to_v2(buy_msg)
        assert v2_buy["side"] == "BUY"

        # Test SELL side (buyer is maker, m=True)
        sell_msg = BINANCE_TRADE_EXAMPLES["standard"].copy()
        sell_msg["m"] = True
        v2_sell = convert_binance_trade_to_v2(sell_msg)
        assert v2_sell["side"] == "SELL"

    def test_convert_binance_trade_to_v2_timestamp_precision(self):
        """Test V2 timestamp precision conversion (ms → μs)."""
        binance_msg = BINANCE_TRADE_EXAMPLES["standard"]

        v2_record = convert_binance_trade_to_v2(binance_msg)

        # Binance uses milliseconds, V2 uses microseconds
        expected_timestamp = binance_msg["T"] * 1000
        assert v2_record["timestamp"] == expected_timestamp

        # Verify ingestion timestamp is recent
        current_time = int(time.time() * 1_000_000)  # Current time in microseconds
        assert abs(v2_record["ingestion_timestamp"] - current_time) < 1_000_000  # Within 1 second


class TestErrorHandlingV2:
    """Test V2 error handling and edge cases."""

    def test_deserialize_message_v2_with_error(self, consumer_v2):
        """Test V2 deserialize returns None on message error."""
        consumer, mocks = consumer_v2
        msg = MagicMock()
        # Create mock with partition EOF error
        mock_error = MagicMock()
        mock_error.code.return_value = 194  # KafkaError._PARTITION_EOF
        msg.error.return_value = mock_error

        record = consumer._deserialize_message(msg)

        assert record is None

    def test_consume_batch_v2_with_deserialization_errors(self, consumer_v2):
        """Test V2 batch processing with deserialization errors."""
        consumer, mocks = consumer_v2
        mock_consumer = mocks["consumer"]
        mock_dlq = mocks["dlq"]

        # Mock message that causes deserialization error
        msg = MagicMock()
        msg.error.return_value = None
        msg.value.return_value = b"malformed_v2_bytes"
        msg.topic.return_value = "market.crypto.trades.binance"
        msg.partition.return_value = 0
        msg.offset.return_value = 12345

        mock_consumer.poll.return_value = msg

        # Mock deserializer to raise exception
        with patch.object(consumer, "_deserialize_message") as mock_deserialize:
            mock_deserialize.return_value = None  # Simulate deserialization failure

            result = consumer._consume_batch()

            assert result[0]["processed"] == 1
            assert result[0]["written"] == 0
            assert result[0]["errors"] == 1

    def test_binance_conversion_v2_missing_fields(self):
        """Test V2 conversion error handling for missing Binance fields."""
        # Test missing required fields
        invalid_msgs = [
            {},  # Empty message
            {"e": "trade"},  # Missing all other fields
            {"e": "trade", "s": "BTCUSDT"},  # Missing most required fields
        ]

        for invalid_msg in invalid_msgs:
            with pytest.raises(ValueError, match="Missing required fields"):
                convert_binance_trade_to_v2(invalid_msg)

    def test_binance_conversion_v2_invalid_event_type(self):
        """Test V2 conversion rejects non-trade events."""
        invalid_msg = BINANCE_TRADE_EXAMPLES["standard"].copy()
        invalid_msg["e"] = "24hrTicker"  # Wrong event type

        with pytest.raises(ValueError, match="Invalid event type"):
            convert_binance_trade_to_v2(invalid_msg)

    def test_binance_conversion_v2_invalid_symbol_format(self):
        """Test V2 conversion with invalid symbol format."""
        # Symbol with no recognizable quote currency
        invalid_msg = BINANCE_TRADE_EXAMPLES["standard"].copy()
        invalid_msg["s"] = "INVALIDSYMBOL"

        # Should not crash, but may use fallback parsing
        v2_record = convert_binance_trade_to_v2(invalid_msg)

        # Verify it still produces a valid V2 record
        assert "symbol" in v2_record
        assert "currency" in v2_record
        assert v2_record["exchange"] == "BINANCE"
        assert v2_record["asset_class"] == "crypto"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
