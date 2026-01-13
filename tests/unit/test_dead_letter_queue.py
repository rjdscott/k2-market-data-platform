"""Unit tests for Dead Letter Queue with datetime serialization support.

Tests verify that DLQ can handle messages containing datetime objects
by using the custom DateTimeEncoder.

This test suite validates the fix for TD-002: DLQ JSON Serialization.
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from k2.ingestion.dead_letter_queue import DeadLetterQueue, DateTimeEncoder


class TestDateTimeEncoder:
    """Test custom JSON encoder for datetime objects."""

    def test_encode_datetime_object(self):
        """Test that datetime objects are encoded to ISO format."""
        dt = datetime(2024, 1, 15, 10, 30, 45, 123456)
        result = json.dumps({"timestamp": dt}, cls=DateTimeEncoder)

        expected = '{"timestamp": "2024-01-15T10:30:45.123456"}'
        assert result == expected

    def test_encode_datetime_utc(self):
        """Test encoding of UTC datetime."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        result = json.dumps({"timestamp": dt}, cls=DateTimeEncoder)

        # Should contain timezone info
        assert "2024-01-15T10:30:45" in result
        assert "+00:00" in result

    def test_encode_nested_datetime(self):
        """Test encoding of datetime in nested structures."""
        data = {
            "level1": {
                "level2": {
                    "timestamp": datetime(2024, 1, 15, 10, 30, 45)
                }
            }
        }

        result = json.dumps(data, cls=DateTimeEncoder)
        parsed = json.loads(result)

        # Should be serialized as ISO string
        assert isinstance(parsed["level1"]["level2"]["timestamp"], str)
        assert "2024-01-15T10:30:45" in parsed["level1"]["level2"]["timestamp"]

    def test_encode_datetime_in_list(self):
        """Test encoding of datetime objects in lists."""
        data = {
            "timestamps": [
                datetime(2024, 1, 15, 10, 0, 0),
                datetime(2024, 1, 15, 11, 0, 0),
                datetime(2024, 1, 15, 12, 0, 0),
            ]
        }

        result = json.dumps(data, cls=DateTimeEncoder)
        parsed = json.loads(result)

        assert len(parsed["timestamps"]) == 3
        assert all(isinstance(ts, str) for ts in parsed["timestamps"])
        assert parsed["timestamps"][0] == "2024-01-15T10:00:00"

    def test_encode_non_datetime_unchanged(self):
        """Test that non-datetime objects are encoded normally."""
        data = {
            "string": "hello",
            "number": 42,
            "float": 3.14,
            "boolean": True,
            "null": None,
            "list": [1, 2, 3],
            "dict": {"nested": "value"}
        }

        result = json.dumps(data, cls=DateTimeEncoder)
        parsed = json.loads(result)

        # Should match original (through round-trip)
        assert parsed["string"] == "hello"
        assert parsed["number"] == 42
        assert parsed["float"] == 3.14
        assert parsed["boolean"] is True
        assert parsed["null"] is None
        assert parsed["list"] == [1, 2, 3]
        assert parsed["dict"] == {"nested": "value"}


class TestDeadLetterQueueDateTimeSerialization:
    """Test DLQ handling of messages with datetime fields."""

    @pytest.fixture
    def temp_dlq(self):
        """Fixture providing temporary DLQ directory."""
        with TemporaryDirectory() as tmpdir:
            dlq = DeadLetterQueue(path=Path(tmpdir), max_size_mb=1)
            yield dlq

    def test_write_message_with_datetime_field(self, temp_dlq):
        """Test that DLQ can write messages containing datetime objects."""
        # Create message with datetime field (common in trade messages)
        message_with_datetime = {
            "symbol": "BHP",
            "price": 45.67,
            "timestamp": datetime(2024, 1, 15, 10, 30, 45, 123456),
            "exchange_timestamp": datetime(2024, 1, 15, 10, 30, 45),
        }

        # Should not raise TypeError
        dlq_file = temp_dlq.write(
            messages=[message_with_datetime],
            error="Test error",
            error_type="test",
        )

        # Verify file was written
        assert dlq_file.exists()

        # Read back and verify
        entries = temp_dlq.read_file(dlq_file)
        assert len(entries) == 1

        # Datetime should be serialized as ISO string
        assert isinstance(entries[0]["message"]["timestamp"], str)
        assert "2024-01-15T10:30:45" in entries[0]["message"]["timestamp"]

    def test_write_multiple_messages_with_datetime(self, temp_dlq):
        """Test writing multiple messages with datetime fields."""
        messages = [
            {
                "symbol": f"SYM{i}",
                "timestamp": datetime(2024, 1, 15, 10, i, 0),
                "data": {"nested_time": datetime(2024, 1, 15, 10, i, 30)}
            }
            for i in range(5)
        ]

        dlq_file = temp_dlq.write(
            messages=messages,
            error="Batch error",
            error_type="batch_test",
        )

        # Read back and verify all messages
        entries = temp_dlq.read_file(dlq_file)
        assert len(entries) == 5

        # All timestamps should be strings
        for i, entry in enumerate(entries):
            assert isinstance(entry["message"]["timestamp"], str)
            assert f"10:{i:02d}:00" in entry["message"]["timestamp"]
            assert isinstance(entry["message"]["data"]["nested_time"], str)

    def test_write_message_with_mixed_types(self, temp_dlq):
        """Test message with datetime mixed with other types."""
        complex_message = {
            "string_field": "value",
            "int_field": 42,
            "float_field": 3.14,
            "bool_field": True,
            "null_field": None,
            "datetime_field": datetime(2024, 1, 15, 10, 30, 45),
            "list_field": [1, 2, datetime(2024, 1, 15, 11, 0, 0)],
            "dict_field": {
                "nested_datetime": datetime(2024, 1, 15, 12, 0, 0),
                "nested_value": "test"
            }
        }

        dlq_file = temp_dlq.write(
            messages=[complex_message],
            error="Complex message error",
            error_type="complex_test",
        )

        entries = temp_dlq.read_file(dlq_file)
        msg = entries[0]["message"]

        # Verify all field types preserved (datetime as string)
        assert msg["string_field"] == "value"
        assert msg["int_field"] == 42
        assert msg["float_field"] == 3.14
        assert msg["bool_field"] is True
        assert msg["null_field"] is None
        assert isinstance(msg["datetime_field"], str)
        assert isinstance(msg["list_field"][2], str)  # datetime in list
        assert isinstance(msg["dict_field"]["nested_datetime"], str)

    def test_write_with_datetime_in_metadata(self, temp_dlq):
        """Test that datetime in metadata is also handled correctly."""
        message = {"symbol": "BHP", "price": 45.67}

        metadata_with_datetime = {
            "consumer_group": "k2-trades",
            "last_seen": datetime(2024, 1, 15, 10, 30, 45),
            "session_start": datetime(2024, 1, 15, 9, 0, 0),
        }

        dlq_file = temp_dlq.write(
            messages=[message],
            error="Metadata test",
            error_type="metadata_test",
            metadata=metadata_with_datetime,
        )

        entries = temp_dlq.read_file(dlq_file)
        metadata = entries[0]["metadata"]

        # Datetime in metadata should also be serialized
        assert isinstance(metadata["last_seen"], str)
        assert "2024-01-15T10:30:45" in metadata["last_seen"]

    def test_backward_compatibility_without_datetime(self, temp_dlq):
        """Test that messages without datetime still work (backward compatibility)."""
        simple_message = {
            "symbol": "BHP",
            "price": 45.67,
            "quantity": 100,
        }

        dlq_file = temp_dlq.write(
            messages=[simple_message],
            error="Simple message error",
            error_type="simple_test",
        )

        entries = temp_dlq.read_file(dlq_file)
        msg = entries[0]["message"]

        # Should match original
        assert msg == simple_message


class TestDeadLetterQueueIntegration:
    """Integration tests for DLQ with realistic scenarios."""

    @pytest.fixture
    def temp_dlq(self):
        """Fixture providing temporary DLQ directory."""
        with TemporaryDirectory() as tmpdir:
            dlq = DeadLetterQueue(path=Path(tmpdir), max_size_mb=1)
            yield dlq

    def test_realistic_trade_message_v2_schema(self, temp_dlq):
        """Test with realistic trade message matching v2 schema."""
        # This is what an actual trade message looks like after Avro deserialization
        trade_message = {
            "message_id": "550e8400-e29b-41d4-a716-446655440000",
            "trade_id": "BINANCE-12345678",
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "asset_class": "crypto",
            "timestamp": datetime(2024, 1, 15, 10, 30, 45, 123456),  # Avro timestamp-micros → datetime
            "price": 43250.50,
            "quantity": 0.5,
            "currency": "USDT",
            "side": "BUY",
            "trade_conditions": [],
            "source_sequence": 123456789,
            "ingestion_timestamp": datetime(2024, 1, 15, 10, 30, 45, 234567),
            "platform_sequence": 987654321,
            "vendor_data": {
                "is_buyer_maker": "true",
                "event_type": "trade",
                "event_time": datetime(2024, 1, 15, 10, 30, 45)  # Nested datetime
            }
        }

        # Should handle all datetime fields without error
        dlq_file = temp_dlq.write(
            messages=[trade_message],
            error="Simulated processing error",
            error_type="processing_failure",
            metadata={
                "consumer_group": "k2-iceberg-writer-trades-v2",
                "topic": "market.crypto.trades.binance",
                "schema_version": "v2",
                "failure_time": datetime.utcnow()
            }
        )

        # Verify message was written correctly
        entries = temp_dlq.read_file(dlq_file)
        assert len(entries) == 1

        msg = entries[0]["message"]

        # Verify all datetime fields were serialized
        assert isinstance(msg["timestamp"], str)
        assert isinstance(msg["ingestion_timestamp"], str)
        assert isinstance(msg["vendor_data"]["event_time"], str)
        assert isinstance(entries[0]["metadata"]["failure_time"], str)

        # Verify non-datetime fields unchanged
        assert msg["symbol"] == "BTCUSDT"
        assert msg["price"] == 43250.50
        assert msg["quantity"] == 0.5

    def test_error_handling_preserves_datetime(self, temp_dlq):
        """Test that datetime is preserved through error scenarios."""
        # Message that would cause a processing error
        problematic_message = {
            "symbol": "INVALID",
            "price": -1.0,  # Invalid negative price
            "timestamp": datetime(2024, 1, 15, 10, 30, 45),
            "details": {
                "created_at": datetime(2024, 1, 15, 9, 0, 0)
            }
        }

        try:
            # Simulate validation error
            if problematic_message["price"] < 0:
                raise ValueError("Price must be positive")
        except ValueError as err:
            # Write to DLQ as would happen in production
            dlq_file = temp_dlq.write(
                messages=[problematic_message],
                error=str(err),
                error_type="validation",
                metadata={
                    "consumer_group": "k2-trades",
                    "detected_at": datetime.utcnow()
                }
            )

        # Verify message and all datetime fields preserved
        entries = temp_dlq.read_file(dlq_file)
        assert entries[0]["error"] == "Price must be positive"
        assert isinstance(entries[0]["message"]["timestamp"], str)
        assert isinstance(entries[0]["message"]["details"]["created_at"], str)
        assert isinstance(entries[0]["metadata"]["detected_at"], str)
