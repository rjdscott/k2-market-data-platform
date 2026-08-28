# SCD2 security master: what a dimension has to remember, and why

**Date:** 2026-08-29
**Question:** `gold.dim_instrument` and `gold.dim_venue` are SCD1 snapshots — the registry
overwritten on every gold run, one `loaded_at` column, no memory. What does a real security
master look like on an Iceberg lake, and which of its shapes does K2 actually need?
**Status:** committed as [ADR-030](../adr/ADR-030-scd2-security-master.md).

The trigger is not hypothetical. `config/instruments.yaml` already carries the scar: Kraken
WS v1 spelled Bitcoin `XBT/USD`, v2 spells it `BTC/USD`, and the file was rewritten in place
on 2026-08-26 when the Kotlin handlers retired. The old spelling is in git history and
nowhere else. Every trade K2 captured before that date was captured under a native symbol
the dimension can no longer describe. That is a rename the current dimension cannot express,
and it has already happened once.

---

## 1. The SCD types, and why 2

Kimball's catalogue ([Kimball Group, *Slowly Changing
Dimensions*](https://www.kimballgroup.com/2008/08/slowly-changing-dimensions/); [Design Tip
152](https://www.kimballgroup.com/2013/02/design-tip-152-slowly-changing-dimension-types-0-4-5-6-7/))
is seven types. Only four are live options here.

| Type | What it does | Why not, for K2 instruments |
|---|---|---|
| 0 | attribute never changes after first load | `tick_size` and `status` change. Type 0 would be a lie with a schema. |
| **1** | overwrite in place, keep only "now" | **What K2 has today.** A backtest run in September against a dimension loaded in October reads October's tick size. The result is not reproducible and nothing in the data says so. |
| **2** | one row per validity interval, `valid_from`/`valid_to` | **Chosen.** History is queryable by time, the fact tables never move, and a wrong answer is impossible to get accidentally: an as-of join is the only way to read it. |
| 3 | one extra "previous value" column | Remembers exactly one change and only for the attributes you predicted would change. Kraken's rename would have consumed the one slot. |
| 4 | current table + separate history table | Two tables that can disagree, and the disagreement is silent. Type 2 with `is_current` gets the same fast path from one table. |
| 6 | 1 + 2 + 3 hybrid (current value duplicated onto every historical row) | Serves "restate history under today's attributes", a reporting need. K2's need is the opposite — what was true *then*. |

**Type 2 is not the cautious choice, it is the only one that answers the question a backtest
asks.** Every research result K2 produces is a function of instrument attributes: a
queue-position feature is a function of `tick_size`, a fee model of the venue's schedule, a
liquidity screen of `qty_increment`. Under SCD1 the attribute has one value — today's — and a
backtest over August silently applies September's tick size to August's book. The error is
not detectable from the output. Under SCD2 the join carries a timestamp and the wrong answer
requires writing the wrong query.

The counter-argument, which is real: nothing in K2 currently reads `tick_size`, and 34
instruments over three months of history will produce roughly 34 rows. Building a
version-tracking dimension for a table that never changes is the definition of speculative
work. The answer is that the cost is asymmetric and one-directional. SCD2 costs ~120 lines
and a merge; retrofitting history onto a dimension that never kept any costs the history
itself, which cannot be recovered from anywhere — not from `raw.messages` (the registry was
never on the wire) and not from Iceberg snapshots (§6). The cheap moment to start recording
is before there is something to record.

---

## 2. Temporal shape: how many time axes

Two axes exist in principle:

- **Effective time** (`valid_from` / `valid_to`) — when the attribute was true *in the world*.
- **Recorded time** (`recorded_at`, "as known at") — when K2 learned it.

They differ whenever a correction is backfilled. Kraken publishes on 3 September that a pair's
`tick_size` changed on 1 September; K2's ingest sees it on the 3rd. Effective time says the
new tick was in force from the 1st. Recorded time says a backtest run on the 2nd could not
have known that, and *reproducing that backtest exactly* requires reading the dimension as it
stood on the 2nd — not as it stands now.

Full bitemporality (two closed intervals, four timestamps, versions of versions) is the
textbook answer and it is genuinely expensive: every read grows a second range predicate,
every correction writes a version of a version, and the row count multiplies. Snodgrass's
treatment of bitemporal tables is a book, not a section.

**Recommendation: one interval plus a recorded stamp.** `valid_from` / `valid_to` /
`is_current` on the effective axis, and a scalar `recorded_at` on every row. This is
uni-temporal in the query path and carries the bitemporal *evidence* on the row:

- For a registry-sourced change, `valid_from == recorded_at` by construction — the run
  timestamp is both. There is no second axis to store because there is no second value.
- The instant they diverge — the first backfilled or venue-dated correction — the row says so:
  `valid_from < recorded_at` is exactly "we learned this late", and the width of the gap is the
  size of the retroactive window. A query that must exclude late knowledge writes
  `WHERE recorded_at <= @as_of`, which is a coarse as-known-at axis, correct to the run.
- What it cannot do is represent a *restatement* — the same effective interval given two
  different attribute values at two different recorded times. That needs the second interval.

The trigger for the second axis is therefore concrete and not "if needed": the first time a
source publishes an effective date that is not the moment of ingest. Kraken's `instrument`
channel does not carry one today (§7), so that day has not arrived.

This is the honest trade for a single-host research platform: pay for the axis that has values
in it, leave the column that proves when the other one starts mattering.

---

## 3. Surrogate key: `instrument_id`

The requirement is an identity that survives a **venue rename**, a **delisting**, and
**symbol reuse**. Three questions, in order.

### 3.1 What is the natural key?

The brief's phrasing was `(exchange, symbol)`. That key cannot survive the one rename K2 has
actually seen: Kraken `XBT/USD` → `BTC/USD` changes `symbol`, so a key containing `symbol`
produces a *new instrument*, which is the exact failure the surrogate exists to prevent.

The registry already carries the answer. `canonical` is `BASE/QUOTE` and was invariant across
that rename — Kraken's Bitcoin/US-dollar pair was `BTC/USD` canonically before and after.

> **Natural key: `(exchange, canonical_symbol)`. The native `symbol` is a tracked
> attribute.** A venue rename closes the old version and opens a new one *under the same
> `instrument_id`*, with the new spelling in `symbol`. That is precisely what SCD2 is for,
> and the rename stops being an identity event.

This leans on an invariant that is already enforced: `tests/test_contracts.py` rejects a
duplicate `canonical` within an exchange, which is what makes `(exchange, canonical_symbol)`
unique in the registry. If that assertion is ever relaxed the key is no longer a key, so the
two move together.

### 3.2 Deterministic hash or sequence?

| | Sequence / `monotonically_increasing_id` | Deterministic hash of the natural key |
|---|---|---|
| Stable across a full rebuild | **No** — `rebuild.py --layer gold` drops and recreates; every id is renumbered | **Yes** — the same input gives the same id, forever |
| Needs a generator | Yes; Spark has no safe cross-writer sequence, so it means a state table | No |
| Concurrent writers | needs coordination | none needed |
| Opaque to humans | Yes (a virtue: nobody parses it) | Yes |
| Collision risk | none | birthday-bounded; 128 bits over ~10² keys is not a risk |

**Recommendation: deterministic.** `instrument_id = sha256(exchange ‖ 0x1F ‖
canonical_symbol)`, first 32 hex chars (128 bits), as a `STRING`.

The decisive column is the first one. K2 rebuilds gold from silver as a routine operation
(`docker/lake/rebuild.py`), and a rebuild that renumbers every instrument invalidates every
notebook, every saved result and every published id that ever quoted one. A key whose whole
job is to be stable must not be destroyed by the maintenance procedure. A sequence would need
a durable generator — a state table outside Iceberg, the PostgreSQL watermark pattern
[ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md) deleted — to avoid that, which
is a second system for a 34-row table.

The classic objection to hashed surrogates is that they are natural keys in disguise: change
the natural key and the id changes. True, and it is the reason §3.1 matters more than §3.2.
The natural key is chosen to be the thing that does *not* change; the volatile spelling is an
attribute.

### 3.3 Delisting and symbol reuse

**Delisting** is not an identity problem. The id persists; the dimension gets a closing
version with `subscribed = false` (and, when a venue publishes it, `venue_status`). Deletion
would be wrong twice over: it makes historical facts unjoinable, and it destroys the record
that the instrument existed.

**Symbol reuse** is where the deterministic hash has a real limit, and it should be stated
rather than papered over. If a venue delists `FOO/USD` and later lists a *different* asset
under the same canonical symbol, the hash gives both the same `instrument_id` and SCD2 reads
the second listing as a reopening of the first. Nothing in `config/instruments.yaml` can
distinguish them — the registry has no listing date, no ISIN, no venue instrument id.

This is a genuine hole, and it is the same hole as derivatives: for a future or an option,
symbol ≠ instrument, and `BTC-27JUN25` reused after expiry is the normal case rather than the
exotic one. Both are closed the same way — an `instrument_id` seeded from a venue-published
immutable id, or from a listing date folded into the hash. **Revisit trigger: the first
expiry-bearing instrument in the registry, or a canonical symbol reappearing after a
`subscribed = false` version.** The second is detectable in the dimension itself, which is
the useful property: the failure is visible in the data rather than silent.

---

## 4. The open row: NULL or a sentinel

Every SCD2 implementation must decide what `valid_to` holds on the current version.

**`NULL` is semantically pure and operationally hostile.** The classic as-of predicate is

```sql
WHERE ts >= valid_from AND ts < valid_to
```

With `NULL`, `ts < NULL` is `NULL`, which is not `TRUE`, so **the current row is silently
dropped from every as-of join** — the newest data, the rows a query most often wants. The fix
is `AND (valid_to IS NULL OR ts < valid_to)` in every query forever, and the failure mode of
forgetting it is not an error, it is a smaller result set.

Concretely for K2's two engines:

- **DuckDB `ASOF JOIN`** matches on one inequality against `valid_from`
  ([DuckDB ASOF joins](https://duckdb.org/2023/09/15/asof-joins-fuzzy-temporal-lookups.html),
  [`FROM` syntax](https://duckdb.org/docs/stable/sql/query_syntax/from.html)) and takes the
  greatest qualifying row. Because it needs only `valid_from`, a NULL `valid_to` is *not*
  fatal for the pure as-of case — which is exactly what makes it dangerous: the ASOF form
  works, and the moment someone writes the equivalent range join by hand, the current row
  vanishes. Two spellings of the same join returning different row counts is a bug factory.
- **Spark range joins** are the hand-written form, always. `gold.py` joins with plain
  predicates; a NULL upper bound breaks them on first use.

**Recommendation: sentinel `TIMESTAMP '9999-12-31 23:59:59'`, plus `is_current BOOLEAN`.**

- The sentinel makes `ts >= valid_from AND ts < valid_to` **total** — one predicate, correct
  in both engines, with no special case to forget.
- Interval arithmetic stays closed: `valid_to - valid_from` is a duration for every row, and
  `SUM` over a key is the instrument's whole lifetime.
- Its cost is honest and small: `max(valid_to)` is 9999 rather than a real date, so the column
  min/max statistics are useless as a freshness signal. `is_current` and `recorded_at` are the
  freshness signals instead, so nothing is lost.

`is_current` is derivable from `valid_to = sentinel` and is kept anyway. It is the predicate
every consumer actually writes (`WHERE is_current` — the "what do we trade now" query, which
is most of them), it reads as intent rather than as a magic constant, and it makes the current
slice a boolean filter that Iceberg can push down. The redundancy is one bit per row on a
34-row table, and it is maintained in the same statement that sets `valid_to`, so the two
cannot drift.

---

## 5. Change detection: one hash, not N comparisons

A merge must decide "did anything tracked change". The options are a column-by-column
comparison (`prev.tick_size IS DISTINCT FROM cur.tick_size OR …`, ten clauses that grow with
the schema and are wrong the day someone adds a column and forgets the clause) or a hash of
the tracked attributes.

**`attr_hash = sha256(...)` over the tracked attributes**, canonically serialised: fixed field
order, a `0x1F` separator that cannot occur in the values, and `NULL` rendered as a distinct
`0x00` sentinel so that "no value" and the string `"None"` are different inputs. The merge
predicate is then one comparison regardless of how wide the row gets, and the hash is stored,
so "why did this version open" is answerable by comparing two hashes and diffing the columns.

The property to state up front: **adding a tracked attribute changes every row's hash**, so
the next run opens a new version for all 34 instruments with identical attribute values plus
one new column. That is the correct behaviour — the dimension's shape genuinely changed, and
the rows record when — but it is a surprise if it is not written down, so it is written down
here and in the ADR.

Decimals are serialised as their exact string form. `DECIMAL(28,10)` is used precisely because
a `DOUBLE` round-trip would make the hash unstable in the eighth place, the same reason
`gold.bars` refuses decimal division.

---

## 6. Iceberg mechanics

### 6.1 MERGE INTO, copy-on-write

Iceberg on Spark supports `MERGE INTO` with `WHEN MATCHED THEN UPDATE` / `WHEN NOT MATCHED
THEN INSERT` ([Spark writes](https://iceberg.apache.org/docs/latest/spark-writes/)), under
either copy-on-write or merge-on-read
([configuration](https://iceberg.apache.org/docs/latest/configuration/)). Every K2 table sets
`write.{delete,update,merge}.mode = copy-on-write`, and the dimensions keep it: a COW merge
rewrites the touched data files, which for a 34-row single-file table is a few kilobytes, and
it leaves **no delete files**, which is the property that matters. DuckDB's Iceberg reader and
ClickHouse's `iceberg()` both handle position deletes poorly or not at all; the notebooks read
these tables directly, so a merge-on-read dimension would be a table the research surface
cannot trust.

**The close and the insert are two statements, and Iceberg gives no transaction across them.**
A single `MERGE` cannot both update a matched row and insert a second row for the same source
key — one action per matched row. So:

1. `MERGE INTO … WHEN MATCHED AND t.is_current THEN UPDATE SET valid_to = run_ts, is_current = false`
2. append the new versions

**Order matters, and close-first is the right order.** Close-then-insert, interrupted, leaves
a *gap*: a key with no current version for the window between the crash and the next run. The
as-of join returns no row — a visible miss. Insert-then-close, interrupted, leaves **two open
rows for one key**, which makes the as-of join non-deterministic and every downstream count
double. A gap is a failure that announces itself; a duplicate is a failure that produces
plausible wrong numbers. The next run heals a gap on its own: the key has no current row, so
it reads as unseen and a fresh version opens (with the later `valid_from` — the gap stays on
the record, which is correct, since nothing knows what was true inside it).

### 6.2 Snapshot time travel is not a substitute

The obvious objection to all of this is that Iceberg already versions the table: `SELECT … AT
(VERSION => id)` returns the dimension as it stood at any snapshot
([Spark queries](https://iceberg.apache.org/docs/latest/spark-queries/)), so why not keep SCD1
and time-travel? Four reasons, each sufficient:

1. **Snapshots are physical, not logical.** A snapshot versions the whole table. "What was
   `tick_size` for `BTC/USD` on 3 August" becomes a scan of every snapshot for the one where
   the value changed. There is no per-row validity to join on, so there is no as-of join.
2. **Snapshots expire.** `docker/lake/maintenance.py` runs `expire_snapshots` nightly, the
   ordering ADR-017 established, and Iceberg's own maintenance guidance is to expire
   ([maintenance](https://iceberg.apache.org/docs/latest/maintenance/)). History that a
   correctness-motivated cleanup job deletes is not history. Keeping it would mean never
   expiring one table's snapshots — a permanent exception to the maintenance contract, in
   exchange for a worse query.
3. **A rebuild resets it.** `rebuild.py --layer gold` drops and recreates. Snapshot lineage
   does not survive; rows in a table do.
4. **It is not portable.** The parity checks and notebooks read through DuckDB and ClickHouse.
   `AT (VERSION => …)` is an Iceberg-reader feature with uneven support; `valid_from` is a
   column, and every engine can filter a column.

Snapshots remain the right answer to a different question — "what did this table look like
when that notebook ran" — which is what `pin()` uses them for (ADR-029). Two mechanisms, two
questions.

---

## 7. Sources of truth, per attribute

The registry is authoritative for what K2 *subscribes to*. It is not authoritative for what
the venue *publishes*, and pretending otherwise is how a dimension acquires stale
microstructure.

| Attribute | Authority | Available today |
|---|---|---|
| `symbol` (native), `canonical_symbol`, `base`, `quote` | `config/instruments.yaml` | yes |
| `book_depth`, `subscribed` | `config/instruments.yaml` — facts about K2's subscription, which no venue knows | yes |
| `tick_size`, `qty_increment`, `price_precision`, `qty_precision`, `venue_status` | the venue | **Kraken only** |

**Kraken is already captured.** `bronze.kraken_instrument` holds the `instrument` channel
verbatim — a ~566 KB snapshot of all 1,436 pairs at subscribe plus incremental updates — and
its `data.pairs[]` struct carries `tick_size`, `qty_increment`, `price_precision`,
`qty_precision` and `status` per pair, typed. Nothing new is captured to populate Kraken's
columns; the data has been landing since Phase D, read today only by `books.py` for checksum
verification.

**Binance and Coinbase are not.** Binance publishes `GET /api/v3/exchangeInfo`
(`filters[]`: `PRICE_FILTER.tickSize`, `LOT_SIZE.stepSize`, plus `status`) and Coinbase
`GET /api/v3/brokerage/market/products` (`price_increment`, `base_increment`, `status`). Both
are **REST**, and K2's capture tier is WebSocket-only: a REST poller is a new capture surface,
a new failure mode, a new set of credentials for Coinbase, and a new thing to alert on.

**Decision: design the columns, leave them NULL, do not build the poller.** The schema is
where the cost of being wrong is highest — adding a column to an Iceberg table later is an
`ALTER`, but *changing* one is a rebuild — so the columns land now. `source` records which
authority produced a version (`registry` when the venue columns are NULL, `venue:kraken` when
Kraken's snapshot supplied them), so a NULL is never ambiguous between "not published" and
"not captured": the row says which.

**Revisit trigger: the first gold product or research notebook that reads `tick_size` for a
Binance or Coinbase instrument.** At that point a REST fetch into `bronze.<venue>_instrument`
is worth its operational cost, and not before. A cross-venue tick comparison that silently
compares a populated Kraken value against a NULL is the failure this trigger exists to catch,
so the notebook that wants it is exactly the right alarm.

---

## 8. What the fact tables carry

Chapter 12's rule, kept from the start: **silver stays thin** — native symbol and
`canonical_symbol`, nothing joined in. That rule survives untouched. The open question was
gold.

### Should `gold.trades` carry `instrument_id`?

**No.** Four reasons:

1. **It is derivable, exactly.** `instrument_id` is `sha256(exchange ‖ canonical_symbol)` and
   `gold.trades` already carries both columns. Storing it is storing a pure function of two
   columns that sit beside it — a denormalisation with no lookup saved, because the join key
   would be those same two columns anyway.
2. **The join does not need it.** The as-of join is
   `ON t.exchange = d.exchange AND t.canonical_symbol = d.canonical_symbol ASOF t.exchange_ts
   >= d.valid_from`. Adding a hash column would not remove a predicate.
3. **The cost is on the largest table.** `gold.trades` is the ~10 M-row table; a new column
   is an `ALTER` plus a full rebuild to backfill it, and copy-on-write means rewriting the
   files. Millions of rows of derivable hash to save typing.
4. **It would freeze the key.** A stored `instrument_id` on facts makes §3.3's revisit
   (re-seeding ids from a venue instrument id) a fact-table migration instead of a dimension
   change. Keeping the key in one table is what keeps it changeable.

The dimension carries `instrument_id` because that is what a version is identified by —
`(instrument_id, valid_from)` — and because it is the stable handle to quote in a paper or a
saved result. The facts join on the natural key, at query time, as chapter 12 said they would.

---

## 9. How ClickHouse gold would consume it

Design note only — no DDL in this change, and ClickHouse's `k2.*` medallion is frozen
([ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)).

ClickHouse offers two shapes ([dictionaries](https://clickhouse.com/docs/en/sql-reference/dictionaries)):

| Shape | Fits | Cost |
|---|---|---|
| **`complex_key_hashed` dictionary over the current slice** | "what is BTC/USD's tick size *now*" — decoration on a live query, `dictGet` per row, in memory | no history: a dictionary has one value per key |
| **`range_hashed` dictionary** | as-of lookup by a time range — `dictGet(dict, attr, key, ts)`, which *is* SCD2 in dictionary form | whole dimension in memory including history; refresh reloads all of it |
| plain `MergeTree` table + `ASOF LEFT JOIN` | as-of over the full history in SQL | a join, not a lookup; slower per row but no memory ceiling |

**Recommended shape when the need arrives:** a `complex_key_hashed` dictionary over
`is_current = true` only, sourced by the same `iceberg()` pull that already loads ClickHouse
gold from the lake ([`clickhouse-rebuild-from-lake.md`](../runbooks/clickhouse-rebuild-from-lake.md)),
with a `LIFETIME` refresh. That serves the hot-tier question — decorating live queries with
current attributes — and it is the only question ClickHouse is on the path for. Historical
as-of joins stay in the lake, read by DuckDB, where the research is.

`range_hashed` is the documented upgrade and matches the SCD2 shape one-for-one, so the switch
is a dictionary definition rather than a redesign. **Trigger: a served ClickHouse query that
needs an attribute as it stood at the trade's timestamp** — not before, because it costs the
full history resident in memory to answer a question the hot tier is not being asked.

---

## 10. Recommendations, collected

| Question | Recommendation |
|---|---|
| SCD type | Type 2, on both dimensions |
| Temporal shape | One effective interval + scalar `recorded_at`. Second axis when a source publishes an effective date ≠ ingest time |
| Natural key | `(exchange, canonical_symbol)`; native `symbol` is a tracked attribute, so a rename is a version not an identity |
| Surrogate | Deterministic `sha256(exchange ‖ canonical_symbol)`, 128 bits, hex — survives `rebuild.py`, which a sequence would not |
| Open row | Sentinel `9999-12-31 23:59:59` + `is_current`; never NULL |
| Change detection | `attr_hash`, sha256 over canonically serialised tracked attributes |
| Iceberg | `MERGE INTO` copy-on-write to close, append to insert; close **before** insert; snapshots are not history |
| Venue attributes | Kraken from `bronze.kraken_instrument`; Binance/Coinbase columns NULL until a consumer asks |
| `gold.trades` | **No `instrument_id`.** Derivable, and the join is on the natural key |
| ClickHouse | `complex_key_hashed` over `is_current`; `range_hashed` when a served query needs as-of |

## Sources

- Kimball Group, [Slowly Changing Dimensions](https://www.kimballgroup.com/2008/08/slowly-changing-dimensions/) — types 1–3, the surrogate-key argument.
- Kimball Group, [Design Tip 152: SCD types 0, 4, 5, 6, 7](https://www.kimballgroup.com/2013/02/design-tip-152-slowly-changing-dimension-types-0-4-5-6-7/) — the rest of the catalogue, and when a mini-dimension beats type 2.
- Apache Iceberg, [Spark writes](https://iceberg.apache.org/docs/latest/spark-writes/) — `MERGE INTO` semantics, one action per matched row.
- Apache Iceberg, [Configuration](https://iceberg.apache.org/docs/latest/configuration/) — `write.merge.mode`, copy-on-write vs merge-on-read.
- Apache Iceberg, [Spark queries](https://iceberg.apache.org/docs/latest/spark-queries/) — `AT (VERSION => …)` time travel.
- Apache Iceberg, [Maintenance](https://iceberg.apache.org/docs/latest/maintenance/) — `expire_snapshots`, why snapshot history is not durable history.
- DuckDB, [ASOF joins](https://duckdb.org/2023/09/15/asof-joins-fuzzy-temporal-lookups.html) and [`FROM` clause](https://duckdb.org/docs/stable/sql/query_syntax/from.html) — the single-inequality match.
- ClickHouse, [Dictionaries](https://clickhouse.com/docs/en/sql-reference/dictionaries) — `complex_key_hashed`, `range_hashed`.

All URLs checked 2026-08-29.
