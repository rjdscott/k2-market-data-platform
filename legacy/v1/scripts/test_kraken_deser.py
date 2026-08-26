#!/usr/bin/env python3
"""Minimal test to debug Kraken from_avro deserialization."""

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, substring, expr
from pyspark.sql.avro.functions import from_avro

# Kraken schema
KRAKEN_RAW_SCHEMA = """
{
  "type": "record",
  "name": "KrakenRawTrade",
  "namespace": "com.k2.marketdata.raw",
  "fields": [
    {"name": "channel_id", "type": "long"},
    {"name": "price", "type": "string"},
    {"name": "volume", "type": "string"},
    {"name": "timestamp", "type": "string"},
    {"name": "side", "type": "string"},
    {"name": "order_type", "type": "string"},
    {"name": "misc", "type": "string"},
    {"name": "pair", "type": "string"},
    {"name": "ingestion_timestamp", "type": "long"}
  ]
}
"""

spark = SparkSession.builder \
    .appName("test-kraken-deser") \
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.iceberg.type", "rest") \
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181") \
    .config("spark.sql.catalog.iceberg.warehouse", "s3://warehouse/") \
    .config("spark.sql.catalog.iceberg.s3.endpoint", "http://minio:9000") \
    .config("spark.sql.catalog.iceberg.s3.access-key-id", "admin") \
    .config("spark.sql.catalog.iceberg.s3.secret-access-key", "password") \
    .config("spark.sql.catalog.iceberg.s3.path-style-access", "true") \
    .getOrCreate()

print("\n=== Reading Bronze Kraken ===")
bronze = spark.read.table("iceberg.market_data.bronze_kraken_trades").limit(5)

print("\n=== Stripping header ===")
with_payload = bronze.withColumn(
    "avro_payload", substring(col("raw_bytes"), 6, 999999)
).withColumn(
    "raw_length", expr("length(raw_bytes)")
).withColumn(
    "payload_length", expr("length(avro_payload)")
)

print("\n=== Sample raw data ===")
with_payload.select("raw_length", "payload_length").show()

print("\n=== Deserializing with from_avro ===")
deserialized = with_payload.withColumn(
    "kraken_raw", from_avro(col("avro_payload"), KRAKEN_RAW_SCHEMA)
)

print("\n=== Checking for NULLs ===")
deserialized.select(
    "raw_length",
    "payload_length",
    col("kraken_raw").isNull().alias("is_null"),
    col("kraken_raw").alias("struct_value")
).show(truncate=False)

print("\n=== Extracting fields ===")
deserialized.select(
    col("kraken_raw.channel_id"),
    col("kraken_raw.price"),
    col("kraken_raw.pair"),
    col("kraken_raw.timestamp")
).show(truncate=False)

spark.stop()
