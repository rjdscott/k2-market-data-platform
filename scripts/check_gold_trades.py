#!/usr/bin/env python3
"""Quick script to check gold_crypto_trades data."""
import sys
from pathlib import Path
import importlib.util

# Import spark_session module directly
spark_session_path = Path(__file__).parent.parent / "src/k2/spark/utils/spark_session.py"
spec = importlib.util.spec_from_file_location("spark_session", spark_session_path)
spark_session_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spark_session_module)
create_spark_session = spark_session_module.create_spark_session

# Create Spark session
spark = create_spark_session("Check-Gold-Trades")

try:
    print("Checking gold_crypto_trades table...")

    # Count total rows
    total = spark.sql("SELECT COUNT(*) as count FROM iceberg.market_data.gold_crypto_trades").collect()[0]['count']
    print(f"\nTotal trades: {total:,}")

    if total > 0:
        # Show recent trades
        print("\nRecent trades (last 10):")
        spark.sql("""
            SELECT symbol, exchange, timestamp, price, quantity
            FROM iceberg.market_data.gold_crypto_trades
            ORDER BY timestamp DESC
            LIMIT 10
        """).show(truncate=False)

        # Show data distribution
        print("\nData distribution by exchange:")
        spark.sql("""
            SELECT exchange, COUNT(*) as trade_count
            FROM iceberg.market_data.gold_crypto_trades
            GROUP BY exchange
            ORDER BY trade_count DESC
        """).show()

        # Show date range
        print("\nDate range:")
        spark.sql("""
            SELECT
                MIN(FROM_UNIXTIME(timestamp/1000000)) as earliest_trade,
                MAX(FROM_UNIXTIME(timestamp/1000000)) as latest_trade
            FROM iceberg.market_data.gold_crypto_trades
        """).show(truncate=False)
    else:
        print("\n⚠ WARNING: gold_crypto_trades table is EMPTY!")
        print("\nPossible reasons:")
        print("  1. Streaming pipelines are not running")
        print("  2. No data has been ingested yet")
        print("  3. Bronze/Silver/Gold pipelines need to be started")

        print("\nTo check streaming jobs:")
        print("  docker ps | grep -E 'bronze|silver|gold'")

finally:
    spark.stop()
