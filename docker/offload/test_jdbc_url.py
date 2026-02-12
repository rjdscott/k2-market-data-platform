#!/usr/bin/env python3
"""
Test 3: Test different JDBC URL formats
Purpose: Find which URL format works with ClickHouse
"""

from pyspark.sql import SparkSession
import sys

def test_url_formats():
    print("=" * 80)
    print("TEST 3: JDBC URL Format Testing")
    print("=" * 80)

    spark = SparkSession.builder.appName("JDBC-URL-Test").getOrCreate()

    test_cases = [
        {
            "name": "Format 1: URL with embedded credentials",
            "url": "jdbc:clickhouse://default:clickhouse@clickhouse:8123/k2",
            "properties": {
                "driver": "com.clickhouse.jdbc.ClickHouseDriver"
            }
        },
        {
            "name": "Format 2: URL params only",
            "url": "jdbc:clickhouse://clickhouse:8123/k2?user=default&password=clickhouse",
            "properties": {
                "driver": "com.clickhouse.jdbc.ClickHouseDriver"
            }
        },
        {
            "name": "Format 3: Properties only (original)",
            "url": "jdbc:clickhouse://clickhouse:8123/k2",
            "properties": {
                "driver": "com.clickhouse.jdbc.ClickHouseDriver",
                "user": "default",
                "password": "clickhouse"
            }
        }
    ]

    for i, test in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"Test Case {i}: {test['name']}")
        print(f"  URL: {test['url']}")
        print(f"  Properties: {test['properties']}")

        try:
            df = spark.read.jdbc(
                url=test['url'],
                table="(SELECT 1 as test_col) AS test",
                properties=test['properties']
            )
            result = df.first()[0]
            print(f"  ✓ SUCCESS: Query returned {result}")
            spark.stop()
            return True

        except Exception as e:
            error_msg = str(e)
            if "k2_user" in error_msg:
                print(f"  ✗ FAILED: Still trying to use k2_user")
            elif "default" in error_msg:
                print(f"  ✗ FAILED: Error with default user")
            elif "Authentication" in error_msg:
                print(f"  ✗ FAILED: Authentication error")
            else:
                print(f"  ✗ FAILED: {error_msg[:100]}...")

    print(f"\n{'='*60}")
    print("All test cases failed")
    spark.stop()
    return False

if __name__ == "__main__":
    success = test_url_formats()
    sys.exit(0 if success else 1)
