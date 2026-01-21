#!/usr/bin/env python3
"""OHLCV Retention Enforcement - Automated Partition Deletion.

This script enforces retention policies on OHLCV tables by deleting old partitions:
- gold_ohlcv_1m: 90-day retention
- gold_ohlcv_5m: 180-day retention
- gold_ohlcv_30m: 1-year retention
- gold_ohlcv_1h: 3-year retention
- gold_ohlcv_1d: 5-year retention

Retention Strategy:
- Daily cleanup frequency for 1m/5m (high-churn tables)
- Weekly cleanup for 30m
- Monthly cleanup for 1h/1d
- Partition-level deletion (efficient, no table scan)
- Idempotent (safe to run multiple times)

Industry Best Practices:
✓ Partition pruning (efficient deletion without table scan)
✓ Retention policies aligned with use cases
✓ Automated enforcement via Prefect scheduling
✓ Structured logging (JSON format for observability)

Iceberg Advantage:
- Partition deletion is metadata-only operation (no data scan)
- ACID guarantees (atomic deletion)
- File-level tracking (efficient cleanup)

Usage:
    # Delete expired partitions for all tables
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/orchestration/flows/ohlcv_retention.py

    # Dry-run mode (preview deletions without executing)
    spark-submit --master spark://spark-master:7077 \
      --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar \
      /opt/k2/src/k2/orchestration/flows/ohlcv_retention.py \
      --dry-run

Related:
- Phase 13 Documentation: docs/phases/phase-13-ohlcv-analytics/
- Retention Policy: Storage Efficiency section
"""

import argparse

# Import spark_session module directly without adding k2 to path
import importlib.util
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from pyspark.sql import SparkSession

# Path: /opt/k2/src/k2/orchestration/flows/ohlcv_retention.py -> /opt/k2/src/k2/spark/utils/spark_session.py
spark_session_path = Path(__file__).parent.parent.parent / "spark" / "utils" / "spark_session.py"
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
            "job": "ohlcv-retention",
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


# Retention policies (in days)
RETENTION_POLICIES = {
    "1m": 90,  # 90 days
    "5m": 180,  # 180 days (6 months)
    "30m": 365,  # 1 year
    "1h": 1095,  # 3 years
    "1d": 1825,  # 5 years
}


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="OHLCV Retention Enforcement")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletions without executing (default: False)",
    )
    parser.add_argument(
        "--namespace",
        default="market_data",
        help="Iceberg namespace (default: market_data)",
    )
    return parser.parse_args()


def get_expired_partitions(
    spark: SparkSession, table: str, retention_days: int, dry_run: bool
) -> list[str]:
    """Get list of expired partitions for a table.

    Args:
        spark: SparkSession
        table: Full table name (e.g., iceberg.market_data.gold_ohlcv_1m)
        retention_days: Retention period in days
        dry_run: If True, only preview (don't delete)

    Returns:
        List of partition date strings that are expired
    """
    # Calculate cutoff date
    cutoff_date = datetime.now().date() - timedelta(days=retention_days)

    # Get all partition dates
    query = f"""
    SELECT DISTINCT window_date
    FROM {table}
    WHERE window_date < DATE '{cutoff_date}'
    ORDER BY window_date
    """

    try:
        expired_partitions = spark.sql(query).collect()
        return [row["window_date"] for row in expired_partitions]
    except Exception as e:
        logger.error(f"Failed to query partitions for {table}: {e}")
        return []


def delete_expired_partitions(
    spark: SparkSession,
    table: str,
    timeframe: str,
    retention_days: int,
    dry_run: bool,
) -> dict[str, int]:
    """Delete expired partitions from OHLCV table.

    Args:
        spark: SparkSession
        table: Full table name
        timeframe: Timeframe (1m, 5m, 30m, 1h, 1d)
        retention_days: Retention period in days
        dry_run: If True, only preview (don't delete)

    Returns:
        dict with deletion results
    """
    print(f"\n{'=' * 70}")
    print(f"Table: {table}")
    print(f"Retention: {retention_days} days")
    print(f"{'=' * 70}")

    # Get expired partitions
    expired_partitions = get_expired_partitions(spark, table, retention_days, dry_run)

    if not expired_partitions:
        print("✓ No expired partitions found")
        return {"table": table, "partitions_deleted": 0, "status": "no_action"}

    print(f"⚠ Found {len(expired_partitions)} expired partitions:")
    for partition_date in expired_partitions[:10]:  # Show first 10
        print(f"  - {partition_date}")
    if len(expired_partitions) > 10:
        print(f"  ... and {len(expired_partitions) - 10} more")

    if dry_run:
        print("✓ DRY-RUN: Partitions would be deleted (not executed)")
        logger.info(
            "Dry-run partition deletion",
            extra={
                "table": table,
                "timeframe": timeframe,
                "partitions": len(expired_partitions),
            },
        )
        return {"table": table, "partitions_deleted": 0, "status": "dry_run"}

    # Delete expired partitions
    print("✓ Deleting expired partitions...")
    deleted_count = 0

    for partition_date in expired_partitions:
        try:
            delete_query = f"""
            DELETE FROM {table}
            WHERE window_date = DATE '{partition_date}'
            """
            spark.sql(delete_query)
            deleted_count += 1
        except Exception as e:
            logger.error(f"Failed to delete partition {partition_date} from {table}: {e}")

    print(f"✓ Deleted {deleted_count}/{len(expired_partitions)} partitions")
    logger.info(
        "Partitions deleted",
        extra={
            "table": table,
            "timeframe": timeframe,
            "partitions_deleted": deleted_count,
        },
    )

    return {
        "table": table,
        "partitions_deleted": deleted_count,
        "status": "success",
    }


def main():
    """Main entry point."""
    args = parse_args()

    print("\n" + "=" * 70)
    print("K2 OHLCV Retention Enforcement")
    print("Automated Partition Deletion")
    print("=" * 70)
    print("\nConfiguration:")
    print(
        f"  • Mode: {'DRY-RUN (preview only)' if args.dry_run else 'EXECUTE (delete partitions)'}"
    )
    print(f"  • Namespace: {args.namespace}")
    print("\nRetention Policies:")
    for timeframe, days in RETENTION_POLICIES.items():
        print(f"  • {timeframe}: {days} days")
    print(f"\n{'=' * 70}\n")

    logger.info(
        "Starting OHLCV retention enforcement",
        extra={"dry_run": args.dry_run},
    )

    # Create Spark session
    spark = create_spark_session("K2-OHLCV-Retention")

    try:
        # Process each OHLCV table
        results = []
        for timeframe, retention_days in RETENTION_POLICIES.items():
            table = f"iceberg.{args.namespace}.gold_ohlcv_{timeframe}"
            result = delete_expired_partitions(
                spark, table, timeframe, retention_days, args.dry_run
            )
            results.append(result)

        # Summary report
        print(f"\n{'=' * 70}")
        print("Retention Enforcement Summary")
        print(f"{'=' * 70}\n")

        total_deleted = sum(r["partitions_deleted"] for r in results)

        for result in results:
            timeframe = result["table"].split("_")[-1]
            status = result["status"]
            deleted = result["partitions_deleted"]
            print(f"{timeframe:>4s}: {deleted:>3d} partitions - {status}")

        print(f"\nTotal Partitions Deleted: {total_deleted}")

        print(f"\n{'=' * 70}")
        if args.dry_run:
            print("✓ Dry-run completed (no partitions deleted)")
        else:
            print(f"✓ Retention enforcement completed ({total_deleted} partitions deleted)")
        print(f"{'=' * 70}\n")

        logger.info(
            "OHLCV retention enforcement completed",
            extra={"total_deleted": total_deleted, "dry_run": args.dry_run},
        )

        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        logger.error("OHLCV retention enforcement failed", extra={"error": str(e)})
        import traceback

        traceback.print_exc()
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
