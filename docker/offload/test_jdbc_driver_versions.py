#!/usr/bin/env python3
"""
Test 4: Test different ClickHouse JDBC driver versions
Purpose: Find which driver version works correctly
"""

from pyspark.sql import SparkSession
import sys

def test_driver_version(version):
    """Test a specific JDBC driver version"""
    print(f"\n{'='*60}")
    print(f"Testing ClickHouse JDBC Driver: {version}")
    print(f"{'='*60}")

    try:
        # Create Spark session with specific driver version
        spark = SparkSession.builder \
            .appName(f"JDBC-Test-{version}") \
            .config("spark.jars.packages", f"com.clickhouse:clickhouse-jdbc:{version}") \
            .getOrCreate()

        print(f"  Spark session created with driver {version}")

        # Test connection
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
        print(f"  ✓ SUCCESS with driver {version}: Result = {result}")
        spark.stop()
        return True

    except Exception as e:
        error_str = str(e)
        if "k2_user" in error_str:
            print(f"  ✗ FAILED: Still using k2_user (wrong user)")
        elif "Authentication" in error_str:
            print(f"  ✗ FAILED: Authentication error")
        else:
            print(f"  ✗ FAILED: {error_str[:150]}")

        try:
            spark.stop()
        except:
            pass
        return False

if __name__ == "__main__":
    print("=" * 80)
    print("TEST 4: ClickHouse JDBC Driver Version Testing")
    print("=" * 80)

    # Test different driver versions
    versions = [
        "0.6.3",  # Newer stable version
        "0.5.0",  # Middle version
        "0.4.6",  # Current version (for comparison)
    ]

    for version in versions:
        if test_driver_version(version):
            print(f"\n{'='*80}")
            print(f"✓ SOLUTION FOUND: ClickHouse JDBC Driver {version} works!")
            print(f"{'='*80}")
            sys.exit(0)

    print(f"\n{'='*80}")
    print("✗ All driver versions failed")
    print(f"{'='*80}")
    sys.exit(1)
