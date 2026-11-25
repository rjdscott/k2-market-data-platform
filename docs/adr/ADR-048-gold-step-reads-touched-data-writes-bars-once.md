# ADR-048: Gold step reads only touched data and writes bars once

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Lake / Compute
**Followed by:** [ADR-049](ADR-049-event-bars-extended-not-recomputed.md), which builds on this one

---

## In plain words

Every five minutes the lake rebuilds its candles and event bars, and on 2026-11-25 that step
grew to more than half of each run and caused out-of-memory crashes. Most of the work changes
nothing: it reads the whole trade table instead of the few minutes that changed, it computes
the bars twice, and it moves and sorts every trade more often than the result needs. We will
read only the time range a run touched, compute the bars once, write them in one atomic commit,
and remove two avoidable passes over the trades. No table changes, and every stored row stays
exactly the same. The owner has already chosen the one-commit write; nothing is left open here.

---

## Context

### Where this happens: the ingest run and its steps

The lake is fed by one Spark job, `docker/lake/ingest.py`, that Airflow starts every five
minutes. A run is a sequence of steps, each reading what the step before it wrote:

| Step | Reads | Writes | Code |
|---|---|---|---|
| 1 | Kafka topics | `raw.messages`, every record byte for byte | `ingest.py` |
| 2b | `raw.messages` | `bronze.<venue>_*`, the venue's JSON decoded into columns | `bronze.py` |
| 2c | the bronze trade tables | `silver.trades_<venue>`, typed trades, duplicate deliveries flagged | `silver.py` |
| **2d** | `silver.trades_*` | **gold**: `gold.trades`, the instrument and venue dimensions, candles `gold.ohlcv_{1m,5m,1h,1d}`, event bars `gold.bars` | `gold.py`, `bars.py` |
| 2e | the bronze book tables | the order-book tables (`silver.book_*`, `gold.book_top20`, `gold.bbo_1s`) | `books.py` |

This ADR is about step 2d, which the code calls "stage 2d". From here on it is **the gold
candle and bar step**. It appends the run's new trades to `gold.trades`, one row per trade
across all venues, and then recomputes two derived products:

- **Candles** (`gold.ohlcv_*`): open, high, low, close and volume per time bucket (1 minute,
  5 minutes, 1 hour, 1 day). `gold.candles()` recomputes every bucket the run's trades fell in,
  from all of that bucket's trades, and `stage_ohlcv()` writes them with an Iceberg `MERGE`
  (update the bucket's row if it exists, insert it if not).
- **Event bars** (`gold.bars`): bars closed by a trade count (tick bars), a traded quantity
  (volume bars) or a traded dollar value (dollar bars), instead of by the clock. The threshold
  per instrument is in `config/bars.yaml`. Bars restart at 00:00 UTC, so the unit of work is a
  **symbol-day**: one instrument, on one venue, for one UTC day. `gold._bars()` recomputes every
  symbol-day the run touched from all of that day's trades, and `gold.stage_bars()` writes it.

### Five things you need to know about Spark and Iceberg to follow this

**A Spark DataFrame is a recipe, not data.** Building one does nothing. Each *action* on it (a
`count()`, a write) runs the whole recipe from the source tables again. `cache()` asks Spark to
keep the result of the first action and to substitute that stored result into later recipes
that contain the same steps. The substitution is Spark's choice, made by matching recipes.

**Shuffle, sort and spill.** To join or group rows by a key, Spark often *shuffles*: it moves
rows between tasks so that every row with the same key lands in the same task, writing them to
local disk and reading them back. Grouping or joining large inputs usually also *sorts* each
task's rows. When a sort needs more memory than the task can get, it *spills*: writes sorted
runs to disk and merges them afterwards. Spills cost time, and merging many of them costs
memory too.

**The heap.** Here Spark runs in local mode: one Java process (the driver) is also the only
executor, so every task of a run shares that process's memory, its *heap*: 1,536 MiB since
2026-11-25. Spark lends 741 MiB of it to running tasks for sorts, hash tables and buffers. A run
that needs more than the heap fails with `OutOfMemoryError`.

**An Iceberg commit and snapshot.** An Iceberg table is a set of Parquet data files plus
metadata listing them. A *commit* replaces that metadata with a new version in one atomic step,
and every commit creates a *snapshot*, an immutable version of the table. Readers always read
one whole snapshot, so a single commit is never seen half-done. But Spark gives no transaction
across two statements: a `DELETE` followed by an `append` is two commits, and between them the
table really is in the deleted state.

**Pruning.** `gold.trades` is *partitioned* by venue and UTC day (`PARTITIONED BY (exchange,
days(exchange_ts))`): each data file holds one venue's trades for one day. For every file
Iceberg also stores the smallest and largest `exchange_ts` in it. When a query filters on the
column itself against constants, such as `exchange_ts >= TIMESTAMP '2026-11-25 21:20:00'`,
Spark hands that filter to Iceberg, and Iceberg skips every partition and file whose range
cannot match. That skipping is *pruning*. It only works for that shape of filter: a comparison
of the column to a value known when the query is planned.

### What was measured

All on the 2026-11-25 UTC runs, read from the Spark History Server; every number, and the
command that produced it, is in the
[research note](../research/2026-11-26-incremental-bars-recompute.md) (cited as "note §n").

- **The step dominates and grows.** A run took 166 s at 07:56 UTC and 242 s at 21:26, against a
  300 s schedule. Everything outside the gold candle and bar step stayed at about 100 s; the
  step itself grew from 67 s to 139 s. (note §3)
- **The bars are computed twice.** `stage_bars()` calls `fresh.cache()` and `fresh.count()`,
  then `DELETE`, then `fresh.writeTo(...).append()`. The count's plan reads the cache; the
  append's plan does not, and reads `gold.trades` again, in all 11 runs sampled. That second
  computation is 43.3 s of the 139 s at 21:26. Why Spark did not substitute the cache is not
  established. (note §1, finding 1)
- **Every read of `gold.trades` is the whole table.** Each candle `MERGE` reads its input twice,
  as its query plan shows: once to find which `gold.ohlcv_*` files contain matching rows (so
  that only those files are rewritten) and once to produce the rows. With four timeframes and
  the two bar computations that is ten scans per run, each reading 1,133 of 1,133 data files at 21:26, none skipped. The
  reason is the join: `candles()` matches trades to buckets with
  `to_timestamp(floor(unix_timestamp(exchange_ts) / 60) * 60) = window_start`, and `_bars()`
  with `CAST(exchange_ts AS DATE) = day`. Both compare an *expression* of `exchange_ts` to
  values from another table, which is not the filter shape Iceberg can prune by, so every file
  is read and the join throws most rows away. Spark's dynamic partition pruning, which can turn
  a join into a runtime filter, only applies when the join key is the partition column itself.
  So the candle cost follows the size of the table, not of the minutes being recomputed: the
  1m candle `MERGE` grew from 7.3 s to 11.7 s while its work stayed the same size. (note §1,
  finding 2, §3)
- **The heap peak is in the bars.** The heap reached 1,276 of 1,536 MiB during the bar
  computation, against about 500 MiB in the candle steps. The largest stage (a group of tasks
  between two shuffles) is the last one of the bar query: 601 MiB shuffled, 75 s of task time,
  1,083 MiB spilled to disk, 560 MiB of the 741 MiB lent to tasks. The 2026-11-25 crashes failed
  in the code that reads spill files back (`UnsafeSorterSpillReader`, in the comment above
  `DRIVER_MEMORY` in `docker/lake/spark_conf.py`). Two avoidable choices create that stage; the
  Decision explains both. (note §1, finding 4, §3)
- **The crashes happened between the two commits.** The two failed runs sampled (17:46 and
  18:39 UTC) failed in the second computation, after the `DELETE` had committed. Each left the
  symbol-days it had touched with no bars until a later run touched them again. (note §1,
  finding 5)

### Constraints

- [ADR-029](ADR-029-research-production-parity-contract.md) requires the stored candles and bars
  to equal independent recomputations exactly, at tolerance zero: ClickHouse and DuckDB for
  candles, DuckDB and a Python reference for bars. A performance change must leave every row
  identical.
- The bars are a *cumulative bucket*: a trade belongs to bar `k` of its symbol-day when the
  total (count, quantity or dollar value) of the day's earlier trades lies between `k * T` and
  `(k + 1) * T`. Recomputing a symbol-day therefore needs its trades in order. The query does
  it with a *window function*, a running total computed over rows in a given order
  (`sum(...) OVER (PARTITION BY exchange, canonical_symbol, day ORDER BY ...)` in
  `bars.bars_sql()`): Spark shuffles the trades by symbol-day and sorts each day. That sort is
  the real cost of recomputing a day. This ADR leaves it alone; ADR-049 removes it.

---

## Decision

**We will make the gold candle and bar step read only the time range a run touched, compute
the bars once and write them in one commit, because most of its cost today is work that
changes no row.**

Five changes, all in `docker/lake/gold.py` and `docker/lake/bars.py`, then what stays the same.
Each change keeps every stored row identical; the reason is given with it.

### 1. Read only the touched time range

Before each candle or bar query, compute two constants on the driver: the earliest and latest
touched bucket (or day), one row of output from the run's touched keys. Add
`g.exchange_ts >= lo AND g.exchange_ts < hi` to the `gold.trades` side of `candles()` and
`_bars()`. This is the filter shape Iceberg prunes by. Expected reads per scan, from today's
file layout (each data file of the current day holds one run's five minutes): two or three
files per venue for 1m and 5m candles, about twelve for 1h, the day's files for 1d candles and
bars.

*Why rows stay identical:* every trade the existing join keeps lies inside `[lo, hi)`, so the
new filter only removes rows the join would have discarded. A late trade outside the run's
minutes widens the range and costs extra reads, never wrong rows.

### 2. Compute the bars once, before anything is written

Replace `cache()` and `count()` with an eager `localCheckpoint()`. It runs the recipe once,
stores the result in the driver's memory and local disk, and returns a new DataFrame whose
recipe is just "read those stored blocks". Later actions cannot recompute from `gold.trades`,
whatever Spark's cache does. The stored result is the bars, about 1.2 MiB at 21:26, not the
trades. The row count for the log comes from the commit's own summary (`added_records`, as
`rebuild_bars()` already does). The candle `MERGE` input is checkpointed the same way, so each
`MERGE` reads `gold.trades` once instead of twice.

*Why it matters beyond speed:* the computation is where the crashes struck. Done first, a crash
leaves the old bars in place.

### 3. Write the bars in one commit

Replace `DELETE` then `append` with one statement that replaces the touched symbol-days, so a
reader or a crash sees either the old day or the new one. Three ways to do it, in order of
preference:

1. **`MERGE ... WHEN NOT MATCHED BY SOURCE`.** A `MERGE` compares a source (the fresh bars)
   with the table on a key (`exchange, canonical_symbol, bar_kind, day, bar_seq`): rows in both
   are updated, rows only in the source are inserted, and the `NOT MATCHED BY SOURCE` clause
   deletes rows only in the table, limited here to the touched symbol-days. The delete matters:
   a recomputed day can lose a `bar_seq` it had before (one large trade can carry the total past
   two thresholds, so a bar number is skipped, and a late trade moves where that happens), and
   the stale row must go. It rewrites only the files holding touched days, and to find unmatched
   rows it reads the whole of `gold.bars` (845 KB today).
2. **`overwritePartitions()`.** `gold.bars` is partitioned by venue and month. This write
   replaces every partition that appears in the DataFrame with exactly the DataFrame's rows,
   in one commit. So the DataFrame must hold the touched days' fresh bars plus all other rows
   of those venue-months, read from the table first: it rewrites a month of bars per venue on
   every run.
3. **A branch, then a fast-forward (fallback).** An Iceberg branch is a named pointer to a
   snapshot, like a git branch; `main` is what readers read. Reset a branch to `main`, run
   today's `DELETE` and `append` on the branch, then `CALL lake.system.fast_forward(...)` moves
   `main` to the branch's snapshot in one commit. A crash before that leaves `main` untouched.
   Reported proven on this stack on 2026-11-25 by the P4 spike; not re-checked here.

None of the three has been run against Iceberg 1.8.1 and Spark 3.5.5 for this table. The pull
request starts with a rehearsal on a copy of one day of `gold.bars` in the scratch namespace
`lake.scratch_p4`, in a Spark session started by hand outside the ingest schedule: one new
snapshot per write, the touched day equal to the fresh bars, a stale row removed, an untouched
day unchanged, and a driver killed before the commit leaving the table at its previous
snapshot. The first form that passes is used.

### 4. Broadcast the threshold table

How Spark joins two tables decides how much it moves. A *sort-merge join* shuffles both sides
by the join key and sorts them; it is the default for two large inputs. A *broadcast hash join*
copies a small side whole to every task, where it becomes a hash table; the large side then
stays where it is, with no shuffle and no sort. Spark broadcasts automatically when it knows a
side is small (under 10 MB by default), from the table's statistics. The thresholds (about a
hundred rows, one per instrument and bar kind) are built by `_thresholds_view()` from Python
rows with
`spark.createDataFrame`, which carries no size statistics, so Spark assumes it is large and
shuffles every windowed trade of the touched days to meet it: the 601 MiB above. A broadcast
hint on that DataFrame removes the shuffle. It also helps the nightly `bars_parity` audit,
which uses the same view.

*Why rows stay identical:* the join strategy changes how rows meet, not which rows meet.

### 5. Find a bar's first and last trade by its position in the day

The bar query picks the open and close price with `min_by(price_e8, struct(exchange_ts,
recv_ts_ns, trade_seq))`: "the price of the row with the smallest order key". Spark's fast
*hash aggregate* keeps one small fixed-size record per group in a hash table and updates it
row by row. It can only do that when the record holds fixed-width values (numbers, timestamps,
decimals); a `struct` is not one, so Spark falls back to a *sort aggregate*, which sorts every
row by group before scanning. Here that is two sorts of every windowed trade, three times over
(one copy per bar kind), which is where the spills come from. The window step already computes
`n_before`, each trade's position in its symbol-day under the same order (0 for the day's
first trade). Use
`min_by(price_e8, n_before)` and `max_by(price_e8, n_before)` instead, in both the Spark and
DuckDB spellings in `bars.DIALECT`.

*Why rows stay identical:* a bar never spans two symbol-days, and within a symbol-day
`n_before` is that same order, so the smallest `n_before` in a bar is the same trade as the
smallest order key. The new DuckDB test below holds it.

### 6. What does not change

No table, column, partition, contract, threshold or bar definition. `make parity-bars` at the
current pin then compares bars stored by the old code with bars computed by the new SQL, which
is the proof the semantics held.

### Tests that land with it

- DuckDB runs `bars.bars_sql()` over seeded random trades (two venues, a day boundary, ties on
  `exchange_ts` and `recv_ts_ns`) and must equal the Python reference `bars.reference()` on
  every column. Nothing in CI executes the bar SQL today.
- The range helper's edges: a trade at `lo` is in, at `hi` is out, a day spans `[d, d + 1)`;
  and on random data, filter-then-join equals join alone.
- `stage_bars()` against a stub Spark that records calls: the frame written is the one an eager
  `localCheckpoint()` returned, no other action runs on an uncomputed bars frame, and exactly
  one write statement and no `DELETE` is issued. Today's code fails all three.
- If form 1 is chosen, DuckDB 1.4.4 runs the same `MERGE ... NOT MATCHED BY SOURCE` (it
  supports the clause): a stale tail is removed, an untouched day is unchanged, the same input
  twice gives the same rows.

Rollout, rollback and measurement are in note §6: pull `main` into the live checkout between
two runs; the next driver loads the new code. Rollback is `git revert`; no table moved.

---

## Rationale

The measured cost is mostly work no row needs: a second computation (43 s), whole-table reads
where a few files would do (most of the 47 s of candles), and a shuffle and two sorts that exist
only because of how the query was written. Each fix is a few lines, can be checked row for row
against the old output, and attacks a measured number rather than a guess. The one-commit write
removes the window in which a crash leaves a day without bars, and ADR-049 needs that property:
its incremental update is only correct if every write is whole.

What this does not fix: the window function that orders a symbol-day's trades still grows from
00:00 to 24:00 UTC, and it holds the heap's high-water mark once the avoidable stage is gone.
That is ADR-049's subject.

---

## Alternatives Considered

| Option | Why it lost |
|---|---|
| Compute once, keep `DELETE` then `append` | Saves the 43 s and moves the crash point before the `DELETE`, but keeps a window in which a day has no bars, and ADR-049 cannot build on a write that can be half-applied |
| Partition `gold.trades` more finely (by hour, or by a hash of the symbol) | A contract change and a rewrite of every data file, to fix a scan that skips nothing today; the existing day partitions and file ranges prune once the query states a range |
| Raise the driver heap again | It went from 768 MiB to 1,536 MiB on 2026-11-25. The cost grows with the table and with the day, so a third raise only postpones the next failure, and the container shares 8 GiB with other always-on processes |
| Go straight to incremental bars (ADR-049) | A schema change and a new algorithm in one step. It needs the one-commit write anyway, and is easier to verify on top of a step whose rows are proven unchanged |

---

## Consequences

**Easier.** Shorter runs: an estimate of 125 to 140 s instead of 242 s at 21:26 (note §5). The
candle cost stops growing with the table's history. A reader, or a crashed run, never sees a
symbol-day without bars. The bar SQL is executed in CI for the first time.

**Harder.** Three write forms to rehearse before one is chosen, on the live stack's scratch
namespace and outside the schedule. Form 1 is a SQL `MERGE`, which takes no write options, so
`gold.bars` commits lose the `k2.job` snapshot property (a key-value note stored on the
snapshot) that today's append sets; nothing reads it for this table.

**Committed to.** Every write to `gold.bars` is one commit. Every read of `gold.trades` in this
step carries the touched time range.

**Risks, and what is not yet verified.**

- That any of the three one-commit forms works on Iceberg 1.8.1 and Spark 3.5.5. If none passes
  the rehearsal, this ADR returns to the owner before the write changes.
- That Spark chooses the hash aggregate once the ordering key is a `BIGINT`. Expected from the
  types; checked with `EXPLAIN` (which prints the plan Spark will run) before merging.
- Why the cached bars were not reused. Seen in every run sampled, not explained; change 2 does
  not depend on the cache at all.
- Every "after" number is an estimate from the measured breakdown, not a measurement.
- The heap peak will barely move: the window over the day still needs memory that grows
  through the UTC day.

**Revisit when** any scheduled ingest run takes more than 200 s after this lands, or an
`OutOfMemoryError` appears in an ingest log again.

---

## Questions for the owner

- Decided 2026-11-26: the bars write becomes one atomic commit, in this ADR.
- Open: none.

---

## References

- [`../research/2026-11-26-incremental-bars-recompute.md`](../research/2026-11-26-incremental-bars-recompute.md):
  measurements and their commands, the options, implementation plan, rollout and rollback
- [ADR-049](ADR-049-event-bars-extended-not-recomputed.md): incremental event bars, which depend
  on this ADR
- [ADR-029](ADR-029-research-production-parity-contract.md): the exact-parity contract
- `docker/lake/spark_conf.py`, the comment above `DRIVER_MEMORY`: the 2026-11-25 heap incident
