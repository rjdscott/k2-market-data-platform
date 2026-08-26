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
[`iceberg-offload-watermark-recovery.md`](../../legacy/v2-offload/runbooks/iceberg-offload-watermark-recovery.md)
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
| **Crash after the Parquet files are written, before the Iceberg commit** | orphan data files in MinIO; no snapshot | reads the same `startingOffsets` as the dead run, re-reads the same records, writes new files, commits | no duplicates, no gap. The orphans are unreferenced by any snapshot, so no reader ever sees them; the nightly `remove_orphan_files` reclaims the space, with a 24 h floor so it cannot race a live write ([maintenance.py](../../docker/lake/maintenance.py)) |
| **Crash after the commit, before the process exits** | the snapshot, with its offsets | reads the committed offsets, starts at the successor | no duplicates, no gap. The commit *is* the completion signal — there is nothing else to do after it |
| **Double-run** (two ingests launched over the same window) | both read the same start; both write | an exclusive `flock` in `ingest.py`'s `main()` — the second process exits 2 before it opens a Spark session. Prefect's `concurrency_limit=1` is belt and braces behind it, and covers only Prefect-launched runs; the runbooks, the chaos scripts and `make lake-verify` all dispatch by hand | no duplicates. **Without the lock there would be duplicates**, and not because Iceberg fails to notice: measured on a scratch table with this DDL's `commit.retry.num-retries=10`, two identical 5-row appends raised nothing and left 10 rows in two append snapshots. Iceberg's optimistic concurrency detects the conflict and its retry re-applies the loser on the new base — which is correct for an append and fatal for an append that also carries the reader's position |
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
| **Keep the PostgreSQL `offload_watermarks` table**, pointed at offsets instead of timestamps | Fixes the timestamp problem and leaves the two-store problem: the commit and the position update are still not atomic, so the crash window survives and so does [the watermark recovery runbook](../../legacy/v2-offload/runbooks/iceberg-offload-watermark-recovery.md). It also keeps PostgreSQL on the archive's critical path, where a Prefect database outage stops the system of record from advancing. |
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
read, with a topic truncation treated as an incident rather than a skip; one ingest at a
time enforced by an exclusive `flock` in the script rather than by the scheduler, because
the runbooks, the chaos scripts and `make lake-verify` all dispatch an ingest outside
Prefect (`grep -rn 'docker exec.*ingest\.py' docs scripts docker/lake` — 26 sites across
10 files on 2026-08-26); and
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

## Outcome so far

_2026-08-27, after the first scheduled runs. The ADR is still Proposed; this
records what the mechanism did on contact with the live stack, ahead of the
burn-in that will close it._

**The offsets were right. The first read was unbounded, and that was the bug.**
The first cron run (20:51 UTC, 2026-08-26) found no prior ingest snapshot, which
is the correct reading of an empty table, and did exactly what this ADR says:
resumed from EARLIEST on every partition. What the design never bounded was how
much "from EARLIEST" is — 41.5 M records / 9.5 GB across 108 partitions of a
48-hour retention. The driver died on `java.lang.OutOfMemoryError` in
`BasicColumnBuilder.appendFrom` and took the py4j gateway with it.

The proximate cause was a `persist(DISK_ONLY)` on the payload DataFrame, added so
that the *end offsets could be derived from the rows that were written* — a
consequence of pairing `endingOffsets=latest` with this ADR's requirement that
the committed offsets describe exactly the committed data. `latest` resolves
afresh on every evaluation, so the batch had to be pinned somehow, and caching it
was the obvious way. It is also the wrong way: `DISK_ONLY` serialises through an
in-memory columnar cache 10,000 rows at a time, and 10,000 Coinbase level2 frames
at up to 5.2 MB each never reach the disk they are nominally spilling to.

**The fix keeps the decision and removes the guess.** End offsets are now decided
in pure code before the read — `min(latest, start + --max-offsets-per-partition)`
per partition, from broker metadata (`offsets.bounded_offsets`, default 200,000,
unit-tested) — so the range is pinned by construction and nothing has to be
cached to know what a run consumed. Row counts come from the commit's own
`added-records`. `--end-timestamp` resolves to offsets through the broker instead
of Spark's `endingTimestamp`, which also closed a real hole: Kafka timestamps are
not monotonic within a partition, so the old timestamp filter could drop a record
sitting under a committed end offset and never archive it.

Three consequences for this ADR's own claims:

- **"Re-running an ingest that did not commit is a no-op" survived its first real
  test.** The OOM run committed nothing and the next run read the same range —
  the behaviour the consequence table predicts for "crash before the Iceberg
  commit": orphan files, no snapshot, no duplicates. Across four hand-run cycles
  the offsets committed equalled the rows written on every one (2,721,812 /
  1,770,914 / 1,564,334).
- **A cold start is now a sequence, not an event.** Draining is deterministic and
  idempotent, one bounded slice per run, with `k2.kafka-backlog` committed
  alongside the offsets and exported as `k2_lake_ingest_backlog_offsets{topic}`.
  The decision being tested here paid for that: the backlog is one more property
  on a commit that was already atomic, not a second thing to keep in step.
- **Reading a summary back by array position is not safe.** The exporter took
  "newest snapshot with `k2.job=ingest`" to mean the last matching entry of the
  metadata array. Lakekeeper 0.13.3 returned the same five snapshots in two
  different orders on two successive `loadTable` calls, and the lag gauge read a
  two-commit-stale run. `offsets.latest_summary` was always ordered by
  `committed_at` and was unaffected; `metrics.latest_job_snapshot` now orders by
  `timestamp-ms`. **The `k2.job` rule in the Rationale is necessary and was not
  sufficient** — the property identifies the writer; the timestamp is what
  identifies the newest.

**The "topic truncated" row of the consequence table then fired for real, and the
bound was why.** 50,000 offsets per partition per 5-minute cycle is below what
`market.crypto.v3.raw.kraken-0` receives (11,050 records/minute = 55,250 per
cycle, measured), so that partition fell further behind on every run until
Redpanda's 512 MiB-per-partition cap evicted 1,168,954 unread records. The ADR
predicted the failure mode and its handling exactly — a loud stop, no silent
skip, a human decision — and what it did not say is that **a bound below the
arrival rate makes this outcome certain rather than possible**. The default is
now 200,000, and `offsets.evicted` compares the resume point against the broker's
log start at plan time so the failure names every affected partition, its
committed offset and its record count before a Spark job starts, rather than
arriving as an `OffsetOutOfRangeException` inside a stack trace. Nothing is
repaired automatically: skipping forward is the unrecorded hole this ADR exists
to prevent.

**Measured, 2026-08-27:** peak ingest driver RSS 1,243 MiB against a 768m heap in
a 4 GiB container, and 53× the batch size moved that peak by *less than nothing* —
the largest run had the lowest peak. Numbers,
commands and the backlog drain are in
[`../../docker/lake/README.md`](../../docker/lake/README.md) under "What one run
may read, and why nothing is cached", and in the Phase D deployment gate.

**Still open for the burn-in:** whether the nightly `offset_continuity` audit
reports a non-abutting range once compaction and expiry have run against a table
built by a bounded, many-run cold start. The "Revisit when" trigger above is
unchanged.
