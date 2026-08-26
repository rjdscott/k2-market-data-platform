#!/usr/bin/env python3
"""Create Gold Iceberg table for unified analytics.

Gold Layer Purpose:
- Unified multi-exchange trades for analytics
- Deduplicated by message_id (across all exchanges)
- Hourly partitioning for efficient analytical queries
- Unlimited retention (analytics layer)
- Sorted by timestamp for time-series queries

Usage:
    # From host
    docker exec spark-master spark-submit \
      --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_gold_table.py

    # From within container
    spark-submit --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_gold_table.py
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from k2.spark.utils import create_spark_session


def create_gold_table(spark, namespace: str = "market_data"):
    """Create Gold Iceberg table for unified analytics.

    Args:
        spark: SparkSession with Iceberg catalog configured
        namespace: Iceberg namespace (default: market_data)

    Returns:
        True if successful, False otherwise
    """
    table_name = f"iceberg.{namespace}.gold_crypto_trades"

    print(f"\n{'=' * 70}")
    print(f"Creating Gold Table: {table_name}")
    print(f"{'=' * 70}\n")

    # Drop table if exists (for clean setup)
    try:
        spark.sql(f"DROP TABLE IF EXISTS {table_name}")
        print("✓ Cleaned up existing table")
    except Exception as e:
        print(f"⚠ Table cleanup: {e}")

    # Create Gold table with V2 schema + partitioning fields
    # Schema: 15 V2 fields + exchange_date + exchange_hour
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_id STRING COMMENT 'UUID v4 (unique across all exchanges) for deduplication',
        trade_id STRING COMMENT 'Exchange-specific trade ID (format: EXCHANGE-{{id}})',
        symbol STRING COMMENT 'Trading pair (normalized: BTCUSDT, BTCUSD, ETHUSDT, etc.)',
        exchange STRING COMMENT 'Exchange code: BINANCE, KRAKEN, COINBASE, etc.',
        asset_class STRING COMMENT 'Always crypto for this table',
        timestamp BIGINT COMMENT 'Trade execution time (microseconds since epoch)',
        price DECIMAL(18, 8) COMMENT 'Trade price (Decimal 18,8 for micro-price precision)',
        quantity DECIMAL(18, 8) COMMENT 'Trade quantity/volume (Decimal 18,8 for fractional crypto)',
        currency STRING COMMENT 'Quote currency (USDT, USD, BTC, EUR, etc.)',
        side STRING COMMENT 'Trade side: BUY or SELL',
        trade_conditions ARRAY<STRING> COMMENT 'Trade conditions (may be empty for crypto)',
        source_sequence BIGINT COMMENT 'Exchange-provided sequence number (may be null)',
        ingestion_timestamp BIGINT COMMENT 'Platform ingestion time (microseconds)',
        platform_sequence BIGINT COMMENT 'Platform sequence number (may be null)',
        vendor_data MAP<STRING, STRING> COMMENT 'Exchange-specific fields as key-value pairs',
        exchange_date DATE COMMENT 'Partition key 1: Trade date derived from timestamp (exchange time)',
        exchange_hour INT COMMENT 'Partition key 2: Hour of day (0-23) derived from timestamp'
    )
    USING iceberg
    PARTITIONED BY (exchange_date, exchange_hour)
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'commit.retry.num-retries' = '5',
        'commit.manifest.min-count-to-merge' = '10',
        'commit.manifest-merge.enabled' = 'true',
        'write.target-file-size-bytes' = '268435456',
        'write.distribution-mode' = 'hash',
        'write.delete.mode' = 'merge-on-read',
        'write.update.mode' = 'merge-on-read',
        'write.metadata.delete-after-commit.enabled' = 'false',
        'write.metadata.previous-versions-max' = '100'
    )
    COMMENT 'Gold layer: Unified crypto trades from all exchanges for analytics (unlimited retention)'
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

    print("\n✓ Gold table created successfully")

    return True


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Gold Table Creation")
    print("Medallion Architecture - Gold Layer (Unified Analytics)")
    print("=" * 70 + "\n")

    # Create Spark session
    spark = create_spark_session("K2-Create-Gold-Table")

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

        # Create Gold table
        success = create_gold_table(spark, namespace="market_data")

        # Summary
        print(f"\n{'=' * 70}")
        print("Gold Table Creation Summary")
        print(f"{'=' * 70}\n")
        print("✓ gold_crypto_trades: Unified multi-exchange trades")
        print("\nSchema: 17 fields (15 V2 fields + exchange_date + exchange_hour)")
        print("Partitioning: PARTITIONED BY (exchange_date, exchange_hour)")
        print("  - Hourly granularity for efficient analytical queries")
        print("  - Partition pruning benefits: Query 1 hour vs 1 day of data")
        print("Compression: Zstd (Parquet), Gzip (metadata)")
        print("Retention: Unlimited (analytics layer)")
        print("Target File Size: 256 MB (larger for analytics)")
        print("Distribution Mode: Hash (for even partition distribution)")
        print("\nDeduplication Strategy:")
        print("  - message_id: UUID v4 unique across all exchanges")
        print("  - Gold layer ensures no duplicates in analytics")
        print("\nQuery Optimization:")
        print("  - Hourly partitions: Fast time-range queries")
        print("  - Sorted by timestamp: Efficient time-series analysis")
        print("  - Metadata tracking: 100 previous versions for time-travel")
        print("\nNext: Implement Bronze ingestion job (Kafka → Bronze)")

        if success:
            print("\n✓ Gold table created successfully")

            # Show all Medallion tables
            print(f"\n{'=' * 70}")
            print("Medallion Architecture Tables (Complete)")
            print(f"{'=' * 70}\n")
            tables = spark.sql("SHOW TABLES IN iceberg.market_data")
            tables.show(truncate=False)

            return 0
        else:
            print("\n✗ Gold table creation failed")
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
