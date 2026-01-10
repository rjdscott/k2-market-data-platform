# Step 3: Storage Layer - Iceberg Table Initialization

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 6-8 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 1 (Infrastructure), Step 2 (Schemas for reference)
- **Blocks**: Step 4 (Iceberg Writer), Step 9 (Query Engine)

## Goal
Create Iceberg tables with correct partitioning and sorting for time-travel queries. Establish the lakehouse storage foundation with ACID guarantees, schema evolution support, and efficient time-range query capabilities.

---

## Implementation

### 3.1 Implement Iceberg Catalog Manager

**File**: `src/k2/storage/catalog.py`

```python
"""Iceberg catalog management."""
from typing import Optional, Dict, Any
from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import (
    NestedField, StringType, LongType, TimestampType, IntegerType, DecimalType
)
from pyiceberg.partitioning import PartitionSpec, PartitionField
from pyiceberg.transforms import DayTransform, HourTransform
from pyiceberg.table.sorting import SortOrder, SortField
from pyiceberg.transforms import IdentityTransform
import structlog

logger = structlog.get_logger()

class IcebergCatalogManager:
    """Manages Iceberg catalog and table operations."""

    def __init__(
        self,
        catalog_uri: str = "http://localhost:8181",
        s3_endpoint: str = "http://localhost:9000",
        s3_access_key: str = "admin",
        s3_secret_key: str = "password",
    ):
        """Initialize catalog connection."""
        self.catalog = load_catalog(
            "k2_catalog",
            **{
                "uri": catalog_uri,
                "s3.endpoint": s3_endpoint,
                "s3.access-key-id": s3_access_key,
                "s3.secret-access-key": s3_secret_key,
                "s3.path-style-access": "true",
            }
        )
        logger.info("Iceberg catalog initialized", uri=catalog_uri)

    def create_trades_table(self, namespace: str = "market_data") -> None:
        """Create trades table with appropriate partitioning."""
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

        # Partition by day for efficient time-range queries
        partition_spec = PartitionSpec(
            PartitionField(
                source_id=4,
                field_id=1000,
                transform=DayTransform(),
                name="exchange_date"
            )
        )

        # Sort by timestamp and sequence for ordered scans
        sort_order = SortOrder(
            SortField(source_id=4, transform=IdentityTransform()),
            SortField(source_id=11, transform=IdentityTransform()),
        )

        table_id = f"{namespace}.trades"
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
                }
            )
            logger.info("Trades table created", table=table_id)
        except Exception as e:
            logger.error("Failed to create table", table=table_id, error=str(e))
            raise

    def create_quotes_table(self, namespace: str = "market_data") -> None:
        """Create quotes table."""
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

        partition_spec = PartitionSpec(
            PartitionField(
                source_id=4,
                field_id=1000,
                transform=DayTransform(),
                name="exchange_date"
            )
        )

        sort_order = SortOrder(
            SortField(source_id=4, transform=IdentityTransform()),
            SortField(source_id=10, transform=IdentityTransform()),
        )

        table_id = f"{namespace}.quotes"
        self.catalog.create_table(
            identifier=table_id,
            schema=schema,
            partition_spec=partition_spec,
            sort_order=sort_order,
            properties={
                "write.format.default": "parquet",
                "write.parquet.compression-codec": "zstd",
            }
        )
        logger.info("Quotes table created", table=table_id)
```

### 3.2 Update Infrastructure Init Script

**File**: Update `scripts/init_infra.py` to call table creation:

```python
# Add to init_infra.py
from k2.storage.catalog import IcebergCatalogManager

def create_iceberg_tables() -> None:
    """Create Iceberg tables."""
    manager = IcebergCatalogManager()
    manager.create_trades_table()
    manager.create_quotes_table()
    logger.info("Iceberg tables created")

if __name__ == '__main__':
    # ... existing code ...
    create_iceberg_tables()
```

### 3.3 Test Table Creation

**File**: `tests/integration/test_iceberg_catalog.py`

```python
"""Integration tests for Iceberg catalog."""
import pytest
from k2.storage.catalog import IcebergCatalogManager
from pyiceberg.catalog import load_catalog

@pytest.mark.integration
class TestIcebergCatalog:
    """Test Iceberg table operations."""

    def test_create_trades_table(self):
        """Trades table should be created with correct schema."""
        manager = IcebergCatalogManager()
        manager.create_trades_table()

        # Verify table exists
        catalog = manager.catalog
        table = catalog.load_table("market_data.trades")

        assert table is not None
        assert "symbol" in [f.name for f in table.schema().fields]
        assert "exchange_timestamp" in [f.name for f in table.schema().fields]

    def test_table_partition_spec(self):
        """Table should be partitioned by day."""
        manager = IcebergCatalogManager()
        table = manager.catalog.load_table("market_data.trades")

        # Verify daily partitioning
        partition_spec = table.spec()
        assert len(partition_spec.fields) == 1
        assert partition_spec.fields[0].name == "exchange_date"

    def test_table_sort_order(self):
        """Table should be sorted by timestamp and sequence."""
        manager = IcebergCatalogManager()
        table = manager.catalog.load_table("market_data.trades")

        sort_order = table.sort_order()
        assert len(sort_order.fields) == 2
```

---

## Testing

### Integration Tests
- `tests/integration/test_iceberg_catalog.py` - Table creation and schema validation

### Commands
```bash
# Initialize infrastructure
make init-infra

# Run integration tests
pytest tests/integration/test_iceberg_catalog.py -v
```

---

## Validation Checklist

- [ ] Catalog manager implementation created (`src/k2/storage/catalog.py`)
- [ ] Trades table created with correct schema
- [ ] Quotes table created with correct schema
- [ ] Tables partitioned by day (exchange_date)
- [ ] Sort order includes timestamp and sequence_number
- [ ] Parquet format with zstd compression configured
- [ ] Integration tests pass
- [ ] MinIO warehouse contains table metadata: `warehouse/market_data.db/trades/metadata/`
- [ ] Can query PostgreSQL catalog: `SELECT * FROM iceberg_tables;`
- [ ] `make init-infra` completes successfully
- [ ] Tables visible in Iceberg REST catalog: `curl http://localhost:8181/v1/namespaces/market_data/tables`

---

## Rollback Procedure

If this step needs to be reverted:

1. **Drop Iceberg tables**:
   ```python
   from k2.storage.catalog import IcebergCatalogManager

   manager = IcebergCatalogManager()
   manager.catalog.drop_table("market_data.trades")
   manager.catalog.drop_table("market_data.quotes")
   ```

2. **Remove MinIO data**:
   ```bash
   # Using MinIO client (mc)
   mc alias set minio http://localhost:9000 admin password
   mc rm --recursive minio/warehouse/market_data.db/
   ```

3. **Revert code**:
   ```bash
   rm src/k2/storage/catalog.py
   rm tests/integration/test_iceberg_catalog.py

   # Revert init_infra.py changes
   git checkout scripts/init_infra.py
   ```

4. **Verify rollback**:
   ```bash
   pytest tests/integration/test_iceberg_catalog.py
   # Should fail - table doesn't exist (expected)
   ```

5. **Clean PostgreSQL catalog** (if needed):
   ```bash
   docker exec -it k2-postgres psql -U iceberg -d iceberg_catalog
   # Then: DROP TABLE IF EXISTS iceberg_tables;
   ```

---

## Notes & Decisions

### Decisions Made
- **Decision #002**: Daily partitioning chosen over hourly
  - Rationale: Market data queries typically filter by date, daily partitions prevent over-partitioning
  - Trade-off: Slightly larger partition sizes but more efficient query planning

- **Compression codec**: zstd chosen for optimal compression ratio vs speed
  - Alternative considered: snappy (faster but less compression)

### Issues Encountered
[Space for documenting any issues during implementation]

### Performance Considerations
- Daily partitions optimize for time-range queries
- Sort order (timestamp, sequence) enables efficient ordered scans for replay
- Zstd compression reduces storage costs with acceptable CPU overhead

### References
- PyIceberg documentation: https://py.iceberg.apache.org/
- Iceberg table spec: https://iceberg.apache.org/spec/
- Partition evolution: https://iceberg.apache.org/docs/latest/evolution/
