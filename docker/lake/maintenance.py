#!/usr/bin/env python3
"""
K2 v3 lake maintenance — compact, expire, audit. Runs nightly under Prefect
(`lake-maintenance-daily`), or by hand:

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --audit-only

**Exit code is the product.** Any failed audit exits non-zero, which fails the
Prefect flow run, which is what `LakeAuditFailed` alerts on. Every check also
lands as a row in `lake.audit.checks` so "when did this last hold" is a query
rather than a log grep — including a check that *raised*, which is why every
call is wrapped: a run that dies with nothing written fires the alert and leaves
the table the runbook points at empty.

**No row is ever deleted here.** Compaction rewrites files, not rows.
`expire_snapshots` drops old *metadata* and the data files that compaction
already superseded — never a file the current snapshot still references.
`remove_orphan_files` deletes objects no snapshot ever referenced, from writes
that crashed before committing, with a 24-hour floor so it cannot race one.
None of the three can remove a row that is in the current snapshot, which is how
`raw.messages` is both "never expired" (requirements clarification Q8) and not
accumulating a snapshot per five minutes forever.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import bronze
from lock import LOCK_PATH, acquire_lock
from pyspark.sql import Row
from pyspark.sql import functions as F

import offsets as O
import wire
from spark_conf import (
    CATALOG,
    MAINTENANCE_DRIVER_MEMORY,
    S3_ENDPOINT,
    S3_PATH_STYLE,
    S3_REGION,
    lake_session,
)

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
    """Binpack `raw.messages`, sort-rewrite the unified and per-venue bronze tables.

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
    # max-concurrent-file-group-rewrites 1 + partial-progress: the default (5
    # groups at once) OOM'd a 768m driver on 2026-08-26 at 22:16Z rewriting
    # raw.kraken rows of up to 5 MB each; one group at a time fits, and partial
    # progress keeps the groups already rewritten when a later one fails.
    _call(
        spark,
        f"CALL {CATALOG}.system.rewrite_data_files("
        f"  table => '{_bare(RAW_TABLE)}',"
        f"  strategy => 'binpack',"
        f"  where => 'kafka_ts >= {_where_ts(since)}',"
        f"  options => map('min-input-files', '5', 'target-file-size-bytes', '268435456',"
        f"                  'max-concurrent-file-group-rewrites', '1',"
        f"                  'partial-progress.enabled', 'true'))",
    )

    per_venue = [(t.table, "recv_ts") for t in bronze.VENUE_TABLES]
    for table, column in [(TRADES_TABLE, "exchange_ts"), (BOOK_TABLE, "snapshot_ts")] + per_venue:
        # No sort_order argument: the tables declare theirs in lake.sql and the
        # procedure uses the declared one. Repeating it here is a second place
        # to get it wrong.
        _call(
            spark,
            f"CALL {CATALOG}.system.rewrite_data_files("
            f"  table => '{_bare(table)}',"
            f"  strategy => 'sort',"
            f"  where => '{column} >= {_where_ts(since)}',"
            f"  options => map('min-input-files', '5', 'target-file-size-bytes', '134217728',"
            f"                  'max-concurrent-file-group-rewrites', '1',"
            f"                  'partial-progress.enabled', 'true'))",
        )


def expire(spark, retain_days: int) -> None:
    """Drop snapshots older than `retain_days`, keeping at least 10.

    Seven days is the floor because that is the window `bronze.*` incremental
    reads need: stage 2 resumes from the snapshot id it last decoded, and
    expiring that snapshot turns the next run into an error. A week of slack
    covers a long outage without unbounded metadata growth.
    """
    older_than = datetime.now(timezone.utc) - timedelta(days=retain_days)
    for table in (RAW_TABLE, TRADES_TABLE, BOOK_TABLE, CHECKS_TABLE, *bronze.TABLES):
        _call(
            spark,
            f"CALL {CATALOG}.system.expire_snapshots("
            f"  table => '{_bare(table)}',"
            f"  older_than => {_ts(older_than)},"
            f"  retain_last => 10)",
        )


def table_location(spark, table: str) -> str:
    """The table's own object-store prefix, from the catalog."""
    for row in spark.sql(f"DESCRIBE TABLE EXTENDED {table}").collect():
        if row["col_name"] == "Location":
            return row["data_type"]
    raise RuntimeError(f"{table} reports no Location")


def file_list_view(spark, location: str, view: str) -> int:
    """Materialise `(file_path, last_modified)` for everything under `location`.

    `remove_orphan_files` lists the prefix through the HADOOP FileSystem by
    default, and this Spark image has no `hadoop-aws` — the procedure answers
    `UnsupportedFileSystemException: No FileSystem for scheme "s3"` and does
    nothing. The alternative to this function is baking hadoop-aws plus a ~190 MB
    aws-java-sdk-bundle into docker/spark/Dockerfile so a second S3 client can
    list what the first one already can.

    So: list with Iceberg's own S3FileIO, which is already on the classpath,
    already speaks to MinIO, and takes the same four properties spark_conf.py
    hands the catalog. `file_list_view` is the procedure's supported way to be
    given a listing rather than to make one.

    # ponytail: the listing lands in the driver before it becomes a DataFrame,
    # so it is bounded by driver memory — roughly 200 bytes per object, i.e. a
    # few hundred MB at ten million files. Past that, page `listPrefix` into
    # Parquet and read it back instead of collecting it.
    """
    jvm = spark._jvm
    props = jvm.java.util.HashMap()
    props.put("s3.endpoint", S3_ENDPOINT)
    props.put("s3.path-style-access", S3_PATH_STYLE)
    props.put("s3.access-key-id", os.environ["MINIO_ROOT_USER"])
    props.put("s3.secret-access-key", os.environ["MINIO_ROOT_PASSWORD"])
    props.put("client.region", S3_REGION)
    io = jvm.org.apache.iceberg.aws.s3.S3FileIO()
    io.initialize(props)
    try:
        rows, listing = [], io.listPrefix(location).iterator()
        while listing.hasNext():
            info = listing.next()
            rows.append((info.location(), datetime.fromtimestamp(info.createdAtMillis() / 1000, timezone.utc)))
    finally:
        io.close()
    spark.createDataFrame(rows, "file_path string, last_modified timestamp").createOrReplaceTempView(view)
    return len(rows)


def remove_orphans(spark, older_than_hours: int) -> None:
    """Delete files under each table's prefix that no snapshot references.

    A crashed or killed write leaves Parquet in the object store that the
    commit never named. Nothing reads it — a file no manifest lists is invisible
    to every reader — but it costs disk forever, and `raw.messages` is never
    expired, so the disk runbook's 80/90% path has nothing else to reclaim.

    `older_than` is clamped to 24 h and that clamp is the safety property, not a
    default. `remove_orphan_files` decides "unreferenced" from the table
    metadata at the instant it runs; a file a *concurrent* writer has staged but
    not yet committed is unreferenced by that definition, and deleting it
    corrupts the commit that was about to name it. A 24-hour floor puts every
    candidate far outside any in-flight write on this stack, where the longest
    job is a one-hour backlog slice (INGEST_TIMEOUT_S in flows/lake_flows.py).
    """
    if older_than_hours < 24:
        raise ValueError(
            f"--orphan-hours {older_than_hours} is below the 24 h floor; "
            "see remove_orphans() for why that floor is a safety property"
        )
    older_than = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    for table in (RAW_TABLE, TRADES_TABLE, BOOK_TABLE, CHECKS_TABLE):
        view = "k2_orphan_candidates"
        listed = file_list_view(spark, table_location(spark, table), view)
        print(f"  {table}: {listed} objects under its prefix")
        _call(
            spark,
            f"CALL {CATALOG}.system.remove_orphan_files("
            f"  table => '{_bare(table)}',"
            f"  older_than => {_ts(older_than)},"
            f"  file_list_view => '{view}')",
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

    **A hole that was already written down is not a finding.** A
    `--accept-data-loss` repair files an `offset_gap` row naming the exact range
    Redpanda evicted, and that hole is permanent — so without netting, this check
    fails on that partition every night forever and `LakeAuditFailed` (critical)
    latches on a loss a person already acknowledged. An alert that can only ever
    fire is an alert nobody reads, which costs more than the check is worth.
    Recorded ranges are therefore netted out; **anything they do not cover still
    fails**, which is the whole point — the check keeps its teeth for new loss on
    a partition that has already lost records once.
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
    recorded = O.recorded_gaps(
        [
            (r["scope"], r["detail"])
            for r in spark.sql(
                f"SELECT scope, detail FROM {CHECKS_TABLE} WHERE check_name = 'offset_gap'"
            ).collect()
        ]
    )
    return [_net_recorded(spark, f, recorded) for f in failures]


def _net_recorded(spark, failure: dict, recorded: list) -> dict:
    """One `offset_gaps` failure, minus the ranges `audit.checks` already holds.

    The aggregate check above counts; it cannot say *where*. So the exact holes
    are read only for a partition already flagged — the nightly healthy path
    still does one group-by and no window function over 40 M rows.

    Two conditions have to hold before a failure is downgraded to a pass, and
    the second one is the subtle one:

      * every hole is inside a recorded range, and
      * the holes account for the whole shortfall.

    `observed` is `missing - duplicated`, so a partition with 100 acknowledged
    missing offsets and 100 rows written twice reports 0 and looks intact. If
    the hole sizes do not add up to `observed` exactly, something other than the
    recorded eviction is going on and the row keeps failing.
    """
    topic, _, partition = failure["scope"].rpartition("/")
    if failure["observed"] <= 0 or not partition.isdigit():
        # Negative is duplication — nothing to net, the contract broke.
        return _result("offset_continuity", failure["scope"], False, failure["observed"], failure["detail"])

    holes = [
        (topic, int(partition), int(r["first_missing"]), int(r["last_missing"]))
        for r in spark.sql(
            f"""
            SELECT prev + 1 AS first_missing, offset - 1 AS last_missing
            FROM (
              SELECT offset, lag(offset) OVER (
                       PARTITION BY topic, partition ORDER BY offset
                     ) AS prev
              FROM {RAW_TABLE} WHERE topic = '{topic}' AND partition = {int(partition)}
            )
            WHERE prev IS NOT NULL AND offset > prev + 1
            ORDER BY first_missing
            """
        ).collect()
    ]
    uncovered = O.uncovered_holes(holes, recorded)
    accounted = sum(last - first + 1 for _, _, first, last in holes)
    if uncovered or accounted != failure["observed"]:
        return _result(
            "offset_continuity",
            failure["scope"],
            False,
            failure["observed"],
            "{}; {} of {} holes are NOT covered by a recorded offset_gap ({})".format(
                failure["detail"], len(uncovered), len(holes), _ranges(uncovered) or "none"
            ),
        )
    return _result(
        "offset_continuity",
        failure["scope"],
        True,
        failure["observed"],
        f"{len(holes)} recorded gaps netted ({_ranges(holes)}); {accounted} records "
        f"missing, all acknowledged by an offset_gap row in {CHECKS_TABLE} — "
        "nothing else is",
    )


def _ranges(holes: list) -> str:
    return ", ".join(f"{first}..{last}" for _, _, first, last in holes)


def audit_duplicates(spark, table: str, keys: list) -> list:
    """No two rows share a table's identifier fields.

    The keys are the `SET IDENTIFIER FIELDS` from lake.sql, passed in rather
    than read from the catalog: the point of the check is to assert the DDL's
    claim, and a check that reads its own expectation out of the thing it is
    checking asserts nothing.

    A failure here means the ingest wrote the same record twice, which would
    mean the offset contract broke. It does **not** mean the venue sent the same
    trade twice — that is a separate number, reported below. That is why the
    trade key carries the source lineage: measured 2026-08-26, Coinbase re-sends
    a trade inside the *same* connection too (5,034 keys, 15 s apart, two
    distinct frames), so no combination of venue fields is unique in an archive
    of frames. Only "which archived record produced this row" is.
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
    such trades in 287,184 over 30 min on 2026-08-26. It also re-sends trades
    inside one connection: the same day, 5,034 Coinbase trade ids arrived twice
    on one conn_id in two distinct `market_trades` frames ~15 s apart (raw
    offsets 9374 and 9772 on trades.coinbase/9 are one such pair). Those rows
    are real frames that really arrived and the append-only archive keeps both.

    What this row buys: the replay rate becomes a published number instead of
    background noise, so a *change* in it is visible. A jump means reconnect
    churn, which is a capture-tier question.
    """
    row = spark.sql(
        f"""
        SELECT count(*) AS replayed,
               coalesce(sum(CASE WHEN conns > 1 THEN 1 ELSE 0 END), 0) AS across_conns
        FROM (
          SELECT exchange, symbol, trade_id, count(DISTINCT conn_id) AS conns
          FROM {TRADES_TABLE}
          GROUP BY exchange, symbol, trade_id
          HAVING count(*) > 1
        )
        """
    ).collect()[0]
    count, across = int(row["replayed"]), int(row["across_conns"])
    return [
        _result(
            "venue_replay",
            TRADES_TABLE,
            True,
            count,
            f"{count} trade ids delivered 2+ times ({across} across reconnects, "
            f"{count - across} within one connection; venue replay, expected)",
        )
    ]


def audit_sequence(spark, table: str) -> list:
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

    Ordered by ARRIVAL — `(recv_ts_ns, conn_msg_seq)` — not by the venue clock.
    Two reasons, and the first one is that the check was previously unable to
    fail: ordering by `(exchange_ts, seq)` sorts any out-of-order pair sharing
    an `exchange_ts` into ascending `seq`, which makes `seq < previous`
    unreachable for exactly the ties a venue produces most of. The second is
    that "frames arrived out of order" is a statement about arrival, and
    `conn_msg_seq` is K2's own strictly increasing frame counter on the
    connection — the only total order in the row that we control.
    """
    row = spark.sql(
        f"""
        SELECT count(*) AS regressions
        FROM (
          SELECT seq, lag(seq) OVER (
                   PARTITION BY exchange, symbol, conn_id
                   ORDER BY recv_ts_ns, conn_msg_seq
                 ) AS previous
          FROM {table} WHERE seq > 0
        )
        WHERE previous IS NOT NULL AND seq < previous
        """
    ).collect()[0]
    count = int(row["regressions"])
    detail = "no sequence regressions" if count == 0 else f"{count} rows where seq < previous seq"
    return [_result("sequence_gaps", table, count == 0, count, detail)]


def audit_unparseable(spark, t: bronze.VenueTable) -> list:
    """Frames bronze.py wrote with their venue columns NULL: the JSON did not parse as the declared shape.

    The decode is PERMISSIVE on purpose (bronze.py): a rejected frame lands with
    lineage and NULL payload columns rather than blocking the snapshot range.
    This is where it becomes a failure — with the offsets, so the frame can be
    read back out of raw.messages and the DDL fixed to match what the venue
    actually sent.
    """
    rows = spark.sql(
        f"SELECT src_partition, src_offset FROM {t.table} WHERE {t.required} IS NULL "
        f"ORDER BY src_partition, src_offset LIMIT 10"
    ).collect()
    if not rows:
        return [_result("bronze_unparseable", t.table, True, 0, "every frame parsed as the declared shape")]
    count = spark.sql(f"SELECT count(*) FROM {t.table} WHERE {t.required} IS NULL").collect()[0][0]
    first = ", ".join(f"{r['src_partition']}/{r['src_offset']}" for r in rows)
    return [
        _result(
            "bronze_unparseable",
            t.table,
            False,
            count,
            f"{count} frame(s) with {t.required} IS NULL; first (partition/offset): {first}. "
            f"Read them back from raw.messages by (src_topic, src_partition, src_offset)",
        )
    ]


# Sample size for the schema-drift audit, as a TABLESAMPLE percentage of the
# venue's last day of raw frames. 0.1 % of a 13 M-frame Kraken day is ~13,000
# frames, which is plenty to see a key the venue added to every frame and not
# enough to see one it adds to one frame in a million — which is what the
# unparseable audit above is for, since a shape change that breaks the parse
# shows up there on every row it touches.
DRIFT_SAMPLE_PERCENT = 0.1


def audit_schema_drift(spark, t: bronze.VenueTable) -> list:
    """Keys the venue now sends that the bronze table does not declare.

    A PERMISSIVE `from_json` silently drops a key it has no column for, which is
    the one way bronze can lose vendor data without any row going NULL. So the
    check reads the *raw* frames, not bronze: a sample of the venue's last day,
    RawMessage-decoded, `json_object_keys` at each path the table declares,
    minus the declared keys. Anything left is a column to add (a schema
    change: /schema-change) and a rebuild of the table.
    """
    from catalog import fetch_schema
    from pyspark.sql.avro.functions import from_avro

    sample = spark.sql(
        f"SELECT schema_id, payload FROM {RAW_TABLE} TABLESAMPLE ({DRIFT_SAMPLE_PERCENT} PERCENT) "
        f"WHERE topic LIKE '%.raw.{t.exchange}' AND schema_id IS NOT NULL "
        f"AND kafka_ts >= current_timestamp() - INTERVAL 1 DAY"
    )
    schema_ids = [r[0] for r in sample.select("schema_id").distinct().collect()]
    if not schema_ids:
        return [_result("bronze_schema_drift", t.table, True, 0, "no raw frames in the last day to sample")]
    seen = {}
    for schema_id in schema_ids:
        frames = sample.where(F.col("schema_id") == schema_id).select(
            from_avro(F.expr(wire.body_expr("payload")), fetch_schema(schema_id)).alias("r")
        )
        frames = frames.where(F.col("r.stream") == t.stream).select(F.col("r.payload").cast("string").alias("p"))
        for path in t.keys:
            expr = "json_object_keys(p)" if path == "$" else f"json_object_keys(get_json_object(p, '{path}'))"
            keys = frames.select(F.explode(F.expr(expr)).alias("k")).distinct().collect()
            seen.setdefault(path, set()).update(r["k"] for r in keys)
    extra = bronze.drift(seen, t.keys)
    if not extra:
        return [_result("bronze_schema_drift", t.table, True, 0, f"sampled {DRIFT_SAMPLE_PERCENT}% of the last day: no undeclared keys")]
    return [
        _result(
            "bronze_schema_drift",
            t.table,
            False,
            sum(len(v) for v in extra.values()),
            "undeclared keys the venue sends: " + "; ".join(f"{p}: {k}" for p, k in extra.items()) + ". Add the column (/schema-change), rebuild the table",
        )
    ]


def _result(check: str, scope: str, passed: bool, observed: int, detail: str) -> dict:
    return {
        "check_name": check,
        "scope": scope,
        "passed": passed,
        "observed": int(observed),
        "detail": detail,
    }


# venue_replay has no pass/fail semantics — it publishes a rate so a *change* in
# it is visible. Counting it as a passing audit inflates the summary line with a
# check that cannot fail, so it is named here and reported separately.
INFORMATIONAL = {"venue_replay"}

# (check name, scope, callable). The scope is here rather than only inside the
# check so that a check which RAISES still has somewhere to file its failure.
AUDITS = (
    ("offset_continuity", RAW_TABLE, audit_offset_continuity),
    (
        "duplicate_identifiers",
        TRADES_TABLE,
        lambda s: audit_duplicates(
            s, TRADES_TABLE, ["exchange", "symbol", "trade_id", "src_topic", "src_partition", "src_offset"]
        ),
    ),
    (
        "duplicate_identifiers",
        BOOK_TABLE,
        lambda s: audit_duplicates(s, BOOK_TABLE, ["exchange", "symbol", "conn_id", "snapshot_ts_ns"]),
    ),
    ("venue_replay", TRADES_TABLE, audit_venue_replay),
    ("sequence_gaps", TRADES_TABLE, lambda s: audit_sequence(s, TRADES_TABLE)),
    ("sequence_gaps", BOOK_TABLE, lambda s: audit_sequence(s, BOOK_TABLE)),
) + tuple(
    row
    for t in bronze.VENUE_TABLES
    for row in (
        ("duplicate_identifiers", t.table, lambda s, t=t: audit_duplicates(s, t.table, list(bronze.IDENTIFIER_FIELDS))),
        ("bronze_unparseable", t.table, lambda s, t=t: audit_unparseable(s, t)),
        ("bronze_schema_drift", t.table, lambda s, t=t: audit_schema_drift(s, t)),
    )
)


def run_audits(spark, run_ts: datetime) -> list:
    results = []
    for name, scope, check in AUDITS:
        try:
            results += check(spark)
        except Exception as exc:  # noqa: BLE001 - a raising check is a finding
            # The row is the product, not the exit code. Letting the exception
            # out means the run dies with nothing durable written, the alert
            # fires, and audit.checks — the table the runbook tells you to
            # query — is silent about the run that fired it.
            results.append(
                _result(name, scope, False, -1, f"check raised {type(exc).__name__}: {exc}")
            )

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
    # The failure count rides in the snapshot summary as well as in the rows.
    # docker/lake/metrics.py then reads k2_lake_audit_failures_total straight
    # off the summary — no table scan, no pyarrow, no credentials beyond the
    # catalog's, in a 128 MB exporter that polls every 30 s.
    failures = sum(1 for r in results if not r["passed"])
    (
        frame.writeTo(CHECKS_TABLE)
        .option(f"snapshot-property.{O.JOB}", O.JOB_MAINTENANCE)
        .option(f"snapshot-property.{O.AUDIT_FAILURES}", str(failures))
        .append()
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=2, help="compaction window, days back")
    parser.add_argument("--retain-days", type=int, default=7, help="snapshot retention floor")
    parser.add_argument(
        "--orphan-hours",
        type=int,
        default=24,
        help="remove_orphan_files horizon, hours back. Floor 24; see remove_orphans()",
    )
    parser.add_argument("--audit-only", action="store_true", help="skip compaction and expiry")
    args = parser.parse_args()
    if args.retain_days < 7:
        parser.error("--retain-days below 7 breaks the bronze incremental read; see expire()")
    if args.orphan_hours < 24:
        parser.error("--orphan-hours below 24 is unsafe against a concurrent write; see remove_orphans()")

    # Blocking: wait out the ingest that may be mid-commit, then keep every
    # ingest tick out of the container until this driver has exited. Held for
    # the life of the process, including --audit-only, which still runs Spark.
    print(f"waiting for {LOCK_PATH}", flush=True)
    lock = acquire_lock(LOCK_PATH, blocking=True)  # noqa: F841 - held until exit
    run_ts = datetime.now(timezone.utc)
    spark = lake_session("k2-lake-maintenance", driver_memory=MAINTENANCE_DRIVER_MEMORY)
    try:
        if not args.audit_only:
            compact(spark, run_ts - timedelta(days=args.days))
            expire(spark, args.retain_days)
            remove_orphans(spark, args.orphan_hours)
        results = run_audits(spark, run_ts)
    finally:
        spark.stop()

    failed = [r for r in results if not r["passed"]]
    informational = [r for r in results if r["check_name"] in INFORMATIONAL]
    print("\naudits:")
    for r in results:
        mark = "FAIL" if not r["passed"] else ("info" if r["check_name"] in INFORMATIONAL else "ok  ")
        print("  {} {:<24} {:<44} {}".format(mark, r["check_name"], r["scope"], r["detail"]))
    if failed:
        print(f"\n{len(failed)} audit(s) FAILED — see {CHECKS_TABLE}")
        return 1
    print(f"\n{len(results) - len(informational)} audits passed, {len(informational)} informational")
    return 0


if __name__ == "__main__":
    sys.exit(main())
