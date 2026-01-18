#!/usr/bin/env python3
"""Create all Medallion architecture tables (Bronze → Silver → Gold).

This script creates the complete 3-layer Medallion architecture:
1. Bronze: Raw Kafka data (1 table)
2. Silver: Validated per-exchange data (2 tables: Binance + Kraken)
3. Gold: Unified analytics data (1 table)

Total: 4 Iceberg tables

Usage:
    # From host
    docker exec spark-master spark-submit \
      --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_medallion_tables.py

    # From within container
    spark-submit --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_medallion_tables.py
"""

import sys
from pathlib import Path

from pyspark.sql import SparkSession

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog configuration."""
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
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .getOrCreate()
    )


def create_bronze_table(spark, namespace: str = "market_data"):
    """Create Bronze table (raw Kafka data)."""
    table_name = f"iceberg.{namespace}.bronze_crypto_trades"

    print("\n[1/4] Creating Bronze Table...")
    print(f"      Table: {table_name}")

    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
    except Exception:
        pass

    spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_key STRING,
        avro_payload BINARY,
        topic STRING,
        partition INT,
        offset BIGINT,
        kafka_timestamp TIMESTAMP,
        ingestion_timestamp TIMESTAMP,
        ingestion_date DATE
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip'
    )
    COMMENT 'Bronze: Raw Kafka data (7-day retention)'
    """)

    print("      ✓ Bronze table created")
    return True


def create_silver_table(spark, exchange: str, namespace: str = "market_data"):
    """Create Silver table for specific exchange."""
    table_name = f"iceberg.{namespace}.silver_{exchange.lower()}_trades"

    print(
        f"\n[{2 if exchange == 'binance' else 3}/4] Creating Silver Table ({exchange.capitalize()})..."
    )
    print(f"      Table: {table_name}")

    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
    except Exception:
        pass

    spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_id STRING,
        trade_id STRING,
        symbol STRING,
        exchange STRING,
        asset_class STRING,
        timestamp BIGINT,
        price DECIMAL(18, 8),
        quantity DECIMAL(18, 8),
        currency STRING,
        side STRING,
        trade_conditions ARRAY<STRING>,
        source_sequence BIGINT,
        ingestion_timestamp BIGINT,
        platform_sequence BIGINT,
        vendor_data MAP<STRING, STRING>,
        exchange_date DATE
    )
    USING iceberg
    PARTITIONED BY (days(exchange_date))
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip'
    )
    COMMENT 'Silver: Validated {exchange.capitalize()} trades (30-day retention)'
    """)

    print(f"      ✓ Silver {exchange.capitalize()} table created")
    return True


def create_gold_table(spark, namespace: str = "market_data"):
    """Create Gold table (unified analytics)."""
    table_name = f"iceberg.{namespace}.gold_crypto_trades"

    print("\n[4/4] Creating Gold Table...")
    print(f"      Table: {table_name}")

    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
    except Exception:
        pass

    spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_id STRING,
        trade_id STRING,
        symbol STRING,
        exchange STRING,
        asset_class STRING,
        timestamp BIGINT,
        price DECIMAL(18, 8),
        quantity DECIMAL(18, 8),
        currency STRING,
        side STRING,
        trade_conditions ARRAY<STRING>,
        source_sequence BIGINT,
        ingestion_timestamp BIGINT,
        platform_sequence BIGINT,
        vendor_data MAP<STRING, STRING>,
        exchange_date DATE,
        exchange_hour INT
    )
    USING iceberg
    PARTITIONED BY (exchange_date, exchange_hour)
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'write.target-file-size-bytes' = '268435456'
    )
    COMMENT 'Gold: Unified multi-exchange trades (unlimited retention)'
    """)

    print("      ✓ Gold table created")
    return True


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Medallion Architecture - Table Creation")
    print("=" * 70)
    print("\nCreating complete 3-layer Medallion architecture:")
    print("  • Bronze: Raw Kafka data (1 table)")
    print("  • Silver: Validated per-exchange (2 tables)")
    print("  • Gold: Unified analytics (1 table)")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Create-Medallion-Tables")

    try:
        # Create namespace
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready")

        # Create all tables
        results = []
        results.append(create_bronze_table(spark))
        results.append(create_silver_table(spark, "binance"))
        results.append(create_silver_table(spark, "kraken"))
        results.append(create_gold_table(spark))

        # Summary
        print(f"\n{'=' * 70}")
        print("Medallion Architecture - Creation Summary")
        print(f"{'=' * 70}\n")

        print(f"Tables Created: {sum(results)}/4\n")

        # Show all tables
        print("All Medallion Tables:")
        tables = spark.sql("SHOW TABLES IN iceberg.market_data")
        tables.show(truncate=False)

        # Table details
        print("\nTable Schemas:\n")
        print("Bronze (8 fields):")
        print("  message_key, avro_payload, topic, partition, offset,")
        print("  kafka_timestamp, ingestion_timestamp, ingestion_date\n")

        print("Silver (16 fields):")
        print("  message_id, trade_id, symbol, exchange, asset_class, timestamp,")
        print("  price, quantity, currency, side, trade_conditions, source_sequence,")
        print("  ingestion_timestamp, platform_sequence, vendor_data, exchange_date\n")

        print("Gold (17 fields):")
        print("  [Same as Silver] + exchange_hour\n")

        print("Partitioning Strategy:")
        print("  • Bronze: PARTITIONED BY (days(ingestion_date))")
        print("  • Silver: PARTITIONED BY (days(exchange_date))")
        print("  • Gold:   PARTITIONED BY (exchange_date, exchange_hour)\n")

        print("Retention Policies:")
        print("  • Bronze: 7 days (reprocessing window)")
        print("  • Silver: 30 days (validated data)")
        print("  • Gold: Unlimited (analytics layer)\n")

        print("Compression:")
        print("  • Parquet: Zstd")
        print("  • Metadata: Gzip\n")

        if all(results):
            print("✓ All Medallion tables created successfully!\n")
            print("Next Steps:")
            print("  1. Implement Bronze ingestion job (Kafka → Bronze)")
            print("  2. Implement Silver transformation (Bronze → Silver)")
            print("  3. Implement Gold aggregation (Silver → Gold)")
            return 0
        else:
            print("⚠ Some tables failed to create\n")
            return 1

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
