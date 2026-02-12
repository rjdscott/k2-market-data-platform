#!/usr/bin/env python3
"""
Test 7: Try latest ClickHouse JDBC driver (0.6.5)
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 7: Latest JDBC Driver (0.6.5)")
print("=" * 80)

spark = SparkSession.builder.appName("JDBC-Latest-Test").getOrCreate()

print("\nAttempting connection with JDBC driver 0.6.5...")
print("URL: jdbc:clickhouse://clickhouse:8123/k2")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:8123/k2",
        table="(SELECT 1 as test_col) AS test",
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse"
        }
    )
    result = df.first()[0]
    print(f"✓ SUCCESS with driver 0.6.5: {result}")
    spark.stop()
    sys.exit(0)
except Exception as e:
    print(f"✗ FAILED with driver 0.6.5: {e}")
    spark.stop()
    sys.exit(1)
