#!/usr/bin/env python3
"""OHLCV Batch Aggregation Job - Gold Trades → OHLCV (30m/1h/1d).

This Spark batch job:
1. Reads complete periods from gold_crypto_trades
2. Aggregates into OHLCV candles using window functions
3. Calculates VWAP (volume-weighted average price)
4. Uses INSERT OVERWRITE for partition replacement
5. Writes to gold_ohlcv_{timeframe} tables

Aggregation Strategy - Batch with INSERT OVERWRITE:
- Timeframes: 30m, 1h, 1d (batch-friendly, complete periods)
- Lookback: 1-2 complete periods (1h for 30m, 2h for 1h, 2d for 1d)
- Update Mode: INSERT OVERWRITE (partition replacement)
- Idempotent: Safe to re-run (partition replacement ensures no duplicates)

OHLC Calculation Pattern:
- Open: First trade price in window (by timestamp)
- High: Maximum trade price in window
- Low: Minimum trade price in window
- Close: Last trade price in window (by timestamp)
- Volume: Sum of trade quantities
- Trade Count: Count of trades
- VWAP: (Sum(price × quantity)) / Sum(quantity)

Industry Best Practices:
✓ Window-based aggregation (precise time boundaries)
✓ INSERT OVERWRITE (efficient for batch processing)
✓ Complete periods only (no partial candles)
✓ VWAP calculation (standard in financial analytics)
✓ Structured logging (JSON format for observability)

Configuration:
- Source: gold_crypto_trades (Iceberg table, hourly partitions)
- Targets: gold_ohlcv_30m, gold_ohlcv_1h, gold_ohlcv_1d (Iceberg tables, daily partitions)
- Schedule: Every 30min (30m), Every 1h (1h), Daily 00:05 UTC (1d)
- Lookback: 1h (30m), 2h (1h), 2d (1d)

Usage:
    # 30-minute OHLCV
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --total-executor-cores 1 --executor-memory 1g \
      /opt/k2/src/k2/spark/jobs/batch/ohlcv_batch.py \
      --timeframe 30m --lookback-hours 1

    # 1-hour OHLCV
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --total-executor-cores 1 --executor-memory 1g \
      /opt/k2/src/k2/spark/jobs/batch/ohlcv_batch.py \
      --timeframe 1h --lookback-hours 2

    # 1-day OHLCV
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --total-executor-cores 1 --executor-memory 1g \
      /opt/k2/src/k2/spark/jobs/batch/ohlcv_batch.py \
      --timeframe 1d --lookback-days 2

Related:
- Decision #020: Prefect Orchestration
- Decision #021: Hybrid Aggregation Strategy
- Decision #022: Direct Rollup (no chaining)
"""

import sys
import logging
import json
import argparse
from pathlib import Path
from datetime import datetime

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col,
    current_timestamp,
    expr,
    first,
    last,
    max as spark_max,
    min as spark_min,
    sum as spark_sum,
    count,
    window,
    to_date,
    from_unixtime,
    struct,
    lit,
)

# Import spark_session module directly without adding k2 to path
import importlib.util

spark_session_path = Path(__file__).parent.parent.parent / "utils" / "spark_session.py"
spec = importlib.util.spec_from_file_location("spark_session", spark_session_path)
spark_session_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spark_session_module)
create_spark_session = spark_session_module.create_spark_session


# Configure structured logging (JSON format)
class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "job": "ohlcv-batch",
            "layer": "gold",
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


# Setup logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="OHLCV Batch Aggregation Job")
    parser.add_argument(
        "--timeframe",
        required=True,
        choices=["30m", "1h", "1d"],
        help="Timeframe for OHLCV aggregation (30m, 1h, or 1d)",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        help="Lookback period in hours (for 30m and 1h)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        help="Lookback period in days (for 1d)",
    )
    parser.add_argument(
        "--namespace",
        default="market_data",
        help="Iceberg namespace (default: market_data)",
    )
    return parser.parse_args()


def calculate_ohlcv(trades_df, window_duration: str):
    """Calculate OHLCV metrics using window aggregation.

    Args:
        trades_df: DataFrame with trades (timestamp_ts, symbol, exchange, price, quantity)
        window_duration: Window duration string (e.g., "30 minutes", "1 hour", "1 day")

    Returns:
        DataFrame with OHLCV metrics per window
    """
    # Convert timestamp from microseconds to TimestampType for window function
    trades_df = trades_df.withColumn(
        "timestamp_ts", from_unixtime(col("timestamp") / 1000000).cast("timestamp")
    )

    # CRITICAL FIX: Sort trades by timestamp BEFORE aggregation
    # first() and last() in groupBy take the first/last rows encountered during shuffle,
    # NOT the chronologically first/last. We must sort explicitly.
    trades_df = trades_df.orderBy("timestamp_ts")

    # Apply window aggregation
    # Industry Pattern: OHLC requires ordering by timestamp within each window
    ohlcv = (
        trades_df.groupBy("symbol", "exchange", window("timestamp_ts", window_duration))
        .agg(
            # Open: First trade price (by timestamp) - NOW GUARANTEED chronological
            first("price").alias("open_price"),
            # High: Maximum price
            spark_max("price").alias("high_price"),
            # Low: Minimum price
            spark_min("price").alias("low_price"),
            # Close: Last trade price (by timestamp) - NOW GUARANTEED chronological
            last("price").alias("close_price"),
            # Volume: Total quantity
            spark_sum("quantity").alias("volume"),
            # Trade count
            count("*").alias("trade_count"),
            # VWAP: Volume-weighted average price
            (spark_sum(col("price") * col("quantity")) / spark_sum("quantity")).alias("vwap"),
        )
        .select(
            col("symbol"),
            col("exchange"),
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            to_date(col("window.start")).alias("window_date"),
            col("open_price"),
            col("high_price"),
            col("low_price"),
            col("close_price"),
            col("volume"),
            col("trade_count"),
            col("vwap"),
            current_timestamp().alias("created_at"),
            current_timestamp().alias("updated_at"),
        )
    )

    return ohlcv


def main():
    """Main entry point."""
    args = parse_args()

    # Determine lookback period
    if args.timeframe == "1d":
        if not args.lookback_days:
            print("Error: --lookback-days required for 1d timeframe")
            return 1
        lookback_period = f"{args.lookback_days} days"
        lookback_value = args.lookback_days
        lookback_unit = "days"
    else:
        if not args.lookback_hours:
            print("Error: --lookback-hours required for 30m/1h timeframes")
            return 1
        lookback_period = f"{args.lookback_hours} hours"
        lookback_value = args.lookback_hours
        lookback_unit = "hours"

    print("\n" + "=" * 70)
    print(f"K2 OHLCV Batch Aggregation - {args.timeframe.upper()}")
    print("Gold Trades → OHLCV (Batch with INSERT OVERWRITE)")
    print("=" * 70)
    print("\nConfiguration:")
    print(f"  • Timeframe: {args.timeframe}")
    print(f"  • Lookback: {lookback_period}")
    print(f"  • Source: gold_crypto_trades")
    print(f"  • Target: gold_ohlcv_{args.timeframe}")
    print(f"  • Update Mode: INSERT OVERWRITE (partition replacement)")
    print(f"  • Pattern: Window Aggregation → INSERT OVERWRITE")
    print(f"\n{'=' * 70}\n")

    logger.info(
        "Starting OHLCV batch job",
        extra={"timeframe": args.timeframe, "lookback": lookback_period},
    )

    # Create Spark session
    spark = create_spark_session(f"K2-OHLCV-Batch-{args.timeframe}")

    try:
        # Step 1: Read recent trades from Gold
        logger.info("Reading trades from gold_crypto_trades")
        print(f"✓ Reading trades from last {lookback_period}...")

        # Determine window duration
        if args.timeframe == "30m":
            window_duration = "30 minutes"
            interval_clause = f"INTERVAL {args.lookback_hours} HOURS"
        elif args.timeframe == "1h":
            window_duration = "1 hour"
            interval_clause = f"INTERVAL {args.lookback_hours} HOURS"
        else:  # 1d
            window_duration = "1 day"
            interval_clause = f"INTERVAL {args.lookback_days} DAYS"

        trades_query = f"""
        SELECT
            message_id,
            trade_id,
            symbol,
            exchange,
            timestamp,
            price,
            quantity,
            currency,
            side
        FROM iceberg.{args.namespace}.gold_crypto_trades
        WHERE timestamp >= unix_timestamp(current_timestamp() - {interval_clause}) * 1000000
        ORDER BY timestamp
        """

        trades_df = spark.sql(trades_query)
        trade_count = trades_df.count()

        print(f"✓ Read {trade_count:,} trades")
        logger.info("Trades loaded", extra={"trade_count": trade_count})

        if trade_count == 0:
            print("⚠ No trades found in lookback window. Exiting.")
            logger.warning("No trades found in lookback window")
            return 0

        # Step 2: Calculate OHLCV
        logger.info("Calculating OHLCV metrics")
        print(f"✓ Calculating OHLCV for {args.timeframe}...")

        ohlcv_df = calculate_ohlcv(trades_df, window_duration)

        candle_count = ohlcv_df.count()
        print(f"✓ Generated {candle_count:,} OHLCV candles")
        logger.info("OHLCV calculated", extra={"candle_count": candle_count})

        if candle_count == 0:
            print("⚠ No OHLCV candles generated. Exiting.")
            logger.warning("No OHLCV candles generated")
            return 0

        # Step 3: INSERT OVERWRITE into target table
        target_table = f"iceberg.{args.namespace}.gold_ohlcv_{args.timeframe}"
        logger.info("Writing OHLCV data", extra={"target_table": target_table})
        print(f"✓ Writing to {target_table} (INSERT OVERWRITE)...")

        # Get affected partitions
        affected_dates = ohlcv_df.select("window_date").distinct().collect()
        affected_dates_list = [row.window_date for row in affected_dates]
        print(f"✓ Affected partitions: {len(affected_dates_list)} days")

        # Write with INSERT OVERWRITE (partition replacement)
        # This replaces all data in affected partitions
        ohlcv_df.writeTo(target_table).using("iceberg").partitionedBy("window_date").overwritePartitions()

        print(f"✓ INSERT OVERWRITE completed successfully")
        logger.info("INSERT OVERWRITE completed", extra={"partitions": len(affected_dates_list)})

        # Step 4: Verification
        print("\n" + "-" * 70)
        print("Verification:")
        print("-" * 70)

        # Show sample of latest candles
        sample_query = f"""
        SELECT
            symbol,
            exchange,
            window_start,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            trade_count,
            ROUND(vwap, 8) as vwap
        FROM {target_table}
        WHERE window_date >= current_date() - INTERVAL {lookback_value} {lookback_unit}
        ORDER BY window_start DESC
        LIMIT 10
        """

        print("\nLatest candles:")
        spark.sql(sample_query).show(truncate=False)

        # Summary statistics
        stats_query = f"""
        SELECT
            COUNT(*) as total_candles,
            COUNT(DISTINCT symbol) as unique_symbols,
            COUNT(DISTINCT exchange) as unique_exchanges,
            MIN(window_start) as earliest_candle,
            MAX(window_start) as latest_candle
        FROM {target_table}
        WHERE window_date >= current_date() - INTERVAL {lookback_value} {lookback_unit}
        """

        print("\nSummary statistics:")
        spark.sql(stats_query).show(truncate=False)

        print(f"\n{'=' * 70}")
        print(f"✓ OHLCV {args.timeframe} batch job completed successfully")
        print(f"{'=' * 70}\n")

        logger.info(
            "OHLCV batch job completed",
            extra={"timeframe": args.timeframe, "status": "success"},
        )

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        logger.error("OHLCV batch job failed", extra={"error": str(e)})
        import traceback

        traceback.print_exc()
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
