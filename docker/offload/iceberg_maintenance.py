#!/usr/bin/env python3
"""
K2 Market Data Platform - Iceberg Daily Maintenance
Purpose: File compaction, snapshot expiry, and warm-cold audit
Actions:
  compact  -- Merge small Parquet files (binpack, target 128 MB)
  expire   -- Remove snapshots older than retention window (default 7 days)
  audit    -- Compare ClickHouse vs Iceberg row counts for yesterday's UTC window
              and persist results to PostgreSQL maintenance_audit_log

Invoked By: iceberg_maintenance_flow.py (via docker exec k2-spark-iceberg)
Version: v1.0
Last Updated: 2026-02-18

Design:
  - Compact and expire are single-table operations; the Prefect flow calls
    docker exec once per table so each action gets its own Spark session.
  - Audit is a full-pipeline operation that opens one Spark session, queries
    all 10 ClickHouse + Iceberg tables, then writes a consolidated report to
    PostgreSQL. One session start-up cost for 10 tables is far cheaper than 10.
  - All env vars inherit from the spark-iceberg container (see docker-compose.v2.yml).
  - Exit code 0 = success, 1 = failure (matches offload_generic.py convention).
"""

from __future__ import annotations

import os
import sys
import argparse
import logging
import psycopg2
from datetime import datetime, timedelta, timezone
from pyspark.sql import SparkSession

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

ICEBERG_WAREHOUSE = "/home/iceberg/warehouse"

CLICKHOUSE_HOST = "clickhouse"
CLICKHOUSE_PORT = "8123"
CLICKHOUSE_DATABASE = "k2"
CLICKHOUSE_URL = (
    f"jdbc:clickhouse://{CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}/{CLICKHOUSE_DATABASE}"
)

# Defaults — overridable via CLI flags.
DEFAULT_TARGET_FILE_SIZE_MB = 128
DEFAULT_SNAPSHOT_MAX_AGE_HOURS = 168   # 7 days — matches DDL tblproperties
DEFAULT_SNAPSHOT_RETAIN_LAST = 3       # Never drop below 3 snapshots
DEFAULT_AUDIT_WINDOW_HOURS = 24        # "Yesterday" UTC window (run at 02:00 UTC)

# All 10 tables the audit compares.  Used only by the audit action; compaction
# and expiry receive --table from the Prefect flow so they stay table-agnostic.
_AUDIT_TABLE_CONFIG = [
    # Bronze ─────────────────────────────────────────────────────────────────
    {
        "iceberg_table": "cold.bronze_trades_binance",
        "ch_source": "bronze_trades_binance",
        "timestamp_col": "exchange_timestamp",
        "layer": "bronze",
    },
    {
        "iceberg_table": "cold.bronze_trades_kraken",
        "ch_source": "bronze_trades_kraken",
        "timestamp_col": "exchange_timestamp",
        "layer": "bronze",
    },
    {
        "iceberg_table": "cold.bronze_trades_coinbase",
        "ch_source": "bronze_trades_coinbase",
        "timestamp_col": "exchange_timestamp",
        "layer": "bronze",
    },
    # Silver ─────────────────────────────────────────────────────────────────
    {
        "iceberg_table": "cold.silver_trades",
        "ch_source": "silver_trades",
        "timestamp_col": "timestamp",
        "layer": "silver",
    },
    # Gold ───────────────────────────────────────────────────────────────────
    {
        "iceberg_table": "cold.gold_ohlcv_1m",
        "ch_source": "ohlcv_1m",
        "timestamp_col": "window_start",
        "layer": "gold",
    },
    {
        "iceberg_table": "cold.gold_ohlcv_5m",
        "ch_source": "ohlcv_5m",
        "timestamp_col": "window_start",
        "layer": "gold",
    },
    {
        "iceberg_table": "cold.gold_ohlcv_15m",
        "ch_source": "ohlcv_15m",
        "timestamp_col": "window_start",
        "layer": "gold",
    },
    {
        "iceberg_table": "cold.gold_ohlcv_30m",
        "ch_source": "ohlcv_30m",
        "timestamp_col": "window_start",
        "layer": "gold",
    },
    {
        "iceberg_table": "cold.gold_ohlcv_1h",
        "ch_source": "ohlcv_1h",
        "timestamp_col": "window_start",
        "layer": "gold",
    },
    {
        "iceberg_table": "cold.gold_ohlcv_1d",
        "ch_source": "ohlcv_1d",
        "timestamp_col": "window_start",
        "layer": "gold",
    },
]

# Tolerance: delta > WARNING_PCT_THRESHOLD triggers 'warning' status;
# > MISSING_DATA_THRESHOLD (AND iceberg < clickhouse) triggers 'missing_data'.
WARNING_PCT_THRESHOLD = 1.0
MISSING_DATA_PCT_THRESHOLD = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Spark Session Factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_spark(app_name: str) -> SparkSession:
    """
    Build a SparkSession with the same Hadoop/Iceberg catalog config used by
    offload_generic.py so that CALL statements and table references resolve
    identically.
    """
    spark = (
        SparkSession.builder
        .appName(app_name)
        .config("spark.sql.catalog.k2", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.k2.type", "hadoop")
        .config("spark.sql.catalog.k2.warehouse", ICEBERG_WAREHOUSE)
        .config("spark.sql.catalog.k2.io-impl", "org.apache.iceberg.hadoop.HadoopFileIO")
        .config("spark.sql.defaultCatalog", "k2")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    logger.info("✓ Spark session initialised")
    return spark


# ─────────────────────────────────────────────────────────────────────────────
# Action: compact
# ─────────────────────────────────────────────────────────────────────────────

def action_compact(table: str, target_file_size_mb: int, dry_run: bool) -> None:
    """
    Rewrite small data files in *table* using Iceberg's binpack strategy.

    binpack is chosen over sort/zorder because:
      - Append-only workloads (no updates/deletes) gain nothing from sort order
        that the partition spec doesn't already provide.
      - Simpler, faster, lower memory overhead than zorder.
      - The partition spec (days(exchange_timestamp) for bronze, months for gold)
        already provides the range-scan locality that matters for queries.

    partial-progress.enabled = true means Iceberg commits progress files in
    batches; a mid-run crash doesn't lose all compaction work.

    Args:
        table: Iceberg table identifier, e.g. 'cold.bronze_trades_binance'
        target_file_size_mb: Target file size in MB (default 128).
        dry_run: If True, log the plan but do not execute the CALL statement.
    """
    target_bytes = target_file_size_mb * 1024 * 1024

    logger.info("=" * 70)
    logger.info(f"COMPACT: {table}")
    logger.info(f"  Strategy      : binpack")
    logger.info(f"  Target size   : {target_file_size_mb} MB ({target_bytes:,} bytes)")
    logger.info(f"  Dry-run       : {dry_run}")
    logger.info("=" * 70)

    if dry_run:
        logger.info("DRY-RUN: skipping CALL rewrite_data_files")
        return

    spark = _build_spark(f"K2-Compact-{table.replace('.', '-')}")
    try:
        result = spark.sql(f"""
            CALL k2.system.rewrite_data_files(
                table              => '{table}',
                strategy           => 'binpack',
                options            => map(
                    'target-file-size-bytes',          '{target_bytes}',
                    'partial-progress.enabled',        'true',
                    'partial-progress.max-commits',    '10'
                )
            )
        """)
        row = result.first()

        rewritten = row["rewritten_data_files_count"]
        added     = row["added_data_files_count"]
        rewritten_bytes = row["rewritten_bytes_count"]
        failed    = row["failed_data_files_count"]

        logger.info(f"✓ Compaction complete for {table}")
        logger.info(f"  Files rewritten : {rewritten}")
        logger.info(f"  Files added     : {added}")
        logger.info(f"  Bytes rewritten : {rewritten_bytes:,}")
        logger.info(f"  Files failed    : {failed}")

        if failed > 0:
            logger.warning(f"  {failed} file(s) failed to compact — check Spark logs")

    except Exception as exc:
        logger.error(f"✗ Compaction failed for {table}: {exc}")
        raise
    finally:
        spark.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Action: expire
# ─────────────────────────────────────────────────────────────────────────────

def action_expire(
    table: str,
    max_age_hours: int,
    retain_last: int,
    dry_run: bool,
) -> None:
    """
    Expire Iceberg snapshots older than *max_age_hours*, keeping at least
    *retain_last* snapshots regardless of age.

    retain_last = 3 ensures the last three snapshots survive even if they
    are older than the retention window (protects against quiet tables).

    After expiry, unreferenced data/manifest/list files are deleted from
    the object store (MinIO). This is the primary mechanism for reclaiming
    disk space from old offload cycles.

    Args:
        table: Iceberg table identifier, e.g. 'cold.silver_trades'
        max_age_hours: Remove snapshots older than this (default 168 = 7 days).
        retain_last: Minimum number of snapshots to keep (default 3).
        dry_run: If True, log the plan but do not execute the CALL statement.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    logger.info("=" * 70)
    logger.info(f"EXPIRE: {table}")
    logger.info(f"  Cutoff        : {cutoff_str} UTC (older_than)")
    logger.info(f"  Retain last   : {retain_last} snapshots")
    logger.info(f"  Dry-run       : {dry_run}")
    logger.info("=" * 70)

    if dry_run:
        logger.info("DRY-RUN: skipping CALL expire_snapshots")
        return

    spark = _build_spark(f"K2-Expire-{table.replace('.', '-')}")
    try:
        result = spark.sql(f"""
            CALL k2.system.expire_snapshots(
                table        => '{table}',
                older_than   => TIMESTAMP '{cutoff_str}',
                retain_last  => {retain_last}
            )
        """)
        row = result.first()

        deleted_data      = row["deleted_data_files_count"]
        deleted_manifests = row["deleted_manifest_files_count"]
        deleted_lists     = row["deleted_manifest_lists_count"]

        logger.info(f"✓ Snapshot expiry complete for {table}")
        logger.info(f"  Data files deleted     : {deleted_data}")
        logger.info(f"  Manifest files deleted : {deleted_manifests}")
        logger.info(f"  Manifest lists deleted : {deleted_lists}")

    except Exception as exc:
        logger.error(f"✗ Snapshot expiry failed for {table}: {exc}")
        raise
    finally:
        spark.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Action: audit
# ─────────────────────────────────────────────────────────────────────────────

def _pg_connection() -> "psycopg2.connection":
    """Open a PostgreSQL connection using the standard PREFECT_DB_* env vars."""
    return psycopg2.connect(
        host=os.environ.get("PREFECT_DB_HOST", "prefect-db"),
        port=int(os.environ.get("PREFECT_DB_PORT", "5432")),
        database=os.environ["PREFECT_DB_NAME"],
        user=os.environ["PREFECT_DB_USER"],
        password=os.environ["PREFECT_DB_PASSWORD"],
    )


def _ensure_audit_table(conn) -> None:
    """
    Create maintenance_audit_log if it doesn't exist yet.

    Idempotent — safe to run on every audit cycle.
    The table lives in the same Prefect PostgreSQL DB as offload_watermarks,
    keeping all platform metadata in one place.
    """
    ddl = """
        CREATE TABLE IF NOT EXISTS maintenance_audit_log (
            id               SERIAL PRIMARY KEY,
            run_timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            iceberg_table    TEXT        NOT NULL,
            ch_source        TEXT        NOT NULL,
            layer            TEXT        NOT NULL,
            audit_window_hours INT       NOT NULL,
            window_start     TIMESTAMPTZ NOT NULL,
            window_end       TIMESTAMPTZ NOT NULL,
            ch_row_count     BIGINT      NOT NULL,
            iceberg_row_count BIGINT     NOT NULL,
            delta            BIGINT      NOT NULL,   -- iceberg - clickhouse
            delta_pct        DECIMAL(8, 4),          -- NULL if ch_count = 0
            status           TEXT        NOT NULL,   -- ok | warning | missing_data
            notes            TEXT
        )
    """
    with conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
    logger.info("✓ maintenance_audit_log table ready")


def _audit_status(ch_count: int, iceberg_count: int) -> tuple[str, float | None, str]:
    """
    Classify the comparison of ClickHouse vs Iceberg row counts.

    Returns:
        (status, delta_pct, notes)

    Status rules:
      ok           — abs(delta_pct) <= WARNING_PCT_THRESHOLD, or ch_count == 0
      warning      — WARNING_PCT_THRESHOLD < abs(delta_pct) <= MISSING_DATA_PCT_THRESHOLD
      missing_data — iceberg_count < ch_count AND abs(delta_pct) > MISSING_DATA_PCT_THRESHOLD
                     (data exists in ClickHouse but is absent from Iceberg)
    """
    if ch_count == 0:
        notes = "ClickHouse returned 0 rows for this window; nothing to compare."
        return "ok", None, notes

    delta = iceberg_count - ch_count
    delta_pct = (delta / ch_count) * 100.0

    if abs(delta_pct) <= WARNING_PCT_THRESHOLD:
        status = "ok"
        notes = f"Counts within {WARNING_PCT_THRESHOLD}% tolerance."
    elif iceberg_count < ch_count and abs(delta_pct) > MISSING_DATA_PCT_THRESHOLD:
        status = "missing_data"
        notes = (
            f"Iceberg is {abs(delta_pct):.2f}% below ClickHouse — "
            f"data may not have been offloaded for this window."
        )
    else:
        status = "warning"
        notes = (
            f"Delta {delta_pct:+.2f}% is outside the {WARNING_PCT_THRESHOLD}% tolerance. "
            f"Check offload logs for this window."
        )

    return status, round(delta_pct, 4), notes


def _count_clickhouse(spark: SparkSession, ch_source: str, timestamp_col: str,
                      window_start_str: str, window_end_str: str) -> int:
    """
    Count rows in ClickHouse for *ch_source* within the audit window.

    Uses the same JDBC driver as offload_generic.py.  The query is a minimal
    aggregate that transfers a single row across the network instead of the
    full dataset.
    """
    jdbc_query = (
        f"(SELECT count() AS cnt FROM {ch_source} "
        f"WHERE {timestamp_col} >= '{window_start_str}' "
        f"AND {timestamp_col} < '{window_end_str}') AS ch_audit"
    )
    df = spark.read.jdbc(
        url=CLICKHOUSE_URL,
        table=jdbc_query,
        properties={
            "driver": "com.clickhouse.jdbc.ClickHouseDriver",
            "user": "default",
            "password": os.environ["CLICKHOUSE_PASSWORD"],
        },
    )
    return int(df.first()["cnt"])


def _count_iceberg(spark: SparkSession, iceberg_table: str, timestamp_col: str,
                   window_start_str: str, window_end_str: str) -> int:
    """
    Count rows in an Iceberg table within the audit window.

    Spark Iceberg's predicate push-down means only the relevant partitions
    are scanned — the full table is never read into memory.
    """
    count = spark.sql(f"""
        SELECT count(*) AS cnt
        FROM {iceberg_table}
        WHERE {timestamp_col} >= TIMESTAMP '{window_start_str}'
          AND {timestamp_col} <  TIMESTAMP '{window_end_str}'
    """).first()["cnt"]
    return int(count)


def action_audit(audit_window_hours: int) -> None:
    """
    Compare ClickHouse vs Iceberg row counts for all 10 tables over the last
    *audit_window_hours* (default = 24).

    Window boundaries are truncated to whole UTC hours so that consecutive
    daily runs produce non-overlapping, comparable windows.  Running at
    02:00 UTC means the window covers the previous calendar day
    (e.g., 2026-02-17 02:00 UTC → 2026-02-18 02:00 UTC).

    Results are written to PostgreSQL maintenance_audit_log and printed as a
    summary table to stdout (captured by Prefect logs).

    Exit code 0 even if individual tables show 'warning' or 'missing_data' —
    the Prefect flow decides whether to raise an alert based on the summary
    counts it parses from stdout.
    """
    now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    window_end = now_utc
    window_start = window_end - timedelta(hours=audit_window_hours)

    window_start_str = window_start.strftime("%Y-%m-%d %H:%M:%S")
    window_end_str   = window_end.strftime("%Y-%m-%d %H:%M:%S")

    logger.info("=" * 70)
    logger.info("AUDIT: Warm-Cold Consistency Check")
    logger.info(f"  Window : {window_start_str} UTC → {window_end_str} UTC")
    logger.info(f"  Tables : {len(_AUDIT_TABLE_CONFIG)}")
    logger.info("=" * 70)

    spark = _build_spark("K2-Audit-WarmCold")
    conn = _pg_connection()
    _ensure_audit_table(conn)

    run_timestamp = datetime.now(timezone.utc)
    results = []

    try:
        for cfg in _AUDIT_TABLE_CONFIG:
            iceberg_table = cfg["iceberg_table"]
            ch_source     = cfg["ch_source"]
            timestamp_col = cfg["timestamp_col"]
            layer         = cfg["layer"]

            logger.info(f"  Auditing {iceberg_table} ...")
            try:
                ch_count      = _count_clickhouse(spark, ch_source, timestamp_col,
                                                  window_start_str, window_end_str)
                iceberg_count = _count_iceberg(spark, iceberg_table, timestamp_col,
                                               window_start_str, window_end_str)
                delta         = iceberg_count - ch_count
                status, delta_pct, notes = _audit_status(ch_count, iceberg_count)

                results.append({
                    "iceberg_table": iceberg_table,
                    "ch_source": ch_source,
                    "layer": layer,
                    "ch_count": ch_count,
                    "iceberg_count": iceberg_count,
                    "delta": delta,
                    "delta_pct": delta_pct,
                    "status": status,
                    "notes": notes,
                    "error": None,
                })
                logger.info(
                    f"    CH={ch_count:>10,}  Iceberg={iceberg_count:>10,}  "
                    f"Δ={delta:>+10,}  [{status.upper()}]"
                )

            except Exception as exc:
                logger.error(f"    ✗ Audit failed for {iceberg_table}: {exc}")
                results.append({
                    "iceberg_table": iceberg_table,
                    "ch_source": ch_source,
                    "layer": layer,
                    "ch_count": 0,
                    "iceberg_count": 0,
                    "delta": 0,
                    "delta_pct": None,
                    "status": "error",
                    "notes": str(exc)[:500],
                    "error": str(exc),
                })

        # ── Write all results to PostgreSQL ──────────────────────────────────
        with conn:
            with conn.cursor() as cur:
                for r in results:
                    cur.execute(
                        """
                        INSERT INTO maintenance_audit_log (
                            run_timestamp, iceberg_table, ch_source, layer,
                            audit_window_hours, window_start, window_end,
                            ch_row_count, iceberg_row_count, delta, delta_pct,
                            status, notes
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            run_timestamp,
                            r["iceberg_table"],
                            r["ch_source"],
                            r["layer"],
                            audit_window_hours,
                            window_start,
                            window_end,
                            r["ch_count"],
                            r["iceberg_count"],
                            r["delta"],
                            r["delta_pct"],
                            r["status"],
                            r["notes"],
                        ),
                    )
        logger.info("✓ Audit results written to maintenance_audit_log")

    finally:
        conn.close()
        spark.stop()

    # ── Summary table ─────────────────────────────────────────────────────────
    ok_count      = sum(1 for r in results if r["status"] == "ok")
    warning_count = sum(1 for r in results if r["status"] == "warning")
    missing_count = sum(1 for r in results if r["status"] == "missing_data")
    error_count   = sum(1 for r in results if r["status"] == "error")

    print("\n" + "=" * 70)
    print("AUDIT SUMMARY")
    print(f"  Window : {window_start_str} → {window_end_str} UTC")
    print(f"  {'Table':<40}  {'CH':>10}  {'Iceberg':>10}  {'Status':<14}")
    print("  " + "-" * 66)
    for r in results:
        label = r["iceberg_table"].replace("cold.", "")
        status_tag = r["status"].upper()
        print(
            f"  {label:<40}  {r['ch_count']:>10,}  "
            f"{r['iceberg_count']:>10,}  [{status_tag}]"
        )
    print("  " + "-" * 66)
    print(f"  OK={ok_count}  WARNING={warning_count}  MISSING={missing_count}  ERROR={error_count}")
    print("=" * 70 + "\n")

    if missing_count > 0 or error_count > 0:
        logger.warning(
            f"Audit completed with issues: "
            f"missing_data={missing_count}, errors={error_count}. "
            "Review maintenance_audit_log for details."
        )
    else:
        logger.info("✓ Audit completed cleanly — all tables within tolerance.")


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="K2 Iceberg daily maintenance (compact | expire | audit)"
    )
    sub = parser.add_subparsers(dest="action", required=True)

    # compact ─────────────────────────────────────────────────────────────────
    p_compact = sub.add_parser("compact", help="Rewrite small data files (binpack)")
    p_compact.add_argument(
        "--table", required=True,
        help="Iceberg table identifier (e.g. cold.bronze_trades_binance)",
    )
    p_compact.add_argument(
        "--target-file-size-mb", type=int, default=DEFAULT_TARGET_FILE_SIZE_MB,
        help=f"Target Parquet file size in MB (default: {DEFAULT_TARGET_FILE_SIZE_MB})",
    )
    p_compact.add_argument(
        "--dry-run", action="store_true",
        help="Log the plan without executing any Iceberg operations",
    )

    # expire ──────────────────────────────────────────────────────────────────
    p_expire = sub.add_parser("expire", help="Remove old snapshots and orphaned files")
    p_expire.add_argument(
        "--table", required=True,
        help="Iceberg table identifier (e.g. cold.silver_trades)",
    )
    p_expire.add_argument(
        "--max-age-hours", type=int, default=DEFAULT_SNAPSHOT_MAX_AGE_HOURS,
        help=f"Remove snapshots older than N hours (default: {DEFAULT_SNAPSHOT_MAX_AGE_HOURS} = 7 days)",
    )
    p_expire.add_argument(
        "--retain-last", type=int, default=DEFAULT_SNAPSHOT_RETAIN_LAST,
        help=f"Always keep at least N most recent snapshots (default: {DEFAULT_SNAPSHOT_RETAIN_LAST})",
    )
    p_expire.add_argument(
        "--dry-run", action="store_true",
        help="Log the plan without executing any Iceberg operations",
    )

    # audit ───────────────────────────────────────────────────────────────────
    p_audit = sub.add_parser("audit", help="Compare ClickHouse vs Iceberg row counts")
    p_audit.add_argument(
        "--audit-window-hours", type=int, default=DEFAULT_AUDIT_WINDOW_HOURS,
        help=f"Audit window in hours, ending now (default: {DEFAULT_AUDIT_WINDOW_HOURS})",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.action == "compact":
            action_compact(
                table=args.table,
                target_file_size_mb=args.target_file_size_mb,
                dry_run=args.dry_run,
            )
        elif args.action == "expire":
            action_expire(
                table=args.table,
                max_age_hours=args.max_age_hours,
                retain_last=args.retain_last,
                dry_run=args.dry_run,
            )
        elif args.action == "audit":
            action_audit(audit_window_hours=args.audit_window_hours)
        else:
            parser.error(f"Unknown action: {args.action}")

        sys.exit(0)

    except Exception as exc:
        logger.error(f"Maintenance job failed ({args.action}): {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
