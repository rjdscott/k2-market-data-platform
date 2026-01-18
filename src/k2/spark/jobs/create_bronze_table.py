#!/usr/bin/env python3
"""Create Bronze Iceberg table for raw Kafka data.

Bronze Layer Purpose:
- Landing zone for raw Kafka messages (no deserialization)
- Stores Avro bytes for reprocessing if needed
- 7-day retention for Bronze data
- Partitioned by ingestion date for efficient cleanup

Usage:
    # From host
    docker exec spark-master spark-submit \
      --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_bronze_table.py

    # From within container
    spark-submit --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_bronze_table.py
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from k2.spark.utils import create_spark_session


def create_bronze_table(spark, namespace: str = "market_data"):
    """Create Bronze Iceberg table for raw Kafka data.

    Args:
        spark: SparkSession with Iceberg catalog configured
        namespace: Iceberg namespace (default: market_data)

    Returns:
        True if successful, False otherwise
    """
    table_name = f"iceberg.{namespace}.bronze_crypto_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating Bronze Table: {table_name}")
    print(f"{'=' * 70}\n")

    # Create namespace if not exists
    try:
        spark.sql(f"CREATE NAMESPACE IF NOT EXISTS iceberg.{namespace}")
        print(f"✓ Namespace 'iceberg.{namespace}' ready")
    except Exception as e:
        print(f"⚠ Namespace creation: {e}")

    # Drop table if exists (for clean setup)
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Cleaned up existing table")
    except Exception as e:
        print(f"⚠ Table cleanup: {e}")

    # Create Bronze table
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_key STRING COMMENT 'Kafka message key (usually symbol or compound key)',
        avro_payload BINARY COMMENT 'Raw Avro V2 serialized bytes (no deserialization)',
        topic STRING COMMENT 'Kafka topic name (e.g., market.crypto.trades.binance)',
        partition INT COMMENT 'Kafka partition number',
        offset BIGINT COMMENT 'Kafka offset within partition',
        kafka_timestamp TIMESTAMP COMMENT 'Kafka message timestamp (producer timestamp)',
        ingestion_timestamp TIMESTAMP COMMENT 'Platform ingestion timestamp (when written to Bronze)',
        ingestion_date DATE COMMENT 'Partition key derived from ingestion_timestamp'
    )
    USING iceberg
    PARTITIONED BY (days(ingestion_date))
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'commit.retry.num-retries' = '5',
        'commit.manifest.min-count-to-merge' = '5',
        'commit.manifest-merge.enabled' = 'true',
        'write.target-file-size-bytes' = '134217728'
    )
    COMMENT 'Bronze layer: Raw Kafka data for reprocessing (7-day retention)'
    """

    try:
        spark.sql(create_ddl)
        print(f"✓ Table created: {table_name}")
    except Exception as e:
        print(f"✗ Table creation failed: {e}")
        return False

    # Verify table
    print("\nVerifying table schema...")
    df_schema = spark.sql(f"DESCRIBE EXTENDED {table_name}")
    df_schema.show(50, truncate=False)

    # Show table properties
    print("\nTable properties:")
    props = spark.sql(f"SHOW TBLPROPERTIES {table_name}")
    props.show(truncate=False)

    # Show partitioning
    print("\nPartitioning:")
    partitions = spark.sql(f"SHOW PARTITIONS {table_name}")
    partitions.show(truncate=False)

    print(f"\n{'=' * 70}")
    print("✓ Bronze table created successfully")
    print(f"{'=' * 70}\n")
    print(f"Table: {table_name}")
    print(
        "Schema: 8 fields (message_key, avro_payload, topic, partition, offset, timestamps, date)"
    )
    print("Partitioning: PARTITIONED BY (days(ingestion_date))")
    print("Compression: Zstd (Parquet), Gzip (metadata)")
    print("Retention: 7 days (reprocessing window)")
    print("Target File Size: 128 MB")
    print("\nNext: Create Silver tables (Binance + Kraken)")

    return True


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Bronze Table Creation")
    print("Medallion Architecture - Bronze Layer (Raw Kafka Data)")
    print("=" * 70 + "\n")

    # Create Spark session
    spark = create_spark_session("K2-Create-Bronze-Table")

    try:
        # Show current Spark config
        print("Spark Configuration:")
        print(f"  Catalog: {spark.conf.get('spark.sql.catalog.iceberg')}")
        print(f"  Catalog Type: {spark.conf.get('spark.sql.catalog.iceberg.type')}")
        print(f"  Catalog URI: {spark.conf.get('spark.sql.catalog.iceberg.uri')}")
        print(f"  Warehouse: {spark.conf.get('spark.sql.catalog.iceberg.warehouse')}")
        print()

        # Create Bronze table
        success = create_bronze_table(spark, namespace="market_data")

        if success:
            print("\n✓ Bronze table creation completed successfully")
            return 0
        else:
            print("\n✗ Bronze table creation failed")
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
