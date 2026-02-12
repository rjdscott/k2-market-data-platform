#!/usr/bin/env python3
"""
Test 1: Basic JDBC Connectivity to ClickHouse
Purpose: Verify Spark can connect to ClickHouse and execute simple queries
Expected: Should print row count successfully
"""

from pyspark.sql import SparkSession
import sys

def test_basic_connection():
    """Test basic JDBC connection with minimal query"""

    print("=" * 80)
    print("TEST 1: Basic JDBC Connection to ClickHouse")
    print("=" * 80)

    # Initialize Spark (minimal config)
    spark = SparkSession.builder \
        .appName("JDBC-Test-Basic") \
        .getOrCreate()

    print("✓ Spark session created")

    # Test connection with simplest possible query
    try:
        print("\nAttempting JDBC connection to ClickHouse...")
        print("  URL: jdbc:clickhouse://clickhouse:8123/k2")
        print("  User: default")
        print("  Password: clickhouse")

        df = spark.read.jdbc(
            url="jdbc:clickhouse://clickhouse:8123/k2",
            table="(SELECT 1 as test_col) AS simple_test",  # Simplest query possible
            properties={
                "driver": "com.clickhouse.jdbc.ClickHouseDriver",
                "user": "default",
                "password": "clickhouse"
            }
        )

        result = df.first()[0]
        print(f"✓ SUCCESS: Query returned {result}")
        print("  JDBC connection working!")

        spark.stop()
        return True

    except Exception as e:
        print(f"✗ FAILED: {type(e).__name__}: {str(e)}")
        spark.stop()
        return False

if __name__ == "__main__":
    success = test_basic_connection()
    sys.exit(0 if success else 1)
