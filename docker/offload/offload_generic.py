#!/usr/bin/env python3
"""
K2 Market Data Platform - Generic Table Offload Script
Purpose: Parameterized PySpark job for offloading any ClickHouse table to Iceberg
Invoked By: Prefect flow (iceberg_offload_flow.py)
Version: v2.0 (ADR-014)
Last Updated: 2026-02-11
"""

import os
import sys
import argparse
import logging
from datetime import datetime
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, max as spark_max

# Add watermark utilities to path
sys.path.append('/home/iceberg/offload')
from watermark_pg import WatermarkManager, create_incremental_query

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

# ClickHouse Connection
CLICKHOUSE_HOST = "clickhouse"
CLICKHOUSE_PORT = "8123"
CLICKHOUSE_DATABASE = "k2"  # Updated from 'default' to 'k2' (production database)
CLICKHOUSE_URL = f"jdbc:clickhouse://{CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}/{CLICKHOUSE_DATABASE}"

# Iceberg Catalog Configuration (Hadoop catalog from ADR-013)
ICEBERG_WAREHOUSE = "/home/iceberg/warehouse"

# Offload Configuration
BUFFER_MINUTES = 5  # Safety buffer to avoid TTL race + late arrivals


# ============================================================================
# Main Offload Logic
# ============================================================================

def run_generic_offload(
    source_table: str,
    target_table: str,
    timestamp_col: str,
    sequence_col: str,
    layer: str,
    columns: str = "*"
):
    """
    Generic offload function for any ClickHouse table → Iceberg table.

    Args:
        source_table: ClickHouse source table name
        target_table: Iceberg target table name (with catalog prefix, e.g., cold.bronze_trades_binance)
        timestamp_col: Timestamp column for incremental reads
        sequence_col: Sequence column for ordering/deduplication
        layer: Data layer (bronze, silver, gold) - for logging only
        columns: Explicit comma-separated column list or "*". Prefer explicit list
                 to avoid schema drift between ClickHouse and Iceberg.
    """
    start_time = datetime.now()
    logger.info("=" * 80)
    logger.info(f"Generic Offload Job")
    logger.info(f"Source: {source_table} (ClickHouse)")
    logger.info(f"Target: {target_table} (Iceberg)")
    logger.info(f"Layer: {layer}")
    logger.info(f"Timestamp: {start_time}")
    logger.info("=" * 80)

    # ────────────────────────────────────────────────────────────────────────
    # Step 1: Initialize Spark with Iceberg + ClickHouse JDBC
    # ────────────────────────────────────────────────────────────────────────

    try:
        spark = SparkSession.builder \
            .appName(f"K2-Offload-{source_table}") \
            .config("spark.sql.catalog.k2", "org.apache.iceberg.spark.SparkCatalog") \
            .config("spark.sql.catalog.k2.type", "hadoop") \
            .config("spark.sql.catalog.k2.warehouse", ICEBERG_WAREHOUSE) \
            .config("spark.sql.catalog.k2.io-impl", "org.apache.iceberg.hadoop.HadoopFileIO") \
            .config("spark.sql.defaultCatalog", "k2") \
            .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions") \
            .getOrCreate()
        # spark.jars.packages removed: Iceberg runtime is pre-installed in the tabulario/spark-iceberg
        # base image (1.4.2) and the ClickHouse JDBC driver is baked into docker/spark/Dockerfile.

        logger.info("✓ Spark session initialized")

    except Exception as e:
        logger.error(f"Failed to initialize Spark: {e}")
        sys.exit(1)

    # ────────────────────────────────────────────────────────────────────────
    # Step 2: Get Last Watermark (PostgreSQL)
    # ────────────────────────────────────────────────────────────────────────

    wm = WatermarkManager(
        pg_host=os.environ.get("PREFECT_DB_HOST", "prefect-db"),
        pg_port=int(os.environ.get("PREFECT_DB_PORT", "5432")),
        pg_database=os.environ["PREFECT_DB_NAME"],
        pg_user=os.environ["PREFECT_DB_USER"],
        pg_password=os.environ["PREFECT_DB_PASSWORD"]
    )

    try:
        # Recover any stale 'running' status from a previous run that was killed.
        # Must happen BEFORE mark_offload_running so monitoring doesn't see a
        # false-positive 'running' row from a dead process.
        wm.recover_stale_running(source_table)
        wm.mark_offload_running(source_table)
        last_timestamp, last_sequence = wm.get_watermark(source_table)
        logger.info(f"✓ Watermark: timestamp={last_timestamp}, sequence={last_sequence}")

    except Exception as e:
        logger.error(f"Failed to get watermark: {e}")
        wm.mark_offload_failed(source_table, str(e))
        wm.close()
        spark.stop()
        sys.exit(1)

    # ────────────────────────────────────────────────────────────────────────
    # Step 3: Calculate Incremental Time Window
    # ────────────────────────────────────────────────────────────────────────

    window_start, window_end = wm.get_incremental_window(
        last_watermark=last_timestamp,
        buffer_minutes=BUFFER_MINUTES
    )

    if window_end <= window_start:
        logger.info("No new data to offload (empty time window). Exiting cleanly.")
        spark.stop()
        sys.exit(0)

    logger.info(f"✓ Incremental window: {window_start} to {window_end}")

    # ────────────────────────────────────────────────────────────────────────
    # Step 4: Read Incremental Data from ClickHouse
    # ────────────────────────────────────────────────────────────────────────

    try:
        incremental_query = create_incremental_query(
            table_name=source_table,
            timestamp_column=timestamp_col,
            sequence_column=sequence_col,
            start_time=window_start,
            end_time=window_end,
            columns=columns
        )

        logger.info(f"Reading incremental data from ClickHouse...")

        # Cache the DataFrame so ClickHouse is scanned exactly once.
        # Without cache: count() + max() + writeTo() each re-execute the JDBC read
        # (3 full network fetches). With cache: one scan fills Spark memory/disk;
        # all subsequent actions (max, write, count) read from cache.
        clickhouse_df = spark.read.jdbc(
            url=CLICKHOUSE_URL,
            table=incremental_query,
            properties={
                "driver": "com.clickhouse.jdbc.ClickHouseDriver",
                "user": "default",
                "password": os.environ["CLICKHOUSE_PASSWORD"]
            }
        ).cache()

        # Use max() as the empty-check — one action fills the cache and gives us
        # the watermark values in a single pass. A separate count() before write
        # would scan ClickHouse a second time unnecessarily.
        max_row = clickhouse_df.select(
            spark_max(col(timestamp_col)).alias("max_timestamp"),
            spark_max(col(sequence_col)).alias("max_sequence")
        ).first()

        if max_row['max_timestamp'] is None:
            logger.info("No new rows to offload. Exiting cleanly.")
            clickhouse_df.unpersist()
            wm.close()
            spark.stop()
            sys.exit(0)

        max_timestamp = max_row['max_timestamp']
        max_sequence = max_row['max_sequence']

        logger.info(f"✓ Max timestamp: {max_timestamp}, Max sequence: {max_sequence}")

    except Exception as e:
        logger.error(f"Failed to read from ClickHouse: {e}")
        wm.mark_offload_failed(source_table, str(e))
        wm.close()
        spark.stop()
        sys.exit(1)

    # ────────────────────────────────────────────────────────────────────────
    # Step 5: Write to Iceberg (Atomic Commit)
    # ────────────────────────────────────────────────────────────────────────

    try:
        logger.info(f"Writing rows to Iceberg: {target_table}")

        # Append to Iceberg table (atomic commit).
        # clickhouse_df is still cached from Step 4; no second ClickHouse scan.
        clickhouse_df.writeTo(target_table) \
            .using("iceberg") \
            .option("write-format", "parquet") \
            .option("compression-codec", "zstd") \
            .option("compression-level", "3") \
            .append()

        # count() from cache — O(1), no re-scan of ClickHouse
        row_count = clickhouse_df.count()
        clickhouse_df.unpersist()

        logger.info(f"✓ Successfully wrote {row_count:,} rows to Iceberg")

    except Exception as e:
        logger.error(f"Failed to write to Iceberg: {e}")
        clickhouse_df.unpersist()
        wm.mark_offload_failed(source_table, str(e))
        wm.close()
        spark.stop()
        sys.exit(1)

    # ────────────────────────────────────────────────────────────────────────
    # Step 6: Update Watermark (Only After Successful Commit)
    # ────────────────────────────────────────────────────────────────────────

    try:
        end_time = datetime.now()
        duration_seconds = int((end_time - start_time).total_seconds())

        wm.update_watermark(
            table_name=source_table,
            max_timestamp=max_timestamp,
            max_sequence=max_sequence,
            row_count=row_count,
            duration_seconds=duration_seconds
        )

        logger.info(f"✓ Watermark updated successfully")

    except Exception as e:
        logger.error(f"Failed to update watermark: {e}")
        logger.warning("Data written to Iceberg but watermark update failed. "
                       "Next run will re-read same data (Iceberg will deduplicate).")

    # ────────────────────────────────────────────────────────────────────────
    # Step 7: Cleanup and Summary
    # ────────────────────────────────────────────────────────────────────────

    wm.close()
    spark.stop()

    logger.info("=" * 80)
    logger.info("Offload job completed successfully")
    logger.info(f"Rows offloaded: {row_count:,}")
    logger.info(f"Duration: {duration_seconds}s")
    logger.info(f"Table: {source_table} → {target_table}")
    logger.info("=" * 80)


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    """Parse CLI arguments and run offload job."""
    parser = argparse.ArgumentParser(
        description="Generic ClickHouse → Iceberg offload job"
    )

    parser.add_argument(
        "--source-table",
        required=True,
        help="ClickHouse source table name (e.g., bronze_trades_binance)"
    )
    parser.add_argument(
        "--target-table",
        required=True,
        help="Iceberg target table name with catalog (e.g., cold.bronze_trades_binance)"
    )
    parser.add_argument(
        "--timestamp-col",
        required=True,
        help="Timestamp column for incremental reads (e.g., exchange_timestamp)"
    )
    parser.add_argument(
        "--sequence-col",
        required=True,
        help="Sequence column for ordering (e.g., sequence_number)"
    )
    parser.add_argument(
        "--layer",
        required=True,
        choices=["bronze", "silver", "gold"],
        help="Data layer (for logging/monitoring)"
    )
    parser.add_argument(
        "--columns",
        default="*",
        help="Explicit comma-separated column list for the ClickHouse SELECT. "
             "Use '*' (default) only when the Iceberg target schema matches ClickHouse exactly. "
             "Prefer an explicit list to prevent schema drift from silently flowing into Iceberg."
    )

    args = parser.parse_args()

    try:
        run_generic_offload(
            source_table=args.source_table,
            target_table=args.target_table,
            timestamp_col=args.timestamp_col,
            sequence_col=args.sequence_col,
            layer=args.layer,
            columns=args.columns
        )
        sys.exit(0)

    except Exception as e:
        logger.error(f"Unhandled exception: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
