"""
K2 Market Data Platform - Prefect Iceberg Offload Flow
Purpose: Orchestrate ClickHouse → Iceberg offload for all tables
Frequency: Every 15 minutes
Architecture: Bronze (parallel) → Silver → Gold (parallel)
Version: v3.0 (Prefect 3.x migration)
Last Updated: 2026-02-14
"""

import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List
import subprocess

from prefect import flow, task
from prefect.task_runners import ConcurrentTaskRunner

# Import Prometheus metrics (optional - graceful degradation)
try:
    sys.path.insert(0, '/opt/prefect/offload')
    from metrics import (
        record_offload_success,
        record_offload_failure,
        record_cycle_complete,
        set_configured_tables,
        set_pipeline_info,
        start_metrics_server,
    )
    METRICS_ENABLED = True
except ImportError:
    METRICS_ENABLED = False
    print("Warning: Prometheus metrics module not available")

# Add offload scripts to Python path
sys.path.append(str(Path(__file__).parent.parent))

from watermark_pg import WatermarkManager


# ============================================================================
# Configuration
# ============================================================================

# Table Configuration: All ClickHouse tables to offload
TABLE_CONFIG = {
    "bronze": [
        {
            "source": "bronze_trades_binance",
            "target": "cold.bronze_trades_binance",
            "timestamp_col": "exchange_timestamp",
            "sequence_col": "sequence_number",
        },
        {
            "source": "bronze_trades_kraken",
            "target": "cold.bronze_trades_kraken",
            "timestamp_col": "exchange_timestamp",
            "sequence_col": "sequence_number",
        },
        {
            "source": "bronze_trades_coinbase",
            "target": "cold.bronze_trades_coinbase",
            "timestamp_col": "exchange_timestamp",
            "sequence_col": "sequence_num",  # Coinbase field name differs from binance/kraken
        },
    ],
    "silver": [
        {
            "source": "silver_trades",
            "target": "cold.silver_trades",
            "timestamp_col": "timestamp",
            "sequence_col": "platform_sequence",
        },
    ],
    "gold": [
        {
            "source": "ohlcv_1m",
            "target": "cold.gold_ohlcv_1m",
            "timestamp_col": "window_start",
            "sequence_col": "window_start",  # No sequence for OHLCV, use timestamp
        },
        {
            "source": "ohlcv_5m",
            "target": "cold.gold_ohlcv_5m",
            "timestamp_col": "window_start",
            "sequence_col": "window_start",
        },
        {
            "source": "ohlcv_15m",
            "target": "cold.gold_ohlcv_15m",
            "timestamp_col": "window_start",
            "sequence_col": "window_start",
        },
        {
            "source": "ohlcv_30m",
            "target": "cold.gold_ohlcv_30m",
            "timestamp_col": "window_start",
            "sequence_col": "window_start",
        },
        {
            "source": "ohlcv_1h",
            "target": "cold.gold_ohlcv_1h",
            "timestamp_col": "window_start",
            "sequence_col": "window_start",
        },
        {
            "source": "ohlcv_1d",
            "target": "cold.gold_ohlcv_1d",
            "timestamp_col": "window_start",
            "sequence_col": "window_start",
        },
    ],
}

# Spark Configuration
SPARK_SCRIPT_DIR = Path(__file__).parent.parent
SPARK_SUBMIT_CMD = "/opt/spark/bin/spark-submit"


# ============================================================================
# Prefect Tasks
# ============================================================================

@task(
    name="offload-table",
    description="Offload single table from ClickHouse to Iceberg",
    retries=2,
    retry_delay_seconds=30,
    tags=["iceberg", "offload"],
    log_prints=True,
)
def offload_table(
    source_table: str,
    target_table: str,
    timestamp_col: str,
    sequence_col: str,
    layer: str
) -> Dict[str, any]:
    """
    Offload a single table from ClickHouse to Iceberg.

    Args:
        source_table: ClickHouse source table name
        target_table: Iceberg target table name
        timestamp_col: Timestamp column for incremental reads
        sequence_col: Sequence column for ordering
        layer: Data layer (bronze, silver, gold)

    Returns:
        Dict with execution metrics (rows_written, duration_seconds, etc.)

    Raises:
        subprocess.CalledProcessError: If PySpark job fails
    """
    from prefect import get_run_logger
    logger = get_run_logger()

    logger.info(f"Starting offload: {source_table} → {target_table}")
    start_time = datetime.now()

    # Simplified command - uses the existing offload_generic.py script
    cmd = [
        "docker", "exec", "k2-spark-iceberg",
        "python3", "/home/iceberg/offload/offload_generic.py",
        "--source-table", source_table,
        "--target-table", target_table,
        "--timestamp-col", timestamp_col,
        "--sequence-col", sequence_col,
        "--layer", layer
    ]

    try:
        # Execute PySpark job via docker exec
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=600  # 10-minute timeout
        )

        duration = (datetime.now() - start_time).total_seconds()

        # Parse rows from output
        rows_written = 0
        for line in result.stdout.split('\n'):
            if "Rows offloaded:" in line:
                try:
                    rows_written = int(line.split(':')[1].strip().replace(',', ''))
                except Exception:
                    pass

        logger.info(f"✓ Offload completed: {source_table}")
        logger.info(f"  Duration: {duration:.2f}s")
        logger.info(f"  Rows: {rows_written:,}")

        # Record Prometheus metrics
        if METRICS_ENABLED:
            record_offload_success(
                table=source_table,
                layer=layer,
                rows=rows_written,
                duration=duration
            )

        return {
            "table": source_table,
            "status": "success",
            "rows": rows_written,
            "duration_seconds": duration,
            "timestamp": datetime.now().isoformat(),
        }

    except subprocess.CalledProcessError as e:
        duration = (datetime.now() - start_time).total_seconds()

        logger.error(f"✗ Offload failed: {source_table}")
        logger.error(f"  Exit code: {e.returncode}")
        logger.error(f"  Stderr: {e.stderr}")

        # Record Prometheus metrics
        if METRICS_ENABLED:
            record_offload_failure(
                table=source_table,
                layer=layer,
                error_type="process_error",
                duration=duration
            )

        raise RuntimeError(f"Offload failed for {source_table}: {e.stderr}")

    except subprocess.TimeoutExpired as e:
        duration = (datetime.now() - start_time).total_seconds()
        logger.error(f"✗ Offload timeout: {source_table} (exceeded 10 minutes)")

        # Record Prometheus metrics
        if METRICS_ENABLED:
            record_offload_failure(
                table=source_table,
                layer=layer,
                error_type="timeout",
                duration=duration
            )

        raise


@task(
    name="check-watermark",
    description="Check if table has new data to offload",
    tags=["iceberg", "watermark"]
)
def check_watermark(source_table: str) -> bool:
    """
    Check if table has new data since last watermark.

    Args:
        source_table: ClickHouse source table name

    Returns:
        True if new data available, False otherwise
    """
    from prefect import get_run_logger
    logger = get_run_logger()

    # For now, always return True (let PySpark job handle empty windows)
    # Future enhancement: Query ClickHouse to check for new data first
    logger.info(f"Checking watermark for {source_table} (always proceeding)")
    return True


# ============================================================================
# Prefect Flows
# ============================================================================

@flow(
    name="offload-bronze-layer",
    description="Offload all Bronze tables in parallel",
    task_runner=ConcurrentTaskRunner(),
    retries=1,
    retry_delay_seconds=300,
    log_prints=True,
)
def offload_bronze_layer() -> List[Dict]:
    """
    Offload all Bronze tables from ClickHouse to Iceberg in parallel.

    Returns:
        List of execution results for each table
    """
    from prefect import get_run_logger
    logger = get_run_logger()

    logger.info("=" * 80)
    logger.info("Starting Bronze Layer Offload (3 tables in parallel)")
    logger.info("=" * 80)

    results = []
    for table_config in TABLE_CONFIG["bronze"]:
        # Submit tasks - Prefect handles parallel execution
        result = offload_table(
            source_table=table_config["source"],
            target_table=table_config["target"],
            timestamp_col=table_config["timestamp_col"],
            sequence_col=table_config["sequence_col"],
            layer="bronze"
        )
        results.append(result)

    logger.info(f"✓ Bronze layer offload complete ({len(results)} tables)")
    return results


@flow(
    name="offload-silver-layer",
    description="Offload Silver table (depends on Bronze)",
    retries=1,
    retry_delay_seconds=300,
)
def offload_silver_layer() -> List[Dict]:
    """
    Offload Silver table from ClickHouse to Iceberg.

    Returns:
        List of execution results
    """
    from prefect import get_run_logger
    logger = get_run_logger()

    logger.info("=" * 80)
    logger.info("Starting Silver Layer Offload (1 table)")
    logger.info("=" * 80)

    results = []
    for table_config in TABLE_CONFIG["silver"]:
        result = offload_table(
            source_table=table_config["source"],
            target_table=table_config["target"],
            timestamp_col=table_config["timestamp_col"],
            sequence_col=table_config["sequence_col"],
            layer="silver"
        )
        results.append(result)

    logger.info(f"✓ Silver layer offload complete")
    return results


@flow(
    name="offload-gold-layer",
    description="Offload all Gold OHLCV tables in parallel (depends on Silver)",
    task_runner=ConcurrentTaskRunner(),
    retries=1,
    retry_delay_seconds=300,
)
def offload_gold_layer() -> List[Dict]:
    """
    Offload all Gold OHLCV tables from ClickHouse to Iceberg in parallel.

    Returns:
        List of execution results for each table
    """
    from prefect import get_run_logger
    logger = get_run_logger()

    logger.info("=" * 80)
    logger.info("Starting Gold Layer Offload (6 OHLCV tables in parallel)")
    logger.info("=" * 80)

    results = []
    for table_config in TABLE_CONFIG["gold"]:
        result = offload_table(
            source_table=table_config["source"],
            target_table=table_config["target"],
            timestamp_col=table_config["timestamp_col"],
            sequence_col=table_config["sequence_col"],
            layer="gold"
        )
        results.append(result)

    logger.info(f"✓ Gold layer offload complete ({len(results)} tables)")
    return results


@flow(
    name="iceberg-offload-main",
    description="Main orchestration: Bronze layer offload pipeline (Phase 5 MVP)",
    version="3.0.0",
    log_prints=True,
)
def iceberg_offload_main() -> Dict[str, any]:
    """
    Main orchestration flow for ClickHouse → Iceberg offload.

    Phase 5 MVP: Bronze layer only (2 tables)
    Future: Will expand to Silver → Gold when those layers are populated

    Execution Order:
    1. Bronze layer (2 tables in parallel: binance + kraken)

    Returns:
        Dict with summary metrics
    """
    from prefect import get_run_logger
    logger = get_run_logger()

    start_time = datetime.now()

    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " " * 20 + "K2 ICEBERG OFFLOAD PIPELINE" + " " * 31 + "║")
    logger.info("║" + " " * 11 + "ClickHouse → Iceberg (Bronze Layer, 3 exchanges)" + " " * 19 + "║")
    logger.info("╚" + "=" * 78 + "╝")
    logger.info("")

    # Step 1: Offload Bronze layer (parallel)
    bronze_results = offload_bronze_layer()

    # TODO: Add Silver and Gold layers when they are populated in ClickHouse
    # silver_results = offload_silver_layer()
    # gold_results = offload_gold_layer()

    # Summary
    total_duration = (datetime.now() - start_time).total_seconds()
    total_tables = len(bronze_results)

    # Calculate totals
    successful = sum(1 for r in bronze_results if r["status"] == "success")
    failed = len(bronze_results) - successful
    total_rows = sum(r.get("rows", 0) for r in bronze_results if r["status"] == "success")

    logger.info("")
    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " " * 30 + "OFFLOAD COMPLETE" + " " * 32 + "║")
    logger.info("╚" + "=" * 78 + "╝")
    logger.info(f"  Total tables offloaded: {successful}/{total_tables}")
    logger.info(f"  Total rows: {total_rows:,}")
    logger.info(f"  Total duration: {total_duration:.2f}s")
    logger.info(f"  Bronze: {len(bronze_results)} tables")
    logger.info("")

    # Record cycle metrics
    if METRICS_ENABLED:
        status = "success" if failed == 0 else "partial" if successful > 0 else "failed"
        record_cycle_complete(
            status=status,
            duration=total_duration,
            tables_processed=total_tables,
            successful=successful,
            failed=failed,
            total_rows=total_rows
        )

    return {
        "total_tables": total_tables,
        "successful": successful,
        "failed": failed,
        "total_rows": total_rows,
        "total_duration_seconds": total_duration,
        "bronze_results": bronze_results,
        "timestamp": start_time.isoformat(),
    }


# ============================================================================
# Deployment Configuration (Prefect 3.x)
# ============================================================================

if __name__ == "__main__":
    # For local testing - run the flow once
    print("Running iceberg offload flow (local test mode)...")

    # Start Prometheus metrics server if running standalone
    if METRICS_ENABLED:
        if start_metrics_server(port=8000):
            print("Prometheus metrics enabled on port 8000")
            set_configured_tables(len(TABLE_CONFIG["bronze"]))
            table_names = [t["source"] for t in TABLE_CONFIG["bronze"]]
            set_pipeline_info(
                version="3.0.0",
                schedule_minutes=15,
                tables=table_names
            )

    # Run flow
    iceberg_offload_main()


# ============================================================================
# Design Notes
# ============================================================================

# Orchestration Pattern:
# ──────────────────────
# Bronze (parallel):  binance + kraken (independent, no dependencies)
# Silver (sequential): unified_trades (depends on Bronze completion)
# Gold (parallel):    ohlcv_* (6 tables, depends on Silver completion)
#
# Rationale:
# - Bronze tables are independent (per-exchange data)
# - Silver aggregates Bronze (must wait for Bronze)
# - Gold aggregates Silver (must wait for Silver)

# Retry Strategy:
# ───────────────
# Task level: 3 retries with 60s delay (transient failures)
# Flow level: 1 retry with 300s delay (upstream dependency failures)
#
# Total: Up to 4 attempts per task (1 initial + 3 retries)

# Parallelism:
# ────────────
# Bronze: 2 concurrent tasks (binance || kraken)
# Gold: 6 concurrent tasks (all OHLCV timeframes)
#
# Resource impact: 2-6 concurrent Spark jobs
# Peak memory: 2-6 * 500 MB = 1-3 GB

# Monitoring:
# ───────────
# Prefect UI: http://localhost:4200
# - View flow runs, task status, logs
# - Check retry history, failure reasons
# - Monitor execution duration trends

# Exactly-Once Semantics:
# ────────────────────────
# Prefect retries are safe because:
# 1. Watermark prevents duplicate reads
# 2. Iceberg atomic commits prevent partial writes
# 3. Watermark updated only after successful commit
# 4. Failed tasks re-run from last watermark (idempotent)
