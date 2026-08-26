#!/usr/bin/env python3
"""Create Silver Iceberg tables for validated per-exchange data.

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
    # From host
    docker exec spark-master spark-submit \
      --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_silver_tables.py

    # From within container
    spark-submit --master spark://spark-master:7077 \
      --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0 \
      /opt/k2/src/k2/spark/jobs/create_silver_tables.py
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from k2.spark.utils import create_spark_session


def create_silver_table(spark, exchange: str, namespace: str = "market_data"):
    """Create Silver Iceberg table for specific exchange.

    Args:
        spark: SparkSession with Iceberg catalog configured
        exchange: Exchange name (binance or kraken)
        namespace: Iceberg namespace (default: market_data)

    Returns:
        True if successful, False otherwise
    """
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

    # Create Silver table with V2 schema
    # Schema matches TradeV2 Avro schema: 15 fields
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        message_id STRING COMMENT 'UUID v4 for deduplication',
        trade_id STRING COMMENT '{exchange.capitalize()} trade ID (format: {exchange.upper()}-{{id}})',
        symbol STRING COMMENT 'Trading pair (e.g., BTCUSDT, BTCUSD)',
        exchange STRING COMMENT 'Always {exchange.upper()}',
        asset_class STRING COMMENT 'Always crypto',
        timestamp BIGINT COMMENT 'Trade execution time (microseconds since epoch)',
        price DECIMAL(18, 8) COMMENT 'Trade price (Decimal 18,8 for crypto precision)',
        quantity DECIMAL(18, 8) COMMENT 'Trade quantity/volume (Decimal 18,8 for fractional crypto)',
        currency STRING COMMENT 'Quote currency (USDT, USD, BTC, etc.)',
        side STRING COMMENT 'Trade side: BUY or SELL',
        trade_conditions ARRAY<STRING> COMMENT 'Trade conditions (may be empty for crypto)',
        source_sequence BIGINT COMMENT 'Exchange-provided sequence number (may be null)',
        ingestion_timestamp BIGINT COMMENT 'Platform ingestion time (microseconds)',
        platform_sequence BIGINT COMMENT 'Platform sequence number (may be null)',
        vendor_data MAP<STRING, STRING> COMMENT '{exchange.capitalize()}-specific fields as key-value pairs',
        exchange_date DATE COMMENT 'Partition key derived from timestamp (exchange time, not ingestion)'
    )
    USING iceberg
    PARTITIONED BY (days(exchange_date))
    TBLPROPERTIES (
        'format-version' = '2',
        'write.parquet.compression-codec' = 'zstd',
        'write.metadata.compression-codec' = 'gzip',
        'commit.retry.num-retries' = '5',
        'commit.manifest.min-count-to-merge' = '5',
        'commit.manifest-merge.enabled' = 'true',
        'write.target-file-size-bytes' = '134217728',
        'write.delete.mode' = 'merge-on-read',
        'write.update.mode' = 'merge-on-read'
    )
    COMMENT 'Silver layer: Validated {exchange.capitalize()} trades with V2 schema (30-day retention)'
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

    print(f"\n✓ Silver {exchange.capitalize()} table created successfully")

    return True


def main():
    """Main entry point."""
    print("\n" + "=" * 70)
    print("K2 Silver Tables Creation")
    print("Medallion Architecture - Silver Layer (Validated Per-Exchange)")
    print("=" * 70 + "\n")

    # Create Spark session
    spark = create_spark_session("K2-Create-Silver-Tables")

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

        # Create Silver tables for each exchange
        exchanges = ["binance", "kraken"]
        success_count = 0

        for exchange in exchanges:
            success = create_silver_table(spark, exchange, namespace="market_data")
            if success:
                success_count += 1

        # Summary
        print(f"\n{'=' * 70}")
        print("Silver Tables Creation Summary")
        print(f"{'=' * 70}\n")
        print(f"Tables Created: {success_count}/{len(exchanges)}")
        print("  ✓ silver_binance_trades: Validated Binance trades (V2 schema)")
        print("  ✓ silver_kraken_trades: Validated Kraken trades (V2 schema)")
        print("\nSchema: 16 fields (15 V2 fields + exchange_date)")
        print("Partitioning: PARTITIONED BY (days(exchange_date))")
        print("Compression: Zstd (Parquet), Gzip (metadata)")
        print("Retention: 30 days (validated data)")
        print("Target File Size: 128 MB")
        print("\nNext: Create Gold table (unified analytics)")

        if success_count == len(exchanges):
            print("\n✓ All Silver tables created successfully")
            return 0
        else:
            print("\n⚠ Some Silver tables failed to create")
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
