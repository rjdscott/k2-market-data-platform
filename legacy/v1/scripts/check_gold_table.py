#!/usr/bin/env python3
"""Check if gold_crypto_trades table exists and describe its schema."""

from pyspark.sql import SparkSession

# Create Spark session
spark = (
    SparkSession.builder
    .appName("CheckGoldTable")
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg.type", "rest")
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
    .config("spark.jars", "/opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar")
    .getOrCreate()
)

try:
    # List tables
    print("\\n" + "=" * 70)
    print("Tables in iceberg.market_data:")
    print("=" * 70)
    tables = spark.sql("SHOW TABLES IN iceberg.market_data")
    tables.show(truncate=False)

    # Check if gold_crypto_trades exists
    table_list = [row.tableName for row in tables.collect()]

    if "gold_crypto_trades" in table_list:
        print("\\n" + "=" * 70)
        print("gold_crypto_trades table EXISTS - Describing schema:")
        print("=" * 70)
        schema = spark.sql("DESCRIBE iceberg.market_data.gold_crypto_trades")
        schema.show(100, truncate=False)
    else:
        print("\\n" + "=" * 70)
        print("gold_crypto_trades table DOES NOT EXIST")
        print("=" * 70)

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

finally:
    spark.stop()
