#!/usr/bin/env python3
"""
Test 2: Verbose JDBC Error Diagnosis
Purpose: Get the FULL error stack from ClickHouse JDBC driver
"""

from pyspark.sql import SparkSession
import sys
import traceback

def test_verbose_connection():
    print("=" * 80)
    print("TEST 2: Verbose JDBC Error Diagnosis")
    print("=" * 80)

    spark = SparkSession.builder \
        .appName("JDBC-Test-Verbose") \
        .getOrCreate()

    try:
        print("\nTesting JDBC connection...")

        df = spark.read.jdbc(
            url="jdbc:clickhouse://clickhouse:8123/k2",
            table="(SELECT 1 as test_col) AS simple_test",
            properties={
                "driver": "com.clickhouse.jdbc.ClickHouseDriver",
                "user": "default",
                "password": "clickhouse"
            }
        )

        result = df.first()[0]
        print(f"✓ SUCCESS: {result}")
        spark.stop()
        return True

    except Exception as e:
        print(f"\n✗ EXCEPTION CAUGHT")
        print(f"Type: {type(e).__name__}")
        print(f"Message: {str(e)}")
        print("\nFull traceback:")
        traceback.print_exc()

        # Try to extract more info from Java exception
        if hasattr(e, 'java_exception'):
            print("\nJava Exception Details:")
            java_ex = e.java_exception
            print(f"  Java Class: {java_ex.getClass().getName()}")
            print(f"  Message: {java_ex.getMessage()}")

            # Check for cause chain
            cause = java_ex.getCause()
            depth = 0
            while cause is not None and depth < 10:
                depth += 1
                print(f"\n  Cause {depth}:")
                print(f"    Class: {cause.getClass().getName()}")
                print(f"    Message: {cause.getMessage()}")
                cause = cause.getCause()

        spark.stop()
        return False

if __name__ == "__main__":
    success = test_verbose_connection()
    sys.exit(0 if success else 1)
