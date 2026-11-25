# ADR-048: Stage 2d computes what a run touched: pruned reads and one evaluation now, an in-order fold for gold.bars next

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Lake / Compute

---

## Context

Every 5-minute ingest ends stage 2d by recomputing candles and event bars for what the batch
touched (`docker/lake/gold.py`). On 2026-11-25 a run took 166 s at 07:56 UTC and 242 s at
21:26 of its 300 s slot, and from 17:12 UTC the ingest died of `OutOfMemoryError` inside the
`gold.bars` append until the driver heap went from 768m to 1536m (the comment above
`DRIVER_MEMORY` in `docker/lake/spark_conf.py`). That comment's own revisit trigger says more
heap buys time only, and that the fix is an incremental recompute. This ADR decides which.

Measured before deciding which, from the Spark History Server and read-only Trino
([research note](../research/2026-11-26-incremental-bars-recompute.md), every number with
its command):

- **The bars are evaluated twice.** `cache()` then `count()` fills a cache that the append
  after the DELETE does not use: its plan reads `gold.trades` again, in all 11 runs sampled.
  That second evaluation is 43.3 s of the 139 s of candle and bar steps at 21:26, and the two
  incident failures sampled happened in it, after the DELETE had committed.
- **Every `gold.trades` read in stage 2d is the whole table.** Ten scans a run, each 1,133 of
  1,133 data files at 21:26, none skipped: the joins compare an expression of `exchange_ts`
  to the touched keys, so nothing reaches the Iceberg scan. The four candle `MERGE`s (47 s)
  cost what they cost because of the table's size, not the day's, and so keep growing with
  history.
- **The heap peak is in the bars**, in a sort-merge join against a hundred-row threshold
  table nobody broadcasts and a sort aggregate forced by `min_by` over a `struct` (560 MiB of
  execution memory, 1.08 GiB spilled to disk per evaluation), and in the window over the
  day's touched trades (343 to 431 MiB), the one part that is the day's recompute. The JVM
  heap reached 1,276 MiB of 1,536 MiB in those stages, about 0.5 GiB in the candle stages.
- **Batches arrive in order.** No symbol-batch on 2026-11-25 started at or before its
  symbol-day's stored latest `exchange_ts`; on the other days in the table, at most 2 %.

Three constraints bound the answer. ADR-029 holds `gold.bars` to tolerance zero against
DuckDB and a Python reference at a pinned snapshot, and the cumulative-bucket bar was chosen
because one window expression is what three engines agree on. A bar's index is the units of
the day before its trade, so all three kinds are order-dependent: a late trade moves every
later boundary of its day. And `gold.bars` is written in two commits today, DELETE then
append, with no transaction across them.

---

## Decision

**We will make stage 2d's cost follow what a run adds, in two stages: first, with no change to
any table, contract or bar definition, prune every `gold.trades` read to the touched
`exchange_ts` range, evaluate the bars once before the DELETE, broadcast the threshold table
and order a bar's open and close by its ordinal in the day; then fold each in-order batch into
the day's stored bars in one `MERGE` per run, adding an exact `quote_volume_e16` column to
`gold.bars` through the schema-change procedure and keeping the full-day recompute as the
path for any out-of-order or tied batch, because most of today's cost is work that is not the
recompute, and the part that is, the day's window sort, holds the heap's peak.**

Scope: `gold.bars` and the candle reads in stage 2d. Candles keep their per-bucket
recompute and `MERGE`; only their reads are pruned and their source evaluated once.

- **Stage 1** (one PR): `candles()` and `_bars()` gain a pushed `exchange_ts` range, which
  the existing `days(exchange_ts)` partition and file metrics prune by; `stage_bars`
  materialises the bars with an eager `localCheckpoint()` before the DELETE; the candle
  `MERGE` source is checkpointed once; `_thresholds_view` is broadcast; `bars.DIALECT` orders
  `first`/`last` by `n_before`. Rows are unchanged; `make parity-bars` at the current pin and
  a new DuckDB-against-reference test hold that.
- **Stage 2** (one PR, `/schema-change`, after stage 1 has run a full UTC day): the trades
  not yet folded are read incrementally from the `gold.trades` snapshot the last `gold.bars`
  commit recorded. A symbol-day whose earliest new `exchange_ts` is strictly after its stored
  latest `close_time` is folded: `bars_sql` over the new rows with each kind's stored day
  total as an offset, combined with the open bar. Any other symbol-day is recomputed whole.
  Both go into one `MERGE`, with `WHEN NOT MATCHED BY SOURCE` deleting the stale tail of
  recomputed days, so every bars commit is whole.

---

## Rationale

**Remove the work that is not the recompute before redesigning the recompute.** Stage 1's
changes are each a few lines, leave every row identical and attack the measured costs
directly. Estimated, not measured: the 21:26 run's candle and bar steps from 139 s to 20 to
35 s, the run from 242 s to 125 to 140 s. It also moves the failure point: the evaluation that
OOM'd now runs before the DELETE, so a failure leaves the old bars in place.

**Then fold, because stage 1 leaves the heap where it was.** The window over the day's
trades still grows from 00:00 to 24:00 UTC and still sets the high-water mark. A fold reads
the day's stored bars (under a megabyte) and one run's trades, so the bars step should be
flat through the day (estimate: under 5 s), and the heap peak should fall to the candle
stages' level.

**One column, because the state has to be exact.** The tick and volume day totals are sums
of stored columns. The dollar total and the open bar's running notional are not:
`quote_volume_e8` is floored per bar, and `floor((a + b) / 1e8)` is not the sum of the
floors. `quote_volume_e16 DECIMAL(38,0)` is the bar's exact notional; it makes the fold an
integer computation that DuckDB can run in CI with the same `MERGE`.

**In-order only, with the full day as the fallback**, because order is what the definition
depends on and the measured out-of-order share is 0 to 2 %. The fallback is today's
algorithm, already under parity.

**Parity gets stronger, not weaker.** Today the stored bars and the nightly `bars_parity`
recompute are the same SQL over the same day. After stage 2 the stored table is built by the
fold, and the nightly audit, DuckDB and the Python reference recompute whole days against
it: three independent derivations instead of one derivation checked against itself.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Stage 1's single evaluation alone | Cuts 31 % of the steps and fixes the failure point, but leaves ten whole-table scans and the threshold sort. Kept, as part of stage 1 |
| Recompute from the earliest affected bar onward | With batches in order the earliest affected bar is always the open one, so it buys what the fold buys, while needing a day scan for the dollar prefix and tie handling at the cut because bars store only `exchange_ts`. A third code path for nothing |
| Full-day recompute hourly or nightly, intraday bars on read | Each run stays linear in the day, so the end-of-day heap peak moves rather than goes; breaks the product's 5-minute schedule; an on-read view in Trino or ClickHouse is a new engine for ADR-029 to hold at zero |
| A finer `gold.trades` partition or sort | A contract change and a rewrite of every file for a scan that prunes nothing today; the existing partition and metrics prune once a predicate reaches them |
| A third heap raise | What the spark_conf trigger rules out: linear in the day, only buys time, and the container shares 8 GiB with the always-on JVMs |
| Fold with no new column, prefix sums from `gold.trades` | Exact, but a scan of the day per run, and the open bar's notional needs the bar's membership, which ties at its first `exchange_ts` make ambiguous without recomputing it |

---

## Consequences

**Easier:** an ingest run's time and heap follow its batch rather than the table and the
hour; a failure in the bars step leaves the stored bars as they were; a day can no longer be
half-applied; the nightly bars audit checks a different algorithm from the one that wrote the
table; `gold.bars` carries an exact notional any reader can use.

**Harder:** two code paths for one table, the fold and the full day, and the fold is only as
right as the tests and the parity set make it. The fold depends on the snapshot the last bars
commit recorded: expire it (a stop longer than `gold.trades`' 7-day snapshot retention) or
recreate `gold.trades` by hand without `gold.bars`, and the run fails until
`make lake-rebuild LAYER=bars`.
Stage 2 rolls out and back by a bars rebuild with the ingest paused, and moves the parity pin.

**Committed to:** the cumulative-bucket definition and the `(exchange_ts, recv_ts_ns,
trade_seq)` order, unchanged; every `gold.bars` commit whole, so stage 2's slow path is a
`MERGE`, not DELETE then append; `quote_volume_e16` as a lake-only column, out of ClickHouse,
whose pull names its columns; `duplicate_identifiers` on `gold.bars`'s key, since
`bars_mismatches` collapses duplicate stored rows silently.

**Risks:** that Iceberg 1.8.1's copy-on-write `MERGE` accepts `WHEN NOT MATCHED BY SOURCE`
(Spark 3.5's grammar has it; not verified, and rehearsed on a throwaway table before stage 2
merges). That Spark picks hash aggregation once the ordering key is a `BIGINT` (expected from
the buffer types; `EXPLAIN` before stage 1 merges). That the estimates are wrong: every "after"
number above is derived from the measured stage breakdown, not run.

**Revisit when:** out-of-order symbol-batches pass 1 % over a UTC day (the research note's
Trino query), so the fallback becomes the common path; any scheduled ingest run passes 200 s
after stage 2; an `OutOfMemoryError` appears in an ingest log again; or the 1d candle `MERGE`
passes 10 s, which extends the fold to candles under the same strict test.

---

## References

- [`../research/2026-11-26-incremental-bars-recompute.md`](../research/2026-11-26-incremental-bars-recompute.md):
  the measurements, the five options, the implementation plan, the rollout and rollback
- [ADR-029](ADR-029-research-production-parity-contract.md): the parity contract this keeps
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md): positions in the snapshot summary,
  the pattern the fold's source snapshot reuses
- [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md): gold products carry
  the snapshot they were computed from
- `docker/lake/spark_conf.py`, the comment above `DRIVER_MEMORY`: the 2026-11-25 incident
