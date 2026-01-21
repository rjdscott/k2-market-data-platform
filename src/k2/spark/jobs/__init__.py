"""Spark jobs for Medallion architecture.

Table Creation Jobs:
    - create_bronze_table: Create Bronze Iceberg table (raw Kafka data)
    - create_silver_tables: Create Silver Iceberg tables (Binance + Kraken)
    - create_gold_table: Create Gold Iceberg table (unified analytics)
    - create_medallion_tables: Create all tables at once

Streaming Jobs (Phase 5):
    - bronze_ingestion: Kafka → Bronze (raw bytes)
    - silver_transformation: Bronze → Silver (validated, per-exchange)
    - gold_aggregation: Silver → Gold (unified, analytics-ready)
"""

__all__ = [
    "create_bronze_table",
    "create_silver_tables",
    "create_gold_table",
    "create_medallion_tables",
]
