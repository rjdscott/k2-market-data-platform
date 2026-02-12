#!/usr/bin/env python3
"""
Test 6: Try native ClickHouse protocol (port 9000) instead of HTTP (port 8123)
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 6: JDBC Native Protocol Test (port 9000)")
print("=" * 80)

spark = SparkSession.builder.appName("JDBC-Native-Test").getOrCreate()

# Try native protocol on port 9000
print("\nTest 1: Native protocol (port 9000)")
print("URL: jdbc:clickhouse://clickhouse:9000/k2")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:9000/k2",
        table="(SELECT 1 as test_col) AS test",
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse"
        }
    )
    result = df.first()[0]
    print(f"✓ SUCCESS with native protocol: {result}")
    spark.stop()
    sys.exit(0)
except Exception as e:
    print(f"✗ FAILED with native protocol: {str(e)[:200]}")

# Try explicit protocol parameter
print("\nTest 2: HTTP protocol with explicit protocol parameter")
print("URL: jdbc:clickhouse://clickhouse:8123/k2?protocol=http")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:8123/k2?protocol=http",
        table="(SELECT 1 as test_col) AS test",
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse"
        }
    )
    result = df.first()[0]
    print(f"✓ SUCCESS with explicit HTTP: {result}")
    spark.stop()
    sys.exit(0)
except Exception as e:
    print(f"✗ FAILED with explicit HTTP: {str(e)[:200]}")

spark.stop()
sys.exit(1)
