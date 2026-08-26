#!/usr/bin/env python3
"""
K2 v3 lake ingest — Redpanda to Iceberg, in two stages, in one Spark session.

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --stage raw
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
        --end-timestamp 2026-08-27T02:00:00Z          # backlog, one slice at a time
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --probe

**Stage 1 — Kafka to `raw.messages`.** Every one of the nine v3 topics, read as a
bounded batch from the offsets the last ingest committed to `endingOffsets=latest`.
The Kafka value is stored byte for byte, Confluent framing included. Nothing is
parsed, reformatted or validated on the way in; that is what makes this the
system of record rather than a view of one (ADR-018).

**Stage 2 — `raw.messages` to `bronze.*`.** An Iceberg incremental read of the
snapshots stage 1 just added, the Confluent header stripped, the Avro body
decoded against the exact writer schema fetched from the registry by id, in
FAILFAST mode. Only `trades.*` and `book.*` are decoded; `raw.*` frames stay
where they are, verbatim, because reconstructing a book from deltas is a
different job with different failure modes.

**Exactly once, with no watermark table.** Both stages write their position into
the Iceberg snapshot summary in the same commit as the data — `k2.kafka-offsets`
for stage 1, `k2.src-snapshot-id` for stage 2. See docker/lake/offsets.py for
why that is not a stylistic choice.

Re-running with no new data is a no-op: stage 1 reads an empty offset range and
commits nothing, stage 2 reads an empty snapshot range and commits nothing. That
is what `scripts/lake-verify.sh` asserts.

**One at a time.** Exactly-once is a property of a *sequence* of runs, not of a
pair of concurrent ones: two appends racing each other both commit (Iceberg
retries the loser onto the new base) and the second one's offsets overwrite the
first's. An exclusive `flock` at the top of `main()` makes that unrepresentable
on every dispatch path — cron, Prefect, runbook, chaos script — rather than only
on the one Prefect's `concurrency_limit=1` covers.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import reduce

from pyspark import StorageLevel
from pyspark.sql import DataFrame, Row
from pyspark.sql import functions as F
from pyspark.sql.avro.functions import from_avro

import offsets as O
import wire
from spark_conf import CATALOG, KAFKA_BROKERS, SCHEMA_REGISTRY_URL, lake_session

RAW_TABLE = f"{CATALOG}.raw.messages"
TRADES_TABLE = f"{CATALOG}.bronze.trades"
BOOK_TABLE = f"{CATALOG}.bronze.book_snapshots_l2"
CHECKS_TABLE = f"{CATALOG}.audit.checks"

# One ingest at a time, per container, on every dispatch path.
#
# Prefect's `concurrency_limit=1` only gates runs Prefect launched, and the
# runbooks, the chaos scripts and `make lake-verify` all dispatch by hand while
# the */5 cron is armed. Two concurrent `writeTo(...).append()` calls do NOT
# conflict: measured on a scratch table with this DDL's
# `commit.retry.num-retries=10`, the loser raised CatalogCommitConflicts,
# Iceberg re-applied it on the new base, and BOTH landed — 10 rows from two
# identical 5-row appends. The offsets live in the snapshot summary, so the
# second commit also overwrites the first one's bookkeeping.
#
# An exclusive non-blocking flock is the whole guard: the second run exits
# non-zero immediately instead of duplicating the first one's work.
LOCK_PATH = os.environ.get("K2_LAKE_LOCK", "/tmp/k2-lake-ingest.lock")  # noqa: S108

# Same three names, same prefix and same default as docker/redpanda/init.sh.
EXCHANGES = os.environ.get("K2_EXCHANGES", "binance,kraken,coinbase").split(",")
V3_PREFIX = os.environ.get("K2_V3_PREFIX", "market.crypto.v3")


def topics(kind: str) -> list:
    return [f"{V3_PREFIX}.{kind}.{ex}" for ex in EXCHANGES]


ALL_TOPICS = topics("raw") + topics("trades") + topics("book")


# ── catalog helpers ─────────────────────────────────────────────────────────


def snapshot_history(spark, table: str) -> list:
    """`[(committed_at, summary)]` for one table, newest last. Empty if unwritten."""
    rows = spark.sql(
        f"SELECT committed_at, summary FROM {table}.snapshots"
    ).collect()
    return [(r["committed_at"], r["summary"] or {}) for r in rows]


def current_snapshot_id(spark, table: str):
    """The snapshot the `main` branch points at — the authoritative pointer.

    Not `ORDER BY committed_at DESC LIMIT 1`: `<table>.snapshots` lists every
    snapshot in the metadata, and the newest by commit time is not necessarily
    the current one after a rollback, a cherry-pick or a branch write.
    `<table>.refs` is where Iceberg records which one is live.
    """
    rows = spark.sql(
        f"SELECT snapshot_id FROM {table}.refs WHERE name = 'main'"
    ).collect()
    return rows[0][0] if rows else None


def topic_partitions(spark, topic_list: list) -> dict:
    """`{topic: partition count}` straight from the broker.

    Over the Kafka AdminClient already on the Spark classpath
    (kafka-clients-3.4.1.jar, pulled in by spark-sql-kafka-0-10), through the
    driver's JVM gateway — no new Python dependency for one metadata call.

    This used to be a `--partitions` flag. See docker/lake/offsets.py for why a
    number on a command line is the wrong place for it.
    """
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    props.put("bootstrap.servers", KAFKA_BROKERS)
    admin = jvm.org.apache.kafka.clients.admin.AdminClient.create(props)
    try:
        names = jvm.java.util.ArrayList()
        for topic in topic_list:
            names.add(topic)
        described = admin.describeTopics(names).all().get()
        return {topic: described.get(topic).partitions().size() for topic in topic_list}
    finally:
        admin.close()


class UnresolvableSchema(Exception):
    """The registry does not serve this schema id."""


def fetch_schema(schema_id: int) -> str:
    """The registered Avro schema for `schema_id`, as a JSON string.

    By id, not by subject: a payload names its own writer schema and that is the
    only schema that can decode it. Resolving the subject's *latest* version
    instead would decode last week's records against this week's schema and
    succeed at it, which is the silent-corruption path Avro's id exists to close.
    """
    url = "{}/schemas/ids/{}".format(SCHEMA_REGISTRY_URL.rstrip("/"), schema_id)
    try:
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed internal host
            return json.load(response)["schema"]
    except urllib.error.HTTPError as exc:
        # 404 is a real state, not a bug: a record framed with an id this
        # registry has never held. Raising it as its own type is what lets
        # stage 2 skip that id and file an audit row instead of dying on every
        # cycle for as long as the record stays in the archive — which, since
        # raw.messages is never expired, is forever.
        raise UnresolvableSchema(f"schema id {schema_id}: {exc}") from exc


# ── stage 1: Kafka -> raw.messages ──────────────────────────────────────────


def stage_raw(spark, ingest_ts: datetime, end_timestamp: str) -> int:
    """Append one bounded Kafka batch to `raw.messages`. Returns rows written."""
    committed = {}
    previous = O.latest_summary(snapshot_history(spark, RAW_TABLE), O.JOB_INGEST)
    if previous and O.KAFKA_OFFSETS in previous:
        committed = O.decode(previous[O.KAFKA_OFFSETS])
        print(f"resuming from {sum(len(p) for p in committed.values())} committed partitions")
    else:
        print("no prior ingest snapshot — starting from the beginning of every topic")

    starting = O.next_starting_offsets(committed, topic_partitions(spark, ALL_TOPICS))

    reader = (
        spark.read.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BROKERS)
        .option("subscribe", ",".join(ALL_TOPICS))
        .option("startingOffsets", O.encode(starting))
        .option("includeHeaders", "true")
        # A committed offset the broker no longer holds means the topic was
        # truncated past what the lake ingested — a permanent hole. Failing is
        # the only honest response: the alternative silently skips forward and
        # leaves a gap the continuity audit then reports days later.
        .option("failOnDataLoss", "true")
    )
    end_ms = _epoch_ms(end_timestamp) if end_timestamp else 0
    if end_timestamp:
        reader = reader.option("endingTimestamp", str(end_ms))
    else:
        reader = reader.option("endingOffsets", "latest")

    loaded = reader.load()
    if end_timestamp:
        # `endingTimestamp` is a REQUEST, not a bound. Spark's
        # `fetchSpecificTimestampBasedOffsets(..., isStartingOffsets=false)`
        # falls through to `KafkaOffsetRangeLimit.LATEST` for any partition with
        # no offset at or after the instant, and `latest` moves. Measured on
        # market.crypto.v3.trades.kraken with one FIXED window whose end was 10
        # minutes in the future: the same read returned 696 rows, then 888 rows
        # 45 seconds later.
        #
        # In that particular case the extra records were all still older than
        # the requested instant — `latest` is at or below the request precisely
        # when the fallback fires — so the read was unpinned rather than wrong.
        # What it leaves open is the plan-time gap: Spark resolves the timestamp
        # first and the LATEST sentinel second, so a record arriving between the
        # two lands past the requested instant, its offset is committed, and the
        # next run with the same `--end-timestamp` starts beyond the end it
        # resolves. This filter closes that for one line and makes
        # `--end-timestamp` mean the same thing on every partition regardless of
        # what Kafka resolved.
        #
        # `endingTimestamp` stays: it is what keeps a backlog slice from reading
        # to `latest` on the partitions it *can* bound. The filter alone would
        # be correct and would read the whole topic to discard most of it.
        loaded = loaded.where(F.col("timestamp") <= F.timestamp_millis(F.lit(end_ms)))

    rows = loaded.select(
        F.col("topic"),
        F.col("partition"),
        F.col("offset"),
        F.col("timestamp").alias("kafka_ts"),
        F.lit(ingest_ts).alias("ingest_ts"),
        F.col("key"),
        # NULL where the payload is not Confluent-framed. Nothing is dropped:
        # the bytes are archived either way and stage 2 skips them, so a foreign
        # producer on a v3 topic shows up as an audit finding rather than as a
        # crashed ingest that blocks every following run on the same record.
        #
        # The guard lives in wire.py so this and `wire.parse_confluent` reject
        # the same three things. It used to check the magic byte only, which
        # let a 3-byte frame through with a fabricated schema id.
        F.expr(wire.schema_id_guarded_expr("value")).alias("schema_id"),
        F.col("value").alias("payload"),
        # A duplicate header key would throw here under Spark's default
        # mapKeyDedupPolicy, on the *archive* write — one foreign producer and
        # raw.messages is blocked at that offset forever. spark_conf.py sets
        # LAST_WIN; capture sets exactly one header (recv_ts_ns,
        # services/capture-rust/src/sink.rs) so there is nothing of ours to lose.
        F.map_from_entries(
            F.expr("transform(headers, h -> struct(h.key AS key, h.value AS value))")
        ).alias("headers"),
    )

    # DISK_ONLY, not a second Kafka read. The end offsets have to describe
    # exactly the records that got written, and `endingOffsets=latest` resolves
    # afresh on every read — so re-reading to compute them would resolve a later
    # `latest` than the one that was written and commit offsets for records that
    # are not in the table.
    rows = rows.persist(StorageLevel.DISK_ONLY)
    try:
        bounds = (
            rows.groupBy("topic", "partition")
            .agg(
                F.max("offset").alias("max_offset"),
                F.max("kafka_ts").alias("max_kafka_ts"),
                F.count(F.lit(1)).alias("n"),
            )
            .collect()
        )
        if not bounds:
            print("stage 1: no new records")
            return 0

        produced = O.end_offsets([(r["topic"], r["partition"], r["max_offset"]) for r in bounds])
        merged = O.merge_committed(committed, produced)
        max_kafka_ts = max(r["max_kafka_ts"] for r in bounds)
        written = sum(r["n"] for r in bounds)

        # Printed before the commit, not after, and scripts/chaos/
        # lake-ingest-kill.sh greps for it: a SIGKILL that lands before this
        # line killed a Kafka read and proves nothing about the commit.
        print(f"stage 1: committing {written} rows", flush=True)
        (
            rows.writeTo(RAW_TABLE)
            .option(f"snapshot-property.{O.JOB}", O.JOB_INGEST)
            .option(f"snapshot-property.{O.KAFKA_OFFSETS}", O.encode(merged))
            .option(f"snapshot-property.{O.MAX_KAFKA_TS}", max_kafka_ts.isoformat())
            .append()
        )
        print(f"stage 1: {written} rows -> {RAW_TABLE} (max kafka_ts {max_kafka_ts})")
        return written
    finally:
        rows.unpersist()


def _epoch_ms(value: str) -> int:
    """Accept epoch millis or an ISO-8601 instant for --end-timestamp."""
    if value.isdigit():
        return int(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


# ── stage 2: raw.messages -> bronze.* ───────────────────────────────────────

# (topic kind, target table, projection). Two entries, one loop — the alternative
# is the same fifteen lines twice with `trade` swapped for `book`.
_TRADE_COLUMNS = [
    "exchange",
    "symbol",
    "canonical_symbol",
    "trade_id",
    "side",
    "exchange_ts",
    "recv_ts_ns",
    "seq",
    "conn_id",
    "conn_msg_seq",
]


def _project_trades(df: DataFrame) -> DataFrame:
    return df.select(
        *[F.col(f"d.{c}").alias(c) for c in _TRADE_COLUMNS[:4]],
        F.expr(wire.fixed_point_expr("d.price")).alias("price"),
        F.expr(wire.fixed_point_expr("d.qty")).alias("qty"),
        *[F.col(f"d.{c}").alias(c) for c in _TRADE_COLUMNS[4:]],
        F.col("topic").alias("src_topic"),
        F.col("partition").alias("src_partition"),
        F.col("offset").alias("src_offset"),
        F.col("ingest_ts"),
    )


def _levels_expr(px: str, qty: str) -> str:
    """`array<struct<px, qty>>` from the wire's two parallel int64 arrays."""
    return (
        "transform(arrays_zip(d.{px}, d.{qty}), lvl -> struct("
        "  {px_dec} AS px,"
        "  {qty_dec} AS qty))"
    ).format(
        px=px,
        qty=qty,
        px_dec=wire.fixed_point_expr(f"lvl.{px}"),
        qty_dec=wire.fixed_point_expr(f"lvl.{qty}"),
    )


def _project_book(df: DataFrame) -> DataFrame:
    return df.select(
        F.col("d.exchange").alias("exchange"),
        F.col("d.symbol").alias("symbol"),
        F.col("d.canonical_symbol").alias("canonical_symbol"),
        F.col("d.depth").alias("depth"),
        F.col("d.seq").alias("seq"),
        F.col("d.checksum_ok").alias("checksum_ok"),
        F.expr(_levels_expr("bid_px", "bid_qty")).alias("bids"),
        F.expr(_levels_expr("ask_px", "ask_qty")).alias("asks"),
        F.col("d.exchange_ts").alias("exchange_ts"),
        F.col("d.recv_ts_ns").alias("recv_ts_ns"),
        F.col("d.snapshot_ts_ns").alias("snapshot_ts_ns"),
        # Nanoseconds to a microsecond TIMESTAMP so the table can partition and
        # range-scan on it. snapshot_ts_ns above stays the authoritative value —
        # this is the queryable projection of it, not a replacement.
        F.expr("timestamp_micros(d.snapshot_ts_ns div 1000)").alias("snapshot_ts"),
        F.col("d.conn_id").alias("conn_id"),
        F.col("d.conn_msg_seq").alias("conn_msg_seq"),
        F.col("topic").alias("src_topic"),
        F.col("partition").alias("src_partition"),
        F.col("offset").alias("src_offset"),
        F.col("ingest_ts"),
    )


BRONZE = (
    ("trades", TRADES_TABLE, _project_trades),
    ("book", BOOK_TABLE, _project_book),
)


def stage_bronze(spark, raw_snapshot_id, run_ts: datetime) -> int:
    """Decode the new `raw.messages` snapshots into `bronze.*`. Returns rows written."""
    if raw_snapshot_id is None:
        print("stage 2: raw.messages has no snapshots yet")
        return 0

    total = 0
    for kind, table, project in BRONZE:
        previous = O.latest_summary(snapshot_history(spark, table), O.JOB_DECODE)
        start = previous.get(O.SRC_SNAPSHOT_ID) if previous else None

        # Stage 1 added nothing, so this table is already level with the archive.
        # Guarding here rather than letting the read fail: an incremental scan
        # whose start equals its end raises "not a parent ancestor of end
        # snapshot", which reads like corruption and means "up to date".
        if start and str(start) == str(raw_snapshot_id):
            print(f"stage 2: {table} is level with raw.messages, nothing to decode")
            continue

        reader = spark.read.format("iceberg")
        if start:
            # Incremental: (start, end], start exclusive. Iceberg fails loudly if
            # `start` is no longer an ancestor of `end`, which is what happens
            # when snapshot expiry outruns this job — a real condition worth an
            # error rather than a silent full re-read.
            reader = reader.option("start-snapshot-id", start).option(
                "end-snapshot-id", raw_snapshot_id
            )
        else:
            # First run for this table: the whole archive as of `raw_snapshot_id`.
            reader = reader.option("snapshot-id", raw_snapshot_id)

        source = reader.load(RAW_TABLE).where(
            F.col("topic").isin(topics(kind)) & F.col("schema_id").isNotNull()
        )
        # Three passes read this otherwise — the distinct() below, the count()
        # and the append() — each one a full scan of the incremental range.
        source = source.persist(StorageLevel.DISK_ONLY)
        try:
            total += _decode_into(spark, source, table, project, raw_snapshot_id, run_ts)
        finally:
            source.unpersist()
    return total


def _decode_into(spark, source, table: str, project, raw_snapshot_id, run_ts) -> int:
    schema_ids = [r[0] for r in source.select("schema_id").distinct().collect()]
    if not schema_ids:
        print(f"stage 2: nothing new for {table}")
        return 0

    # One decode per writer schema, then union. Today every subject has one
    # version so this loop runs once; it is a loop because the day a schema
    # evolves, a batch spans both versions and decoding it with either one
    # alone is wrong.
    parts, unresolvable = [], []
    for schema_id in sorted(schema_ids):
        try:
            schema = fetch_schema(schema_id)
        except UnresolvableSchema as exc:
            # Skip the id, keep the rest of the batch, and leave the reason in
            # audit.checks. Raising here would kill this run and every run
            # after it: the record is already in raw.messages and stage 2
            # re-reads the same snapshot range until it succeeds, so one
            # unregistered id would be a permanent outage.
            print(f"stage 2: SKIPPING {exc} — filing an audit row")
            unresolvable.append((schema_id, str(exc)))
            continue
        decoded = source.where(F.col("schema_id") == schema_id).withColumn(
            "d",
            from_avro(
                F.expr(wire.body_expr("payload")),
                schema,
                # FAILFAST: a body that does not decode is corruption, and a
                # PERMISSIVE null row would put a silently empty trade into
                # a table whose whole purpose is to be trustworthy.
                {"mode": "FAILFAST"},
            ),
        )
        parts.append(project(decoded))

    for schema_id, detail in unresolvable:
        write_audit_row(
            spark,
            run_ts,
            "unresolvable_schema_id",
            f"{table}/schema_id={schema_id}",
            passed=False,
            observed=schema_id,
            detail=detail,
        )
    if not parts:
        return 0

    out = reduce(DataFrame.unionByName, parts).persist(StorageLevel.DISK_ONLY)
    try:
        written = out.count()
        (
            out.writeTo(table)
            .option(f"snapshot-property.{O.JOB}", O.JOB_DECODE)
            .option(f"snapshot-property.{O.SRC_SNAPSHOT_ID}", str(raw_snapshot_id))
            .append()
        )
    finally:
        out.unpersist()
    print(
        f"stage 2: {written} rows -> {table} (schema ids {sorted(schema_ids)}, src snapshot {raw_snapshot_id})"
    )
    return written


def write_audit_row(spark, run_ts, check_name, scope, passed, observed, detail) -> None:
    """One row into `audit.checks`, from a job that is not the nightly audit.

    Same table, `job='ingest'`, so "what did the pipeline find and when" stays
    one query. Best-effort: a finding that cannot be recorded must not become a
    second failure on top of the one it was reporting.
    """
    try:
        (
            spark.createDataFrame(
                [
                    Row(
                        run_ts=run_ts,
                        job="ingest",
                        check_name=check_name,
                        scope=scope,
                        passed=passed,
                        observed=int(observed),
                        detail=detail,
                    )
                ]
            )
            .writeTo(CHECKS_TABLE)
            .option(f"snapshot-property.{O.JOB}", "ingest")
            .append()
        )
    except Exception as exc:  # noqa: BLE001 - the finding already printed above
        print(f"stage 2: could not write the audit row ({exc})")


# ── probe ───────────────────────────────────────────────────────────────────


def probe(spark, window_seconds: int = 120) -> int:
    """Report the framing of one recent record per topic. Writes nothing.

    The check to run before the first ingest, and the first thing to run when a
    decode fails: it answers "is this topic carrying what the lake thinks it is"
    without a table, a commit, or a five-minute wait for the next scheduled run.

    Two bounds keep it a diagnostic rather than a job. It reads only the last
    `window_seconds`, and it pulls the first 5 bytes of each value rather than
    the value — a Coinbase level2 snapshot is 5.2 MB and collecting whole
    payloads for every topic is how the driver dies (which it did, first try).
    """
    since_ms = int((datetime.now(timezone.utc).timestamp() - window_seconds) * 1000)
    sample = (
        spark.read.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BROKERS)
        .option("subscribe", ",".join(ALL_TOPICS))
        .option("startingTimestamp", str(since_ms))
        # A partition with no record inside the window has no offset matching
        # the timestamp, and the default is to fail the whole read on it. `latest`
        # starts that one partition at its end instead — an empty result for a
        # quiet partition, which is the correct answer, not an error.
        .option("startingOffsetsByTimestampStrategy", "latest")
        .option("endingOffsets", "latest")
        # A diagnostic must report on the topics it can read rather than refuse
        # to run because one of them rolled a segment. The ingest path keeps
        # failOnDataLoss=true, where it means something.
        .option("failOnDataLoss", "false")
        .load()
        .select(
            "topic",
            "offset",
            F.expr(f"substring(value, 1, {wire.HEADER_BYTES})").alias("head"),
            F.length("value").alias("bytes"),
        )
        .dropDuplicates(["topic"])
    )
    seen, bad = {}, 0
    for row in sample.collect():
        try:
            schema_id, _ = wire.parse_confluent(bytes(row["head"]))
            seen[row["topic"]] = "schema id {}, {} byte record".format(schema_id, row["bytes"])
        except wire.BadFrame as exc:
            bad += 1
            seen[row["topic"]] = "NOT FRAMED at offset {}: {}".format(row["offset"], exc)
    print(f"last {window_seconds}s:")
    for topic in ALL_TOPICS:
        print("  {:<40} {}".format(topic, seen.get(topic, "no records")))
    return 1 if bad else 0


def _acquire_lock(path: str):
    """Exclusive, non-blocking. Returns the held handle, or None if another
    ingest holds it. The handle is returned so the caller keeps it open — the
    lock dies with the file descriptor, and with the process, so a SIGKILL
    releases it without leaving a stale lock behind."""
    handle = open(path, "w")  # noqa: SIM115 - held for the life of the run
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("all", "raw", "bronze"), default="all")
    parser.add_argument(
        "--end-timestamp",
        default="",
        help="stop stage 1 at this instant (ISO-8601 or epoch ms) instead of at latest",
    )
    parser.add_argument("--probe", action="store_true", help="report topic framing, write nothing")
    args = parser.parse_args()

    ingest_ts = datetime.now(timezone.utc)

    # --probe writes nothing, so it does not contend with a running ingest.
    lock = None
    if not args.probe:
        lock = _acquire_lock(LOCK_PATH)
        if lock is None:
            print(
                f"another ingest holds {LOCK_PATH} — refusing to run a second one.\n"
                "Two concurrent appends both commit and both write offsets; see LOCK_PATH above.",
                file=sys.stderr,
            )
            return 2

    spark = lake_session("k2-lake-ingest")
    try:
        if args.probe:
            return probe(spark)
        if args.stage in ("all", "raw"):
            stage_raw(spark, ingest_ts, args.end_timestamp)
        if args.stage in ("all", "bronze"):
            stage_bronze(spark, current_snapshot_id(spark, RAW_TABLE), ingest_ts)
    finally:
        spark.stop()
        if lock is not None:
            lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
