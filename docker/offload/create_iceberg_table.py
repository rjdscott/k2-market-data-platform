#!/usr/bin/env python3
"""
Create/recreate Iceberg table for bronze_trades_binance with correct schema
"""
from pyspark.sql import SparkSession
from pyspark.sql.types import *

spark = SparkSession.builder \
    .appName("Create-Iceberg-Table") \
    .config("spark.sql.catalog.demo", "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.demo.type", "hadoop") \
    .config("spark.sql.catalog.demo.warehouse", "/home/iceberg/warehouse") \
    .getOrCreate()

# Drop existing table
spark.sql("DROP TABLE IF EXISTS demo.cold.bronze_trades_binance")
print("✓ Dropped existing table")

# Define schema matching ClickHouse bronze_trades_binance
schema = StructType([
    StructField("exchange_timestamp", TimestampType(), False),
    StructField("sequence_number", LongType(), False),
    StructField("symbol", StringType(), False),
    StructField("price", DecimalType(18, 8), False),
    StructField("quantity", DecimalType(18, 8), False),
    StructField("quote_volume", DecimalType(18, 8), False),
    StructField("event_time", TimestampType(), False),
    StructField("kafka_offset", LongType(), False),
    StructField("kafka_partition", IntegerType(), False),
    StructField("ingestion_timestamp", TimestampType(), False)
])

# Create Iceberg table
spark.sql("""
    CREATE TABLE demo.cold.bronze_trades_binance (
        exchange_timestamp TIMESTAMP,
        sequence_number BIGINT,
        symbol STRING,
        price DECIMAL(18,8),
        quantity DECIMAL(18,8),
        quote_volume DECIMAL(18,8),
        event_time TIMESTAMP,
        kafka_offset BIGINT,
        kafka_partition INT,
        ingestion_timestamp TIMESTAMP
    )
    USING iceberg
    PARTITIONED BY (days(exchange_timestamp))
    TBLPROPERTIES (
        'write.format.default' = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
    )
""")

print("✓ Created Iceberg table: demo.cold.bronze_trades_binance")
print("\nSchema:")
spark.sql("DESCRIBE demo.cold.bronze_trades_binance").show(truncate=False)

spark.stop()
