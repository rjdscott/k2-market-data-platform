#!/usr/bin/env python3
"""Quick verification of Iceberg table data."""

from pyspark.sql import SparkSession

spark = (
    SparkSession.builder.appName("VerifyIceberg")
    .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0")
    .config("spark.sql.catalog.demo", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.demo.type", "hadoop")
    .config("spark.sql.catalog.demo.warehouse", "/home/iceberg/warehouse")
    .config("spark.sql.catalog.demo.io-impl", "org.apache.iceberg.hadoop.HadoopFileIO")
    .getOrCreate()
)

print("\n" + "="*80)
print("Iceberg Table Verification: demo.cold.bronze_trades_binance")
print("="*80)

# Count and stats
result = spark.sql("""
    SELECT
        count(*) as total_rows,
        min(exchange_timestamp) as earliest,
        max(exchange_timestamp) as latest,
        count(DISTINCT symbol) as unique_symbols,
        min(sequence_number) as min_seq,
        max(sequence_number) as max_seq
    FROM demo.cold.bronze_trades_binance
""")

result.show(truncate=False)

# Sample data
print("\nSample rows:")
spark.sql("SELECT * FROM demo.cold.bronze_trades_binance LIMIT 5").show(truncate=False)

spark.stop()
