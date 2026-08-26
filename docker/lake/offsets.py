#!/usr/bin/env python3
"""
Offset bookkeeping for docker/lake/ingest.py. Pure functions — no Spark, no
network, no clock. tests/test_lake_offsets.py runs against this file directly.

**The exactly-once contract lives here.** There is no watermark table. The Kafka
offsets a run consumed are written into the Iceberg snapshot summary *by the
same commit that writes the data*, so the offsets and the rows they produced are
one atomic fact. A watermark row in PostgreSQL is two facts in two systems, and
every failure between them is a duplicate or a hole — which is exactly what v2's
offload watermark table had to be careful about and this design does not
(ADR-022; the offload itself was deleted in Phase D).

Reading back is therefore "what does the latest ingest snapshot say", and the
word *ingest* is load-bearing: compaction and snapshot expiry also produce
snapshots on `raw.messages`, and those carry no offsets. They are skipped by
`k2.job`, a property this code sets on every commit it makes, rather than by
guessing from Iceberg's own `operation` field.
"""

from __future__ import annotations

import json

# Kafka's sentinel offsets, as Spark's kafka source spells them in the
# startingOffsets/endingOffsets JSON.
EARLIEST = -2
LATEST = -1

# Snapshot summary properties. Spark writes them through
# `.option("snapshot-property.<name>", value)`; Iceberg surfaces them in
# `<table>.snapshots.summary`.
JOB = "k2.job"
KAFKA_OFFSETS = "k2.kafka-offsets"
KAFKA_BACKLOG = "k2.kafka-backlog"
MAX_KAFKA_TS = "k2.max-kafka-ts"
SRC_SNAPSHOT_ID = "k2.src-snapshot-id"
AUDIT_FAILURES = "k2.audit-failures"

# Values for the JOB property.
JOB_INGEST = "ingest"
JOB_DECODE = "decode"
JOB_MAINTENANCE = "maintenance"

# The partition count per topic is read from the broker, not configured — see
# `ingest.broker_offsets`. It matters because a partition that has never
# carried a record produces no row, so it never appears in a committed offset
# map, and a startingOffsets JSON that omits a partition leaves where it starts
# up to Spark. Filling the gap with EARLIEST makes it explicit and correct for
# an append-only archive.
#
# It used to be a `--partitions` flag defaulting to 12. A value below the real
# count silently dropped every committed offset above it and restarted those
# partitions at EARLIEST — a full re-ingest, i.e. duplicates, from one wrong
# number on a command line. The broker knows the answer; nothing else should
# be asked.


def encode(offsets: dict) -> str:
    """`{topic: {partition: offset}}` -> the JSON Spark's kafka source wants.

    Partition keys go out as strings and come back as ints; that asymmetry is
    Kafka's JSON format, not ours, so it is confined to this pair of functions.
    """
    return json.dumps(
        {topic: {str(p): int(o) for p, o in sorted(parts.items())} for topic, parts in sorted(offsets.items())},
        separators=(",", ":"),
    )


def decode(raw: str) -> dict:
    """Inverse of `encode`. Partition keys come back as ints."""
    return {
        topic: {int(p): int(o) for p, o in parts.items()}
        for topic, parts in json.loads(raw).items()
    }


def next_starting_offsets(committed: dict, partition_counts: dict) -> dict:
    """Where the next run starts, given what the last one committed.

    `partition_counts` is `{topic: partitions}` as the broker reports it.

    `committed` holds *end* offsets — exclusive, in Kafka's convention — so the
    next start is that same number with no arithmetic. Getting this wrong by one
    in either direction is a duplicate or a hole, and the fact that the correct
    operation is "copy it" is the whole reason `endingOffsets` are stored rather
    than "last offset seen".

    Any (topic, partition) the committed map does not mention starts at
    EARLIEST: for an archive that is never expired, the beginning of the topic is
    always the right place to start reading a partition nothing has read yet.

    A committed offset is never dropped, even for a partition outside
    `range(partitions)`. The count is written *under* the committed map rather
    than over it, so a count that is somehow short cannot rewind a partition to
    EARLIEST and re-ingest it — the failure mode that made this a broker lookup
    rather than a flag.
    """
    out = {}
    for topic, partitions in partition_counts.items():
        starts = {p: EARLIEST for p in range(int(partitions))}
        starts.update(committed.get(topic, {}))
        out[topic] = starts
    return out


def bounded_offsets(
    starting: dict, earliest: dict, latest: dict, max_per_partition: int, until: dict = None
) -> tuple:
    """Where this run reads from, where it stops, and what it leaves behind.

    `(starts, ends, backlog)` — the first two `{topic: {partition: offset}}`
    ready for Spark's `startingOffsets`/`endingOffsets`, the third
    `{topic: remaining records}` for the log line and the
    `k2_lake_ingest_backlog_offsets` gauge.

    `earliest` and `latest` come from the broker (`ingest.broker_offsets`);
    `until` is the offset of the first record at or after `--end-timestamp`,
    or None when there is no such bound.

    **This function replaced a `count()`-and-`max()` pass over the data.** Stage
    1 used to read to `endingOffsets=latest` and then derive the offsets it had
    consumed from the rows themselves — which meant the payload DataFrame had to
    be cached so the second pass saw the same `latest`, and caching a DataFrame
    whose rows are up to 5.2 MB of Coinbase level2 killed the driver with an
    OutOfMemoryError on the first cold start (41.5 M records / 9.5 GB across 108
    partitions, 2026-08-26). Deciding the end offsets *before* the read makes the
    range pinned, so nothing has to be cached to know what was in it, and
    `max_per_partition` puts a ceiling on how much a single run can pull.

    Three rules, each one a way to lose records if it is missing:

      * **Sentinels resolve first.** EARLIEST is -2, and `-2 + 50000` is an
        offset, not a bound.
      * **`latest` is a hard ceiling.** An end past it is a range Kafka cannot
        serve.
      * **An end never rewinds below its start.** Committed offsets above
        `latest` mean the topic was recreated under the lake; the honest read is
        an empty one, not a negative range.

    `max_per_partition <= 0` disables the bound and reads to `latest`, which is
    what a caught-up 5-minute cycle does anyway — the bound only bites on a
    backlog.
    """
    starts: dict = {}
    ends: dict = {}
    backlog: dict = {}
    for topic, partitions in starting.items():
        starts[topic], ends[topic], backlog[topic] = {}, {}, 0
        for partition, offset in partitions.items():
            first = int(earliest.get(topic, {}).get(partition, 0))
            end_of_log = int(latest.get(topic, {}).get(partition, first))

            start = int(offset)
            if start == EARLIEST:
                start = first
            elif start == LATEST:
                start = end_of_log

            stop = end_of_log
            if until is not None:
                # -1: the broker holds no record at or after the instant, so
                # every record in the partition is under the bound.
                at_instant = int(until.get(topic, {}).get(partition, -1))
                if at_instant >= 0:
                    stop = min(stop, at_instant)
            if max_per_partition > 0:
                stop = min(stop, start + max_per_partition)
            stop = max(stop, start)

            starts[topic][partition] = start
            ends[topic][partition] = stop
            backlog[topic] += max(0, end_of_log - stop)
    return starts, ends, backlog


def evicted(starts: dict, earliest: dict) -> list:
    """Partitions whose start offset is below what the broker still holds.

    `[(topic, partition, start, log_start, records_lost)]`, empty when the
    archive is still inside retention.

    This is ADR-022's "topic truncated below the stored offset" row, and Spark
    will find it too — `failOnDataLoss=true` raises `OffsetOutOfRangeException`
    on the first fetch. It is detected here as well because *when* and *how* a
    permanent hole is reported decides how well it is handled: the Kafka
    exception names one partition, arrives 384 lines into a stack trace after a
    Spark job has started, and carries neither the committed offset nor the
    count. The runbook's first step is "establish exactly what was lost, per
    partition", and that is a list this function can produce before anything
    reads a byte.

    It never repairs. Advancing the start to `log_start` here would be Spark's
    `failOnDataLoss=false` behaviour written by hand — an unrecorded hole, the
    one outcome the design exists to prevent. The recovery is a human decision
    with a written record as its deliverable
    (docs/runbooks/lake-ingest-lag.md §3).
    """
    losses = []
    for topic, partitions in starts.items():
        for partition, start in sorted(partitions.items()):
            log_start = int(earliest.get(topic, {}).get(partition, 0))
            if int(start) < log_start:
                losses.append((topic, partition, int(start), log_start, log_start - int(start)))
    return losses


def merge_committed(previous: dict, produced: dict) -> dict:
    """Carry forward partitions this run read nothing from.

    A quiet 5-minute window on one symbol means its partition contributes no
    rows and so no end offset. Dropping it would send the next run back to
    EARLIEST on that partition and re-ingest the whole topic — the bug this
    function exists to not have.
    """
    merged = {topic: dict(parts) for topic, parts in previous.items()}
    for topic, parts in produced.items():
        merged.setdefault(topic, {}).update(parts)
    return merged


def latest_summary(snapshots: list, job: str):
    """Newest snapshot summary written by `job`, or None.

    `snapshots` is `[(committed_at, summary_dict)]` — whatever ordering key the
    caller has; `<table>.snapshots` gives `committed_at`, which is monotonic per
    table because Iceberg commits are serialised.

    Snapshots from compaction (`rewrite_data_files`) and expiry carry no `k2.job`
    and are skipped. That is the "skip maintenance snapshots" rule, expressed as
    a property we set rather than as a guess about Iceberg's `operation` field:
    a future maintenance procedure that happens to report `append` would slip
    past an operation check and cannot slip past this one.
    """
    matching = [(ts, s) for ts, s in snapshots if s and s.get(JOB) == job]
    if not matching:
        return None
    return max(matching, key=lambda pair: pair[0])[1]


def offset_gaps(rows: list) -> list:
    """Offset continuity per (topic, partition), from aggregate rows.

    `rows` is `[(topic, partition, n_rows, min_offset, max_offset)]`. A partition
    is intact iff its offsets form an unbroken run, i.e. `max - min + 1 == n`.
    Two failures are distinguishable and both matter:

      * `max - min + 1 > n` — records are missing. Either the ingest skipped
        them or the topic was truncated under it.
      * `max - min + 1 < n` — the same offset was written twice, which means the
        exactly-once contract broke.

    Grouping over the whole table rather than per day is deliberate: a gap that
    straddles midnight sits exactly on the seam between two `days(kafka_ts)`
    partitions, and a per-day check is blind to precisely that case.

    # ponytail: a full group-by over the offset column, ~O(rows) per nightly
    # run. Cheap at one host's volume. Past a few TB, bound it with
    # `WHERE kafka_ts >= <last audited day - 1>` and keep the seam by starting
    # one day early.
    """
    failures = []
    for topic, partition, n_rows, min_offset, max_offset in rows:
        expected = int(max_offset) - int(min_offset) + 1
        if expected == int(n_rows):
            continue
        failures.append(
            {
                "scope": f"{topic}/{partition}",
                "observed": expected - int(n_rows),
                "detail": (
                    "offsets {}..{} span {} records but {} rows are present ({})".format(
                        min_offset,
                        max_offset,
                        expected,
                        n_rows,
                        "missing" if expected > int(n_rows) else "duplicated",
                    )
                ),
            }
        )
    return failures
