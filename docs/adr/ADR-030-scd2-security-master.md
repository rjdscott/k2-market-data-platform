# ADR-030: SCD2 security master for the instrument and venue dimensions

**Status:** Accepted
**Date:** 2026-08-29
**Author:** Rob Scott
**Category:** Data model · Storage

---

## Context

`gold.dim_instrument` and `gold.dim_venue` are SCD1 snapshots. `gold.py`'s `load_dims()`
reads `config/instruments.yaml`, builds 34 instrument rows and 3 venue rows, and calls
`overwritePartitions()` on every ingest — every five minutes. The only temporal column is
`loaded_at`, which says when the overwrite happened, not when anything was true. The
dimension has no memory: the previous state is gone, and nothing anywhere records that it
ever differed.

`docs/architecture/12-data-strategy.md` deferred the fix to v3.1 with four triggers, one per
missing piece. Two have now fired:

1. **The rename already happened.** Kraken WS v1 spelled Bitcoin `XBT/USD` and Dogecoin
   `XDG/USD`; v2 spells them `BTC/USD` and `DOGE/USD`. `config/instruments.yaml` was rewritten
   in place on 2026-08-26 when the Kotlin handlers retired (ADR-019) and the translation table
   went with them. The old spellings survive in git history and nowhere in the lake. Every
   Kraken trade captured before that date carries a native symbol the current dimension cannot
   describe. The trigger written in chapter 12 was "the first rename or delisting in the
   registry" — it fired before the row that would have recorded it existed.
2. **The venue attributes are already captured.** `bronze.kraken_instrument` has been landing
   since Phase D: a ~566 KB `instrument`-channel snapshot of all 1,436 Kraken pairs at
   subscribe plus incremental updates, with `tick_size`, `qty_increment`, `price_precision`,
   `qty_precision` and `status` typed per pair in `data.pairs[]`. It is read today only by
   `books.py`, for checksum verification. The dimension that should carry these attributes
   discards them.

The asymmetry is what forces the decision now rather than at the next trigger. An SCD2
dimension costs a merge and roughly 120 lines. Retrofitting history onto a dimension that
never kept any costs the history itself, and it is unrecoverable: the registry was never on
the wire, so `raw.messages` cannot reconstruct it, and Iceberg's snapshot history — the
obvious fallback — is deleted nightly by `expire_snapshots` in `maintenance.py`. Every day
this stays SCD1, one more day of instrument history is permanently lost.

The analysis behind every choice below, including the ones rejected, is
[`docs/research/2026-08-29-scd2-security-master.md`](../research/2026-08-29-scd2-security-master.md).

---

## Decision

**We will make `gold.dim_instrument` and `gold.dim_venue` Kimball type-2 dimensions — one row
per validity interval, keyed by a deterministic surrogate over `(exchange, canonical_symbol)`
— because a research result computed from an instrument attribute is only reproducible if the
attribute can be read as it stood at the trade's timestamp, and SCD1 makes reading the wrong
one undetectable from the output.**

Scope: the lake's two gold dimensions. Silver stays thin (chapter 12's rule, unchanged), the
fact tables are untouched, and no new capture surface is built.

The shape:

- **Natural key `(exchange, canonical_symbol)`**, not `(exchange, symbol)`. The native symbol
  is a *tracked attribute*, so `XBT/USD` → `BTC/USD` closes one version and opens another
  under the same instrument, which is the point.
- **Surrogate `instrument_id` / `venue_id` = `sha256(exchange ‖ 0x1F ‖ canonical_symbol)`**,
  first 32 hex chars. Deterministic, not a sequence.
- **`valid_from` / `valid_to` / `is_current`**, with `valid_to` on the open row set to the
  sentinel `9999-12-31 23:59:59`, never NULL.
- **`recorded_at`** on every row — one effective interval plus an as-known-at stamp, not full
  bitemporality.
- **`attr_hash`**, sha256 over the canonically serialised tracked attributes, is the change
  predicate.
- **Venue-published attributes** `tick_size`, `qty_increment`, `price_precision`,
  `qty_precision`, `venue_status`, populated from `bronze.kraken_instrument` for Kraken and
  left NULL for Binance and Coinbase; `source` says which authority produced the version.
- **A row that disappears from the registry is closed and reopened with `subscribed = false`**,
  never deleted.
- **`gold.trades` does not carry `instrument_id`.** It carries `exchange` and
  `canonical_symbol` already, the id is a pure function of those two, and the as-of join uses
  the natural key.

---

## Rationale

**Type 2, because the failure mode of type 1 is a plausible wrong number.** A queue-position
feature is a function of `tick_size`; a liquidity screen is a function of `qty_increment`. Run
a backtest over August against an SCD1 dimension loaded in October and it applies October's
tick size to August's book. No error is raised, the result is simply wrong, and nothing in the
output distinguishes it from a right one. Under SCD2 the join carries a timestamp and getting
the wrong answer requires deliberately writing the wrong query. This is the same argument
ADR-029 made for pinned snapshots, applied to the one table pinning does not help with —
because under SCD1 there is no historical value in the table to pin.

**`(exchange, canonical_symbol)`, because it is the key the rename did not change.** Kraken's
Bitcoin/US-dollar pair was canonically `BTC/USD` before and after the WS v2 move; only the
native spelling changed. A key containing `symbol` would have produced a second instrument,
which is exactly the failure a surrogate exists to prevent. This depends on an invariant that
is already asserted — `tests/test_contracts.py` rejects a duplicate canonical within an
exchange — so the key and its guarantee move together.

**Deterministic hash, because `rebuild.py --layer gold` exists.** Rebuilding gold from silver
is a routine operation in this repo. A sequence-generated id is renumbered by it, which
invalidates every notebook, saved result and published id that ever quoted one; avoiding that
would need a durable generator outside Iceberg, which is the PostgreSQL watermark pattern
ADR-022 deleted. A hash of the natural key survives a rebuild because it is a function of
data that survives a rebuild.

**Sentinel over NULL, because `ts < NULL` is not `TRUE`.** With a NULL upper bound, the
textbook range predicate `ts >= valid_from AND ts < valid_to` silently drops the *current*
row — the newest data, the rows most queries want — and the failure is a smaller result set,
not an error. DuckDB's `ASOF JOIN` happens to survive it (it matches on `valid_from` alone),
which makes it worse rather than better: the ASOF spelling works and the hand-written range
join beside it does not, and the two return different row counts for the same question. The
sentinel makes both spellings total. Its one cost — `max(valid_to)` is 9999, so the column's
statistics are useless as a freshness signal — is paid by `is_current` and `recorded_at`.

**Close before insert, because a gap is a better failure than a duplicate.** Iceberg's `MERGE
INTO` allows one action per matched row, so closing a version and inserting its successor are
two statements with no transaction across them. Interrupted between the two, close-first
leaves a key with no current row: the as-of join returns nothing, the miss is visible, and the
next run heals it by treating the key as unseen. Insert-first would leave two open rows for
one key, which makes the as-of join non-deterministic and doubles every downstream count —
plausible wrong numbers again.

**Kraken now, Binance and Coinbase never, until asked.** Kraken's attributes cost a read of a
table that is already in the lake. Binance's `exchangeInfo` and Coinbase's `products` are REST
endpoints, and K2's capture tier is WebSocket-only: a poller is a new capture surface, a new
failure mode, new Coinbase credentials and a new alert. The columns land now because changing
an Iceberg column later is a rebuild while adding one is an `ALTER`; the poller waits for a
consumer.

**Fresh tables, not a migration.** The stack's data was wiped on 2026-08-28, so both
dimensions are recreated empty on the next `lake-ddl` run and there is no history to migrate —
which is the cheapest moment this change will ever have. The DDL still uses `CREATE TABLE IF
NOT EXISTS` rather than `DROP` + `CREATE`: the `lake-ddl` one-shot runs on every
`docker compose up`, and a `DROP` there would delete the dimension's accumulated history on
every stack restart, which is the opposite of the point. The one-time drop of the two
old-shape tables is an operator action, recorded in Verification below, not a line in the DDL.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| **Keep SCD1, use Iceberg snapshot time travel as the history** | Snapshots version the whole table, not the row: there is no per-row validity to join on, so there is no as-of join. `maintenance.py` expires them nightly and `rebuild.py` resets them, so the history is not durable. And `AT (VERSION => …)` is an Iceberg-reader feature, while `valid_from` is a column every engine can filter |
| **Type 3 (a `previous_symbol` column)** | Remembers exactly one change, for exactly the attributes someone predicted would change. Kraken's rename would have consumed the one slot and the next change would overwrite it |
| **Type 4 (current table + history table)** | Two tables that can disagree, silently. Type 2 with `is_current` gives the same fast path from one table |
| **Full bitemporal (two closed intervals)** | Both axes carry the same value for a registry-sourced change, so the second interval would store no information today. `recorded_at` carries the evidence and makes the divergence detectable when it arrives |
| **Sequence-generated surrogate** | Renumbered by `rebuild.py --layer gold`; avoiding that needs a durable generator outside Iceberg |
| **`(exchange, symbol)` as the natural key** | Cannot survive the one rename that has actually happened — it makes `XBT/USD` and `BTC/USD` two instruments |
| **`instrument_id` on `gold.trades`** | A pure function of two columns already on the row, stored on the ~10 M-row table, saving no join predicate — and it would make any future re-seeding of the key a fact-table migration |
| **Build the REST poller for Binance and Coinbase now** | A new capture surface with a new failure mode, for columns no consumer reads. The columns land; the poller waits for the trigger below |

---

## Consequences

**Easier:**

- "What was `BTC/USD`'s tick size when this trade printed" is one `ASOF JOIN` away, in DuckDB
  or Spark, over a dimension the notebooks already pin.
- A venue rename, a delisting, a re-listing and a precision change are all the same operation:
  close a version, open a version. None of them is a schema change or a data-loss event.
- Kraken's published precisions are queryable outside `books.py` for the first time.
- The registry's history becomes a table rather than a git log, and `recorded_at` says when
  K2 learned each fact.

**Harder:**

- `load_dims()` is no longer one `overwritePartitions()`. It reads the current slice, diffs it,
  closes and appends — two Iceberg commits per dimension per run instead of one.
- Every consumer must filter. `SELECT * FROM gold.dim_instrument` now returns history, and a
  query that forgets `WHERE is_current` gets multiple rows per instrument. This is a real
  regression in convenience, paid deliberately: making the wrong query return an obviously
  wrong row count is better than making it return one silently stale row.
- Adding a tracked attribute rewrites every hash, so the next run opens a new version for all
  34 instruments with identical values plus one new column. Correct — the dimension's shape
  did change — but it looks like a mass update in the snapshot log, and it will be startling
  if nobody remembers this line.

**Committed to:**

- `(exchange, canonical_symbol)` being unique per venue. `tests/test_contracts.py` asserts it;
  relaxing that assertion breaks the key.
- The `9999-12-31 23:59:59` sentinel appearing in query results, exports and DuckDB output
  forever. It is a real timestamp that is not a real date.
- The dimensions being the one part of gold that is **not** rebuildable from silver.
  `rebuild.py --layer gold` therefore no longer drops them; a `--layer gold` rebuild
  reconstructs trades, candles and bars, and leaves the dimension history alone. There is no
  procedure that reconstructs lost dimension history, because there is no source to
  reconstruct it from.

**Risks:**

- **A gap, not a duplicate, when a run dies between the close and the insert.** Bounded by
  design (see Rationale) and self-healing on the next run, but the gap stays on the record and
  an as-of join inside it returns no row. Accepted: the alternative failure is worse and
  harder to notice.
- **Symbol reuse is not distinguishable.** If a venue lists a genuinely different asset under a
  canonical symbol that was previously delisted, the deterministic hash gives both the same
  `instrument_id` and SCD2 reads the second listing as a reopening. `config/instruments.yaml`
  carries no listing date, ISIN or venue instrument id to separate them. This is the same hole
  as derivatives, where symbol ≠ instrument by construction.
- **A NULL venue attribute means two different things** — not published, or not captured —
  which is why `source` is on the row. A cross-venue comparison that treats NULL as zero would
  be wrong; nothing does that today.

**Revisit when** any one of:

1. **An expiry-bearing instrument enters `config/instruments.yaml`, or a canonical symbol
   reappears after a `subscribed = false` version.** Both mean symbol ≠ instrument, and
   `instrument_id` must then be seeded from a venue-published immutable id or a listing date.
   The second condition is detectable in the dimension itself — a query for it belongs in the
   nightly audit when the first derivatives arrive.
2. **A gold product or a research notebook reads `tick_size` or `qty_increment` for a Binance
   or Coinbase instrument.** That is when the REST fetch into `bronze.<venue>_instrument` is
   worth its operational cost, and not before.
3. **A source publishes an effective date that is not the ingest time** — a venue announcing
   a precision change ahead of it, or a corrected registry entry backfilled. That is the day
   `recorded_at` stops being a scalar and the second temporal axis has values in it.
4. **A served ClickHouse query needs an instrument attribute as it stood at a trade's
   timestamp.** The hot tier then needs a `range_hashed` dictionary rather than the
   `complex_key_hashed` current-slice one this ADR recommends; the research note has the shape.

---

## Verification

Against the live stack, 2026-08-29 (`docker compose up -d`; container clock reads
2026-08-28 UTC). The captures were running, so the ingest had a cold-start backlog to drain.

**The one-time drop.** `lake-ddl` failed the first time with
`IllegalArgumentException: Cannot add field valid_from as an identifier field: not found in
current schema` — the old-shape tables had survived the 2026-08-28 wipe, so `CREATE TABLE IF
NOT EXISTS` was a no-op and the `ALTER … SET IDENTIFIER FIELDS` had nothing to bind to. That
is the failure this ADR predicted and it is why the drop is an operator action:

```
DROP TABLE lake.gold.dim_instrument PURGE     # then dim_venue, via rebuild.drop()
docker start k2-lake-ddl   →  ✓ 67 statements applied to catalog `lake`   (exit 0)
```

**First load, and Kraken's attributes.** One `lake-ingest` flow, `Finished in state
Completed()`:

```
stage 2d: dims: lake.gold.dim_instrument 0 closed / 34 new version(s),
                lake.gold.dim_venue 0 closed / 3 new version(s); 1437 Kraken pairs published

registry instruments = 34   dim_instrument is_current = 34   total rows = 34

|exchange|canonical_symbol|symbol |subscribed|tick_size   |qty_increment|price_precision|venue_status|source      |valid_to           |
|binance |BTC/USDT        |BTCUSDT|true      |NULL        |NULL         |NULL           |NULL        |registry    |9999-12-31 23:59:59|
|coinbase|BTC/USD         |BTC-USD|true      |NULL        |NULL         |NULL           |NULL        |registry    |9999-12-31 23:59:59|
|kraken  |BTC/USD         |BTC/USD|true      |0.1000000000|0.0000000100 |1              |online      |venue:kraken|9999-12-31 23:59:59|

|source      |rows|with_tick|ids|
|registry    |23  |0        |23 |
|venue:kraken|11  |11       |11 |
```

11 Kraken instruments carry published attributes, 23 Binance and Coinbase rows carry NULL with
`source = registry`, exactly as designed. 34 surrogates for 34 instruments.

**A second run adds nothing.** `docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py`:

```
stage 2d: dims: lake.gold.dim_instrument 0 closed / 0 new version(s),
                lake.gold.dim_venue 0 closed / 0 new version(s); 1437 Kraken pairs published

|total_rows|current_rows|instruments|          lake.gold.dim_instrument.snapshots:
|        34|          34|         34|          one append, added-records = 34
```

**The DDL one-shot stays idempotent and keeps the history.** `docker start k2-lake-ddl` again:
`exit=0`, and `rows_after_ddl_rerun = 34, current_rows = 34`. This is why the DDL is `CREATE
IF NOT EXISTS` and not `DROP` + `CREATE`: the one-shot runs on every `docker compose up`.

**The change path, on Spark, against a throwaway namespace.** `config/instruments.yaml` was
not touched (its file-level bind mount pins the inode). `apply_ddl`'s `--namespace-map`
mechanism built `lake.scratch_dim.dim_instrument` from the same DDL and `gold._merge_dim` ran
five cycles against it, then the table and namespace were dropped:

```
cycle 1 (first load)          closed/new = (0, 2)
cycle 2 (nothing changed)     closed/new = (0, 0)
cycle 3 (rename + tick_size)  closed/new = (1, 1)
cycle 4 (ETH leaves registry) closed/new = (1, 1)
cycle 5 (still absent)        closed/new = (0, 0)

|canonical_symbol|symbol |subscribed|tick_size   |source      |valid_from         |valid_to           |is_current|id12        |
|BTC/USD         |XBT/USD|true      |NULL        |registry    |2026-08-29 09:00:00|2026-08-29 11:00:00|false     |4419ceb35e8a|
|BTC/USD         |BTC/USD|true      |0.1000000000|venue:kraken|2026-08-29 11:00:00|9999-12-31 23:59:59|true      |4419ceb35e8a|
|ETH/USD         |ETH/USD|true      |NULL        |registry    |2026-08-29 09:00:00|2026-08-29 12:00:00|false     |b2e5f443989a|
|ETH/USD         |ETH/USD|false     |NULL        |registry    |2026-08-29 12:00:00|9999-12-31 23:59:59|true      |b2e5f443989a|
```

The `XBT/USD` → `BTC/USD` rename kept `instrument_id` `4419ceb35e8a…` and opened a version;
one version's `valid_to` is the next one's `valid_from` to the microsecond, so the intervals
tile with no gap and no overlap; the delisted instrument got exactly one `subscribed = false`
version and the run after it was silent.

**Nothing else moved.** `bash scripts/lake-verify.sh` → `✓ lake-verify passed`, all thirteen
checks `ok` over 25,952,166 raw rows: offsets present and gapless across 79 partitions, silver
== bronze on all three venues for trades and books, gold == silver first deliveries, snapshot
summary == `COUNT(*)`.

`make test-python` 273 passed (255 before, +18 in `tests/test_lake_scd2.py`);
`make lint` clean; `bash scripts/check-docs.sh` all eight gates PASS.

---

## References

- [`docs/research/2026-08-29-scd2-security-master.md`](../research/2026-08-29-scd2-security-master.md) — the analysis: SCD types, bitemporality, surrogate design, open-row representation, Iceberg mechanics, ClickHouse consumption. Sources cited there.
- [ADR-026](ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md) — the four-layer lake and gold-canonical model these dimensions sit in.
- [ADR-029](ADR-029-research-production-parity-contract.md) — the reproducibility contract this extends to instrument attributes.
- [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md) — why a durable id generator outside Iceberg is not an option here.
- [`docs/architecture/12-data-strategy.md`](../architecture/12-data-strategy.md) § Security master — the deferral this supersedes.
- [`docker/lake/scd2.py`](../../docker/lake/scd2.py), [`docker/lake/gold.py`](../../docker/lake/gold.py) `load_dims()`, [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql), [`tests/test_lake_scd2.py`](../../tests/test_lake_scd2.py).

---

## Outcome (2026-09-03)

**A first version opened at the run that discovered it, and that silently emptied the
as-of join.** The security master first ran `2026-08-28 14:59:20`; `gold.trades` starts
`08:19:11` the same day. Every dimension row therefore had
`valid_from = 2026-08-28 14:59:20`, and the ASOF join this ADR taught
(`notebooks/README.md`, `03_asof_trades_book.ipynb`) matched only trades *after* that
instant: **36 of 7,651** kraken `BTC/USD` trades for 14:00–15:00, 72,788 of 1,380,178 over
`BTC/USD` + `BTC/USDT`. No error, no warning — a smaller result set, with `tick_size` NULL
on everything that did survive the hand-written range-join spelling. Exactly the failure
mode the `FOREVER` sentinel exists to prevent, at the other end of the interval.

**The rule changed for the first version only.** `scd2.plan()` now opens the first version
of a natural key at `EPOCH` (`1970-01-01`, a module constant documented beside `FOREVER`)
with `recorded_at = run_ts`. The registry asserts what an instrument *is* — base, quote,
tick size — and those were true before K2 started recording them; opening at `run_ts`
claimed the platform's own start date as the instrument's. A CHANGED attribute still closes
at `run_ts` and opens at `run_ts`, and a delisting still opens at `run_ts`: those are new as
of the run. The resulting `valid_from < recorded_at` on the seed rows is not a defect but
the "we learned this late" evidence § Decision already anticipated — here the gap is the
whole of history before the first run.

**Migration**, run once against the live tables (both had exactly one version per key: 34
instruments, 3 venues, all at `2026-08-28 14:59:20.699765`):

```bash
docker exec k2-spark-iceberg python3 -c "
import sys; sys.path.insert(0, '/home/iceberg/lake')
from spark_conf import CATALOG, lake_session
s = lake_session('k2-scd2-first-version-epoch')
for t, key in (('gold.dim_instrument', 'exchange, canonical_symbol'), ('gold.dim_venue', 'exchange')):
    on = ' AND '.join(f'd.{c} = m.{c}' for c in key.split(', '))
    s.sql(f'''
      MERGE INTO {CATALOG}.{t} d
      USING (SELECT {key}, MIN(valid_from) AS first_from FROM {CATALOG}.{t} GROUP BY {key}) m
      ON {on} AND d.valid_from = m.first_from
      WHEN MATCHED THEN UPDATE SET d.valid_from = TIMESTAMP '1970-01-01 00:00:00'
    ''')"
#  gold.dim_instrument  34 rows  valid_from 1970-01-01 00:00:00  recorded_at 2026-08-28 14:59:20.699765
#  gold.dim_venue        3 rows  valid_from 1970-01-01 00:00:00  recorded_at 2026-08-28 14:59:20.699765
```

**After**, from `notebooks/` (`k2lake.connect()` + `pin()`, current snapshots):

| Scope | Trades | ASOF-joined | Match |
|---|---|---|---|
| kraken `BTC/USD`, 2026-08-28 14:00–15:00 | 7,651 | 7,651 | yes |
| `BTC/USD` + `BTC/USDT`, all venues | 1,442,335 | 1,442,335 | yes |
| kraken, all — `tick_size IS NULL` after the join | — | 90,662 | 0 NULL |

(The overall count is above the 1,380,178 measured at diagnosis because the ingest kept
running between the two reads.)

`make test-python` 275 passed (+3 in `tests/test_lake_scd2.py`, one of which — a trade a
year older than the first run must still join — fails against the old rule); `make lint`
clean; `bash scripts/check-docs.sh` PASS.

**Revisit when** a source publishes an effective date of its own — a venue-dated tick-size
change, say. At that point `valid_from` stops being derivable from the run at all and comes
off the payload, and `EPOCH` applies only to attributes with no stated start.
