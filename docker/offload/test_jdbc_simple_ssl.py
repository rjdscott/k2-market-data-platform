#!/usr/bin/env python3
"""
Test 11: Simplified SSL configuration
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 11: JDBC with ssl=false")
print("=" * 80)

spark = SparkSession.builder.appName("JDBC-Simple-SSL").getOrCreate()

print("\nAttempting connection...")
print("URL: jdbc:clickhouse://clickhouse:8123/k2")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:8123/k2",
        table="jdbc_test",
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse",
            "ssl": "false"
        }
    )

    count = df.count()
    print(f"✓ SUCCESS: Read {count} row(s) from jdbc_test")
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
