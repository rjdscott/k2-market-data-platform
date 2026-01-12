"""Iceberg table writer with ACID guarantees.

This module provides write operations for Iceberg tables with:
- ACID transactions (all-or-nothing writes)
- PyArrow for efficient columnar conversion
- Automatic schema validation
- Metrics collection for observability
- Exponential backoff retry for transient failures
- Support for v1 (legacy) and v2 (industry-standard) schemas

Writers are designed for batch writes (100-10,000 records per call) to
optimize Iceberg's append performance and minimize metadata operations.

Schema Evolution:
- v1: Legacy ASX-specific schemas (volume, company_id fields)
- v2: Industry-standard hybrid schemas (quantity, vendor_data pattern)
"""

import json
import time
from decimal import Decimal
from typing import Any

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import CommitFailedException, NoSuchTableError

from k2.common.config import config
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics

logger = get_logger(__name__, component="storage")
metrics = create_component_metrics("storage")


def _retry_with_exponential_backoff(
    max_retries: int = 3,
    initial_delay: float = 0.1,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
):
    """Decorator for retrying operations with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_factor: Multiplier for delay between retries

    Example:
        @_retry_with_exponential_backoff(max_retries=5)
        def write_data():
            # ... code that might fail transiently
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError, CommitFailedException) as e:
                    last_exception = e

                    if attempt < max_retries:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed, retrying",
                            function=func.__name__,
                            error=str(e),
                            delay_seconds=delay,
                        )
                        time.sleep(delay)
                        delay = min(delay * backoff_factor, max_delay)
                    else:
                        logger.error(
                            f"All {max_retries} retry attempts failed",
                            function=func.__name__,
                            error=str(e),
                        )

            raise last_exception

        return wrapper

    return decorator


class IcebergWriter:
    """Write data to Iceberg tables with ACID guarantees.

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
        catalog_uri: str | None = None,
        s3_endpoint: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        schema_version: str = "v2",
    ):
        """Initialize Iceberg writer.

        Args:
            catalog_uri: Iceberg REST catalog URI (defaults to config)
            s3_endpoint: S3/MinIO endpoint (defaults to config)
            s3_access_key: S3 access key (defaults to config)
            s3_secret_key: S3 secret key (defaults to config)
            schema_version: Schema version - 'v1' or 'v2' (default: 'v2')

        Raises:
            ValueError: If schema_version is not 'v1' or 'v2'
        """
        if schema_version not in ("v1", "v2"):
            raise ValueError(f"Invalid schema_version: {schema_version}. Must be 'v1' or 'v2'")

        self.catalog_uri = catalog_uri or config.iceberg.catalog_uri
        self.s3_endpoint = s3_endpoint or config.iceberg.s3_endpoint
        self.s3_access_key = s3_access_key or config.iceberg.s3_access_key
        self.s3_secret_key = s3_secret_key or config.iceberg.s3_secret_key
        self.schema_version = schema_version

        try:
            self.catalog = load_catalog(
                "k2_catalog",
                **{
                    "uri": self.catalog_uri,
                    "s3.endpoint": self.s3_endpoint,
                    "s3.access-key-id": self.s3_access_key,
                    "s3.secret-access-key": self.s3_secret_key,
                    "s3.path-style-access": "true",
                },
            )

            logger.debug("Iceberg writer initialized", catalog_uri=self.catalog_uri)

        except Exception as e:
            logger.error(
                "Failed to initialize Iceberg writer", error=str(e), catalog_uri=self.catalog_uri,
            )
            metrics.increment(
                "iceberg_write_errors_total",
                labels={"error_type": "initialization_failed", "table": "unknown"},
            )
            raise

    @_retry_with_exponential_backoff(max_retries=3)
    def write_trades(
        self,
        records: list[dict[str, Any]],
        table_name: str = "market_data.trades",
        exchange: str = "unknown",
        asset_class: str = "equities",
    ) -> int:
        """Write trade records to Iceberg table with automatic retry.

        Args:
            records: List of trade dictionaries matching schema
            table_name: Fully qualified table name (namespace.table)
            exchange: Exchange code for metrics (e.g., "asx", "nyse")
            asset_class: Asset class for metrics (e.g., "equities", "futures")

        Returns:
            Number of records written

        Raises:
            NoSuchTableError: If table doesn't exist
            Exception: If write fails after retries
        """
        if not records:
            logger.warning("No records to write", table=table_name)
            return 0

        # Load table
        try:
            table = self.catalog.load_table(table_name)
        except NoSuchTableError:
            logger.error("Table not found", table=table_name)
            metrics.increment(
                "iceberg_write_errors_total",
                labels={
                    "exchange": exchange,
                    "asset_class": asset_class,
                    "table": "trades",
                    "error_type": "table_not_found",
                },
            )
            raise

        # Convert to PyArrow table (use appropriate schema version)
        try:
            if self.schema_version == "v2":
                arrow_table = self._records_to_arrow_trades_v2(records)
            else:
                arrow_table = self._records_to_arrow_trades(records)
        except Exception as e:
            logger.error(
                "Failed to convert records to Arrow",
                table=table_name,
                record_count=len(records),
                schema_version=self.schema_version,
                error=str(e),
            )
            metrics.increment(
                "iceberg_write_errors_total",
                labels={
                    "exchange": exchange,
                    "asset_class": asset_class,
                    "table": "trades",
                    "error_type": "arrow_conversion_failed",
                    "schema_version": self.schema_version,
                },
            )
            raise

        # Append data with timing (ACID transaction)
        with metrics.timer(
            "iceberg_write_duration_seconds",
            labels={
                "exchange": exchange,
                "asset_class": asset_class,
                "table": "trades",
                "operation": "append",
            },
        ):
            try:
                table.append(arrow_table)

                logger.info(
                    "Trades written to Iceberg",
                    table=table_name,
                    exchange=exchange,
                    asset_class=asset_class,
                    record_count=len(records),
                )

                # Track rows written
                metrics.increment(
                    "iceberg_rows_written_total",
                    value=len(records),
                    labels={
                        "exchange": exchange,
                        "asset_class": asset_class,
                        "table": "trades",
                    },
                )

                # Track batch size distribution
                metrics.histogram(
                    "iceberg_batch_size",
                    value=len(records),
                    labels={
                        "exchange": exchange,
                        "asset_class": asset_class,
                        "table": "trades",
                    },
                )

                return len(records)

            except Exception as e:
                logger.error(
                    "Failed to append trades",
                    table=table_name,
                    record_count=len(records),
                    error=str(e),
                )
                metrics.increment(
                    "iceberg_write_errors_total",
                    labels={
                        "exchange": exchange,
                        "asset_class": asset_class,
                        "table": "trades",
                        "error_type": "append_failed",
                    },
                )
                raise

    @_retry_with_exponential_backoff(max_retries=3)
    def write_quotes(
        self,
        records: list[dict[str, Any]],
        table_name: str = "market_data.quotes",
        exchange: str = "unknown",
        asset_class: str = "equities",
    ) -> int:
        """Write quote records to Iceberg table with automatic retry.

        Args:
            records: List of quote dictionaries matching schema
            table_name: Fully qualified table name (namespace.table)
            exchange: Exchange code for metrics (e.g., "asx", "nyse")
            asset_class: Asset class for metrics (e.g., "equities", "futures")

        Returns:
            Number of records written

        Raises:
            NoSuchTableError: If table doesn't exist
            Exception: If write fails after retries
        """
        if not records:
            logger.warning("No records to write", table=table_name)
            return 0

        # Load table
        try:
            table = self.catalog.load_table(table_name)
        except NoSuchTableError:
            logger.error("Table not found", table=table_name)
            metrics.increment(
                "iceberg_write_errors_total",
                labels={
                    "exchange": exchange,
                    "asset_class": asset_class,
                    "table": "quotes",
                    "error_type": "table_not_found",
                },
            )
            raise

        # Convert to PyArrow table (use appropriate schema version)
        try:
            if self.schema_version == "v2":
                arrow_table = self._records_to_arrow_quotes_v2(records)
            else:
                arrow_table = self._records_to_arrow_quotes(records)
        except Exception as e:
            logger.error(
                "Failed to convert records to Arrow",
                table=table_name,
                record_count=len(records),
                schema_version=self.schema_version,
                error=str(e),
            )
            metrics.increment(
                "iceberg_write_errors_total",
                labels={
                    "exchange": exchange,
                    "asset_class": asset_class,
                    "table": "quotes",
                    "error_type": "arrow_conversion_failed",
                    "schema_version": self.schema_version,
                },
            )
            raise

        # Append data with timing (ACID transaction)
        with metrics.timer(
            "iceberg_write_duration_seconds",
            labels={
                "exchange": exchange,
                "asset_class": asset_class,
                "table": "quotes",
                "operation": "append",
            },
        ):
            try:
                table.append(arrow_table)

                logger.info(
                    "Quotes written to Iceberg",
                    table=table_name,
                    exchange=exchange,
                    asset_class=asset_class,
                    record_count=len(records),
                )

                # Track rows written
                metrics.increment(
                    "iceberg_rows_written_total",
                    value=len(records),
                    labels={
                        "exchange": exchange,
                        "asset_class": asset_class,
                        "table": "quotes",
                    },
                )

                # Track batch size distribution
                metrics.histogram(
                    "iceberg_batch_size",
                    value=len(records),
                    labels={
                        "exchange": exchange,
                        "asset_class": asset_class,
                        "table": "quotes",
                    },
                )

                return len(records)

            except Exception as e:
                logger.error(
                    "Failed to append quotes",
                    table=table_name,
                    record_count=len(records),
                    error=str(e),
                )
                metrics.increment(
                    "iceberg_write_errors_total",
                    labels={
                        "exchange": exchange,
                        "asset_class": asset_class,
                        "table": "quotes",
                        "error_type": "append_failed",
                    },
                )
                raise

    def _records_to_arrow_trades(self, records: list[dict[str, Any]]) -> pa.Table:
        """Convert trade records to PyArrow table.

        Args:
            records: List of trade dictionaries

        Returns:
            PyArrow table matching trades schema

        Raises:
            Exception: If conversion fails
        """
        # Define schema matching Iceberg table (with explicit nullable flags)
        schema = pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("company_id", pa.int32(), nullable=False),
                pa.field("exchange", pa.string(), nullable=False),
                pa.field("exchange_timestamp", pa.timestamp("us"), nullable=False),
                pa.field("price", pa.decimal128(18, 6), nullable=False),
                pa.field("volume", pa.int64(), nullable=False),
                pa.field("qualifiers", pa.int32(), nullable=False),
                pa.field("venue", pa.string(), nullable=False),
                pa.field("buyer_id", pa.string(), nullable=True),
                pa.field("ingestion_timestamp", pa.timestamp("us"), nullable=False),
                pa.field("sequence_number", pa.int64(), nullable=True),
            ],
        )

        # Convert records, handling type conversions
        converted_records = []
        for record in records:
            converted = {
                "symbol": record["symbol"],
                "company_id": record["company_id"],
                "exchange": record["exchange"],
                "exchange_timestamp": record["exchange_timestamp"],
                "price": (
                    record["price"]
                    if isinstance(record["price"], Decimal)
                    else Decimal(str(record["price"]))
                ),
                "volume": record["volume"],
                "qualifiers": record["qualifiers"],
                "venue": record["venue"],
                "buyer_id": record.get("buyer_id"),
                "ingestion_timestamp": record["ingestion_timestamp"],
                "sequence_number": record.get("sequence_number"),
            }
            converted_records.append(converted)

        return pa.Table.from_pylist(converted_records, schema=schema)

    def _records_to_arrow_quotes(self, records: list[dict[str, Any]]) -> pa.Table:
        """Convert quote records to PyArrow table.

        Args:
            records: List of quote dictionaries

        Returns:
            PyArrow table matching quotes schema

        Raises:
            Exception: If conversion fails
        """
        # Define schema matching Iceberg table (with explicit nullable flags)
        schema = pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("company_id", pa.int32(), nullable=False),
                pa.field("exchange", pa.string(), nullable=False),
                pa.field("exchange_timestamp", pa.timestamp("us"), nullable=False),
                pa.field("bid_price", pa.decimal128(18, 6), nullable=False),
                pa.field("bid_volume", pa.int64(), nullable=False),
                pa.field("ask_price", pa.decimal128(18, 6), nullable=False),
                pa.field("ask_volume", pa.int64(), nullable=False),
                pa.field("ingestion_timestamp", pa.timestamp("us"), nullable=False),
                pa.field("sequence_number", pa.int64(), nullable=True),
            ],
        )

        # Convert records, handling type conversions
        converted_records = []
        for record in records:
            converted = {
                "symbol": record["symbol"],
                "company_id": record["company_id"],
                "exchange": record["exchange"],
                "exchange_timestamp": record["exchange_timestamp"],
                "bid_price": (
                    record["bid_price"]
                    if isinstance(record["bid_price"], Decimal)
                    else Decimal(str(record["bid_price"]))
                ),
                "bid_volume": record["bid_volume"],
                "ask_price": (
                    record["ask_price"]
                    if isinstance(record["ask_price"], Decimal)
                    else Decimal(str(record["ask_price"]))
                ),
                "ask_volume": record["ask_volume"],
                "ingestion_timestamp": record["ingestion_timestamp"],
                "sequence_number": record.get("sequence_number"),
            }
            converted_records.append(converted)

        return pa.Table.from_pylist(converted_records, schema=schema)

    def _records_to_arrow_trades_v2(self, records: list[dict[str, Any]]) -> pa.Table:
        """Convert v2 trade records to PyArrow table.

        V2 schema changes from v1:
        - volume → quantity (Decimal instead of int64)
        - exchange_timestamp → timestamp (field rename for clarity)
        - Add: message_id, trade_id, currency, side, asset_class
        - Add: trade_conditions (array of strings)
        - Add: vendor_data (map serialized as JSON string)
        - company_id, qualifiers, venue → moved to vendor_data
        - precision: Decimal(18,6) → Decimal(18,8)
        - timestamp: milliseconds → microseconds

        Args:
            records: List of v2 trade dictionaries

        Returns:
            PyArrow table matching trades_v2 schema

        Raises:
            Exception: If conversion fails
        """
        # Define v2 schema matching TradeV2 Iceberg table
        schema = pa.schema(
            [
                pa.field("message_id", pa.string(), nullable=False),
                pa.field("trade_id", pa.string(), nullable=False),
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("exchange", pa.string(), nullable=False),
                pa.field("asset_class", pa.string(), nullable=False),  # enum stored as string
                pa.field("timestamp", pa.timestamp("us"), nullable=False),
                pa.field("price", pa.decimal128(18, 8), nullable=False),  # Higher precision
                pa.field("quantity", pa.decimal128(18, 8), nullable=False),  # Decimal, not int64
                pa.field("currency", pa.string(), nullable=False),
                pa.field("side", pa.string(), nullable=False),  # enum stored as string
                pa.field("trade_conditions", pa.list_(pa.string()), nullable=False),
                pa.field("source_sequence", pa.int64(), nullable=True),
                pa.field("ingestion_timestamp", pa.timestamp("us"), nullable=False),
                pa.field("platform_sequence", pa.int64(), nullable=True),
                pa.field("vendor_data", pa.string(), nullable=True),  # JSON string
            ],
        )

        # Convert records, handling type conversions
        converted_records = []
        for record in records:
            # Serialize vendor_data to JSON string if present
            vendor_data_json = None
            if record.get("vendor_data"):
                vendor_data_json = json.dumps(record["vendor_data"])

            converted = {
                "message_id": record["message_id"],
                "trade_id": record["trade_id"],
                "symbol": record["symbol"],
                "exchange": record["exchange"],
                "asset_class": record["asset_class"],  # enum value as string
                "timestamp": record["timestamp"],  # microseconds
                "price": (
                    record["price"]
                    if isinstance(record["price"], Decimal)
                    else Decimal(str(record["price"]))
                ),
                "quantity": (
                    record["quantity"]
                    if isinstance(record["quantity"], Decimal)
                    else Decimal(str(record["quantity"]))
                ),
                "currency": record["currency"],
                "side": record["side"],  # enum value as string
                "trade_conditions": record.get("trade_conditions", []),
                "source_sequence": record.get("source_sequence"),
                "ingestion_timestamp": record["ingestion_timestamp"],  # microseconds
                "platform_sequence": record.get("platform_sequence"),
                "vendor_data": vendor_data_json,
            }
            converted_records.append(converted)

        return pa.Table.from_pylist(converted_records, schema=schema)

    def _records_to_arrow_quotes_v2(self, records: list[dict[str, Any]]) -> pa.Table:
        """Convert v2 quote records to PyArrow table.

        V2 schema changes from v1:
        - bid_volume/ask_volume → bid_quantity/ask_quantity (Decimal)
        - exchange_timestamp → timestamp (field rename)
        - Add: message_id, quote_id, currency, asset_class
        - Add: vendor_data (map serialized as JSON string)
        - company_id → moved to vendor_data
        - precision: Decimal(18,6) → Decimal(18,8)

        Args:
            records: List of v2 quote dictionaries

        Returns:
            PyArrow table matching quotes_v2 schema

        Raises:
            Exception: If conversion fails
        """
        # Define v2 schema matching QuoteV2 Iceberg table
        schema = pa.schema(
            [
                pa.field("message_id", pa.string(), nullable=False),
                pa.field("quote_id", pa.string(), nullable=False),
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("exchange", pa.string(), nullable=False),
                pa.field("asset_class", pa.string(), nullable=False),
                pa.field("timestamp", pa.timestamp("us"), nullable=False),
                pa.field("bid_price", pa.decimal128(18, 8), nullable=False),
                pa.field("bid_quantity", pa.decimal128(18, 8), nullable=False),
                pa.field("ask_price", pa.decimal128(18, 8), nullable=False),
                pa.field("ask_quantity", pa.decimal128(18, 8), nullable=False),
                pa.field("currency", pa.string(), nullable=False),
                pa.field("source_sequence", pa.int64(), nullable=True),
                pa.field("ingestion_timestamp", pa.timestamp("us"), nullable=False),
                pa.field("platform_sequence", pa.int64(), nullable=True),
                pa.field("vendor_data", pa.string(), nullable=True),  # JSON string
            ],
        )

        # Convert records, handling type conversions
        converted_records = []
        for record in records:
            # Serialize vendor_data to JSON string if present
            vendor_data_json = None
            if record.get("vendor_data"):
                vendor_data_json = json.dumps(record["vendor_data"])

            converted = {
                "message_id": record["message_id"],
                "quote_id": record["quote_id"],
                "symbol": record["symbol"],
                "exchange": record["exchange"],
                "asset_class": record["asset_class"],
                "timestamp": record["timestamp"],
                "bid_price": (
                    record["bid_price"]
                    if isinstance(record["bid_price"], Decimal)
                    else Decimal(str(record["bid_price"]))
                ),
                "bid_quantity": (
                    record["bid_quantity"]
                    if isinstance(record["bid_quantity"], Decimal)
                    else Decimal(str(record["bid_quantity"]))
                ),
                "ask_price": (
                    record["ask_price"]
                    if isinstance(record["ask_price"], Decimal)
                    else Decimal(str(record["ask_price"]))
                ),
                "ask_quantity": (
                    record["ask_quantity"]
                    if isinstance(record["ask_quantity"], Decimal)
                    else Decimal(str(record["ask_quantity"]))
                ),
                "currency": record["currency"],
                "source_sequence": record.get("source_sequence"),
                "ingestion_timestamp": record["ingestion_timestamp"],
                "platform_sequence": record.get("platform_sequence"),
                "vendor_data": vendor_data_json,
            }
            converted_records.append(converted)

        return pa.Table.from_pylist(converted_records, schema=schema)
