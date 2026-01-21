#!/usr/bin/env python3
"""Recreate Silver tables with professional partitioning strategy.

New partitioning: (exchange_date, symbol)
- Enables efficient time-range queries (date first)
- Enables efficient symbol-specific queries (symbol second)
- Optimal balance for Silver layer query patterns

Usage:
    docker exec k2-spark-master bash -c "cd /opt/k2 && /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1' \
      --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1' \
      src/k2/spark/jobs/recreate_silver_tables_partitioned.py"
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


def recreate_silver_table(spark, exchange: str, namespace: str = "market_data"):
    """Recreate Silver table with (exchange_date, symbol) partitioning."""
    table_name = f"iceberg.{namespace}.silver_{exchange.lower()}_trades"

    print(f"\n{'=' * 70}")
    print(f"Recreating Silver Table: {table_name}")
    print("New Partitioning: (exchange_date, symbol)")
    print(f"{'=' * 70}\n")

    # Drop old table
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Dropped old table")
    except Exception as e:
        print(f"⚠ Table drop: {e}")

    # Create Silver table with professional partitioning
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_id STRING COMMENT 'UUID v4 for deduplication',
        trade_id STRING COMMENT '{exchange.capitalize()} trade ID',
        symbol STRING COMMENT 'Trading pair (e.g., BTCUSDT, BTCUSD)',
        exchange STRING COMMENT 'Always {exchange.upper()}',
        asset_class STRING COMMENT 'Always crypto',
        timestamp BIGINT COMMENT 'Trade execution time (microseconds)',
        price DECIMAL(18, 8) COMMENT 'Trade price',
        quantity DECIMAL(18, 8) COMMENT 'Trade quantity',
        currency STRING COMMENT 'Quote currency (USDT, USD, etc.)',
        side STRING COMMENT 'Trade side: BUY or SELL',
        trade_conditions ARRAY<STRING> COMMENT 'Trade conditions',
        source_sequence BIGINT COMMENT 'Exchange sequence number',
        ingestion_timestamp BIGINT COMMENT 'Platform ingestion time',
        platform_sequence BIGINT COMMENT 'Platform sequence number',
        vendor_data MAP<STRING, STRING> COMMENT '{exchange.capitalize()}-specific fields',
        exchange_date DATE COMMENT 'Partition key 1: Trade date (from timestamp)',
        partition_symbol STRING COMMENT 'Partition key 2: Symbol for efficient querying'
    )
    USING iceberg
    PARTITIONED BY (exchange_date, partition_symbol)
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'write.target-file-size-bytes' = '134217728',
        'commit.retry.num-retries' = '5',
        'write.distribution-mode' = 'hash'
    )
    COMMENT 'Silver layer: Validated {exchange.capitalize()} trades (partitioned by date + symbol for optimal queries)'
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
        print("  Partitioning: (exchange_date, partition_symbol)")
        print("  Target file size: 128 MB")
        print("  Compression: Zstd")
        return True
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("Recreate Silver Tables with Professional Partitioning")
    print("Strategy: (exchange_date, symbol)")
    print("=" * 70 + "\n")

    spark = create_spark_session("K2-Recreate-Silver-Tables-Partitioned")

    try:
        # Create namespace
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready\n")

        # Recreate Silver tables with new partitioning
        exchanges = ["binance", "kraken"]
        success_count = 0

        for exchange in exchanges:
            if recreate_silver_table(spark, exchange):
                success_count += 1

        # Summary
        print(f"\n{'=' * 70}")
        print(f"Summary: {success_count}/{len(exchanges)} tables created")
        print(f"{'=' * 70}")
        print("\nPartitioning Strategy Benefits:")
        print("  1. Time-range queries: Prune by exchange_date (primary partition)")
        print("  2. Symbol queries: Prune by partition_symbol (secondary partition)")
        print("  3. Combined queries: Optimal pruning (date + symbol)")
        print("\nExample Query:")
        print("  SELECT * FROM silver_binance_trades")
        print("  WHERE exchange_date = '2026-01-18' AND partition_symbol = 'BTCUSDT'")
        print("  → Prunes to 1 partition (excellent performance)")
        print(f"\n{'=' * 70}\n")

        return 0 if success_count == len(exchanges) else 1

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
