#!/usr/bin/env python3
"""
Prefect flows for the v3 lake: `lake-ingest-5min` and `lake-maintenance-daily`.

Both dispatch the same way v2's offload flows do — `docker exec
k2-spark-iceberg python3 …` over the mounted Docker socket — rather than
running Spark inside the worker. That keeps one Spark image, one set of jars
and one 4 GB memory limit, and it means an ingest that dies takes its container
down and nothing else.

**The flow adds no logic.** Position, idempotency and exit codes all live in
docker/lake/{ingest,maintenance}.py. What the flow contributes is a schedule, a
concurrency limit of 1, and a failure that Prometheus can see. Anything smarter
here would be a second place where "did this run succeed" is decided.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from prefect import flow, get_run_logger, task

SPARK_CONTAINER = "k2-spark-iceberg"
LAKE_DIR = "/home/iceberg/lake"

# Generous: a backlog slice after an outage reads far more than a five-minute
# window. The concurrency limit of 1 on the deployment, not this timeout, is
# what stops two ingests overlapping.
INGEST_TIMEOUT_S = 3600
MAINTENANCE_TIMEOUT_S = 7200


def _run(script: str, args: list, timeout: int) -> str:
    """`docker exec` one lake script; raise on a non-zero exit, with its output.

    stdout and stderr are both surfaced in the exception because a failed audit
    prints which check failed to stdout and exits 1 — swallowing that would turn
    "duplicate_identifiers failed on bronze.trades" into "returned exit status 1".
    """
    logger = get_run_logger()
    cmd = ["docker", "exec", SPARK_CONTAINER, "python3", f"{LAKE_DIR}/{script}", *args]
    logger.info("running: %s", " ".join(cmd))
    started = datetime.now(timezone.utc)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info("%s exited %d after %.0fs", script, result.returncode, elapsed)
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.returncode != 0:
        logger.error(result.stderr.strip())
        raise RuntimeError(
            f"{script} exited {result.returncode} after {elapsed:.0f}s\n"
            f"stdout:\n{result.stdout.strip()}\nstderr:\n{result.stderr.strip()}"
        )
    return result.stdout


@task(name="lake-ingest", retries=1, retry_delay_seconds=60, log_prints=True)
def ingest(end_timestamp: str = "") -> str:
    """One ingest cycle: Kafka to raw.messages, raw.messages to bronze.

    One retry, because the failure this actually sees is a Lakekeeper commit
    losing an optimistic-concurrency race, which succeeds on the next attempt.
    A retry is safe at any point: the offsets live in the snapshot summary, so a
    run that died before committing re-reads the same range and a run that died
    after committing reads the next one.
    """
    args = ["--end-timestamp", end_timestamp] if end_timestamp else []
    return _run("ingest.py", args, INGEST_TIMEOUT_S)


@task(name="lake-maintenance", retries=0, log_prints=True)
def maintain(days: int = 2, retain_days: int = 7) -> str:
    """Compact, expire, audit. No retry: a failed audit is a finding, not a blip,
    and retrying it would only delay the alert by the length of a second run."""
    return _run(
        "maintenance.py",
        ["--days", str(days), "--retain-days", str(retain_days)],
        MAINTENANCE_TIMEOUT_S,
    )


@flow(name="lake-ingest", log_prints=True)
def lake_ingest(end_timestamp: str = "") -> None:
    ingest(end_timestamp)


@flow(name="lake-maintenance", log_prints=True)
def lake_maintenance(days: int = 2, retain_days: int = 7) -> None:
    maintain(days, retain_days)
