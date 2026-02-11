#!/usr/bin/env python3
"""
Test 13: Clean JDBC test without Iceberg interference
"""
from pyspark.sql import SparkSession
import sys

print("=" * 80)
print("TEST 13: Clean JDBC Test (ClickHouse 24.3 LTS)")
print("=" * 80)

# Create Spark session WITHOUT Iceberg configs
spark = SparkSession.builder \
    .appName("JDBC-Clean-Test") \
    .config("spark.sql.catalogImplementation", "in-memory") \
    .getOrCreate()

print("\nAttempting JDBC connection to ClickHouse 24.3...")
print("URL: jdbc:clickhouse://clickhouse:8123/k2")

try:
    df = spark.read.jdbc(
        url="jdbc:clickhouse://clickhouse:8123/k2",
        table="jdbc_test",
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": "clickhouse"
        }
    )

    print(f"✓ Connection successful!")
    count = df.count()
    print(f"✓ Row count: {count}")

    if count > 0:
        print("\nData preview:")
        df.show()

    print("\n" + "=" * 80)
    print("SUCCESS: JDBC connectivity works with ClickHouse 24.3 LTS!")
    print("=" * 80)

    spark.stop()
    sys.exit(0)

except Exception as e:
    print(f"\n✗ FAILED:")
    import traceback
    traceback.print_exc()
    spark.stop()
    sys.exit(1)
