#!/usr/bin/env python3
"""Monitor Gold table for multiple successful batch writes."""

import time
from datetime import datetime
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("VerifyGoldBatches")
    .config("spark.sql.catalog.iceberg", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.iceberg.type", "rest")
    .config("spark.sql.catalog.iceberg.uri", "http://iceberg-rest:8181")
    .config("spark.jars", "/opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar")
    .getOrCreate()
)

try:
    print("\n" + "=" * 80)
    print("GOLD TABLE BATCH VERIFICATION")
    print("=" * 80)
    print(f"Started at: {datetime.utcnow().isoformat()}Z")
    print("Checking for data every 30 seconds...")
    print("=" * 80)

    checks = []
    for i in range(5):  # Check 5 times over 2.5 minutes
        try:
            count_df = spark.sql("SELECT COUNT(*) as count FROM iceberg.market_data.gold_crypto_trades")
            count = count_df.collect()[0]['count']
            timestamp = datetime.utcnow().isoformat()

            checks.append({"check": i + 1, "timestamp": timestamp, "count": count})

            print(f"\nCheck {i + 1}/5 at {timestamp}Z:")
            print(f"  Total records: {count:,}")

            if count > 0:
                # Get exchange breakdown
                exchange_df = spark.sql("""
                    SELECT exchange, COUNT(*) as trade_count
                    FROM iceberg.market_data.gold_crypto_trades
                    GROUP BY exchange
                    ORDER BY exchange
                """)
                print("  Exchange breakdown:")
                for row in exchange_df.collect():
                    print(f"    - {row['exchange']}: {row['trade_count']:,} trades")

                # Get latest partition
                latest_df = spark.sql("""
                    SELECT exchange_date, exchange_hour, COUNT(*) as trade_count
                    FROM iceberg.market_data.gold_crypto_trades
                    GROUP BY exchange_date, exchange_hour
                    ORDER BY exchange_date DESC, exchange_hour DESC
                    LIMIT 1
                """)
                latest = latest_df.collect()
                if latest:
                    print(f"  Latest partition: {latest[0]['exchange_date']} hour {latest[0]['exchange_hour']} ({latest[0]['trade_count']} trades)")
            else:
                print("  ⚠️  No data yet")

            if i < 4:  # Don't sleep after last check
                print(f"\nWaiting 30 seconds for next check...")
                time.sleep(30)

        except Exception as e:
            print(f"\n✗ Error on check {i + 1}: {e}")
            checks.append({"check": i + 1, "timestamp": datetime.utcnow().isoformat(), "error": str(e)})
            if i < 4:
                time.sleep(30)

    # Summary
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)

    successful_checks = [c for c in checks if "count" in c and c["count"] > 0]

    if len(successful_checks) == 0:
        print("❌ NO DATA FOUND - Gold job may not be writing successfully")
        print("\nTroubleshooting:")
        print("  1. Check Gold job logs: docker logs k2-gold-aggregation")
        print("  2. Verify Silver tables have data: SELECT COUNT(*) FROM silver_binance_trades;")
        print("  3. Check for errors: docker logs k2-gold-aggregation | grep ERROR")

    elif len(successful_checks) == 1:
        print("⚠️  PARTIAL SUCCESS - Data found but not increasing")
        print(f"   Record count: {successful_checks[0]['count']:,}")
        print("\nPossible issues:")
        print("  - Job may have stopped processing after first batch")
        print("  - Check logs for errors or warnings")

    else:
        # Check if data is increasing
        first_count = successful_checks[0]["count"]
        last_count = successful_checks[-1]["count"]

        if last_count > first_count:
            print("✅ DATA FLOWING SUCCESSFULLY")
            print(f"   First check: {first_count:,} records")
            print(f"   Last check:  {last_count:,} records")
            print(f"   Growth:      {last_count - first_count:,} records ({len(successful_checks)} checks)")

            # Check for duplicates
            dup_df = spark.sql("""
                SELECT message_id, COUNT(*) as count
                FROM iceberg.market_data.gold_crypto_trades
                GROUP BY message_id
                HAVING COUNT(*) > 1
            """)
            dup_count = dup_df.count()

            if dup_count == 0:
                print("   ✅ No duplicates found (deduplication working)")
            else:
                print(f"   ⚠️  Found {dup_count} duplicate message_ids (deduplication issue)")

        else:
            print("⚠️  DATA NOT INCREASING")
            print(f"   All checks show: {first_count:,} records")
            print("   Job may be stuck or no new data arriving")

    print("\nDetailed Checks:")
    for c in checks:
        if "count" in c:
            print(f"  {c['check']}. {c['timestamp']}Z: {c['count']:,} records")
        else:
            print(f"  {c['check']}. {c['timestamp']}Z: ERROR - {c.get('error', 'unknown')}")

    print("=" * 80)

except Exception as e:
    print(f"\n✗ FATAL ERROR: {e}")
    import traceback
    traceback.print_exc()

finally:
    spark.stop()
