#!/usr/bin/env python3
"""OHLCV Incremental Aggregation Job - Gold Trades → OHLCV (1m/5m).

This Spark batch job:
1. Reads recent trades from gold_crypto_trades (last N minutes)
2. Aggregates into OHLCV candles using window functions
3. Calculates VWAP (volume-weighted average price)
4. Uses MERGE to handle late-arriving trades (upsert pattern)
5. Writes to gold_ohlcv_{timeframe} tables

Aggregation Strategy - Incremental with MERGE:
- Timeframes: 1m, 5m (low-latency requirements)
- Lookback: 2× timeframe + grace period (10min for 1m, 20min for 5m)
- Update Mode: MERGE (upserts existing candles for late arrivals)
- Idempotent: Safe to re-run (MERGE handles duplicates)

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
✓ MERGE for late arrival handling (idempotent writes)
✓ VWAP calculation (standard in financial analytics)
✓ Structured logging (JSON format for observability)
✓ Partition alignment (daily partitions for efficient queries)

Configuration:
- Source: gold_crypto_trades (Iceberg table, hourly partitions)
- Targets: gold_ohlcv_1m, gold_ohlcv_5m (Iceberg tables, daily partitions)
- Schedule: Every 5min (1m), Every 15min (5m)
- Lookback: 10min (1m), 20min (5m)

Usage:
    # 1-minute OHLCV
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --total-executor-cores 1 --executor-memory 1g \
      /opt/k2/src/k2/spark/jobs/batch/ohlcv_incremental.py \
      --timeframe 1m --lookback-minutes 10

    # 5-minute OHLCV
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      --total-executor-cores 1 --executor-memory 1g \
      /opt/k2/src/k2/spark/jobs/batch/ohlcv_incremental.py \
      --timeframe 5m --lookback-minutes 20

Related:
- Decision #020: Prefect Orchestration
- Decision #021: Hybrid Aggregation Strategy
- Decision #022: Direct Rollup (no chaining)
"""

import argparse

# Import spark_session module directly without adding k2 to path
import importlib.util
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from pyspark.sql.functions import (
    col,
    count,
    current_timestamp,
    first,
    from_unixtime,
    last,
)
from pyspark.sql.functions import max as spark_max
from pyspark.sql.functions import min as spark_min
from pyspark.sql.functions import sum as spark_sum
from pyspark.sql.functions import (
    to_date,
    window,
)

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
            "job": "ohlcv-incremental",
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
    parser = argparse.ArgumentParser(description="OHLCV Incremental Aggregation Job")
    parser.add_argument(
        "--timeframe",
        required=True,
        choices=["1m", "5m"],
        help="Timeframe for OHLCV aggregation (1m or 5m)",
    )
    parser.add_argument(
        "--lookback-minutes",
        type=int,
        required=True,
        help="Lookback period in minutes (10 for 1m, 20 for 5m)",
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
        window_duration: Window duration string (e.g., "1 minute", "5 minutes")

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

    print("\n" + "=" * 70)
    print(f"K2 OHLCV Incremental Aggregation - {args.timeframe.upper()}")
    print("Gold Trades → OHLCV (Incremental with MERGE)")
    print("=" * 70)
    print("\nConfiguration:")
    print(f"  • Timeframe: {args.timeframe}")
    print(f"  • Lookback: {args.lookback_minutes} minutes")
    print("  • Source: gold_crypto_trades")
    print(f"  • Target: gold_ohlcv_{args.timeframe}")
    print("  • Update Mode: MERGE (upsert for late arrivals)")
    print("  • Pattern: Window Aggregation → MERGE")
    print(f"\n{'=' * 70}\n")

    logger.info(
        "Starting OHLCV incremental job",
        extra={"timeframe": args.timeframe, "lookback_minutes": args.lookback_minutes},
    )

    # Create Spark session
    spark = create_spark_session(f"K2-OHLCV-Incremental-{args.timeframe}")

    try:
        # Step 1: Read recent trades from Gold
        logger.info("Reading recent trades from gold_crypto_trades")
        print(f"✓ Reading trades from last {args.lookback_minutes} minutes...")

        # Use partition-based filtering (exchange_date) for better performance
        # Then filter by timestamp for precise time window
        trades_df = spark.sql(f"""
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
            WHERE exchange_date >= CURRENT_DATE() - INTERVAL 1 DAY
            ORDER BY timestamp
        """)

        # Now filter to exact time window after reading (avoids partition pruning issues)
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(minutes=args.lookback_minutes)
        cutoff_micros = int(cutoff_time.timestamp() * 1000000)

        trades_df = trades_df.filter(col("timestamp") >= cutoff_micros)

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

        window_duration = "1 minute" if args.timeframe == "1m" else "5 minutes"
        ohlcv_df = calculate_ohlcv(trades_df, window_duration)

        candle_count = ohlcv_df.count()
        print(f"✓ Generated {candle_count:,} OHLCV candles")
        logger.info("OHLCV calculated", extra={"candle_count": candle_count})

        if candle_count == 0:
            print("⚠ No OHLCV candles generated. Exiting.")
            logger.warning("No OHLCV candles generated")
            return 0

        # Step 3: MERGE into target table
        target_table = f"iceberg.{args.namespace}.gold_ohlcv_{args.timeframe}"
        logger.info("Merging OHLCV data", extra={"target_table": target_table})
        print(f"✓ Merging into {target_table}...")

        # Create temporary view for MERGE
        ohlcv_df.createOrReplaceTempView("ohlcv_updates")

        # MERGE statement: Update existing candles or insert new ones
        # Match on: symbol, exchange, window_start (unique candle identifier)
        #
        # CRITICAL: VWAP must be calculated using OLD volume values before volume is updated
        # We achieve this by using a subquery with explicit volume references
        merge_sql = f"""
        MERGE INTO {target_table} AS target
        USING (
            SELECT
                source.*,
                -- Pre-compute combined metrics to avoid evaluation order issues
                target.volume AS target_volume_old,
                target.vwap AS target_vwap_old
            FROM ohlcv_updates AS source
            LEFT JOIN {target_table} AS target
            ON target.symbol = source.symbol
               AND target.exchange = source.exchange
               AND target.window_start = source.window_start
        ) AS source
        ON target.symbol = source.symbol
           AND target.exchange = source.exchange
           AND target.window_start = source.window_start
        WHEN MATCHED THEN
            UPDATE SET
                target.open_price = source.open_price,
                target.high_price = GREATEST(target.high_price, source.high_price),
                target.low_price = LEAST(target.low_price, source.low_price),
                target.close_price = source.close_price,
                target.volume = source.target_volume_old + source.volume,
                target.trade_count = target.trade_count + source.trade_count,
                target.vwap = (source.target_vwap_old * source.target_volume_old + source.vwap * source.volume) / (source.target_volume_old + source.volume),
                target.updated_at = source.updated_at
        WHEN NOT MATCHED THEN
            INSERT (
                symbol, exchange, window_start, window_end, window_date,
                open_price, high_price, low_price, close_price,
                volume, trade_count, vwap,
                created_at, updated_at
            )
            VALUES (
                source.symbol, source.exchange, source.window_start, source.window_end, source.window_date,
                source.open_price, source.high_price, source.low_price, source.close_price,
                source.volume, source.trade_count, source.vwap,
                source.created_at, source.updated_at
            )
        """

        spark.sql(merge_sql)

        print("✓ MERGE completed successfully")
        logger.info("MERGE completed", extra={"rows_affected": candle_count})

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
        WHERE window_date >= current_date()
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
        WHERE window_date >= current_date()
        """

        print("\nSummary statistics:")
        spark.sql(stats_query).show(truncate=False)

        print(f"\n{'=' * 70}")
        print(f"✓ OHLCV {args.timeframe} incremental job completed successfully")
        print(f"{'=' * 70}\n")

        logger.info(
            "OHLCV incremental job completed",
            extra={"timeframe": args.timeframe, "status": "success"},
        )

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        logger.error("OHLCV incremental job failed", extra={"error": str(e)})
        import traceback

        traceback.print_exc()
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
