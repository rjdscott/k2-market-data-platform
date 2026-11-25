# ADR-048: Stage 2d computes what a run touched: pruned reads, one evaluation and one commit now, an in-order fold for gold.bars next

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Lake / Compute

---

## Context

**What is wrong today** (2026-11-25 UTC runs; every number and its command are in the
[research note](../research/2026-11-26-incremental-bars-recompute.md)):

- **The bars are computed twice, and the second time is after the DELETE.** The cache
  filled by `fresh.count()` is not used by the append, in all 11 runs sampled: 43.3 s of the
  139 s of candle and bar steps at 21:26 (a 242 s run in a 300 s slot). Both incident
  failures sampled (17:46, 18:39, `OutOfMemoryError`) struck in that second evaluation, after
  the DELETE had committed, leaving those symbol-days without bars.
- **Every `gold.trades` read in stage 2d is the whole table.** Ten scans a run, each 1,133 of
  1,133 data files, none skipped, because the joins compare an expression of `exchange_ts`
  and nothing reaches the Iceberg scan. The four candle `MERGE`s (47 s) grow with history,
  not with the day.
- **The heap peak is in the bars.** 1,276 of 1,536 MiB, in a sort-merge join against a
  hundred-row threshold table nobody broadcasts plus a sort aggregate forced by `min_by` over a
  `struct` (560 MiB execution memory, 1.08 GiB spilled per evaluation), and in the window over
  the day's trades (343 to 431 MiB), the one part that is the day's recompute.

Constraints. ADR-029 holds `gold.bars` to tolerance zero against DuckDB and a Python reference;
the cumulative-bucket bar exists because one window expression is what three engines agree on,
and it makes all three kinds order-dependent (a late trade moves every later boundary of its
day). `gold.bars` is written in two commits with no transaction across them. Batches arrive in
order: 100 % of symbol-batches on 2026-11-25, at least 98 % on every day in the table. ClickHouse
holds a `gold.bars` copy, loaded only by the runbook's manual `iceberg()` pull, never from the
wire; on the live stack it is empty. The comment above `DRIVER_MEMORY` in
`docker/lake/spark_conf.py` already says more heap only buys time.

---

## Decision

**We will make stage 2d's cost follow what a run adds and write `gold.bars` in one commit per
run, in two stages: first, with no change to any table, contract or bar definition, prune
every `gold.trades` read to the touched `exchange_ts` range, evaluate the bars once, replace
the touched days in a single statement, broadcast the threshold table and order a bar's open
and close by its ordinal in the day; then fold each in-order batch into the day's stored bars,
adding an exact `quote_volume_e16` column to `gold.bars` in the lake and in ClickHouse through
the schema-change procedure and keeping the full-day recompute for any out-of-order or tied
batch, because most of today's cost is work that is not the recompute, and the part that is,
the day's window sort, holds the heap's peak.**

**What changes, stage 1** (one PR, no schema change; rows identical):

- `candles()` and `_bars()` gain a pushed `exchange_ts` range, which the existing
  `days(exchange_ts)` partition and file metrics prune by.
- `stage_bars` materialises the bars once (eager `localCheckpoint()`), and DELETE then append
  becomes one statement: `MERGE` with `WHEN NOT MATCHED BY SOURCE` limited to the touched days;
  if Iceberg 1.8.1 on Spark 3.5.5 rejects that, `overwritePartitions()` of the touched
  `(exchange, month)` partitions with their untouched rows carried; if both fail, DELETE and
  append on a branch then `fast_forward` of `main` (reported proven on this stack on
  2026-11-25). None is used before it passes a rehearsal on a scratch table in
  `lake.scratch_p4`.
- The candle `MERGE` source is checkpointed once; `_thresholds_view` is broadcast;
  `bars.DIALECT` orders `first`/`last` by `n_before`.

**What changes, stage 2** (one PR through `/schema-change`, after stage 1 has run a full UTC
day): the trades not yet folded are read from `gold.trades` since the snapshot on the newest
bars rows; a symbol-day whose earliest new `exchange_ts` is strictly after its stored latest
`close_time` is folded into the open bar and new bars, any other is recomputed whole, all in
stage 1's single statement. `quote_volume_e16 DECIMAL(38,0)` is added to:
`docker/lake/ddl/lake.sql`; `docker/clickhouse/ddl/10-gold-tables.sql` (`CREATE` and an
idempotent `ADD COLUMN IF NOT EXISTS`); the pull in `docs/runbooks/clickhouse-rebuild-from-lake.md`;
`tests/clickhouse/bars.jsonl` and `assertions.sql`; `bars.COMPARE_COLUMNS` and
`scripts/parity_bars.py`; `contracts/market-analytics/event-bars/gold.bars.odcs.yaml`'s
description; `docs/architecture/13-schema-design.md`. Unchanged, by inspection:
`20-gold-kafka.sql` and `schemas/avro/` (bars never travel on the wire), `tests/test_wire_format.py`
(it holds Avro against the queue tables), `docs/architecture/14-partitioning-strategy.md` (no
partition or sort change).

**What it costs.** Estimated, not measured, for the 21:26 run: stage 1 takes the candle and
bar steps from 139 s to 20 to 35 s and the run from 242 s to 125 to 140 s, but leaves the heap
peak in the day's window; stage 2 makes the bars step under 5 s and flat through the day, the
heap peak about 0.5 GiB. In effort and risk: a second bars code path, a lake and ClickHouse
schema change rolled out and back by a bars rebuild with the ingest paused, a moved parity pin,
and three write forms to rehearse before stage 1 can use one.

**What I need from you.**

1. Stage 2 straight after stage 1's measured day (the default), or stop at stage 1?
2. ClickHouse `gold.bars` is filled only by the manual runbook pull and is empty today. Is
   "the column in ClickHouse" the DDL and the pull's column list (what this ADR plans), or
   also a scheduled pull? The latter is not designed here.
3. If none of the three single-commit forms passes the rehearsal, ship stage 1 with (a)'s
   shorter two-commit window, or hold it?
4. Fold the 1h and 1d candles the same way now, or when the 1d `MERGE` passes 10 s?

---

## Rationale

**Remove the work that is not the recompute before redesigning the recompute.** Stage 1's
changes are each a few lines, leave every row identical and attack the measured costs. It also
moves the failure point: the evaluation that OOM'd now runs before any write, and the write is
one commit, so a failure or a kill leaves the old bars and never half a day.

**One commit per run in stage 1, not stage 2,** because the fold's correctness depends on it:
a day left absent by a half-applied write would be folded from zero. Doing it first makes the
fold a change to what the statement contains, not to how the table is written.

**Then fold, because stage 1 leaves the heap where it was.** The window over the day's trades
still grows from 00:00 to 24:00 UTC and still sets the high-water mark. A fold reads the day's
stored bars (under a megabyte) and one run's trades.

**One column, because the state has to be exact.** The tick and volume day totals are sums of
stored columns; the dollar total and the open bar's running notional are not, since
`quote_volume_e8` is floored per bar and `floor((a + b) / 1e8)` is not the sum of the floors.
ClickHouse carries it too so the served copy states the same exact notional as the lake.

**In-order only, with the full day as the fallback**, because order is what the definition
depends on, the measured out-of-order share is 0 to 2 %, and the fallback is today's algorithm,
already under parity.

**Parity gets stronger.** Today the stored bars and the nightly `bars_parity` recompute are
the same SQL over the same day; after stage 2 the stored table comes from the fold and three
whole-day recomputes check it.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Single evaluation alone, two commits kept | Fixes the failure point but keeps a window in which a day is empty, and the fold cannot rely on a write that can be half-applied |
| Recompute from the earliest affected bar onward | With batches in order the earliest affected bar is the open one, so it buys what the fold buys, while needing a day scan for the dollar prefix and tie handling at the cut. A third code path for nothing |
| Full-day recompute hourly or nightly, intraday bars on read | Each run stays linear in the day, so the end-of-day heap peak moves rather than goes; breaks the product's 5-minute schedule; an on-read view is a new engine for ADR-029 to hold at zero |
| A finer `gold.trades` partition or sort | A contract change and a rewrite of every file for a scan that prunes nothing today; the existing partition and metrics prune once a predicate reaches them |
| A third heap raise | Linear in the day, only buys time, and the container shares 8 GiB with the always-on JVMs |
| The new column in the lake only | The owner's decision: the served copy carries the same exact notional |

---

## Consequences

**Easier:** a run's time and heap follow its batch, not the table and the hour; a failure in
the bars step leaves the stored bars as they were, and no reader ever sees half a day; the
nightly bars audit checks a different algorithm from the one that wrote the table; both copies
of `gold.bars` carry an exact notional.

**Harder:** two code paths for one table, as right as the tests and the parity set make them.
The fold depends on the snapshot on the newest bars rows: expire it (a stop longer than
`gold.trades`' 7-day snapshot retention) or recreate `gold.trades` by hand without `gold.bars`,
and the run fails until `make lake-rebuild LAYER=bars`. Stage 2 rolls out with a bars rebuild,
a ClickHouse `ADD COLUMN` and a `TRUNCATE` plus whole-table pull, and back with a rebuild and
`DROP COLUMN`. Form 1 of the single commit drops the `k2.job` snapshot property from
`gold.bars` commits; nothing reads it for this table.

**Committed to:** the cumulative-bucket definition and the `(exchange_ts, recv_ts_ns,
trade_seq)` order, unchanged; one `gold.bars` commit per run; `quote_volume_e16` in both the
lake and ClickHouse, moved together; `duplicate_identifiers` on `gold.bars`'s key, since
`bars_mismatches` collapses duplicate stored rows silently.

**Risks:** that none of the three single-commit forms works on Iceberg 1.8.1 and Spark 3.5.5
(each rehearsed on `lake.scratch_p4` first; the branch form is reported proven but not
re-checked). That Spark keeps a sort aggregate after the ordering key becomes a `BIGINT`
(`EXPLAIN` before stage 1 merges). That the estimates are wrong: every "after" number above
comes from the measured stage breakdown, not a run.

**Revisit when:** out-of-order symbol-batches pass 1 % over a UTC day (the research note's
Trino query), so the fallback becomes the common path; any scheduled ingest run passes 200 s
after stage 2; an `OutOfMemoryError` appears in an ingest log again; or the 1d candle `MERGE`
passes 10 s, which extends the fold to candles under the same strict test.

---

## References

- [`../research/2026-11-26-incremental-bars-recompute.md`](../research/2026-11-26-incremental-bars-recompute.md):
  the measurements, the options, the implementation plan with tests, rollout and rollback
- [ADR-029](ADR-029-research-production-parity-contract.md): the parity contract this keeps
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md): the position travels with the data,
  which the fold's source snapshot on the bars rows reuses
- [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) and
  [`clickhouse-rebuild-from-lake.md`](../runbooks/clickhouse-rebuild-from-lake.md): ClickHouse
  `gold.bars` is a derived copy, loaded by pull
- `docker/lake/spark_conf.py`, the comment above `DRIVER_MEMORY`: the 2026-11-25 incident
- [`.claude/skills/schema-change/SKILL.md`](../../.claude/skills/schema-change/SKILL.md): the procedure stage 2 follows
