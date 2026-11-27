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
       or nothing commits, with the end offsets, the batch's starts and every accepted gap so far
       (cumulative: the previous connector commit's and any this run accepts) in that one commit's
       summary. A retry plans from that commit, so it lands only what is new.
stage  takes, per partition, every landing row after the offset bronze has staged through (for a
       partition bronze never staged, from the landing's first offset in it, never 0) up to the
       landing's newest end offsets, read from the landing at one snapshot and never from the
       topic, so whatever earlier runs landed and did not stage is picked up exactly once. Each
       frame's envelope is decoded with the writer schema its schema id names (fetched from the
       registry by id), and bronze takes the decoded envelope, never the frame's bytes: the
       frame's offset key and timestamp, its schema id, then each of bronze's envelope columns by
       name from the decoded record (project_sql), and the landing batch's lineage. The filter
       keeps its records for bronze's `audit` branch and counts the rest; kept plus skipped must
       equal the rows read. The bronze commit records the through-offsets (the landing's end
       offsets: trailing skipped records advance them too), the landing snapshot it read and the
       skipped counts. While `audit` holds another run's unpublished batch that failed its audit it
       is refused, after the landing has committed (bronze publishes in order; a blocked bronze
       never blocks the landing); one never audited, or audited and passed but never published, is
       discarded and its range staged again by this run (restageable).

audit  checks the batch at `audit`'s head over the range it staged (main's through-offsets to its
       own), never the whole branch, one audit_checks row per check of CHECKS:
       accounting   per partition the landing, at the snapshot the stage read, holds every offset
                    of the range but those an accepted loss records, once each; staged plus
                    skipped equals that count; the commit added only rows of its range.
       parses       every staged frame, read again from the landing at the snapshot the stage
                    read, is Confluent-framed with an id the registry serves as RawMessage, its
                    body reads, its writer schema has each of bronze's envelope columns by name
                    and of a type that reads as the column's (problems), and the filter keeps
                    it; a failure names partition and offset and any column, at most LISTED of
                    them, never a byte of the frame. So a column the stage wrote NULL because
                    the record lacks it never publishes as a silent null.
       keys_unique  (kafka_partition, kafka_offset) once within the batch, and none on main.
       A run whose batch staged nothing passes each, saying so.

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
# landing commits: every accepted loss so far, {topic: {partition: [[committed, log start], ...]}}
KAFKA_GAPS = "k2.kafka-gaps"
KAFKA_SKIPPED = "k2.kafka-skipped"  # bronze commits: landing rows the filter skipped, per partition
KEPT = "k2_kept"  # the filter's verdict on a landing row (a column of the stage's read, never written)
# The landing's columns from the Kafka source's, as read: the bytes are never decoded or re-encoded.
# tests/test_lake_kafka.py holds these with the run's four equal to model.PLATFORM_TABLES["landing"].
LANDING_SOURCE = (("kafka_partition", "partition"), ("kafka_offset", "offset"),
                  ("kafka_timestamp", "timestamp"), ("record_key", "key"), ("record_value", "value"))
LANDING_RUN = ("batch_id", "landed_at", "run_id", "release_id")
# Bronze's columns (I-1, 2026-11-28): these, then the envelope's columns by field name, then
# LANDING_RUN. k2gen's connectors.kafka-bronze-shape holds a contract to it; record_key and
# record_value stay in the landing, which bronze is rebuilt from.
BRONZE_LEAD = ("kafka_partition", "kafka_offset", "kafka_timestamp", "schema_id")
# Avro's promotions that the bronze column's type also takes without loss (Spark simpleString names)
WIDENS = {("int", "bigint"), ("float", "double")}
CHECKS = ("accounting", "parses", "keys_unique")  # a Kafka batch's audit_checks names: what publish requires
DETAIL_MAX = 1000  # model.PLATFORM_TABLES["audit_checks"]: detail, at most 1000 characters
LISTED = 10  # frames a parses failure names
ENVELOPE = "com.k2.market.v3.RawMessage"  # schemas/avro/raw-message.avsc, held equal by the tests


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


def merged(ranges) -> list:
    """`[(committed, log start)]` sorted, those that meet or overlap made one."""
    out: list = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def gaps_of(summary: dict | None) -> dict:
    """One landing commit's KAFKA_GAPS as `{topic: {partition: [(committed, log start)]}}`."""
    return {t: {int(p): [(int(c), int(ls)) for c, ls in v] for p, v in parts.items()}
            for t, parts in json.loads((summary or {}).get(KAFKA_GAPS, "{}")).items()}


def carried_gaps(previous: dict, accepted: list) -> dict:
    """What a landing commit records as KAFKA_GAPS: `previous` (gaps_of the newest connector
    commit before it) plus this run's accepted losses `[(topic, partition, committed, log start,
    lost)]`, per partition merged. Carried on every commit like the end offsets, so expiring the
    commit that accepted a loss never drops its record (maintain's continuity reads it)."""
    out = {t: {p: list(v) for p, v in parts.items()} for t, parts in previous.items()}
    for topic, part, committed, log_start, _ in accepted:
        out.setdefault(topic, {}).setdefault(int(part), []).append((int(committed), int(log_start)))
    return {t: {p: merged(v) for p, v in parts.items()} for t, parts in out.items()}


def gaps_json(gaps: dict) -> str:
    """KAFKA_GAPS's value from carried_gaps: each hole is committed..log start - 1."""
    return json.dumps({t: {str(p): [list(g) for g in v] for p, v in parts.items()} for t, parts in gaps.items()},
                      separators=(",", ":"), sort_keys=True)


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


def decoded_sql(fields: list[str], envelope: str = "envelope") -> str:
    """The decoded envelope, or NULL when its body did not read. Spark's PERMISSIVE from_avro gives
    such a body a struct whose every field is null, never a null struct (Spark 3.5.9, seen by
    scripts/verify/kafka-path.sh claim 9), so without this keep_sql would skip a corrupt frame as
    one whose field is null and parses would count it decoded: skipped unseen.

    ponytail: a real record with every field null reads as undecoded too, so it is kept and fails
    parses (closed); RawMessage has six required fields, so it cannot be one."""
    e = C.name(envelope)
    nulls = " AND ".join(f"{e}.{C.name(f)} IS NULL" for f in fields)
    return f"CASE WHEN {nulls} THEN NULL ELSE {e} END"


def envelope_columns(columns: dict) -> dict:
    """Bronze's envelope columns `{name: type}` of its `{name: type}`: all but BRONZE_LEAD and LANDING_RUN."""
    return {n: t for n, t in columns.items() if n not in BRONZE_LEAD and n not in LANDING_RUN}


def compatible(found, want: str) -> bool:
    """Whether a decoded field of type `found` (None: the record has no such field) reads as a
    bronze column of type `want`, both Spark simpleString names."""
    return found == want or (found, want) in WIDENS


def problems(decoded: dict, columns: dict) -> list[str]:
    """Each envelope column of bronze (`columns`, envelope_columns) the writer schema's decoded
    record (`decoded`, `{field: type}`) lacks or holds as a type that does not read as it."""
    return [f"has no field {n}, which bronze's column {n} takes" if n not in decoded else
            f"has field {n} as {decoded[n]}, which bronze's column {n} ({t}) does not take"
            for n, t in columns.items() if not compatible(decoded.get(n), t)]


def project_sql(decoded: dict, columns: dict, envelope: str = "envelope") -> list[str]:
    """Bronze's envelope columns as Spark SQL over the decoded struct, by name: a compatible field
    cast to its column's type, any other NULL, which parses then fails (problems). `decoded` empty
    (a frame unframed or of an id that is no RawMessage) makes every column NULL."""
    e = C.name(envelope)
    return [f"CAST({e}.{C.name(n)} AS {t}) AS {C.name(n)}" if compatible(decoded.get(n), t) else
            f"CAST(NULL AS {t}) AS {C.name(n)}" for n, t in columns.items()]


def range_sql(ranges: dict) -> tuple[str, dict]:
    """`(where, args)` selecting the landing's rows in `{partition: (start, end)}`, end exclusive;
    the values bound as Spark named parameters."""
    terms, args = [], {}
    for i, (part, (start, end)) in enumerate(sorted(ranges.items())):
        terms.append(f"(kafka_partition = :p{i} AND kafka_offset >= :s{i} AND kafka_offset < :e{i})")
        args.update({f"p{i}": int(part), f"s{i}": int(start), f"e{i}": int(end)})
    return " OR ".join(terms), args


def stage_ranges(through: dict, landed: dict, firsts: dict) -> dict:
    """`{partition: (start, end)}` of the landing not yet in bronze: from the offset bronze has
    staged through (exclusive) to the landing's end offsets. A partition bronze has never staged
    starts at the landing's first offset in it (`firsts`, `{partition: offset}`), never at 0: the
    first run, and a partition added since, land from the log start. One the landing holds no row
    of has nothing to stage. Empty when level. Refused when bronze is past the landing on a
    partition (a landing restored older than its bronze): restaging would skip or double it."""
    [(topic, ends)] = landed.items()
    starts = {**{p: firsts.get(p, e) for p, e in ends.items()}, **through.get(topic, {})}
    ahead = sorted(p for p, s in through.get(topic, {}).items() if s > ends.get(p, 0))
    if ahead:
        raise C.Refused(f"bronze is past the landing on {topic} partitions {ahead}; nothing staged")
    return {p: (starts[p], e) for p, e in ends.items() if e > starts[p]}


def ranges_of(lake, landing: str, snapshot: int, through: dict, ends: dict) -> dict:
    """stage_ranges over the landing at `snapshot`. The first offset of a partition bronze has no
    through-offset for is the landing's minimum `kafka_offset` in it, read from its rows: they are
    never expired (Retention), where the landing commit that recorded the batch's start
    (KAFKA_STARTS) may be. The land step held that commit's read to its planned span, first offset
    at its start, so the minimum is that start."""
    [(topic, parts)] = ends.items()
    new = sorted(p for p in parts if p not in through.get(topic, {}))
    return stage_ranges(through, ends, lake.firsts(landing, snapshot, new) if new else {})


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


def restageable(found: list, head: dict) -> tuple[bool, str]:
    """`(restaged, why)` for the unpublished batch of another run on `audit`: `found` its
    audit_checks rows at `audit`'s head (connector.checks_of), `head` that commit's summary.

    A Kafka batch is its offsets, reproducible from the landing, so one never audited (its run
    ended between stage and audit: Airflow retries no task, and the run's timeout kills it) or
    audited, passed and never published is discarded with `audit` and its range staged again by
    this run, with whatever landed since, under this run's own audit. Restaged, not adopted:
    publish takes only this run's audit at the head, and adopting another run's would widen what
    licenses a publish. A batch whose newest audit failed or lacks a check, or a head no Kafka
    stage wrote, is never reset: it blocks until an operator clears it."""
    if (head or {}).get(O.JOB) != JOB_CONNECTOR or O.KAFKA_OFFSETS not in head:
        return False, "no Kafka stage wrote it"
    if not found:
        return True, "never audited"
    why = C.unaudited(found, list(CHECKS))
    return (True, "audited and passed, never published") if why is None else (False, why)


def summary(run: C.Run, **props) -> dict:
    """A connector commit's summary: the run's keys (model.SNAPSHOT_SUMMARY_KEYS but the sha256,
    which a Kafka batch has none of), k2.job and the commit's own properties."""
    return {**dict(zip(C.SUMMARY_KEYS[:3], (run.run_id, run.release, run.run_id))), O.JOB: JOB_CONNECTOR, **props}


def accepted_losses(history: list, topic: str) -> dict:
    """`{partition: [(first, last)]}`, inclusive and merged, so no two overlap: the offsets the
    landing's commits record as an accepted loss (KAFKA_GAPS), which no landing row holds and no
    bronze row can. Each commit carries every loss before it; reading them all gives the same set."""
    ranges: dict = {}
    for _, s in history:
        for p, v in gaps_of(s).get(topic, {}).items():
            ranges.setdefault(p, []).extend(v)
    return {p: [(lo, hi - 1) for lo, hi in merged(v)] for p, v in ranges.items()}


def planned(start: int, end: int, lost: list):
    """`(rows, first, last)` the landing must hold in `start..end - 1`: every offset but the
    accepted losses in it (offsets.batch_audit's rule, less those); None when none is left.

    ponytail: compared as count, distinct offsets, first and last, so with a loss inside the
    range (a kill between landing and staging across an accept-data-loss run) a row inside the
    loss and a hole elsewhere net to a pass; count the rows inside each loss if one is ever seen."""
    spans, cursor = [], start
    for first, last in sorted(lost):
        if first > cursor:
            spans.append((cursor, min(first, end)))
        cursor = max(cursor, last + 1)
    spans = [(lo, hi) for lo, hi in [*spans, (cursor, end)] if lo < hi]
    return (sum(hi - lo for lo, hi in spans), spans[0][0], spans[-1][1] - 1) if spans else None


def accounting(topic: str, ranges: dict, lost: dict, landed: list, staged: list, skipped: dict,
               added: int) -> tuple[bool, str]:
    """`landed` and `staged` are offset counts `[(partition, rows, offsets, first, last)]` over
    `ranges`, of the landing at the snapshot the stage read and of bronze at `audit`'s head;
    `skipped` the batch's KAFKA_SKIPPED for the topic, `added` its commit's added-records."""
    land = {p: (n, d, lo, hi) for p, n, d, lo, hi in landed}
    bron = {p: n for p, n, *_ in staged}
    bad, want_all = [], 0
    for p, (start, end) in sorted(ranges.items()):
        want = planned(start, end, lost.get(p, []))
        rows = want[0] if want else 0
        want_all += rows
        n, d, lo, hi = land.get(p, (0, 0, None, None))
        if (n, lo, hi) != (want or (0, None, None)) or d != n:
            bad.append(f"{topic}/{p}: the landing holds {n} rows, {d} offsets, at {lo}..{hi} of {start}..{end - 1}; "
                       f"planned {rows}")
        if bron.get(p, 0) + skipped.get(p, 0) != rows:
            bad.append(f"{topic}/{p}: {bron.get(p, 0)} staged + {skipped.get(p, 0)} skipped, planned {rows}")
    if sum(bron.values()) != added:
        bad.append(f"the batch's commit added {added} rows, {sum(bron.values())} of them in its range")
    if bad:
        return False, "; ".join(bad)
    return True, (f"{sum(bron.values())} staged + {sum(skipped.get(p, 0) for p in ranges)} skipped = "
                  f"{want_all} landed over {len(ranges)} partitions")


def unparsed_reason(schema_id, registered: bool, decoded: bool, problem: str | None = None) -> str:
    """Why a staged frame fails parses, from facts about it alone: never a byte of it. `problem` is
    its writer schema's problems() joined, or None."""
    if schema_id is None:
        return "not Confluent-framed"
    if not registered:
        return f"schema id {int(schema_id)} is not a registered {ENVELOPE}"
    if not decoded:
        return f"its body does not read as {ENVELOPE}"
    return f"its writer schema {int(schema_id)} {problem}" if problem else "the filter does not keep it"


def parses(topic: str, frames: int, failing: int, listed: list) -> tuple[bool, str]:
    """`listed` is the first `[(partition, offset, schema id, registered, decoded, problem)]` of the
    `failing` frames, by partition and offset."""
    if not failing:
        return True, f"{frames} frames parse"
    shown = listed[:LISTED]
    named = "; ".join(f"{topic}/{p} offset {o}: {unparsed_reason(*facts)}" for p, o, *facts in shown)
    more = f"; {failing - len(shown)} more" if failing > len(shown) else ""
    return False, f"{failing} of {frames} frames do not parse: {named}{more}"


def keys_unique(staged: list, on_main: list) -> tuple[bool, str]:
    """`staged` and `on_main` are offset counts over the batch's range at `audit`'s head and at main."""
    repeats, published = sum(n - d for _, n, d, _, _ in staged), sum(n for _, n, *_ in on_main)
    if repeats or published:
        return False, f"{repeats} rows repeat a key of the batch; {published} rows of its range are on main already"
    return True, f"{sum(n for _, n, *_ in staged)} keys, each once, none on main"


def is_envelope(schema: str) -> bool:
    """Whether a registered schema is the RawMessage record, by its full name."""
    s = json.loads(schema)
    if not isinstance(s, dict) or s.get("type") != "record":
        return False
    full = s.get("name", "") if "." in s.get("name", "") else f"{s.get('namespace')}.{s.get('name')}"
    return full == ENVELOPE


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
    gaps_so_far = carried_gaps(gaps_of(newest(history)), p.gaps)
    if gaps_so_far:  # absent when no loss was ever accepted
        props[KAFKA_GAPS] = gaps_json(gaps_so_far)
    values = dict(zip(LANDING_RUN, (run.run_id, C.now(), run.run_id, run.release)))
    lake.land(table, frames, values, summary(run, **props))
    landed = sum(n for _, n, _, _ in found)
    print(f"load: landed {landed} records in {table}")
    return C.EXIT_OK, {**result, "landed": landed}


def stage(run: C.Run, lake) -> tuple[int, dict]:
    """Stages the landing's rows past bronze's through-offsets on bronze's `audit` branch."""
    # ponytail: one bronze table per Kafka product, its envelope decoded; a second needs a router.
    [bronze] = run.pipeline["bronze"]
    landing, table = run.pipeline["landing"]["table"], bronze["table"]
    refs = lake.refs(table)
    result = {"table": table, "snapshotBefore": refs.get(C.AUDIT), "rows": 0}
    pending = C.pending_batch(lake, table, refs)
    if pending == run.run_id:  # a retry: what it landed since waits for the next run's stage
        print(f"load: batch {run.run_id} is on {C.AUDIT} already; nothing staged")
        return C.EXIT_OK, result
    if pending is not None:
        at = refs[C.AUDIT]
        restage, why = restageable(lake.checks_of(bronze["checks"], table, pending, at), lake.summary_of(table, at))
        if not restage:
            clear = (f"ALTER TABLE {table} REPLACE BRANCH {C.AUDIT} AS OF VERSION {refs['main']}"
                     if refs.get("main") is not None else f"ALTER TABLE {table} DROP BRANCH {C.AUDIT}")
            print(f"load refused: {C.AUDIT} of {table} holds batch {pending} at {at}, unpublished: {why}. This batch "
                  "waits behind it (its records are landed); once the cause is fixed an operator clears it as the "
                  f"Spark key (docs/runbooks/file-source-audit-failed.md, step 5): {clear}", file=sys.stderr)
            return C.EXIT_REFUSED, result
        print(f"load: {C.AUDIT} of {table} holds batch {pending} at {at}, {why}: a Kafka batch is its offsets, so it "
              "is discarded and its range staged again by this run, with what landed since")
    head, landed = lake.history(landing)
    last_landed = newest(landed)
    if last_landed is None:
        print(f"load: {landing} holds nothing yet; nothing staged")
        return C.EXIT_OK, result
    last_staged = newest(lake.history(table)[1])
    through = O.decode(last_staged[O.KAFKA_OFFSETS]) if last_staged else {}
    ends = O.decode(last_landed[O.KAFKA_OFFSETS])
    try:
        ranges = ranges_of(lake, landing, head, through, ends)
        if not ranges:
            print(f"load: {table} is level with {landing}; nothing staged")
            return C.EXIT_OK, result
        frames = lake.landed(landing, head, ranges, keep_sql(run.pipeline["connector"].get("filter")), table)
        [topic] = ends
        skipped = stage_account(topic, ranges, lake.stage_counts(frames))
    except C.Refused as e:
        print(f"load refused: {e}", file=sys.stderr)
        return C.EXIT_REFUSED, result
    if C.AUDIT not in refs:
        lake.create_branch(table)
    elif refs[C.AUDIT] != refs.get("main"):  # behind main, which maintenance moved on, or a batch restaged
        lake.reset_branch(table, refs.get("main"))
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


def audit_results(run: C.Run, lake, refs: dict) -> list[tuple[str, bool, str]]:
    """`(check, passed, detail)` in CHECKS order for the batch at `audit`'s head, over the range
    from main's through-offsets to the batch's own, as stage chose it."""
    [bronze] = run.pipeline["bronze"]
    table, landing, head = bronze["table"], run.pipeline["landing"]["table"], refs[C.AUDIT]
    batch = lake.summary_of(table, head)
    before = newest(lake.history(table)[1])
    after, src = O.decode(batch[O.KAFKA_OFFSETS]), int(batch[O.SRC_SNAPSHOT_ID])
    [topic] = after
    ranges = ranges_of(lake, landing, src, O.decode(before[O.KAFKA_OFFSETS]) if before else {}, after)
    staged = lake.offset_counts(table, head, ranges)
    failing, listed = lake.unparsed(landing, src, table, head, ranges, keep_sql(run.pipeline["connector"].get("filter")))
    return [
        ("accounting", *accounting(topic, ranges, accepted_losses(lake.history(landing)[1], topic),
                                   lake.offset_counts(landing, src, ranges), staged,
                                   O.decode(batch[KAFKA_SKIPPED]).get(topic, {}), int(batch.get("added-records") or 0))),
        ("parses", *parses(topic, sum(n for _, n, *_ in staged), failing, listed)),
        ("keys_unique", *keys_unique(staged, lake.offset_counts(table, refs.get("main"), ranges))),
    ]


def audit(run: C.Run, source, lake) -> tuple[int, dict]:
    """Runs every check of CHECKS on the batch at `audit`'s head and records each in audit_checks."""
    [bronze] = run.pipeline["bronze"]
    table = bronze["table"]
    refs = lake.refs(table)
    pending = C.pending_batch(lake, table, refs)
    if pending not in (None, run.run_id):
        print(f"audit refused: {C.AUDIT} of {table} holds batch {pending}, not this one", file=sys.stderr)
        return C.EXIT_REFUSED, {"table": table}
    head = refs.get(C.AUDIT, refs.get("main"))
    results = (audit_results(run, lake, refs) if pending else
               [(c, True, f"batch {run.run_id} staged nothing unpublished on {C.AUDIT}") for c in CHECKS])
    at = C.now()
    lake.append(bronze["checks"], [
        {"table_name": table, "batch_id": run.run_id, "snapshot_id": head, "check_name": check, "passed": passed,
         "detail": detail[:DETAIL_MAX], "checked_at": at, "run_id": run.run_id, "release_id": run.release}
        for check, passed, detail in results], summary(run))
    for check, passed, detail in results:
        print(f"  {'pass' if passed else 'FAIL'}  {check:<12} {detail[:DETAIL_MAX]}")
    failed = [c for c, passed, _ in results if not passed]
    print(f"audit: batch {run.run_id} at {C.AUDIT} snapshot {head}: "
          f"{'FAILED ' + ', '.join(failed) + '; main not moved' if failed else 'passed'}")
    return (C.EXIT_FAILED if failed else C.EXIT_OK), {"table": table, "snapshot": head, "failed": failed}


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

    def envelopes(self, frames) -> dict:
        """`{schema id: writer schema}` of the ids among `frames`' `schema_id` that the registry
        serves as RawMessage, each fetched by id (catalog.fetch_schema), so a writer schema with a
        field added still reads; an id it does not serve, or serves as another record, is left out."""
        from catalog import UnresolvableSchema, fetch_schema

        out = {}
        for (sid,) in frames.select("schema_id").where("schema_id IS NOT NULL").distinct().collect():
            try:
                schema = fetch_schema(sid, self.registry)
            except UnresolvableSchema as e:
                print(f"load: {e}; its records are not decoded")
                continue
            if is_envelope(schema):
                out[sid] = schema
        return out

    def _framed(self, table: str, snapshot: int, ranges: dict, columns: str = "*"):
        from pyspark.sql import functions as F

        import wire

        where, args = range_sql(ranges)
        return self.spark.sql(f"SELECT {columns} FROM {C.rel(table)} VERSION AS OF {C.snapshot_id(snapshot)} "
                              f"WHERE {where}", args).withColumn("schema_id", F.expr(wire.schema_id_guarded_expr("record_value")))

    def landed(self, landing: str, snapshot: int, ranges: dict, keep: str, bronze: str):
        """The landing's rows in `ranges` at `snapshot` with KEPT, `schema_id` and bronze's
        envelope columns (project_sql), each frame decoded by its writer schema id. A body that
        does not read decodes to null (decoded_sql) and is kept with its columns null, as is a
        record unframed or of an id the registry does not serve as RawMessage (keep_sql): parses
        fails each."""
        from pyspark.sql import functions as F

        columns = envelope_columns({f.name: f.dataType.simpleString() for f in self.spark.table(C.rel(bronze)).schema})
        raw = self._framed(landing, snapshot, ranges)
        schemas = self.envelopes(raw)
        out = (raw.where(F.col("schema_id").isNull() | ~F.col("schema_id").isin(list(schemas)))
               .withColumn(KEPT, F.lit(True)).selectExpr("*", *project_sql({}, columns)))
        for sid, schema in sorted(schemas.items()):
            decoded = SparkLanding._decoded(raw.where(F.col("schema_id") == sid), schema)
            fields = {f.name: f.dataType.simpleString() for f in decoded.schema["envelope"].dataType.fields}
            out = out.unionByName(decoded.withColumn(KEPT, F.expr(keep))
                                  .selectExpr("*", *project_sql(fields, columns)).drop("envelope"))
        return out

    @staticmethod
    def _decoded(raw, schema: str):
        """`raw` with column `envelope`: record_value's body under `schema`, NULL where it does not
        read (decoded_sql)."""
        from pyspark.sql import functions as F
        from pyspark.sql.avro.functions import from_avro

        import wire

        out = raw.withColumn("envelope", from_avro(F.expr(wire.body_expr("record_value")), schema,
                                                   {"mode": "PERMISSIVE"}))
        return out.withColumn("envelope", F.expr(decoded_sql(out.schema["envelope"].dataType.fieldNames())))

    @staticmethod
    def stage_counts(frames) -> list:
        from pyspark.sql import functions as F

        return [tuple(r) for r in frames.groupBy("kafka_partition").agg(
            F.count(F.lit(1)), F.sum(F.when(F.col(KEPT), 1).otherwise(0)),
            F.sum(F.when(~F.col(KEPT), 1).otherwise(0)),  # a null verdict counts as neither, and is refused
            F.min("kafka_offset"), F.max("kafka_offset")).collect()]

    def stage_rows(self, table: str, frames, props: dict) -> int:
        from pyspark.sql import functions as F

        # by name: the landing's offset key, timestamp and lineage, landed()'s schema_id and envelope
        # columns; record_key and record_value are left in the landing
        self._write(frames.where(F.col(KEPT)).select(*self.columns(table)),
                    f"{C.rel(table)}.{C.name('branch_' + C.AUDIT)}", props)
        head = self.refs(table)[C.AUDIT]
        return int(self._sql(f"SELECT `summary`['added-records'] FROM {C.rel(table)}.`snapshots` "
                             "WHERE `snapshot_id` = :s", s=head)[0][0] or 0)

    def summary_of(self, table: str, snapshot: int) -> dict:
        found = self._sql(f"SELECT `summary` FROM {C.rel(table)}.`snapshots` WHERE `snapshot_id` = :s",
                          s=C.snapshot_id(snapshot))
        return dict(found[0][0] or {}) if found else {}

    def firsts(self, table: str, snapshot: int, partitions: list) -> dict:
        """`{partition: min(kafka_offset)}` of `table` at `snapshot` over `partitions`, those it holds
        a row of. ponytail: a scan of those partitions, run only on a partition's first stage."""
        marks = ", ".join(f":p{i}" for i in range(len(partitions)))
        return dict(self._sql(f"SELECT `kafka_partition`, min(`kafka_offset`) FROM {C.rel(table)} VERSION AS OF "
                              f"{C.snapshot_id(snapshot)} WHERE `kafka_partition` IN ({marks}) GROUP BY `kafka_partition`",
                              **{f"p{i}": int(p) for i, p in enumerate(partitions)}))

    def offset_counts(self, table: str, snapshot, ranges: dict) -> list:
        """`[(partition, rows, offsets, first, last)]` of `table` at `snapshot` in `ranges`."""
        if snapshot is None or not ranges:
            return []
        where, args = range_sql(ranges)
        return self._sql("SELECT `kafka_partition`, count(*), count(DISTINCT `kafka_offset`), min(`kafka_offset`), "
                         f"max(`kafka_offset`) FROM {C.rel(table)} VERSION AS OF {C.snapshot_id(snapshot)} "
                         f"WHERE {where} GROUP BY `kafka_partition`", **args)

    def unparsed(self, landing: str, src: int, table: str, snapshot: int, ranges: dict, keep: str) -> tuple[int, list]:
        """How many frames of `ranges` in `table` at `snapshot` fail parses, each read again from
        `landing` at `src`, the snapshot the stage read, and the first LISTED by partition and
        offset as `(partition, offset, schema id, registered, decoded, problem)`: only the facts,
        never the bytes. A body that does not read decodes to null (decoded_sql), and fails."""
        from pyspark.sql import functions as F

        if not ranges:
            return 0, []
        where, args = range_sql(ranges)
        staged = self.spark.sql(f"SELECT `kafka_partition`, `kafka_offset` FROM {C.rel(table)} VERSION AS OF "
                                f"{C.snapshot_id(snapshot)} WHERE {where}", args).distinct()
        raw = self._framed(landing, src, ranges, "`kafka_partition`, `kafka_offset`, `record_value`").join(
            staged, ["kafka_partition", "kafka_offset"], "left_semi")
        columns = envelope_columns({f.name: f.dataType.simpleString() for f in self.spark.table(C.rel(table)).schema})
        envelopes = self.envelopes(raw)
        cols = ["kafka_partition", "kafka_offset", "schema_id"]
        facts = (raw.where(F.col("schema_id").isNull() | ~F.col("schema_id").isin(list(envelopes)))
                 .select(*cols, F.lit(False).alias("registered"), F.lit(False).alias("decoded"),
                         F.lit(None).cast("string").alias("problem"), F.lit(False).alias("kept")))
        for sid, schema in sorted(envelopes.items()):
            decoded = self._decoded(raw.where(F.col("schema_id") == sid), schema)
            fields = {f.name: f.dataType.simpleString() for f in decoded.schema["envelope"].dataType.fields}
            problem = ", and ".join(problems(fields, columns)) or None
            facts = facts.unionByName(decoded.select(*cols, F.lit(True).alias("registered"),
                                                     F.col("envelope").isNotNull().alias("decoded"),
                                                     F.lit(problem).cast("string").alias("problem"),
                                                     F.coalesce(F.expr(keep), F.lit(False)).alias("kept")))
        bad = facts.where(~(F.col("registered") & F.col("decoded") & F.col("kept") & F.col("problem").isNull()))
        listed = [tuple(r)[:6] for r in bad.orderBy("kafka_partition", "kafka_offset").limit(LISTED).collect()]
        return bad.count(), listed
