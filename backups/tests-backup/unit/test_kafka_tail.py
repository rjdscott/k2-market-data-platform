"""Unit tests-backup for KafkaTail - In-memory Kafka buffer.

Test Coverage:
- Initialization and configuration
- Message processing and buffering
- Query operations (time range, symbol filtering)
- Trimming old messages
- Statistics tracking
- Thread safety
- Edge cases
"""

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from k2.query.kafka_tail import KafkaTail, TailMessage


class TestKafkaTailInitialization:
    """Test KafkaTail initialization and configuration."""

    def test_initialization_defaults(self):
        """Test KafkaTail initializes with default configuration."""
        tail = KafkaTail()

        assert tail.topic == "market-data.trades.v2"
        assert tail.group_id == "k2-kafka-tail"
        assert tail.buffer_minutes == 5
        assert tail.max_messages_per_symbol == 10000
        assert tail.trim_interval_seconds == 10
        assert not tail._running

    def test_initialization_custom_config(self):
        """Test KafkaTail initializes with custom configuration."""
        tail = KafkaTail(
            bootstrap_servers="custom:9092",
            topic="custom-topic",
            buffer_minutes=10,
            max_messages_per_symbol=5000,
        )

        assert tail.bootstrap_servers == "custom:9092"
        assert tail.topic == "custom-topic"
        assert tail.buffer_minutes == 10
        assert tail.max_messages_per_symbol == 5000


class TestKafkaTailMessageProcessing:
    """Test message processing and buffering."""

    @pytest.fixture
    def tail(self):
        """Create KafkaTail instance for testing."""
        return KafkaTail(buffer_minutes=5)

    def test_process_message_adds_to_buffer(self, tail):
        """Test processing message adds it to buffer."""
        # Create mock Kafka message
        msg = MagicMock()
        msg.value.return_value = json.dumps(
            {
                "symbol": "BTCUSDT",
                "exchange": "binance",
                "message_id": "msg-001",
                "timestamp": datetime.now(UTC).isoformat(),
                "price": 50000.0,
                "quantity": 1.5,
            }
        ).encode("utf-8")
        msg.timestamp.return_value = (1, int(time.time() * 1000))

        # Process message
        tail._process_message(msg)

        # Verify added to buffer
        assert "BTCUSDT" in tail._buffer
        assert len(tail._buffer["BTCUSDT"]) == 1
        assert tail._stats["messages_received"] == 1

    def test_process_message_enforces_limit(self, tail):
        """Test processing messages enforces per-symbol limit."""
        tail.max_messages_per_symbol = 3

        # Add 5 messages (should keep only last 3)
        for i in range(5):
            msg = MagicMock()
            msg.value.return_value = json.dumps(
                {
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                    "message_id": f"msg-{i:03d}",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "price": 50000.0 + i,
                }
            ).encode("utf-8")
            msg.timestamp.return_value = (1, int(time.time() * 1000))

            tail._process_message(msg)

        # Should only keep last 3
        assert len(tail._buffer["BTCUSDT"]) == 3
        assert tail._buffer["BTCUSDT"][0].message_id == "msg-002"
        assert tail._stats["messages_trimmed"] == 2

    def test_process_message_handles_missing_fields(self, tail):
        """Test processing message with missing required fields."""
        msg = MagicMock()
        msg.value.return_value = json.dumps(
            {
                "price": 50000.0,
                # Missing symbol, exchange, message_id
            }
        ).encode("utf-8")

        # Should not crash, just log warning
        tail._process_message(msg)

        # Buffer should be empty
        assert len(tail._buffer) == 0


class TestKafkaTailQuerying:
    """Test query operations."""

    @pytest.fixture
    def tail_with_data(self):
        """Create KafkaTail with test data."""
        tail = KafkaTail()

        # Add test messages
        now = datetime.now(UTC)
        for i in range(10):
            msg = TailMessage(
                symbol="BTCUSDT",
                exchange="binance",
                timestamp=now - timedelta(minutes=i),
                message_id=f"msg-{i:03d}",
                data={
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                    "message_id": f"msg-{i:03d}",
                    "timestamp": (now - timedelta(minutes=i)).isoformat(),
                    "price": 50000.0 + i,
                },
            )
            tail._buffer["BTCUSDT"].append(msg)

        return tail

    def test_query_returns_messages_in_time_range(self, tail_with_data):
        """Test query returns messages within time range."""
        now = datetime.now(UTC)
        start = now - timedelta(minutes=5)
        end = now

        results = tail_with_data.query(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=start,
            end_time=end,
        )

        # Should return messages from last 5 minutes (0-4)
        assert len(results) == 5
        assert all(
            start <= datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) <= end
            for r in results
        )

    def test_query_filters_by_exchange(self, tail_with_data):
        """Test query filters by exchange."""
        now = datetime.now(UTC)

        # Query for different exchange (should return empty)
        results = tail_with_data.query(
            symbol="BTCUSDT",
            exchange="coinbase",
            start_time=now - timedelta(minutes=10),
            end_time=now,
        )

        assert len(results) == 0

    def test_query_empty_symbol_returns_empty(self, tail_with_data):
        """Test query for non-existent symbol returns empty."""
        now = datetime.now(UTC)

        results = tail_with_data.query(
            symbol="ETHUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=10),
            end_time=now,
        )

        assert len(results) == 0


class TestKafkaTailTrimming:
    """Test message trimming."""

    @pytest.fixture
    def tail(self):
        """Create KafkaTail for testing."""
        return KafkaTail(buffer_minutes=5, trim_interval_seconds=1)

    def test_trim_removes_old_messages(self, tail):
        """Test trimming removes messages older than buffer_minutes."""
        now = datetime.now(UTC)

        # Add messages: 3 old, 2 new
        for i in range(5):
            age_minutes = 10 - i  # 10, 9, 8, 7, 6 minutes ago
            msg = TailMessage(
                symbol="BTCUSDT",
                exchange="binance",
                timestamp=now - timedelta(minutes=age_minutes),
                message_id=f"msg-{i:03d}",
                data={"message_id": f"msg-{i:03d}"},
            )
            tail._buffer["BTCUSDT"].append(msg)

        # Trim (buffer_minutes=5, so remove messages > 5 min old)
        tail._trim_old_messages()

        # Should keep only messages < 5 minutes old (last 2: 7, 6 min ago? No, none)
        # Actually with cutoff at 5 minutes, all are old
        # Let's fix the test data
        tail._buffer.clear()

        # Add messages: 2 within window, 3 outside
        for i in range(5):
            age_minutes = i  # 0, 1, 2, 3, 4 minutes ago (all within 5 min window)
            msg = TailMessage(
                symbol="BTCUSDT",
                exchange="binance",
                timestamp=now - timedelta(minutes=age_minutes),
                message_id=f"msg-{i:03d}",
                data={"message_id": f"msg-{i:03d}"},
            )
            tail._buffer["BTCUSDT"].append(msg)

        # Add 3 old messages
        for i in range(5, 8):
            age_minutes = i + 5  # 10, 11, 12 minutes ago
            msg = TailMessage(
                symbol="BTCUSDT",
                exchange="binance",
                timestamp=now - timedelta(minutes=age_minutes),
                message_id=f"msg-{i:03d}",
                data={"message_id": f"msg-{i:03d}"},
            )
            tail._buffer["BTCUSDT"].append(msg)

        initial_count = len(tail._buffer["BTCUSDT"])
        assert initial_count == 8

        tail._trim_old_messages()

        # Should keep only messages within 5-minute window (first 5)
        assert len(tail._buffer["BTCUSDT"]) == 5
        assert tail._stats["messages_trimmed"] == 3

    def test_trim_removes_empty_symbols(self, tail):
        """Test trimming removes symbols with no messages."""
        now = datetime.now(UTC)

        # Add old message
        msg = TailMessage(
            symbol="BTCUSDT",
            exchange="binance",
            timestamp=now - timedelta(minutes=10),
            message_id="msg-001",
            data={},
        )
        tail._buffer["BTCUSDT"].append(msg)

        # Trim (buffer is 5 minutes, message is 10 minutes old)
        tail._trim_old_messages()

        # Symbol should be removed from buffer
        assert "BTCUSDT" not in tail._buffer


class TestKafkaTailStatistics:
    """Test statistics tracking."""

    def test_get_stats_returns_statistics(self):
        """Test get_stats returns current statistics."""
        tail = KafkaTail()

        # Add some test data
        tail._stats = {
            "messages_received": 100,
            "messages_trimmed": 10,
            "buffer_size": 90,
            "symbols_tracked": 5,
        }

        stats = tail.get_stats()

        assert stats["messages_received"] == 100
        assert stats["messages_trimmed"] == 10
        assert stats["buffer_size"] == 90
        assert stats["symbols_tracked"] == 5

    def test_clear_resets_buffer_and_stats(self):
        """Test clear() resets buffer and statistics."""
        tail = KafkaTail()

        # Add data
        tail._buffer["BTCUSDT"].append(
            TailMessage(
                symbol="BTCUSDT",
                exchange="binance",
                timestamp=datetime.now(UTC),
                message_id="msg-001",
                data={},
            ),
        )
        tail._stats["messages_received"] = 10

        # Clear
        tail.clear()

        # Should be reset
        assert len(tail._buffer) == 0
        assert tail._stats["messages_received"] == 0
        assert tail._stats["buffer_size"] == 0


class TestKafkaTailEdgeCases:
    """Test edge cases and error handling."""

    def test_query_with_no_buffer_returns_empty(self):
        """Test query with empty buffer returns empty list."""
        tail = KafkaTail()
        now = datetime.now(UTC)

        results = tail.query(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=10),
            end_time=now,
        )

        assert results == []

    def test_process_message_with_invalid_json(self):
        """Test processing message with invalid JSON doesn't crash."""
        tail = KafkaTail()

        msg = MagicMock()
        msg.value.return_value = b"invalid json {{{{"

        # Should not raise exception
        tail._process_message(msg)

        # Buffer should remain empty
        assert len(tail._buffer) == 0

    def test_start_stop_lifecycle(self):
        """Test start/stop lifecycle."""
        with patch("k2.query.kafka_tail.Consumer") as mock_consumer:
            tail = KafkaTail()

            # Start
            tail.start()
            assert tail._running is True
            mock_consumer.assert_called_once()

            # Stop
            tail.stop()
            assert tail._running is False


@pytest.mark.integration
class TestKafkaTailFactory:
    """Test factory function."""

    @patch("k2.query.kafka_tail.Consumer")
    def test_create_kafka_tail_starts_consumer(self, mock_consumer):
        """Test create_kafka_tail factory creates and starts tail."""
        from k2.query.kafka_tail import create_kafka_tail

        tail = create_kafka_tail(buffer_minutes=10)

        assert tail._running is True
        assert tail.buffer_minutes == 10
        mock_consumer.assert_called_once()

        # Cleanup
        tail.stop()
