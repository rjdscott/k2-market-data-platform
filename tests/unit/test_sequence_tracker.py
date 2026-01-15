"""Comprehensive test suite for SequenceTracker and DeduplicationCache.

These components are CRITICAL for data integrity - they detect:
- Sequence gaps (potential data loss)
- Duplicate messages (at-least-once delivery)
- Out-of-order delivery (Kafka rebalancing)
- Session resets (daily market opens)

Without proper testing, silent data loss can occur in production.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from k2.ingestion.sequence_tracker import (
    DeduplicationCache,
    SequenceEvent,
    SequenceState,
    SequenceTracker,
)


class TestSequenceTracker:
    """Test SequenceTracker - critical for detecting data loss."""

    def test_first_message_for_symbol(self):
        """Test that first message for a symbol is accepted"""
        tracker = SequenceTracker()
        result = tracker.check_sequence(
            exchange="ASX",
            symbol="BHP",
            sequence=1,
            timestamp=datetime(2024, 1, 1, 10, 0, 0),
        )

        assert result == SequenceEvent.OK

        # Verify state was created
        state = tracker.get_stats("ASX", "BHP")
        assert state is not None
        assert state.last_sequence == 1
        assert state.total_messages == 1
        assert state.gap_count == 0

    def test_in_order_sequence(self):
        """Test normal in-order sequence: 1, 2, 3, 4"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        assert tracker.check_sequence("ASX", "BHP", 1, timestamp) == SequenceEvent.OK
        assert tracker.check_sequence("ASX", "BHP", 2, timestamp) == SequenceEvent.OK
        assert tracker.check_sequence("ASX", "BHP", 3, timestamp) == SequenceEvent.OK
        assert tracker.check_sequence("ASX", "BHP", 4, timestamp) == SequenceEvent.OK

        state = tracker.get_stats("ASX", "BHP")
        assert state.last_sequence == 4
        assert state.total_messages == 4
        assert state.gap_count == 0
        assert state.out_of_order_count == 0

    def test_small_gap_detection(self):
        """Test small gap detection: 1, 2, 4 (missing 3)"""
        tracker = SequenceTracker(gap_alert_threshold=10)
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 2, timestamp)

        # Gap of 1 (expected 3, got 4)
        result = tracker.check_sequence("ASX", "BHP", 4, timestamp)

        assert result == SequenceEvent.SMALL_GAP

        state = tracker.get_stats("ASX", "BHP")
        assert state.last_sequence == 4  # Advanced past gap
        assert state.total_messages == 3
        assert state.gap_count == 1

    def test_large_gap_detection(self):
        """Test large gap detection: 1, 2, 100 (gap > threshold)"""
        tracker = SequenceTracker(gap_alert_threshold=10)
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 2, timestamp)

        # Gap of 97 (expected 3, got 100) - exceeds threshold of 10
        result = tracker.check_sequence("ASX", "BHP", 100, timestamp)

        assert result == SequenceEvent.LARGE_GAP

        state = tracker.get_stats("ASX", "BHP")
        assert state.last_sequence == 100
        assert state.gap_count == 1

    def test_multiple_gaps_tracked(self):
        """Test that multiple gaps are counted correctly"""
        tracker = SequenceTracker(gap_alert_threshold=10)
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 3, timestamp)  # Gap 1
        tracker.check_sequence("ASX", "BHP", 6, timestamp)  # Gap 2
        tracker.check_sequence("ASX", "BHP", 10, timestamp)  # Gap 3

        state = tracker.get_stats("ASX", "BHP")
        assert state.gap_count == 3

    def test_out_of_order_detection(self):
        """Test out-of-order: 1, 2, 4, 3 (3 arrives late)"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 2, timestamp)
        tracker.check_sequence("ASX", "BHP", 4, timestamp)

        # Sequence 3 arrives after 4 (out of order)
        result = tracker.check_sequence("ASX", "BHP", 3, timestamp)

        assert result == SequenceEvent.OUT_OF_ORDER

        state = tracker.get_stats("ASX", "BHP")
        assert state.last_sequence == 4  # High watermark preserved
        assert state.out_of_order_count == 1

    def test_duplicate_detection(self):
        """Test duplicate: 1, 2, 2 (duplicate 2)"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 2, timestamp)

        # Duplicate sequence 2
        result = tracker.check_sequence("ASX", "BHP", 2, timestamp)

        assert result == SequenceEvent.OUT_OF_ORDER

        state = tracker.get_stats("ASX", "BHP")
        assert state.last_sequence == 2
        assert state.out_of_order_count == 1

    def test_session_reset_detection(self):
        """Test session reset: large sequence drop + time jump"""
        tracker = SequenceTracker(
            reset_detection_window=timedelta(hours=1),
        )

        # Build up large sequence
        timestamp1 = datetime(2024, 1, 1, 16, 0, 0)  # 4 PM
        for i in range(1, 10001):
            tracker.check_sequence("ASX", "BHP", i, timestamp1)

        # Next day, sequence resets
        timestamp2 = datetime(2024, 1, 2, 10, 0, 0)  # Next day 10 AM (18 hours later)
        result = tracker.check_sequence("ASX", "BHP", 1, timestamp2)

        assert result == SequenceEvent.RESET

        # State should be reset
        state = tracker.get_stats("ASX", "BHP")
        assert state.last_sequence == 1
        assert state.gap_count == 0  # Gaps before reset don't carry over

    def test_session_reset_requires_time_jump(self):
        """Test that session reset requires BOTH sequence drop AND time jump"""
        tracker = SequenceTracker(
            reset_detection_window=timedelta(hours=1),
        )

        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        # Build up sequence to 10000
        for i in range(1, 10001):
            tracker.check_sequence("ASX", "BHP", i, timestamp)

        # Sequence drops but NO time jump (within 1 hour)
        result = tracker.check_sequence("ASX", "BHP", 1, timestamp + timedelta(minutes=30))

        # Should be LARGE_GAP, not RESET (time window not exceeded)
        assert result == SequenceEvent.LARGE_GAP

    def test_multi_symbol_isolation(self):
        """Test that sequences for different symbols are independent"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        # Interleave sequences for different symbols
        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "RIO", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 2, timestamp)
        tracker.check_sequence("ASX", "RIO", 2, timestamp)
        tracker.check_sequence("ASX", "BHP", 4, timestamp)  # Gap for BHP
        tracker.check_sequence("ASX", "RIO", 3, timestamp)  # OK for RIO

        # BHP should have gap
        bhp_state = tracker.get_stats("ASX", "BHP")
        assert bhp_state.gap_count == 1
        assert bhp_state.last_sequence == 4

        # RIO should have no gaps
        rio_state = tracker.get_stats("ASX", "RIO")
        assert rio_state.gap_count == 0
        assert rio_state.last_sequence == 3

    def test_multi_exchange_isolation(self):
        """Test that sequences for same symbol on different exchanges are independent"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        # Same symbol on different exchanges
        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("BINANCE", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 3, timestamp)  # Gap on ASX
        tracker.check_sequence("BINANCE", "BHP", 2, timestamp)  # No gap on BINANCE

        # ASX should have gap
        asx_state = tracker.get_stats("ASX", "BHP")
        assert asx_state.gap_count == 1

        # BINANCE should have no gaps
        binance_state = tracker.get_stats("BINANCE", "BHP")
        assert binance_state.gap_count == 0

    def test_get_stats_unknown_symbol(self):
        """Test get_stats returns None for unknown symbol"""
        tracker = SequenceTracker()

        state = tracker.get_stats("ASX", "UNKNOWN")

        assert state is None

    def test_reset_clears_state(self):
        """Test that reset() clears tracking state for a symbol"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        # Create some state
        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "BHP", 2, timestamp)

        # Verify state exists
        assert tracker.get_stats("ASX", "BHP") is not None

        # Reset
        tracker.reset("ASX", "BHP")

        # Verify state cleared
        assert tracker.get_stats("ASX", "BHP") is None

    def test_reset_one_symbol_preserves_others(self):
        """Test that resetting one symbol doesn't affect others"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        # Create state for multiple symbols
        tracker.check_sequence("ASX", "BHP", 1, timestamp)
        tracker.check_sequence("ASX", "RIO", 1, timestamp)

        # Reset only BHP
        tracker.reset("ASX", "BHP")

        # BHP should be cleared
        assert tracker.get_stats("ASX", "BHP") is None

        # RIO should still exist
        assert tracker.get_stats("ASX", "RIO") is not None

    def test_high_volume_stress(self):
        """Test tracker with high message volume"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        # Process 100K messages in sequence
        for i in range(1, 100_001):
            result = tracker.check_sequence("ASX", "BHP", i, timestamp)
            assert result == SequenceEvent.OK

        state = tracker.get_stats("ASX", "BHP")
        assert state.total_messages == 100_000
        assert state.gap_count == 0

    def test_metrics_integration(self):
        """Test that metrics are recorded for gaps and resets"""
        tracker = SequenceTracker()
        timestamp1 = datetime(2024, 1, 1, 10, 0, 0)

        with patch("k2.ingestion.sequence_tracker.metrics") as mock_metrics:
            # Create gap
            tracker.check_sequence("ASX", "BHP", 1, timestamp1)
            tracker.check_sequence("ASX", "BHP", 5, timestamp1)  # Gap

            # Verify gap metric was recorded
            mock_metrics.increment.assert_called_with(
                "sequence_gaps_total",
                tags={
                    "exchange": "ASX",
                    "symbol": "BHP",
                    "gap_size": "3",
                },
            )

            # Create reset
            timestamp2 = datetime(2024, 1, 2, 10, 0, 0)
            for i in range(6, 10001):
                tracker.check_sequence("ASX", "BHP", i, timestamp1)

            tracker.check_sequence("ASX", "BHP", 1, timestamp2)

            # Verify reset metric was recorded
            assert any(
                call[0][0] == "sequence_resets_total"
                for call in mock_metrics.increment.call_args_list
            )


class TestDeduplicationCache:
    """Test DeduplicationCache - prevents duplicate message processing."""

    def test_first_message_not_duplicate(self):
        """Test that first occurrence of message ID is not a duplicate"""
        dedup = DeduplicationCache(window_hours=24)

        result = dedup.is_duplicate("msg_001")

        assert result is False

    def test_duplicate_detection(self):
        """Test that second occurrence is detected as duplicate"""
        dedup = DeduplicationCache(window_hours=24)

        # First occurrence
        dedup.is_duplicate("msg_001")

        # Second occurrence (duplicate)
        result = dedup.is_duplicate("msg_001")

        assert result is True

    def test_different_messages_not_duplicates(self):
        """Test that different message IDs are not duplicates"""
        dedup = DeduplicationCache(window_hours=24)

        dedup.is_duplicate("msg_001")
        dedup.is_duplicate("msg_002")

        # msg_002 is not a duplicate of msg_001
        result = dedup.is_duplicate("msg_002")

        assert result is True  # It's seen before (itself)

        result = dedup.is_duplicate("msg_003")
        assert result is False  # New message

    def test_cleanup_removes_expired_entries(self):
        """Test that cache cleanup removes old entries"""
        dedup = DeduplicationCache(window_hours=1)

        # Mock current time
        fake_now = datetime(2024, 1, 1, 10, 0, 0)

        with patch("k2.ingestion.sequence_tracker.datetime") as mock_datetime:
            # Set initial time
            mock_datetime.utcnow.return_value = fake_now

            # Add messages
            dedup.is_duplicate("msg_001")
            dedup.is_duplicate("msg_002")

            # Advance time by 2 hours (beyond 1-hour window)
            mock_datetime.utcnow.return_value = fake_now + timedelta(hours=2)

            # Trigger cleanup (happens on next is_duplicate call)
            dedup.is_duplicate("msg_003")

            # msg_001 and msg_002 should be expired
            # Force another cleanup check
            mock_datetime.utcnow.return_value = fake_now + timedelta(hours=2, minutes=15)
            result = dedup.is_duplicate("msg_001")

            # Should not be duplicate (expired and removed)
            assert result is False

    def test_cleanup_only_runs_periodically(self):
        """Test that cleanup doesn't run on every message"""
        dedup = DeduplicationCache(window_hours=24)

        fake_now = datetime(2024, 1, 1, 10, 0, 0)

        with patch("k2.ingestion.sequence_tracker.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value = fake_now

            # Add message
            dedup.is_duplicate("msg_001")

            # Check cache size
            initial_size = len(dedup._cache)

            # Advance by 5 minutes (less than 10-minute cleanup interval)
            mock_datetime.utcnow.return_value = fake_now + timedelta(minutes=5)
            dedup.is_duplicate("msg_002")

            # Cleanup should NOT have run
            assert len(dedup._cache) == initial_size + 1

    def test_deduplication_metrics(self):
        """Test that duplicate detection increments metrics"""
        dedup = DeduplicationCache(window_hours=24)

        with patch("k2.ingestion.sequence_tracker.metrics") as mock_metrics:
            # First message (not duplicate)
            dedup.is_duplicate("msg_001")
            assert mock_metrics.increment.call_count == 0

            # Second message (duplicate)
            dedup.is_duplicate("msg_001")

            # Verify metric was incremented
            mock_metrics.increment.assert_called_with("duplicates_detected_total")

    def test_large_cache_performance(self):
        """Test cache with large number of entries"""
        dedup = DeduplicationCache(window_hours=24)

        # Add 10K unique messages
        for i in range(10_000):
            result = dedup.is_duplicate(f"msg_{i:05d}")
            assert result is False  # First occurrence

        # Verify all are cached
        assert len(dedup._cache) == 10_000

        # Check random duplicates
        assert dedup.is_duplicate("msg_00500") is True
        assert dedup.is_duplicate("msg_05000") is True
        assert dedup.is_duplicate("msg_09999") is True

    def test_window_configuration(self):
        """Test that different window sizes work correctly"""
        # 1-hour window
        dedup_short = DeduplicationCache(window_hours=1)

        # 48-hour window
        dedup_long = DeduplicationCache(window_hours=48)

        # Both should accept first message
        assert dedup_short.is_duplicate("msg_001") is False
        assert dedup_long.is_duplicate("msg_001") is False

        # Both should detect duplicate
        assert dedup_short.is_duplicate("msg_001") is True
        assert dedup_long.is_duplicate("msg_001") is True


class TestSequenceState:
    """Test SequenceState dataclass."""

    def test_sequence_state_creation(self):
        """Test SequenceState can be created with all fields"""
        state = SequenceState(
            last_sequence=100,
            last_timestamp=datetime(2024, 1, 1, 10, 0, 0),
            total_messages=100,
            gap_count=5,
            out_of_order_count=2,
        )

        assert state.last_sequence == 100
        assert state.total_messages == 100
        assert state.gap_count == 5
        assert state.out_of_order_count == 2


class TestSequenceEvent:
    """Test SequenceEvent constants."""

    def test_sequence_event_constants(self):
        """Test that SequenceEvent constants are defined"""
        assert SequenceEvent.OK == "ok"
        assert SequenceEvent.SMALL_GAP == "small_gap"
        assert SequenceEvent.LARGE_GAP == "large_gap"
        assert SequenceEvent.RESET == "reset"
        assert SequenceEvent.OUT_OF_ORDER == "out_of_order"


# Integration tests
class TestSequenceTrackerIntegration:
    """Integration tests simulating real-world scenarios."""

    def test_market_open_session(self):
        """Test typical market open session"""
        tracker = SequenceTracker(gap_alert_threshold=100)

        # Pre-market: Few messages
        timestamp = datetime(2024, 1, 1, 9, 30, 0)
        for i in range(1, 11):
            result = tracker.check_sequence("ASX", "BHP", i, timestamp)
            assert result == SequenceEvent.OK

        # Market open: High volume with occasional gaps
        timestamp = datetime(2024, 1, 1, 10, 0, 0)
        seq = 11
        for _ in range(1000):
            tracker.check_sequence("ASX", "BHP", seq, timestamp)
            seq += 1 if _ % 100 != 99 else 2  # Small gap every 100 messages

        state = tracker.get_stats("ASX", "BHP")
        assert state.total_messages > 1000
        assert state.gap_count > 0  # Should have detected gaps

    def test_kafka_rebalance_scenario(self):
        """Test out-of-order delivery during Kafka partition rebalance"""
        tracker = SequenceTracker()
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        # Normal sequence
        for i in range(1, 101):
            tracker.check_sequence("ASX", "BHP", i, timestamp)

        # Kafka rebalances, messages arrive out of order
        tracker.check_sequence("ASX", "BHP", 105, timestamp)
        tracker.check_sequence("ASX", "BHP", 103, timestamp)
        tracker.check_sequence("ASX", "BHP", 102, timestamp)
        tracker.check_sequence("ASX", "BHP", 104, timestamp)
        tracker.check_sequence("ASX", "BHP", 101, timestamp)

        state = tracker.get_stats("ASX", "BHP")
        assert state.out_of_order_count == 4  # 103, 102, 104, 101 all out of order
        assert state.last_sequence == 105  # High watermark

    def test_weekend_gap(self):
        """Test handling of weekend gap (not a data loss, just market closed)"""
        tracker = SequenceTracker(gap_alert_threshold=1000)

        # Friday close
        friday = datetime(2024, 1, 5, 16, 0, 0)
        for i in range(1, 10001):
            tracker.check_sequence("ASX", "BHP", i, friday)

        # Monday open (sequence continues, but large time gap)
        monday = datetime(2024, 1, 8, 10, 0, 0)
        result = tracker.check_sequence("ASX", "BHP", 10001, monday)

        # Should be OK (sequence continues normally)
        assert result == SequenceEvent.OK


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
