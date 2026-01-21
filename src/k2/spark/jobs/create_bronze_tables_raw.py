#!/usr/bin/env python3
"""Create Bronze tables for raw exchange data.

This script creates Bronze Iceberg tables that store exchange-native trade data
without any transformation. This implements industry best practice for the
Bronze layer of the Medallion Architecture.

Architecture:
- bronze_binance_trades: Raw Binance trade data (native format)
- bronze_kraken_trades: Raw Kraken trade data (native format)

Partitioning: Daily partitioning by ingestion_date for efficient retention management
Retention: 7 days (configurable via table properties)

Usage:
    docker exec k2-spark-master bash -c "cd /opt/k2 && /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1' \
      --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1' \
      src/k2/spark/jobs/create_bronze_tables_raw.py"
"""

import sys

from pyspark.sql import SparkSession


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog configured."""
    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.iceberg.type", "rest")
        .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
        .config("spark.sql.catalog.iceberg.warehouse", "s3://warehouse/")
        .config("spark.sql.catalog.iceberg.s3.endpoint", "http://minio:9000")
        .config("spark.sql.catalog.iceberg.s3.access-key-id", "admin")
        .config("spark.sql.catalog.iceberg.s3.secret-access-key", "password")
        .config("spark.sql.catalog.iceberg.s3.path-style-access", "true")
        # S3/MinIO configuration
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
        .config("spark.hadoop.fs.s3a.access.key", "admin")
        .config("spark.hadoop.fs.s3a.secret.key", "password")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config(
            "spark.hadoop.fs.s3a.aws.credentials.provider",
            "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
        )
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        # AWS SDK v2 region configuration
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .getOrCreate()
    )


def create_bronze_binance_table(spark: SparkSession, namespace: str = "market_data") -> bool:
    """Create Bronze table for raw Binance trades."""
    table_name = f"iceberg.{namespace}.bronze_binance_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating Bronze Table: {table_name}")
    print("Schema: Raw Binance native format")
    print(f"{'=' * 70}\n")

    # Drop old table
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Dropped old table (if existed)")
    except Exception as e:
        print(f"⚠ Table drop: {e}")

    # Create Bronze Binance table with raw schema
    # Note: Using descriptive names instead of Binance's single-char fields (e, E, s, t, etc.)
    # to avoid Spark SQL case-sensitivity issues
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        event_type STRING COMMENT 'Event type (always trade) - Binance field: e',
        event_time_ms BIGINT COMMENT 'Event time milliseconds - Binance field: E',
        symbol STRING COMMENT 'Symbol (e.g., BTCUSDT) - Binance field: s',
        trade_id BIGINT COMMENT 'Trade ID (Binance native) - Binance field: t',
        price STRING COMMENT 'Price (string for precision) - Binance field: p',
        quantity STRING COMMENT 'Quantity (string for precision) - Binance field: q',
        trade_time_ms BIGINT COMMENT 'Trade time milliseconds - Binance field: T',
        is_buyer_maker BOOLEAN COMMENT 'Is buyer maker - Binance field: m',
        is_best_match BOOLEAN COMMENT 'Is best match (optional) - Binance field: M',
        ingestion_timestamp BIGINT COMMENT 'Platform ingestion timestamp (microseconds)',
        ingestion_date DATE COMMENT 'Partition key (derived from ingestion_timestamp)'
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'write.target-file-size-bytes' = '134217728',
        'commit.retry.num-retries' = '5',
        'history.expire.max-snapshot-age-ms' = '604800000'
    )
    COMMENT 'Bronze layer: Raw Binance trades (exchange-native format, 7-day retention)'
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
        print("  Partitioning: days(ingestion_date)")
        print("  Retention: 7 days")
        print("  Compression: Zstd")
        print("  Target file size: 128 MB")
        return True
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def create_bronze_kraken_table(spark: SparkSession, namespace: str = "market_data") -> bool:
    """Create Bronze table for raw Kraken trades."""
    table_name = f"iceberg.{namespace}.bronze_kraken_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating Bronze Table: {table_name}")
    print("Schema: Raw Kraken native format (flattened)")
    print(f"{'=' * 70}\n")

    # Drop old table
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Dropped old table (if existed)")
    except Exception as e:
        print(f"⚠ Table drop: {e}")

    # Create Bronze Kraken table with raw schema
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        channel_id BIGINT COMMENT 'Kraken WebSocket channel ID',
        price STRING COMMENT 'Trade price (string for precision preservation)',
        volume STRING COMMENT 'Trade volume (string for precision preservation)',
        timestamp STRING COMMENT 'Trade timestamp (Kraken format: seconds.microseconds)',
        side STRING COMMENT 'Trade side: b = buy, s = sell',
        order_type STRING COMMENT 'Order type: l = limit, m = market',
        misc STRING COMMENT 'Miscellaneous info (often empty)',
        pair STRING COMMENT 'Trading pair (e.g., XBT/USD)',
        ingestion_timestamp BIGINT COMMENT 'Platform ingestion timestamp (microseconds)',
        ingestion_date DATE COMMENT 'Partition key (derived from ingestion_timestamp)'
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'write.target-file-size-bytes' = '134217728',
        'commit.retry.num-retries' = '5',
        'history.expire.max-snapshot-age-ms' = '604800000'
    )
    COMMENT 'Bronze layer: Raw Kraken trades (exchange-native format, 7-day retention)'
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
        print("  Partitioning: days(ingestion_date)")
        print("  Retention: 7 days")
        print("  Compression: Zstd")
        print("  Target file size: 128 MB")
        return True
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("Create Bronze Tables (Raw Exchange Data)")
    print("Medallion Architecture: True Bronze Layer")
    print("=" * 70 + "\n")

    spark = create_spark_session("K2-Create-Bronze-Tables-Raw")

    try:
        # Create namespace
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready\n")

        # Create Bronze tables
        success_count = 0

        if create_bronze_binance_table(spark):
            success_count += 1

        if create_bronze_kraken_table(spark):
            success_count += 1

        # Summary
        print(f"\n{'=' * 70}")
        print(f"Summary: {success_count}/2 tables created")
        print(f"{'=' * 70}")
        print("\nBronze Layer Design:")
        print("  • Raw exchange-native data (no transformation)")
        print("  • Daily partitioning for efficient retention")
        print("  • 7-day retention (configurable)")
        print("  • Preserves complete audit trail")
        print("\nData Flow:")
        print("  WebSocket → Kafka (raw) → Bronze (raw) → Silver (V2 transform)")
        print(f"\n{'=' * 70}\n")

        return 0 if success_count == 2 else 1

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
