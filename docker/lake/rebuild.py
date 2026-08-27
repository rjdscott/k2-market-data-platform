#!/usr/bin/env python3
"""
Rebuild a lake layer from its parent over the whole archive.

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bronze
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bronze --exchange kraken
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bronze --dry-run

`make lake-rebuild LAYER=bronze` is the same thing from the host.

What it does, in order, under the blocking writer lock (docker/lake/lock.py):
pin the parent's current snapshot; DROP ... PURGE the layer's tables (for one
venue, only that venue's); re-create them from ddl/lake.sql; decode the parent
one day per venue at a time (bronze.rebuild); print the timing. Every commit
carries the pinned parent snapshot id, so when the lock is released the
5-minute ingest resumes exactly after the pin and nothing arrives twice.

**Pause the ingest schedule first** — the lock makes a concurrent ingest exit 2
rather than corrupt anything, but an hour of exit-2 runs is an hour of
LakeIngestFailed noise:

    docker exec k2-prefect-server prefect deployment schedule pause lake-ingest/lake-ingest-5min --all
    ... rebuild ...
    docker exec k2-prefect-server prefect deployment schedule resume lake-ingest/lake-ingest-5min --all

Bronze only, today. Silver and gold get a branch here when they exist; the
shape (pin parent, drop, recreate, decode by day, record) is the same.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import bronze
from apply_ddl import apply, table_statements
from catalog import current_snapshot_id
from lock import LOCK_PATH, acquire_lock

from spark_conf import MAINTENANCE_DRIVER_MEMORY, lake_session


def archive_days(spark, exchanges: list) -> list:
    """Every UTC day with at least one raw frame for the venues, oldest first."""
    topics = " OR ".join(f"topic LIKE '%.raw.{ex}'" for ex in exchanges)
    rows = spark.sql(
        f"SELECT DISTINCT to_date(kafka_ts) AS d FROM {bronze.RAW_TABLE} WHERE {topics} ORDER BY d"
    ).collect()
    return [r["d"] for r in rows]


def recreate(spark, name: str, attempts: int = 10) -> None:
    """Re-apply a table's DDL after a PURGE, retrying while the catalog finishes the drop.

    Lakekeeper purges asynchronously: a CREATE issued straight after DROP ...
    PURGE failed once on 2026-08-27 with a catalog error and left the table
    absent (the next ingest then died on TABLE_OR_VIEW_NOT_FOUND). A few
    seconds' retry is the whole fix; the DDL is idempotent so a retry after a
    half-applied attempt converges.
    """
    for attempt in range(1, attempts + 1):
        try:
            apply(spark, table_statements(name))
            return
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            if attempt == attempts:
                raise
            print(f"recreate {name}: attempt {attempt} failed ({str(exc).splitlines()[0][:120]}); retrying in 3 s", flush=True)
            time.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=("bronze",), required=True)
    parser.add_argument("--exchange", choices=sorted({t.exchange for t in bronze.VENUE_TABLES}), help="one venue only")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, drop nothing, write nothing")
    args = parser.parse_args()

    exchanges = [args.exchange] if args.exchange else sorted({t.exchange for t in bronze.VENUE_TABLES})
    tables = [t for t in bronze.VENUE_TABLES if t.exchange in exchanges]

    print(f"waiting for {LOCK_PATH}", flush=True)
    lock = acquire_lock(LOCK_PATH, blocking=True)  # noqa: F841 - held until exit
    run_ts = datetime.now(timezone.utc)
    spark = lake_session("k2-lake-rebuild", driver_memory=MAINTENANCE_DRIVER_MEMORY)
    try:
        pinned = current_snapshot_id(spark, bronze.RAW_TABLE)
        days = archive_days(spark, exchanges)
        print(f"rebuild {args.layer}: {len(tables)} table(s) for {exchanges}, {len(days)} day(s) of raw as of snapshot {pinned}")
        if args.dry_run:
            for t in tables:
                print(f"  DROP TABLE {t.table} PURGE; then {len(table_statements(t.name))} DDL statement(s)")
            print(f"  days: {days[0]} .. {days[-1]}" if days else "  no raw frames")
            return 0
        if pinned is None or not days:
            print("nothing to rebuild from: raw.messages is empty")
            return 0
        for t in tables:
            print(f"DROP TABLE {t.table} PURGE", flush=True)
            spark.sql(f"DROP TABLE IF EXISTS {t.table} PURGE")
        for t in tables:
            recreate(spark, t.name)
        started = datetime.now()
        totals = bronze.rebuild(spark, pinned, run_ts, days, exchanges)
        elapsed = (datetime.now() - started).total_seconds()
        print(f"\nrebuild {args.layer}: done in {elapsed:.0f} s, src snapshot {pinned}")
        for table, n in sorted(totals.items()):
            print(f"  {table:<40} {n:>14,} rows")
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
