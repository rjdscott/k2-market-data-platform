#!/usr/bin/env python3
"""
K2 Market Data Platform - Simple Offload Scheduler
Purpose: Run Iceberg offload jobs every 15 minutes (production schedule)
Version: v1.0 (Pragmatic approach - no Prefect dependency mismatch)
Last Updated: 2026-02-12

This scheduler runs the offload pipeline on a 15-minute schedule.
It's simpler than Prefect for Phase 5 MVP and avoids version mismatch issues.

Future: Migrate to Prefect 3.x when server is upgraded.
"""

import subprocess
import time
import logging
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/iceberg-offload-scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
OFFLOAD_SCRIPT = Path(__file__).parent / "offload_generic.py"
SCHEDULE_INTERVAL_MINUTES = 15

# Tables to offload (Bronze only for Phase 5)
BRONZE_TABLES = [
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
]

# Graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global shutdown_requested
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def run_offload_for_table(table_config: dict) -> dict:
    """
    Run offload for a single table.

    Args:
        table_config: Table configuration dict

    Returns:
        Dict with execution results
    """
    source = table_config["source"]
    target = table_config["target"]
    timestamp_col = table_config["timestamp_col"]
    sequence_col = table_config["sequence_col"]

    logger.info(f"Starting offload: {source} → {target}")
    start_time = time.time()

    cmd = [
        "docker", "exec", "k2-spark-iceberg",
        "python3", "/home/iceberg/offload/offload_generic.py",
        "--source-table", source,
        "--target-table", target,
        "--timestamp-col", timestamp_col,
        "--sequence-col", sequence_col,
        "--layer", "bronze"
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10-minute timeout
            check=True
        )

        duration = time.time() - start_time

        # Parse rows from output
        rows_written = 0
        for line in result.stdout.split('\n'):
            if "Rows offloaded:" in line:
                try:
                    rows_written = int(line.split(':')[1].strip().replace(',', ''))
                except:
                    pass

        logger.info(f"✓ Offload completed: {source} ({rows_written:,} rows in {duration:.1f}s)")

        return {
            "table": source,
            "status": "success",
            "rows": rows_written,
            "duration": duration,
            "timestamp": datetime.now().isoformat()
        }

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        logger.error(f"✗ Offload timeout: {source} (>10 minutes)")
        return {
            "table": source,
            "status": "timeout",
            "duration": duration,
            "timestamp": datetime.now().isoformat()
        }

    except subprocess.CalledProcessError as e:
        duration = time.time() - start_time
        logger.error(f"✗ Offload failed: {source}")
        logger.error(f"  Exit code: {e.returncode}")
        logger.error(f"  Stderr: {e.stderr[:500]}")
        return {
            "table": source,
            "status": "failed",
            "duration": duration,
            "error": e.stderr[:500],
            "timestamp": datetime.now().isoformat()
        }


def run_offload_cycle():
    """
    Run one complete offload cycle (all Bronze tables).

    Returns:
        Dict with cycle results
    """
    logger.info("=" * 80)
    logger.info("OFFLOAD CYCLE STARTED")
    logger.info(f"Time: {datetime.now().isoformat()}")
    logger.info(f"Tables: {len(BRONZE_TABLES)}")
    logger.info("=" * 80)

    cycle_start = time.time()
    results = []

    # Offload each table sequentially (could be parallel with ThreadPoolExecutor)
    for table_config in BRONZE_TABLES:
        if shutdown_requested:
            logger.info("Shutdown requested, stopping cycle")
            break

        result = run_offload_for_table(table_config)
        results.append(result)

    cycle_duration = time.time() - cycle_start

    # Summary
    successful = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    timeout = sum(1 for r in results if r["status"] == "timeout")
    total_rows = sum(r.get("rows", 0) for r in results if r["status"] == "success")

    logger.info("=" * 80)
    logger.info("OFFLOAD CYCLE COMPLETED")
    logger.info(f"Duration: {cycle_duration:.1f}s")
    logger.info(f"Success: {successful}, Failed: {failed}, Timeout: {timeout}")
    logger.info(f"Total rows offloaded: {total_rows:,}")
    logger.info("=" * 80)

    return {
        "cycle_duration": cycle_duration,
        "successful": successful,
        "failed": failed,
        "timeout": timeout,
        "total_rows": total_rows,
        "results": results,
        "timestamp": datetime.now().isoformat()
    }


def calculate_next_run_time() -> datetime:
    """
    Calculate next run time aligned to 15-minute intervals.

    Examples:
    - Current: 14:07 → Next: 14:15
    - Current: 14:15 → Next: 14:30
    - Current: 14:29 → Next: 14:30

    Returns:
        Datetime of next scheduled run
    """
    now = datetime.now()
    current_minute = now.minute

    # Find next 15-minute interval
    next_interval = ((current_minute // SCHEDULE_INTERVAL_MINUTES) + 1) * SCHEDULE_INTERVAL_MINUTES

    if next_interval >= 60:
        # Next hour
        next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        # Same hour
        next_run = now.replace(minute=next_interval, second=0, microsecond=0)

    return next_run


def main():
    """
    Main scheduler loop - runs offload every 15 minutes.
    """
    logger.info("=" * 80)
    logger.info("K2 ICEBERG OFFLOAD SCHEDULER STARTED")
    logger.info(f"Schedule: Every {SCHEDULE_INTERVAL_MINUTES} minutes")
    logger.info(f"Tables: {len(BRONZE_TABLES)} Bronze tables")
    logger.info(f"Log file: /tmp/iceberg-offload-scheduler.log")
    logger.info("=" * 80)

    # Run first cycle immediately
    logger.info("Running initial offload cycle...")
    run_offload_cycle()

    # Schedule loop
    while not shutdown_requested:
        next_run = calculate_next_run_time()
        wait_seconds = (next_run - datetime.now()).total_seconds()

        logger.info(f"Next run scheduled at: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Waiting {wait_seconds:.0f} seconds...")

        # Sleep in small intervals to check for shutdown
        for _ in range(int(wait_seconds)):
            if shutdown_requested:
                break
            time.sleep(1)

        if shutdown_requested:
            break

        # Run offload cycle
        try:
            run_offload_cycle()
        except Exception as e:
            logger.error(f"Offload cycle failed with exception: {e}")
            logger.exception(e)

    logger.info("Scheduler stopped gracefully")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Scheduler crashed: {e}")
        logger.exception(e)
        sys.exit(1)
