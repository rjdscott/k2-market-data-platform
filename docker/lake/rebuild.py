#!/usr/bin/env python3
"""
Rebuild a lake layer from its parent over the whole archive.

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bronze
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bronze --exchange kraken
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bronze --dry-run
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer silver
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer gold
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer bars    # gold.bars only; gold.trades and the candles untouched
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/rebuild.py --layer books   # silver.book_* + gold.book_top20/bbo_1s

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

    docker exec k2-prefect-server prefect deployment schedule pause lake-ingest/lake-ingest-5min <schedule-id>   # id from: prefect deployment schedule ls lake-ingest/lake-ingest-5min
    ... rebuild ...
    docker exec k2-prefect-server prefect deployment schedule resume lake-ingest/lake-ingest-5min <schedule-id>

Bronze, silver and gold: the shape (pin parent, drop, recreate, decode, record)
is the same for each.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import books
import bronze
import gold
import silver
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


def drop(spark, table: str, attempts: int = 10) -> None:
    """DROP ... PURGE, treating Lakekeeper's post-drop 400 as the success it is.

    Spark's `DROP TABLE ... PURGE` asks the catalog to drop with
    `purgeRequested=true`, then tries to delete the data files itself by loading
    the table again. Lakekeeper has already dropped it and queued its own purge,
    so that second load fails with `BadRequestException: Malformed request:
    Table does not exist or user does not have permission to view it at location
    s3://.../metadata/0000N-....metadata.json` — every table, every time
    (2026-08-27, six for six). The table IS gone and the files DO go: the
    catalog's purge task deletes the prefix. So the exception is read as "check
    whether the drop happened", and only a table that still exists is retried.
    """
    for attempt in range(1, attempts + 1):
        try:
            if spark.catalog.tableExists(table):
                spark.sql(f"REFRESH TABLE {table}")
            print(f"DROP TABLE {table} PURGE", flush=True)
            spark.sql(f"DROP TABLE IF EXISTS {table} PURGE")
            return
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            if not spark.catalog.tableExists(table):
                print(f"drop {table}: dropped; the catalog purges the files asynchronously", flush=True)
                return
            if attempt == attempts:
                raise
            print(f"drop {table}: attempt {attempt} failed ({str(exc).splitlines()[0][:120]}); retrying in 3 s", flush=True)
            time.sleep(3)


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
    parser.add_argument("--layer", choices=("bronze", "silver", "gold", "books", "bars"), required=True)
    parser.add_argument("--exchange", choices=sorted({t.exchange for t in bronze.VENUE_TABLES}), help="one venue only")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, drop nothing, write nothing")
    args = parser.parse_args()

    exchanges = [args.exchange] if args.exchange else sorted({t.exchange for t in bronze.VENUE_TABLES})
    if args.layer == "bronze":
        tables = [t for t in bronze.VENUE_TABLES if t.exchange in exchanges]
    elif args.layer == "silver":
        tables = [t for t in silver.TRADES if t.exchange in exchanges]
    elif args.layer == "books":
        tables = [t.table for t in books.BOOKS if t.exchange in exchanges] + [books.GOLD_BOOK, books.GOLD_BBO, books.STATE]
    elif args.layer == "bars":
        tables = [gold.BARS]
    else:
        tables = list(gold.TABLES)

    print(f"waiting for {LOCK_PATH}", flush=True)
    lock = acquire_lock(LOCK_PATH, blocking=True)  # noqa: F841 - held until exit
    run_ts = datetime.now(timezone.utc)
    spark = lake_session("k2-lake-rebuild", driver_memory=MAINTENANCE_DRIVER_MEMORY)
    try:
        if args.layer == "books":
            print(f"rebuild books: {len(tables)} table(s) for {exchanges}, from the bronze book tables' current snapshots")
            if args.dry_run:
                for t in tables:
                    print(f"  DROP TABLE {t} PURGE; then recreate")
                return 0
            for t in tables:
                drop(spark, t)
            for t in tables:
                recreate(spark, t.split(".")[-1])
            started = datetime.now()
            totals = books.rebuild(spark, run_ts, exchanges)
            elapsed = (datetime.now() - started).total_seconds()
            print(f"\nrebuild books: done in {elapsed:.0f} s")
            for table, n in sorted(totals.items()):
                print(f"  {table:<40} {n:>14,} rows")
            return 0
        if args.layer == "bars":
            print("rebuild bars: gold.bars from gold.trades' current snapshot at config/bars.yaml")
            if args.dry_run:
                print(f"  DROP TABLE {gold.BARS} PURGE; then recreate")
                return 0
            drop(spark, gold.BARS)
            recreate(spark, "bars")
            started = datetime.now()
            n = gold.rebuild_bars(spark, run_ts)
            print(f"\nrebuild bars: done in {(datetime.now() - started).total_seconds():.0f} s, {n:,} rows")
            return 0
        if args.layer == "gold":
            print(f"rebuild gold: {len(tables)} table(s) from silver's current snapshots")
            if args.dry_run:
                for t in tables:
                    print(f"  DROP TABLE {t} PURGE; then recreate")
                return 0
            for t in tables:
                drop(spark, t)
            for t in tables:
                recreate(spark, t.split(".")[-1])
            started = datetime.now()
            totals = gold.rebuild(spark, run_ts)
            elapsed = (datetime.now() - started).total_seconds()
            print(f"\nrebuild gold: done in {elapsed:.0f} s")
            for table, n in sorted(totals.items()):
                print(f"  {table:<40} {n:>14,} rows")
            return 0
        if args.layer == "silver":
            print(f"rebuild silver: {len(tables)} table(s) for {exchanges}, from their bronze tables' current snapshots")
            if args.dry_run:
                for t in tables:
                    print(f"  DROP TABLE {t.table} PURGE; then recreate; replay {t.source} by day")
                return 0
            for t in tables:
                drop(spark, t.table)
            for t in tables:
                recreate(spark, t.table.split(".")[-1])
            started = datetime.now()
            totals = silver.rebuild(spark, run_ts, exchanges)
            elapsed = (datetime.now() - started).total_seconds()
            print(f"\nrebuild silver: done in {elapsed:.0f} s")
            for table, n in sorted(totals.items()):
                print(f"  {table:<40} {n:>14,} rows")
            return 0
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
            drop(spark, t.table)
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
