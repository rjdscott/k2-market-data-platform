#!/usr/bin/env python3
"""Verify gold_crypto_trades table has data and check data quality."""

from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("VerifyGoldTable")
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg.type", "rest")
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
    .config("spark.jars.packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0")
    .getOrCreate()
)

try:
    print("\n" + "=" * 80)
    print("GOLD TABLE VERIFICATION")
    print("=" * 80)

    # Count total records
    count_df = spark.sql("SELECT COUNT(*) as count FROM iceberg.market_data.gold_crypto_trades")
    count = count_df.collect()[0]['count']
    print(f"\n✓ Total records: {count:,}")

    if count > 0:
        # Exchange breakdown
        print("\n" + "-" * 80)
        print("Exchange Breakdown:")
        print("-" * 80)
        exchange_df = spark.sql("""
            SELECT
                exchange,
                COUNT(*) as trade_count,
                MIN(exchange_date) as earliest_date,
                MAX(exchange_date) as latest_date
            FROM iceberg.market_data.gold_crypto_trades
            GROUP BY exchange
            ORDER BY exchange
        """)
        exchange_df.show(truncate=False)

        # Check for duplicates
        print("\n" + "-" * 80)
        print("Deduplication Check:")
        print("-" * 80)
        dup_df = spark.sql("""
            SELECT message_id, COUNT(*) as count
            FROM iceberg.market_data.gold_crypto_trades
            GROUP BY message_id
            HAVING COUNT(*) > 1
        """)
        dup_count = dup_df.count()

        if dup_count > 0:
            print(f"⚠️  Found {dup_count} duplicate message_ids!")
            dup_df.show(5)
        else:
            print("✓ No duplicates found - Deduplication working correctly")

        # Sample records
        print("\n" + "-" * 80)
        print("Sample Records (3):")
        print("-" * 80)
        sample_df = spark.sql("""
            SELECT
                message_id,
                trade_id,
                symbol,
                exchange,
                price,
                quantity,
                side,
                exchange_date,
                exchange_hour,
                gold_ingestion_timestamp
            FROM iceberg.market_data.gold_crypto_trades
            LIMIT 3
        """)
        sample_df.show(3, truncate=False)

        # Partition distribution
        print("\n" + "-" * 80)
        print("Partition Distribution (Hourly):")
        print("-" * 80)
        partition_df = spark.sql("""
            SELECT
                exchange_date,
                exchange_hour,
                COUNT(*) as trade_count
            FROM iceberg.market_data.gold_crypto_trades
            GROUP BY exchange_date, exchange_hour
            ORDER BY exchange_date DESC, exchange_hour DESC
            LIMIT 10
        """)
        partition_df.show(10, truncate=False)

        print("\n" + "=" * 80)
        print("✓ GOLD TABLE VERIFICATION COMPLETE")
        print("=" * 80)
        print(f"\nSummary:")
        print(f"  • Total records: {count:,}")
        print(f"  • Duplicates: {dup_count}")
        print(f"  • Status: {'✓ PASS' if dup_count == 0 else '✗ FAIL (duplicates found)'}")
        print("=" * 80)

    else:
        print("\n⚠️  No data in table yet.")
        print("   Wait for first batch to process (~60 seconds trigger interval)")
        print("\nTroubleshooting:")
        print("  1. Check Gold job logs: docker logs k2-gold-aggregation")
        print("  2. Verify Silver tables have data: SELECT COUNT(*) FROM silver_binance_trades;")
        print("  3. Check Spark UI: http://localhost:8090")

except Exception as e:
    print(f"\n✗ ERROR: {e}")
    import traceback
    traceback.print_exc()

finally:
    spark.stop()
