"""Spark utilities module.

Provides Spark session factory and helper functions for:
- Iceberg catalog configuration
- Streaming job management
- S3 storage integration
"""

from k2.spark.utils.spark_session import create_spark_session, create_streaming_spark_session

__all__ = ["create_spark_session", "create_streaming_spark_session"]
