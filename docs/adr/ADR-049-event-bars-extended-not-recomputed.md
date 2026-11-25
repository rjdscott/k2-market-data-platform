# ADR-049: Event bars are extended by new trades, not recomputed

**Status:** Proposed
**Date:** 2026-11-26
**Author:** Platform
**Category:** Lake / Data model
**Depends on:** [ADR-048](ADR-048-gold-step-reads-touched-data-writes-bars-once.md), and on one
measured UTC day of it running live

---

## In plain words

Even after ADR-048, every five-minute run recomputes each touched day of event bars from all of
that day's trades, so its memory use grows from midnight to midnight UTC. Almost every batch of
new trades arrives after everything already stored, so the stored bars can simply be extended:
add the new trades to the day's last open bar and start new bars where they fill. We will do
that, and keep today's full-day recompute for the rare batch that arrives out of order. It needs
one new column on `gold.bars`, in the lake and in ClickHouse, holding each bar's exact dollar
value, because the stored one is rounded. Two questions remain for the owner, at the end.

---

## Context

### Where this happens

An ingest run (`docker/lake/ingest.py`, every five minutes) has steps 1, 2b, 2c, 2d and 2e;
[ADR-048](ADR-048-gold-step-reads-touched-data-writes-bars-once.md) has the table. Step 2d,
"stage 2d" in the code, is **the gold candle and bar step**: it builds the gold tables
(`gold.trades`, the dimensions, candles and event bars) from the run's new silver trades.

**Event bars** (`gold.bars`, defined in `docker/lake/bars.py`) are bars closed by a trade count
(tick bars), a traded quantity (volume bars) or a traded dollar value (dollar bars) instead of by
the clock, at one threshold `T` per instrument and kind in `config/bars.yaml`. They restart at
00:00 UTC, so the unit of work is a **symbol-day**: one instrument, on one venue, for one UTC
day.

### How a trade's bar is decided

The bar is a *cumulative bucket*. Order a symbol-day's trades by `(exchange_ts, recv_ts_ns,
trade_seq)`: the venue's timestamp, then the time K2 received it, then the trade id, which is a
total order. For each trade, add up the units (1 per trade for tick bars, the quantity for volume
bars, price times quantity for dollar bars) of every **earlier** trade that day; call it the
trade's *units before*. The trade belongs to bar `floor(units before / T)`.

A worked example with tick bars and `T = 3`. Trades `a b c d e f g` have units before 0 to 6, so
they fall in bars `0 0 0 1 1 1 2`: `[a b c] [d e f] [g]`, and bar 2 is still open. Two things
follow:

- **New trades after `g` only touch the end.** If `h` and `i` arrive later, their units before
  are 7 and 8, so both join bar 2: `[g h i]`. Bars 0 and 1 are untouched, and nothing about them
  had to be reread; only the day's total so far (7) and the open bar were needed.
- **A late trade moves everything after it.** If instead `x` arrives with a timestamp between
  `b` and `c`, every trade from `c` on gains one unit before: the day becomes `[a b x] [c d e]
  [f g]`. Every bar from the insertion point on changes, so the day has to be recomputed. The
  same holds for volume and dollar bars, by `x`'s quantity or dollar value.

In Spark the units before are a *window function*: a running sum over each symbol-day in order.
To compute it, Spark *shuffles* the trades (moves rows between tasks so that each symbol-day
lands in one task) and *sorts* each symbol-day. When a sort needs more memory than the task can
get, it *spills*: writes sorted runs to disk and merges them later.

### Forces

Numbers are from the 2026-11-25 UTC runs and the lake's tables; commands and detail are in the
[research note](../research/2026-11-26-incremental-bars-recompute.md) (cited as "note §n").

- **The day's recompute is what grows.** At 21:26 UTC the window step over the touched
  symbol-days had 7 tasks (each holding whole symbol-days), 20.9 s of task time, its largest task
  8.2 s, 375 MiB spilled to disk and up to 431 MiB of the memory Spark lends to tasks. All of it
  scales with the trades since 00:00 UTC. ADR-048 removes the larger avoidable stage after it,
  but leaves this one, and with it the high-water mark of the *heap* (the Java memory of the one
  Spark process, 1,536 MiB, which the 2026-11-25 crashes exhausted). (note §3)
- **Batches arrive in order.** Call a run's new trades for one symbol-day an **in-order batch**
  when its earliest `exchange_ts` is strictly later than the latest one already stored for that
  day. On 2026-11-25, 100 % of symbol-batches were in order. On every other day in the table the
  lowest share for any venue was 98 % (2026-11-24, where the exceptions tied the stored latest
  timestamp exactly). On 2026-08-28, 0.24 % of one venue's symbol-batches were up to 317 s late.
  (note §1, finding 6)
- **Extending a day needs its totals so far, exactly.** For tick and volume bars the stored rows
  hold them: `sum(trade_count)` and `sum(volume_e8)` over the day's bars. For dollar bars they do
  not. Prices and quantities are fixed-point integers (`price_e8` is the price times 10^8), so a
  trade's dollar value `price_e8 * qty_e8` is in units of 10^-16. Each stored bar keeps only
  `quote_volume_e8`, its sum divided by 10^8 and rounded down. Rounded parts do not add up to
  the rounded whole: two halves of 1.5 units each round down to 1 + 1 = 2, while their sum rounds
  down to 3. The open bar's running dollar value, needed to keep `quote_volume_e8` exact as it
  grows, is lost the same way. (note §4 (b))
- **A failed run must not be lost.** Today the touched symbol-days come from the run's own batch
  of silver trades. If a run fails after appending to `gold.trades`, its retry finds no new
  trades for that range and never recomputes their bars; only a later trade on the same
  symbol-day, or the nightly audit, repairs it. Read from the code; never observed. (note §1,
  finding 7)
- **Exactness is a contract.** [ADR-029](ADR-029-research-production-parity-contract.md) holds
  the stored bars equal, at tolerance zero, to a DuckDB recomputation and a Python reference
  over whole days (`make parity-bars`, and the nightly `bars_parity` audit in
  `docker/lake/maintenance.py`).
- **ClickHouse keeps a copy.** ClickHouse's `gold.bars`
  (`docker/clickhouse/ddl/10-gold-tables.sql`) is loaded from the lake only by the manual pull in
  `docs/runbooks/clickhouse-rebuild-from-lake.md`, which names its columns. Bars never travel
  through Kafka: no Avro schema, no queue table in `20-gold-kafka.sql`. On the live stack the
  table is empty today. (note §1, finding 8)

---

## Decision

**We will extend the stored bars with each in-order batch and recompute a whole day only when a
batch is out of order, because nearly every batch is in order and the full-day recompute is what
grows memory.**

To **fold** a batch is to extend the stored bars with it: start from the day's stored totals,
assign each new trade its bar, add the trades that land in the open bar to it, and append the
bars that follow, without reading the day's earlier trades.

### What a run does

1. **Find the trades not yet folded.** Every bar row carries `src_snapshot_id`, the
   `gold.trades` *snapshot* (an immutable, numbered version of an Iceberg table; each *commit*,
   one atomic change, creates one) it was computed from. Read `gold.trades` incrementally from
   the snapshot on the newest bar rows to the current one: Iceberg returns exactly the rows
   appended in between. The position travels with the data it describes, as
   [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) does for Kafka offsets, so a failed
   run's trades are picked up by the next run.
2. **Route each touched symbol-day.** In order (earliest new `exchange_ts` strictly after the
   stored latest `close_time`): fold. Otherwise, including an exact tie: recompute the whole day
   with today's query, pruned to that day by ADR-048.
3. **Fold.** Per bar kind, the day's units so far come from the stored bars: `sum(trade_count)`,
   `sum(volume_e8)`, or `sum(quote_volume_e16)` (below). `bars.bars_sql()` runs over the new
   trades only, with that total added to each trade's units before. The first resulting bar may
   have the same number as the stored open bar; the two combine by keeping the stored `open_e8`
   and `open_time`, taking the greater `high_e8` and the lesser `low_e8`, taking `close_e8` and
   `close_time` from the new side, and adding `volume_e8`, `quote_volume_e16` and `trade_count`.
   `quote_volume_e8` is then recomputed from the exact sum.
4. **Write once.** Folded rows and recomputed days go into the single commit ADR-048 introduces.
   That single commit is what makes step 1 safe: if a write could leave a day deleted but not
   rewritten, the next fold would restart that day from zero.

### The new column

`quote_volume_e16 DECIMAL(38,0)`: each bar's exact dollar value, the sum of `price_e8 * qty_e8`
in units of 10^-16. `quote_volume_e8` stays, and equals `quote_volume_e16` divided by 10^8,
rounded down. Per the owner's decision it is added in the lake and in ClickHouse together, with
tests and docs in the same pull request, through the procedure in
`.claude/skills/schema-change/SKILL.md`:

- Lake: `docker/lake/ddl/lake.sql` (`gold.bars`), written by `bars.bars_sql()` and the fold;
  compared by `bars.COMPARE_COLUMNS` (the nightly audit) and `scripts/parity_bars.py`.
- ClickHouse: `docker/clickhouse/ddl/10-gold-tables.sql` (`quote_volume_e16 Decimal(38, 0)` in
  `CREATE TABLE gold.bars`, plus `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for running
  servers), the pull's column list in `docs/runbooks/clickhouse-rebuild-from-lake.md`, and
  `tests/clickhouse/bars.jsonl` with an assertion in `tests/clickhouse/assertions.sql` that
  `quote_volume_e8` is `quote_volume_e16` divided by 10^8 (`make test-clickhouse`).
- Contract and docs: the description in
  `contracts/market-analytics/event-bars/gold.bars.odcs.yaml` ("a day a new trade touches is
  replaced whole" no longer holds), `docs/architecture/13-schema-design.md`,
  `docker/lake/README.md`, `docs/operations/data-catalog.md`,
  `docs/products/market-analytics/event-bars.md`.
- Not touched, and why: `schemas/avro/*.avsc` and `20-gold-kafka.sql` (bars never travel on the
  wire); `tests/test_wire_format.py` (it checks Avro against the Kafka queue tables, where bars do
  not appear); `docs/architecture/14-partitioning-strategy.md` (no partition or sort change).

Also added: a `duplicate_identifiers` audit on `gold.bars`'s key in
`docker/lake/maintenance.py`. The bars audit compares rows through a dictionary keyed by bar, so
two stored rows with one key would collapse into one silently; a table updated in place should
be checked for that directly.

### Tests that land with it

- **Fold equals full recompute, on random data.** For many seeds: random trades, split at a
  random point where the second part starts strictly later; folding the second part into the
  full bars of the first must equal the full bars of all trades, for every kind and column, in
  the Python reference and in DuckDB running the same bar SQL and combine step the lake runs. Include
  dollar values near 10^30, so an overflow of `DECIMAL(38,0)` would show.
- **Late trade:** routed to the full-day path, equal to the reference, and at least one later
  trade's bar differs from where an in-order arrival would put it (the example above, as a
  test).
- **Tie at the boundary:** an earliest new `exchange_ts` equal to the stored latest goes to the
  full-day path.
- **Day boundary:** a batch spanning `23:59:59.999999` and `00:00:00` folds the old day and
  starts the new one at bar 0 with zero totals.
- **One write per run** when a run has both folded and recomputed days.

Rollout and rollback (note §6). Rollout: pause the ingest DAG; pull `main`;
`make lake-rebuild LAYER=bars`, which drops and recreates `gold.bars` from the new DDL and fills
every day with the new column (it holds the lake lock, so a scheduled run that starts meanwhile
exits and is skipped); in ClickHouse, apply `10-gold-tables.sql`, `TRUNCATE TABLE gold.bars` and
re-pull the whole table; re-pin `make parity-bars` with `scripts/parity-bars.sh --pin-current`;
unpause. Rollback is the same in reverse: revert, rebuild without the column, `DROP COLUMN` in
ClickHouse, re-pull, re-pin.

---

## Rationale

**It removes the part ADR-048 cannot.** After ADR-048 the remaining cost of the bars is the
window over the day, and only not doing it removes it. A fold reads the day's stored bars (under
a megabyte for the whole table today) and one run's trades, so the bar step should be flat
through the day: an estimate of under 5 s at any hour, and a heap peak near the candle steps'
level, about 0.5 GiB at 21:26. (note §5)

**In order is the common case, by measurement.** The fast path covers 98 to 100 % of
symbol-batches on every day measured, and the slow path is today's algorithm, already proven
under parity.

**One column makes the state exact.** Without it the fold would have to recompute the dollar
total from the day's trades on every run, which is most of the cost this ADR removes.

**Parity becomes stronger.** Today the stored bars and the nightly audit are the same SQL over the
same day, so the audit can only catch a partly applied write. After this change the stored table
is produced by folding and checked against three whole-day recomputations.

---

## Alternatives Considered

| Option | Why it lost |
|---|---|
| Recompute from the first changed bar onward | With in-order batches the first changed bar is the open one, so it does the same work as folding. Without the new column it must scan the day for the dollar total, and because bars store only the `exchange_ts` of their first and last trades, an equal timestamp at the cut forces the cut back a bar at a time |
| Recompute hourly or nightly, serve today's bars on query | Each recompute still grows with the day, so the memory peak moves rather than goes; it breaks the five-minute `schedule` in the event-bars contract; a query-time view in Trino or ClickHouse is a new engine ADR-029 would have to hold at zero |
| Fold without the new column, summing the dollar value from `gold.trades` each run | Exact, but it reads the whole day's trades every run, which is the cost being removed |
| The new column in the lake only | Decided against by the owner on 2026-11-26: ClickHouse's copy states the same exact value |

---

## Consequences

**Easier.** The bar step's time and memory follow the batch, not the hour. A failed run's trades
are picked up by the next run. The nightly audit checks an independent method. Both copies of
`gold.bars` carry an exact dollar value.

**Harder.** Two code paths for one table, kept correct only by the tests and the parity checks.
Rollout and rollback each need the ingest paused, a bars rebuild, a ClickHouse column change and
a full re-pull, and a new parity pin. If the snapshot on the newest bar rows no longer exists in
`gold.trades` (the ingest stopped for longer than `gold.trades`' seven-day snapshot retention, or
`gold.trades` was recreated by hand without rebuilding bars), runs fail until
`make lake-rebuild LAYER=bars`.

**Committed to.** The bar definition, thresholds and order unchanged. `quote_volume_e16` in the
lake and ClickHouse, always moved together. One write per run (ADR-048).

**Risks, and what is not yet verified.**

- Every "after" number is an estimate from ADR-048's measured breakdown; nothing here has run.
- The fold's correctness rests on the randomised tests and on the parity checks, not on a
  proof.
- The fast-path share was measured over four days of data, one of them a full day of continuous
  ingest; a venue that reorders trades across batches would push work onto the slow path.
- ClickHouse's copy is only as fresh as the last manual pull; this ADR does not change that.

**Revisit when** out-of-order symbol-batches exceed 1 % over a UTC day (the Trino query in the
research note's appendix), the bar step takes more than 10 s on any run, or the 1d candle step
takes more than 10 s (the trigger to fold candles the same way).

---

## Questions for the owner

- Decided 2026-11-26: `quote_volume_e16` lives in the lake and in ClickHouse.
- Open: should ClickHouse `gold.bars` (it exists, is empty, and is filled only by a manual
  runbook pull) get a scheduled pull? Yes or no.
- Open: fold the 1h and 1d candles the same way now, or only when the 1d candle step passes
  10 s?

---

## References

- [`../research/2026-11-26-incremental-bars-recompute.md`](../research/2026-11-26-incremental-bars-recompute.md):
  measurements and commands, options, implementation plan, rollout and rollback
- [ADR-048](ADR-048-gold-step-reads-touched-data-writes-bars-once.md): the one-commit write and
  the pruned reads this depends on
- [ADR-029](ADR-029-research-production-parity-contract.md): the exact-parity contract
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md): the position carried with the data
- [`.claude/skills/schema-change/SKILL.md`](../../.claude/skills/schema-change/SKILL.md): the
  procedure the new column follows
