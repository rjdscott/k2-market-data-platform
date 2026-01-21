#!/usr/bin/env python3
"""Iceberg Table Maintenance - Snapshot Expiration & Orphan File Cleanup.

This script performs critical maintenance operations on Iceberg tables:
1. Expire old snapshots (older than 7 days, keep last 100)
2. Remove orphan data files (files no longer referenced by metadata)
3. Remove orphan metadata files (old metadata.json versions)

Best Practice Schedule:
- Run daily via cron or K8s CronJob
- Adjust retention based on time-travel requirements
- Monitor execution time and adjust batch sizes if needed

Why This is Critical:
- Without snapshot expiration: Metadata grows unbounded
- Without orphan cleanup: Storage costs accumulate (orphan files never deleted)
- Financial data requirement: Balance auditability with cost efficiency

Safety Features:
- Dry-run mode by default (--execute flag required)
- Per-table operations with error handling
- Retention policies prevent accidental data loss

Usage:
    # Dry run (shows what would be deleted)
    python scripts/iceberg_maintenance.py

    # Execute cleanup
    python scripts/iceberg_maintenance.py --execute

    # Custom retention
    python scripts/iceberg_maintenance.py --execute --days 14 --keep-snapshots 200

Configuration (2026-01-20):
- Snapshot retention: 7 days (configurable)
- Keep recent: 100 snapshots (configurable)
- Orphan file age: 3 days (safety margin)
- Tables maintained:
  * bronze_binance_trades
  * bronze_kraken_trades
  * silver_binance_trades
  * silver_kraken_trades
  * silver_dlq_trades

Related:
- Decision #013: Iceberg maintenance strategy
- Step 11: Silver transformation (writes to Iceberg)
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from k2.spark.utils.spark_session import create_spark_session


# Tables to maintain (add new tables here as needed)
TABLES_TO_MAINTAIN = [
    "iceberg.market_data.bronze_binance_trades",
    "iceberg.market_data.bronze_kraken_trades",
    "iceberg.market_data.silver_binance_trades",
    "iceberg.market_data.silver_kraken_trades",
    "iceberg.market_data.silver_dlq_trades",
]


def expire_snapshots(
    spark,
    table_name: str,
    older_than_days: int,
    retain_last: int,
    dry_run: bool = True
) -> dict:
    """Expire old snapshots from an Iceberg table.

    Args:
        spark: SparkSession
        table_name: Fully qualified table name (e.g., iceberg.market_data.bronze_binance_trades)
        older_than_days: Expire snapshots older than this many days
        retain_last: Minimum number of recent snapshots to retain
        dry_run: If True, only show what would be deleted

    Returns:
        dict with operation results
    """
    older_than_ts = datetime.now() - timedelta(days=older_than_days)
    older_than_str = older_than_ts.strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'=' * 70}")
    print(f"Table: {table_name}")
    print(f"Operation: Expire Snapshots")
    print(f"Policy: Older than {older_than_days} days, Keep last {retain_last}")
    print(f"Cutoff: {older_than_str}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print(f"{'=' * 70}")

    try:
        # Get current snapshot count
        snapshots_df = spark.sql(f"SELECT * FROM {table_name}.snapshots")
        current_count = snapshots_df.count()
        print(f"Current snapshots: {current_count}")

        if current_count <= retain_last:
            print(f"✓ Skipping: Only {current_count} snapshots (below minimum {retain_last})")
            return {
                "table": table_name,
                "operation": "expire_snapshots",
                "status": "skipped",
                "reason": f"below_minimum_{retain_last}",
                "snapshots_expired": 0
            }

        if not dry_run:
            # Execute snapshot expiration
            spark.sql(f"""
                CALL {table_name.split('.')[0]}.system.expire_snapshots(
                    table => '{table_name}',
                    older_than => TIMESTAMP '{older_than_str}',
                    retain_last => {retain_last}
                )
            """)
            new_count = spark.sql(f"SELECT * FROM {table_name}.snapshots").count()
            expired = current_count - new_count
            print(f"✓ Expired {expired} snapshots (retained {new_count})")

            return {
                "table": table_name,
                "operation": "expire_snapshots",
                "status": "success",
                "snapshots_before": current_count,
                "snapshots_after": new_count,
                "snapshots_expired": expired
            }
        else:
            # Dry run: estimate eligible snapshots
            eligible_df = spark.sql(f"""
                SELECT COUNT(*) as eligible
                FROM {table_name}.snapshots
                WHERE committed_at < TIMESTAMP '{older_than_str}'
            """)
            eligible = eligible_df.collect()[0]['eligible']
            would_expire = max(0, min(eligible, current_count - retain_last))
            print(f"DRY RUN: Would expire ~{would_expire} snapshots")

            return {
                "table": table_name,
                "operation": "expire_snapshots",
                "status": "dry_run",
                "snapshots_current": current_count,
                "snapshots_would_expire": would_expire
            }

    except Exception as e:
        print(f"✗ ERROR: {e}")
        return {
            "table": table_name,
            "operation": "expire_snapshots",
            "status": "error",
            "error": str(e)
        }


def remove_orphan_files(
    spark,
    table_name: str,
    older_than_days: int = 3,
    dry_run: bool = True
) -> dict:
    """Remove orphan data files from an Iceberg table.

    Orphan files are data files that are no longer referenced by any snapshot
    (e.g., from failed writes or expired snapshots).

    Args:
        spark: SparkSession
        table_name: Fully qualified table name
        older_than_days: Only remove files older than this (safety margin)
        dry_run: If True, only show what would be deleted

    Returns:
        dict with operation results
    """
    older_than_ts = datetime.now() - timedelta(days=older_than_days)
    older_than_str = older_than_ts.strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'=' * 70}")
    print(f"Table: {table_name}")
    print(f"Operation: Remove Orphan Files")
    print(f"Safety margin: {older_than_days} days")
    print(f"Cutoff: {older_than_str}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print(f"{'=' * 70}")

    try:
        if not dry_run:
            # Execute orphan file removal
            result_df = spark.sql(f"""
                CALL {table_name.split('.')[0]}.system.remove_orphan_files(
                    table => '{table_name}',
                    older_than => TIMESTAMP '{older_than_str}'
                )
            """)
            result = result_df.collect()[0]
            orphan_file_count = result['orphan_file_count'] if 'orphan_file_count' in result else 0
            print(f"✓ Removed {orphan_file_count} orphan files")

            return {
                "table": table_name,
                "operation": "remove_orphan_files",
                "status": "success",
                "orphan_files_removed": orphan_file_count
            }
        else:
            print("DRY RUN: Would scan for orphan files (no estimate available)")
            return {
                "table": table_name,
                "operation": "remove_orphan_files",
                "status": "dry_run"
            }

    except Exception as e:
        print(f"✗ ERROR: {e}")
        return {
            "table": table_name,
            "operation": "remove_orphan_files",
            "status": "error",
            "error": str(e)
        }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Iceberg table maintenance (snapshot expiration & orphan cleanup)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (shows what would be deleted)
  python scripts/iceberg_maintenance.py

  # Execute with default settings (7 days, keep 100 snapshots)
  python scripts/iceberg_maintenance.py --execute

  # Custom retention policy
  python scripts/iceberg_maintenance.py --execute --days 14 --keep-snapshots 200
        """
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute cleanup (default: dry run)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Expire snapshots older than N days (default: 7)"
    )
    parser.add_argument(
        "--keep-snapshots",
        type=int,
        default=100,
        help="Minimum recent snapshots to keep (default: 100)"
    )
    parser.add_argument(
        "--orphan-days",
        type=int,
        default=3,
        help="Only remove orphan files older than N days (default: 3)"
    )

    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("K2 Iceberg Table Maintenance")
    print("=" * 70)
    print(f"\nMode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    print(f"Snapshot retention: {args.days} days, keep last {args.keep_snapshots}")
    print(f"Orphan file safety margin: {args.orphan_days} days")
    print(f"Tables: {len(TABLES_TO_MAINTAIN)}")
    print(f"\n{'=' * 70}\n")

    if not args.execute:
        print("⚠️  DRY RUN MODE - No changes will be made")
        print("⚠️  Use --execute flag to perform actual cleanup\n")

    # Create Spark session
    spark = create_spark_session("K2-Iceberg-Maintenance")

    results = []

    try:
        for table_name in TABLES_TO_MAINTAIN:
            # Check if table exists
            try:
                spark.sql(f"DESCRIBE TABLE {table_name}").collect()
            except Exception:
                print(f"\n⚠️  Skipping {table_name} (table does not exist)")
                results.append({
                    "table": table_name,
                    "status": "skipped",
                    "reason": "table_not_found"
                })
                continue

            # Expire old snapshots
            snapshot_result = expire_snapshots(
                spark,
                table_name,
                older_than_days=args.days,
                retain_last=args.keep_snapshots,
                dry_run=not args.execute
            )
            results.append(snapshot_result)

            # Remove orphan files
            orphan_result = remove_orphan_files(
                spark,
                table_name,
                older_than_days=args.orphan_days,
                dry_run=not args.execute
            )
            results.append(orphan_result)

        # Summary
        print("\n" + "=" * 70)
        print("MAINTENANCE SUMMARY")
        print("=" * 70)

        success_count = len([r for r in results if r["status"] == "success"])
        error_count = len([r for r in results if r["status"] == "error"])
        skipped_count = len([r for r in results if r["status"] == "skipped"])
        dry_run_count = len([r for r in results if r["status"] == "dry_run"])

        if args.execute:
            print(f"✓ Successful operations: {success_count}")
            print(f"✗ Failed operations: {error_count}")
            print(f"⊘ Skipped operations: {skipped_count}")

            total_snapshots_expired = sum(
                r.get("snapshots_expired", 0)
                for r in results
                if r.get("operation") == "expire_snapshots"
            )
            total_orphans_removed = sum(
                r.get("orphan_files_removed", 0)
                for r in results
                if r.get("operation") == "remove_orphan_files"
            )

            print(f"\nTotal snapshots expired: {total_snapshots_expired}")
            print(f"Total orphan files removed: {total_orphans_removed}")
        else:
            print(f"DRY RUN operations: {dry_run_count}")
            print("\n⚠️  No changes were made (dry run mode)")
            print("⚠️  Use --execute flag to perform actual cleanup")

        print("=" * 70 + "\n")

        return 0 if error_count == 0 else 1

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        return 130
    except Exception as e:
        print(f"\n✗ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
