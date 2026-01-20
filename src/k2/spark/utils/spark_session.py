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

    Production-Ready Features (2026-01-20):
    - Memory management: Tuned fraction (0.75) and storage (0.3) for streaming
    - Backpressure: Enabled to prevent consumer lag spikes
    - S3 connection pooling: Prevents MinIO connection exhaustion
    - Iceberg optimization: Auto-cleanup of old metadata (5 versions retained)
    - Checkpoint retention: Keeps last 10 batches for recovery

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
    session = (
        create_spark_session(app_name, master, log_level)
        # Memory Management (prevents OOM in long-running streams)
        .config("spark.memory.fraction", "0.75")  # 75% heap for execution/storage (up from 60%)
        .config("spark.memory.storageFraction", "0.3")  # 30% of memory.fraction for caching (down from 50%)
        .config("spark.executor.memoryOverhead", "384m")  # Off-heap memory for containers
        .config("spark.driver.memoryOverhead", "384m")  # Driver off-heap memory
        # Streaming Backpressure (prevents Kafka consumer lag)
        .config("spark.streaming.backpressure.enabled", "true")
        .config("spark.streaming.kafka.maxRatePerPartition", "1000")  # Max records/sec/partition
        # Checkpoint Management
        .config("spark.sql.streaming.checkpointLocation", checkpoint_location)
        .config("spark.sql.streaming.schemaInference", "true")
        .config("spark.sql.streaming.minBatchesToRetain", "10")  # Keep last 10 checkpoints
        # S3/MinIO Connection Pooling (prevents connection exhaustion)
        .config("spark.hadoop.fs.s3a.connection.maximum", "50")  # Max S3 connections
        .config("spark.hadoop.fs.s3a.threads.max", "20")  # Max upload threads
        .config("spark.hadoop.fs.s3a.connection.establish.timeout", "5000")  # 5s connect timeout
        .config("spark.hadoop.fs.s3a.connection.timeout", "200000")  # 200s socket timeout
        # Iceberg Write Optimization (prevents metadata bloat)
        .config("spark.sql.catalog.iceberg.write.metadata.delete-after-commit.enabled", "true")
        .config("spark.sql.catalog.iceberg.write.metadata.previous-versions-max", "5")  # Keep 5 versions
        .config("spark.sql.catalog.iceberg.write.target-file-size-bytes", "134217728")  # 128MB files
    )

    return session
