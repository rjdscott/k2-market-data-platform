"""
K2 Market Data Platform - Prefect Iceberg Offload Flow
Purpose: Orchestrate ClickHouse → Iceberg offload for all tables
Frequency: Every 15 minutes
Architecture: Bronze (parallel) → Silver → Gold (parallel)
Version: v2.0 (ADR-014)
Last Updated: 2026-02-11
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List
import subprocess

from prefect import flow, task, serve
from prefect.task_runners import ConcurrentTaskRunner

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
    retries=3,
    retry_delay_seconds=60,
    tags=["iceberg", "offload"]
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

    # Build PySpark job command (executed in Spark container via docker exec)
    spark_container = "k2-spark-iceberg"
    pyspark_script = "/home/iceberg/offload/offload_generic.py"

    # Build the spark-submit command to run inside the Spark container
    spark_cmd = (
        f"/opt/spark/bin/spark-submit "
        f"--master local[2] "
        f"--conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog "
        f"--conf spark.sql.catalog.demo.type=hadoop "
        f"--conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse "
        f"--conf spark.sql.catalog.demo.io-impl=org.apache.iceberg.hadoop.HadoopFileIO "
        f"--conf spark.sql.defaultCatalog=demo "
        f"--packages com.clickhouse:clickhouse-jdbc:0.4.6 "
        f"{pyspark_script} "
        f"--source-table {source_table} "
        f"--target-table {target_table} "
        f"--timestamp-col {timestamp_col} "
        f"--sequence-col {sequence_col} "
        f"--layer {layer}"
    )

    # Docker exec command to run spark-submit in the Spark container
    cmd = ["docker", "exec", spark_container, "bash", "-c", spark_cmd]

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

        logger.info(f"✓ Offload completed: {source_table}")
        logger.info(f"  Duration: {duration:.2f}s")
        logger.info(f"  Stdout: {result.stdout[-500:]}")  # Last 500 chars

        return {
            "table": source_table,
            "status": "success",
            "duration_seconds": duration,
            "stdout": result.stdout,
        }

    except subprocess.CalledProcessError as e:
        duration = (datetime.now() - start_time).total_seconds()

        logger.error(f"✗ Offload failed: {source_table}")
        logger.error(f"  Exit code: {e.returncode}")
        logger.error(f"  Stderr: {e.stderr}")

        raise RuntimeError(f"Offload failed for {source_table}: {e.stderr}")

    except subprocess.TimeoutExpired as e:
        logger.error(f"✗ Offload timeout: {source_table} (exceeded 10 minutes)")
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
    logger.info("Starting Bronze Layer Offload (2 tables in parallel)")
    logger.info("=" * 80)

    results = []
    for table_config in TABLE_CONFIG["bronze"]:
        # Check if new data available (future optimization)
        has_new_data = check_watermark(table_config["source"])

        if has_new_data:
            result = offload_table(
                source_table=table_config["source"],
                target_table=table_config["target"],
                timestamp_col=table_config["timestamp_col"],
                sequence_col=table_config["sequence_col"],
                layer="bronze"
            )
            results.append(result)
        else:
            logger.info(f"Skipping {table_config['source']} (no new data)")

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
    description="Main orchestration: Bronze → Silver → Gold offload pipeline",
    version="1.0.0",
)
def iceberg_offload_main() -> Dict[str, any]:
    """
    Main orchestration flow for ClickHouse → Iceberg offload.

    Execution Order:
    1. Bronze layer (2 tables in parallel)
    2. Silver layer (1 table, depends on Bronze)
    3. Gold layer (6 tables in parallel, depends on Silver)

    Returns:
        Dict with summary metrics
    """
    from prefect import get_run_logger
    logger = get_run_logger()

    start_time = datetime.now()

    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " " * 20 + "K2 ICEBERG OFFLOAD PIPELINE" + " " * 31 + "║")
    logger.info("║" + " " * 15 + "ClickHouse → Iceberg (All 9 Tables)" + " " * 28 + "║")
    logger.info("╚" + "=" * 78 + "╝")
    logger.info("")

    # Step 1: Offload Bronze layer (parallel)
    bronze_results = offload_bronze_layer()

    # Step 2: Offload Silver layer (depends on Bronze)
    silver_results = offload_silver_layer()

    # Step 3: Offload Gold layer (parallel, depends on Silver)
    gold_results = offload_gold_layer()

    # Summary
    total_duration = (datetime.now() - start_time).total_seconds()
    total_tables = len(bronze_results) + len(silver_results) + len(gold_results)

    logger.info("")
    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " " * 30 + "OFFLOAD COMPLETE" + " " * 32 + "║")
    logger.info("╚" + "=" * 78 + "╝")
    logger.info(f"  Total tables offloaded: {total_tables}/9")
    logger.info(f"  Total duration: {total_duration:.2f}s")
    logger.info(f"  Bronze: {len(bronze_results)} tables")
    logger.info(f"  Silver: {len(silver_results)} tables")
    logger.info(f"  Gold: {len(gold_results)} tables")
    logger.info("")

    return {
        "total_tables": total_tables,
        "total_duration_seconds": total_duration,
        "bronze_results": bronze_results,
        "silver_results": silver_results,
        "gold_results": gold_results,
        "timestamp": start_time.isoformat(),
    }


# ============================================================================
# Deployment Configuration
# ============================================================================

if __name__ == "__main__":
    # Deploy flow with 15-minute schedule using new Prefect 2.14+ API
    iceberg_offload_main.serve(
        name="iceberg-offload-15min",
        cron="*/15 * * * *",
        tags=["iceberg", "offload", "production"],
        description="ClickHouse → Iceberg offload pipeline (runs every 15 minutes)",
        parameters={},
        pause_on_shutdown=False,
    )


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
