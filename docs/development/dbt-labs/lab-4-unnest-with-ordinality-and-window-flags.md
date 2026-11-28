# Lab 4. UNNEST WITH ORDINALITY and window flags

**Goal.** Follow one Kraken websocket frame through `silver_trade`: how it becomes one row per
trade with its place in the frame, how a window function flags a repeated delivery, and where a
frame goes when it cannot be used. Then read the two tables that hold what silver leaves out.

**You will learn**

- what `CROSS JOIN UNNEST(...) WITH ORDINALITY` does, and why the ordinal is part of the key;
- why every field is typed under `try()` or `try_cast`, and what a frame-wide `bool_and(...) over`
  does with the result;
- how `row_number() over (partition by ... order by ...)` marks a duplicate, and why the model
  re-emits an older row;
- how the held table `silver_trade_unresolved` and the rejected table `silver_trade_rejected` agree
  with silver on every frame.

You need nothing from labs 1 to 3 except their vocabulary: source, model, incremental, unit test.

## The files

| File | What it is |
|---|---|
| `contracts/market-capture/kraken-trades/models/silver_trade.sql` | the incremental model: one row per trade |
| `contracts/market-capture/kraken-trades/models/silver_trade_unresolved.sql` | a `table` model: the trades of every frame silver does not hold, because a symbol resolves to nothing |
| `contracts/market-capture/kraken-trades/models/silver_trade_rejected.sql` | a `table` model: every frame the model cannot type, with its reason |
| `contracts/market-capture/kraken-trades/unit_tests.yml` | thirteen unit tests, invented symbols and prices only |

Open `silver_trade.sql` beside this page. Its header comment is the longest of any model here, and
it is the specification.

## Steps

### 1. One frame is many trades

Kraken's websocket v2 trade channel sends one message, a **frame**, holding a `data` array of
trades. Bronze keeps each frame whole, as bytes, with where it came from (`kafka_partition`,
`kafka_offset`). The frame below is invented:

```json
{"channel":"trade","type":"update","data":[
  {"symbol":"AAA/ZZZ","side":"buy","price":10.5,"qty":2,"ord_type":"market","trade_id":1,"timestamp":"2026-01-05T10:00:00.000001Z"},
  {"symbol":"AAA/ZZZ","side":"sell","price":11,"qty":3,"ord_type":"limit","trade_id":2,"timestamp":"2026-01-05T10:00:00.000002Z"}]}
```

Silver wants one row per trade. SQL's tool for turning an array into rows is `UNNEST`, and
`silver_trade.sql` says it this way:

```sql
  from frames as f
  cross join unnest(try(cast(json_extract(f.frame, '$.data') as array(json)))) with ordinality as t (trade, ordinal)
  where json_extract_scalar(f.frame, '$.channel') = 'trade'
```

Read it from the inside out:

- `json_extract(f.frame, '$.data')` takes the `data` member of the parsed frame, still JSON;
- `cast(... as array(json))` makes it an array of JSON values, one per trade;
- `unnest(...) with ordinality as t (trade, ordinal)` turns the array into rows of two columns:
  `trade`, the element, and `ordinal`, its 1-based position in the array;
- `cross join` pairs each frame with each of its own rows, so a frame of two trades gives two rows
  and a frame with an empty array gives none.

The ordinal matters because a frame's trades have no key of their own that includes the frame:
the same `trade_id` can arrive in two frames (step 4). The contract's key is
`(kafka_partition, kafka_offset, trade_ordinal, trade_ts)`: the frame, the trade's place in it, and
the trade's time, the table's partition source (day), which lab 2, step 2, is why a merge's key holds. The first three alone are already unique.
The model casts it, since Trino's ordinal is a `bigint` and the contract's column is an integer:
`cast(t.ordinal as integer) as trade_ordinal`.

### 2. Typing under try

Each field is read from the JSON text and typed in the same select:

```sql
    try_cast(json_extract(t.trade, '$.price') as decimal(38, 18)) as price,
    try_cast(json_extract(t.trade, '$.qty') as decimal(38, 18)) as quantity,
```

`json_extract` keeps the number as JSON, and a cast from JSON to `decimal` reads the number's own
digits, never a double, so nothing is rounded (the unit test `silver_trade_keeps_every_decimal_digit`
pins this). The `try_` forms return null where a plain cast would raise. That is the design: a
trade whose price is missing or is the string `"n/a"` becomes a row with a null price, not an error.
An error would fail the build, and the wrapper would roll it back (lab 3). Because the model reads
every frame it has not yet read, on every build, one bad frame would fail every later build, and
the venue would stop publishing.

So silver cannot let a null into a `NOT NULL` column either. The frame decides, as a whole:

```sql
    bool_and(
      t.trade_ts is not null and t.trade_id is not null and t.venue_symbol is not null and t.side is not null
      and t.price is not null and t.quantity is not null and t.frame_type is not null
    ) over (partition by t.kafka_partition, t.kafka_offset) as frame_types
```

`bool_and(x) over (partition by ...)` is a **window function**: it computes over a set of rows
without collapsing them. Here the window is the frame (its partition and offset), so every row of
a frame carries one answer, true only if every trade of the frame typed. The select later keeps
`where frame_resolves and frame_types`. One bad trade leaves its whole frame out, which is the
rule for a venue that sends its trades together.

### 3. Resolving the symbol

Silver also names the instrument. It joins the security master's `venue_symbol` (lab 5) on the
symbol and on time:

```sql
  left join {{ k2_input('security_master', 'venue_symbol') }} as v
    on v.venue = 'kraken'
    and v.venue_symbol = t.venue_symbol
    and t.trade_ts >= v.valid_from
    and t.trade_ts < v.valid_to
```

This is a **type-2 as-of join**: each symbol has versions, each valid over a half-open span
`[valid_from, valid_to)`, and the trade picks the one its own time falls in. `k2_input` (lab 1)
is how another product's table is read; compile the product to see what it becomes, as lab 1 did:

```bash
make dbt-dev K2_DBT_DEV=$PWD/build/lab
printf '%s\n' "{% macro is_incremental() %}{{ return(var('lab_incremental', false)) }}{% endmacro %}" \
  > build/lab/compiled/dbt/macros/lab_is_incremental.sql
export K2_TRINO_HOST=127.0.0.1 K2_TRINO_INTERNAL_PORT=18089 DBT_ENV_SECRET_K2_TRINO_PASSWORD=nothing-listens K2_TRINO_CA=/dev/null
L=$PWD/build/lab
uv run --project dbt --locked -q dbt --no-use-colors compile --project-dir $L/compiled/dbt --profiles-dir dbt \
  --target-path $L/target-false --log-path $L/logs --no-partial-parse --selector kraken_trades \
  --vars '{"lab_incremental": false}'
M=$L/target-false/compiled/k2/models/market_capture/kraken_trades
grep -n 'unnest\|"bronze_trade"\|"venue_symbol"' $M/kraken_trades__silver_trade.sql
```

```text
42:  from "lake"."kraken_trades"."bronze_trade" as b
65:  cross join unnest(try(cast(json_extract(f.frame, '$.data') as array(json)))) with ordinality as t (trade, ordinal)
79:  left join "reference_data"."security_master__base"."venue_symbol" as v
```

(Both `dbt compile` runs end with dbt's summary, `Found 20 models, 4 seeds, ...`.) The source and
the other product's table became plain three-part names; `UNNEST ... WITH ORDINALITY` went to Trino
as written, since dbt does not rewrite SQL it does not template.

The `left join` is deliberate. An inner join would drop a trade whose symbol resolves to nothing,
and silently. A left join keeps it with a null `instrument_id`, and a second window decides:

```sql
    bool_and(v.instrument_id is not null) over (partition by t.kafka_partition, t.kafka_offset) as frame_resolves,
```

A frame is taken only when every trade in it resolves. One that does not stays out of silver
whole, and step 5 shows where it is held.

### 4. The duplicate flag

Kraken sends a snapshot of recent trades after a reconnect, so a trade can arrive twice: once in an
update, once in the snapshot. Silver keeps both and flags the later one, so a reader chooses. The
flag is a window function again:

```sql
    row_number() over (
      partition by venue_symbol, trade_id
      order by kafka_partition, kafka_offset, trade_ordinal
    ) > 1 as is_duplicate
```

Within one `(venue_symbol, trade_id)`, `row_number()` numbers the deliveries in the order bronze
received them. The first is number 1 and is the original; every later one is `is_duplicate`. The
`order by` is the order the capture produced, because one symbol keys one partition (the header's
words). The unit test `silver_trade_flags_a_later_delivery` shows it: trade 7 in an update at
offset 10 and again in a snapshot at offset 12.

The model is incremental, so a build sees only frames it has not read. A later build can bring a
frame that is older than a delivery already merged: a held frame whose symbol now resolves
(step 5). Then the new row is the original and the merged one becomes the duplicate, so the model
must change a row it already wrote. Read the two blocks that do this:

```sql
  -- the rows already here that share a trade id with an arriving row
  select ... false as is_new, s.is_duplicate as was_duplicate
  from {{ this }} as s
  where exists (select 1 from arrived as a where a.venue_symbol = s.venue_symbol and a.trade_id = s.trade_id)
```

```sql
from flagged
where is_new or is_duplicate <> was_duplicate
```

The model pulls in the existing rows that share a trade id with an arriving one, numbers them all
together, and emits the new rows plus any old row whose flag changed. The merge on the contract's
key (lab 2) then updates those old rows in place. The unit test
`silver_trade_takes_back_a_held_frame_and_reflags` is that case; its header says "for an
incremental run the expected rows are what the model hands the MERGE, not the merged table".

### 5. What silver leaves out, and where it goes

Which frames does an incremental build read? The header says: every frame no silver row comes from
yet, an anti-join on `(kafka_partition, kafka_offset)`:

```sql
  where not exists (
    select 1 from {{ this }} as s
    where s.kafka_partition = b.kafka_partition and s.kafka_offset = b.kafka_offset
  )
```

A high-water mark (read offsets above the newest one silver holds) would be cheaper, and wrong
here: a held frame lies below the newest offset, and silver must read it again the build its symbol
resolves. The anti-join is the watermark kept per frame. Its cost is a read of every key of bronze
and silver on each build, which the model's `ponytail:` comment names, with when to bound it.

A frame silver does not hold after the merge is in one of two tables, both `materialized='table'`,
rebuilt whole on every build:

| Table | A frame is here when | Its test |
|---|---|---|
| `silver_trade_unresolved` | every field typed, but a symbol resolved to nothing at the trade's time | `unresolved_rows_zero`, `severity: warn` |
| `silver_trade_rejected` | the model cannot type it: not JSON, no `data` array, no `type`, or a trade missing a field or holding one that does not cast | `rejected_frames_zero`, `severity: warn` |

A frame of the trade channel that is neither (a subscribe acknowledgement) is in no table.

Both tables read `silver_trade` through `ref('kraken_trades__silver_trade')` to find the frames it
does not hold, so dbt builds silver first. They repeat silver's own typing expressions rather than
share them, and the rejected model's header says why: each test "is the very expression
silver_trade and silver_trade_unresolved type the field with, under try() or try_cast, so the three
agree on every frame". `silver_trade_rejected.sql` gives the reason as a `case` over the same
expressions, field by field (`field_missing:price`, `field_not_numeric:price`), and takes the
frame's first bad trade with `min_by(reason, ordinal)`: here the ordinal is used to pick one reason
per frame.

Why `warn`? A `warn` test reports and does not fail the build (lab 3). A frame that cannot be typed
is a fact about the venue's data, not a defect in the build, and the rest of the venue publishes
while the owner reads the rejected table. A `fail` would roll the whole venue back for one frame.

### 6. Run the logic yourself

The unit tests in `unit_tests.yml` run the model on invented frames, each input replaced by
`select` rows. Read two and what each asserts:

- `silver_trade_unnests_each_frame_with_ordinals`: an update frame with two trades gives ordinals 1
  and 2; a snapshot frame gives its one row marked `snapshot`.
- `silver_trade_resolves_through_the_1970_version`: a trade before the symbol's first fetch still
  resolves, because the security master's first version opens at 1970-01-01 (lab 5).

Running them needs Trino, which a throwaway lake gives (Docker, a few minutes):

```bash
make test-dbt SELECTOR=kraken_trades
```

`SELECTOR=kraken_trades` builds this product and the products its inputs name, transitively, each
with its tests, on a throwaway lake: it prints `dbt-test: building in order: currency_master
kraken_instruments security_master kraken_trades`. Each product then prints a row per test, as
lab 2, step 5 showed: `<table> run_id=<run id>: <unique_id> | <status> | <failures> | <tested> |
<release id> | <invocation id>`. A run on 2026-11-28 printed these rows among kraken_trades' (the
ids are that run's own; the data is the synthetic fixtures'), and its last three lines:

```text
  "market_capture_build"."kraken_trades__base"."dq_test_results" run_id=dbt-test/dbt-test-2121393-kraken_trades: unit_test.k2.kraken_trades__silver_trade.silver_trade_resolves_through_the_1970_version | pass | 0 | market_capture_build.kraken_trades__base.silver_trade | v0.0.0-ci.1-f8bf65111acb | 0a233acb-df69-4026-9949-3c8e94394026
  "market_capture_build"."kraken_trades__base"."dq_test_results" run_id=dbt-test/dbt-test-2121393-kraken_trades: unit_test.k2.kraken_trades__silver_trade.silver_trade_unnests_each_frame_with_ordinals | pass | 0 | market_capture_build.kraken_trades__base.silver_trade | v0.0.0-ci.1-f8bf65111acb | 0a233acb-df69-4026-9949-3c8e94394026
  "market_capture_build"."kraken_trades__base"."dq_test_results" run_id=dbt-test/dbt-test-2121393-kraken_trades: unit_test.k2.kraken_trades__silver_trade_rejected.rejected_keeps_each_frame_it_cannot_type_with_its_reason | pass | 0 | market_capture_build.kraken_trades__base.silver_trade_rejected | v0.0.0-ci.1-f8bf65111acb | 0a233acb-df69-4026-9949-3c8e94394026
ok results: 49 row(s) in "market_capture_build"."kraken_trades__base"."dq_test_results" under run_id dbt-test/dbt-test-2121393-kraken_trades, one per test, statuses as dbt reported
ok build: 4 compiled product(s) of v0.0.0-ci.1-f8bf65111acb built with their tests on a throwaway lake, every result recorded
dbt-test: PASS
```

The two tests above and the rejected table's (step 5) each read `pass` with no failing row; the
`results` line is the check that the results table holds one row per test dbt ran, and `4
compiled product(s)` are kraken_trades and the three it reads through.

## Exercise

Add a third trade to the frame in the first unit test, with a `price` of `"n/a"` (a string), on
paper. Answer without running anything:

1. Does `silver_trade` hold the frame's other two trades?
2. Which table holds the frame, and what is its `reason`?
3. The frame's reason names the field of its first bad trade. Which expression in
   `silver_trade_rejected.sql` picks that one when two trades are bad?

## Why it works

`UNNEST ... WITH ORDINALITY` gives a trade its place in the frame, and place plus frame is what
makes a key out of data that has none. Every typed field is a `try`, so a bad frame is a null and
never an error. Two frame-wide window functions turn per-trade nulls into a per-frame decision, and
the three models type every field with the same expression, so each frame is in exactly one place:
silver, held, or rejected.

## Check yourself

1. A frame holds trades of two symbols, and one resolves and one does not. Which of the three
   tables holds each trade, and why does the `is_duplicate` window see neither?
2. Why is `silver_trade_unresolved` a `table` rebuilt whole while `silver_trade` is incremental?
3. `silver_trade_unresolved` has no `is_duplicate` column. Which window in `silver_trade.sql`
   would need to move into it, and why does the model leave it out?
