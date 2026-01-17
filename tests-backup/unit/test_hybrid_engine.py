"""Unit tests-backup for HybridQueryEngine - Unified Kafka + Iceberg queries.

Test Coverage:
- Query routing logic (Kafka only, Iceberg only, both)
- Deduplication by message_id
- Time range handling
- Error handling and graceful degradation
- Statistics tracking
- Edge cases and integration scenarios
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from k2.query.hybrid_engine import HybridQueryEngine


class TestHybridQueryEngineInitialization:
    """Test HybridQueryEngine initialization."""

    def test_initialization_with_engines(self):
        """Test hybrid engine initializes with provided engines."""
        iceberg_engine = MagicMock()
        kafka_tail = MagicMock()

        hybrid = HybridQueryEngine(
            iceberg_engine=iceberg_engine,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        assert hybrid.iceberg == iceberg_engine
        assert hybrid.kafka_tail == kafka_tail
        assert hybrid.commit_lag == timedelta(seconds=120)

    def test_initialization_custom_commit_lag(self):
        """Test custom commit lag configuration."""
        hybrid = HybridQueryEngine(
            iceberg_engine=MagicMock(),
            kafka_tail=MagicMock(),
            commit_lag_seconds=180,  # 3 minutes
        )

        assert hybrid.commit_lag == timedelta(seconds=180)


class TestHybridQueryEngineRouting:
    """Test query routing logic."""

    @pytest.fixture(scope="class")
    def hybrid_engine(self):
        """Create HybridQueryEngine with mocked dependencies.

        Memory optimization: Class-scoped to reuse across tests-backup.
        Mock data is kept minimal (2 records per source).
        """
        iceberg = MagicMock(spec_set=["query_trades"])
        kafka_tail = MagicMock(spec_set=["query", "get_stats"])

        # Mock responses - minimal data (2 records each)
        iceberg.query_trades.return_value = [
            {"message_id": "ice-001", "price": 50000, "timestamp": "2024-01-01T10:00:00Z"},
            {"message_id": "ice-002", "price": 50100, "timestamp": "2024-01-01T10:01:00Z"},
        ]

        kafka_tail.query.return_value = [
            {"message_id": "kaf-001", "price": 50200, "timestamp": "2024-01-01T10:02:00Z"},
            {"message_id": "kaf-002", "price": 50300, "timestamp": "2024-01-01T10:03:00Z"},
        ]

        engine = HybridQueryEngine(
            iceberg_engine=iceberg,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        yield engine

        # Explicit cleanup
        del iceberg
        del kafka_tail

    def test_query_historical_only_uses_iceberg(self, hybrid_engine):
        """Test querying historical data uses only Iceberg."""
        # Reset mocks from previous tests-backup
        hybrid_engine.iceberg.reset_mock()
        hybrid_engine.kafka_tail.reset_mock()

        now = datetime.now(UTC)
        # Query 1 hour ago (all data committed)
        start = now - timedelta(hours=1, minutes=10)
        end = now - timedelta(hours=1)

        results = hybrid_engine.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=start,
            end_time=end,
            limit=1000,
        )

        # Should only call Iceberg
        hybrid_engine.iceberg.query_trades.assert_called_once()
        hybrid_engine.kafka_tail.query.assert_not_called()

        # Results from Iceberg only
        assert len(results) == 2

    def test_query_recent_only_uses_kafka(self, hybrid_engine):
        """Test querying very recent data uses only Kafka."""
        # Reset mocks from previous tests-backup
        hybrid_engine.iceberg.reset_mock()
        hybrid_engine.kafka_tail.reset_mock()

        now = datetime.now(UTC)
        # Query last 1 minute (not yet committed to Iceberg)
        start = now - timedelta(minutes=1)
        end = now

        results = hybrid_engine.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=start,
            end_time=end,
            limit=1000,
        )

        # Should only call Kafka
        hybrid_engine.kafka_tail.query.assert_called_once()
        hybrid_engine.iceberg.query_trades.assert_not_called()

        # Results from Kafka only
        assert len(results) == 2

    def test_query_spanning_both_sources(self, hybrid_engine):
        """Test querying data spanning both Kafka and Iceberg."""
        # Reset mocks from previous tests-backup
        hybrid_engine.iceberg.reset_mock()
        hybrid_engine.kafka_tail.reset_mock()

        now = datetime.now(UTC)
        # Query last 15 minutes (commit lag is 2 minutes)
        start = now - timedelta(minutes=15)
        end = now

        results = hybrid_engine.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=start,
            end_time=end,
            limit=1000,
        )

        # Should call both sources
        hybrid_engine.iceberg.query_trades.assert_called_once()
        hybrid_engine.kafka_tail.query.assert_called_once()

        # Results from both sources (2 Iceberg + 2 Kafka = 4)
        assert len(results) == 4


class TestHybridQueryEngineDeduplication:
    """Test deduplication logic."""

    @pytest.fixture(scope="class")
    def hybrid_with_duplicates(self):
        """Create HybridQueryEngine with overlapping data.

        Memory optimization: Class-scoped, minimal test data.
        """
        iceberg = MagicMock(spec_set=["query_trades"])
        kafka_tail = MagicMock(spec_set=["query", "get_stats"])

        # Mock overlapping messages (same message_id) - minimal set
        iceberg.query_trades.return_value = [
            {
                "message_id": "msg-001",
                "price": 50000,
                "timestamp": "2024-01-01T10:00:00Z",
                "_source": "iceberg",
            },
            {
                "message_id": "msg-002",
                "price": 50100,
                "timestamp": "2024-01-01T10:01:00Z",
                "_source": "iceberg",
            },
            {
                "message_id": "overlap",
                "price": 50200,
                "timestamp": "2024-01-01T10:02:00Z",
                "_source": "iceberg",
            },
        ]

        kafka_tail.query.return_value = [
            {
                "message_id": "overlap",
                "price": 50200,
                "timestamp": "2024-01-01T10:02:00Z",
                "_source": "kafka",
            },
            {
                "message_id": "msg-003",
                "price": 50300,
                "timestamp": "2024-01-01T10:03:00Z",
                "_source": "kafka",
            },
        ]

        engine = HybridQueryEngine(
            iceberg_engine=iceberg,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        yield engine

        # Explicit cleanup
        del iceberg
        del kafka_tail

    def test_deduplicates_by_message_id(self, hybrid_with_duplicates):
        """Test deduplication removes duplicate message_ids."""
        now = datetime.now(UTC)

        results = hybrid_with_duplicates.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=15),
            end_time=now,
            limit=1000,
        )

        # Should have 4 unique messages (5 total - 1 duplicate)
        message_ids = [r["message_id"] for r in results]
        assert len(message_ids) == 4
        assert len(set(message_ids)) == 4  # All unique
        assert "overlap" in message_ids  # Duplicate kept (first occurrence)

    def test_deduplication_prefers_iceberg(self, hybrid_with_duplicates):
        """Test deduplication prefers Iceberg data over Kafka."""
        now = datetime.now(UTC)

        results = hybrid_with_duplicates.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=15),
            end_time=now,
            limit=1000,
        )

        # Find the 'overlap' message
        overlap_msg = next(r for r in results if r["message_id"] == "overlap")

        # Should not have _source tag in final results (cleaned up)
        assert "_source" not in overlap_msg


class TestHybridQueryEngineSorting:
    """Test result sorting."""

    @pytest.fixture(scope="class")
    def hybrid_with_unsorted(self):
        """Create HybridQueryEngine with unsorted data.

        Memory optimization: Class-scoped fixture with minimal data.
        """
        iceberg = MagicMock(spec_set=["query_trades"])
        kafka_tail = MagicMock(spec_set=["query", "get_stats"])

        # Mock data with different timestamps - minimal set
        iceberg.query_trades.return_value = [
            {"message_id": "msg-003", "timestamp": "2024-01-01T10:03:00Z"},
            {"message_id": "msg-001", "timestamp": "2024-01-01T10:01:00Z"},
        ]

        kafka_tail.query.return_value = [
            {"message_id": "msg-004", "timestamp": "2024-01-01T10:04:00Z"},
            {"message_id": "msg-002", "timestamp": "2024-01-01T10:02:00Z"},
        ]

        engine = HybridQueryEngine(
            iceberg_engine=iceberg,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        yield engine

        # Explicit cleanup
        del iceberg
        del kafka_tail

    def test_results_sorted_by_timestamp(self, hybrid_with_unsorted):
        """Test results are sorted by timestamp."""
        now = datetime.now(UTC)

        results = hybrid_with_unsorted.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=15),
            end_time=now,
            limit=1000,
        )

        # Should be sorted by timestamp
        timestamps = [r["timestamp"] for r in results]
        assert timestamps == sorted(timestamps)

        # Verify order
        message_ids = [r["message_id"] for r in results]
        assert message_ids == ["msg-001", "msg-002", "msg-003", "msg-004"]


class TestHybridQueryEngineErrorHandling:
    """Test error handling and graceful degradation."""

    @pytest.fixture(scope="class")
    def hybrid_with_failures(self):
        """Create HybridQueryEngine with failing sources.

        Memory optimization: Class-scoped to reuse across all 3 error tests-backup.
        """
        iceberg = MagicMock(spec_set=["query_trades"])
        kafka_tail = MagicMock(spec_set=["query", "get_stats"])

        engine = HybridQueryEngine(
            iceberg_engine=iceberg,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        yield engine

        # Explicit cleanup
        del iceberg
        del kafka_tail

    def test_iceberg_failure_returns_kafka_only(self, hybrid_with_failures):
        """Test Iceberg failure still returns Kafka data."""
        # Reset mocks from previous tests-backup
        hybrid_with_failures.iceberg.reset_mock()
        hybrid_with_failures.kafka_tail.reset_mock()
        # Clear side effects from previous tests-backup
        hybrid_with_failures.iceberg.query_trades.side_effect = None
        hybrid_with_failures.kafka_tail.query.side_effect = None

        now = datetime.now(UTC)

        # Iceberg fails
        hybrid_with_failures.iceberg.query_trades.side_effect = Exception("Iceberg error")

        # Kafka succeeds
        hybrid_with_failures.kafka_tail.query.return_value = [
            {"message_id": "kaf-001", "price": 50000, "timestamp": "2024-01-01T10:00:00Z"},
        ]

        results = hybrid_with_failures.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=15),
            end_time=now,
            limit=1000,
        )

        # Should return Kafka data only (degraded mode)
        assert len(results) == 1
        assert results[0]["message_id"] == "kaf-001"

    def test_kafka_failure_returns_iceberg_only(self, hybrid_with_failures):
        """Test Kafka failure still returns Iceberg data."""
        # Reset mocks from previous tests-backup
        hybrid_with_failures.iceberg.reset_mock()
        hybrid_with_failures.kafka_tail.reset_mock()
        # Clear side effects from previous tests-backup
        hybrid_with_failures.iceberg.query_trades.side_effect = None
        hybrid_with_failures.kafka_tail.query.side_effect = None

        now = datetime.now(UTC)

        # Kafka fails
        hybrid_with_failures.kafka_tail.query.side_effect = Exception("Kafka error")

        # Iceberg succeeds
        hybrid_with_failures.iceberg.query_trades.return_value = [
            {"message_id": "ice-001", "price": 50000, "timestamp": "2024-01-01T10:00:00Z"},
        ]

        results = hybrid_with_failures.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=15),
            end_time=now,
            limit=1000,
        )

        # Should return Iceberg data only (degraded mode)
        assert len(results) == 1
        assert results[0]["message_id"] == "ice-001"

    def test_both_sources_fail_returns_empty(self, hybrid_with_failures):
        """Test both sources failing returns empty results."""
        # Reset mocks from previous tests-backup
        hybrid_with_failures.iceberg.reset_mock()
        hybrid_with_failures.kafka_tail.reset_mock()
        # Clear side effects from previous tests-backup
        hybrid_with_failures.iceberg.query_trades.side_effect = None
        hybrid_with_failures.kafka_tail.query.side_effect = None

        now = datetime.now(UTC)

        # Both fail
        hybrid_with_failures.iceberg.query_trades.side_effect = Exception("Iceberg error")
        hybrid_with_failures.kafka_tail.query.side_effect = Exception("Kafka error")

        results = hybrid_with_failures.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=15),
            end_time=now,
            limit=1000,
        )

        # Should return empty (both sources failed)
        assert len(results) == 0


class TestHybridQueryEngineLimit:
    """Test limit parameter."""

    @pytest.fixture(scope="class")
    def hybrid_with_many_results(self):
        """Create HybridQueryEngine with many results.

        Memory optimization: Reduced from 100 to 20 total records.
        Class-scoped to avoid recreating for each test.
        """
        iceberg = MagicMock(spec_set=["query_trades"])
        kafka_tail = MagicMock(spec_set=["query", "get_stats"])

        # Mock results - REDUCED from 50+50 to 10+10 for memory efficiency
        # Still tests-backup limit functionality with minimal overhead
        iceberg.query_trades.return_value = [
            {"message_id": f"ice-{i:03d}", "timestamp": f"2024-01-01T10:{i:02d}:00Z"}
            for i in range(10)
        ]

        kafka_tail.query.return_value = [
            {"message_id": f"kaf-{i:03d}", "timestamp": f"2024-01-01T11:{i:02d}:00Z"}
            for i in range(10)
        ]

        engine = HybridQueryEngine(
            iceberg_engine=iceberg,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        yield engine

        # Explicit cleanup
        del iceberg
        del kafka_tail

    def test_respects_limit_parameter(self, hybrid_with_many_results):
        """Test query respects limit parameter."""
        now = datetime.now(UTC)

        results = hybrid_with_many_results.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now - timedelta(minutes=15),
            end_time=now,
            limit=5,  # Limit to 5 (reduced from 10 for memory efficiency)
        )

        # Should return only 5 results
        assert len(results) == 5


class TestHybridQueryEngineStatistics:
    """Test statistics tracking."""

    @pytest.fixture(scope="class")
    def hybrid_stats_engine(self):
        """Create HybridQueryEngine for statistics tests-backup.

        Memory optimization: Class-scoped fixture with explicit cleanup.
        """
        iceberg = MagicMock(spec_set=["query_trades"])
        kafka_tail = MagicMock(spec_set=["query", "get_stats"])

        kafka_tail.get_stats.return_value = {
            "messages_received": 1000,
            "buffer_size": 500,
        }

        engine = HybridQueryEngine(
            iceberg_engine=iceberg,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        yield engine

        # Explicit cleanup
        del iceberg
        del kafka_tail

    def test_get_query_stats_returns_statistics(self, hybrid_stats_engine):
        """Test get_query_stats returns combined statistics."""
        stats = hybrid_stats_engine.get_query_stats()

        assert "iceberg" in stats
        assert "kafka_tail" in stats
        assert stats["commit_lag_seconds"] == 120
        assert stats["kafka_tail"]["messages_received"] == 1000


class TestHybridQueryEngineEdgeCases:
    """Test edge cases."""

    @pytest.fixture(scope="class")
    def hybrid_engine(self):
        """Create basic HybridQueryEngine.

        Memory optimization: Class-scoped fixture with spec_set mocks.
        """
        iceberg = MagicMock(spec_set=["query_trades"])
        kafka_tail = MagicMock(spec_set=["query", "get_stats"])

        engine = HybridQueryEngine(
            iceberg_engine=iceberg,
            kafka_tail=kafka_tail,
            commit_lag_seconds=120,
        )

        yield engine

        # Explicit cleanup
        del iceberg
        del kafka_tail

    def test_query_with_none_end_time_uses_now(self, hybrid_engine):
        """Test query with None end_time defaults to now."""
        # Reset mocks to clear history from previous tests-backup
        hybrid_engine.iceberg.reset_mock()
        hybrid_engine.kafka_tail.reset_mock()

        hybrid_engine.iceberg.query_trades.return_value = []
        hybrid_engine.kafka_tail.query.return_value = []

        now_before = datetime.now(UTC)

        hybrid_engine.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=now_before - timedelta(minutes=5),
            end_time=None,  # Should default to now
            limit=1000,
        )

        # Should use approximate current time
        # Verify Kafka was called (since end_time would be recent)
        assert hybrid_engine.kafka_tail.query.called

    def test_query_with_naive_datetime_converts_to_utc(self, hybrid_engine):
        """Test query with naive datetime converts to UTC."""
        # Reset mocks to clear history from previous tests-backup
        hybrid_engine.iceberg.reset_mock()
        hybrid_engine.kafka_tail.reset_mock()

        hybrid_engine.iceberg.query_trades.return_value = []
        hybrid_engine.kafka_tail.query.return_value = []

        # Use naive datetime (no timezone)
        naive_time = datetime(2024, 1, 1, 10, 0, 0)

        results = hybrid_engine.query_trades(
            symbol="BTCUSDT",
            exchange="binance",
            start_time=naive_time,
            end_time=naive_time + timedelta(hours=1),
            limit=1000,
        )

        # Should not raise error (timezone added automatically)
        assert results is not None


@pytest.mark.integration
class TestHybridQueryEngineFactory:
    """Test factory function."""

    @patch("k2.query.kafka_tail.create_kafka_tail")
    @patch("k2.query.hybrid_engine.QueryEngine")
    def test_create_hybrid_engine_initializes_components(self, mock_engine, mock_kafka):
        """Test create_hybrid_engine factory creates all components."""
        from k2.query.hybrid_engine import create_hybrid_engine

        hybrid = create_hybrid_engine(commit_lag_seconds=180)

        # Should create Iceberg engine
        mock_engine.assert_called_once()

        # Should create Kafka tail
        mock_kafka.assert_called_once_with(buffer_minutes=5)

        # Should configure commit lag
        assert hybrid.commit_lag == timedelta(seconds=180)

        # Explicit cleanup of factory-created objects
        del hybrid
