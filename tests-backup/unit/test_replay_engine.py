"""Unit tests-backup for Replay Engine.

These tests-backup mock the PyIceberg catalog and DuckDB connection to test
replay logic without requiring actual Iceberg tables or S3 storage.
"""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from k2.query.replay import ReplayEngine, SnapshotInfo


@pytest.fixture
def mock_catalog():
    """Mock PyIceberg catalog."""
    with patch("k2.query.replay.load_catalog") as mock:
        catalog = MagicMock()
        mock.return_value = catalog
        yield mock, catalog


@pytest.fixture
def mock_query_engine():
    """Mock QueryEngine."""
    with patch("k2.query.replay.QueryEngine") as mock:
        engine = MagicMock()
        mock.return_value = engine
        yield mock, engine


@pytest.fixture
def engine(mock_catalog, mock_query_engine):
    """Create ReplayEngine with mocked dependencies."""
    engine = ReplayEngine(
        catalog_uri="http://localhost:8181",
        s3_endpoint="http://localhost:9000",
        s3_access_key="test_key",
        s3_secret_key="test_secret",
        warehouse_path="s3://test-warehouse/",
    )
    return engine


class TestReplayEngineInitialization:
    """Tests for ReplayEngine initialization."""

    def test_init_with_defaults(self, mock_catalog, mock_query_engine):
        """Test engine initializes with default config."""
        engine = ReplayEngine()

        # Should have created catalog
        mock, _ = mock_catalog
        mock.assert_called_once()

    def test_init_with_custom_config(self, mock_catalog, mock_query_engine):
        """Test engine initializes with custom config."""
        engine = ReplayEngine(
            catalog_uri="http://custom:8181",
            s3_endpoint="http://custom:9000",
            warehouse_path="s3://custom-bucket/",
        )

        assert engine.catalog_uri == "http://custom:8181"
        assert engine.s3_endpoint == "http://custom:9000"
        assert engine.warehouse_path == "s3://custom-bucket/"


class TestSnapshotInfo:
    """Tests for SnapshotInfo dataclass."""

    def test_snapshot_info_creation(self):
        """Test SnapshotInfo creation."""
        info = SnapshotInfo(
            snapshot_id=12345,
            timestamp_ms=1704067200000,  # 2024-01-01 00:00:00 UTC
            parent_id=12344,
            manifest_list="s3://bucket/manifest.avro",
            summary={"added-records": "100", "total-records": "1000"},
        )

        assert info.snapshot_id == 12345
        assert info.timestamp_ms == 1704067200000
        assert info.parent_id == 12344

    def test_snapshot_info_timestamp_property(self):
        """Test timestamp conversion."""
        info = SnapshotInfo(
            snapshot_id=1,
            timestamp_ms=1704067200000,
            parent_id=None,
            manifest_list="",
            summary={},
        )

        # Should be 2024-01-01 00:00:00 UTC
        assert info.timestamp.year == 2024
        assert info.timestamp.month == 1
        assert info.timestamp.day == 1

    def test_snapshot_info_records_properties(self):
        """Test record count properties."""
        info = SnapshotInfo(
            snapshot_id=1,
            timestamp_ms=1000,
            parent_id=None,
            manifest_list="",
            summary={"added-records": "50", "total-records": "150"},
        )

        assert info.added_records == 50
        assert info.total_records == 150

    def test_snapshot_info_empty_summary(self):
        """Test with empty summary."""
        info = SnapshotInfo(
            snapshot_id=1,
            timestamp_ms=1000,
            parent_id=None,
            manifest_list="",
            summary={},
        )

        assert info.added_records == 0
        assert info.total_records == 0


class TestListSnapshots:
    """Tests for snapshot listing."""

    def test_list_snapshots(self, engine, mock_catalog):
        """Test listing snapshots."""
        _, catalog = mock_catalog

        # Mock table with snapshots
        mock_table = MagicMock()
        mock_snapshot1 = MagicMock()
        mock_snapshot1.snapshot_id = 1
        mock_snapshot1.timestamp_ms = 2000000
        mock_snapshot1.parent_snapshot_id = None
        mock_snapshot1.manifest_list = "s3://bucket/m1.avro"
        mock_snapshot1.summary = MagicMock()
        mock_snapshot1.summary.get.return_value = "100"

        mock_snapshot2 = MagicMock()
        mock_snapshot2.snapshot_id = 2
        mock_snapshot2.timestamp_ms = 1000000
        mock_snapshot2.parent_snapshot_id = 1
        mock_snapshot2.manifest_list = "s3://bucket/m2.avro"
        mock_snapshot2.summary = MagicMock()
        mock_snapshot2.summary.get.return_value = "50"

        mock_table.metadata.snapshots = [mock_snapshot2, mock_snapshot1]
        catalog.load_table.return_value = mock_table

        snapshots = engine.list_snapshots()

        # Should be sorted newest first
        assert len(snapshots) == 2
        assert snapshots[0].timestamp_ms == 2000000
        assert snapshots[1].timestamp_ms == 1000000

    def test_list_snapshots_with_limit(self, engine, mock_catalog):
        """Test listing snapshots with limit."""
        _, catalog = mock_catalog

        mock_table = MagicMock()
        mock_table.metadata.snapshots = [
            MagicMock(
                snapshot_id=i,
                timestamp_ms=i * 1000,
                parent_snapshot_id=None,
                manifest_list="",
                summary=None,
            )
            for i in range(10)
        ]
        catalog.load_table.return_value = mock_table

        snapshots = engine.list_snapshots(limit=3)
        assert len(snapshots) == 3


class TestGetCurrentSnapshot:
    """Tests for current snapshot retrieval."""

    def test_get_current_snapshot(self, engine, mock_catalog):
        """Test getting current snapshot."""
        _, catalog = mock_catalog

        mock_table = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.snapshot_id = 123
        mock_snapshot.timestamp_ms = 1704067200000
        mock_snapshot.parent_snapshot_id = 122
        mock_snapshot.manifest_list = "s3://bucket/manifest.avro"
        mock_snapshot.summary = MagicMock()
        mock_snapshot.summary.get.return_value = "100"

        mock_table.metadata.current_snapshot.return_value = mock_snapshot
        catalog.load_table.return_value = mock_table

        current = engine.get_current_snapshot()

        assert current is not None
        assert current.snapshot_id == 123

    def test_get_current_snapshot_empty_table(self, engine, mock_catalog):
        """Test current snapshot on empty table."""
        _, catalog = mock_catalog

        mock_table = MagicMock()
        mock_table.metadata.current_snapshot.return_value = None
        catalog.load_table.return_value = mock_table

        current = engine.get_current_snapshot()
        assert current is None


class TestQueryAtSnapshot:
    """Tests for point-in-time queries."""

    def test_query_at_snapshot(self, engine, mock_query_engine):
        """Test querying at specific snapshot."""
        _, query_engine = mock_query_engine

        mock_df = Mock()
        mock_df.to_dict.return_value = [{"symbol": "BHP", "price": 36.50}]
        query_engine.connection.execute.return_value.fetchdf.return_value = mock_df
        query_engine._get_table_path.return_value = "s3://warehouse/market_data/trades"

        result = engine.query_at_snapshot(snapshot_id=12345, symbol="BHP", limit=100)

        assert len(result) == 1
        assert result[0]["symbol"] == "BHP"

    def test_query_at_snapshot_with_exchange(self, engine, mock_query_engine):
        """Test snapshot query with exchange filter."""
        _, query_engine = mock_query_engine

        mock_df = Mock()
        mock_df.to_dict.return_value = []
        query_engine.connection.execute.return_value.fetchdf.return_value = mock_df
        query_engine._get_table_path.return_value = "s3://warehouse/market_data/trades"

        engine.query_at_snapshot(snapshot_id=12345, symbol="BHP", exchange="ASX")

        # Check query contains filters
        call_args = query_engine.connection.execute.call_args
        query = str(call_args)
        assert "version = '12345'" in query
        assert "symbol = 'BHP'" in query
        assert "exchange = 'ASX'" in query


class TestColdStartReplay:
    """Tests for historical data replay."""

    def test_cold_start_replay_basic(self, engine, mock_query_engine):
        """Test basic cold start replay."""
        _, query_engine = mock_query_engine
        query_engine._get_table_path.return_value = "s3://warehouse/market_data/trades"

        # Mock count query
        count_result = MagicMock()
        count_result.fetchone.return_value = (25,)

        # Mock data query
        mock_df = Mock()
        mock_df.to_dict.side_effect = [
            [{"symbol": "BHP", "price": 36.0 + i} for i in range(10)],
            [{"symbol": "BHP", "price": 46.0 + i} for i in range(10)],
            [{"symbol": "BHP", "price": 56.0 + i} for i in range(5)],
        ]

        query_engine.connection.execute.return_value = count_result
        query_engine.connection.execute.return_value.fetchdf.return_value = mock_df

        batches = list(engine.cold_start_replay(symbol="BHP", batch_size=10))

        # Should yield batches
        assert len(batches) >= 1

    def test_cold_start_replay_empty(self, engine, mock_query_engine):
        """Test replay with no data."""
        _, query_engine = mock_query_engine
        query_engine._get_table_path.return_value = "s3://warehouse/market_data/trades"

        # Mock empty count
        count_result = MagicMock()
        count_result.fetchone.return_value = (0,)
        query_engine.connection.execute.return_value = count_result

        batches = list(engine.cold_start_replay(symbol="NONEXISTENT"))
        assert len(batches) == 0

    def test_cold_start_replay_with_time_range(self, engine, mock_query_engine):
        """Test replay with time range filter."""
        _, query_engine = mock_query_engine
        query_engine._get_table_path.return_value = "s3://warehouse/market_data/trades"

        count_result = MagicMock()
        count_result.fetchone.return_value = (0,)
        query_engine.connection.execute.return_value = count_result

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 31)

        list(engine.cold_start_replay(start_time=start, end_time=end))

        # Check time filters in query
        call_args = query_engine.connection.execute.call_args
        query = str(call_args)
        assert "exchange_timestamp >=" in query
        assert "exchange_timestamp <=" in query


class TestRewindToTimestamp:
    """Tests for finding snapshots by timestamp."""

    def test_rewind_to_timestamp(self, engine, mock_catalog):
        """Test finding snapshot at timestamp."""
        _, catalog = mock_catalog

        mock_table = MagicMock()
        mock_table.metadata.snapshots = [
            MagicMock(
                snapshot_id=3,
                timestamp_ms=3000000,  # 3000 seconds
                parent_snapshot_id=2,
                manifest_list="",
                summary=None,
            ),
            MagicMock(
                snapshot_id=2,
                timestamp_ms=2000000,  # 2000 seconds
                parent_snapshot_id=1,
                manifest_list="",
                summary=None,
            ),
            MagicMock(
                snapshot_id=1,
                timestamp_ms=1000000,  # 1000 seconds
                parent_snapshot_id=None,
                manifest_list="",
                summary=None,
            ),
        ]
        catalog.load_table.return_value = mock_table

        # Find snapshot at time between snapshot 2 and 3
        # Target = 2500 seconds = 2500000 ms
        target = datetime.fromtimestamp(2500)
        result = engine.rewind_to_timestamp(target)

        # Should return snapshot 2 (latest before target)
        assert result is not None
        assert result.snapshot_id == 2

    def test_rewind_to_timestamp_no_match(self, engine, mock_catalog):
        """Test rewind with no matching snapshot."""
        _, catalog = mock_catalog

        mock_table = MagicMock()
        mock_table.metadata.snapshots = [
            MagicMock(
                snapshot_id=1,
                timestamp_ms=2000000,
                parent_snapshot_id=None,
                manifest_list="",
                summary=None,
            ),
        ]
        catalog.load_table.return_value = mock_table

        # Target before any snapshot
        target = datetime.fromtimestamp(0.5)
        result = engine.rewind_to_timestamp(target)

        assert result is None


class TestReplayStats:
    """Tests for replay statistics."""

    def test_get_replay_stats(self, engine, mock_catalog, mock_query_engine):
        """Test getting replay statistics."""
        _, catalog = mock_catalog
        _, query_engine = mock_query_engine

        mock_table = MagicMock()
        mock_table.metadata.snapshots = [
            MagicMock(
                snapshot_id=1,
                timestamp_ms=1000,
                parent_snapshot_id=None,
                manifest_list="",
                summary=None,
            ),
        ]
        catalog.load_table.return_value = mock_table

        query_engine._get_table_path.return_value = "s3://warehouse/market_data/trades"
        query_engine.connection.execute.return_value.fetchone.return_value = (
            1000,  # total_records
            datetime(2024, 1, 1),  # min_timestamp
            datetime(2024, 12, 31),  # max_timestamp
            50,  # unique_symbols
            3,  # unique_exchanges
        )

        stats = engine.get_replay_stats()

        assert stats["total_records"] == 1000
        assert stats["unique_symbols"] == 50
        assert stats["snapshot_count"] == 1


class TestContextManager:
    """Tests for context manager functionality."""

    def test_context_manager(self, mock_catalog, mock_query_engine):
        """Test engine works as context manager."""
        _, query_engine = mock_query_engine

        with ReplayEngine() as engine:
            pass

        # QueryEngine should be closed
        query_engine.close.assert_called()

    def test_close(self, engine, mock_query_engine):
        """Test explicit close."""
        _, query_engine = mock_query_engine

        engine.close()

        query_engine.close.assert_called_once()
