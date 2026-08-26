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
MAX_KAFKA_TS = "k2.max-kafka-ts"
SRC_SNAPSHOT_ID = "k2.src-snapshot-id"
AUDIT_FAILURES = "k2.audit-failures"

# Values for the JOB property.
JOB_INGEST = "ingest"
JOB_DECODE = "decode"
JOB_MAINTENANCE = "maintenance"

# The partition count per topic is read from the broker, not configured — see
# `ingest.topic_partitions`. It matters because a partition that has never
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


def end_offsets(rows: list) -> dict:
    """`[(topic, partition, max_offset)]` -> `{topic: {partition: max+1}}`.

    +1 converts "the highest offset this run actually wrote" into Kafka's
    exclusive end, which is what `next_starting_offsets` copies verbatim.
    """
    out: dict = {}
    for topic, partition, max_offset in rows:
        out.setdefault(topic, {})[int(partition)] = int(max_offset) + 1
    return out


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
