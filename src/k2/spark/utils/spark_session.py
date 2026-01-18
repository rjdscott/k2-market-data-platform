"""Spark session factory with Iceberg configuration.

This module provides utilities for creating PySpark sessions configured for:
- Apache Iceberg catalog integration (REST catalog)
- MinIO S3-compatible storage backend
- Kafka streaming integration
- Optimized for Medallion architecture (Bronze/Silver/Gold)

Usage:
    from k2.spark.utils.spark_session import create_spark_session

    spark = create_spark_session("K2-Bronze-Ingestion")
    # Use spark session for Iceberg operations
    spark.sql("CREATE TABLE IF NOT EXISTS iceberg.market_data.bronze_trades ...")
    spark.stop()
"""

from pyspark.sql import SparkSession


def create_spark_session(
    app_name: str,
    master: str = "spark://spark-master:7077",
    log_level: str = "WARN",
) -> SparkSession:
    """Create Spark session with Iceberg catalog configuration.

    Args:
        app_name: Application name for Spark UI identification
        master: Spark master URL (default: spark://spark-master:7077 for Docker)
        log_level: Spark log level (ERROR, WARN, INFO, DEBUG)

    Returns:
        Configured SparkSession with Iceberg catalog support

    Example:
        >>> spark = create_spark_session("K2-Create-Bronze-Table")
        >>> spark.sql("SHOW TABLES IN iceberg.market_data").show()
        >>> spark.stop()

    Configuration Details:
        - Catalog: Apache Iceberg REST catalog (http://iceberg-rest:8181)
        - Warehouse: MinIO S3-compatible storage (s3://warehouse/)
        - S3 Endpoint: http://minio:9000 (Docker internal network)
        - Credentials: From environment or defaults (admin/password)
    """
    return (
        SparkSession.builder.appName(app_name)
        .master(master)
        # Iceberg Catalog Configuration
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "rest")
        .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
        .config("spark.sql.catalog.iceberg.warehouse", "s3://warehouse/")
        # S3 Configuration (MinIO)
        .config("spark.sql.catalog.iceberg.s3.endpoint", "http://minio:9000")
        .config("spark.sql.catalog.iceberg.s3.access-key-id", "admin")
        .config("spark.sql.catalog.iceberg.s3.secret-access-key", "password")
        .config("spark.sql.catalog.iceberg.s3.path-style-access", "true")
        # Default to Iceberg catalog for SQL queries
        .config("spark.sql.defaultCatalog", "iceberg")
        # Iceberg table properties
        .config("spark.sql.catalog.iceberg.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        # Performance tuning
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        # Get or create session
        .getOrCreate()
    ).setLogLevel(log_level)


def create_streaming_spark_session(
    app_name: str,
    checkpoint_location: str = "/checkpoints",
    master: str = "spark://spark-master:7077",
    log_level: str = "WARN",
) -> SparkSession:
    """Create Spark session optimized for structured streaming.

    This variant includes Kafka and streaming-specific configurations
    for Bronze/Silver/Gold streaming jobs.

    Args:
        app_name: Application name for Spark UI
        checkpoint_location: Base directory for streaming checkpoints
        master: Spark master URL
        log_level: Spark log level

    Returns:
        Configured SparkSession with streaming support

    Example:
        >>> spark = create_streaming_spark_session("K2-Bronze-Ingestion")
        >>> kafka_df = spark.readStream.format("kafka") \\
        ...     .option("kafka.bootstrap.servers", "kafka:29092") \\
        ...     .option("subscribe", "market.crypto.trades.binance") \\
        ...     .load()
    """
    session = create_spark_session(app_name, master, log_level)

    # Add streaming-specific configurations
    session.conf.set("spark.sql.streaming.checkpointLocation", checkpoint_location)
    session.conf.set("spark.sql.streaming.schemaInference", "true")
    session.conf.set("spark.sql.streaming.stateStore.providerClass", "")

    return session
