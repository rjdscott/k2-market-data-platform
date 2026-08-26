#!/usr/bin/env python3
"""
K2 v3 lake ingest — Redpanda to Iceberg, in two stages, in one Spark session.

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --stage raw
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
        --end-timestamp 2026-08-27T02:00:00Z          # backlog, one slice at a time
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py \
        --max-offsets-per-partition 200000            # drain a backlog faster
    docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py --probe

**Stage 1 — Kafka to `raw.messages`.** Every one of the nine v3 topics, read as a
batch whose offsets are pinned on both ends before the read: from what the last
ingest committed, to `min(latest, start + --max-offsets-per-partition)` per
partition. The Kafka value is stored byte for byte, Confluent framing included.
Nothing is parsed, reformatted or validated on the way in; that is what makes
this the system of record rather than a view of one (ADR-018).

**Bounded, and nothing payload-bearing is cached.** Both properties are the same
fix. An unbounded first run over a 48-hour retention put 41.5 M records through a
`persist(DISK_ONLY)` — which serialises via the in-memory columnar cache — and
the driver died on `java.lang.OutOfMemoryError` (2026-08-26). Pinning the end
offsets in pure code (`offsets.bounded_offsets`) means nothing has to be cached
to know what a run consumed, and it caps what one run can pull.

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
# the 5-minute cron is armed. Two concurrent `writeTo(...).append()` calls do NOT
# conflict: measured on a scratch table with this DDL's
# `commit.retry.num-retries=10`, the loser raised CatalogCommitConflicts,
# Iceberg re-applied it on the new base, and BOTH landed — 10 rows from two
# identical 5-row appends. The offsets live in the snapshot summary, so the
# second commit also overwrites the first one's bookkeeping.
#
# An exclusive non-blocking flock is the whole guard: the second run exits
# non-zero immediately instead of duplicating the first one's work.
LOCK_PATH = os.environ.get("K2_LAKE_LOCK", "/tmp/k2-lake-ingest.lock")  # noqa: S108

# How far past its start offset one run may read each partition. The ceiling on
# what a single ingest can pull, and therefore on how long it runs and how much
# it writes — 108 partitions x this, not "however much arrived".
#
# It exists because a first run has no other bound. With the offsets committed
# by the previous run there is nothing to read but five minutes of arrivals; with
# no prior snapshot the range is the whole retention, which on 2026-08-26 was
# 41.5 M records / 9.5 GB across the nine v3 topics, and the run died. The drain
# is now deterministic: each run takes at most this many offsets per partition
# and commits them, so a backlog empties over a predictable number of runs
# instead of in one that cannot finish.
#
# 50,000 x 108 partitions is ~5.4 M records upper bound per run; the live
# measurement of what that costs in driver RSS and wall time is in
# docker/lake/README.md. Lower it if a run cannot finish inside
# `INGEST_TIMEOUT_S`; raise it to drain a backlog faster on an idle host. 0
# disables the bound entirely, which is only ever right when a person is
# watching it.
MAX_OFFSETS_PER_PARTITION = int(os.environ.get("K2_LAKE_MAX_OFFSETS_PER_PARTITION", "50000"))

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


def broker_offsets(spark, topic_list: list, at_timestamp_ms: int = 0) -> tuple:
    """`(earliest, latest, until)` as the broker reports them, per partition.

    Each is `{topic: {partition: offset}}`; `until` is None unless
    `at_timestamp_ms` is given, in which case it holds the offset of the first
    record at or after that instant (Kafka's -1 where there is none).

    Over the Kafka AdminClient already on the Spark classpath
    (kafka-clients-3.4.1.jar, pulled in by spark-sql-kafka-0-10), through the
    driver's JVM gateway — no new Python dependency for three metadata calls.

    **The offsets a run will consume are decided from this, before Spark reads a
    byte.** That is what lets `bounded_offsets` pin `endingOffsets` instead of
    resolving `latest` inside the read, and a pinned range is what removed the
    `persist(DISK_ONLY)` that killed the driver — see docker/lake/offsets.py.

    This used to be a `--partitions` flag. See docker/lake/offsets.py for why a
    number on a command line is the wrong place for it.
    """
    jvm = spark._jvm
    admin_pkg = jvm.org.apache.kafka.clients.admin
    props = jvm.java.util.Properties()
    props.put("bootstrap.servers", KAFKA_BROKERS)
    admin = admin_pkg.AdminClient.create(props)
    try:
        names = jvm.java.util.ArrayList()
        for topic in topic_list:
            names.add(topic)
        described = admin.describeTopics(names).all().get()
        counts = {topic: described.get(topic).partitions().size() for topic in topic_list}

        def list_offsets(spec) -> dict:
            """One `listOffsets` round trip for every partition, under one spec."""
            request = jvm.java.util.HashMap()
            for topic, count in counts.items():
                for partition in range(count):
                    request.put(jvm.org.apache.kafka.common.TopicPartition(topic, partition), spec())
            answer = admin.listOffsets(request).all().get()
            return {
                topic: {
                    partition: answer.get(
                        jvm.org.apache.kafka.common.TopicPartition(topic, partition)
                    ).offset()
                    for partition in range(count)
                }
                for topic, count in counts.items()
            }

        return (
            list_offsets(admin_pkg.OffsetSpec.earliest),
            list_offsets(admin_pkg.OffsetSpec.latest),
            list_offsets(lambda: admin_pkg.OffsetSpec.forTimestamp(at_timestamp_ms))
            if at_timestamp_ms
            else None,
        )
    finally:
        admin.close()


def added_records(spark, table: str) -> int:
    """Rows the table's current snapshot added, from Iceberg's own summary.

    Not `df.count()`. A count on the DataFrame that was just written is a second
    full evaluation of it — for stage 1 a second read of every Kafka record, for
    stage 2 a second Avro decode of the whole range — and the reflex fix for
    that (cache it first) is what put gigabytes of 5.2 MB payload rows into the
    driver heap. Iceberg already counted the rows while committing them; the
    number is a metadata read away and it describes the commit rather than the
    plan that produced it.
    """
    snapshot_id = current_snapshot_id(spark, table)
    if snapshot_id is None:
        return 0
    rows = spark.sql(
        f"SELECT summary FROM {table}.snapshots WHERE snapshot_id = {snapshot_id}"
    ).collect()
    return int((rows[0][0] or {}).get("added-records", 0)) if rows else 0


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


def stage_raw(spark, ingest_ts: datetime, end_timestamp: str, max_per_partition: int) -> int:
    """Append one bounded Kafka batch to `raw.messages`. Returns rows written."""
    committed = {}
    previous = O.latest_summary(snapshot_history(spark, RAW_TABLE), O.JOB_INGEST)
    if previous and O.KAFKA_OFFSETS in previous:
        committed = O.decode(previous[O.KAFKA_OFFSETS])
        print(f"resuming from {sum(len(p) for p in committed.values())} committed partitions")
    else:
        print("no prior ingest snapshot — starting from the beginning of every topic")

    end_ms = _epoch_ms(end_timestamp) if end_timestamp else 0
    earliest, latest, until = broker_offsets(spark, ALL_TOPICS, end_ms)
    starting = O.next_starting_offsets(committed, {t: len(p) for t, p in earliest.items()})
    starts, ends, backlog = O.bounded_offsets(starting, earliest, latest, max_per_partition, until)

    # What this run is choosing NOT to read, per topic. On a caught-up 5-minute
    # cycle every number is 0; on a cold start it is the drain, and it has to
    # fall on every run or the bound is too small for the arrival rate. The same
    # numbers ride on the commit as `k2.kafka-backlog` and come back out as
    # `k2_lake_ingest_backlog_offsets`.
    for topic in sorted(backlog):
        print(f"stage 1: {topic} backlog remaining {backlog[topic]}")

    # Decided in arithmetic, before Spark opens a connection: an empty range on
    # every partition is "no new records", and there is nothing to read, plan or
    # commit. scripts/lake-verify.sh asserts this line on its second cycle.
    if starts == ends:
        print("stage 1: no new records")
        return 0

    loaded = (
        spark.read.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BROKERS)
        .option("subscribe", ",".join(ALL_TOPICS))
        .option("startingOffsets", O.encode(starts))
        # Explicit on both ends, never `latest` and never `endingTimestamp`.
        # Both of those resolve *inside* the read, so the range a second
        # evaluation sees is not the range the first one saw — which is why the
        # offsets used to have to be derived from the rows, why the rows had to
        # be cached to derive them, and ultimately why the driver ran out of
        # heap. Pinned offsets make the plan reproducible, so anything can be
        # evaluated twice and the committed offsets still describe exactly what
        # was written.
        #
        # `--end-timestamp` is resolved to offsets by the broker in
        # `broker_offsets` instead. That also closes a hole `endingTimestamp`
        # had: Kafka timestamps are not monotonic within a partition, so
        # filtering by timestamp dropped records under a committed end offset
        # and never archived them. An offset range keeps every record it spans.
        .option("endingOffsets", O.encode(ends))
        .option("includeHeaders", "true")
        # A committed offset the broker no longer holds means the topic was
        # truncated past what the lake ingested — a permanent hole. Failing is
        # the only honest response: the alternative silently skips forward and
        # leaves a gap the continuity audit then reports days later.
        .option("failOnDataLoss", "true")
        .load()
    )

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

    # NOTHING payload-bearing is cached here, and that is the whole memory
    # story. `persist(DISK_ONLY)` is not the cheap spill it reads as: Spark
    # serialises through the in-memory columnar cache first, 10,000 rows to a
    # batch, and a batch of 10,000 Coinbase level2 frames at up to 5.2 MB each is
    # tens of gigabytes of heap before a single byte reaches disk. That is the
    # OutOfMemoryError the first cold start died on
    # (BasicColumnBuilder.appendFrom, 2026-08-26).
    #
    # Two things replaced it. The offsets come from `bounded_offsets` above
    # rather than from the rows, and the row count comes from the commit's own
    # summary rather than from a `count()` — so the only thing that ever walks
    # the payload column is the write itself.
    #
    # The newest record's timestamp is the exception that could not be got any
    # other way: it rides on the commit, so it has to be known before the commit,
    # and the broker has no API for "the timestamp at this offset". So it is a
    # second pass over the same PINNED range — safe precisely because the range
    # is pinned, which is the whole point of deciding the offsets up front. It
    # reads `loaded`, not `rows`, and projects the one column: neither the
    # payload nor the header map is planned at all. (`timestamp` is the Kafka
    # source's own name for what `rows` renames to `kafka_ts`.)
    #
    # ponytail: that second pass re-fetches the batch's bytes from Redpanda,
    # which is I/O, not heap, and local — ~16 MB at a 5-minute cadence. If the
    # double read ever shows up in the ingest duration, read only the last
    # offset of each partition instead (`ends[p] - 1`) and accept a max that is
    # off by the partition's timestamp disorder.
    max_kafka_ts = loaded.select(F.max("timestamp").alias("m")).collect()[0]["m"]
    if max_kafka_ts is None:
        # A non-empty offset range that yields no record. `failOnDataLoss=true`
        # normally raises before this, so reaching it means something stranger
        # than truncation — say the range is entirely transaction markers.
        # Committing would advance past records nothing archived; not
        # committing costs one wasted run and leaves the same range for the next.
        print(f"stage 1: {sum(len(p) for p in ends.values())} partitions in range "
              "returned no records — not committing")
        return 0

    # Printed before the commit, not after, and scripts/chaos/
    # lake-ingest-kill.sh greps for it: a SIGKILL that lands before this line
    # killed a Kafka read and proves nothing about the commit. The count is not
    # known yet — Iceberg reports it after the append — so what is printed is
    # the range, which is the thing that was decided up front anyway.
    spanned = sum(ends[t][p] - starts[t][p] for t in ends for p in ends[t])
    print(f"stage 1: committing {spanned} offsets", flush=True)
    (
        rows.writeTo(RAW_TABLE)
        .option(f"snapshot-property.{O.JOB}", O.JOB_INGEST)
        .option(f"snapshot-property.{O.KAFKA_OFFSETS}", O.encode(O.merge_committed(committed, ends)))
        .option(f"snapshot-property.{O.KAFKA_BACKLOG}", json.dumps(backlog, separators=(",", ":")))
        .option(f"snapshot-property.{O.MAX_KAFKA_TS}", max_kafka_ts.isoformat())
        .append()
    )
    written = added_records(spark, RAW_TABLE)
    print(f"stage 1: {written} rows -> {RAW_TABLE} (max kafka_ts {max_kafka_ts})")
    return written


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
        # Not cached. This DataFrame carries `payload`, and caching a payload
        # column is what killed stage 1 (see `stage_raw`). What the cache was
        # buying — one scan instead of three — is bought instead by making the
        # other two passes cheap: the schema-id probe below projects a single
        # int column, which Parquet prunes to, and the row count comes from the
        # commit summary rather than from a `count()` over the decode.
        total += _decode_into(spark, source, table, project, raw_snapshot_id, run_ts)
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

    # ponytail: if EVERY id in the range is unresolvable there is nothing to
    # commit, so the position does not advance and the same rows file the same
    # audit row on the next cycle. That is deliberate — the condition really is
    # still true, and a later registration of the id recovers the records — but
    # it repeats every 5 minutes until one decodable record arrives. Dedupe on
    # (check_name, scope) if that ever becomes noise worth suppressing.
    if unresolvable:
        write_audit_rows(
            spark,
            [
                Row(
                    run_ts=run_ts,
                    job="ingest",
                    check_name="unresolvable_schema_id",
                    scope=f"{table}/schema_id={schema_id}",
                    passed=False,
                    observed=int(schema_id),
                    detail=detail,
                )
                for schema_id, detail in unresolvable
            ],
        )
    if not parts:
        return 0

    # Written once, counted afterwards from Iceberg's own summary. The `count()`
    # that used to precede this was a full second Avro decode of the range, and
    # the `persist(DISK_ONLY)` that made it cheaper serialised decoded book
    # snapshots — 100 levels of two struct arrays per row — through the
    # in-memory columnar cache. Same failure as stage 1, one table downstream.
    out = reduce(DataFrame.unionByName, parts)
    (
        out.writeTo(table)
        .option(f"snapshot-property.{O.JOB}", O.JOB_DECODE)
        .option(f"snapshot-property.{O.SRC_SNAPSHOT_ID}", str(raw_snapshot_id))
        .append()
    )
    written = added_records(spark, table)
    print(
        f"stage 2: {written} rows -> {table} (schema ids {sorted(schema_ids)}, src snapshot {raw_snapshot_id})"
    )
    return written


def write_audit_rows(spark, rows: list) -> None:
    """This run's findings into `audit.checks`, in ONE commit.

    Same table as the nightly audit, `job='ingest'`, so "what did the pipeline
    find and when" stays one query.

    Two properties ride on the commit and both are load-bearing.
    `k2.job=ingest` is what keeps this snapshot out of
    `k2_lake_audit_failures_total`: that gauge is the nightly audit's count, and
    an ingest row landing as the current snapshot used to zero a firing
    `LakeAuditFailed` with no audit having passed. `k2.audit-failures` is the
    same property `maintenance.run_audits` writes, and it is why this is one
    commit rather than one per row — per row the count in the newest summary is
    always 1, and `k2_lake_unresolvable_schema_ids_total` would report "at least
    one" while claiming to be a count.

    Best-effort: a finding that cannot be recorded must not become a second
    failure on top of the one it was reporting.
    """
    failures = sum(1 for r in rows if not r["passed"])
    try:
        (
            spark.createDataFrame(rows)
            .writeTo(CHECKS_TABLE)
            .option(f"snapshot-property.{O.JOB}", "ingest")
            .option(f"snapshot-property.{O.AUDIT_FAILURES}", str(failures))
            .append()
        )
    except Exception as exc:  # noqa: BLE001 - the finding already printed above
        print(f"stage 2: could not write {len(rows)} audit row(s) ({exc})")


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
    parser.add_argument(
        "--max-offsets-per-partition",
        type=int,
        default=MAX_OFFSETS_PER_PARTITION,
        help="stop each partition this many offsets past where it started (0 = read to latest)",
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
            stage_raw(spark, ingest_ts, args.end_timestamp, args.max_offsets_per_partition)
        if args.stage in ("all", "bronze"):
            stage_bronze(spark, current_snapshot_id(spark, RAW_TABLE), ingest_ts)
    finally:
        spark.stop()
        if lock is not None:
            lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
