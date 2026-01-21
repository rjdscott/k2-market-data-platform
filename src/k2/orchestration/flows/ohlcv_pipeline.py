#!/usr/bin/env python3
"""OHLCV Pipeline Flows - Prefect Orchestration for Gold OHLCV Aggregation.

This module defines Prefect flows for orchestrating OHLCV aggregation jobs:
- 1m OHLCV: Every 5 minutes (incremental with MERGE)
- 5m OHLCV: Every 15 minutes (incremental with MERGE)
- 30m OHLCV: Every 30 minutes (batch with INSERT OVERWRITE)
- 1h OHLCV: Every hour (batch with INSERT OVERWRITE)
- 1d OHLCV: Daily at 00:05 UTC (batch with INSERT OVERWRITE)

Schedule Design - Staggered Execution:
- 1m: */5 * * * * (every 5 minutes)
- 5m: 5,20,35,50 * * * * (every 15 minutes, +5min offset)
- 30m: 10,40 * * * * (every 30 minutes, +10min offset)
- 1h: 15 * * * * (every hour, +15min offset)
- 1d: 5 0 * * * (daily at 00:05 UTC, +5min offset)

Rationale: Staggered schedules prevent resource contention by ensuring
jobs don't run simultaneously on the limited Spark cluster (2 available cores).

Architecture:
- Each flow wraps a spark-submit command
- Retry logic: 2 retries with 60s delay
- Timeout: 5 minutes per job
- Sequential execution via Prefect (no concurrency)

Usage:
    # Start Prefect server (docker-compose)
    docker-compose up -d prefect-server prefect-agent

    # Deploy flows
    python src/k2/orchestration/flows/ohlcv_pipeline.py

    # View flows in UI
    open http://localhost:4200

Related:
- Decision #020: Prefect Orchestration
- Decision #021: Hybrid Aggregation Strategy
- Phase 13 Documentation: docs/phases/phase-13-ohlcv-analytics/
"""

import subprocess
import sys
from pathlib import Path
from datetime import timedelta

from prefect import flow, task, serve


# Spark cluster configuration (matches k2-gold-aggregation container)
SPARK_MASTER = "spark://spark-master:7077"
SPARK_CORES = 1
SPARK_MEMORY = "1024m"
SPARK_DRIVER_MEMORY = "512m"
# Include all Iceberg + AWS JARs (same as gold_aggregation.py)
JARS = ",".join([
    "/opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar",
    "/opt/spark/jars-extra/iceberg-aws-1.4.0.jar",
    "/opt/spark/jars-extra/bundle-2.20.18.jar",
    "/opt/spark/jars-extra/url-connection-client-2.20.18.jar",
    "/opt/spark/jars-extra/hadoop-aws-3.3.4.jar",
    "/opt/spark/jars-extra/aws-java-sdk-bundle-1.12.262.jar",
])


@task(name="spark-submit", retries=2, retry_delay_seconds=60, timeout_seconds=300)
def run_spark_job(
    job_path: str,
    job_args: list[str],
    job_name: str,
) -> dict:
    """Execute a Spark job via docker exec spark-submit.

    Args:
        job_path: Path to the Spark job Python script (inside container)
        job_args: List of command-line arguments for the job
        job_name: Human-readable name for logging

    Returns:
        dict with execution results (returncode, stdout, stderr)

    Raises:
        Exception if spark-submit fails (after retries)
    """
    # Build spark-submit command matching gold_aggregation.py pattern exactly
    spark_cmd = (
        f"cd /opt/k2 && /opt/spark/bin/spark-submit "
        f"--master {SPARK_MASTER} "
        f"--total-executor-cores {SPARK_CORES} "
        f"--executor-cores {SPARK_CORES} "
        f"--executor-memory {SPARK_MEMORY} "
        f"--driver-memory {SPARK_DRIVER_MEMORY} "
        f"--conf spark.driver.extraJavaOptions=-Daws.region=us-east-1 "
        f"--conf spark.executor.extraJavaOptions=-Daws.region=us-east-1 "
        f"--jars {JARS} "
        f"{job_path} {' '.join(job_args)}"
    )

    # Wrap in docker exec bash -c to run inside spark-master container
    cmd = ["docker", "exec", "k2-spark-master", "bash", "-c", spark_cmd]

    print(f"[{job_name}] Executing spark-submit via docker exec...")
    print(f"[{job_name}] Command: {spark_cmd}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minutes
    )

    if result.returncode != 0:
        error_msg = f"[{job_name}] FAILED: {result.stderr}"
        print(error_msg)
        raise Exception(error_msg)

    print(f"[{job_name}] SUCCESS")
    print(f"[{job_name}] Output: {result.stdout[-500:]}")  # Last 500 chars

    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


# =============================================================================
# Flow 1: OHLCV 1-Minute (Incremental)
# =============================================================================


@flow(name="OHLCV-1m-Pipeline", log_prints=True)
def ohlcv_1m_flow():
    """Generate 1-minute OHLCV candles (incremental with MERGE).

    Schedule: Every 5 minutes
    Lookback: 10 minutes
    Target: gold_ohlcv_1m
    """
    print("=" * 70)
    print("OHLCV 1-Minute Pipeline - Starting")
    print("=" * 70)

    run_spark_job(
        job_path="src/k2/spark/jobs/batch/ohlcv_incremental.py",
        job_args=["--timeframe", "1m", "--lookback-minutes", "10"],
        job_name="OHLCV-1m",
    )

    print("✓ OHLCV 1-minute pipeline completed")


# =============================================================================
# Flow 2: OHLCV 5-Minute (Incremental)
# =============================================================================


@flow(name="OHLCV-5m-Pipeline", log_prints=True)
def ohlcv_5m_flow():
    """Generate 5-minute OHLCV candles (incremental with MERGE).

    Schedule: Every 15 minutes (offset +5min)
    Lookback: 20 minutes
    Target: gold_ohlcv_5m
    """
    print("=" * 70)
    print("OHLCV 5-Minute Pipeline - Starting")
    print("=" * 70)

    run_spark_job(
        job_path="src/k2/spark/jobs/batch/ohlcv_incremental.py",
        job_args=["--timeframe", "5m", "--lookback-minutes", "20"],
        job_name="OHLCV-5m",
    )

    print("✓ OHLCV 5-minute pipeline completed")


# =============================================================================
# Flow 3: OHLCV 30-Minute (Batch)
# =============================================================================


@flow(name="OHLCV-30m-Pipeline", log_prints=True)
def ohlcv_30m_flow():
    """Generate 30-minute OHLCV candles (batch with INSERT OVERWRITE).

    Schedule: Every 30 minutes (offset +10min)
    Lookback: 1 hour
    Target: gold_ohlcv_30m
    """
    print("=" * 70)
    print("OHLCV 30-Minute Pipeline - Starting")
    print("=" * 70)

    run_spark_job(
        job_path="src/k2/spark/jobs/batch/ohlcv_batch.py",
        job_args=["--timeframe", "30m", "--lookback-hours", "1"],
        job_name="OHLCV-30m",
    )

    print("✓ OHLCV 30-minute pipeline completed")


# =============================================================================
# Flow 4: OHLCV 1-Hour (Batch)
# =============================================================================


@flow(name="OHLCV-1h-Pipeline", log_prints=True)
def ohlcv_1h_flow():
    """Generate 1-hour OHLCV candles (batch with INSERT OVERWRITE).

    Schedule: Every hour (offset +15min)
    Lookback: 2 hours
    Target: gold_ohlcv_1h
    """
    print("=" * 70)
    print("OHLCV 1-Hour Pipeline - Starting")
    print("=" * 70)

    run_spark_job(
        job_path="src/k2/spark/jobs/batch/ohlcv_batch.py",
        job_args=["--timeframe", "1h", "--lookback-hours", "2"],
        job_name="OHLCV-1h",
    )

    print("✓ OHLCV 1-hour pipeline completed")


# =============================================================================
# Flow 5: OHLCV 1-Day (Batch)
# =============================================================================


@flow(name="OHLCV-1d-Pipeline", log_prints=True)
def ohlcv_1d_flow():
    """Generate 1-day OHLCV candles (batch with INSERT OVERWRITE).

    Schedule: Daily at 00:05 UTC (offset +5min)
    Lookback: 2 days
    Target: gold_ohlcv_1d
    """
    print("=" * 70)
    print("OHLCV 1-Day Pipeline - Starting")
    print("=" * 70)

    run_spark_job(
        job_path="src/k2/spark/jobs/batch/ohlcv_batch.py",
        job_args=["--timeframe", "1d", "--lookback-days", "2"],
        job_name="OHLCV-1d",
    )

    print("✓ OHLCV 1-day pipeline completed")


# =============================================================================
# Deployment Configuration
# =============================================================================


def main():
    """Deploy all OHLCV flows to Prefect server using Prefect 3 serve() API."""
    print("\n" + "=" * 70)
    print("K2 OHLCV Pipeline Deployment")
    print("Registering 5 OHLCV flows with Prefect server")
    print("=" * 70 + "\n")

    print("Deploying flows with staggered schedules:")
    print("  1. ohlcv-1m-scheduled - Generate 1-minute OHLCV candles every 5 minutes")
    print("  2. ohlcv-5m-scheduled - Generate 5-minute OHLCV candles every 15 minutes")
    print("  3. ohlcv-30m-scheduled - Generate 30-minute OHLCV candles every 30 minutes")
    print("  4. ohlcv-1h-scheduled - Generate 1-hour OHLCV candles every hour")
    print("  5. ohlcv-1d-scheduled - Generate 1-day OHLCV candles daily at 00:05 UTC")

    # Use Prefect 3 serve() API to deploy all flows with schedules
    serve(
        ohlcv_1m_flow.to_deployment(
            name="ohlcv-1m-scheduled",
            version="1.0",
            cron="*/5 * * * *",
            tags=["ohlcv", "1m", "incremental", "gold"],
            description="Generate 1-minute OHLCV candles every 5 minutes",
        ),
        ohlcv_5m_flow.to_deployment(
            name="ohlcv-5m-scheduled",
            version="1.0",
            cron="5,20,35,50 * * * *",
            tags=["ohlcv", "5m", "incremental", "gold"],
            description="Generate 5-minute OHLCV candles every 15 minutes",
        ),
        ohlcv_30m_flow.to_deployment(
            name="ohlcv-30m-scheduled",
            version="1.0",
            cron="10,40 * * * *",
            tags=["ohlcv", "30m", "batch", "gold"],
            description="Generate 30-minute OHLCV candles every 30 minutes",
        ),
        ohlcv_1h_flow.to_deployment(
            name="ohlcv-1h-scheduled",
            version="1.0",
            cron="15 * * * *",
            tags=["ohlcv", "1h", "batch", "gold"],
            description="Generate 1-hour OHLCV candles every hour",
        ),
        ohlcv_1d_flow.to_deployment(
            name="ohlcv-1d-scheduled",
            version="1.0",
            cron="5 0 * * *",
            tags=["ohlcv", "1d", "batch", "gold"],
            description="Generate 1-day OHLCV candles daily at 00:05 UTC",
        ),
    )

    print("\n" + "=" * 70)
    print("✓ All OHLCV flows deployed successfully")
    print("=" * 70)
    print("\nNext Steps:")
    print("  1. View flows in Prefect UI: http://localhost:4200")
    print("  2. Monitor flow runs in real-time")
    print("  3. Check Spark UI for job execution: http://localhost:8090")
    print("\nSchedules:")
    print("  • 1m OHLCV: Every 5 minutes (*/5 * * * *)")
    print("  • 5m OHLCV: Every 15 minutes at 5,20,35,50 (5,20,35,50 * * * *)")
    print("  • 30m OHLCV: Every 30 minutes at 10,40 (10,40 * * * *)")
    print("  • 1h OHLCV: Every hour at :15 (15 * * * *)")
    print("  • 1d OHLCV: Daily at 00:05 UTC (5 0 * * *)")
    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
