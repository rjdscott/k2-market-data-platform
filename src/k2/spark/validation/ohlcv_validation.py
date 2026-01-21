#!/usr/bin/env python3
"""OHLCV Data Quality Validation - Invariant Checks.

This script validates OHLCV data quality using 4 critical invariants:
1. Price Relationships: high >= open, high >= close, low <= open, low <= close
2. VWAP Bounds: low <= vwap <= high
3. Positive Metrics: volume > 0, trade_count > 0
4. Window Alignment: window_end > window_start

Industry Best Practices:
✓ Invariant-based validation (mathematical guarantees)
✓ Post-job validation (run after OHLCV generation)
✓ Non-blocking warnings (alerts on violations, doesn't halt pipeline)
✓ Structured logging (JSON format for observability)

Target Tables:
- gold_ohlcv_1m (90-day retention)
- gold_ohlcv_5m (180-day retention)
- gold_ohlcv_30m (1-year retention)
- gold_ohlcv_1h (3-year retention)
- gold_ohlcv_1d (5-year retention)

Usage:
    # Validate specific timeframe
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/validation/ohlcv_validation.py \
      --timeframe 1m

    # Validate all timeframes
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/spark/validation/ohlcv_validation.py \
      --timeframe all

Related:
- Phase 13 Documentation: docs/phases/phase-13-ohlcv-analytics/
- Data Quality Plan: Invariant checks section
"""

import argparse

# Import spark_session module directly without adding k2 to path
import importlib.util
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pyspark.sql import SparkSession

# Path: /opt/k2/src/k2/spark/validation/ohlcv_validation.py -> /opt/k2/src/k2/spark/utils/spark_session.py
spark_session_path = Path(__file__).parent.parent / "utils" / "spark_session.py"
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
            "job": "ohlcv-validation",
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
    parser = argparse.ArgumentParser(description="OHLCV Data Quality Validation")
    parser.add_argument(
        "--timeframe",
        required=True,
        choices=["1m", "5m", "30m", "1h", "1d", "all"],
        help="Timeframe to validate (1m, 5m, 30m, 1h, 1d, or all)",
    )
    parser.add_argument(
        "--namespace",
        default="market_data",
        help="Iceberg namespace (default: market_data)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="Validate last N days of data (default: 7)",
    )
    return parser.parse_args()


def validate_price_relationships(
    spark: SparkSession, table: str, lookback_days: int
) -> dict[str, Any]:
    """Invariant 1: Validate price relationships (OHLC consistency).

    Rules:
    - high_price >= open_price
    - high_price >= close_price
    - low_price <= open_price
    - low_price <= close_price

    Args:
        spark: SparkSession
        table: Full table name (e.g., iceberg.market_data.gold_ohlcv_1m)
        lookback_days: Number of days to validate

    Returns:
        dict with validation results
    """
    query = f"""
    SELECT COUNT(*) as violations
    FROM {table}
    WHERE window_date >= current_date() - INTERVAL {lookback_days} DAYS
      AND (
          high_price < open_price OR
          high_price < close_price OR
          low_price > open_price OR
          low_price > close_price
      )
    """

    result = spark.sql(query).collect()[0]
    violations = result["violations"]

    return {
        "invariant": "price_relationships",
        "description": "OHLC price consistency (high >= open/close, low <= open/close)",
        "violations": violations,
        "passed": violations == 0,
    }


def validate_vwap_bounds(spark: SparkSession, table: str, lookback_days: int) -> dict[str, Any]:
    """Invariant 2: Validate VWAP is within price bounds.

    Rules:
    - vwap >= low_price
    - vwap <= high_price

    Args:
        spark: SparkSession
        table: Full table name
        lookback_days: Number of days to validate

    Returns:
        dict with validation results
    """
    query = f"""
    SELECT COUNT(*) as violations
    FROM {table}
    WHERE window_date >= current_date() - INTERVAL {lookback_days} DAYS
      AND (vwap < low_price OR vwap > high_price)
    """

    result = spark.sql(query).collect()[0]
    violations = result["violations"]

    return {
        "invariant": "vwap_bounds",
        "description": "VWAP within low/high price bounds",
        "violations": violations,
        "passed": violations == 0,
    }


def validate_positive_metrics(
    spark: SparkSession, table: str, lookback_days: int
) -> dict[str, Any]:
    """Invariant 3: Validate positive metrics (volume, trade_count > 0).

    Rules:
    - volume > 0
    - trade_count > 0

    Args:
        spark: SparkSession
        table: Full table name
        lookback_days: Number of days to validate

    Returns:
        dict with validation results
    """
    query = f"""
    SELECT COUNT(*) as violations
    FROM {table}
    WHERE window_date >= current_date() - INTERVAL {lookback_days} DAYS
      AND (volume <= 0 OR trade_count <= 0)
    """

    result = spark.sql(query).collect()[0]
    violations = result["violations"]

    return {
        "invariant": "positive_metrics",
        "description": "Volume and trade_count are positive",
        "violations": violations,
        "passed": violations == 0,
    }


def validate_window_alignment(
    spark: SparkSession, table: str, lookback_days: int
) -> dict[str, Any]:
    """Invariant 4: Validate window time alignment.

    Rules:
    - window_end > window_start

    Args:
        spark: SparkSession
        table: Full table name
        lookback_days: Number of days to validate

    Returns:
        dict with validation results
    """
    query = f"""
    SELECT COUNT(*) as violations
    FROM {table}
    WHERE window_date >= current_date() - INTERVAL {lookback_days} DAYS
      AND window_end <= window_start
    """

    result = spark.sql(query).collect()[0]
    violations = result["violations"]

    return {
        "invariant": "window_alignment",
        "description": "Window end time is after start time",
        "violations": violations,
        "passed": violations == 0,
    }


def validate_table(
    spark: SparkSession, timeframe: str, namespace: str, lookback_days: int
) -> list[dict[str, Any]]:
    """Run all invariant checks on a single OHLCV table.

    Args:
        spark: SparkSession
        timeframe: Timeframe (1m, 5m, 30m, 1h, 1d)
        namespace: Iceberg namespace
        lookback_days: Number of days to validate

    Returns:
        List of validation results
    """
    table = f"iceberg.{namespace}.gold_ohlcv_{timeframe}"

    print(f"\n{'=' * 70}")
    print(f"Validating: {table}")
    print(f"{'=' * 70}")

    # Get table stats
    stats_query = f"""
    SELECT
        COUNT(*) as total_rows,
        COUNT(DISTINCT window_date) as unique_dates,
        MIN(window_start) as earliest_candle,
        MAX(window_start) as latest_candle
    FROM {table}
    WHERE window_date >= current_date() - INTERVAL {lookback_days} DAYS
    """
    stats = spark.sql(stats_query).collect()[0]
    print("\nTable Statistics:")
    print(f"  Total rows: {stats['total_rows']:,}")
    print(f"  Unique dates: {stats['unique_dates']}")
    print(f"  Earliest candle: {stats['earliest_candle']}")
    print(f"  Latest candle: {stats['latest_candle']}")

    # Run all invariant checks
    results = []
    print("\nRunning Invariant Checks:")

    # Invariant 1: Price relationships
    result = validate_price_relationships(spark, table, lookback_days)
    results.append(result)
    status = "✓ PASS" if result["passed"] else f"✗ FAIL ({result['violations']} violations)"
    print(f"  1. {result['description']}: {status}")

    # Invariant 2: VWAP bounds
    result = validate_vwap_bounds(spark, table, lookback_days)
    results.append(result)
    status = "✓ PASS" if result["passed"] else f"✗ FAIL ({result['violations']} violations)"
    print(f"  2. {result['description']}: {status}")

    # Invariant 3: Positive metrics
    result = validate_positive_metrics(spark, table, lookback_days)
    results.append(result)
    status = "✓ PASS" if result["passed"] else f"✗ FAIL ({result['violations']} violations)"
    print(f"  3. {result['description']}: {status}")

    # Invariant 4: Window alignment
    result = validate_window_alignment(spark, table, lookback_days)
    results.append(result)
    status = "✓ PASS" if result["passed"] else f"✗ FAIL ({result['violations']} violations)"
    print(f"  4. {result['description']}: {status}")

    return results


def main():
    """Main entry point."""
    args = parse_args()

    print("\n" + "=" * 70)
    print("K2 OHLCV Data Quality Validation")
    print("Invariant-Based Quality Checks")
    print("=" * 70)
    print("\nConfiguration:")
    print(f"  • Timeframe: {args.timeframe}")
    print(f"  • Lookback: {args.lookback_days} days")
    print(f"  • Namespace: {args.namespace}")
    print(f"\n{'=' * 70}\n")

    logger.info(
        "Starting OHLCV validation",
        extra={"timeframe": args.timeframe, "lookback_days": args.lookback_days},
    )

    # Create Spark session
    spark = create_spark_session("K2-OHLCV-Validation")

    try:
        # Determine which timeframes to validate
        if args.timeframe == "all":
            timeframes = ["1m", "5m", "30m", "1h", "1d"]
        else:
            timeframes = [args.timeframe]

        # Run validation for each timeframe
        all_results = {}
        for timeframe in timeframes:
            results = validate_table(spark, timeframe, args.namespace, args.lookback_days)
            all_results[timeframe] = results

        # Summary report
        print(f"\n{'=' * 70}")
        print("Validation Summary")
        print(f"{'=' * 70}\n")

        total_checks = 0
        passed_checks = 0
        failed_checks = 0

        for timeframe, results in all_results.items():
            table_passed = all(r["passed"] for r in results)
            status = "✓ PASS" if table_passed else "✗ FAIL"
            print(f"{timeframe:>4s}: {status}")

            for result in results:
                total_checks += 1
                if result["passed"]:
                    passed_checks += 1
                else:
                    failed_checks += 1
                    logger.warning(
                        f"Invariant violation: {timeframe} - {result['invariant']}",
                        extra={"violations": result["violations"]},
                    )

        print(f"\nTotal Checks: {total_checks}")
        print(f"  Passed: {passed_checks}")
        print(f"  Failed: {failed_checks}")

        success_rate = (passed_checks / total_checks * 100) if total_checks > 0 else 0
        print(f"  Success Rate: {success_rate:.1f}%")

        print(f"\n{'=' * 70}")
        if failed_checks == 0:
            print("✓ All invariant checks passed")
            print(f"{'=' * 70}\n")
            logger.info("OHLCV validation completed", extra={"status": "success"})
            return 0
        else:
            print(f"⚠ {failed_checks} invariant checks failed")
            print(f"{'=' * 70}\n")
            logger.warning(
                "OHLCV validation completed with failures",
                extra={"failed_checks": failed_checks},
            )
            return 1

    except Exception as e:
        print(f"\n✗ Error: {e}")
        logger.error("OHLCV validation failed", extra={"error": str(e)})
        import traceback

        traceback.print_exc()
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
