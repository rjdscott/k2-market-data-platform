"""
K2 Market Data Platform - Prefect Iceberg Maintenance Flow
Purpose: Daily Iceberg maintenance (compaction → snapshot expiry → audit)
Schedule: 02:00 UTC daily
Version: v1.0
Last Updated: 2026-02-18

Design:
  Tables are processed SEQUENTIALLY (not in parallel) to avoid saturating the
  shared k2-spark-iceberg container.  The offload pipeline runs every 15 minutes
  and shares the same Spark JVM; concurrent Spark sessions increase GC pressure
  and can cause OOM on a 4 GB container.  At 02:00 UTC the entire maintenance
  window (compaction + expiry + audit) completes in ~25–30 minutes, well inside
  the maintenance budget before the business day begins.

Execution Order:
  1. compact_all_tables   — rewrite small Parquet files for all 10 tables
  2. expire_all_snapshots — remove snapshots older than 7 days for all 10 tables
  3. run_audit            — compare ClickHouse vs Iceberg counts for yesterday

Error Policy:
  - Any individual table failure → logged as 'partial', remaining tables continue.
  - The parent flow raises RuntimeError if missing_data or error tables > 0 in
    the audit, which Prefect captures and marks the run as 'Failed' for alerting.
  - Compaction/expiry failures are warnings only — they don't block the audit.
"""

import re
import subprocess
from datetime import datetime
from typing import Dict, List

from prefect import flow, task
from prefect.task_runners import ThreadPoolTaskRunner


# ─────────────────────────────────────────────────────────────────────────────
# Configuration — mirrors _AUDIT_TABLE_CONFIG in iceberg_maintenance.py
# ─────────────────────────────────────────────────────────────────────────────

# Tables processed for compaction and expiry (all 10).
_ALL_TABLES = [
    # Bronze
    "cold.bronze_trades_binance",
    "cold.bronze_trades_kraken",
    "cold.bronze_trades_coinbase",
    # Silver
    "cold.silver_trades",
    # Gold
    "cold.gold_ohlcv_1m",
    "cold.gold_ohlcv_5m",
    "cold.gold_ohlcv_15m",
    "cold.gold_ohlcv_30m",
    "cold.gold_ohlcv_1h",
    "cold.gold_ohlcv_1d",
]

# Maintenance script path inside the k2-spark-iceberg container.
_MAINTENANCE_SCRIPT = "/home/iceberg/offload/iceberg_maintenance.py"

# Shared docker exec prefix.
_DOCKER_EXEC = ["docker", "exec", "k2-spark-iceberg", "python3", _MAINTENANCE_SCRIPT]

# Per-task timeout (10 min for compaction; 5 min for expiry/audit).
_COMPACT_TIMEOUT_S = 600
_EXPIRE_TIMEOUT_S  = 300
_AUDIT_TIMEOUT_S   = 600


# ─────────────────────────────────────────────────────────────────────────────
# Helper: run a docker exec maintenance command
# ─────────────────────────────────────────────────────────────────────────────

def _run_maintenance_cmd(cmd: List[str], timeout: int, logger) -> str:
    """
    Execute a docker exec command against k2-spark-iceberg and return stdout.

    Raises RuntimeError on non-zero exit or timeout so the calling Prefect task
    can catch and decide whether to re-raise (abort) or log-and-continue.
    """
    logger.info(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        if result.stdout:
            logger.info(result.stdout.strip())
        return result.stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command failed (exit {exc.returncode}):\n"
            f"  cmd    : {' '.join(cmd)}\n"
            f"  stderr : {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Command timed out after {timeout}s: {' '.join(cmd)}"
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Prefect Tasks
# ─────────────────────────────────────────────────────────────────────────────

@task(
    name="compact-table",
    description="Rewrite small Parquet files for one Iceberg table (binpack)",
    retries=1,
    retry_delay_seconds=120,
    tags=["iceberg", "maintenance", "compact"],
    log_prints=True,
)
def compact_table(table: str, target_file_size_mb: int = 128) -> Dict:
    """
    Run iceberg_maintenance.py compact for a single table.

    Returns a result dict so the parent flow can build a summary without
    needing to parse log output.
    """
    from prefect import get_run_logger
    logger = get_run_logger()
    logger.info(f"Compacting {table} (target={target_file_size_mb} MB) ...")

    cmd = _DOCKER_EXEC + [
        "compact",
        "--table", table,
        "--target-file-size-mb", str(target_file_size_mb),
    ]
    try:
        _run_maintenance_cmd(cmd, _COMPACT_TIMEOUT_S, logger)
        logger.info(f"✓ Compact complete: {table}")
        return {"table": table, "status": "success", "timestamp": datetime.now().isoformat()}
    except RuntimeError as exc:
        logger.error(f"✗ Compact failed: {table} — {exc}")
        return {"table": table, "status": "failed", "error": str(exc),
                "timestamp": datetime.now().isoformat()}


@task(
    name="expire-snapshots",
    description="Remove Iceberg snapshots older than retention window",
    retries=1,
    retry_delay_seconds=60,
    tags=["iceberg", "maintenance", "expire"],
    log_prints=True,
)
def expire_snapshots(
    table: str,
    max_age_hours: int = 168,
    retain_last: int = 3,
) -> Dict:
    """
    Run iceberg_maintenance.py expire for a single table.

    168 hours = 7 days; matches the DDL tblproperty
    'history.expire.max-snapshot-age-ms' = 604800000.
    """
    from prefect import get_run_logger
    logger = get_run_logger()
    logger.info(f"Expiring snapshots for {table} (max_age={max_age_hours}h, retain={retain_last}) ...")

    cmd = _DOCKER_EXEC + [
        "expire",
        "--table", table,
        "--max-age-hours", str(max_age_hours),
        "--retain-last", str(retain_last),
    ]
    try:
        _run_maintenance_cmd(cmd, _EXPIRE_TIMEOUT_S, logger)
        logger.info(f"✓ Expire complete: {table}")
        return {"table": table, "status": "success", "timestamp": datetime.now().isoformat()}
    except RuntimeError as exc:
        logger.error(f"✗ Expire failed: {table} — {exc}")
        return {"table": table, "status": "failed", "error": str(exc),
                "timestamp": datetime.now().isoformat()}


@task(
    name="run-audit",
    description="Compare ClickHouse vs Iceberg row counts; write to maintenance_audit_log",
    retries=1,
    retry_delay_seconds=120,
    tags=["iceberg", "maintenance", "audit"],
    log_prints=True,
)
def run_audit(audit_window_hours: int = 24) -> Dict:
    """
    Run iceberg_maintenance.py audit (all 10 tables in one Spark session).

    The task parses 'MISSING=N  ERROR=N' from the audit summary printed to
    stdout and returns them so the parent flow can decide whether to raise an
    alert without reading the PostgreSQL table.
    """
    from prefect import get_run_logger
    logger = get_run_logger()
    logger.info(f"Running warm-cold audit (window={audit_window_hours}h) ...")

    cmd = _DOCKER_EXEC + ["audit", "--audit-window-hours", str(audit_window_hours)]
    try:
        stdout = _run_maintenance_cmd(cmd, _AUDIT_TIMEOUT_S, logger)
    except RuntimeError as exc:
        logger.error(f"✗ Audit task failed: {exc}")
        return {
            "status": "failed",
            "missing_count": 0,
            "error_count": 1,
            "error": str(exc),
            "timestamp": datetime.now().isoformat(),
        }

    # Parse summary line: "OK=N  WARNING=N  MISSING=N  ERROR=N"
    missing_count = 0
    error_count   = 0
    m = re.search(r"MISSING=(\d+)", stdout)
    if m:
        missing_count = int(m.group(1))
    e = re.search(r"ERROR=(\d+)", stdout)
    if e:
        error_count = int(e.group(1))

    logger.info(
        f"✓ Audit complete — missing_data={missing_count}, errors={error_count}"
    )
    return {
        "status": "success",
        "missing_count": missing_count,
        "error_count": error_count,
        "timestamp": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sub-Flows
# ─────────────────────────────────────────────────────────────────────────────

@flow(
    name="compact-all-tables",
    description="Compact all 10 Iceberg tables (sequential)",
    task_runner=ThreadPoolTaskRunner(max_workers=1),
    log_prints=True,
)
def compact_all_tables(target_file_size_mb: int = 128) -> List[Dict]:
    """
    Compact every Iceberg table sequentially.

    Sequential (not ConcurrentTaskRunner) because all tables share the same
    k2-spark-iceberg container.  Running two compactions simultaneously would
    double the JVM heap pressure with diminishing returns — the bottleneck is
    I/O to MinIO, not CPU.
    """
    from prefect import get_run_logger
    logger = get_run_logger()
    logger.info(f"Starting compaction for {len(_ALL_TABLES)} tables ...")

    results = []
    for table in _ALL_TABLES:
        result = compact_table(table=table, target_file_size_mb=target_file_size_mb)
        results.append(result)

    failed = [r for r in results if r["status"] == "failed"]
    logger.info(
        f"Compaction complete: {len(results) - len(failed)}/{len(results)} succeeded, "
        f"{len(failed)} failed"
    )
    return results


@flow(
    name="expire-all-snapshots",
    description="Expire snapshots for all 10 Iceberg tables (sequential)",
    task_runner=ThreadPoolTaskRunner(max_workers=1),
    log_prints=True,
)
def expire_all_snapshots(max_age_hours: int = 168, retain_last: int = 3) -> List[Dict]:
    """Expire old snapshots sequentially across all Iceberg tables."""
    from prefect import get_run_logger
    logger = get_run_logger()
    logger.info(f"Starting snapshot expiry for {len(_ALL_TABLES)} tables ...")

    results = []
    for table in _ALL_TABLES:
        result = expire_snapshots(
            table=table, max_age_hours=max_age_hours, retain_last=retain_last
        )
        results.append(result)

    failed = [r for r in results if r["status"] == "failed"]
    logger.info(
        f"Expiry complete: {len(results) - len(failed)}/{len(results)} succeeded, "
        f"{len(failed)} failed"
    )
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main Flow
# ─────────────────────────────────────────────────────────────────────────────

@flow(
    name="iceberg-maintenance-main",
    description="Daily Iceberg maintenance: compact → expire → audit",
    version="1.0.0",
    log_prints=True,
)
def iceberg_maintenance_main(
    target_file_size_mb: int = 128,
    snapshot_max_age_hours: int = 168,
    snapshot_retain_last: int = 3,
    audit_window_hours: int = 24,
) -> Dict:
    """
    Orchestrate the full daily maintenance window.

    Execution order is strictly sequential:
      1. Compact  — fill in first so expired snapshots aren't compacted needlessly
      2. Expire   — clean up old metadata AFTER compaction has committed
      3. Audit    — validate data integrity for yesterday's window

    Compact before expire is intentional: compaction creates new snapshots
    (committing rewritten files), so expiring first would immediately lose
    the benefit of having those compacted snapshots for time-travel queries
    during the retention window.

    Raises RuntimeError if the audit finds missing_data or error tables so
    Prefect marks the run as Failed and sends an alert.
    """
    from prefect import get_run_logger
    logger = get_run_logger()
    start_time = datetime.now()

    logger.info("╔" + "=" * 68 + "╗")
    logger.info("║" + " " * 18 + "K2 ICEBERG DAILY MAINTENANCE" + " " * 22 + "║")
    logger.info("║" + " " * 12 + "compact → expire → audit  (02:00 UTC)" + " " * 19 + "║")
    logger.info("╚" + "=" * 68 + "╝")

    # Step 1: Compaction
    compact_results = compact_all_tables(target_file_size_mb=target_file_size_mb)

    # Step 2: Snapshot expiry
    expire_results = expire_all_snapshots(
        max_age_hours=snapshot_max_age_hours,
        retain_last=snapshot_retain_last,
    )

    # Step 3: Audit
    audit_result = run_audit(audit_window_hours=audit_window_hours)

    # Build summary
    total_duration = (datetime.now() - start_time).total_seconds()

    compact_failed = sum(1 for r in compact_results if r["status"] == "failed")
    expire_failed  = sum(1 for r in expire_results  if r["status"] == "failed")
    audit_missing  = audit_result.get("missing_count", 0)
    audit_errors   = audit_result.get("error_count", 0)

    overall_status = "success"
    if compact_failed > 0 or expire_failed > 0:
        overall_status = "partial"
    if audit_missing > 0 or audit_errors > 0:
        overall_status = "failed"

    logger.info("")
    logger.info("╔" + "=" * 68 + "╗")
    logger.info("║" + " " * 26 + "MAINTENANCE COMPLETE" + " " * 23 + "║")
    logger.info("╚" + "=" * 68 + "╝")
    logger.info(f"  Duration         : {total_duration:.0f}s")
    logger.info(f"  Overall status   : {overall_status.upper()}")
    logger.info(f"  Compact failures : {compact_failed}/{len(_ALL_TABLES)}")
    logger.info(f"  Expiry failures  : {expire_failed}/{len(_ALL_TABLES)}")
    logger.info(f"  Audit missing    : {audit_missing}")
    logger.info(f"  Audit errors     : {audit_errors}")

    summary = {
        "overall_status": overall_status,
        "total_duration_seconds": total_duration,
        "compact_failed": compact_failed,
        "expire_failed": expire_failed,
        "audit_missing": audit_missing,
        "audit_errors": audit_errors,
        "timestamp": start_time.isoformat(),
    }

    if overall_status == "failed":
        raise RuntimeError(
            f"Maintenance audit detected data integrity issues: "
            f"missing_data={audit_missing}, errors={audit_errors}. "
            "Review maintenance_audit_log in PostgreSQL for details."
        )

    return summary


if __name__ == "__main__":
    # Local test: run once immediately.
    print("Running iceberg maintenance flow (local test mode) ...")
    iceberg_maintenance_main()