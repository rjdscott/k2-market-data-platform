#!/usr/bin/env python3
"""Create Bronze Iceberg tables for raw Kafka data - Per Exchange.

Bronze Layer Purpose (Per-Exchange Design):
- Landing zone for raw Kafka messages (no deserialization)
- Stores Avro bytes for reprocessing if needed
- Per-exchange tables for isolation, independent scaling, and blast radius control
- Configurable retention per exchange (Binance: 7d, Kraken: 14d)
- Partitioned by ingestion date for efficient cleanup

Architecture Decision:
- Bronze per exchange (NOT unified) follows industry best practices
- Rationale: Isolation, independent scaling, clearer observability
- Pattern used at: Netflix, Uber, Airbnb, Databricks
- See: docs/architecture/decisions/ADR-002-bronze-per-exchange.md

Usage:
    # From host - Create specific exchange Bronze table
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/jobs/create_bronze_table.py binance

    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/jobs/create_bronze_table.py kraken

    # Create all Bronze tables
    docker exec k2-spark-master /opt/spark/bin/spark-submit \
      --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/jobs/create_bronze_table.py all
"""

import sys
from pathlib import Path

from pyspark.sql import SparkSession

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


# Exchange-specific configuration
EXCHANGE_CONFIG = {
    "binance": {
        "retention_days": 7,
        "target_file_size_mb": 128,
        "description": "High-volume exchange, aggressive cleanup",
        "topic": "market.crypto.trades.binance",
    },
    "kraken": {
        "retention_days": 14,
        "target_file_size_mb": 64,
        "description": "Lower-volume exchange, extended retention",
        "topic": "market.crypto.trades.kraken",
    },
}


def create_spark_session(app_name: str) -> SparkSession:
    """Create Spark session with Iceberg catalog configuration.

    Args:
        app_name: Application name for Spark UI

    Returns:
        Configured SparkSession with Iceberg catalog
    """
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


def create_bronze_table(spark, exchange: str, namespace: str = "market_data") -> tuple[bool, str]:
    """Create Bronze Iceberg table for specific exchange.

    Args:
        spark: SparkSession with Iceberg catalog configured
        exchange: Exchange name (binance, kraken, etc.)
        namespace: Iceberg namespace (default: market_data)

    Returns:
        Tuple of (success: bool, table_name: str)
    """
    exchange_lower = exchange.lower()

    if exchange_lower not in EXCHANGE_CONFIG:
        print(f"✗ Unknown exchange: {exchange}")
        print(f"  Supported exchanges: {', '.join(EXCHANGE_CONFIG.keys())}")
        return False, ""

    config = EXCHANGE_CONFIG[exchange_lower]
    table_name = f"iceberg.{namespace}.bronze_{exchange_lower}_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating Bronze Table: {exchange.upper()}")
    print(f"{'=' * 70}\n")
    print(f"Table: {table_name}")
    print(f"Configuration: {config['description']}")
    print(f"Retention: {config['retention_days']} days")
    print(f"Target File Size: {config['target_file_size_mb']} MB")
    print(f"Source Topic: {config['topic']}\n")

    # Drop table if exists (for clean setup)
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Cleaned up existing table")
    except Exception as e:
        print(f"⚠ Table cleanup: {e}")

    # Create Bronze table with exchange-specific configuration
    target_file_size_bytes = config["target_file_size_mb"] * 1024 * 1024
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_key STRING COMMENT 'Kafka message key (usually symbol)',
        avro_payload BINARY COMMENT 'Raw Avro V2 serialized bytes (no deserialization)',
        topic STRING COMMENT 'Kafka topic name: {config["topic"]}',
        partition INT COMMENT 'Kafka partition number',
        offset BIGINT COMMENT 'Kafka offset within partition',
        kafka_timestamp TIMESTAMP COMMENT 'Kafka message timestamp (producer timestamp)',
        ingestion_timestamp TIMESTAMP COMMENT 'Platform ingestion timestamp (when written to Bronze)',
        ingestion_date DATE COMMENT 'Partition key derived from ingestion_timestamp'
    )
    USING iceberg
    PARTITIONED BY (ingestion_date)
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'commit.retry.num-retries' = '5',
        'commit.manifest.min-count-to-merge' = '5',
        'commit.manifest-merge.enabled' = 'true',
        'write.target-file-size-bytes' = '{target_file_size_bytes}'
    )
    COMMENT 'Bronze layer: Raw {exchange.upper()} Kafka data ({config["retention_days"]}-day retention)'
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        return False, table_name

    # Verify table
    print("\nTable Details:")
    print("  Schema: 8 fields (message_key, avro_payload, Kafka metadata)")
    print("  Partitioning: PARTITIONED BY (ingestion_date) - Date partitioning for Spark Streaming")
    print("  Compression: Zstd (Parquet), Gzip (metadata)")
    print(f"  Retention: {config['retention_days']} days")
    print(f"  Target File Size: {config['target_file_size_mb']} MB")
    print(f"  Topic Mapping: {config['topic']} → {table_name}")

    print(f"\n{'=' * 70}")
    print(f"✓ Bronze {exchange.upper()} table created successfully")
    print(f"{'=' * 70}\n")

    return True, table_name


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Bronze Table Creation - Per Exchange Architecture")
    print("Medallion Architecture - Bronze Layer (Isolated Raw Data)")
    print("=" * 70 + "\n")

    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: create_bronze_table.py <exchange|all>")
        print(f"  Supported exchanges: {', '.join(EXCHANGE_CONFIG.keys())}")
        print("  Or use 'all' to create all Bronze tables")
        return 1

    target = sys.argv[1].lower()

    # Create Spark session
    spark = create_spark_session("K2-Create-Bronze-Tables")

    try:
        # Show current Spark config
        print("Spark Configuration:")
        print(f"  Catalog: {spark.conf.get('spark.sql.catalog.iceberg')}")
        print(f"  Catalog Type: {spark.conf.get('spark.sql.catalog.iceberg.type')}")
        print(f"  Catalog URI: {spark.conf.get('spark.sql.catalog.iceberg.uri')}")
        print(f"  Warehouse: {spark.conf.get('spark.sql.catalog.iceberg.warehouse')}")
        print()

        # Create namespace if not exists
        spark.sql("CREATE NAMESPACE IF NOT EXISTS iceberg.market_data")
        print("✓ Namespace 'iceberg.market_data' ready\n")

        # Determine which exchanges to create
        exchanges = list(EXCHANGE_CONFIG.keys()) if target == "all" else [target.lower()]

        # Create tables
        results = []
        tables_created = []

        for exchange in exchanges:
            if exchange not in EXCHANGE_CONFIG:
                print(f"⚠ Skipping unknown exchange: {exchange}")
                continue

            success, table_name = create_bronze_table(spark, exchange)
            results.append(success)
            if success:
                tables_created.append(table_name)

        # Summary
        print(f"{'=' * 70}")
        print("Bronze Table Creation Summary")
        print(f"{'=' * 70}\n")
        print(f"Tables Created: {sum(results)}/{len(exchanges)}\n")

        if tables_created:
            print("Created Bronze Tables:")
            for table in tables_created:
                print(f"  ✓ {table}")
            print()

        # Show all Bronze tables
        print("All Bronze Tables in Catalog:")
        all_tables = spark.sql("SHOW TABLES IN iceberg.market_data")
        all_tables.filter("tableName LIKE 'bronze%'").show(truncate=False)

        print("\nArchitecture Benefits:")
        print("  • Isolation: Each exchange can fail independently")
        print("  • Scalability: Independent resource allocation per exchange")
        print("  • Observability: Clear per-exchange metrics and monitoring")
        print("  • Flexibility: Different retention and file size policies")
        print("  • Topic Alignment: Clean 1:1 topic-to-table mapping\n")

        print("Next Steps:")
        print("  1. Silver tables already created (silver_binance_trades, silver_kraken_trades)")
        print("  2. Implement Bronze ingestion jobs (per-exchange)")
        print("  3. Implement Silver transformation (Bronze → Silver per exchange)")

        if all(results):
            print("\n✓ All Bronze tables created successfully")
            return 0
        else:
            print("\n⚠ Some Bronze tables failed to create")
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
