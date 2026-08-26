#!/usr/bin/env python3
"""Quick check of Gold table data."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("QuickGoldCheck") \
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog") \
    .config("spark.sql.catalog.iceberg.type", "rest") \
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181") \
    .config("spark.hadoop.fs.s3a.access.key", "admin") \
    .config("spark.hadoop.fs.s3a.secret.key", "password") \
    .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000") \
    .config("spark.hadoop.fs.s3a.path.style.access", "true") \
    .getOrCreate()

try:
    count = spark.sql("SELECT COUNT(*) as cnt FROM iceberg.market_data.gold_crypto_trades").collect()[0]['cnt']
    print(f"\n✓ Gold table record count: {count:,}")

    if count > 0:
        exchanges = spark.sql("SELECT exchange, COUNT(*) as cnt FROM iceberg.market_data.gold_crypto_trades GROUP BY exchange ORDER BY exchange").collect()
        print("\nExchange breakdown:")
        for row in exchanges:
            print(f"  - {row['exchange']}: {row['cnt']:,} trades")
    else:
        print("\n⚠️  No data yet (wait for first batch)")
except Exception as e:
    print(f"\n✗ Error: {e}")
finally:
    spark.stop()
