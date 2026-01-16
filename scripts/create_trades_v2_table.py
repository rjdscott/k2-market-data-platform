#!/usr/bin/env python3
"""Create trades_v2 Iceberg table."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import (
    NestedField,
    StringType,
    TimestampType,
    DoubleType,
    IntegerType,
    LongType,
)
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import DayTransform
from pyiceberg.table.sorting import SortOrder, SortField
from pyiceberg.transforms import IdentityTransform
from pyiceberg.exceptions import TableAlreadyExistsError

from k2.common.config import config

print("Loading Iceberg catalog...")
catalog = load_catalog(
    "rest",
    **{
        "uri": config.iceberg.catalog_uri,
        "s3.endpoint": config.iceberg.s3_endpoint,
        "s3.access-key-id": config.iceberg.s3_access_key,
        "s3.secret-access-key": config.iceberg.s3_secret_key,
        "s3.path-style-access": "true",
    },
)
print("✓ Catalog loaded\n")

# Define v2 schema
trades_v2_schema = Schema(
    NestedField(1, "message_id", StringType(), required=True),
    NestedField(2, "symbol", StringType(), required=True),
    NestedField(3, "exchange", StringType(), required=True),
    NestedField(4, "asset_class", StringType(), required=True),
    NestedField(5, "timestamp", TimestampType(), required=True),
    NestedField(6, "trade_id", StringType(), required=False),
    NestedField(7, "price", DoubleType(), required=True),
    NestedField(8, "quantity", DoubleType(), required=True),
    NestedField(9, "side", StringType(), required=False),
    NestedField(10, "trade_conditions", StringType(), required=False),
    NestedField(11, "sequence_number", IntegerType(), required=False),
    NestedField(12, "vendor_data", StringType(), required=False),
    NestedField(13, "ingestion_timestamp", TimestampType(), required=True),
    NestedField(14, "data_version", StringType(), required=False),
)

# Partitioning: day(timestamp) for efficient time-based queries
partition_spec = PartitionSpec(
    PartitionField(
        source_id=5,  # timestamp field
        field_id=1000,
        transform=DayTransform(),
        name="timestamp_day",
    )
)

# Sort order: symbol, timestamp for efficient lookups
sort_order = SortOrder(
    SortField(
        source_id=2,  # symbol
        transform=IdentityTransform(),
    ),
    SortField(
        source_id=5,  # timestamp
        transform=IdentityTransform(),
    ),
)

print("Creating trades table...")
try:
    catalog.create_table(
        identifier="market_data.trades",
        schema=trades_v2_schema,
        partition_spec=partition_spec,
        sort_order=sort_order,
    )
    print("✓ Table created: market_data.trades\n")
except TableAlreadyExistsError:
    print("✓ Table already exists: market_data.trades\n")

print("Done!")
