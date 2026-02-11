#!/usr/bin/env python3
"""
Test 9: Query actual table instead of subquery
Maybe the subquery syntax is the issue
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 9: Query Real Table (not subquery)")
print("=" * 80)

spark = SparkSession.builder.appName("JDBC-Real-Table-Test").getOrCreate()

print("\nAttempting to query k2.jdbc_test table...")
print("URL: jdbc:clickhouse://clickhouse:8123/k2")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:8123/k2",
        table="jdbc_test",  # Real table name, not subquery
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse"
        }
    )

    count = df.count()
    if count > 0:
        first_row = df.first()
        print(f"✓ SUCCESS: Read {count} row(s) from jdbc_test")
        print(f"  First row: {first_row}")
    else:
        print(f"✓ SUCCESS: Connected but table is empty")

    spark.stop()
    sys.exit(0)

except Exception as e:
    print(f"✗ FAILED:")
    print(f"  {str(e)}")
    spark.stop()
    sys.exit(1)
