#!/usr/bin/env python3
"""
Test 5: Trace what user is actually being sent to ClickHouse
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 5: JDBC User Trace Test")
print("=" * 80)

# Configure Spark with verbose JDBC logging
spark = SparkSession.builder \
    .appName("JDBC-Trace-Test") \
    .config("spark.sql.debug.maxToStringFields", "100") \
    .getOrCreate()

print("\nConfigured JDBC properties:")
properties = {
    "driver": "com.clickhouse.jdbc.ClickHouseDriver",
    "user": "default",
    "password": "clickhouse"
}
for key, value in properties.items():
    if key == "password":
        print(f"  {key}: {'*' * len(value)}")
    else:
        print(f"  {key}: {value}")

print("\nAttempting connection...")
print("URL: jdbc:clickhouse://clickhouse:8123/k2")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:8123/k2",
        table="(SELECT 1 as test_col) AS test",
        properties=properties
    )
    result = df.first()[0]
    print(f"✓ SUCCESS: {result}")
    sys.exit(0)
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)
finally:
    spark.stop()
