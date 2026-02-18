#!/usr/bin/env python3
"""Create Iceberg table for bronze_trades_kraken (matches binance structure)."""

from pyspark.sql import SparkSession

spark = (
    SparkSession.builder.appName("CreateKrakenIcebergTable")
    .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0")
    .config("spark.sql.catalog.k2", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.k2.type", "hadoop")
    .config("spark.sql.catalog.k2.warehouse", "/home/iceberg/warehouse")
    .config("spark.sql.catalog.k2.io-impl", "org.apache.iceberg.hadoop.HadoopFileIO")
    .getOrCreate()
)

print("\n" + "="*80)
print("Creating Iceberg Table: k2.cold.bronze_trades_kraken")
print("="*80)

# Drop if exists
spark.sql("DROP TABLE IF EXISTS k2.cold.bronze_trades_kraken")
print("✅ Dropped old table (if any)")

# Create with same schema as Binance
spark.sql("""
CREATE TABLE k2.cold.bronze_trades_kraken (
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
    'write.format.default'='parquet',
    'write.parquet.compression-codec'='zstd',
    'write.parquet.compression-level'='3'
)
""")

print("✅ Created table: k2.cold.bronze_trades_kraken")

# Verify
result = spark.sql("DESCRIBE TABLE k2.cold.bronze_trades_kraken")
print("\nTable schema:")
result.show(truncate=False)

spark.stop()
