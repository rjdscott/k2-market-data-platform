"""Iceberg catalog management for K2 platform.

This module manages Apache Iceberg tables in the lakehouse architecture.
Tables are partitioned by day for efficient time-range queries and sorted
by timestamp + sequence for ordered scans.

See docs/STORAGE_OPTIMIZATION.md for partitioning strategy rationale.
"""

from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import TableAlreadyExistsError
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import SortField, SortOrder
from pyiceberg.transforms import DayTransform, IdentityTransform
from pyiceberg.types import (
    DecimalType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestampType,
)

from k2.common.config import config
from k2.common.logging import get_logger
from k2.common.metrics import create_component_metrics

logger = get_logger(__name__, component="storage")
metrics = create_component_metrics("storage")


class IcebergCatalogManager:
    """Manages Iceberg catalog and table operations.

    This class handles:
    - Catalog connection management
    - Table creation with proper partitioning and sorting
    - Namespace management
    - Schema evolution (future)

    Tables are partitioned by day (exchange_timestamp) for:
    - Efficient time-range query pruning
    - Manageable file sizes
    - Alignment with typical query patterns (daily analytics)

    Tables are sorted by (exchange_timestamp, sequence_number) for:
    - Ordered scans during replay
    - Efficient sequence gap detection
    - Optimal Parquet row group statistics

    Usage:
        manager = IcebergCatalogManager()
        manager.create_trades_table()
        manager.create_quotes_table()

        # List tables
        tables = manager.list_tables('market_data')
    """

    def __init__(
        self,
        catalog_uri: str | None = None,
        s3_endpoint: str | None = None,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
    ):
        """Initialize Iceberg catalog connection.

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
                },
            )
            logger.info(
                "Iceberg catalog initialized",
                uri=self.catalog_uri,
                endpoint=self.s3_endpoint,
            )

        except Exception as e:
            logger.error(
                "Failed to initialize Iceberg catalog",
                error=str(e),
                catalog_uri=self.catalog_uri,
            )
            metrics.increment(
                "iceberg_write_errors_total",
                labels={"error_type": "catalog_init_failed", "table": "unknown"},
            )
            raise

    def create_trades_table(
        self,
        namespace: str = "market_data",
        table_name: str = "trades",
    ) -> None:
        """Create trades table with appropriate partitioning and sorting.

        Table schema matches trade.avsc Avro schema with Iceberg types.
        Partitioned by day for efficient time-range queries.
        Sorted by (exchange_timestamp, sequence_number) for ordered scans.

        Args:
            namespace: Iceberg namespace (database)
            table_name: Table name within namespace

        Raises:
            TableAlreadyExistsError: If table already exists
        """
        logger.info("Creating trades table", namespace=namespace, table=table_name)

        # Schema matches trade.avsc Avro schema
        schema = Schema(
            NestedField(1, "symbol", StringType(), required=True),
            NestedField(2, "company_id", IntegerType(), required=True),
            NestedField(3, "exchange", StringType(), required=True),
            NestedField(4, "exchange_timestamp", TimestampType(), required=True),
            NestedField(5, "price", DecimalType(18, 6), required=True),
            NestedField(6, "volume", LongType(), required=True),
            NestedField(7, "qualifiers", IntegerType(), required=True),
            NestedField(8, "venue", StringType(), required=True),
            NestedField(9, "buyer_id", StringType(), required=False),
            NestedField(10, "ingestion_timestamp", TimestampType(), required=True),
            NestedField(11, "sequence_number", LongType(), required=False),
        )

        # Partition by day (field 4 = exchange_timestamp)
        # Hidden partitioning: users query by timestamp, Iceberg handles partition pruning
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=4,
                field_id=1000,
                transform=DayTransform(),
                name="exchange_date",
            ),
        )

        # Sort for ordered scans (replay, gap detection)
        sort_order = SortOrder(
            SortField(source_id=4, transform=IdentityTransform()),  # timestamp first
            SortField(source_id=11, transform=IdentityTransform()),  # sequence second
        )

        table_id = f"{namespace}.{table_name}"

        try:
            self.catalog.create_table(
                identifier=table_id,
                schema=schema,
                partition_spec=partition_spec,
                sort_order=sort_order,
                properties={
                    "write.format.default": "parquet",
                    "write.parquet.compression-codec": "zstd",  # Best compression/speed balance
                    "write.metadata.compression-codec": "gzip",
                    "write.parquet.page-size-bytes": "1048576",  # 1MB pages
                    "write.parquet.row-group-size-bytes": "134217728",  # 128MB row groups
                },
            )

            logger.info(
                "Trades table created successfully",
                table=table_id,
                partitions="daily",
                sort="timestamp,sequence",
            )

            metrics.increment(
                "iceberg_rows_written_total",
                value=0,
                labels={"exchange": "system", "asset_class": "system", "table": table_name},
            )

        except TableAlreadyExistsError:
            logger.info("Trades table already exists", table=table_id)

        except Exception as e:
            logger.error("Failed to create trades table", table=table_id, error=str(e))
            metrics.increment(
                "iceberg_write_errors_total",
                labels={
                    "exchange": "system",
                    "asset_class": "system",
                    "table": table_name,
                    "error_type": "table_creation_failed",
                },
            )
            raise

    def create_quotes_table(
        self,
        namespace: str = "market_data",
        table_name: str = "quotes",
    ) -> None:
        """Create quotes table with appropriate partitioning and sorting.

        Table schema matches quote.avsc Avro schema with Iceberg types.
        Same partitioning and sorting strategy as trades table.

        Args:
            namespace: Iceberg namespace (database)
            table_name: Table name within namespace

        Raises:
            TableAlreadyExistsError: If table already exists
        """
        logger.info("Creating quotes table", namespace=namespace, table=table_name)

        # Schema matches quote.avsc Avro schema
        schema = Schema(
            NestedField(1, "symbol", StringType(), required=True),
            NestedField(2, "company_id", IntegerType(), required=True),
            NestedField(3, "exchange", StringType(), required=True),
            NestedField(4, "exchange_timestamp", TimestampType(), required=True),
            NestedField(5, "bid_price", DecimalType(18, 6), required=True),
            NestedField(6, "bid_volume", LongType(), required=True),
            NestedField(7, "ask_price", DecimalType(18, 6), required=True),
            NestedField(8, "ask_volume", LongType(), required=True),
            NestedField(9, "ingestion_timestamp", TimestampType(), required=True),
            NestedField(10, "sequence_number", LongType(), required=False),
        )

        # Same partitioning and sorting as trades
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=4,
                field_id=1000,
                transform=DayTransform(),
                name="exchange_date",
            ),
        )

        sort_order = SortOrder(
            SortField(source_id=4, transform=IdentityTransform()),
            SortField(source_id=10, transform=IdentityTransform()),
        )

        table_id = f"{namespace}.{table_name}"

        try:
            self.catalog.create_table(
                identifier=table_id,
                schema=schema,
                partition_spec=partition_spec,
                sort_order=sort_order,
                properties={
                    "write.format.default": "parquet",
                    "write.parquet.compression-codec": "zstd",
                    "write.metadata.compression-codec": "gzip",
                    "write.parquet.page-size-bytes": "1048576",
                    "write.parquet.row-group-size-bytes": "134217728",
                },
            )

            logger.info(
                "Quotes table created successfully",
                table=table_id,
                partitions="daily",
                sort="timestamp,sequence",
            )

            metrics.increment(
                "iceberg_rows_written_total",
                value=0,
                labels={"exchange": "system", "asset_class": "system", "table": table_name},
            )

        except TableAlreadyExistsError:
            logger.info("Quotes table already exists", table=table_id)

        except Exception as e:
            logger.error("Failed to create quotes table", table=table_id, error=str(e))
            metrics.increment(
                "iceberg_write_errors_total",
                labels={
                    "exchange": "system",
                    "asset_class": "system",
                    "table": table_name,
                    "error_type": "table_creation_failed",
                },
            )
            raise

    def list_tables(self, namespace: str) -> list[str]:
        """List all tables in a namespace.

        Args:
            namespace: Namespace to list tables from

        Returns:
            List of table names
        """
        try:
            tables = self.catalog.list_tables(namespace)
            logger.debug("Listed tables", namespace=namespace, count=len(tables))
            return tables

        except Exception as e:
            logger.error("Failed to list tables", namespace=namespace, error=str(e))
            return []

    def table_exists(self, namespace: str, table_name: str) -> bool:
        """Check if a table exists.

        Args:
            namespace: Table namespace
            table_name: Table name

        Returns:
            True if table exists, False otherwise
        """
        try:
            table_id = f"{namespace}.{table_name}"
            self.catalog.load_table(table_id)
            return True

        except Exception:
            return False

    def drop_table(self, namespace: str, table_name: str, purge: bool = False) -> None:
        """Drop a table.

        Args:
            namespace: Table namespace
            table_name: Table name
            purge: If True, also delete data files (WARNING: irreversible)

        Raises:
            Exception: If table doesn't exist or drop fails
        """
        table_id = f"{namespace}.{table_name}"

        logger.warning(
            "Dropping table",
            table=table_id,
            purge=purge,
            warning="This operation cannot be undone if purge=True",
        )

        try:
            self.catalog.drop_table(table_id, purge=purge)

            logger.info("Table dropped", table=table_id, purged=purge)

            logger.info("Table dropped successfully", table=table_id, purged=purge)

        except Exception as e:
            logger.error("Failed to drop table", table=table_id, error=str(e))
            raise
