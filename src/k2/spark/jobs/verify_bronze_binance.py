#!/usr/bin/env python3
"""Verify Bronze Binance table has data."""

from pyspark.sql import SparkSession

# Create Spark session
spark = (
    SparkSession.builder.appName("Verify-Bronze-Binance")
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg.type", "rest")
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
    .config("spark.sql.catalog.iceberg.warehouse", "s3://warehouse/")
    .config("spark.sql.catalog.iceberg.s3.endpoint", "http://minio:9000")
    .config("spark.sql.catalog.iceberg.s3.access-key-id", "admin")
    .config("spark.sql.catalog.iceberg.s3.secret-access-key", "password")
    .config("spark.sql.catalog.iceberg.s3.path-style-access", "true")
    # S3/MinIO configuration
    .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
    .config("spark.hadoop.fs.s3a.access.key", "admin")
    .config("spark.hadoop.fs.s3a.secret.key", "password")
    .config("spark.hadoop.fs.s3a.path.style.access", "true")
    .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config(
        "spark.hadoop.fs.s3a.aws.credentials.provider",
        "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
    )
    .config("spark.hadoop.fs.s3a.endpoint.region", "us-east-1")
    .getOrCreate()
)

print("\n" + "=" * 70)
print("Bronze Binance Table Verification")
print("=" * 70 + "\n")

# Query Bronze table
print("Querying iceberg.market_data.bronze_binance_trades...\n")

result = spark.sql("""
    SELECT
        COUNT(*) as count,
        MIN(ingestion_timestamp) as first_record,
        MAX(ingestion_timestamp) as last_record,
        COUNT(DISTINCT topic) as topics,
        COUNT(DISTINCT partition) as partitions
    FROM iceberg.market_data.bronze_binance_trades
""")

print("Results:")
result.show(truncate=False)

# Get sample records
print("\nSample Records (first 5):")
sample = spark.sql("""
    SELECT
        message_key,
        topic,
        partition,
        offset,
        kafka_timestamp,
        ingestion_timestamp,
        ingestion_date
    FROM iceberg.market_data.bronze_binance_trades
    LIMIT 5
""")
sample.show(truncate=False)

spark.stop()
print("\n✓ Verification complete\n")
