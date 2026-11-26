# ADR-049: Event bars are extended by new trades, not recomputed

**Status:** Withdrawn: superseded by [ADR-056](ADR-056-unified-data-product-platform.md) before acceptance, never built
**Date:** 2026-11-26
**Author:** Platform
**Category:** Lake / Data model
**Depends on:** [ADR-048](ADR-048-gold-step-reads-touched-data-writes-bars-once.md), landed and
measured for one full UTC day

---

## In plain words

Even after ADR-048, every five-minute run recomputes each touched day of event bars from all of
that day's trades, so its memory use grows from midnight to midnight UTC. Almost every batch of
new trades arrives after everything already stored, so the stored bars can simply be extended:
add the new trades to the day's last open bar and start new bars where they fill. We will do that,
and keep today's full-day recompute for every case where extending would be unsafe. It needs one
new column on `gold.bars`, each bar's exact dollar value, added in the lake and in ClickHouse. It
is not ready to accept until the list below is done, and the owner has one gate threshold to
confirm.

### Not ready to accept until

1. ADR-048 has landed and run for one full UTC day.
2. The measured gate below shows the window's memory and spill are still a problem; below its
   thresholds, which are proposals, this ADR is marked Rejected with the measurement.
3. The unsafe cases (an out-of-order or tied batch, a threshold change in `config/bars.yaml`, a
   commit on `gold.trades` that is not an append or a compaction) each route to the full-day path,
   with a test that would fail if they did not.
4. One UTC day in `shadow` mode (the fold computed beside the full path and compared every run,
   the full result written) has shown no difference.

---

## Context

Terms are explained where they first appear and are in the [glossary](glossary.md). Numbers and
their commands are in the [research note](../research/2026-11-26-incremental-bars-recompute.md)
("note §n"; "§1.n" is its finding n).

### Where this happens

An ingest run (`docker/lake/ingest.py`, every five minutes) has steps 1, 2b, 2c, 2d and 2e;
ADR-048 has the table. Step 2d, "stage 2d" in the code, is **the gold candle and bar step**: it
builds the gold tables from the run's new trades. **Event bars** (`gold.bars`, defined in
`docker/lake/bars.py`) are bars closed by a trade count (tick bars), a traded quantity (volume
bars) or a traded dollar value (dollar bars) instead of by the clock, at one threshold `T` per
instrument and kind in `config/bars.yaml`. They restart at 00:00 UTC, so the unit of work is a
**symbol-day**: one instrument, on one venue, for one UTC day.

### How a trade's bar is decided

The bar is a *cumulative bucket*. Order a symbol-day's trades by `(exchange_ts, recv_ts_ns,
trade_seq)`: the venue's timestamp, then the time K2 received the trade, then the trade id, which
together are a total order. For each trade, add up the units (1 per trade, the quantity, or price
times quantity) of every earlier trade that day: its *units before*. The trade belongs to bar
`floor(units before / T)`.

A worked example with tick bars and `T = 3`. Trades `a b c d e f g` have units before 0 to 6, so
they fall in bars `0 0 0 1 1 1 2`: `[a b c] [d e f] [g]`, and bar 2 is still open.

- **New trades after `g` touch only the end.** If `h` and `i` arrive later, their units before
  are 7 and 8, so both join bar 2: `[g h i]`. Bars 0 and 1 did not have to be reread; only the
  day's total so far (7) and the open bar were needed.
- **A late trade moves everything after it.** If `x` instead arrives with a timestamp between `b`
  and `c`, every trade from `c` on gains one unit before: `[a b x] [c d e] [f g]`. Every bar from
  the insertion point changes, so the day must be recomputed. Volume and dollar bars behave the
  same way, by `x`'s quantity or dollar value.

Spark computes units before with a *window function*, a running total over rows in a set order:
it *shuffles* the trades (moves rows between tasks through local disk so that each symbol-day
lands in one task) and *sorts* each symbol-day. A sort that outgrows its memory *spills* sorted
runs to disk and merges them later.

### Forces

- **The day's recompute is what is left.** At 21:26 UTC the window stage had 7 tasks (each holding
  whole symbol-days), 20.9 s of task time, its largest task 8.2 s, 375 MiB spilled and up to
  431 MiB of the 741 MiB Spark lends to tasks, all growing with the trades since 00:00 UTC.
  ADR-048 removes the larger avoidable stage after it, not this one. (note §3)
- **Batches arrive in order.** An *in-order batch* is a run's new trades for one symbol-day whose
  earliest `exchange_ts` is strictly later than the latest already stored for that day. 100 % of
  symbol-batches on 2026-11-25; at least 98 % for every venue on every other day in the table
  (the exceptions on 2026-11-24 tied the stored latest timestamp; on 2026-08-28, 0.24 % of one
  venue's were up to 317 s late). (note §1.6)
- **Extending a day needs its totals so far, exactly.** Prices and quantities are fixed-point
  integers (`price_e8` is the price times 10^8), so a trade's dollar value `price_e8 * qty_e8` is
  in units of 10^-16. The stored bars give the tick and volume totals exactly (`sum(trade_count)`,
  `sum(volume_e8)`), but each bar keeps only `quote_volume_e8`, its dollar sum divided by 10^8 and
  rounded down, and rounded parts do not add up to the rounded whole: two values of 1.5 units each
  round down to 1 + 1 = 2, their sum to 3. (note §4 (b))
- **Some changes must still recompute the whole day, and two would corrupt silently if missed.**
  A threshold changed in `config/bars.yaml` mid-day: the stored bars of that day were cut at the
  old threshold. A repair that deletes or overwrites rows of `gold.trades`: an *incremental read*
  (Iceberg returning only what commits between two snapshots added) returns `append` commits only
  and silently skips the rest (`BaseIncrementalAppendScan.appendsBetween` filters on the operation
  string `append`, read from the Iceberg 1.8.1 jar). (note §8)
- **Exactness is a contract.** [ADR-029](ADR-029-research-production-parity-contract.md) holds the
  stored bars equal, at tolerance zero, to a DuckDB recomputation and a Python reference
  (`make parity-bars`), and the nightly `bars_parity` audit in `docker/lake/maintenance.py`
  recomputes yesterday.
- **ClickHouse keeps a copy, unused today.** ClickHouse's `gold.bars`
  (`docker/clickhouse/ddl/10-gold-tables.sql`) is filled only by the manual pull in
  `docs/runbooks/clickhouse-rebuild-from-lake.md`, which names its columns, and is empty on the
  live stack. Bars never travel through Kafka: no Avro schema, no queue table in
  `20-gold-kafka.sql`. (note §1.8)

---

## Decision

**We will extend the stored bars with each in-order batch and recompute a whole day whenever
extension would be unsafe, because nearly every batch is in order and the full-day recompute is
what grows memory.**

To **fold** a batch is to extend the stored bars with it: start from the day's stored totals,
give each new trade its bar, add the trades that land in the open bar to it, and append the bars
after it, without reading the day's earlier trades.

### What a run does

1. **New trades and position.** ADR-048 already gives the step a *position*: the `gold.trades`
   snapshot recorded as the summary property `k2.src-snapshot-id.trades` (a key-value pair stored
   on an Iceberg snapshot) on every `gold.bars` publish, beside `k2.job=decode`, the property
   `offsets.latest_summary` looks for when it picks the newest such commit on `main`'s history.
   That is the [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) pattern: the position
   travels in the same commit as the data. "Newest" is by `committed_at`, the time Iceberg records
   at commit, so it does rest on a clock, one that only moves forward per table in practice because
   commits to one table are serialised. The trades not yet folded are what `gold.trades` gained
   since the position. A missing or expired position stops the step (ADR-048, change 1), so the
   fold never runs without one.
2. **Route each touched symbol-day to the full-day path when**
   - its earliest new `exchange_ts` is not strictly after the stored latest `close_time` (late or
     tied);
   - the stored `threshold` of any of its bars differs from `config/bars.yaml`;
   - ADR-048 marked its day fully touched, because an `overwrite` or `delete` commit on
     `gold.trades` since the position changed that `(exchange, day)` partition. ADR-048 finds
     those partitions in `gold.trades.all_entries`, an Iceberg metadata table with one row per data
     file per snapshot and a status saying whether that snapshot added, kept or deleted the file;
     a compaction (`replace`) passes. ADR-048 carries on rather than stopping, so this route runs;
   - the run's `bars_mode` is `full` or `shadow` (below).
   Every other symbol-day is folded.
3. **Fold.** Per bar kind, the day's units so far are `sum(trade_count)`, `sum(volume_e8)` or
   `sum(quote_volume_e16)` over the stored bars. `bars.bars_sql()` runs over the new trades only,
   with that total added to each trade's units before. If the first resulting bar has the stored
   open bar's number, the two combine: keep the stored `open_e8` and `open_time`, take the greater
   `high_e8` and the lesser `low_e8`, take `close_e8` and `close_time` from the new side, add
   `volume_e8`, `quote_volume_e16` and `trade_count`, and recompute `quote_volume_e8` from the
   exact sum.
4. **Check, then publish.** Before writing, a conservation check: for each touched symbol-day,
   the sums of `trade_count`, `volume_e8` and `quote_volume_e16` over the result must equal the
   count, `sum(qty_e8)` and `sum(price_e8 * qty_e8)` of that day in `gold.trades`. It is a hash
   aggregate (one record per symbol-day, no sort) over the day's files; a mismatch sends the day
   to the full path in the same run and files a row in `audit.checks`. Then ADR-048's publish:
   on the branch, `DELETE` the replaced rows (the open bar of a folded day, every row of a
   recomputed day), `append` the new ones, fast-forward `main`. One commit on `main`, so a day can
   never be half-written, which the fold needs: a day deleted but not rewritten would be folded
   from zero next time.

### The new column

`quote_volume_e16 DECIMAL(38,0)`: each bar's exact dollar value in units of 10^-16;
`quote_volume_e8` stays and equals it divided by 10^8, rounded down. Added in the lake and in
ClickHouse together (the owner's decision), with tests and docs in the same pull request, through
`.claude/skills/schema-change/SKILL.md`:

- Lake: `docker/lake/ddl/lake.sql`; written by `bars.bars_sql()` and the fold; compared by
  `bars.COMPARE_COLUMNS` and `scripts/parity_bars.py`.
- ClickHouse: `10-gold-tables.sql` (the column in `CREATE TABLE gold.bars` and an `ALTER TABLE
  ... ADD COLUMN IF NOT EXISTS` for running servers), the pull's column list in the runbook,
  `tests/clickhouse/bars.jsonl` and an assertion in `tests/clickhouse/assertions.sql`
  (`make test-clickhouse`).
- Contract and docs: `contracts/market-analytics/event-bars/gold.bars.odcs.yaml`'s description,
  `docs/architecture/13-schema-design.md`, `docker/lake/README.md`,
  `docs/operations/data-catalog.md`, `docs/products/market-analytics/event-bars.md`.
- Untouched, and why: `schemas/avro/*.avsc` and `20-gold-kafka.sql` (bars never travel on the
  wire); `tests/test_wire_format.py` (it checks Avro against the Kafka queue tables);
  `docs/architecture/14-partitioning-strategy.md` (no partition or sort change).

### Detecting drift, and the way back

- **Audit every changed day.** `bars_parity` recomputes every symbol-day whose bars changed since
  the previous audit (from `gold.bars`' snapshots since then), not only yesterday.
- **Duplicate keys.** A `duplicate_identifiers` audit on `gold.bars`' key: the bars audit compares
  through a dictionary keyed by bar, so two stored rows with one key would collapse silently.
- **Compaction.** Folded runs leave one small file per venue-month per run, where today each run
  replaces the last; `gold.bars` joins the nightly compaction in `maintenance.compact`.
- **Two run modes, set without a deploy.** An Airflow DAG param, `bars_mode`, passed to
  `ingest.py`, takes one of three values. `fold` is the normal mode this ADR introduces. `full` is
  the switch: every touched symbol-day goes to the full-day path, no fold is computed, and the run
  behaves as ADR-048 does. It is the rollback, and the setting to use while anything about the fold
  is in doubt. `shadow` is the comparison mode: every day still goes to the full path and the
  full result is what gets written, but the run also computes the fold for the same days, compares
  the two with `EXCEPT ALL` (the rows one side has and the other does not, counting duplicates),
  and files any difference in `audit.checks`; it costs two computations per run. Cutover is one
  UTC day in `shadow` with no difference, then `fold`.

### The gate

Measured after ADR-048 has landed, on the first full UTC day whose `gold.trades` partitions hold
at least as many bytes as 2026-11-25's (Trino over `gold.trades$files`, grouped by
`partition.exchange_ts_day`), at the last run before 00:00 UTC, for the stage that holds the
window (History Server: the stage's `peakExecutorMetrics.OnHeapExecutionMemory`, the memory Spark
lent its sorts and hash tables, and its `diskBytesSpilled`). Not the heap: the JVM's used heap also
counts garbage not yet collected, so it runs high on healthy runs and would almost never let this
gate reject.

**Proposed thresholds, to be confirmed by that measurement:** if the window stage's peak execution
memory stays below 370 MiB (half of the 741 MiB lent to tasks) and its spill below 256 MiB, this
ADR is marked Rejected with those numbers. For reference only: at 21:26 on 2026-11-25, before
ADR-048, that stage peaked at 343 MiB (431 MiB in the repeated computation) and spilled 375 MiB.

### Tests that land with it

- **Fold equals full recompute, on random data.** For many seeds: random trades split at a random
  point where the second part starts strictly later; folding the second part into the full bars of
  the first equals the full bars of all trades, every kind and column, in the Python reference and
  in DuckDB running the same bar SQL and combine step. Dollar values near 10^30 check overflow on
  DuckDB's 128-bit `HUGEINT`, not on Spark's `DECIMAL(38,0)`. Spark's behaviour depends on *ANSI
  mode*, its switch for standard SQL semantics: with it on, an arithmetic overflow is an error;
  with it off (Spark 3.5's default, which `spark_conf.py` does not change), a decimal sum that
  overflows becomes `NULL`, which the `NOT NULL` column would then refuse at write. So the Spark
  side is checked in `shadow` mode, not in CI.
- **Each unsafe case routes to the full path:** a late trade (and at least one later trade's bar
  moves, the example above as a test), a tie, a changed threshold, a non-append commit.
- **Day boundary:** a batch spanning `23:59:59.999999` and `00:00:00` folds the old day and starts
  the new one at bar 0 with zero totals.
- **Conservation check** fails on a dropped or doubled trade.

Rollout (note §6): pause the ingest; pull `main`; `make lake-rebuild LAYER=bars` (drops and
recreates `gold.bars` from the new DDL; it takes the lake lock); in ClickHouse apply
`10-gold-tables.sql`, `TRUNCATE TABLE gold.bars` and re-pull; re-pin `make parity-bars`; unpause
with `bars_mode=shadow` for one UTC day, then `fold`. Rollback: `bars_mode=full` first, which needs
no deploy; then, if the column must go, revert, rebuild without it, `DROP COLUMN` in ClickHouse and
re-pull.

---

## Rationale

**It removes what ADR-048 cannot.** After ADR-048 the remaining cost of the bars is the window
over the day, and only not doing it removes it. A fold reads the day's stored bars (845 KB for the
whole table today) and one run's trades; estimate: under 5 s at any hour, with no window sort
and so no spill from it (note §5). The gate makes sure it is still needed.

**In order is the measured common case**, and every case that is not goes to today's algorithm,
already under parity.

**One column makes the state exact.** Without it the fold would recompute the dollar total from
the day's trades on every run, most of the cost being removed.

**Parity becomes stronger.** Today the stored bars and the nightly audit are the same SQL over the
same day; after this the stored table is produced by folding and checked by whole-day
recomputations, every changed day, plus a conservation check every run.

---

## Alternatives Considered

| Option | Why it lost |
|---|---|
| Recompute from the first changed bar onward | With in-order batches the first changed bar is the open one, so it does the fold's work; without the new column it scans the day for the dollar total, and bars store only `exchange_ts` of their first and last trades, so a tie at the cut forces the cut back a bar at a time |
| A separate state table holding each day's totals | Spark cannot commit two Iceberg tables atomically, so after a crash the state and the bars can disagree; the bars' own rows already hold the state, apart from one column |
| Recompute hourly or nightly, serve today's bars on query | Each recompute still grows with the day, so the peak moves rather than goes; it breaks the event-bars contract's five-minute `schedule`; a query-time view would be a new engine for ADR-029 to hold at zero |
| Fold without the new column, summing dollar values from `gold.trades` each run | Exact, but reads the day's trades every run, which is the cost removed |
| The new column in the lake only | Decided against by the owner on 2026-11-26, so both copies state the same exact value |

---

## Consequences

**Easier.** The bar step's time and memory follow the batch, not the hour. Every changed day is
audited, and a conservation check runs every run.

**Harder.** Two code paths for one table, kept correct only by the tests, the `shadow` day, the
conservation check and the parity checks. Rollout and rollback each need the ingest paused, a
bars rebuild, a ClickHouse column change and a full re-pull, and a new parity pin. If the recorded
position no longer exists in `gold.trades` (the ingest stopped for longer than the 7-day snapshot
retention, or `gold.trades` was recreated by hand without rebuilding bars), runs fail until
`make lake-rebuild LAYER=bars`. `gold.bars` needs nightly compaction it does not need today.

**The ClickHouse part, honestly.** The column gives ClickHouse nothing until a scheduled pull
exists: today the table is empty and filled only by hand. The owner chose lake and ClickHouse,
so it is kept; its cost is a DDL change, an `ALTER` on the running server, a test fixture, a
runbook edit and a truncate and re-pull at rollout and at rollback.

**Committed to.** The bar definition, thresholds and order unchanged. `quote_volume_e16` in the
lake and ClickHouse, moved together. ADR-048's one publish per run and its position.

**Risks, and what is not yet verified.**

- Every "after" number is an estimate; nothing here has run.
- The fold's correctness rests on tests, the `shadow` day and the checks, not on a proof.
- The in-order share was measured over four days of data, one a full day of continuous ingest.

**Revisit when** out-of-order symbol-batches exceed 1 % over a UTC day (the Trino query in the
research note's appendix), the bar step takes more than 10 s on any run, or a conservation check
fails.

---

## Questions for the owner

- Decided 2026-11-26: `quote_volume_e16` lives in the lake and in ClickHouse.
- Open: accept the proposed gate thresholds (the window stage's peak execution memory 370 MiB and
  spill 256 MiB at the last run before 00:00 UTC, on a day at least as large as 2026-11-25)? Yes,
  or give the numbers.

### Future decisions, not part of this ADR

- **A scheduled ClickHouse pull for `gold.bars`.** Until one exists, ClickHouse's copy stays empty
  or manual.
- **Candles.** The 1-hour and 1-day candles still recompute their whole bucket. A future ADR should
  weigh folding them under the same in-order rule against rolling 5m, 1h and 1d up from the 1m
  candles: a roll-up needs no in-order rule and absorbs a late trade by recomputing its minute, but
  `quote_volume` would then be a sum of 1m values, which is not exact while the lake's 1m
  `quote_volume` is Spark's 6-place product (#213), so it needs an exact 1m notional first.

---

## References

- [`../research/2026-11-26-incremental-bars-recompute.md`](../research/2026-11-26-incremental-bars-recompute.md):
  measurements and commands, options, plan; §8 records this ADR's review and evidence
- [ADR-048](ADR-048-gold-step-reads-touched-data-writes-bars-once.md): the publish and position
  this depends on
- [ADR-029](ADR-029-research-production-parity-contract.md): the exact-parity contract
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md): the position carried with the data
- [`.claude/skills/schema-change/SKILL.md`](../../.claude/skills/schema-change/SKILL.md): the
  procedure the new column follows
- [`glossary.md`](glossary.md): every term above
