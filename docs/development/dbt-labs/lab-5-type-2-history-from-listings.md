# Lab 5. Type-2 history from listings

**Goal.** Read how the security master turns a venue's listings, fetched again and again, into
tables where every instrument and every trading symbol has versions with a validity span, and why
the platform builds them as plain `table` models and not as dbt snapshots.

**You will learn**

- what **type-2** history is, and the half-open span `[valid_from, valid_to)` that lets a trade
  find the version in force at its own time;
- the chain of models: claims, held claims, then `instrument` and `venue_symbol`;
- the pattern for a version table: `lag` to compare a row with the previous one, `lead` to close
  its span, `row_number` to find the first;
- why the first version opens at 1970-01-01 and the open one closes at 9999-12-31 23:59:59;
- why the history is rebuilt whole every run, and what a dbt snapshot would have done instead.

Lab 4 used `venue_symbol` from the reading side, in the as-of join of `silver_trade`; this lab
builds it. Lab 2's vocabulary (a `table` model, a unit test) is assumed.

## The files

| File | What it is |
|---|---|
| `contracts/reference-data/security-master/models/venue_symbol_claim.sql` | one **claim** per listed version of a pair: this venue spelling means this instrument, from a fetch to the pair's next version |
| `contracts/reference-data/security-master/models/venue_symbol_held.sql` | the claims set aside, for the owner to read |
| `contracts/reference-data/security-master/models/instrument.sql` | type-2 history of each instrument: its attributes over time, and when it was listed |
| `contracts/reference-data/security-master/models/venue_symbol.sql` | type-2 history of each venue spelling: which instrument it named over time |
| `contracts/reference-data/security-master/models/asset.sql` | every base and quote code, once: no history, so not used here |
| `contracts/reference-data/security-master/unit_tests.yml` | the unit tests, with `ALPHA/USD`-style invented symbols |

## Steps

### 1. Type-2 history, and a span that does not overlap

A venue's listing changes: a pair's tick size moves, a pair is delisted and relisted, a symbol is
reused. A table that holds only the latest row (**type 1**) loses every earlier state. A **type-2**
table keeps a row per state, each with the span over which it was true:

| venue_symbol | instrument_id | valid_from | valid_to |
|---|---|---|---|
| ALPHA/USD | ALPHA-USD | 1970-01-01 00:00:00 | 2026-01-05 11:00:00 |
| ALPHA/USD | ALPHA-USD | 2026-01-05 11:00:00 | 9999-12-31 23:59:59 |

(An invented illustration of the shape, not output.) The span is **half-open**: `valid_from` is
included and `valid_to` is not, so one instant falls in exactly one version, and the
join in `silver_trade.sql` (lab 4) is `t.trade_ts >= v.valid_from and t.trade_ts < v.valid_to`.
A closed span (`<=` on both ends) would put a trade at exactly 11:00 in two versions and the join
would return it twice.

Both tables are `materialized='table'`: dbt drops and rebuilds the whole table on every run
(lab 2, step 1). No `is_incremental()` appears in any model here.

### 2. Claims: the one place the matching rule lives

A venue lists pairs under its own codes (`XBT`), a trade names its pair by the websocket stream's
spelling (`BTC/USD`), and the platform names an instrument `BASE-QUOTE`. `venue_symbol_claim.sql`
reads the Kraken product's `silver_listing` through `k2_input`, and its header says what it
makes: "Each listed version of a pair ... becomes one claim, a span from the fetch that first stated
it to the pair's next version". The span comes from one window:

```sql
    coalesce(
      lead(first_fetched_at) over (partition by pair_code order by first_fetched_at),
      timestamp '9999-12-31 23:59:59 UTC'
    ) as next_fetched_at,
```

`lead(x) over (partition by p order by t)` is the value of `x` on the next row of the same
partition. For a pair's last version there is no next row, so `lead` is null and `coalesce` fills
the open end. It is `9999-12-31 23:59:59 UTC`, never null: a null end would make `t < valid_to` null
for every trade, and a join on it match nothing. A far-future date compares like any other.

The model then splits the websocket name at its one slash, upper-cases each code, maps it through
the reviewed seed `seeds/code_exceptions.csv` (Kraken's older `XBT` is `BTC`), and recomposes
`BASE/QUOTE` and `BASE-QUOTE`. The header explains why it splits instead of stripping the `X` and
`Z` prefixes of the REST names: "no prefix rule tells XXBT from an asset whose code starts with X".

### 3. Held claims: refuse the ambiguous, publish the rest

Two pairs of one venue can claim one spelling over spans that overlap. Publishing either would
pick an answer for the overlap, so both are held:

```sql
contested as (
  -- each spelling of a venue that two pairs claim over spans that overlap
  select distinct a.venue, a.venue_symbol
  from claims as a
  inner join claims as b
    on b.venue = a.venue and b.venue_symbol = a.venue_symbol and b.pair_code <> a.pair_code
      and b.first_fetched_at < a.next_fetched_at
      and a.first_fetched_at < b.next_fetched_at
)
```

The last two lines are the standard test for two half-open spans overlapping: each starts before
the other ends. The claim table keeps the claim and sets `held_reason` to `claimed_twice`, or to
`not_base_slash_quote` when the name has no single slash. `venue_symbol_held.sql` is the same table
filtered to `where held_reason is not null`, so the owner reads one small table, and its warn test
reports any row while the rest publishes (lab 4 used the same pattern).

Every history model starts with `where held_reason is null`, so "only unheld claims reach here, so
no two overlap for a spelling" is a fact the later windows rely on.

### 4. `venue_symbol`: a version starts when the answer changes

Read `venue_symbol.sql` from the top. A spelling's claims, in order of time, are compared with
the one before:

```sql
    row_number() over w as claim_number,
    lag(instrument_id) over w as was_instrument_id,
    lag(pair_code) over w as was_pair_code
  from claims as c
  window w as (partition by venue, venue_symbol order by claimed_from)
```

`lag(x)` is `lead`'s opposite: the value on the previous row. `window w as (...)` names a window
once so three functions share it. A version opens where the answer changed:

```sql
  where
    claim_number = 1
    or instrument_id is distinct from was_instrument_id
    or pair_code is distinct from was_pair_code
```

`is distinct from` is `<>` that treats null as a value: `x <> null` is null, which a `where` drops,
where `x is distinct from null` is true. A claim whose previous claim does not exist, or an attribute
that was null, compares correctly.

A claim that only changes the listing's attributes (a tick size) opens no version, since this table
answers "what does the spelling mean", not "what is the pair like". A gap while the pair is
delisted opens none either, so a trade still resolves. The delisting is `instrument.is_listed`'s to
record.

The select that follows closes each span with `lead`, and opens the first with 1970:

```sql
  case when row_number() over w = 1 then timestamp '1970-01-01 00:00:00 UTC' else recorded_at end as valid_from,
  coalesce(lead(recorded_at) over w, timestamp '9999-12-31 23:59:59 UTC') as valid_to,
```

Notice what `valid_from` is: not when the version was fetched, `recorded_at`, except for the first,
which is forced to `1970-01-01`. Two reasons, and the second is the one that cost the platform a
bug:

- *The spans must tile the timeline.* Each `valid_to` is the next `valid_from`, and the last is open,
  so every instant from 1970 on is in exactly one version. A gap or an overlap is a trade matching
  nothing or twice.
- *The first fetch is not when the symbol began.* The first version is "the first from 1970-01-01
  so that a trade older than the first fetch still resolves". A symbol the platform first learned
  of on 5 January was already trading on 4 January. If the first version opened on 5 January, every
  earlier trade would match nothing: no error, only fewer rows. ADR-030's Outcome records exactly
  this: the first version of a security master had opened at the run that found it and had
  silently emptied an as-of join, 36 of 7,651 trades surviving for one hour. `recorded_at` keeps the
  honest fetch time beside it, so "we learned this late" is still in the data.

The unit test `venue_symbol_trade_before_first_fetch_resolves_through_1970` pins it, with a trade
dated before any fetch.

### 5. `instrument`: more than one claim, so first find the dates

`instrument.sql` is the larger case: the attributes are the pair code, tick size, decimals, minimum order and status, and
an instrument can be delisted. The change points are not the rows of a table but the moments any
claim of the instrument starts or ends:

```sql
changes as (
  -- every fetch at which an instrument's state may change: one of its claims starts or ends
  select venue, instrument_id, base_asset, quote_asset, claimed_from as changed_at from claims
  union
  select venue, instrument_id, base_asset, quote_asset, claimed_to from claims
  where claimed_to < timestamp '9999-12-31 23:59:59 UTC'
),
```

At each of those moments the `states` model asks which claim is in force, by the half-open test:

```sql
  left join claims as c
    on c.venue = ch.venue and c.instrument_id = ch.instrument_id
      and c.claimed_from <= ch.changed_at and ch.changed_at < c.claimed_to
```

A `left join` again: when no claim is in force the instrument is **not listed**, and its attributes
are null (`c.pair_code is not null as is_listed`). Then `compared` takes `lag` of every attribute,
and `versions` keeps a moment where any one differs (`is distinct from` seven times). A moment where
nothing differs adds nothing. The select closes each span with `lead` as in step 4.

Work it by hand with the rows of the unit test `instrument_delisted_then_relisted`. The instrument
`ALPHA-USD` has two claims: 10:00 to 11:00, then 12:00 to open, same attributes. The change points
are 10:00, 11:00 (the first claim ends) and 12:00. The states are listed, **not listed** (no claim is
in force at 11:00), listed. All three differ from the one before, so there are three versions:

| valid_from | valid_to | is_listed | tick_size |
|---|---|---|---|
| 1970-01-01 00:00:00 | 2026-01-05 11:00:00 | true | 0.1 |
| 2026-01-05 11:00:00 | 2026-01-05 12:00:00 | false | null |
| 2026-01-05 12:00:00 | 9999-12-31 23:59:59 | true | 0.1 |

(Worked from the unit test's rows, not run. The test states the same three rows.) A trade at 11:30
finds the middle version and reads `is_listed = false`. The ordinary left join of a type-2 table,
which would have returned nothing for the delisted hour, now returns the answer "not listed".

Compare this with the test `instrument_changed_tick_size_closes_one_version_and_opens_another`: at
12:00 only the websocket name changes (`XALP/USD`), a column `instrument` does not carry, so no
version opens. A version is a change in what the table records, not any change at all.

### 6. Why not a dbt snapshot

dbt has a feature built for type-2 tables: a **snapshot**, which on each run compares a source with
its earlier rows and closes and opens versions. This repo uses none, and the allowed
materializations are table, incremental, ephemeral and an invoker view
(`MATERIALIZATIONS` in [`checks/sql.py`](../../../platform/k2gen/k2gen/checks/sql.py)). The reason is in
`instrument.sql`'s header: the table is "rebuilt whole from the published claims on every run, so a
`--full-refresh` gives the same rows and the history is the listing's". The difference:

- A snapshot's history is the history of its **runs**: it knows only what the source looked like
  when dbt ran. A run skipped, a source overwritten between runs, or the table dropped, and the
  history is gone, and no later run can rebuild it.
- Here the history is in the data. Silver keeps every fetched version of a pair, with the fetch
  time, so the full history is a pure function of it. Drop the table, build again, the same rows.
  That also makes the model testable on invented claim rows, as the unit tests do, with no table
  of earlier state to set up.

A snapshot is right when the source keeps no history and the only record of change is yours. It is
the wrong tool here, because the source keeps it.

### 7. Run it

The unit tests need Trino, so they are **run at a merge point**:

```bash
make test-dbt SELECTOR=security_master
```

The output is the shape of lab 2, step 5: a row per test (the ones whose `unique_id` contains
`instrument_delisted_then_relisted` and `venue_symbol_trade_before_first_fetch_resolves_through_1970`,
status `pass`) and `dbt-test: PASS`. It was not run for this lab, so check it at the merge point
against this shape. The compile, which needs no Trino, you can run now, as in lab 4 with
`--selector security_master`: it prints `Found 20 models, 4 seeds, 1 operation, 192 data tests,
10 sources, 522 macros, 40 unit tests`, the counts of the whole tree.

## Exercise

On paper, add a third claim to `instrument_changed_tick_size_closes_one_version_and_opens_another`:
from 13:00, `pair_decimals` goes from 1 to 2, nothing else changes. Write the rows of `instrument`
and the rows of `venue_symbol` for it. Which of the two gets a new version at 13:00?

## Why it works

Every span is half-open, every history starts at 1970 and ends at an explicit far-future date, so
the versions of a key tile the timeline and a trade lands in exactly one. The versions are computed
from the listing's own history, not from what dbt happened to see, so a rebuild reproduces them,
and the claim that two spellings clash is decided once, in one model, with the ambiguous held where
the owner reads it.

## Check yourself

1. A trade's time is exactly `valid_to` of one version of its symbol. Which version resolves it?
2. `venue_symbol` ignores a tick-size change and `instrument` does not. What does each table answer,
   and which one would a price rounding step read?
3. Someone proposes `valid_to` null for the open version, "since it has not ended". Which condition
   in `silver_trade.sql` stops working, and how would the failure show?
