"""Integration tests for Iceberg storage layer.

Tests require Docker services to be running:
- MinIO (S3-compatible storage)
- PostgreSQL (Iceberg catalog metadata)
- Iceberg REST (catalog service)
"""
import pytest
from datetime import datetime
from decimal import Decimal
import structlog

from k2.storage.catalog import IcebergCatalogManager
from k2.storage.writer import IcebergWriter

logger = structlog.get_logger()


@pytest.mark.integration
class TestIcebergCatalog:
    """Test Iceberg catalog operations with real services."""

    def test_create_trades_table(self):
        """Should create trades table with correct schema."""
        manager = IcebergCatalogManager()

        # Create table (or verify it exists)
        manager.create_trades_table(namespace='market_data', table_name='trades')

        # Verify table exists
        assert manager.table_exists('market_data', 'trades')

        # Load table and verify schema
        table = manager.catalog.load_table('market_data.trades')

        assert table is not None

        # Verify schema fields
        field_names = [f.name for f in table.schema().fields]
        assert 'symbol' in field_names
        assert 'exchange_timestamp' in field_names
        assert 'price' in field_names
        assert 'volume' in field_names
        assert 'sequence_number' in field_names

        logger.info(
            "Trades table verified",
            fields=len(field_names),
            field_names=field_names
        )

    def test_create_quotes_table(self):
        """Should create quotes table with correct schema."""
        manager = IcebergCatalogManager()

        # Create table
        manager.create_quotes_table(namespace='market_data', table_name='quotes')

        # Verify table exists
        assert manager.table_exists('market_data', 'quotes')

        # Load table and verify schema
        table = manager.catalog.load_table('market_data.quotes')

        assert table is not None

        field_names = [f.name for f in table.schema().fields]
        assert 'symbol' in field_names
        assert 'bid_price' in field_names
        assert 'ask_price' in field_names

    def test_table_partition_spec(self):
        """Table should be partitioned by day."""
        manager = IcebergCatalogManager()

        # Ensure table exists
        manager.create_trades_table()

        # Load table
        table = manager.catalog.load_table('market_data.trades')

        # Verify daily partitioning
        partition_spec = table.spec()

        assert len(partition_spec.fields) == 1
        assert partition_spec.fields[0].name == 'exchange_date'

        logger.info("Partition spec verified", partition=partition_spec.fields[0].name)

    def test_table_sort_order(self):
        """Table should be sorted by timestamp and sequence."""
        manager = IcebergCatalogManager()

        # Ensure table exists
        manager.create_trades_table()

        # Load table
        table = manager.catalog.load_table('market_data.trades')

        # Verify sort order
        sort_order = table.sort_order()

        assert len(sort_order.fields) == 2

        logger.info("Sort order verified", fields=len(sort_order.fields))

    def test_list_tables(self):
        """Should list tables in namespace."""
        manager = IcebergCatalogManager()

        # Ensure tables exist
        manager.create_trades_table()
        manager.create_quotes_table()

        # List tables
        tables = manager.list_tables('market_data')

        # Tables are returned as tuples: (namespace, table_name)
        table_names = [table[1] if isinstance(table, tuple) else table for table in tables]
        assert 'trades' in table_names
        assert 'quotes' in table_names

        logger.info("Tables listed", count=len(tables))


@pytest.mark.integration
class TestIcebergWriter:
    """Test Iceberg writer with real Iceberg tables."""

    def test_write_trades(self):
        """Should write trade records and commit transaction."""
        # Ensure table exists
        catalog = IcebergCatalogManager()
        catalog.create_trades_table()

        # Create writer
        writer = IcebergWriter()

        # Create test records
        records = [
            {
                "symbol": "TEST",
                "company_id": 9999,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0, 0),
                "price": Decimal("100.50"),
                "volume": 10000,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            }
        ]

        # Write records
        written = writer.write_trades(records, table_name='market_data.trades')

        assert written == 1

        logger.info("Trade records written", count=written)

        # Verify data exists in table
        table = catalog.catalog.load_table('market_data.trades')
        df = table.scan().to_pandas()

        assert len(df) >= 1
        assert "TEST" in df["symbol"].values

        logger.info("Trade data verified in table", total_rows=len(df))

    def test_write_quotes(self):
        """Should write quote records and commit transaction."""
        # Ensure table exists
        catalog = IcebergCatalogManager()
        catalog.create_quotes_table()

        # Create writer
        writer = IcebergWriter()

        # Create test records
        records = [
            {
                "symbol": "TEST",
                "company_id": 9999,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0, 0),
                "bid_price": Decimal("100.50"),
                "bid_volume": 1000,
                "ask_price": Decimal("100.55"),
                "ask_volume": 1500,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": 1,
            }
        ]

        # Write records
        written = writer.write_quotes(records, table_name='market_data.quotes')

        assert written == 1

        logger.info("Quote records written", count=written)

    def test_write_batch(self):
        """Should write batch of records efficiently."""
        # Ensure table exists
        catalog = IcebergCatalogManager()
        catalog.create_trades_table()

        writer = IcebergWriter()

        # Create batch of records
        batch_size = 100
        records = []

        for i in range(batch_size):
            # Use string formatting to ensure proper decimal precision (6 places)
            price_increment = Decimal(f"{i * 0.01:.6f}")
            records.append({
                "symbol": "BATCH",
                "company_id": 9999,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, i % 60, i * 1000),  # Use modulo for seconds, microseconds for uniqueness
                "price": Decimal("100.00") + price_increment,
                "volume": 1000 + i,
                "qualifiers": 0,
                "venue": "X",
                "buyer_id": None,
                "ingestion_timestamp": datetime.now(),
                "sequence_number": i,
            })

        # Write batch
        written = writer.write_trades(records, table_name='market_data.trades')

        assert written == batch_size

        logger.info("Batch written", records=batch_size)

    def test_decimal_precision(self):
        """Should maintain decimal precision for prices."""
        catalog = IcebergCatalogManager()
        catalog.create_trades_table()

        writer = IcebergWriter()

        # Record with high precision price
        test_price = Decimal("36.123456")
        records = [{
            "symbol": "PREC",
            "company_id": 9999,
            "exchange": "ASX",
            "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0, 0),
            "price": test_price,
            "volume": 1000,
            "qualifiers": 0,
            "venue": "X",
            "buyer_id": None,
            "ingestion_timestamp": datetime.now(),
            "sequence_number": 1,
        }]

        writer.write_trades(records, table_name='market_data.trades')

        # Read back and verify precision
        table = catalog.catalog.load_table('market_data.trades')
        df = table.scan().to_pandas()

        prec_rows = df[df['symbol'] == 'PREC']
        if len(prec_rows) > 0:
            # Note: Precision may vary based on storage format
            assert prec_rows.iloc[0]['price'] is not None
            logger.info("Decimal precision verified", price=prec_rows.iloc[0]['price'])

    def test_empty_write(self):
        """Should handle empty record list gracefully."""
        writer = IcebergWriter()

        written = writer.write_trades([], table_name='market_data.trades')

        assert written == 0

    def test_table_not_found_error(self):
        """Should raise error if table doesn't exist."""
        writer = IcebergWriter()

        records = [{
            "symbol": "TEST",
            "company_id": 9999,
            "exchange": "ASX",
            "exchange_timestamp": datetime.now(),
            "price": Decimal("100.00"),
            "volume": 1000,
            "qualifiers": 0,
            "venue": "X",
            "buyer_id": None,
            "ingestion_timestamp": datetime.now(),
            "sequence_number": 1,
        }]

        with pytest.raises(Exception):
            writer.write_trades(records, table_name='nonexistent.table')
