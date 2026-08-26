#!/usr/bin/env python3
"""Recreate gold_crypto_trades table with correct schema."""

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    LongType,
    DecimalType,
    ArrayType,
    DateType,
    IntegerType,
    TimestampType,
)

# Create Spark session
spark = (
    SparkSession.builder
    .appName("RecreateGoldTable")
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg.type", "rest")
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
    .config("spark.jars", "/opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar")
    .getOrCreate()
)

try:
    # Drop existing table
    print("\n" + "=" * 70)
    print("Dropping existing gold_crypto_trades table...")
    print("=" * 70)
    spark.sql("DROP TABLE IF EXISTS iceberg.market_data.gold_crypto_trades")
    print("✓ Table dropped successfully")

    # Create new table with correct schema
    print("\n" + "=" * 70)
    print("Creating gold_crypto_trades with updated schema...")
    print("=" * 70)

    create_table_sql = """
    CREATE TABLE iceberg.market_data.gold_crypto_trades (
        -- V2 Avro Fields (15 fields)
        message_id STRING COMMENT 'UUID v4 (unique across all exchanges)',
        trade_id STRING COMMENT 'Exchange-specific trade ID',
        symbol STRING COMMENT 'Trading pair (normalized)',
        exchange STRING COMMENT 'Exchange code (BINANCE, KRAKEN, etc.)',
        asset_class STRING COMMENT 'Always crypto',
        timestamp BIGINT COMMENT 'Trade execution time (microseconds)',
        price DECIMAL(18,8) COMMENT 'Trade price',
        quantity DECIMAL(18,8) COMMENT 'Trade quantity',
        currency STRING COMMENT 'Quote currency',
        side STRING COMMENT 'Trade side: BUY or SELL',
        trade_conditions ARRAY<STRING> COMMENT 'Trade conditions',
        source_sequence BIGINT COMMENT 'Exchange sequence number',
        ingestion_timestamp BIGINT COMMENT 'Platform ingestion time',
        platform_sequence BIGINT COMMENT 'Platform sequence number',
        vendor_data STRING COMMENT 'Exchange-specific fields (JSON string)',

        -- Gold Metadata (3 fields)
        gold_ingestion_timestamp TIMESTAMP COMMENT 'Gold layer ingestion time',
        exchange_date DATE COMMENT 'Partition key 1 (derived from timestamp)',
        exchange_hour INT COMMENT 'Partition key 2 (0-23, derived from timestamp)'
    )
    USING iceberg
    PARTITIONED BY (exchange_date, exchange_hour)
    COMMENT 'Gold layer - Unified cross-exchange analytics'
    """

    spark.sql(create_table_sql)
    print("✓ Table created successfully")

    # Verify new schema
    print("\n" + "=" * 70)
    print("Verifying new schema:")
    print("=" * 70)
    schema = spark.sql("DESCRIBE iceberg.market_data.gold_crypto_trades")
    schema.show(100, truncate=False)

    print("\n" + "=" * 70)
    print("SUCCESS: gold_crypto_trades recreated with gold_ingestion_timestamp")
    print("=" * 70)

except Exception as e:
    print(f"\n✗ ERROR: {e}")
    import traceback
    traceback.print_exc()

finally:
    spark.stop()
