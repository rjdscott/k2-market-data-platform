"""Tests for P1.5: Iceberg Transaction Logging Enhancement.

This test suite verifies:
1. Snapshot IDs captured before and after transactions
2. Transaction metrics (success/failure counters, row count histograms)
3. Enhanced error logging with snapshot context
4. Transaction duration tracking
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from k2.storage.writer import IcebergWriter


@pytest.fixture
def mock_catalog():
    """Mock Iceberg catalog with table and snapshot tracking."""
    catalog = MagicMock()

    # Mock table
    table = MagicMock()

    # Mock snapshots (before and after)
    snapshot_before = MagicMock()
    snapshot_before.snapshot_id = 1001
    snapshot_before.sequence_number = 50

    snapshot_after = MagicMock()
    snapshot_after.snapshot_id = 1002
    snapshot_after.sequence_number = 51
    snapshot_after.summary = {
        "total-records": "5000",
        "total-data-files": "10",
        "total-delete-files": "0",
        "added-data-files": "1",
        "added-records": "100",
        "added-files-size": "12345",
    }

    # Setup current_snapshot() to return before, then after
    table.current_snapshot.side_effect = [snapshot_before, snapshot_after]

    catalog.load_table.return_value = table
    return catalog, table, snapshot_before, snapshot_after


@pytest.fixture
def sample_trades_v2():
    """Sample v2 trade records."""
    return [
        {
            "message_id": "msg-001",
            "trade_id": "trade-001",
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "asset_class": "crypto",
            "timestamp": datetime(2024, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
            "price": Decimal("50000.12345678"),
            "quantity": Decimal("1.5"),
            "currency": "USDT",
            "side": "buy",
            "trade_conditions": [],
            "source_sequence": 123,
            "ingestion_timestamp": datetime(2024, 1, 10, 10, 0, 1, tzinfo=timezone.utc),
            "platform_sequence": 456,
            "vendor_data": {"venue": "SPOT"},
            "is_sample_data": False,
        },
        {
            "message_id": "msg-002",
            "trade_id": "trade-002",
            "symbol": "ETHUSDT",
            "exchange": "binance",
            "asset_class": "crypto",
            "timestamp": datetime(2024, 1, 10, 10, 0, 1, tzinfo=timezone.utc),
            "price": Decimal("3000.12345678"),
            "quantity": Decimal("2.0"),
            "currency": "USDT",
            "side": "sell",
            "trade_conditions": [],
            "source_sequence": 124,
            "ingestion_timestamp": datetime(2024, 1, 10, 10, 0, 2, tzinfo=timezone.utc),
            "platform_sequence": 457,
            "vendor_data": {"venue": "SPOT"},
            "is_sample_data": False,
        },
    ]


class TestTransactionLogging:
    """Test transaction logging enhancements (P1.5)."""

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_snapshot_ids_captured_before_and_after(
        self, mock_logger, mock_metrics, mock_load_catalog, mock_catalog, sample_trades_v2
    ):
        """Test that snapshot IDs are captured before and after write."""
        catalog, table, snapshot_before, snapshot_after = mock_catalog
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")
        writer.write_trades(sample_trades_v2, exchange="binance", asset_class="crypto")

        # Verify current_snapshot() called twice (before and after)
        assert table.current_snapshot.call_count == 2

        # Verify logging includes both snapshots
        log_call = mock_logger.info.call_args_list[0]
        assert log_call[0][0] == "Iceberg transaction committed"
        assert log_call[1]["snapshot_id_before"] == 1001
        assert log_call[1]["snapshot_id_after"] == 1002
        assert log_call[1]["sequence_number_before"] == 50
        assert log_call[1]["sequence_number_after"] == 51

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_transaction_success_metrics_recorded(
        self, mock_logger, mock_metrics, mock_load_catalog, mock_catalog, sample_trades_v2
    ):
        """Test that successful transactions are tracked in metrics."""
        catalog, table, _, _ = mock_catalog
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")
        writer.write_trades(sample_trades_v2, exchange="binance", asset_class="crypto")

        # Verify iceberg_transactions_total increment (success)
        # Note: After TD-003 fix, only 'table' label is passed (standard labels added automatically)
        transaction_metric_calls = [
            call
            for call in mock_metrics.increment.call_args_list
            if call[0][0] == "iceberg_transactions_total"
        ]
        assert len(transaction_metric_calls) == 1
        assert transaction_metric_calls[0][1]["labels"]["table"] == "trades"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_transaction_row_count_histogram_recorded(
        self, mock_logger, mock_metrics, mock_load_catalog, mock_catalog, sample_trades_v2
    ):
        """Test that transaction row counts are tracked in histogram."""
        catalog, table, _, _ = mock_catalog
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")
        writer.write_trades(sample_trades_v2, exchange="binance", asset_class="crypto")

        # Verify iceberg_transaction_rows histogram
        histogram_calls = [
            call
            for call in mock_metrics.histogram.call_args_list
            if call[0][0] == "iceberg_transaction_rows"
        ]
        assert len(histogram_calls) == 1
        assert histogram_calls[0][1]["value"] == 2  # 2 trades
        assert histogram_calls[0][1]["labels"]["table"] == "trades"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_transaction_duration_tracked(
        self, mock_logger, mock_metrics, mock_load_catalog, mock_catalog, sample_trades_v2
    ):
        """Test that transaction duration is calculated and logged."""
        catalog, table, _, _ = mock_catalog
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")
        writer.write_trades(sample_trades_v2, exchange="binance", asset_class="crypto")

        # Verify transaction_duration_ms is logged
        log_call = mock_logger.info.call_args_list[0]
        assert "transaction_duration_ms" in log_call[1]
        assert isinstance(log_call[1]["transaction_duration_ms"], float)
        assert log_call[1]["transaction_duration_ms"] >= 0

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_failed_transaction_logging_with_snapshot_context(
        self, mock_logger, mock_metrics, mock_load_catalog
    ):
        """Test that failed transactions log snapshot context."""
        catalog = MagicMock()
        table = MagicMock()

        # Mock snapshot before (transaction fails mid-write)
        snapshot_before = MagicMock()
        snapshot_before.snapshot_id = 2001
        snapshot_before.sequence_number = 100

        table.current_snapshot.return_value = snapshot_before
        table.append.side_effect = Exception("Simulated write failure")

        catalog.load_table.return_value = table
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")

        sample_trades = [
            {
                "message_id": "msg-fail",
                "trade_id": "trade-fail",
                "symbol": "FAILCOIN",
                "exchange": "test",
                "asset_class": "crypto",
                "timestamp": datetime(2024, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
                "price": Decimal("100.0"),
                "quantity": Decimal("1.0"),
                "currency": "USD",
                "side": "buy",
                "trade_conditions": [],
                "source_sequence": 1,
                "ingestion_timestamp": datetime(2024, 1, 10, 10, 0, 1, tzinfo=timezone.utc),
                "platform_sequence": 1,
                "vendor_data": None,
                "is_sample_data": False,
            }
        ]

        with pytest.raises(Exception, match="Simulated write failure"):
            writer.write_trades(sample_trades, exchange="test", asset_class="crypto")

        # Verify error logging includes snapshot context
        error_log = mock_logger.error.call_args_list[0]
        assert error_log[0][0] == "Iceberg transaction failed"
        assert error_log[1]["snapshot_id_before"] == 2001
        assert error_log[1]["sequence_number_before"] == 100
        assert error_log[1]["error"] == "Simulated write failure"
        assert error_log[1]["error_type"] == "Exception"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_failed_transaction_metrics_recorded(
        self, mock_logger, mock_metrics, mock_load_catalog
    ):
        """Test that failed transactions increment failure counter."""
        catalog = MagicMock()
        table = MagicMock()

        snapshot_before = MagicMock()
        snapshot_before.snapshot_id = 3001
        snapshot_before.sequence_number = 200

        table.current_snapshot.return_value = snapshot_before
        table.append.side_effect = Exception("Write failed")

        catalog.load_table.return_value = table
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")

        sample_trades = [
            {
                "message_id": "msg-fail-2",
                "trade_id": "trade-fail-2",
                "symbol": "FAILCOIN2",
                "exchange": "test",
                "asset_class": "crypto",
                "timestamp": datetime(2024, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
                "price": Decimal("100.0"),
                "quantity": Decimal("1.0"),
                "currency": "USD",
                "side": "buy",
                "trade_conditions": [],
                "source_sequence": 1,
                "ingestion_timestamp": datetime(2024, 1, 10, 10, 0, 1, tzinfo=timezone.utc),
                "platform_sequence": 1,
                "vendor_data": None,
                "is_sample_data": False,
            }
        ]

        with pytest.raises(Exception):
            writer.write_trades(sample_trades, exchange="test", asset_class="crypto")

        # Verify iceberg_transactions_total increment (failed)
        # Note: After TD-003 fix, only 'table' label is passed (standard labels added automatically)
        transaction_metric_calls = [
            call
            for call in mock_metrics.increment.call_args_list
            if call[0][0] == "iceberg_transactions_total"
        ]
        # Should have 1 transaction metric (failed transactions still counted)
        assert len(transaction_metric_calls) == 1
        assert transaction_metric_calls[0][1]["labels"]["table"] == "trades"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_handles_null_snapshot_before(
        self, mock_logger, mock_metrics, mock_load_catalog, sample_trades_v2
    ):
        """Test transaction logging when no snapshot exists before write (first write)."""
        catalog = MagicMock()
        table = MagicMock()

        # First call: no snapshot (returns None)
        # Second call: snapshot after first write
        snapshot_after = MagicMock()
        snapshot_after.snapshot_id = 1
        snapshot_after.sequence_number = 1
        snapshot_after.summary = {
            "total-records": "2",
            "total-data-files": "1",
            "added-records": "2",
        }

        table.current_snapshot.side_effect = [None, snapshot_after]

        catalog.load_table.return_value = table
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")
        writer.write_trades(sample_trades_v2, exchange="binance", asset_class="crypto")

        # Verify logging handles None snapshot_before
        log_call = mock_logger.info.call_args_list[0]
        assert log_call[1]["snapshot_id_before"] is None
        assert log_call[1]["snapshot_id_after"] == 1
        assert log_call[1]["sequence_number_before"] is None
        assert log_call[1]["sequence_number_after"] == 1

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.metrics")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_for_quotes(
        self, mock_logger, mock_metrics, mock_load_catalog, mock_catalog
    ):
        """Test transaction logging works for quotes (not just trades)."""
        catalog, table, snapshot_before, snapshot_after = mock_catalog
        mock_load_catalog.return_value = catalog

        writer = IcebergWriter(schema_version="v2")

        sample_quotes = [
            {
                "message_id": "quote-001",
                "quote_id": "q-001",
                "symbol": "BTCUSDT",
                "exchange": "binance",
                "asset_class": "crypto",
                "timestamp": datetime(2024, 1, 10, 10, 0, 0, tzinfo=timezone.utc),
                "bid_price": Decimal("50000.0"),
                "bid_quantity": Decimal("1.0"),
                "ask_price": Decimal("50001.0"),
                "ask_quantity": Decimal("1.5"),
                "currency": "USDT",
                "source_sequence": 123,
                "ingestion_timestamp": datetime(2024, 1, 10, 10, 0, 1, tzinfo=timezone.utc),
                "platform_sequence": 456,
                "vendor_data": None,
            }
        ]

        writer.write_quotes(sample_quotes, exchange="binance", asset_class="crypto")

        # Verify logging for quotes includes transaction metadata
        log_call = mock_logger.info.call_args_list[0]
        assert log_call[0][0] == "Iceberg transaction committed"
        assert log_call[1]["snapshot_id_before"] == 1001
        assert log_call[1]["snapshot_id_after"] == 1002

        # Verify quotes transaction metrics
        # Note: After TD-003 fix, only 'table' label is passed (standard labels added automatically)
        transaction_metric_calls = [
            call
            for call in mock_metrics.increment.call_args_list
            if call[0][0] == "iceberg_transactions_total"
        ]
        assert len(transaction_metric_calls) == 1
        assert transaction_metric_calls[0][1]["labels"]["table"] == "quotes"
