#!/usr/bin/env python3
"""
Test 10: Add explicit connection properties to bypass potential SSL issues
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 10: JDBC with Explicit Connection Properties")
print("=" * 80)

spark = SparkSession.builder.appName("JDBC-Explicit-Props").getOrCreate()

print("\nAttempting connection with explicit properties...")
print("URL: jdbc:clickhouse://clickhouse:8123/k2")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:8123/k2",
        table="jdbc_test",
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse",
            "ssl": "false",
            "sslmode": "disable",
            "use_server_time_zone": "false",
            "use_time_zone": "UTC"
        }
    )

    count = df.count()
    print(f"✓ SUCCESS: Read {count} row(s)")
    spark.stop()
    sys.exit(0)

except Exception as e:
    print(f"✗ FAILED: {str(e)[:200]}")
    spark.stop()
    sys.exit(1)
