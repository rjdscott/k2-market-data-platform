#!/usr/bin/env python3
"""Fix Bronze Tables - Drop and recreate with raw_bytes schema.

This script uses PySpark (not spark-sql CLI) to properly interact with Iceberg catalog.
The spark-sql CLI has issues with multi-catalog namespaces.

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \\
      --master local[2] \\
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \\
      /opt/k2/scripts/migrations/fix_bronze_tables.py
"""

from pyspark.sql import SparkSession


def create_spark_session() -> SparkSession:
    """Create Spark session with Iceberg catalog."""
    return (
        SparkSession.builder.appName("K2-Bronze-Table-Fix")
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
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .getOrCreate()
    )


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("Bronze Table Fix - Drop and Recreate with Raw Bytes Schema")
    print("=" * 70 + "\n")

    spark = create_spark_session()

    try:
        # Drop existing tables
        tables = ["bronze_binance_trades", "bronze_kraken_trades"]

        print("Step 1: Dropping existing Bronze tables...")
        print("-" * 70)
        for table in tables:
            full_table_name = f"iceberg.market_data.{table}"
            print(f"  → Dropping {table}...")
            try:
                spark.sql(f"DROP TABLE IF EXISTS {full_table_name}")
                print(f"    ✓ Dropped {table}")
            except Exception as e:
                print(f"    ✗ Error dropping {table}: {e}")

        print("\nStep 2: Creating Bronze tables with raw_bytes schema...")
        print("-" * 70)

        # Create bronze_binance_trades
        print("  → Creating bronze_binance_trades...")
        binance_ddl = """
        CREATE TABLE IF NOT EXISTS iceberg.market_data.bronze_binance_trades (
            raw_bytes BINARY COMMENT 'Full Kafka value (5-byte Schema Registry header + Avro payload)',
            topic STRING COMMENT 'Source Kafka topic',
            partition INT COMMENT 'Kafka partition',
            offset BIGINT COMMENT 'Kafka offset',
            kafka_timestamp TIMESTAMP COMMENT 'Kafka message timestamp',
            ingestion_timestamp TIMESTAMP COMMENT 'When ingested to Bronze',
            ingestion_date DATE COMMENT 'Partition key (daily)'
        )
        USING iceberg
        PARTITIONED BY (days(ingestion_date))
        TBLPROPERTIES (
            'write.format.default' = 'parquet',
            'write.target-file-size-bytes' = '134217728',
            'format-version' = '2',
            'write.parquet.compression-codec' = 'zstd',
            'comment' = 'Bronze layer - Immutable raw Kafka data for replayability (Decision #011)'
        )
        """
        spark.sql(binance_ddl)
        print("    ✓ Created bronze_binance_trades")

        # Create bronze_kraken_trades
        print("  → Creating bronze_kraken_trades...")
        kraken_ddl = """
        CREATE TABLE IF NOT EXISTS iceberg.market_data.bronze_kraken_trades (
            raw_bytes BINARY COMMENT 'Full Kafka value (5-byte Schema Registry header + Avro payload)',
            topic STRING COMMENT 'Source Kafka topic',
            partition INT COMMENT 'Kafka partition',
            offset BIGINT COMMENT 'Kafka offset',
            kafka_timestamp TIMESTAMP COMMENT 'Kafka message timestamp',
            ingestion_timestamp TIMESTAMP COMMENT 'When ingested to Bronze',
            ingestion_date DATE COMMENT 'Partition key (daily)'
        )
        USING iceberg
        PARTITIONED BY (days(ingestion_date))
        TBLPROPERTIES (
            'write.format.default' = 'parquet',
            'write.target-file-size-bytes' = '67108864',
            'format-version' = '2',
            'write.parquet.compression-codec' = 'zstd',
            'comment' = 'Bronze layer - Immutable raw Kafka data for replayability (Decision #011)'
        )
        """
        spark.sql(kraken_ddl)
        print("    ✓ Created bronze_kraken_trades")

        print("\nStep 3: Verifying table schemas...")
        print("-" * 70)
        for table in tables:
            full_table_name = f"iceberg.market_data.{table}"
            print(f"  → Verifying {table}...")
            schema = spark.sql(f"DESCRIBE {full_table_name}")

            # Check for raw_bytes field
            has_raw_bytes = False
            for row in schema.collect():
                if row.col_name == "raw_bytes":
                    has_raw_bytes = True
                    break

            if has_raw_bytes:
                print(f"    ✓ {table} has raw_bytes field")
            else:
                print(f"    ✗ {table} missing raw_bytes field!")
                print("\n    Schema:")
                schema.show(truncate=False)

        print("\n" + "=" * 70)
        print("✓ Bronze tables fixed successfully!")
        print("=" * 70)
        print("\nNext Steps:")
        print("  1. Restart Bronze streaming jobs:")
        print("     docker compose restart bronze-binance-stream bronze-kraken-stream")
        print()
        print("  2. Check logs:")
        print("     docker logs k2-bronze-binance-stream -f")
        print()
        print("  3. Verify data ingestion:")
        print("     Wait 30 seconds, then run validation script")
        print("     ./scripts/migrations/validate_bronze_raw_bytes.sh")
        print()

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        spark.stop()

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
