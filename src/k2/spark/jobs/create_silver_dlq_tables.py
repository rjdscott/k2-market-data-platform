#!/usr/bin/env python3
"""Create Silver and DLQ Iceberg tables for Step 11.

This script creates:
1. silver_binance_trades (updated with validation metadata)
2. silver_kraken_trades (updated with validation metadata)
3. silver_dlq_trades (Dead Letter Queue for validation failures)

Medallion Architecture - Silver Layer:
- Silver: Validated, deserialized trades from Bronze
- DLQ: Invalid records with error tracking for observability

Usage:
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master 'local[2]' \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/jobs/create_silver_dlq_tables.py
"""

import sys
from pathlib import Path
from pyspark.sql import SparkSession

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog."""
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
        .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
        .config("spark.hadoop.fs.s3a.access.key", "admin")
        .config("spark.hadoop.fs.s3a.secret.key", "password")
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.aws.credentials.provider", "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
        .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
        .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
        .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
        .getOrCreate()
    )


def create_silver_table(spark, exchange: str):
    """Create Silver Iceberg table for specific exchange with validation metadata.

    Silver Schema = V2 Avro (15 fields) + Validation Metadata (3 fields)
    """
    table_name = f"iceberg.market_data.silver_{exchange.lower()}_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating Silver Table: {table_name}")
    print(f"{'=' * 70}\n")

    # Drop table if exists
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Cleaned up existing table")
    except Exception as e:
        print(f"⚠ Table cleanup warning: {e}")

    # Create Silver table with V2 schema + validation metadata
    # Industry best practice: Keep Silver close to source schema (no derived columns)
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        -- V2 Avro Schema Fields (15 fields from trade_v2.avsc)
        message_id STRING COMMENT 'UUID v4 for deduplication',
        trade_id STRING COMMENT 'Exchange trade ID (format: {exchange.upper()}-{{id}})',
        symbol STRING COMMENT 'Trading pair (e.g., BTCUSDT, ETHUSD)',
        exchange STRING COMMENT 'Exchange code: {exchange.upper()}',
        asset_class STRING COMMENT 'Asset class: crypto',
        timestamp BIGINT COMMENT 'Exchange trade execution time (microseconds since epoch)',
        price DECIMAL(18, 8) COMMENT 'Trade price (Decimal 18,8)',
        quantity DECIMAL(18, 8) COMMENT 'Trade quantity (Decimal 18,8)',
        currency STRING COMMENT 'Quote currency (USDT, USD, BTC)',
        side STRING COMMENT 'Trade side: BUY, SELL',
        trade_conditions ARRAY<STRING> COMMENT 'Trade condition codes (usually empty for crypto)',
        source_sequence BIGINT COMMENT 'Exchange sequence number (nullable)',
        ingestion_timestamp BIGINT COMMENT 'Platform ingestion time (microseconds)',
        platform_sequence BIGINT COMMENT 'Platform sequence number (nullable)',
        vendor_data STRING COMMENT 'Exchange-specific fields as JSON string',

        -- Silver Metadata (Added in Silver Layer for observability)
        validation_timestamp TIMESTAMP COMMENT 'When validated in Silver transformation',
        bronze_ingestion_timestamp TIMESTAMP COMMENT 'From Bronze (for latency tracking)',
        schema_id INT COMMENT 'Schema Registry ID used for deserialization'
    )
    USING iceberg
    PARTITIONED BY (days(validation_timestamp))
    TBLPROPERTIES (
        'write.format.default' = 'parquet',
        'write.target-file-size-bytes' = '67108864',
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'comment' = 'Silver layer - Validated {exchange.capitalize()} trades (V2 schema + validation metadata)'
    )
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
        print(f"  • Schema: 18 fields (15 V2 + 3 metadata)")
        print(f"  • Partitioning: days(validation_timestamp)")
        print(f"  • Target file size: 64 MB")
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        return False

    # Verify table
    print("\nVerifying table schema...")
    schema_df = spark.sql(f"DESCRIBE {table_name}")
    schema_count = schema_df.count()
    print(f"✓ Field count: {schema_count}")

    return True


def create_dlq_table(spark):
    """Create DLQ (Dead Letter Queue) table for validation failures.

    Industry best practice: Route invalid records to DLQ for observability and recovery.
    """
    table_name = "iceberg.market_data.silver_dlq_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating DLQ Table: {table_name}")
    print(f"{'=' * 70}\n")

    # Drop table if exists
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Cleaned up existing table")
    except Exception as e:
        print(f"⚠ Table cleanup warning: {e}")

    # Create DLQ table
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        raw_record BINARY COMMENT 'Original Bronze raw_bytes (for replay)',
        error_reason STRING COMMENT 'Validation failure reason (e.g., price_must_be_positive)',
        error_type STRING COMMENT 'Error category (e.g., price_negative, symbol_null)',
        error_timestamp TIMESTAMP COMMENT 'When validation failed',
        bronze_source STRING COMMENT 'Source Bronze table (e.g., bronze_binance_trades)',
        kafka_offset BIGINT COMMENT 'Bronze Kafka offset (for tracking)',
        schema_id INT COMMENT 'Schema Registry ID (nullable if deserialization failed)',
        dlq_date DATE COMMENT 'Partition key (daily)'
    )
    USING iceberg
    PARTITIONED BY (days(dlq_date))
    TBLPROPERTIES (
        'write.format.default' = 'parquet',
        'write.target-file-size-bytes' = '33554432',
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'comment' = 'Dead Letter Queue - Invalid records from Silver validation'
    )
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
        print(f"  • Schema: 8 fields (raw_record + error tracking)")
        print(f"  • Partitioning: days(dlq_date)")
        print(f"  • Target file size: 32 MB (smaller, DLQ should be rare)")
        print(f"  • Purpose: Track validation failures for observability")
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        return False

    # Verify table
    print("\nVerifying table schema...")
    schema_df = spark.sql(f"DESCRIBE {table_name}")
    schema_count = schema_df.count()
    print(f"✓ Field count: {schema_count}")

    return True


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Silver + DLQ Tables Creation (Step 11)")
    print("Medallion Architecture - Silver Layer with Dead Letter Queue")
    print("=" * 70 + "\n")

    spark = create_spark_session("K2-Create-Silver-DLQ-Tables")

    try:
        # Show Spark config
        print("Spark Configuration:")
        print(f"  • Catalog: {spark.conf.get('spark.sql.catalog.iceberg')}")
        print(f"  • Catalog URI: {spark.conf.get('spark.sql.catalog.iceberg.uri')}")
        print(f"  • Warehouse: {spark.conf.get('spark.sql.catalog.iceberg.warehouse')}")
        print()

        # Create namespace if not exists
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready\n")

        # Create Silver tables
        exchanges = ["binance", "kraken"]
        silver_success = sum(create_silver_table(spark, exchange) for exchange in exchanges)

        # Create DLQ table
        dlq_success = create_dlq_table(spark)

        # Summary
        print(f"\n{'=' * 70}")
        print("Silver + DLQ Tables Creation Summary")
        print(f"{'=' * 70}\n")
        print(f"Silver Tables: {silver_success}/{len(exchanges)} created")
        print("  ✓ silver_binance_trades: 18 fields (15 V2 + 3 metadata)")
        print("  ✓ silver_kraken_trades: 18 fields (15 V2 + 3 metadata)")
        print(f"\nDLQ Table: {'✓' if dlq_success else '✗'} created")
        print("  ✓ silver_dlq_trades: 8 fields (raw_record + error tracking)")
        print("\nIndustry Best Practices Implemented:")
        print("  • Medallion Architecture: Bronze → Silver (validated) → Gold")
        print("  • DLQ Pattern: Invalid records tracked for observability")
        print("  • Silver Metadata: validation_timestamp, bronze_ingestion_timestamp, schema_id")
        print("  • Partition Strategy: Daily partitions for efficient queries")
        print("  • Compression: Zstd for optimal compression/speed balance")
        print("\nNext Steps:")
        print("  1. Create Avro deserialization UDF (strip Schema Registry headers)")
        print("  2. Create validation logic with DLQ routing")
        print("  3. Create Silver transformation Spark jobs (Binance, Kraken)")

        if silver_success == len(exchanges) and dlq_success:
            print("\n✓ All tables created successfully")
            return 0
        else:
            print("\n⚠ Some tables failed to create")
            return 1

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
