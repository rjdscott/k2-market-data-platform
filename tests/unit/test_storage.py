"""Unit tests for storage layer.

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
