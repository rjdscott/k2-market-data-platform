#!/usr/bin/env python3
"""
K2 v3 lake maintenance — compact, expire, audit. Runs nightly under Prefect
(`lake-maintenance-daily`), or by hand:

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --audit-only

**Exit code is the product.** Any failed audit exits non-zero, which fails the
Prefect flow run, which is what `LakeAuditFailed` alerts on. Every check also
lands as a row in `lake.audit.checks` so "when did this last hold" is a query
rather than a log grep.

**No row is ever deleted here.** Compaction rewrites files, not rows.
`expire_snapshots` drops old *metadata* and the data files that compaction
already superseded — never a file the current snapshot still references. That is
how `raw.messages` is both "never expired" (requirements clarification Q8) and
not accumulating a snapshot per five minutes forever.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from pyspark.sql import Row

import offsets as O
from spark_conf import CATALOG, lake_session

RAW_TABLE = f"{CATALOG}.raw.messages"
TRADES_TABLE = f"{CATALOG}.bronze.trades"
BOOK_TABLE = f"{CATALOG}.bronze.book_snapshots_l2"
CHECKS_TABLE = f"{CATALOG}.audit.checks"

# Iceberg's procedures take the table without the catalog prefix.
def _bare(table: str) -> str:
    return table.split(".", 1)[1]


def _ts(moment: datetime) -> str:
    """A Spark SQL TIMESTAMP literal, for a procedure argument."""
    return "TIMESTAMP '{}'".format(moment.strftime("%Y-%m-%d %H:%M:%S"))


def _where_ts(moment: datetime) -> str:
    """The same instant for use *inside* a `where => '...'` string.

    `timestamp_seconds(<int>)` rather than `TIMESTAMP '...'` because the
    predicate is itself a single-quoted procedure argument, and a quoted literal
    nested inside it closes the argument early: Spark answers
    `extraneous input '2026' expecting {,, )}`, which does not obviously mean
    "your quotes nested". No quotes, no nesting, same instant.
    """
    return f"timestamp_seconds({int(moment.timestamp())})"


# ── compaction ──────────────────────────────────────────────────────────────


def compact(spark, since: datetime) -> None:
    """Binpack `raw.messages`, sort-rewrite the two bronze tables.

    Both are bounded to recent partitions. Everything older was compacted by a
    previous run and rewriting it again would rewrite the whole archive nightly
    — the cost of which grows without bound while the benefit is zero.
    """
    print(f"compacting partitions from {since:%Y-%m-%d %H:%M} UTC")

    # binpack, not sort: raw.messages is written in offset order already (the
    # table's own sort order), so a sort rewrite would re-sort data that is
    # sorted. Merging small files is the whole job.
    # min-input-files 5 stops a partition with two healthy files being rewritten
    # every night for no gain.
    _call(
        spark,
        f"CALL {CATALOG}.system.rewrite_data_files("
        f"  table => '{_bare(RAW_TABLE)}',"
        f"  strategy => 'binpack',"
        f"  where => 'kafka_ts >= {_where_ts(since)}',"
        f"  options => map('min-input-files', '5', 'target-file-size-bytes', '268435456'))",
    )

    for table, column in ((TRADES_TABLE, "exchange_ts"), (BOOK_TABLE, "snapshot_ts")):
        # No sort_order argument: the tables declare theirs in lake.sql and the
        # procedure uses the declared one. Repeating it here is a second place
        # to get it wrong.
        _call(
            spark,
            f"CALL {CATALOG}.system.rewrite_data_files("
            f"  table => '{_bare(table)}',"
            f"  strategy => 'sort',"
            f"  where => '{column} >= {_where_ts(since)}',"
            f"  options => map('min-input-files', '5', 'target-file-size-bytes', '134217728'))",
        )


def expire(spark, retain_days: int) -> None:
    """Drop snapshots older than `retain_days`, keeping at least 10.

    Seven days is the floor because that is the window `bronze.*` incremental
    reads need: stage 2 resumes from the snapshot id it last decoded, and
    expiring that snapshot turns the next run into an error. A week of slack
    covers a long outage without unbounded metadata growth.
    """
    older_than = datetime.now(timezone.utc) - timedelta(days=retain_days)
    for table in (RAW_TABLE, TRADES_TABLE, BOOK_TABLE, CHECKS_TABLE):
        _call(
            spark,
            f"CALL {CATALOG}.system.expire_snapshots("
            f"  table => '{_bare(table)}',"
            f"  older_than => {_ts(older_than)},"
            f"  retain_last => 10)",
        )


def _call(spark, sql: str) -> None:
    print(f"  {' '.join(sql.split())}")
    for row in spark.sql(sql).collect():
        print(f"    -> {row.asDict()}")


# ── audits ──────────────────────────────────────────────────────────────────


def audit_offset_continuity(spark) -> list:
    """Every (topic, partition) in `raw.messages` holds an unbroken offset run.

    The one check that proves the exactly-once contract end to end: a hole means
    an ingest skipped records, an overlap means it wrote some twice. Grouped over
    the whole table, not per day, so a gap sitting exactly on a `days(kafka_ts)`
    seam is caught rather than hidden by the partition boundary.
    """
    rows = spark.sql(
        f"""
        SELECT topic, partition, count(*) AS n, min(offset) AS lo, max(offset) AS hi
        FROM {RAW_TABLE}
        GROUP BY topic, partition
        """
    ).collect()
    if not rows:
        return [_result("offset_continuity", RAW_TABLE, True, 0, "table is empty")]

    failures = O.offset_gaps([(r["topic"], r["partition"], r["n"], r["lo"], r["hi"]) for r in rows])
    if not failures:
        return [
            _result("offset_continuity", RAW_TABLE, True, 0, f"{len(rows)} partitions gapless")
        ]
    return [
        _result("offset_continuity", f["scope"], False, f["observed"], f["detail"])
        for f in failures
    ]


def audit_duplicates(spark, table: str, keys: list) -> list:
    """No two rows share a table's identifier fields.

    The keys are the `SET IDENTIFIER FIELDS` from lake.sql, passed in rather
    than read from the catalog: the point of the check is to assert the DDL's
    claim, and a check that reads its own expectation out of the thing it is
    checking asserts nothing.

    A failure here means the ingest wrote the same record twice, which would
    mean the offset contract broke. It does **not** mean the venue sent the same
    trade twice — that is a separate number, reported below.
    """
    key_list = ", ".join(keys)
    row = spark.sql(
        f"""
        SELECT count(*) AS dup_keys, coalesce(sum(n - 1), 0) AS extra_rows
        FROM (SELECT {key_list}, count(*) AS n FROM {table} GROUP BY {key_list} HAVING count(*) > 1)
        """
    ).collect()[0]
    extra = int(row["extra_rows"])
    detail = (
        f"({key_list}) is unique"
        if extra == 0
        else f"{row['dup_keys']} duplicated keys, {extra} extra rows on ({key_list})"
    )
    return [_result("duplicate_identifiers", table, extra == 0, extra, detail)]


def audit_venue_replay(spark) -> list:
    """How many logical trades arrived on more than one connection.

    Not a failure, and it is here so that it cannot be mistaken for one.
    Coinbase replays recent `market_trades` when a subscription is
    re-established, so after every reconnect the archive legitimately holds the
    same (exchange, symbol, trade_id) twice under two conn_ids — measured at 956
    such trades in 287,184 over 30 min on 2026-08-26. Those rows are real frames
    that really arrived and the append-only archive keeps both.

    What this row buys: the replay rate becomes a published number instead of
    background noise, so a *change* in it is visible. A jump means reconnect
    churn, which is a capture-tier question.
    """
    row = spark.sql(
        f"""
        SELECT count(*) AS replayed
        FROM (
          SELECT exchange, symbol, trade_id
          FROM {TRADES_TABLE}
          GROUP BY exchange, symbol, trade_id
          HAVING count(DISTINCT conn_id) > 1
        )
        """
    ).collect()[0]
    count = int(row["replayed"])
    return [
        _result(
            "venue_replay",
            TRADES_TABLE,
            True,
            count,
            f"{count} trade ids seen on 2+ conn_ids (venue replay after reconnect, expected)",
        )
    ]


def audit_sequence(spark, table: str, time_column: str) -> list:
    """`seq` never goes backwards within one (exchange, symbol, conn_id).

    Deliberately monotonicity, not `+1` continuity, and the reason is in the
    data rather than in convenience:

      * Kraken v2 publishes no sequence at all and writes 0 — filtered out here.
      * Binance trades write 0 for the same reason; its book stream carries
        `lastUpdateId`, which jumps by however many updates a frame folded in.
      * Coinbase's `sequence_num` is connection-wide across `l2_data`,
        `market_trades` and `heartbeats`, and only two of those three reach
        bronze — so a `+1` check would report a gap for every heartbeat, which
        is a correct capture, not a loss.

    A regression is a different animal: it means frames arrived out of order or
    the stream was silently re-keyed, which is real and is the `lastUpdateId`
    row in docs/architecture/failure-modes.md. Full `+1` continuity is provable
    against `conn_msg_seq` in the raw frames, and that belongs to a replay tool
    (Phase G), not to a nightly SQL pass over bronze.
    """
    row = spark.sql(
        f"""
        SELECT count(*) AS regressions
        FROM (
          SELECT seq, lag(seq) OVER (
                   PARTITION BY exchange, symbol, conn_id ORDER BY {time_column}, seq
                 ) AS previous
          FROM {table} WHERE seq > 0
        )
        WHERE previous IS NOT NULL AND seq < previous
        """
    ).collect()[0]
    count = int(row["regressions"])
    detail = "no sequence regressions" if count == 0 else f"{count} rows where seq < previous seq"
    return [_result("sequence_gaps", table, count == 0, count, detail)]


def _result(check: str, scope: str, passed: bool, observed: int, detail: str) -> dict:
    return {
        "check_name": check,
        "scope": scope,
        "passed": passed,
        "observed": int(observed),
        "detail": detail,
    }


def run_audits(spark, run_ts: datetime) -> list:
    results = []
    results += audit_offset_continuity(spark)
    results += audit_duplicates(spark, TRADES_TABLE, ["exchange", "symbol", "trade_id", "conn_id"])
    results += audit_duplicates(
        spark, BOOK_TABLE, ["exchange", "symbol", "conn_id", "snapshot_ts_ns"]
    )
    results += audit_venue_replay(spark)
    results += audit_sequence(spark, TRADES_TABLE, "exchange_ts")
    results += audit_sequence(spark, BOOK_TABLE, "snapshot_ts")

    frame = spark.createDataFrame(
        [
            Row(
                run_ts=run_ts,
                job=O.JOB_MAINTENANCE,
                check_name=r["check_name"],
                scope=r["scope"],
                passed=r["passed"],
                observed=r["observed"],
                detail=r["detail"],
            )
            for r in results
        ]
    )
    (
        frame.writeTo(CHECKS_TABLE)
        .option(f"snapshot-property.{O.JOB}", O.JOB_MAINTENANCE)
        .append()
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=2, help="compaction window, days back")
    parser.add_argument("--retain-days", type=int, default=7, help="snapshot retention floor")
    parser.add_argument("--audit-only", action="store_true", help="skip compaction and expiry")
    args = parser.parse_args()
    if args.retain_days < 7:
        parser.error("--retain-days below 7 breaks the bronze incremental read; see expire()")

    run_ts = datetime.now(timezone.utc)
    spark = lake_session("k2-lake-maintenance")
    try:
        if not args.audit_only:
            compact(spark, run_ts - timedelta(days=args.days))
            expire(spark, args.retain_days)
        results = run_audits(spark, run_ts)
    finally:
        spark.stop()

    failed = [r for r in results if not r["passed"]]
    print("\naudits:")
    for r in results:
        print("  {} {:<24} {:<44} {}".format(
            "FAIL" if not r["passed"] else "ok  ", r["check_name"], r["scope"], r["detail"]
        ))
    if failed:
        print(f"\n{len(failed)} audit(s) FAILED — see {CHECKS_TABLE}")
        return 1
    print(f"\n{len(results)} audits passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
