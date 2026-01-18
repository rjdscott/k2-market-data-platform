#!/usr/bin/env python3
"""Create Silver Iceberg tables (standalone - no k2 dependencies).

Silver Layer Purpose:
- Validated, per-exchange data (Binance, Kraken)
- Deserialized V2 Avro schema (15 fields)
- Data quality checks passed
- 30-day retention for Silver data
- Partitioned by exchange date for efficient queries

Tables Created:
- silver_binance_trades: Validated Binance trades (V2 schema)
- silver_kraken_trades: Validated Kraken trades (V2 schema)

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1' \
      --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1' \
      /opt/k2/src/k2/spark/jobs/standalone_create_silver_tables.py
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
        .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        # AWS SDK v2 region configuration
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .getOrCreate()
    )


def create_silver_table(spark, exchange: str, namespace: str = "market_data"):
    """Create Silver Iceberg table for specific exchange."""
    table_name = f"iceberg.{namespace}.silver_{exchange.lower()}_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating Silver Table: {table_name}")
    print(f"{'=' * 70}\n")

    # Drop table if exists (for clean setup)
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Cleaned up existing table")
    except Exception as e:
        print(f"⚠ Table cleanup: {e}")

    # Create Silver table with V2 schema (16 fields)
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
        exchange_date DATE COMMENT 'Partition key (derived from timestamp)'
    )
    USING iceberg
    PARTITIONED BY (exchange_date)
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'commit.retry.num-retries' = '5'
    )
    COMMENT 'Silver layer: Validated {exchange.capitalize()} trades (V2 schema, 30-day retention)'
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
        return True
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Silver Tables Creation (Standalone)")
    print("=" * 70 + "\n")

    spark = create_spark_session("K2-Create-Silver-Tables")

    try:
        # Create namespace
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready\n")

        # Create Silver tables
        exchanges = ["binance", "kraken"]
        success_count = 0

        for exchange in exchanges:
            if create_silver_table(spark, exchange):
                success_count += 1

        # Summary
        print(f"\n{'=' * 70}")
        print(f"Summary: {success_count}/{len(exchanges)} tables created")
        print("=" * 70)

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
