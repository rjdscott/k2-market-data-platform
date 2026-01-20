#!/usr/bin/env python3
"""Check if gold_crypto_trades table has data."""

from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("CheckGoldData")
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg.type", "rest")
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
    .config("spark.jars", "/opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar")
    .getOrCreate()
)

try:
    print("\n" + "=" * 70)
    print("Gold Table Data Check")
    print("=" * 70)

    # Count total records
    count = spark.sql("SELECT COUNT(*) FROM iceberg.market_data.gold_crypto_trades").collect()[0][0]
    print(f"\nTotal records in gold_crypto_trades: {count:,}")

    if count > 0:
        # Show exchange breakdown
        print("\nExchange breakdown:")
        spark.sql("""
            SELECT exchange, COUNT(*) as trade_count
            FROM iceberg.market_data.gold_crypto_trades
            GROUP BY exchange
            ORDER BY exchange
        """).show()

        # Show sample records
        print("\nSample records (5):")
        spark.sql("""
            SELECT message_id, trade_id, symbol, exchange, price, quantity,
                   side, gold_ingestion_timestamp, exchange_date, exchange_hour
            FROM iceberg.market_data.gold_crypto_trades
            LIMIT 5
        """).show(truncate=False)

        # Check for duplicates
        print("\nDuplicate check:")
        duplicates = spark.sql("""
            SELECT message_id, COUNT(*) as count
            FROM iceberg.market_data.gold_crypto_trades
            GROUP BY message_id
            HAVING COUNT(*) > 1
        """).collect()

        if len(duplicates) > 0:
            print(f"⚠️  Found {len(duplicates)} duplicate message_ids!")
        else:
            print("✓ No duplicates found (deduplication working)")

    else:
        print("\n⚠️  No data in table yet. Wait for first batch to process.")

except Exception as e:
    print(f"\n✗ ERROR: {e}")

finally:
    spark.stop()
