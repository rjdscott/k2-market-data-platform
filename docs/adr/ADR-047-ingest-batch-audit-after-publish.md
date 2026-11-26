# ADR-047: The ingest audits each batch after publishing it, and never blocks the commit

**Status:** Accepted
**Date:** 2026-11-26
**Author:** Platform
**Category:** Ingestion / Data quality

---

## Context

Stage 1 of the lake ingest reads a pinned Kafka offset range and commits it to
`raw.messages` with the end offsets in the same Iceberg commit
([ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)). The next run resumes from those
offsets. So a record the commit's offsets claim, but whose row is not in the commit, is never
read again: it is a hole in the system of record
([ADR-021](ADR-021-raw-first-archive-and-lineage.md)) that nothing repairs.

Until now one check could see that: `offset_continuity`, in the nightly maintenance run at
03:00 UTC (`docker/lake/maintenance.py` `AUDITS`). A batch committed just after it waits up
to 24 hours to be checked. Redpanda keeps raw records for 48 hours **or 2 GiB per partition,
whichever binds first** (`rpk topic describe market.crypto.v3.raw.kraken -c`, 2026-11-25:
`retention.ms 172800000`, `retention.bytes 2147483648`), and a byte cap has evicted unread
records before, at 512 MiB in August (`docker/lake/README.md`, "Why the default is
600,000"). A hole found at 03:00 can already be past re-reading.

Three constraints shape any per-batch check:

- **Time.** A cycle already takes most of its 5-minute schedule: the 19:07 UTC run on
  2026-11-25 ran from 19:07:22 to 19:11:20, 238 s (its Airflow task log).
- **Memory.** Nothing payload-bearing may be cached, and the driver heap is 1536m
  (`docker/lake/README.md`, "What one run may read, and why nothing is cached"; #205).
- **Retention.** A check that holds the commit holds the offsets, and the topics keep
  filling behind it.

Plan 003 pull request 3 asks for a batch audit that records its findings and raises an
alert "without blocking the commit". [ADR-031](ADR-031-governed-self-serve-platform.md)
reserves ADR-036 for write-audit-publish on **file** sources, where a bad write can wait.

---

## Decision

**We will audit each stage-1 batch after it is committed, from the files that commit added,
and never let the audit block, fail or undo the commit, because a blocked archive stops
reading Redpanda and the raw topics evict unread records sooner than a person can answer an
alert.** Scope: stage 1 of `docker/lake/ingest.py` only. Write-audit-publish for file
sources stays ADR-036's.

- **What it checks**, per (topic, partition), against the range the run read after any
  `--accept-data-loss` skip: rows written equal `end - start`, the first offset is `start`
  and the last is `end - 1` (`offsets.batch_audit`, pure). Because the plan is post-skip, an
  eviction already recorded as `offset_gap` is never reported a second time.
- **How**, an Iceberg incremental read of the one snapshot just committed (its parent to
  itself) projecting `topic`, `partition` and `offset`, grouped and collected: about 108
  rows back, no payload decoded, nothing cached (`ingest._batch_offsets`).
- **Where results go**, `audit.checks`, the one lake audit framework, through
  `catalog.write_audit_rows`: `job = 'ingest'`, `check_name = 'batch_offsets'`, `scope =
  '<topic>/<partition>'`, `observed` = planned minus found, `detail` = both ranges. One row
  per failing partition, so a run's rows cannot collide on `(run_ts, scope, check_name)`.
  An audit that raises is a row too, scope `lake.raw.messages`, `observed = -1` and a
  detail starting `check raised`, the form the nightly audit files for a check that raised
  (`offsets.CHECK_RAISED`), so the governance view `dq_results` reads it as `error`, not
  `fail`. No schema change: the columns are the ones every other check uses.
- **A clean batch files nothing.** 288 runs a day would otherwise add 288 commits a day to
  an append-only table that is never pruned, each saying only what the `raw.messages`
  commit already says. The run's log line (`stage 1: batch audit 0 failure(s) in <s>s`) is
  the evidence it ran, and `offset_continuity` stays the whole-table backstop.
- **Alerting** reuses the ingest findings' path: `k2.batch-audit-failures` on the audit
  commit, read by `lake-metrics` into `k2_lake_batch_audit_failures_total`, aged out after
  15 minutes like `k2_lake_offset_gaps_total`, and `LakeIngestBatchAuditFailed` (critical,
  no `for`). Runbook: [lake-ingest-batch-audit.md](../../legacy/v3-lake-ingest/docs/runbooks/lake-ingest-batch-audit.md).

---

## Rationale

The audit is worth having only while the missing records can still be re-read, which is
the retention window, and it is affordable only if it adds seconds to a run with about a
minute to spare. Reading three narrow columns of the files one commit added meets both: the
same aggregate over the files the newest ingest snapshot added, run through Trino as a
proxy, executed in 0.60 to 0.92 s over seven runs on 2026-11-25 UTC (the `EXPLAIN ANALYZE`
command and its `Execution:` line are in `docker/lake/README.md`, "What it costs per run";
the Spark figure is the log line above, to be recorded after rollout). Auditing after the
commit costs one property: a bad batch is visible to readers for the minutes before someone
repairs it. Bronze, silver and gold are rebuildable from `raw.messages`, so that window
costs a re-decode, not data.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Write-audit-publish: stage the batch, audit, then publish | A failing audit holds the offsets, the ingest stops advancing, and Redpanda evicts what it has not read. The pattern fits a source that keeps its data (ADR-036), not a bus with a byte cap |
| Audit the DataFrame before the commit | A per-partition count of `rows` is a second evaluation of the Kafka read, or a cache of payload rows; the cache is the 2026-08-26 OutOfMemoryError |
| Per-file manifest bounds and record counts | `raw.messages` is partitioned by `days(kafka_ts), topic`, so a file holds several Kafka partitions and its bounds cannot give a per-partition count |
| Fail the run on a mismatch | The commit has already happened; failing skips stage 2 for every venue and repairs nothing |
| A passing summary row per run | 288 commits a day on `audit.checks` forever, for a fact the ingest commit already records |
| Rely on the nightly `offset_continuity` | Up to 24 hours to detect, against a retention window a byte cap can make shorter than 48 hours |

---

## Consequences

**Easier:** a hole in a committed batch is a row and a critical alert within one run,
while Redpanda still holds the records; the recovery is a re-read, not a recorded loss.

**Harder:** a passing batch leaves no row, so "did the audit run" is answered by the task
log, not by the table. Every run pays one extra small Spark job and two metadata queries.

**Committed to:** dense offsets on the nine v3 topics. Today all are `cleanup.policy=delete`
(`rpk topic describe <topic> -c`, 2026-11-25) and the capture producer has no
`transactional.id` (`services/capture-rust/src/sink.rs` asserts it), so there are no
compacted offsets and no transaction markers; `offset_continuity` already relies on the
same fact.

**Risks**, and the blind spots behind them. The backstop for every one is the nightly
`offset_continuity` (`maintenance.audit_offset_continuity`): count, first and last offset
per partition over the whole of `raw.messages`, so a hole fails it with a positive
`observed` and a range written twice with a negative one, up to 24 hours later.

- **Count, first and last** per partition, so one hole and one duplicate in the same
  partition of the same batch net to a pass (the same blind spot `offset_continuity` has).
- **A batch the audit never saw.** A driver that dies after the `raw.messages` commit and
  before `audit_batch` finishes leaves that batch committed and unaudited, and no later
  run audits it: each run reads only the snapshot it committed itself.
  `scripts/chaos/lake-ingest-kill.sh` aims its SIGKILL at the commit, and a kill that
  lands after it is in this window. Only the nightly audit covers such a batch.
- **The batch against its own plan, not the plan against the previous commit.** A wrong
  `starts` (one past the committed offset leaves a hole between two batches) or a range
  committed twice is a batch that matches its plan, and it passes. An in-memory
  `starts[t][p] == committed[t][p]` check was considered and not added, because it would
  be a tautology: `starts` is `committed` copied through `next_starting_offsets` and
  `bounded_offsets` in the same call of `stage_raw`, from the same single read of the
  snapshot summary. It would re-test that copy, which
  `test_next_start_is_the_committed_end_verbatim` in `tests/test_lake_offsets.py` already
  pins, and a stale or doubled read of the committed offsets puts the same wrong value on
  both sides. A check with teeth re-reads the previous ingest commit's `k2.kafka-offsets`
  after the commit, which is I/O this decision does not add. Two writers committing the
  same range is what the writer lock (`docker/lake/lock.py`) prevents.
- **A finding that cannot be written** to `audit.checks` is only in the task log, and the
  alert stays silent for it.

**Revisit when:** any v3 topic gets `cleanup.policy=compact` or any producer to a v3 topic
sets `transactional.id` (offsets become sparse and every batch fails the check); or the
logged audit time exceeds 10 s on three consecutive runs; or `offset_continuity` fails on
a hole or duplicate that no `batch_offsets` row reported, at which point the audit gains
the cross-commit check above; or Redpanda's raw retention is raised to 7 days or more on
every raw topic, at which point a blocked archive can wait and write-audit-publish is
worth reopening for the bus.

---

## References

- [Plan 003, pull request 3](../plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md)
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md), [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md), [ADR-031](ADR-031-governed-self-serve-platform.md)
- `docker/lake/README.md`, "What one run may read, and why nothing is cached"
- [lake-ingest-batch-audit.md](../../legacy/v3-lake-ingest/docs/runbooks/lake-ingest-batch-audit.md), [lake-audit-failed.md](../../legacy/v3-lake-ingest/docs/runbooks/lake-audit-failed.md)
