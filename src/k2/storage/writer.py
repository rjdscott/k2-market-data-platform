"""Iceberg table writer with ACID guarantees.

This module provides write operations for Iceberg tables with:
- ACID transactions (all-or-nothing writes)
- PyArrow for efficient columnar conversion
- Automatic schema validation
- Metrics collection for observability

Writers are designed for batch writes (100-10,000 records per call) to
optimize Iceberg's append performance and minimize metadata operations.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from decimal import Decimal
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.table import Table
from pyiceberg.exceptions import NoSuchTableError
import structlog

from k2.common.config import config
from k2.common.metrics import metrics

logger = structlog.get_logger()


class IcebergWriter:
    """
    Write data to Iceberg tables with ACID guarantees.

    This writer:
    - Converts Python dictionaries to PyArrow tables
    - Uses Iceberg append() for ACID transactions
    - Tracks write performance with metrics
    - Validates data against table schema

    Usage:
        writer = IcebergWriter()

        records = [
            {
                "symbol": "BHP",
                "company_id": 7078,
                "exchange": "ASX",
                "exchange_timestamp": datetime(2014, 3, 10, 10, 0, 0),
                "price": Decimal("36.50"),
                "volume": 10000,
                ...
            }
        ]

        writer.write_trades(records, table_name="market_data.trades")
    """

    def __init__(
        self,
        catalog_uri: Optional[str] = None,
        s3_endpoint: Optional[str] = None,
        s3_access_key: Optional[str] = None,
        s3_secret_key: Optional[str] = None,
    ):
        """
        Initialize Iceberg writer.

        Args:
            catalog_uri: Iceberg REST catalog URI (defaults to config)
            s3_endpoint: S3/MinIO endpoint (defaults to config)
            s3_access_key: S3 access key (defaults to config)
            s3_secret_key: S3 secret key (defaults to config)
        """
        self.catalog_uri = catalog_uri or config.iceberg.catalog_uri
        self.s3_endpoint = s3_endpoint or config.iceberg.s3_endpoint
        self.s3_access_key = s3_access_key or config.iceberg.s3_access_key
        self.s3_secret_key = s3_secret_key or config.iceberg.s3_secret_key

        try:
            self.catalog = load_catalog(
                "k2_catalog",
                **{
                    "uri": self.catalog_uri,
                    "s3.endpoint": self.s3_endpoint,
                    "s3.access-key-id": self.s3_access_key,
                    "s3.secret-access-key": self.s3_secret_key,
                    "s3.path-style-access": "true",
                }
            )

            logger.debug("Iceberg writer initialized", uri=self.catalog_uri)

        except Exception as e:
            logger.error(
                "Failed to initialize Iceberg writer",
                error=str(e),
                uri=self.catalog_uri
            )
            metrics.increment("iceberg_writer_init_errors")
            raise

    def write_trades(
        self,
        records: List[Dict[str, Any]],
        table_name: str = "market_data.trades"
    ) -> int:
        """
        Write trade records to Iceberg table.

        Args:
            records: List of trade dictionaries matching schema
            table_name: Fully qualified table name (namespace.table)

        Returns:
            Number of records written

        Raises:
            NoSuchTableError: If table doesn't exist
            Exception: If write fails
        """
        if not records:
            logger.warning("No records to write", table=table_name)
            return 0

        start_time = datetime.now()

        try:
            table = self.catalog.load_table(table_name)
        except NoSuchTableError:
            logger.error("Table not found", table=table_name)
            metrics.increment("iceberg_write_errors", tags={"reason": "table_not_found"})
            raise

        # Convert to PyArrow table
        try:
            arrow_table = self._records_to_arrow_trades(records)
        except Exception as e:
            logger.error(
                "Failed to convert records to Arrow",
                table=table_name,
                records=len(records),
                error=str(e)
            )
            metrics.increment("iceberg_write_errors", tags={"reason": "arrow_conversion"})
            raise

        # Append data (ACID transaction)
        try:
            table.append(arrow_table)

            duration_ms = (datetime.now() - start_time).total_seconds() * 1000

            logger.info(
                "Trades written to Iceberg",
                table=table_name,
                records=len(records),
                duration_ms=f"{duration_ms:.2f}",
                throughput_per_sec=f"{len(records) / (duration_ms / 1000):.0f}"
            )

            metrics.histogram(
                "iceberg_write_duration_ms",
                duration_ms,
                tags={"table": "trades"}
            )
            metrics.increment(
                "iceberg_records_written",
                value=len(records),
                tags={"table": "trades"}
            )

            return len(records)

        except Exception as e:
            logger.error(
                "Failed to write trades",
                table=table_name,
                records=len(records),
                error=str(e),
            )
            metrics.increment("iceberg_write_errors", tags={"reason": "append_failed"})
            raise

    def write_quotes(
        self,
        records: List[Dict[str, Any]],
        table_name: str = "market_data.quotes"
    ) -> int:
        """
        Write quote records to Iceberg table.

        Args:
            records: List of quote dictionaries matching schema
            table_name: Fully qualified table name (namespace.table)

        Returns:
            Number of records written

        Raises:
            NoSuchTableError: If table doesn't exist
            Exception: If write fails
        """
        if not records:
            logger.warning("No records to write", table=table_name)
            return 0

        start_time = datetime.now()

        try:
            table = self.catalog.load_table(table_name)
        except NoSuchTableError:
            logger.error("Table not found", table=table_name)
            metrics.increment("iceberg_write_errors", tags={"reason": "table_not_found"})
            raise

        # Convert to PyArrow table
        try:
            arrow_table = self._records_to_arrow_quotes(records)
        except Exception as e:
            logger.error(
                "Failed to convert records to Arrow",
                table=table_name,
                records=len(records),
                error=str(e)
            )
            metrics.increment("iceberg_write_errors", tags={"reason": "arrow_conversion"})
            raise

        # Append data (ACID transaction)
        try:
            table.append(arrow_table)

            duration_ms = (datetime.now() - start_time).total_seconds() * 1000

            logger.info(
                "Quotes written to Iceberg",
                table=table_name,
                records=len(records),
                duration_ms=f"{duration_ms:.2f}",
                throughput_per_sec=f"{len(records) / (duration_ms / 1000):.0f}"
            )

            metrics.histogram(
                "iceberg_write_duration_ms",
                duration_ms,
                tags={"table": "quotes"}
            )
            metrics.increment(
                "iceberg_records_written",
                value=len(records),
                tags={"table": "quotes"}
            )

            return len(records)

        except Exception as e:
            logger.error(
                "Failed to write quotes",
                table=table_name,
                records=len(records),
                error=str(e),
            )
            metrics.increment("iceberg_write_errors", tags={"reason": "append_failed"})
            raise

    def _records_to_arrow_trades(
        self,
        records: List[Dict[str, Any]]
    ) -> pa.Table:
        """
        Convert trade records to PyArrow table.

        Args:
            records: List of trade dictionaries

        Returns:
            PyArrow table matching trades schema

        Raises:
            Exception: If conversion fails
        """
        # Define schema matching Iceberg table (with explicit nullable flags)
        schema = pa.schema([
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("company_id", pa.int32(), nullable=False),
            pa.field("exchange", pa.string(), nullable=False),
            pa.field("exchange_timestamp", pa.timestamp('us'), nullable=False),
            pa.field("price", pa.decimal128(18, 6), nullable=False),
            pa.field("volume", pa.int64(), nullable=False),
            pa.field("qualifiers", pa.int32(), nullable=False),
            pa.field("venue", pa.string(), nullable=False),
            pa.field("buyer_id", pa.string(), nullable=True),
            pa.field("ingestion_timestamp", pa.timestamp('us'), nullable=False),
            pa.field("sequence_number", pa.int64(), nullable=True),
        ])

        # Convert records, handling type conversions
        converted_records = []
        for record in records:
            converted = {
                "symbol": record["symbol"],
                "company_id": record["company_id"],
                "exchange": record["exchange"],
                "exchange_timestamp": record["exchange_timestamp"],
                "price": record["price"] if isinstance(record["price"], Decimal) else Decimal(str(record["price"])),
                "volume": record["volume"],
                "qualifiers": record["qualifiers"],
                "venue": record["venue"],
                "buyer_id": record.get("buyer_id"),
                "ingestion_timestamp": record["ingestion_timestamp"],
                "sequence_number": record.get("sequence_number"),
            }
            converted_records.append(converted)

        return pa.Table.from_pylist(converted_records, schema=schema)

    def _records_to_arrow_quotes(
        self,
        records: List[Dict[str, Any]]
    ) -> pa.Table:
        """
        Convert quote records to PyArrow table.

        Args:
            records: List of quote dictionaries

        Returns:
            PyArrow table matching quotes schema

        Raises:
            Exception: If conversion fails
        """
        # Define schema matching Iceberg table (with explicit nullable flags)
        schema = pa.schema([
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("company_id", pa.int32(), nullable=False),
            pa.field("exchange", pa.string(), nullable=False),
            pa.field("exchange_timestamp", pa.timestamp('us'), nullable=False),
            pa.field("bid_price", pa.decimal128(18, 6), nullable=False),
            pa.field("bid_volume", pa.int64(), nullable=False),
            pa.field("ask_price", pa.decimal128(18, 6), nullable=False),
            pa.field("ask_volume", pa.int64(), nullable=False),
            pa.field("ingestion_timestamp", pa.timestamp('us'), nullable=False),
            pa.field("sequence_number", pa.int64(), nullable=True),
        ])

        # Convert records, handling type conversions
        converted_records = []
        for record in records:
            converted = {
                "symbol": record["symbol"],
                "company_id": record["company_id"],
                "exchange": record["exchange"],
                "exchange_timestamp": record["exchange_timestamp"],
                "bid_price": record["bid_price"] if isinstance(record["bid_price"], Decimal) else Decimal(str(record["bid_price"])),
                "bid_volume": record["bid_volume"],
                "ask_price": record["ask_price"] if isinstance(record["ask_price"], Decimal) else Decimal(str(record["ask_price"])),
                "ask_volume": record["ask_volume"],
                "ingestion_timestamp": record["ingestion_timestamp"],
                "sequence_number": record.get("sequence_number"),
            }
            converted_records.append(converted)

        return pa.Table.from_pylist(converted_records, schema=schema)
