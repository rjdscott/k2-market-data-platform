"""Unit tests-backup for storage layer.

Tests the Iceberg catalog manager and writer logic without requiring
external services (Docker).
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest


@pytest.mark.unit
class TestIcebergCatalogManager:
    """Test Iceberg catalog manager logic."""

    @patch("k2.storage.catalog.load_catalog")
    def test_catalog_initialization(self, mock_load_catalog):
        """Should initialize catalog with correct configuration."""
        from k2.storage.catalog import IcebergCatalogManager

        mock_catalog = Mock()
        mock_load_catalog.return_value = mock_catalog

        manager = IcebergCatalogManager(
            catalog_uri="http://test:8181",
            s3_endpoint="http://test-s3:9000",
            s3_access_key="test-key",
            s3_secret_key="test-secret",
        )

        assert manager.catalog == mock_catalog
        mock_load_catalog.assert_called_once()

        # Check catalog config
        call_args = mock_load_catalog.call_args
        config = call_args[1]
        assert config["uri"] == "http://test:8181"
        assert config["s3.endpoint"] == "http://test-s3:9000"
        assert config["s3.access-key-id"] == "test-key"

    @patch("k2.storage.catalog.load_catalog")
    def test_table_exists_check(self, mock_load_catalog):
        """Should check if table exists correctly."""
        from k2.storage.catalog import IcebergCatalogManager

        mock_catalog = Mock()
        mock_catalog.load_table.return_value = Mock()
        mock_load_catalog.return_value = mock_catalog

        manager = IcebergCatalogManager()

        # Table exists
        exists = manager.table_exists("market_data", "trades")
        assert exists is True

        # Table doesn't exist
        mock_catalog.load_table.side_effect = Exception("Not found")
        exists = manager.table_exists("market_data", "nonexistent")
        assert exists is False

    @patch("k2.storage.catalog.load_catalog")
    def test_list_tables(self, mock_load_catalog):
        """Should list tables in namespace."""
        from k2.storage.catalog import IcebergCatalogManager

        mock_catalog = Mock()
        mock_catalog.list_tables.return_value = ["trades", "quotes"]
        mock_load_catalog.return_value = mock_catalog

        manager = IcebergCatalogManager()

        tables = manager.list_tables("market_data")

        assert "trades" in tables
        assert "quotes" in tables
        assert len(tables) == 2


@pytest.mark.unit
class TestIcebergWriter:
    """Test Iceberg writer logic."""

    @patch("k2.storage.writer.load_catalog")
    def test_writer_initialization(self, mock_load_catalog):
        """Should initialize writer with correct configuration."""
        from k2.storage.writer import IcebergWriter

        mock_catalog = Mock()
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter(
            catalog_uri="http://test:8181",
            s3_endpoint="http://test-s3:9000",
        )

        assert writer.catalog == mock_catalog
        mock_load_catalog.assert_called_once()

    @patch("k2.storage.writer.load_catalog")
    def test_records_to_arrow_trades(self, mock_load_catalog):
        """Should convert trade records to Arrow table."""
        from k2.storage.writer import IcebergWriter

        mock_catalog = Mock()
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter()

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "price": Decimal("36.50"),
                "volume": 10000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        arrow_table = writer._records_to_arrow_trades(records)

        assert arrow_table.num_rows == 1
        assert "symbol" in arrow_table.column_names
        assert arrow_table["symbol"][0].as_py() == "BHP"
        assert arrow_table["company_id"][0].as_py() == 7078

    @patch("k2.storage.writer.load_catalog")
    def test_records_to_arrow_quotes(self, mock_load_catalog):
        """Should convert quote records to Arrow table."""
        from k2.storage.writer import IcebergWriter

        mock_catalog = Mock()
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter()

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "bid_price": Decimal("36.50"),
                "bid_volume": 1000,
                "ask_price": Decimal("36.55"),
                "ask_volume": 1500,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        arrow_table = writer._records_to_arrow_quotes(records)

        assert arrow_table.num_rows == 1
        assert "symbol" in arrow_table.column_names
        assert "bid_price" in arrow_table.column_names
        assert "ask_price" in arrow_table.column_names

    @patch("k2.storage.writer.load_catalog")
    def test_write_empty_records(self, mock_load_catalog):
        """Should handle empty records list gracefully."""
        from k2.storage.writer import IcebergWriter

        mock_catalog = Mock()
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter()

        # Empty list should return 0
        result = writer.write_trades([])
        assert result == 0

    @patch("k2.storage.writer.load_catalog")
    def test_decimal_conversion(self, mock_load_catalog):
        """Should handle both Decimal and string prices."""
        from k2.storage.writer import IcebergWriter

        mock_catalog = Mock()
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter()

        # Test with string price
        records_str = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime.now(),
                "price": "36.50",  # String
                "volume": 1000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        arrow_table = writer._records_to_arrow_trades(records_str)
        assert arrow_table.num_rows == 1

        # Test with Decimal price
        records_decimal = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime.now(),
                "price": Decimal("36.50"),  # Decimal
                "volume": 1000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        arrow_table = writer._records_to_arrow_trades(records_decimal)
        assert arrow_table.num_rows == 1

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_trades(self, mock_logger, mock_load_catalog):
        """Should log transaction metadata after successful trade write."""
        from k2.storage.writer import IcebergWriter

        # Mock catalog and table
        mock_catalog = Mock()
        mock_table = Mock()
        mock_snapshot = Mock()

        # Configure snapshot metadata
        mock_snapshot.snapshot_id = 12345678901234567890
        mock_snapshot.sequence_number = 42
        mock_snapshot.summary = {
            "total-records": "1000",
            "total-data-files": "5",
            "total-delete-files": "0",
            "added-data-files": "1",
            "added-records": "100",
            "added-files-size": "524288",
        }

        mock_table.current_snapshot.return_value = mock_snapshot
        mock_table.append = Mock()
        mock_catalog.load_table.return_value = mock_table
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter(schema_version="v1")

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "price": Decimal("36.50"),
                "volume": 10000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        # Execute write
        result = writer.write_trades(records, table_name="market_data.trades")

        # Verify write succeeded
        assert result == 1
        mock_table.append.assert_called_once()

        # Verify transaction metadata was logged
        info_calls = [call for call in mock_logger.info.call_args_list]
        assert len(info_calls) > 0

        # Find the log call with transaction metadata
        transaction_log_found = False
        for call in info_calls:
            kwargs = call[1]
            if "transaction_snapshot_id" in kwargs:
                transaction_log_found = True
                assert kwargs["transaction_snapshot_id"] == 12345678901234567890
                assert kwargs["transaction_sequence_number"] == 42
                assert kwargs["total_records"] == "1000"
                assert kwargs["total_data_files"] == "5"
                assert kwargs["added_data_files"] == "1"
                assert kwargs["added_records"] == "100"
                assert kwargs["added_files_size_bytes"] == "524288"
                break

        assert transaction_log_found, "Transaction metadata not found in logs"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_quotes(self, mock_logger, mock_load_catalog):
        """Should log transaction metadata after successful quote write."""
        from k2.storage.writer import IcebergWriter

        # Mock catalog and table
        mock_catalog = Mock()
        mock_table = Mock()
        mock_snapshot = Mock()

        # Configure snapshot metadata
        mock_snapshot.snapshot_id = 98765432109876543210
        mock_snapshot.sequence_number = 123
        mock_snapshot.summary = {
            "total-records": "2000",
            "total-data-files": "10",
            "total-delete-files": "0",
            "added-data-files": "2",
            "added-records": "200",
            "added-files-size": "1048576",
        }

        mock_table.current_snapshot.return_value = mock_snapshot
        mock_table.append = Mock()
        mock_catalog.load_table.return_value = mock_table
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter(schema_version="v1")

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "bid_price": Decimal("36.50"),
                "bid_volume": 1000,
                "ask_price": Decimal("36.55"),
                "ask_volume": 1500,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        # Execute write
        result = writer.write_quotes(records, table_name="market_data.quotes")

        # Verify write succeeded
        assert result == 1
        mock_table.append.assert_called_once()

        # Verify transaction metadata was logged
        info_calls = [call for call in mock_logger.info.call_args_list]
        assert len(info_calls) > 0

        # Find the log call with transaction metadata
        transaction_log_found = False
        for call in info_calls:
            kwargs = call[1]
            if "transaction_snapshot_id" in kwargs:
                transaction_log_found = True
                assert kwargs["transaction_snapshot_id"] == 98765432109876543210
                assert kwargs["transaction_sequence_number"] == 123
                assert kwargs["total_records"] == "2000"
                assert kwargs["total_data_files"] == "10"
                assert kwargs["added_data_files"] == "2"
                assert kwargs["added_records"] == "200"
                assert kwargs["added_files_size_bytes"] == "1048576"
                break

        assert transaction_log_found, "Transaction metadata not found in logs"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_fallback_no_snapshot(self, mock_logger, mock_load_catalog):
        """Should fallback to basic logging when snapshot unavailable."""
        from k2.storage.writer import IcebergWriter

        # Mock catalog and table
        mock_catalog = Mock()
        mock_table = Mock()

        # Configure table to return None for snapshot (edge case)
        mock_table.current_snapshot.return_value = None
        mock_table.append = Mock()
        mock_catalog.load_table.return_value = mock_table
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter(schema_version="v1")

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "price": Decimal("36.50"),
                "volume": 10000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        # Execute write
        result = writer.write_trades(records, table_name="market_data.trades")

        # Verify write succeeded
        assert result == 1
        mock_table.append.assert_called_once()

        # Verify basic logging was used (without transaction metadata)
        info_calls = [call for call in mock_logger.info.call_args_list]
        assert len(info_calls) > 0

        # Verify no transaction metadata in logs
        for call in info_calls:
            kwargs = call[1]
            # Should have basic fields but not transaction metadata
            if "record_count" in kwargs:
                assert "transaction_snapshot_id" not in kwargs
                assert "transaction_sequence_number" not in kwargs

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_empty_summary(self, mock_logger, mock_load_catalog):
        """Should handle snapshot with empty summary gracefully."""
        from k2.storage.writer import IcebergWriter

        # Mock catalog and table
        mock_catalog = Mock()
        mock_table = Mock()
        mock_snapshot = Mock()

        # Configure snapshot with empty summary
        mock_snapshot.snapshot_id = 11111111111111111111
        mock_snapshot.sequence_number = 1
        mock_snapshot.summary = {}  # Empty summary

        mock_table.current_snapshot.return_value = mock_snapshot
        mock_table.append = Mock()
        mock_catalog.load_table.return_value = mock_table
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter(schema_version="v1")

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "price": Decimal("36.50"),
                "volume": 10000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            },
        ]

        # Execute write (should not raise exception)
        result = writer.write_trades(records, table_name="market_data.trades")

        # Verify write succeeded
        assert result == 1
        mock_table.append.assert_called_once()

        # Verify transaction metadata was logged with defaults
        info_calls = [call for call in mock_logger.info.call_args_list]
        transaction_log_found = False
        for call in info_calls:
            kwargs = call[1]
            if "transaction_snapshot_id" in kwargs:
                transaction_log_found = True
                assert kwargs["transaction_snapshot_id"] == 11111111111111111111
                assert kwargs["transaction_sequence_number"] == 1
                # Empty summary should use defaults
                assert kwargs["total_records"] == "unknown"
                assert kwargs["added_data_files"] == "0"
                break

        assert transaction_log_found, "Transaction metadata not found in logs"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_trades_v2(self, mock_logger, mock_load_catalog):
        """Should log transaction metadata after successful trade write (v2 schema)."""
        from k2.storage.writer import IcebergWriter

        # Mock catalog and table
        mock_catalog = Mock()
        mock_table = Mock()
        mock_snapshot = Mock()

        # Configure snapshot metadata
        mock_snapshot.snapshot_id = 55555555555555555555
        mock_snapshot.sequence_number = 99
        mock_snapshot.summary = {
            "total-records": "5000",
            "total-data-files": "15",
            "total-delete-files": "0",
            "added-data-files": "3",
            "added-records": "500",
            "added-files-size": "2097152",
        }

        mock_table.current_snapshot.return_value = mock_snapshot
        mock_table.append = Mock()
        mock_catalog.load_table.return_value = mock_table
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter(schema_version="v2")

        records = [
            {
                "message_id": "msg-123",
                "trade_id": "trade-456",
                "symbol": "BTCUSDT",
                "exchange": "binance",
                "asset_class": "crypto",
                "timestamp": datetime(2025, 1, 13, 10, 0, 0),
                "price": Decimal("45000.12345678"),
                "quantity": Decimal("1.5"),
                "currency": "USDT",
                "side": "buy",
                "trade_conditions": ["regular"],
                "source_sequence": 12345,
                "ingestion_timestamp": datetime.now(),
                "platform_sequence": 67890,
                "vendor_data": {"venue": "spot", "maker": True},
                "is_sample_data": False,
            },
        ]

        # Execute write
        result = writer.write_trades(records, table_name="market_data.trades_v2")

        # Verify write succeeded
        assert result == 1
        mock_table.append.assert_called_once()

        # Verify transaction metadata was logged
        info_calls = [call for call in mock_logger.info.call_args_list]
        assert len(info_calls) > 0

        # Find the log call with transaction metadata
        transaction_log_found = False
        for call in info_calls:
            kwargs = call[1]
            if "transaction_snapshot_id" in kwargs:
                transaction_log_found = True
                assert kwargs["transaction_snapshot_id"] == 55555555555555555555
                assert kwargs["transaction_sequence_number"] == 99
                assert kwargs["total_records"] == "5000"
                assert kwargs["total_data_files"] == "15"
                assert kwargs["added_data_files"] == "3"
                assert kwargs["added_records"] == "500"
                assert kwargs["added_files_size_bytes"] == "2097152"
                break

        assert transaction_log_found, "Transaction metadata not found in logs"

    @patch("k2.storage.writer.load_catalog")
    @patch("k2.storage.writer.logger")
    def test_transaction_logging_quotes_v2(self, mock_logger, mock_load_catalog):
        """Should log transaction metadata after successful quote write (v2 schema)."""
        from k2.storage.writer import IcebergWriter

        # Mock catalog and table
        mock_catalog = Mock()
        mock_table = Mock()
        mock_snapshot = Mock()

        # Configure snapshot metadata
        mock_snapshot.snapshot_id = 77777777777777777777
        mock_snapshot.sequence_number = 200
        mock_snapshot.summary = {
            "total-records": "10000",
            "total-data-files": "25",
            "total-delete-files": "0",
            "added-data-files": "5",
            "added-records": "1000",
            "added-files-size": "4194304",
        }

        mock_table.current_snapshot.return_value = mock_snapshot
        mock_table.append = Mock()
        mock_catalog.load_table.return_value = mock_table
        mock_load_catalog.return_value = mock_catalog

        writer = IcebergWriter(schema_version="v2")

        records = [
            {
                "message_id": "msg-789",
                "quote_id": "quote-012",
                "symbol": "ETHUSDT",
                "exchange": "binance",
                "asset_class": "crypto",
                "timestamp": datetime(2025, 1, 13, 10, 0, 0),
                "bid_price": Decimal("3000.12345678"),
                "bid_quantity": Decimal("2.5"),
                "ask_price": Decimal("3001.12345678"),
                "ask_quantity": Decimal("3.0"),
                "currency": "USDT",
                "source_sequence": 54321,
                "ingestion_timestamp": datetime.now(),
                "platform_sequence": 98765,
                "vendor_data": {"venue": "spot", "level": 1},
            },
        ]

        # Execute write
        result = writer.write_quotes(records, table_name="market_data.quotes_v2")

        # Verify write succeeded
        assert result == 1
        mock_table.append.assert_called_once()

        # Verify transaction metadata was logged
        info_calls = [call for call in mock_logger.info.call_args_list]
        assert len(info_calls) > 0

        # Find the log call with transaction metadata
        transaction_log_found = False
        for call in info_calls:
            kwargs = call[1]
            if "transaction_snapshot_id" in kwargs:
                transaction_log_found = True
                assert kwargs["transaction_snapshot_id"] == 77777777777777777777
                assert kwargs["transaction_sequence_number"] == 200
                assert kwargs["total_records"] == "10000"
                assert kwargs["total_data_files"] == "25"
                assert kwargs["added_data_files"] == "5"
                assert kwargs["added_records"] == "1000"
                assert kwargs["added_files_size_bytes"] == "4194304"
                break

        assert transaction_log_found, "Transaction metadata not found in logs"
