# ADR-024: Unified bronze tables in the lake, partitioned by exchange

**Status:** Superseded by [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)
**Date:** 2026-08-26
**Author:** Rob Scott
**Category:** Data model

---

## Context

[ADR-011](ADR-011-multi-exchange-bronze-architecture.md) is one of the better decisions
in this repo. It says Bronze is *exchange truth*: one table per venue, native shapes
preserved — Kraken's `pair: "XBT/USD"`, its `"1737118158.321597"` timestamp string,
Binance's `is_buyer_maker` — with normalisation deferred to a unified Silver. The
argument was debuggability: a Bronze row you can diff against the exchange's own
documentation, and transformations that live in reviewable SQL rather than in a feed
handler. It was validated end to end when Coinbase became the third exchange in a day
([ADR-016](ADR-016-add-coinbase-exchange.md)).

Phase D has to decide the same question for the lake, and the premise underneath ADR-011
has moved. ADR-011 assumed that if Bronze normalises, *nothing* holds the native shape —
"Lossy: had to pick one format (Binance), Kraken fields lost". In v3 that is no longer
true: [ADR-021](ADR-021-raw-first-archive-and-lineage.md) puts the exchange frame
byte-for-byte in `raw.messages`, kept forever, with `(src_topic, src_partition,
src_offset)` on every derived row pointing back at it. Native truth is not preserved by
a table shape any more; it is preserved by the archive, at a fidelity per-venue Bronze
tables never had — v2's `bronze_trades_*` are already `JSONExtract`ed and cast to
`Decimal(18,8)` (`docker/clickhouse/ddl/01-k2-schema.sql`), so they are a *typed
rendering* of the native shape, not the bytes.

What per-venue lake tables would cost instead is concrete. The three venues now produce
one Avro record type on one contract — `Trade`, fixed-point `int64` at 1e-8, identical
field-for-field across all three ([ADR-020](ADR-020-avro-fixed-point-contracts.md)) —
so three tables would hold three copies of one schema. Every cross-venue query, which is
what a research platform mostly asks, becomes a three-way `UNION ALL` in every notebook.
Three tables at ~0.156 GB/day combined
([capacity model §4c](../architecture/capacity-model.md#4c-per-lake-table-per-day))
means each venue's daily partition is tens of megabytes against a 128 MB target file
size, so compaction can never reach the target and the small-file problem is designed in.
And a fourth exchange becomes a table, a DDL file, a compaction entry and an audit entry
rather than a new value in a column.

---

## Decision

**We will hold one unified `bronze.trades` and one unified
`bronze.book_snapshots_l2` in the lake, partitioned by `exchange` and then by day, with
symbol pruning carried by the sort order rather than by a partition field — because the
three venues now share one wire contract, cross-venue reads are the normal case, and the
native shape is preserved by `raw.messages` rather than by a table per venue.**

Scope: **the lake only.** ADR-011 remains in force for ClickHouse — `bronze_trades_binance`,
`_kraken`, `_coinbase` and their MVs are untouched until Phase E rewrites the hot tier
([ADR-025](ADR-025-clickhouse-derived-hot-tier.md)), and until then the two tiers
deliberately disagree about the shape of Bronze. That is a stated, temporary
inconsistency, not an oversight.

The specs, as applied by `docker/lake/ddl/lake.sql`:

| Table | `PARTITIONED BY` | Sort order (local) | Identifier fields | Target file |
|---|---|---|---|---|
| `bronze.trades` | `exchange, days(exchange_ts)` | `symbol, exchange_ts` | `exchange, symbol, trade_id, src_topic, src_partition, src_offset` (amended 2026-08-26, below) | 128 MB |
| `bronze.book_snapshots_l2` | `exchange, days(snapshot_ts)` | `symbol, snapshot_ts` | `exchange, symbol, conn_id, snapshot_ts_ns` | 128 MB |

Both are `write.distribution-mode = hash`, copy-on-write, Parquet + zstd, and carry
`src_topic` / `src_partition` / `src_offset` lineage plus `ingest_ts`. Full column
commentary is in the DDL; the partitioning argument in
[`../architecture/partitioning-strategy.md`](../architecture/partitioning-strategy.md).

---

## Rationale

**Unification is now cheap because normalisation moved upstream of storage, into a
contract.** ADR-011's transformations lived in per-venue SQL because the venues arrived
in three dialects and something had to reconcile them. In v3 the reconciliation happens
in `k2-capture`, in one Rust codebase, against one Avro record type, and it is enforced
by the schema registry rather than by review of three materialized views. There is no
per-venue shape left at the lake boundary to preserve — `trade.avsc` is the same twelve
fields whichever container produced it. Unifying three identical schemas is not lossy;
keeping them apart would be ceremony.

**`exchange` as the leading partition field, on three values.** Iceberg partitioning is
about file count and pruning, and `exchange` is the field almost every query filters on
— per-venue completeness audits, per-venue latency comparisons, "is Kraken behind" — with
a cardinality of exactly three that will not grow faster than roughly one per year. It
costs 3× the partitions of a day-only spec and buys a two-thirds file skip on every
single-venue read. Putting it first rather than after `days()` matches the way the
directory layout reads and the way the audits scan.

**Symbol is in the sort order, never in the partition spec, and this is the trade-off
worth stating.** Symbol is the obvious partition candidate and it is skewed by
construction: BTC-quoted majors dwarf the tail. `config/instruments.yaml` holds 34
`(exchange, symbol)` pairs — binance 12, kraken 11, coinbase 11 — so an
`exchange × symbol × day` spec is **34 partitions per day**, a handful holding real data
and most holding a few hundred rows. On a table whose whole daily volume is 0.156 GB
that averages 4.6 MB a partition against a 128 MB target file: far too small to be worth
opening, and the skew means the real distribution is worse than the mean. The sort order does the same pruning work at no file-count cost: files are
locally sorted by `(symbol, exchange_ts)`, so per-file min/max bounds on `symbol` make a
single-instrument scan skip most files, and Parquet row-group statistics narrow it again
inside the ones it opens. The price is honest — pruning by sort order is *statistical*
where partitioning is *exact*, so a query for a rare symbol still opens files that might
contain it. At this table's size that is a few milliseconds; at PB scale the arithmetic
is redone in [`../architecture/scale-out-path.md`](../architecture/scale-out-path.md).
Iceberg supports partition evolution, so `ADD PARTITION FIELD` remains available without
rewriting data if a query pattern ever justifies it — this is a reversible decision, and
it is the reason it can be made quickly.

**Different day columns for the two tables, deliberately.** `bronze.trades` partitions on
`days(exchange_ts)` because every venue stamps a trade. `bronze.book_snapshots_l2`
partitions on `days(snapshot_ts)` because Binance's partial-depth stream carries **no
venue timestamp at all** — `exchange_ts` is null for a third of the book rows
([ADR-027](ADR-027-book-snapshot-and-sequencing.md)) — and a nullable column cannot carry
a partition. `snapshot_ts` is K2's own sampler clock and is always present. Using one
column for consistency's sake would have put a third of the book data in a null
partition.

**Identifier fields differ between the two tables, and both were settled by measurement
rather than by which column looked like a key.** The obvious answers were
`(exchange, symbol, trade_id)` for trades and `(exchange, symbol, conn_id, conn_msg_seq)`
for book snapshots. Both are wrong, and the data says so:

* Over **287,184 trades** captured on 2026-08-26 (30 min, all three venues),
  `(exchange, symbol, trade_id)` had **956 duplicated keys** — every one Coinbase, every
  one a pair of rows with identical price, qty, side and `exchange_ts` under two
  different `conn_id`s. Coinbase replays recent `market_trades` on resubscribe, so a
  reconnect genuinely delivers the same trade twice and the append-only archive genuinely
  holds both frames. Adding `conn_id` makes the uniqueness claim true and leaves the
  replay visible instead of hidden. The *logical* trade is still
  `(exchange, symbol, trade_id)` — that is what a research query deduplicates on, and
  `docker/lake/maintenance.py` reports the cross-`conn_id` count on every run so the
  replay rate stays a published number rather than background noise.
* **Amended 2026-08-26, first full day of the archive:** `conn_id` was not enough either.
  5,034 Coinbase `(exchange, symbol, trade_id, conn_id)` keys held two rows, each pair two
  distinct `market_trades` frames ~15 s apart on one connection with identical price, qty,
  side and `exchange_ts`. The venue re-sends recent trades inside a live subscription. So
  the identifier is the **source lineage** — the one uniqueness an archive of frames can
  promise — and `venue_replay` reports the replay count split across / within connections.
* Over **47,331 book snapshots** the same day, `(exchange, symbol, conn_id, conn_msg_seq)`
  had **484 duplicated keys** — e.g. binance ATOMUSDT `conn_msg_seq` 81456 sampled one
  second apart with the same `recv_ts_ns`. `conn_msg_seq` records which frame the book
  last incorporated, so a quiet book gives two consecutive 1 Hz samples the same value.
  Two snapshots of an unchanged book is correct behaviour, not a duplicate. The sampler
  clock `snapshot_ts_ns` has zero duplicates over the same 47,331 rows.

`seq` was no help on either table: Kraken writes 0 because it does not sequence the
stream, and Coinbase's `sequence_num` is connection-wide across all channels rather than
per-symbol (spike S5). Picking a column because it exists, on any of these three
occasions, would have produced a duplicate check that silently passes — which is exactly
what the 956 and the 484 are.

**Rebuildability is what makes all of this low-stakes.** Both tables are pure functions of
`raw.messages` (ADR-021). If the partition spec or the sort order turns out wrong, the
fix is to change the DDL and replay the archive — not a migration, not a backfill from a
source that no longer exists. That is the difference between this decision and ADR-011's,
which was made against tables whose contents could not be reproduced.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Per-exchange lake tables** (`bronze.trades_binance`, …), mirroring ADR-011 | Preserves a per-venue shape that no longer exists — one Avro contract, three producers — and pays for it three ways: a `UNION ALL` in every cross-venue query, daily partitions of tens of MB against a 128 MB target so compaction can never converge, and a fourth exchange costing a table plus DDL plus compaction plus audit entries instead of a new value in a column. The debuggability ADR-011 bought is delivered better by `raw.messages`, which holds the actual bytes rather than a typed rendering of them. |
| **One table, no `exchange` partition field** — `days()` only, exchange in the sort order | One-third the partition count and gives up an exact skip on the field most queries filter by, in exchange for pruning that is statistical. `exchange` has three values; this is the cheapest exact pruning available anywhere in the schema. |
| **Partition by `exchange, symbol, days(...)`** | Exact symbol pruning, and 34 partitions/day (`config/instruments.yaml`) on 0.156 GB/day — 4.6 MB each on average: one large partition (BTC), a long tail holding a few hundred rows, and a metadata tree larger than the data it indexes. This is the classic small-file failure, and it is unrecoverable without a full rewrite. |
| **Partition by `hours(...)`** | 24× the partitions for a table that writes 0.156 GB/day. The day partition plus the sort order already prunes an intraday range to a handful of files; hourly buys metadata. Reconsidered at the 8.5× crossover in the scale-out path, where the arithmetic changes. |
| **Keep the v2 medallion shape in the lake** (per-venue bronze → unified silver → gold) | Three layers of copies of the same trades, where v3 has exactly two representations by design: verbatim (`raw.messages`) and decoded (`bronze.*`). A Silver layer whose only job is unification is redundant when Bronze is already unified, and Gold is not in the lake at all — OHLCV is computed on read in the hot tier ([ADR-026, reserved](ADR-018-v3-lake-first-rust-capture.md#follow-on-adrs)). |
| **Parallel `bid_px` / `bid_qty` arrays in the lake**, matching the wire | Free at write time, and it makes "the third bid level" a two-column zip every reader writes for itself, with a px/qty length mismatch representable in the storage format. `array<struct<px, qty>>` makes the pairing Parquet's problem. The wire keeps parallel arrays because ClickHouse 24.3 decodes `array<long>` natively (spike S4); the lake and the wire are allowed to differ where each has a different reader. |

---

## Consequences

**Easier:** every cross-venue query is a `WHERE exchange IN (…)` rather than a union;
adding a fourth exchange adds rows, not tables — no DDL, no compaction entry, no audit
entry; compaction actually reaches the 128 MB target because the daily partition is one
table's worth of data rather than a third of it; and the duplicate and gap audits run
once per table instead of once per venue.

**Harder:** the lake and the hot tier now disagree about what Bronze means, and will
until Phase E — a reader moving between `lake.bronze.trades` and
`k2.bronze_trades_binance` meets two different models with the same word attached, and
this ADR is the only thing that explains it. Per-venue schema drift, if a venue ever
needs a field the others do not have, must go into a vendor `map<string,string>` rather
than a column, which is less discoverable than a named column in a per-venue table. And
symbol pruning is now a property of file layout: it degrades if compaction is skipped
long enough for unsorted small files to accumulate, which is a failure mode that shows up
as slow queries rather than as an error.

**Committed to:** `exchange` as a leading partition field on both bronze tables — changing
it later is a partition-evolution event, cheap on Iceberg but not free; `snapshot_ts`
rather than `exchange_ts` as the book table's time axis, so any as-of join against trades
crosses two different clocks and must say which; the identifier-field sets above as the
duplicate audit's definition of a row; and the vendor-map escape hatch as the only route
for venue-specific fields.

**Risks:** the sort-order pruning argument is untested on this data — no query has been
run against a populated `bronze.trades`, so "symbol pruning is good enough" is a design
claim, and the first Phase D notebook run is what tests it. `write.distribution-mode =
hash` on a table with three exchange values means at most three writer tasks per day
partition, which is fine at 700 records/s and is a bottleneck at 100×. And a venue that
starts publishing a genuinely different trade shape would strain the one-contract premise
this whole decision rests on.

**Revisit when:** a single-symbol query over a populated `bronze.trades` opens more than
~10 % of the files in its day partition (sort-order pruning is not doing its job, and
`ADD PARTITION FIELD symbol` becomes the answer), or a fourth exchange needs more than
three vendor-map keys to represent (the unified schema is too narrow), or Phase E lands
and this ADR's scoped coexistence with ADR-011 ends.

---

## Related

- [ADR-011](ADR-011-multi-exchange-bronze-architecture.md) — per-exchange Bronze, superseded **for the lake only**; still in force for ClickHouse until Phase E
- [ADR-021](ADR-021-raw-first-archive-and-lineage.md) — why the native shape no longer needs a table to live in, and the lineage columns these tables carry
- [ADR-020](ADR-020-avro-fixed-point-contracts.md) — the one contract that makes unification cheap, and why the lake widens `int64` @1e-8 to `DECIMAL(28,10)`
- [ADR-027](ADR-027-book-snapshot-and-sequencing.md) — why `exchange_ts` is null on Binance book rows, why `seq = 0` on Kraken, and why the deltas are not a table
- [ADR-025](ADR-025-clickhouse-derived-hot-tier.md) — Phase E, where the hot tier stops disagreeing with this
- [`../architecture/partitioning-strategy.md`](../architecture/partitioning-strategy.md) — the full partitioning argument across Kafka, Iceberg and (from Phase E) ClickHouse
- [`../architecture/capacity-model.md`](../architecture/capacity-model.md#4c-per-lake-table-per-day) — the 0.156 GB/day and 0.264 GB/day predictions the file-size arithmetic rests on

---

## Outcome

_To be appended after the Phase D burn-in._
