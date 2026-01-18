#!/usr/bin/env python3
"""Diagnostic script to check Bronze Avro deserialization issues."""

import sys

from pyspark.sql import SparkSession
from pyspark.sql.functions import col

# Create Spark session
spark = (
    SparkSession.builder.appName("Diagnose-Bronze")
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg.type", "rest")
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
    .config("spark.driver.extraJavaOptions", "-Daws.region=us-east-1")
    .config("spark.executor.extraJavaOptions", "-Daws.region=us-east-1")
    .getOrCreate()
)

print("\n" + "=" * 70)
print("Bronze Table Diagnostic")
print("=" * 70)

# Read Bronze table (batch mode)
bronze_df = spark.read.format("iceberg").load("iceberg.market_data.bronze_binance_trades")

print(f"\nTotal Bronze records: {bronze_df.count()}")
print("\nBronze schema:")
bronze_df.printSchema()

print("\nSample Bronze record (first 5):")
bronze_df.select("message_key", "topic", "partition", "offset", "kafka_timestamp").show(5, truncate=False)

print("\nChecking Avro payload sizes:")
bronze_df.select(col("message_key"), col("avro_payload").cast("string").substr(1, 50).alias("payload_preview")).show(
    5, truncate=False
)

# Check for null payloads
null_count = bronze_df.filter(col("avro_payload").isNull()).count()
print(f"\nNull Avro payloads: {null_count}")

# Check payload lengths
bronze_df.select("message_key", col("avro_payload").cast("binary").alias("payload")).createOrReplaceTempView("bronze")
spark.sql("SELECT message_key, length(payload) as payload_len FROM bronze LIMIT 10").show()

spark.stop()
