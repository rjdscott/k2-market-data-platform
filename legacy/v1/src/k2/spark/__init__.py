"""K2 Spark integration module.

Provides utilities and jobs for Spark-based data processing:
- Bronze/Silver/Gold Medallion architecture
- Structured streaming from Kafka to Iceberg
- Data quality validation and transformation
- Spark session management

Submodules:
    utils: Spark session factory and utilities
    jobs: Bronze/Silver/Gold transformation jobs
    schemas: Spark DataFrame schemas for validation
"""

__all__ = ["utils", "jobs", "schemas"]
