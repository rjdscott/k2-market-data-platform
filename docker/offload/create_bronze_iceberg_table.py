#!/usr/bin/env python3
"""
Create Iceberg table for bronze_trades_binance offload.
Schema matches the ClickHouse bronze_trades_binance table structure.
"""

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType,
    StructField,
    TimestampType,
    LongType,
    StringType,
    DecimalType,
    IntegerType,
)

# Initialize Spark with Iceberg
spark = (
    SparkSession.builder.appName("CreateBronzeIcebergTable")
    .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0")
    .config("spark.sql.catalog.demo", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.demo.type", "hadoop")
    .config("spark.sql.catalog.demo.warehouse", "/home/iceberg/warehouse")
    .config("spark.sql.catalog.demo.io-impl", "org.apache.iceberg.hadoop.HadoopFileIO")
    .getOrCreate()
)

# Drop existing table if it exists
spark.sql("DROP TABLE IF EXISTS demo.cold.bronze_trades_binance")
print("✅ Dropped existing table (if any)")

# Create schema matching ClickHouse bronze_trades_binance
schema = StructType(
    [
        StructField("exchange_timestamp", TimestampType(), True),
        StructField("sequence_number", LongType(), True),
        StructField("symbol", StringType(), True),
        StructField("price", DecimalType(18, 8), True),
        StructField("quantity", DecimalType(18, 8), True),
        StructField("quote_volume", DecimalType(18, 8), True),
        StructField("event_time", TimestampType(), True),
        StructField("kafka_offset", LongType(), True),
        StructField("kafka_partition", IntegerType(), True),
        StructField("ingestion_timestamp", TimestampType(), True),
    ]
)

# Create empty DataFrame with schema
df = spark.createDataFrame([], schema)

# Write to create table with partitioning by day
df.writeTo("demo.cold.bronze_trades_binance").using("iceberg").partitionedBy(
    "days(exchange_timestamp)"
).tableProperty("write.format.default", "parquet").tableProperty(
    "write.parquet.compression-codec", "zstd"
).tableProperty(
    "write.parquet.compression-level", "3"
).create()

print("✅ Created Iceberg table: demo.cold.bronze_trades_binance")

# Verify table exists
spark.sql("DESCRIBE TABLE demo.cold.bronze_trades_binance").show(truncate=False)

# Show table properties
print("\nTable properties:")
spark.sql("SHOW TBLPROPERTIES demo.cold.bronze_trades_binance").show(truncate=False)

spark.stop()
