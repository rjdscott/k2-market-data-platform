#!/usr/bin/env python3
"""Create all Medallion architecture tables (Bronze → Silver → Gold).

This script creates the complete 3-layer Medallion architecture with per-exchange Bronze:
1. Bronze: Raw Kafka data per exchange (2 tables: Binance + Kraken)
2. Silver: Validated per-exchange data (2 tables: Binance + Kraken)
3. Gold: Unified analytics data (1 table)

Total: 5 Iceberg tables (2 Bronze + 2 Silver + 1 Gold)

Architecture Decision:
- Bronze per exchange (NOT unified) for production-grade isolation
- See: docs/architecture/decisions/ADR-002-bronze-per-exchange.md

Usage:
    # From host
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/jobs/create_medallion_tables.py

    # From within container
    /opt/spark/bin/spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/jobs/create_medallion_tables.py
"""

import sys
from pathlib import Path

from pyspark.sql import SparkSession

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# Exchange-specific configuration
EXCHANGE_CONFIG = {
    "binance": {
        "bronze_retention_days": 7,
        "bronze_file_size_mb": 128,
        "silver_retention_days": 30,
        "silver_file_size_mb": 128,
        "topic": "market.crypto.trades.binance",
    },
    "kraken": {
        "bronze_retention_days": 14,
        "bronze_file_size_mb": 64,
        "silver_retention_days": 30,
        "silver_file_size_mb": 128,
        "topic": "market.crypto.trades.kraken",
    },
}


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


def create_bronze_table(spark, exchange: str, namespace: str = "market_data"):
    """Create Bronze table for specific exchange (raw Kafka data)."""
    config = EXCHANGE_CONFIG[exchange.lower()]
    table_name = f"iceberg.{namespace}.bronze_{exchange.lower()}_trades"
    step_num = 1 if exchange.lower() == "binance" else 2

    print(f"\n[{step_num}/5] Creating Bronze Table ({exchange.capitalize()})...")
    print(f"      Table: {table_name}")
    print(f"      Retention: {config['bronze_retention_days']} days")

    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
    except Exception:
        pass

    target_file_size = config["bronze_file_size_mb"] * 1024 * 1024
    spark.sql(
        f"""
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
        'write.metadata.compression-codec' = 'gzip',
        'write.target-file-size-bytes' = '{target_file_size}'
    )
    COMMENT 'Bronze: Raw {exchange.capitalize()} Kafka data ({config["bronze_retention_days"]}-day retention)'
    """
    )

    print(f"      ✓ Bronze {exchange.capitalize()} table created")
    return True


def create_silver_table(spark, exchange: str, namespace: str = "market_data"):
    """Create Silver table for specific exchange."""
    config = EXCHANGE_CONFIG[exchange.lower()]
    table_name = f"iceberg.{namespace}.silver_{exchange.lower()}_trades"
    step_num = 3 if exchange.lower() == "binance" else 4

    print(f"\n[{step_num}/5] Creating Silver Table ({exchange.capitalize()})...")
    print(f"      Table: {table_name}")

    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
    except Exception:
        pass

    target_file_size = config["silver_file_size_mb"] * 1024 * 1024
    spark.sql(
        f"""
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
        'write.metadata.compression-codec' = 'gzip',
        'write.target-file-size-bytes' = '{target_file_size}'
    )
    COMMENT 'Silver: Validated {exchange.capitalize()} trades ({config["silver_retention_days"]}-day retention)'
    """
    )

    print(f"      ✓ Silver {exchange.capitalize()} table created")
    return True


def create_gold_table(spark, namespace: str = "market_data"):
    """Create Gold table (unified analytics)."""
    table_name = f"iceberg.{namespace}.gold_crypto_trades"

    print("\n[5/5] Creating Gold Table...")
    print(f"      Table: {table_name}")

    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
    except Exception:
        pass

    spark.sql(
        f"""
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
        'write.target-file-size-bytes' = '268435456',
        'write.distribution-mode' = 'hash'
    )
    COMMENT 'Gold: Unified multi-exchange trades (unlimited retention)'
    """
    )

    print("      ✓ Gold table created")
    return True


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Medallion Architecture - Table Creation")
    print("Per-Exchange Bronze Design (Production-Grade Isolation)")
    print("=" * 70)
    print("\nCreating complete 3-layer Medallion architecture:")
    print("  • Bronze: Raw Kafka data per exchange (2 tables)")
    print("  • Silver: Validated per-exchange (2 tables)")
    print("  • Gold: Unified analytics (1 table)")
    print(f"\n{'=' * 70}\n")

    # Create Spark session
    spark = create_spark_session("K2-Create-Medallion-Tables")

    try:
        # Create namespace
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready")

        # Create all tables (5 total)
        results = []

        # Bronze tables (per exchange)
        results.append(create_bronze_table(spark, "binance"))
        results.append(create_bronze_table(spark, "kraken"))

        # Silver tables (per exchange)
        results.append(create_silver_table(spark, "binance"))
        results.append(create_silver_table(spark, "kraken"))

        # Gold table (unified)
        results.append(create_gold_table(spark))

        # Summary
        print(f"\n{'=' * 70}")
        print("Medallion Architecture - Creation Summary")
        print(f"{'=' * 70}\n")

        print(f"Tables Created: {sum(results)}/5\n")

        # Show all tables
        print("All Medallion Tables:")
        tables = spark.sql("SHOW TABLES IN iceberg.market_data")
        tables.show(truncate=False)

        # Table details
        print("\nTable Schemas:\n")
        print("Bronze (8 fields per exchange):")
        print("  • bronze_binance_trades: 7-day retention, 128 MB files")
        print("  • bronze_kraken_trades: 14-day retention, 64 MB files")
        print("  Fields: message_key, avro_payload, topic, partition, offset,")
        print("          kafka_timestamp, ingestion_timestamp, ingestion_date\n")

        print("Silver (16 fields per exchange):")
        print("  • silver_binance_trades: 30-day retention")
        print("  • silver_kraken_trades: 30-day retention")
        print("  Fields: message_id, trade_id, symbol, exchange, asset_class, timestamp,")
        print("          price, quantity, currency, side, trade_conditions, source_sequence,")
        print("          ingestion_timestamp, platform_sequence, vendor_data, exchange_date\n")

        print("Gold (17 fields):")
        print("  • gold_crypto_trades: Unlimited retention")
        print("  Fields: [Same as Silver] + exchange_hour\n")

        print("Partitioning Strategy:")
        print("  • Bronze: PARTITIONED BY (days(ingestion_date))")
        print("  • Silver: PARTITIONED BY (days(exchange_date))")
        print("  • Gold:   PARTITIONED BY (exchange_date, exchange_hour)\n")

        print("Architecture Benefits (Bronze Per Exchange):")
        print("  • Isolation: Independent failures and maintenance per exchange")
        print("  • Scalability: Independent resource allocation (Binance 3 workers, Kraken 1)")
        print("  • Observability: Clear per-exchange metrics and lag monitoring")
        print("  • Flexibility: Different retention policies (Binance 7d, Kraken 14d)")
        print("  • Topic Alignment: Clean 1:1 topic-to-table mapping\n")

        print("Retention Policies:")
        print("  • Bronze Binance: 7 days (high volume, aggressive cleanup)")
        print("  • Bronze Kraken: 14 days (lower volume, extended retention)")
        print("  • Silver: 30 days (validated data)")
        print("  • Gold: Unlimited (analytics layer)\n")

        print("Compression:")
        print("  • Parquet: Zstd")
        print("  • Metadata: Gzip\n")

        if all(results):
            print("✓ All Medallion tables created successfully!\n")
            print("Next Steps:")
            print("  1. Implement Bronze ingestion jobs (per-exchange)")
            print("     - bronze_binance_ingestion.py (Kafka → bronze_binance_trades)")
            print("     - bronze_kraken_ingestion.py (Kafka → bronze_kraken_trades)")
            print("  2. Implement Silver transformation (Bronze → Silver per exchange)")
            print("  3. Implement Gold aggregation (Silver → Gold union)")
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
