#!/usr/bin/env python3
"""
Test 8: Try ClickHouse Native JDBC driver (com.github.housepower:clickhouse-native-jdbc)
This driver uses the native ClickHouse protocol and is known to work better with Spark
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 8: ClickHouse Native JDBC Driver")
print("=" * 80)

spark = SparkSession.builder.appName("Native-JDBC-Test").getOrCreate()

print("\nAttempting connection with Native JDBC driver...")
print("URL: jdbc:clickhouse://clickhouse:9000/k2")
print("Driver: com.github.housepower.jdbc.ClickHouseDriver")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:9000/k2",
        table="(SELECT 1 as test_col) AS test",
        properties={
            "driver": "com.github.housepower.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse"
        }
    )
    result = df.first()[0]
    print(f"✓ SUCCESS with Native JDBC driver: {result}")
    spark.stop()
    sys.exit(0)
except Exception as e:
    print(f"✗ FAILED with Native JDBC driver:")
    print(f"  {str(e)[:300]}")
    spark.stop()
    sys.exit(1)
