"""Storage layer for K2 platform.

This module provides storage management for the K2 platform using Apache Iceberg
for lakehouse functionality. Iceberg provides:
- ACID transactions for data consistency
- Schema evolution without breaking changes
- Time-travel queries for historical analysis
- Hidden partitioning for query optimization
- Snapshot isolation for concurrent reads/writes

Components:
- catalog: Iceberg catalog management and table creation
- writer: Write data to Iceberg tables with ACID guarantees

See docs/STORAGE_OPTIMIZATION.md for performance tuning guidance.
"""

from . import catalog, writer

__all__ = ["catalog", "writer"]
