#!/usr/bin/env python3
"""
Test 12: Use spark-clickhouse connector instead of raw JDBC
This connector is specifically designed for Spark + ClickHouse integration
https://github.com/housepower/spark-clickhouse-connector
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 12: Spark-ClickHouse Connector")
print("=" * 80)

spark = SparkSession.builder \
    .appName("ClickHouse-Connector-Test") \
    .getOrCreate()

print("\nAttempting connection with spark-clickhouse connector...")
print("Using DataFrame API with clickhouse format")

try:
    # Use the clickhouse format provided by the connector
    df = spark.read \
        .format("clickhouse") \
        .option("host", "clickhouse") \
        .option("port", "9000") \
        .option("database", "k2") \
        .option("user", "default") \
        .option("password", "clickhouse") \
        .option("table", "jdbc_test") \
        .load()

    count = df.count()
    print(f"✓ SUCCESS: Read {count} row(s) using spark-clickhouse connector")
    if count > 0:
        df.show()
    spark.stop()
    sys.exit(0)

except Exception as e:
    print(f"✗ FAILED:")
    import traceback
    traceback.print_exc()
    spark.stop()
    sys.exit(1)
