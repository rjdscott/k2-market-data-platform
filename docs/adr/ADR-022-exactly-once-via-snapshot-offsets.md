# ADR-022: Exactly-once ingest via Kafka offsets in the Iceberg snapshot summary

**Status:** Proposed
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Ingestion

---

## Context

v2's offload keeps its position in a PostgreSQL table. `offload_watermarks` holds one
row per target table — `last_offload_timestamp`, `last_offload_max_sequence`,
`last_successful_run`, `status` — and the job reads the watermark, selects above it over
JDBC, appends to Iceberg, then advances the watermark
([ADR-014](ADR-014-spark-based-iceberg-offload.md)). ADR-014 calls this
"exactly-once" and describes it as a three-layer guarantee: watermark, atomic commit,
deduplication.

It is not exactly-once, and the ADR's own appendix shows why. Two stores have to agree
and there is no transaction across them, so the window between the Iceberg commit and
the watermark update is a window in which a crash produces a re-read of already-written
rows. ADR-014's answer is Layer 3 — "Iceberg MERGE operation deduplicates by primary
key" — but the offload as built does a plain `.append()` with no merge and no key
(`docker/offload/offload_generic.py`), so the third layer does not exist in the code.
The v2 guarantee is at-least-once with a small window, and the operational evidence is
that the window needed a runbook of its own:
[`iceberg-offload-watermark-recovery.md`](../runbooks/iceberg-offload-watermark-recovery.md)
covers a watermark "stuck, stale, wedged in `running`, or needs rewinding". A position
store that needs a recovery procedure for its own state machine is a second system.

There are two further costs. The watermark is a *timestamp*, so correctness depends on
ClickHouse having no late-arriving rows below it — which is why ADR-014 buys a 5-minute
buffer window and accepts the freshness. And it makes PostgreSQL a hard dependency of
the archive path: the lake cannot advance if the Prefect metadata database is down.

v3 changes the source. Spark reads Redpanda by offset range rather than a serving
database by timestamp ([ADR-018](ADR-018-v3-lake-first-rust-capture.md)), so the position
is an offset per partition — a value with an exact successor, unlike a timestamp — and
the question becomes where to put it.

Spike S8 answered that mechanically before any of this was designed: against Lakekeeper
v0.13.3 and the Iceberg 1.8.1 Spark client, a write carrying
`.option("snapshot-property.k2.kafka-offsets", '{"0":42}')` committed and the property
was readable back out of `table.snapshots`
([ADR-018 Appendix A](ADR-018-v3-lake-first-rust-capture.md#s8--lakekeeper--iceberg-spark-client)).

---

## Decision

**We will store the consumed Kafka offsets in the Iceberg snapshot summary of the
commit that wrote them — `snapshot-property.k2.kafka-offsets` — and read the next run's
`startingOffsets` back out of the latest ingest snapshot, deleting the PostgreSQL
`offload_watermarks` table, because the offsets and the data then move in one atomic
commit and there is no second store to disagree with.**

Scope: both ingest stages. Stage 1 (Redpanda → `raw.messages`) commits
`k2.kafka-offsets` plus `k2.max-kafka-ts`; stage 2 (`raw.messages` → `bronze.*`) commits
`k2.src-snapshot-id`, which is the same idea one hop downstream — the position in the
archive rather than in the topic. `failOnDataLoss=true` on the Kafka read.

This is exactly-once *into the lake*, per run, and nothing broader. The capture tier's
delivery to Redpanda is at-least-once by construction and the venues themselves have no
delivery guarantee at all; what this decision buys is that the archive contains each
topic record exactly once, no matter how the ingest job dies.

---

## Rationale

**One store, one commit, no window.** The whole class of bug being removed is "two
durable stores, no transaction". Iceberg commits are atomic and the snapshot summary is
part of the commit, so after any crash there is exactly one durable fact: either the
snapshot exists — in which case both the rows and the offsets that produced them are
there — or it does not, and neither is. There is no ordering to get right, no `status`
column, and no state in which the two disagree, so there is no recovery procedure for
the position store because there is no position store.

**Offsets, not timestamps.** An offset has an exact successor: `endingOffsets` of run *n*
is `startingOffsets` of run *n+1*, with no gap and no overlap, and a partition's offsets
are dense. A timestamp watermark has neither property — it needs a lateness buffer
(ADR-014 chose 5 minutes), and choosing the buffer is choosing between duplicates and
loss with no way to detect which you got. Offset continuity is also *auditable after the
fact*: the maintenance audit checks that consecutive snapshots' offset ranges abut per
`(topic, partition)`, including across the day seam, and a hole in that sequence is a
failed audit rather than a silence.

**`failOnDataLoss=true`, deliberately loud.** If a run's `startingOffsets` fall below a
partition's earliest available offset — Redpanda's 48 h / 512 MiB-per-partition retention
evicted data before ingest read it (`docker/redpanda/init.sh`) — Spark can either skip to
the earliest surviving offset or fail. Skipping produces an archive with an unrecorded
hole, which is precisely the failure v3 exists to make impossible; failing produces a
loud job failure, an alert, and a decision made by a person who records the gap. The
default in Structured Streaming is to warn and continue, so this is a setting that must
be written down rather than assumed.

**Skipping non-ingest snapshots is a correctness requirement, not a detail.** Compaction
and snapshot expiry (`docker/lake/maintenance.py`, the shape ADR-017
established) commit their own snapshots, and those snapshots carry no
`k2.kafka-offsets`. "Latest snapshot" is therefore the wrong thing to read: after a
nightly compaction the latest snapshot is the compaction, and its missing property must
not read as "no offsets, start from the beginning" or as an empty map. The rule is
*latest snapshot whose summary contains `k2.kafka-offsets`*, and it is the single most
important line in `offsets.py` — which is why that module is pure and unit-tested
(`tests/test_lake_offsets.py`) rather than exercised only through Spark.

**The consequence chain, per failure:**

| Failure | What survives | What the next run does | Result |
|---|---|---|---|
| **Crash after the Parquet files are written, before the Iceberg commit** | orphan data files in MinIO; no snapshot | reads the same `startingOffsets` as the dead run, re-reads the same records, writes new files, commits | no duplicates, no gap. The orphans are unreferenced by any snapshot, so no reader ever sees them; `remove_orphan_files` in daily maintenance reclaims the space |
| **Crash after the commit, before the process exits** | the snapshot, with its offsets | reads the committed offsets, starts at the successor | no duplicates, no gap. The commit *is* the completion signal — there is nothing else to do after it |
| **Double-run** (two ingests launched over the same window) | both read the same start; both write | Prefect `lake-ingest-5min` runs at concurrency 1, so this is operator error; if it happens, the second commit conflicts on the base snapshot and Iceberg's optimistic concurrency rejects or retries it | at worst a retry re-reads a range already committed and appends duplicates — the one case that is *not* automatically safe, and the reason concurrency 1 is a deployment setting rather than a convention. The duplicate audit on identifier fields catches it |
| **Compaction snapshot lands between two ingests** | the compaction snapshot, no offsets property | the offset reader skips it and takes the latest snapshot *with* the property | correct resume. If the reader took "latest" instead, it would either fail or restart from zero — silently duplicating a day |
| **Topic truncated below the stored offset** (retention evicted unread data) | the offsets, pointing at records that no longer exist | `failOnDataLoss=true` → the job fails immediately | a loud failure and an alert, not a silent hole. Recovery is a human decision: record the gap, then resume from the earliest surviving offset explicitly. [`../runbooks/lake-ingest-lag.md`](../runbooks/lake-ingest-lag.md) |
| **Lakekeeper unreachable at commit time** | the data files; no snapshot | identical to the first row — the commit never happened | no duplicates, no gap; the run is a no-op with orphans |

**The property that makes all of this true in one sentence:** re-running an ingest that
did not commit is a no-op, and re-running one that did commit is impossible, because its
own commit moved the start.

**Why not keep the watermark table as a belt-and-braces cross-check.** Because a
cross-check between two stores that can disagree is a third state to handle, and the
question "which one is right" has no answer that is not just "the Iceberg one". Keeping
it would preserve the failure mode while adding the code to detect it.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Keep the PostgreSQL `offload_watermarks` table**, pointed at offsets instead of timestamps | Fixes the timestamp problem and leaves the two-store problem: the commit and the position update are still not atomic, so the crash window survives and so does [the watermark recovery runbook](../runbooks/iceberg-offload-watermark-recovery.md). It also keeps PostgreSQL on the archive's critical path, where a Prefect database outage stops the system of record from advancing. |
| **Spark Structured Streaming with a checkpoint directory** | The framework's own answer, and it costs a resident streaming runtime against a binding CPU budget — [ADR-004](ADR-004-eliminate-spark-streaming.md) deleted exactly this to buy 13.5 CPU back. The checkpoint is also a second store with the same atomicity gap against the Iceberg commit, plus a directory whose format is Spark's business and whose corruption is a known v1 incident class. |
| **Kafka consumer-group offsets** (commit back to Redpanda) | The idiomatic Kafka answer and the least atomic of all of them: the offset commit is a separate round trip to a separate system, so the crash window is exactly what it was in v2. It also makes the broker's retention the archive's memory, and the broker keeps 48 h. |
| **Idempotent writes keyed on `(topic, partition, offset)`** — MERGE instead of append | Genuinely exactly-once and pays for it on every row: a merge-on-read or copy-on-write MERGE against a table with no useful key distribution, at 700 records/s, rewriting files to delete duplicates that a correct start offset never creates. Spike S11 also left merge-on-read deletes untested against ClickHouse's `iceberg()` reader, so this would put an unverified read path under the rebuild story. |
| **Derive the resume point from the data**: `SELECT max(offset) FROM raw.messages GROUP BY topic, partition` | No extra store at all, and it reads the whole table's metadata on every run, breaks the moment a partition has a legitimate gap, and cannot distinguish "committed" from "committed and then compacted away". The snapshot summary is the same fact, already indexed, and free to read. |
| **Read "the latest snapshot" without filtering for the offsets property** | Simpler by three lines and wrong on the day after the first compaction — the nightly maintenance commit becomes the resume point and carries no offsets. This is the alternative that would have shipped if the maintenance job had been written after the ingest job instead of alongside it. |

---

## Consequences

**Easier:** a killed ingest needs no cleanup and no operator decision — re-run it;
proving the archive is gapless is a query over snapshot summaries rather than an audit of
a second database; the lake advances with PostgreSQL down; and "where did this run get
to" is answered by `SELECT summary FROM lake.raw.messages.snapshots`, which is the same
place the data is.

**Harder:** the resume logic now depends on an Iceberg implementation detail — that
snapshot summaries survive expiry of the snapshots *before* them but not of the snapshot
that carries them. Snapshot expiry must therefore never remove the newest ingest
snapshot, which couples the maintenance job's retention to the ingest job's correctness
in a way a separate watermark table did not. Debugging moves from `psql` to reading JSON
out of a snapshot summary. And the offsets are a JSON string in a metadata map, so
nothing type-checks them: a malformed write is only caught by the next run failing to
parse it, which is why `offsets.py` is pure and tested rather than inline in the job.

**Committed to:** `k2.kafka-offsets` and `k2.max-kafka-ts` as stable property names on
`raw.messages` commits and `k2.src-snapshot-id` on `bronze.*` commits — renaming one
orphans every prior snapshot as a resume point; `failOnDataLoss=true` on every Kafka
read, with a topic truncation treated as an incident rather than a skip; Prefect
concurrency 1 on `lake-ingest-5min` as a correctness setting, not a politeness one; and
deleting `docker/postgres/ddl/offload-watermarks.sql` together with `docker/offload/`
in the same cutover PR, so no code path can read a stale watermark.

**Risks:** the mechanism rests on one spike (S8) against one Lakekeeper version (v0.13.3)
and one Iceberg client (1.8.1); a catalog that drops or truncates snapshot summaries on
upgrade breaks resume silently rather than loudly, and nothing in the stack would notice
until offsets restarted at zero. Snapshot expiry misconfigured to keep zero ingest
snapshots is unrecoverable-by-code and would restart the ingest from the topic's earliest
offset. And exactly-once holds per run, not across a topic recreation: recreating a v3
topic resets the offset space, and the stored offsets then refer to a coordinate system
that no longer exists.

**Revisit when:** the audit reports a non-abutting offset range for any
`(topic, partition)` — the mechanism has a hole and this ADR gets an Outcome before
Phase E; or Lakekeeper/Iceberg is upgraded and the snapshot-property round trip is not
re-verified by the `--smoke` check in
[`docker/lake/spark_conf.py`](../../docker/lake/spark_conf.py); or ingest concurrency
above 1 is ever wanted, at which point the double-run row above stops being operator
error and needs a real answer.

---

## Related

- [ADR-018](ADR-018-v3-lake-first-rust-capture.md) — Decision §1 states the mechanism; Appendix A spike S8 proves it against Lakekeeper v0.13.3
- [ADR-014](ADR-014-spark-based-iceberg-offload.md) — the PostgreSQL watermark this replaces, and its own appendix's account of the crash window
- [ADR-017](ADR-017-iceberg-maintenance-pipeline.md) — the compact → expire → audit ordering Phase D keeps, and whose snapshots the offset reader must skip
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md) — why the offsets are also the lineage coordinate on every bronze row
- [ADR-023](ADR-023-lakekeeper-rest-catalog.md) — the catalog whose atomic commit this depends on, and why the Hadoop catalog could not provide it
- [`../runbooks/lake-recovery.md`](../runbooks/lake-recovery.md) — killed mid-run, Lakekeeper down, MinIO down: why re-running is safe
- [`../runbooks/lake-ingest-lag.md`](../runbooks/lake-ingest-lag.md) — lag, backlog slicing, and what to do when `failOnDataLoss` fires

---

## Outcome

_To be appended after the Phase D burn-in._
