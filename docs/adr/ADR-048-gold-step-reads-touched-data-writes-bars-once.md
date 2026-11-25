# ADR-048: Gold step reads only touched data and writes bars once

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Lake / Compute
**Followed by:** [ADR-049](ADR-049-event-bars-extended-not-recomputed.md), which builds on this one

---

## In plain words

Every five minutes the lake rebuilds its candles and event bars, and on 2026-11-25 that step
grew to more than half of each run and crashed out of memory. Most of its work changes nothing:
it reads the whole trade table where a few files would do, computes the bars twice, and moves
and sorts every trade more than the result needs. We will read only the trades added since the
step last finished, compute the bars once, and publish them in one atomic step, so a crash can no
longer leave a day without bars or a minute without its candle. No table changes and every stored
row stays the same; the cost is a few small code changes, a rehearsal on a scratch table, and a
row-for-row comparison on real data before merge. Nothing is left to decide: the owner chose the
one-step publish on 2026-11-26.

---

## Context

Terms in this ADR are explained where they first appear; the [glossary](glossary.md) has each
one too. Numbers are from the 2026-11-25 UTC runs; each has its command in the
[research note](../research/2026-11-26-incremental-bars-recompute.md) ("note §n"; "§1.n" is its
finding n).

### Where this happens

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
candle and bar step**. It appends the run's new trades to `gold.trades` (one row per trade,
all venues), then recomputes two products from them:

- **Candles** (`gold.ohlcv_*`): open, high, low, close and volume per time bucket of 1 minute,
  5 minutes, 1 hour and 1 day. `gold.candles()` recomputes every bucket the new trades fell in,
  from all of that bucket's trades, and `stage_ohlcv()` writes them with a `MERGE` (update the
  bucket's row if it exists, insert it if not).
- **Event bars** (`gold.bars`): bars closed by a trade count, a traded quantity or a traded
  dollar value instead of by the clock, at one threshold per instrument in `config/bars.yaml`.
  Bars restart at 00:00 UTC, so the unit of work is a **symbol-day**: one instrument, on one
  venue, for one UTC day. `gold._bars()` recomputes every symbol-day the new trades touched,
  from all of that day's trades, and `gold.stage_bars()` writes them.

Which buckets and symbol-days a run recomputes, its *touched keys*, comes today from the run's
own batch of new silver trades.

### The mechanisms that matter here

**A Spark DataFrame is a recipe, not data.** Building one does nothing. Each *action* on it (a
`count()`, a write) runs the whole recipe from the source tables again. `cache()` asks Spark to
keep the result of the first action and substitute it into later recipes that contain the same
steps; whether it does is Spark's choice, made by matching recipes.

**Shuffle, sort, spill and the heap.** To join or group rows by a key, Spark often *shuffles*:
it moves rows between tasks through local disk so that rows with the same key meet in one task.
Joins and groupings over large inputs usually also *sort* each task's rows, and a sort that
outgrows its memory *spills*, writing sorted runs to disk and merging them later. Here Spark runs
in local mode, so one Java process does all the work and every task shares its memory, the
*heap*: 1,536 MiB, of which Spark lends 741 MiB to running tasks. Running out of it is
`OutOfMemoryError`.

**Iceberg commit, snapshot, copy-on-write.** An Iceberg table is a set of Parquet data files plus
metadata that lists them. A *commit* swaps in a new metadata version atomically and creates a
*snapshot*, an immutable version of the table that readers see whole. Spark offers no
transaction across two statements, so `DELETE` then `append` is two commits with a real gap
between them. K2's tables are *copy-on-write*: changing one row means writing a new copy of every
data file that holds a changed row, never a separate delete file. The snapshot expiry keeps 7
days of snapshots, so every file a commit replaces stays on disk for 7 days.

**Branch and fast-forward.** An Iceberg *branch* is a named pointer to a snapshot, like a git
branch; `main` is the one every reader reads (since #214 K2's own readers also resolve `main`
explicitly). Commits on another branch are invisible to readers. A *fast-forward* moves `main` to
point at the branch's newest snapshot in one commit, keeping that snapshot's summary properties,
which is why K2's rule is that a branch reaches `main` only by fast-forward
(`docker/lake/README.md`).

**Pruning.** `gold.trades` is partitioned by venue and UTC day, and Iceberg stores each data
file's smallest and largest `exchange_ts`. A filter that compares the column itself to constants,
such as `exchange_ts >= <a timestamp>`, is handed to Iceberg, which then skips every partition and
file whose range cannot match: that is *pruning*. Any other shape, such as a function of the
column compared with values from another table, is evaluated only after every file is read.

### What was measured

- **The step dominates and grows.** A run took 166 s at 07:56 UTC and 242 s at 21:26, against a
  300 s schedule; everything outside the step stayed near 100 s, the step grew from 67 s to
  139 s. (note §3)
- **The bars are computed twice.** `stage_bars()` calls `cache()` and `count()`, then `DELETE`,
  then `append`. The append's plan does not use the cache and reads `gold.trades` again, in all
  11 runs sampled: 43.3 s of the 139 s. Why the cache was not reused is not known. (note §1.1)
- **Every read of `gold.trades` is the whole table.** Ten per run, each 1,133 of 1,133 data files,
  none pruned: `candles()` joins on `to_timestamp(floor(unix_timestamp(exchange_ts) / 60) * 60) =
  window_start` and `_bars()` on `CAST(exchange_ts AS DATE) = day`, functions of the column
  compared with another table. A candle `MERGE` reads its input twice, once to find which target
  files hold matching rows, once to write. (note §1.2)
- **The heap peak is in the bars.** 1,276 of 1,536 MiB during the bar query. Its largest stage
  shuffles 601 MiB, spills 1,083 MiB to disk and takes 560 of the 741 MiB lent to tasks; the
  2026-11-25 crashes failed reading spill files back. Two query choices create that stage
  (Decision, items 4 and 5). (note §1.4, §3)
- **A crash in the step leaves stale or missing rows.** The two failed runs sampled (17:46,
  18:39) died after the bars `DELETE` had committed, leaving their symbol-days without bars until
  a later run touched them. The 22:31 UTC run stopped inside the bar computation (its last event
  22:33:08; the containers restarted at 22:39:25, reported as a host out-of-memory kill). Its
  candles had committed, so no damage this time, but a run stopped between its `gold.trades`
  append and its candle `MERGE` leaves those 1-minute and 5-minute candles stale for good: the
  retry's touched keys come from new silver trades, and those minutes never get any.
  (note §8)

### Constraints

- [ADR-029](ADR-029-research-production-parity-contract.md) requires stored candles and bars to
  equal independent recomputations exactly. A performance change must leave every row identical,
  and the existing checks do not run the step's Spark code on real data: `make parity-bars`
  compares stored bars with DuckDB, the nightly `bars_parity` audit runs the same Spark SQL on
  both sides, and candle parity covers only 1-minute candles.
- A bar is a *cumulative bucket*: a trade's bar is the day's total before it divided by the
  threshold. Recomputing a day therefore needs its trades in order, a *window function* (a
  running total over rows in a set order), which shuffles by symbol-day and sorts each day. That
  sort is the real cost of recomputing a day; this ADR leaves it, ADR-049 removes it.

---

## Decision

**We will make the gold candle and bar step read only what changed since its last recorded run,
compute the bars once and publish them in one commit, because most of its cost today changes no
row.**

Six changes in `docker/lake/gold.py` and `docker/lake/bars.py`, each keeping every stored row
identical; the reason is given with each.

### 1. Touched keys come from the step's own position

Every bars publish records the `gold.trades` snapshot the step read, as the summary property
`k2.src-snapshot-id.trades` on the commit. The next run reads `gold.trades` incrementally from
that snapshot to the current one (an *incremental read*: Iceberg returns the rows that `append`
commits added in between) and takes its touched keys from those rows instead of from the silver
batch. The position is found with `offsets.latest_summary` along `main`'s history, the pattern
[ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) uses for Kafka offsets. Because the bars
publish is the step's last write, a run that stops anywhere in the step is redone in full by the
next one; candle `MERGE`s are idempotent, so redoing them is safe.

Two edges. An incremental read returns `append` commits only and silently skips any `delete` or
`overwrite` (verified: `BaseIncrementalAppendScan.appendsBetween` filters on the operation
string `append`). So if `main`'s history since the position holds a commit on `gold.trades` that
is neither an append nor a compaction, the run stops and names `make lake-rebuild LAYER=gold`
rather than miss the repair. The first run after deploy finds no recorded position and uses the
silver batch once. Cost: one incremental read of the files the last run appended, normally three
small files (estimate: under a second).

### 2. Read only the touched time range

From the touched keys, compute two constants: the earliest and the latest-plus-one bucket (or
day), collected as microseconds since the epoch with `unix_micros()`. Add `g.exchange_ts >=
timestamp_micros(lo) AND g.exchange_ts < timestamp_micros(hi)` to the `gold.trades` side of
`candles()` and `_bars()`. That is the shape Iceberg prunes by. Collecting a timestamp instead
would come back to Python as a naive local datetime and be re-read as UTC by the SQL, correct only
while the container clock is UTC (it is today, `Etc/UTC`). Expected reads per scan: two or three
files per venue for 1m and 5m candles, about twelve for 1h, the day's files for 1d and bars.

*Rows stay identical:* every trade the join keeps lies in `[lo, hi)`, so the filter only removes
rows the join would discard.

### 3. Compute the bars once, before anything is written

Replace `cache()` and `count()` with an eager `localCheckpoint()`: it runs the recipe once, keeps
the result in the Spark process's memory and local disk, and returns a DataFrame whose recipe is
"read those blocks", so nothing later can recompute from `gold.trades`. The result is the bars,
about 1.2 MiB at 21:26. The candle `MERGE` input is checkpointed the same way, so each `MERGE`
reads `gold.trades` once. The logged row count comes from the commit summary, as
`rebuild_bars()` does.

### 4. Publish the bars in one commit: a branch, then a fast-forward

Each run: point a fixed branch of `gold.bars` at `main` (`CREATE OR REPLACE BRANCH`), run today's
`DELETE` of the touched symbol-days and the `append` of the fresh bars on that branch, then
`CALL lake.system.fast_forward('gold.bars', 'main', '<branch>')`. Readers of `main` see the old
day or the new day, never the gap; a crash before the fast-forward leaves `main` as it was, and
the next run replaces the branch. The branch's intermediate snapshot (after its `DELETE`) does
join `main`'s history, so a time-travel read of that exact id would show the gap; nothing pins
it, because pins and readers take the snapshot `main` points at. The `DELETE` rewrites only the
files holding touched days and
the `append` writes only the fresh bars, about 308 KB each per run at 22:54 (note §8), so the cost
stays the size of the touched days. The append keeps its write options, so the commit carries the
position of change 1, and the fast-forward keeps it on `main`, as the P4 spike showed on
`lake.scratch_p4.wap_bronze` on 2026-11-25 (`docs/adr/glossary.md`, "Scratch namespace and the P4
spike"; `docker/lake/offsets.py`, `main_lineage`).

Why not a single `MERGE ... WHEN NOT MATCHED BY SOURCE THEN DELETE`, which looks like one
statement: Iceberg 1.8.1's Spark runtime has no `MERGE` rewrite of its own, and Spark 3.5.5's
`RewriteMergeIntoTable` disables both the pushed filter and the runtime file filter whenever that
clause is present (verified in its bytecode: the condition becomes `TrueLiteral` and the group
filter `None`). Copy-on-write would then rewrite every `gold.bars` file every run, today 853 KB
growing with history, and 7 days of retention would keep about 2,016 such copies: cost growing
with history, the pattern this ADR removes. A `MERGE` stays possible as the fallback only in the
other shape: carry the stale keys (stored bars of a touched day that the fresh result no longer
has) in the source with a flag, and delete them with `WHEN MATCHED AND s.stale THEN DELETE`.
Without a `NOT MATCHED BY SOURCE` clause Spark keeps its file filter, so only touched files are
rewritten; but a SQL `MERGE` takes no write options, so it cannot carry change 1's position.

Before the pull request is merged, the branch form is rehearsed on a copy of one day of
`gold.bars` in `lake.scratch_p4`, the producer lake's namespace for spikes that no job reads, in
a Spark session started by hand outside the schedule while holding the *lake lock* (the file lock
every lake writer takes, so the session cannot run beside an ingest). It passes when: `main` gains
one snapshot per run carrying the position; the touched day equals the fresh bars, a stale row is
gone and an untouched day is unchanged; the snapshot summaries' added and removed file bytes are
limited to the touched days' files; and a driver killed before the fast-forward leaves `main` at
its previous snapshot.

### 5. Broadcast the threshold table

A *sort-merge join* shuffles and sorts both sides by the join key; a *broadcast hash join* copies
a small side whole to every task as a hash table and leaves the large side in place, with no
shuffle and no sort. Spark broadcasts on its own when its statistics say a side is small (10 MB by
default). The thresholds, about a hundred rows built by `_thresholds_view()` from Python rows with
`spark.createDataFrame`, carry no statistics, so Spark assumes they are large and shuffles every
windowed trade of the touched days to meet them: the 601 MiB above. A broadcast hint removes it,
for the nightly `bars_parity` audit too. *Rows stay identical:* the strategy changes how rows meet,
not which.

### 6. Find a bar's first and last trade by its position in the day

Open and close are picked with `min_by(price_e8, struct(exchange_ts, recv_ts_ns, trade_seq))`.
Spark's *hash aggregate* keeps one fixed-size record per group in a hash table; it needs
fixed-width values, and a `struct` is not one, so Spark falls back to a *sort aggregate* that
sorts every row by group, here twice and over three copies of each trade (one per bar kind). The
window already computes `n_before`, each trade's position in its symbol-day in the same order;
`min_by(price_e8, n_before)` and `max_by(price_e8, n_before)` replace the struct, in both
spellings in `bars.DIALECT`. *Rows stay identical:* a bar never spans two symbol-days, and within
one `n_before` is that order.

### What stays the same

No table, column, partition, contract, threshold or bar definition.

### The gate before merge

- **Old against new, on real data.** At a pinned `gold.trades` snapshot, run the old and new
  `candles()` for all four timeframes over one batch's touched keys, and old and new `_bars()` for
  one full UTC day; compare each pair with `EXCEPT ALL` both ways, which must be empty. This runs
  the new Spark code on live tables in a hand-started session that writes nothing, and is the only
  check that does: nothing in CI or the parity set runs it. The same session prints each new
  scan's skipped-file count, which must not be zero.
- **CI.** DuckDB runs `bars.bars_sql()` over seeded random trades (two venues, a day boundary,
  ties on `exchange_ts` and `recv_ts_ns`) and must equal the Python reference `bars.reference()`;
  the range helper's edges (a trade at `lo` is in, at `hi` out); `stage_bars()` against a stub
  Spark issues one evaluation and one publish with no `DELETE` on `main`; touched keys come from the
  recorded position, and a non-append commit since it stops the run.
- **After the first UTC day written entirely by the new code**, re-pin `tests/parity/pinned.json`
  (the snapshot ids `make parity-ohlcv` and `make parity-bars` read, so a rerun reads the same
  data) to that day.

### Rollout and rollback

Rollout: pull `main` into the live checkout between two runs; the next run loads the new code.
Rollback: `git revert` does not remove rows a faulty version wrote, and closed 1-minute and
5-minute buckets are never recomputed. So a rollback reverts, then runs the old-against-new
comparison over every day the new code wrote; if any differs, `make lake-rebuild LAYER=gold`
(gold.trades, candles and bars rebuilt from silver under the lake lock; no candle-only rebuild
exists) with the ingest paused.

---

## Rationale

The measured cost is mostly work no row needs: a second computation (43 s), whole-table reads
where a few files would do, and a shuffle and two sorts that exist only because of how the query
was written. Each fix is a few lines and is checked row for row against the old output on real
data. Taking the touched keys from the step's own recorded position makes the step's work follow
what changed even across a crash, which is the subject of this ADR; publishing by branch and
fast-forward removes the gap in which readers see a day without bars, at the cost of the touched
days only, and carries that position. ADR-049 needs both: its incremental update is only correct
if every publish is whole and its position exact.

---

## Alternatives Considered

| Option | Why it lost |
|---|---|
| One `MERGE ... WHEN NOT MATCHED BY SOURCE` | Spark disables file filtering with that clause, so copy-on-write rewrites all of `gold.bars` every run and retention keeps ~2,016 copies: cost grows with history. Kept only as a `WHEN MATCHED AND s.stale` fallback, which filters but cannot carry the position |
| `overwritePartitions()` of the touched venue-months | One commit, but every run rewrites a month of bars per venue, carried through the DataFrame; cost grows through the month |
| Compute once, keep `DELETE` then `append` on `main` | Saves the 43 s, but readers still see the gap and a crash inside it still loses bars |
| Partition `gold.trades` more finely, or raise the heap again | A rewrite of every file for a scan that prunes nothing today; a heap raise only postpones a cost that grows with the day |

---

## Consequences

**Easier.** Shorter runs (estimate: 125 to 140 s instead of 242 s at 21:26; note §5). No reader
sees a symbol-day without bars, and a stopped run is redone in full by the next. The step's Spark
code is compared with its predecessor on real data before it ships.

**Harder.** A branch per table to manage (reset every run, published only by fast-forward). One
extra incremental read per run. A rollback is a revert plus a comparison, and possibly a gold
rebuild.

**Committed to.** `gold.bars` is published to `main` only by fast-forward, one commit per run,
carrying `k2.src-snapshot-id.trades`. Touched keys come from that position. Every read of
`gold.trades` in this step carries the touched time range.

**What this does not fix.**

- The 1-minute candle `MERGE` rewrites the newest data file of its venue-month, and that file
  keeps growing through the month: 1.09 MB rewritten per run at 08:57 UTC, 2.21 MB at 22:52
  (note §8). `gold.ohlcv_*` is not compacted.
- The 1-day candles still sort-aggregate the whole day (their `min_by` keeps the struct).
- The window over each touched day still grows from 00:00 to 24:00 UTC and holds the heap peak.

**Risks, and what is not yet verified.**

- Branch writes, `DELETE` on a branch and `fast_forward` on `gold.bars` with Iceberg 1.8.1: the
  P4 spike proved fast-forward on another table, not this sequence. If the rehearsal fails, this
  ADR returns to the owner before the write changes.
- That Spark chooses the hash aggregate once the ordering key is a `BIGINT`, and that
  `timestamp_micros(literal)` is folded to a constant early enough to be pruned by: checked with
  `EXPLAIN` and the skipped-file count in the gate.
- Every "after" number is an estimate from the measured breakdown.

**Revisit when** any scheduled ingest run takes more than 200 s after this lands, an
`OutOfMemoryError` appears in an ingest log, or the 1-minute candle `MERGE` rewrites more than
10 MB per run.

---

## Questions for the owner

- Decided 2026-11-26: the bars write becomes one atomic commit, in this ADR.
- Open: none.

---

## References

- [`../research/2026-11-26-incremental-bars-recompute.md`](../research/2026-11-26-incremental-bars-recompute.md):
  measurements and their commands; §8 records this ADR's review and its evidence
- [ADR-049](ADR-049-event-bars-extended-not-recomputed.md): incremental event bars, built on this
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md): the position travels with the commit
- [ADR-029](ADR-029-research-production-parity-contract.md): the exact-parity contract
- [`glossary.md`](glossary.md): every term above
