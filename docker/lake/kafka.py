#!/usr/bin/env python3
"""The Kafka connector's load: plan the next offset range, land it, stage bronze from the landing
(ADR-022, ADR-047; plan 004, "Connectors"). connector.py dispatches a `kafka` pipeline here.

    connector.py load --release <id> --product <domain>.<product> --run-id <dagId>/<runId> [--accept-data-loss]

plan   the next range per partition: from the end offsets (`k2.kafka-offsets`) of the landing's newest
       connector snapshot on main, walking back past snapshots without them (compaction, expiry);
       with none, from the log start (`startFrom: earliest-retained`). Bounded by
       `maxOffsetsPerPartition`. A start below the log start is refused, every such partition named,
       unless the run accepts the loss: then those partitions resume at the log start and the gap
       is returned and committed, for the run's `offset_gap` record.
land   reads the pinned range from the topic once to count and once to write. Every record of the
       range lands byte for byte in lake.<fence>.landing, the count per partition equal to its span
       or nothing commits, with the end offsets, the batch's starts and any accepted gap in that
       one commit's summary. A retry plans from that commit, so it lands only what is new.
stage  takes, per partition, every landing row after the offset bronze has staged through up to the
       landing's newest end offsets, read from the landing at one snapshot and never from the
       topic, so whatever earlier runs landed and did not stage is picked up exactly once. The
       filter keeps its records for bronze's `audit` branch and counts the rest; kept plus skipped
       must equal the rows read. The bronze commit records the through-offsets (the landing's end
       offsets: trailing skipped records advance them too), the landing snapshot it read and the
       skipped counts. While `audit` holds another unpublished batch it is refused, after the
       landing has committed (bronze publishes in order; a blocked bronze never blocks the landing).

A paused product lands nothing. The first run after a resume is an accept-data-loss run: what the
topic evicted during the pause is gone, and it resumes at the log start with the gap recorded.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import connector as C

import offsets as O

JOB_CONNECTOR = "connector"  # k2.job on every landing and bronze commit this module makes
KAFKA_STARTS = "k2.kafka-starts"  # landing commits: the batch's first offset per partition
KAFKA_GAPS = "k2.kafka-gaps"  # landing commits: an accepted loss, {topic: {partition: [committed, log start]}}
KAFKA_SKIPPED = "k2.kafka-skipped"  # bronze commits: landing rows the filter skipped, per partition
KEPT = "k2_kept"  # the filter's verdict on a landing row (a column of the stage's read, never written)
# The landing's columns from the Kafka source's, as read: the bytes are never decoded or re-encoded.
# tests/test_lake_kafka.py holds these with the run's four equal to model.PLATFORM_TABLES["landing"].
LANDING_SOURCE = (("kafka_partition", "partition"), ("kafka_offset", "offset"),
                  ("kafka_timestamp", "timestamp"), ("record_key", "key"), ("record_value", "value"))
LANDING_RUN = ("batch_id", "landed_at", "run_id", "release_id")


# --- The pure core ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    starts: dict  # {topic: {partition: first offset read}}
    ends: dict  # {topic: {partition: end offset, exclusive}}
    offsets: dict  # what the commit records as k2.kafka-offsets: ends over the previous resume point
    gaps: list  # [(topic, partition, committed, log start, records lost)], accepted
    backlog: dict  # {topic: records left past the bound}

    @property
    def empty(self) -> bool:
        """Nothing to read and no gap to record: the run commits nothing."""
        return self.starts == self.ends and not self.gaps


def newest(history: list):
    """The newest connector commit's summary that carries end offsets, or None. `history` is
    `[(committed_at, summary)]` over main's lineage (catalog.snapshot_history)."""
    return O.latest_summary([(ts, s) for ts, s in history if s and O.KAFKA_OFFSETS in s], JOB_CONNECTOR)


def plan(history: list, earliest: dict, latest: dict, max_per_partition: int,
         accept_data_loss: bool = False) -> Plan:
    """The next range per partition. `earliest` and `latest` are the broker's `{topic: {partition:
    offset}}` (catalog.broker_offsets) for the product's one topic. Raises Refused, naming every
    partition below the log start, unless `accept_data_loss`."""
    last = newest(history)
    committed = O.decode(last[O.KAFKA_OFFSETS]) if last else {}
    starting = O.next_starting_offsets(committed, {t: len(p) for t, p in earliest.items()})
    starts, ends, backlog = O.bounded_offsets(starting, earliest, latest, max_per_partition)
    losses = O.evicted(starts, earliest)
    if losses and not accept_data_loss:
        named = "; ".join(f"{t}/{p} committed {s}, log start {ls}, {n} records gone" for t, p, s, ls, n in losses)
        raise C.Refused(f"below the log start: {named}. Nothing landed; an accept-data-loss run records the "
                        "gap and resumes those partitions at the log start")
    if losses:
        starts, ends, backlog = O.bounded_offsets(O.skip_evicted(starts, losses), earliest, latest, max_per_partition)
    return Plan(starts, ends, O.merge_committed(committed, ends), losses, backlog)


def account(p: Plan, found: list) -> None:
    """Refused unless every planned partition read exactly its span, first at its start and last at
    its end - 1 (offsets.batch_audit); `found` is `[(partition, records, first, last)]` over the
    read. Committing the end offsets of a read that came up short would be a hole nothing records."""
    [topic] = p.ends
    failures = O.batch_audit(p.starts, p.ends, [(topic, *row) for row in found])
    if failures:
        raise C.Refused("the read does not cover its offsets, nothing landed: "
                        + "; ".join(f"{f['scope']} {f['detail']}" for f in failures))


def gaps_json(gaps: list) -> str:
    """KAFKA_GAPS's value: `{topic: {partition: [committed, log start]}}`, the hole being
    committed..log start - 1, recorded in the very commit that skips it."""
    out: dict = {}
    for topic, part, committed, log_start, _ in gaps:
        out.setdefault(topic, {})[str(part)] = [int(committed), int(log_start)]
    return json.dumps(out, separators=(",", ":"), sort_keys=True)


def keep_sql(rule: dict | None, envelope: str = "envelope") -> str:
    """The filter as a Spark SQL predicate over the decoded envelope struct. True keeps the record:
    its field holds one of the values, or the envelope did not decode (a frame nobody can classify
    goes to bronze for its audit to fail, never skipped unseen). A decoded envelope whose field is
    null is skipped. No filter keeps everything."""
    if not rule:
        return "true"
    values = ", ".join(C.string_literal(v) for v in rule["in"])
    e = C.name(envelope)
    return f"coalesce({e} IS NULL OR {e}.{C.name(rule['field'])} IN ({values}), false)"


def range_sql(ranges: dict) -> tuple[str, dict]:
    """`(where, args)` selecting the landing's rows in `{partition: (start, end)}`, end exclusive;
    the values bound as Spark named parameters."""
    terms, args = [], {}
    for i, (part, (start, end)) in enumerate(sorted(ranges.items())):
        terms.append(f"(kafka_partition = :p{i} AND kafka_offset >= :s{i} AND kafka_offset < :e{i})")
        args.update({f"p{i}": int(part), f"s{i}": int(start), f"e{i}": int(end)})
    return " OR ".join(terms), args


def stage_ranges(through: dict, landed: dict) -> dict:
    """`{partition: (start, end)}` of the landing not yet in bronze: from the offset bronze has
    staged through (exclusive; none staged: 0) to the landing's end offsets. Empty when level.
    Refused when bronze is past the landing on a partition (a landing restored older than its
    bronze): restaging would skip or double it."""
    [(topic, ends)] = landed.items()
    starts = through.get(topic, {})
    ahead = sorted(p for p, s in starts.items() if s > ends.get(p, 0))
    if ahead:
        raise C.Refused(f"bronze is past the landing on {topic} partitions {ahead}; nothing staged")
    return {p: (starts.get(p, 0), e) for p, e in ends.items() if e > starts.get(p, 0)}


def stage_account(topic: str, ranges: dict, found: list) -> dict:
    """`{topic: {partition: skipped}}` for the landing rows a stage read, `found` being
    `[(partition, read, kept, skipped, first, last)]`. Refused unless every row read is kept or
    skipped, once, and lies inside its partition's range."""
    bad = [f"{topic}/{p}: {read} read, {kept} kept, {skipped} skipped, offsets {lo}..{hi}"
           for p, read, kept, skipped, lo, hi in found
           if kept + skipped != read or p not in ranges or not ranges[p][0] <= lo <= hi < ranges[p][1]]
    if bad:
        raise C.Refused("the stage does not account for the landing rows it read, nothing staged: " + "; ".join(bad))
    return {topic: {int(p): int(skipped) for p, _, _, skipped, _, _ in found}}


def summary(run: C.Run, **props) -> dict:
    """A connector commit's summary: the run's keys (model.SNAPSHOT_SUMMARY_KEYS but the sha256,
    which a Kafka batch has none of), k2.job and the commit's own properties."""
    return {**dict(zip(C.SUMMARY_KEYS[:3], (run.run_id, run.release, run.run_id))), O.JOB: JOB_CONNECTOR, **props}


# --- The tasks: flows over a source (the topic) and a lake -------------------------------------


def land(run: C.Run, source, lake) -> tuple[int, dict]:
    """Plans, reads, accounts and lands one batch in the landing table, in one commit.

    Every record of the range lands, not only those the filter keeps, which plan 004's "the
    landing table holds the kept records" does not say: offsets.batch_audit and offset_gaps hold a
    landing to `end - start` rows and `max - min + 1 == n` per partition, which a landing without
    its control frames fails at the first one, and skipped counts kept only in snapshot summaries
    are lost when the snapshots expire. The filter runs at stage, into bronze."""
    connector, table = run.pipeline["connector"], run.pipeline["landing"]["table"]
    result = {"landing": table, "landed": 0}
    _, history = lake.history(table)
    earliest, latest = source.offsets(connector["topic"])
    try:
        p = plan(history, earliest, latest, connector["maxOffsetsPerPartition"], run.accept_data_loss)
    except C.Refused as e:
        print(f"load refused: {e}", file=sys.stderr)
        return C.EXIT_REFUSED, result
    gaps = [{"topic": t, "partition": part, "committed": s, "logStart": ls, "lost": n} for t, part, s, ls, n in p.gaps]
    for g in gaps:
        print(f"load: ACCEPTED DATA LOSS {g['topic']}/{g['partition']}: offsets {g['committed']}.."
              f"{g['logStart'] - 1}, {g['lost']} records, resuming at {g['logStart']}", file=sys.stderr)
    result = {**result, "starts": p.starts, "ends": p.ends, "backlog": p.backlog, "offsetGaps": gaps}
    if p.empty:
        print("load: no new records")
        return C.EXIT_OK, result
    frames = source.frames(connector["topic"], p.starts, p.ends)
    found = source.counts(frames)
    try:
        account(p, found)
    except C.Refused as e:
        print(f"load refused: {e}", file=sys.stderr)
        return C.EXIT_REFUSED, result
    props = {O.KAFKA_OFFSETS: O.encode(p.offsets), KAFKA_STARTS: O.encode(p.starts)}
    if p.gaps:
        props[KAFKA_GAPS] = gaps_json(p.gaps)
    values = dict(zip(LANDING_RUN, (run.run_id, C.now(), run.run_id, run.release)))
    lake.land(table, frames, values, summary(run, **props))
    landed = sum(n for _, n, _, _ in found)
    print(f"load: landed {landed} records in {table}")
    return C.EXIT_OK, {**result, "landed": landed}


def stage(run: C.Run, lake) -> tuple[int, dict]:
    """Stages the landing's rows past bronze's through-offsets on bronze's `audit` branch."""
    # ponytail: one bronze table per Kafka product, the record as landed; a second needs a router.
    [bronze] = run.pipeline["bronze"]
    landing, table = run.pipeline["landing"]["table"], bronze["table"]
    refs = lake.refs(table)
    result = {"table": table, "snapshotBefore": refs.get(C.AUDIT), "rows": 0}
    pending = C.pending_batch(lake, table, refs)
    if pending == run.run_id:  # a retry: what it landed since waits for the next run's stage
        print(f"load: batch {run.run_id} is on {C.AUDIT} already; nothing staged")
        return C.EXIT_OK, result
    if pending is not None:
        print(f"load refused: {C.AUDIT} of {table} holds batch {pending}, unpublished; this batch waits "
              "behind it (its records are landed)", file=sys.stderr)
        return C.EXIT_REFUSED, result
    head, landed = lake.history(landing)
    last_landed = newest(landed)
    if last_landed is None:
        print(f"load: {landing} holds nothing yet; nothing staged")
        return C.EXIT_OK, result
    last_staged = newest(lake.history(table)[1])
    through = O.decode(last_staged[O.KAFKA_OFFSETS]) if last_staged else {}
    ends = O.decode(last_landed[O.KAFKA_OFFSETS])
    try:
        ranges = stage_ranges(through, ends)
        if not ranges:
            print(f"load: {table} is level with {landing}; nothing staged")
            return C.EXIT_OK, result
        frames = lake.landed(landing, head, ranges, keep_sql(run.pipeline["connector"].get("filter")))
        [topic] = ends
        skipped = stage_account(topic, ranges, lake.stage_counts(frames))
    except C.Refused as e:
        print(f"load refused: {e}", file=sys.stderr)
        return C.EXIT_REFUSED, result
    if C.AUDIT not in refs:
        lake.create_branch(table)
    elif refs[C.AUDIT] != refs.get("main"):  # behind main, which maintenance moved on
        lake.reset_branch(table, refs["main"])
    props = {O.SRC_SNAPSHOT_ID: str(head), O.KAFKA_OFFSETS: O.encode(O.merge_committed(through, ends)),
             KAFKA_SKIPPED: O.encode(skipped)}
    rows = lake.stage_rows(table, frames, summary(run, **props))
    after = lake.refs(table).get(C.AUDIT)
    print(f"load: {rows} rows staged on {C.AUDIT} of {table} from {landing} snapshot {head}, "
          f"{sum(skipped[topic].values())} skipped, snapshot {after}")
    return C.EXIT_OK, {**result, "rows": rows, "skipped": sum(skipped[topic].values()), "srcSnapshot": head,
                       "snapshotAfter": after}


def load(run: C.Run, source, lake) -> tuple[int, dict]:
    """land, then stage: the landing commits whatever bronze does next. A paused product does neither."""
    if run.pipeline["status"] == "paused":
        print(f"load: {run.product} is paused; nothing landed or staged")
        return C.EXIT_OK, {"landing": run.pipeline["landing"]["table"], "landed": 0}
    code, landed = land(run, source, lake)
    if code != C.EXIT_OK:
        return code, landed
    code, staged = stage(run, lake)
    return code, {**landed, **staged}


def audit(run: C.Run, source, lake) -> tuple[int, dict]:
    print("audit: the Kafka batch audit is not built yet, so no Kafka batch publishes", file=sys.stderr)
    return C.EXIT_ERROR, {}


TASKS = {"load": load, "audit": audit, "publish": C.publish}


# --- The shells: the topic and the lake through Spark -----------------------------------------


class SparkKafka:
    """The product's topic, read over a pinned offset range by Spark's Kafka source."""

    def __init__(self, spark, connector: dict):
        self.spark, self.brokers = spark, connector["brokers"]

    def offsets(self, topic: str) -> tuple[dict, dict]:
        from catalog import broker_offsets

        earliest, latest, _ = broker_offsets(self.spark, [topic], brokers=self.brokers)
        return earliest, latest

    def frames(self, topic: str, starts: dict, ends: dict):
        return (self.spark.read.format("kafka").option("kafka.bootstrap.servers", self.brokers)
                .option("subscribe", topic).option("startingOffsets", O.encode(starts))
                .option("endingOffsets", O.encode(ends)).option("failOnDataLoss", "true").load())

    @staticmethod
    def counts(frames) -> list:
        from pyspark.sql import functions as F

        return [tuple(r) for r in frames.groupBy("partition").agg(
            F.count(F.lit(1)), F.min("offset"), F.max("offset")).collect()]


class SparkLanding(C.SparkLake):
    """connector.SparkLake, with the landing's commit, the snapshot history and the bronze stage."""

    def __init__(self, spark, registry: str):
        super().__init__(spark)
        self.registry = registry

    def history(self, table: str) -> tuple:
        from catalog import current_snapshot_id, snapshot_history

        return current_snapshot_id(self.spark, C.rel(table)), snapshot_history(self.spark, C.rel(table))

    def land(self, table: str, frames, values: dict, props: dict) -> None:
        from pyspark.sql import functions as F

        cols = [F.col(src).alias(dst) for dst, src in LANDING_SOURCE] + [F.lit(v).alias(k) for k, v in values.items()]
        schema = self.spark.table(C.rel(table)).schema
        self._write(frames.select(*cols).select(*[F.col(f.name).cast(f.dataType) for f in schema.fields]),
                    C.rel(table), props)

    def landed(self, landing: str, snapshot: int, ranges: dict, keep: str):
        """The landing's rows in `ranges` at `snapshot` with KEPT, the envelope decoded once per
        writer schema id (PERMISSIVE: a body that does not read decodes to null and is kept, as is a
        record unframed or of an id the registry does not serve; see keep_sql)."""
        from catalog import UnresolvableSchema, fetch_schema
        from pyspark.sql import functions as F
        from pyspark.sql.avro.functions import from_avro

        import wire

        where, args = range_sql(ranges)
        raw = self.spark.sql(f"SELECT * FROM {C.rel(landing)} VERSION AS OF {C.snapshot_id(snapshot)} WHERE {where}",
                             args).withColumn("k2_schema_id", F.expr(wire.schema_id_guarded_expr("record_value")))
        schemas = {}
        for (sid,) in raw.select("k2_schema_id").where("k2_schema_id IS NOT NULL").distinct().collect():
            try:
                schemas[sid] = fetch_schema(sid, self.registry)
            except UnresolvableSchema as e:
                print(f"load: {e}; its records go to bronze undecoded")
        out = raw.where(F.col("k2_schema_id").isNull() | ~F.col("k2_schema_id").isin(list(schemas)))
        out = out.withColumn(KEPT, F.lit(True))
        for sid, schema in sorted(schemas.items()):
            body = F.expr(wire.body_expr("record_value"))
            out = out.unionByName(raw.where(F.col("k2_schema_id") == sid)
                                  .withColumn("envelope", from_avro(body, schema, {"mode": "PERMISSIVE"}))
                                  .withColumn(KEPT, F.expr(keep)).drop("envelope"))
        return out

    @staticmethod
    def stage_counts(frames) -> list:
        from pyspark.sql import functions as F

        return [tuple(r) for r in frames.groupBy("kafka_partition").agg(
            F.count(F.lit(1)), F.sum(F.when(F.col(KEPT), 1).otherwise(0)),
            F.sum(F.when(~F.col(KEPT), 1).otherwise(0)),  # a null verdict counts as neither, and is refused
            F.min("kafka_offset"), F.max("kafka_offset")).collect()]

    def stage_rows(self, table: str, frames, props: dict) -> int:
        from pyspark.sql import functions as F

        # ponytail: bronze holds the landing's columns as landed, by name; decoded envelope
        # columns, if bronze's contract gains any, are computed here.
        self._write(frames.where(F.col(KEPT)).select(*self.columns(table)),
                    f"{C.rel(table)}.{C.name('branch_' + C.AUDIT)}", props)
        head = self.refs(table)[C.AUDIT]
        return int(self._sql(f"SELECT `summary`['added-records'] FROM {C.rel(table)}.`snapshots` "
                             "WHERE `snapshot_id` = :s", s=head)[0][0] or 0)
