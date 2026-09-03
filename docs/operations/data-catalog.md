# Data Catalog

One row per user-facing table: what a row *is*, how to filter it, which clock it carries,
and which engine's copy is authoritative. Written so a researcher can compose a correct
query from this page alone.

Two engines hold the same products and they are not interchangeable:

- **The Iceberg lake is the record** (ADR-018, ADR-026). Every gold table is populated
  there by the 5-minute Spark ingest, so it trails the market by up to one tick interval.
- **ClickHouse `gold` is the head.** `trades` and `book_top20` are fed live from the Avro
  topics and are seconds fresh; every other table is populated only by the pull runbook
  and is **empty until it runs**.

Run the freshness check before you trust either side.

```bash
docker exec k2-clickhouse clickhouse-client --user quant --password "$K2_QUANT_PASSWORD" -q \
  "SELECT (SELECT max(exchange_ts) FROM gold.trades)     AS trades_max_exchange_ts,
          (SELECT max(second)      FROM gold.book_top20) AS book_max_second,
          (SELECT count()          FROM gold.ohlcv_1m)   AS ohlcv_1m_rows FORMAT Vertical"
```

```
Row 1:
──────
trades_max_exchange_ts: 2026-09-03 13:12:21.514000
book_max_second:        2026-09-03 13:12:21
ohlcv_1m_rows:          0
```

```python
con.sql("""
  SELECT 'gold.trades' AS tbl, max(exchange_ts) AS newest, count(*) AS rows FROM lake.gold.trades
  UNION ALL SELECT 'gold.book_top20', max(second),       count(*) FROM lake.gold.book_top20
  UNION ALL SELECT 'gold.ohlcv_1m',   max(window_start), count(*) FROM lake.gold.ohlcv_1m
  ORDER BY tbl
""").df()
```

```
            tbl                           newest    rows
gold.book_top20        2026-09-03 13:06:03+00:00  763486
  gold.ohlcv_1m        2026-09-03 13:11:00+00:00   12416
    gold.trades 2026-09-03 13:11:04.911000+00:00 4620846
```

The two reads are about a minute apart in wall clock; ClickHouse is nonetheless ahead of
the lake on trades, and has no candles at all where the lake has 12,416. That is the
intended shape, not a fault.

## Lake — Iceberg via Lakekeeper on MinIO

Every table is Iceberg format-version 2, Parquet + zstd, copy-on-write, append-only
([`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql) header). Namespaces are
`raw`, `bronze`, `silver`, `gold`, `audit`
([`docker/lake/init-lake.sh`](../../docker/lake/init-lake.sh) line 122).

| Table | Grain — one row per … | Partition / sort | Time column (unit, zone) | Symbol columns | Identifier (dedup key) | Populated by |
|---|---|---|---|---|---|---|
| `raw.messages` | Kafka record, payload verbatim including the Confluent 5-byte header | `days(kafka_ts), topic` / `topic, partition, offset` | `kafka_ts` broker clock, µs, UTC; `ingest_ts` = batch clock, not per row | none — `key` is the canonical symbol as UTF-8 bytes on trades/book | `(topic, partition, offset)` | `ingest.py` stage 1 |
| `bronze.{binance_trade, binance_depth20, kraken_trade, kraken_book, kraken_instrument, coinbase_market_trades, coinbase_level2}` | captured WebSocket frame, venue field names and JSON types untouched | `days(recv_ts)` / `symbol, recv_ts_ns` | `recv_ts_ns` K2 receive clock, ns, UTC; `recv_ts` the same truncated to µs | `symbol` native only (NULL when the frame names no instrument) | `(src_topic, src_partition, src_offset)` | `bronze.py` stage 2 |
| `silver.trades_{binance,kraken,coinbase}` | trade **delivery** — a venue replay is a second row, flagged | `days(exchange_ts)` / `canonical_symbol, exchange_ts` | `exchange_ts` venue clock, µs, UTC | `symbol` native **and** `canonical_symbol` | `(src_topic, src_partition, src_offset, src_index)` | `silver.py` |
| `silver.book_{binance,kraken,coinbase}` | book frame (Coinbase: one `events[i]` event) | `days(recv_ts)` / `canonical_symbol, recv_ts_ns` | `recv_ts_ns` / `recv_ts`; Kraken also carries `exchange_ts` | `symbol` native **and** `canonical_symbol` | `(src_topic, src_partition, src_offset, src_index)` | `books.py` |
| `gold.trades` | logical trade — the **earliest** delivery, replays dropped | `exchange, days(exchange_ts)` / `canonical_symbol, exchange_ts` | `exchange_ts` venue clock, µs, UTC | `symbol` native **and** `canonical_symbol` | `(exchange, canonical_symbol, trade_id)` | `gold.py` |
| `gold.ohlcv_{1m,5m,1h}` | (exchange, symbol, bucket) | `exchange, months(window_start)` | `window_start` bucket start, UTC; `open_time`/`close_time` are venue clocks | `canonical_symbol` | `(exchange, canonical_symbol, window_start)` | `gold.py`, whole bucket recomputed and MERGEd |
| `gold.ohlcv_1d` | as above | `exchange` only | as above | `canonical_symbol` | as above | as above |
| `gold.bars` | event bar at the one canonical threshold per symbol (`config/bars.yaml`) | `exchange, months(open_time)` | `open_time`/`close_time` venue clocks, µs; `day` is a plain `DATE`, UTC | `canonical_symbol` | `(exchange, canonical_symbol, bar_kind, day, bar_seq)` | `bars.py`, a touched day is deleted and re-appended whole |
| `gold.book_top20` | (exchange, symbol, second) — the book at the **end** of that second | `exchange, days(second)` / `canonical_symbol, second` | `second` 1 Hz bucket, UTC; `recv_ts_ns` = the last frame folded in, **not** the second boundary | `symbol` native **and** `canonical_symbol` | `(exchange, canonical_symbol, second)` | `books.py` per-frame replay |
| `gold.bbo_1s` | one `gold.book_top20` row, projected | `exchange, days(second)` | `second`, as above | `canonical_symbol` | `(exchange, canonical_symbol, second)` | `books.py` |
| `gold.book_state` | (exchange, symbol, conn_id) — replay carry-over, operational, not a product | `exchange` | `last_second`, `updated_at` | `symbol` native only | `(exchange, symbol, conn_id)` | `books.py`, overwritten each run |
| `gold.dim_instrument` | **validity interval** of one instrument, SCD2 (ADR-030) | `exchange` / `canonical_symbol, valid_from` | `valid_from` inclusive, `valid_to` exclusive, open rows `9999-12-31 23:59:59` never NULL; `is_current` | `canonical_symbol` is the natural key half; `symbol` (native) is a **tracked attribute** — a rename opens a version | `(instrument_id, valid_from)` | `gold.py` via `scd2.plan`, from `config/instruments.yaml` + `bronze.kraken_instrument` |
| `gold.dim_venue` | validity interval of one venue, SCD2 | `exchange` | as above | n/a | `(venue_id, valid_from)` | `gold.py` via `scd2.plan` |
| `audit.checks` | check per run — append-only history | `days(run_ts)` | `run_ts` UTC | `scope` holds a table name or `topic/partition` | none; a failed check is never edited out | `maintenance.py`, `ingest.py`, `record_check.py` |

`gold.dim_instrument` and `gold.dim_venue` are the only gold tables `rebuild.py` will not
recreate: nothing is a source for lost dimension history
([`lake.sql`](../../docker/lake/ddl/lake.sql) above `dim_instrument`).

## ClickHouse `gold` — the served tier

[`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql)
is the contract; [`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql)
attaches the Redpanda feeds. Read `ReplacingMergeTree` tables with `FINAL`; a count
without it is a count of deliveries.

| Table / view | Engine | Grain | Partition / ORDER BY | Time column | Symbol columns | Dedup key | Populated by |
|---|---|---|---|---|---|---|---|
| `gold.trades` | `ReplacingMergeTree(first_seen)`, version = `UInt64max − recv_ts_ns` so the **earliest** delivery survives | logical trade | `toYYYYMM(exchange_ts)` / `(exchange, canonical_symbol, exchange_ts, trade_id)` | `exchange_ts` `DateTime64(6,'UTC')` | `symbol` native **and** `canonical_symbol` | the ORDER BY key | Kafka engine `gold.q_trades` live, plus pull |
| `gold.book_top20` | `ReplacingMergeTree(ver)`, `ver = snapshot_ts_ns` so the **latest** sample in a second survives | (exchange, symbol, second) | `toYYYYMM(second)` / `(exchange, canonical_symbol, second)` | `second` `DateTime('UTC')`, `snapshot_ts` µs, `snapshot_ts_ns` authoritative | `symbol` native **and** `canonical_symbol` | the ORDER BY key | Kafka engine `gold.q_book` live, plus pull |
| `gold.feed_errors` | `MergeTree` | Kafka record the decoder rejected, with its bytes | — / `(topic, partition, offset)` | `seen_at` | none | none | `gold.q_{trades,book}_errors_mv` |
| `gold.ohlcv_{1m,5m,1h,1d}` | `ReplacingMergeTree(computed_at)` | as lake `gold.ohlcv_*` | `toYYYYMM(window_start)` / `(exchange, canonical_symbol, window_start)` | `window_start` | `canonical_symbol` | the ORDER BY key | **pull only** — [`clickhouse-rebuild-from-lake.md`](../runbooks/clickhouse-rebuild-from-lake.md) |
| `gold.bars` | `ReplacingMergeTree(computed_at)` | as lake `gold.bars` | `toYYYYMM(day)` / `(exchange, canonical_symbol, bar_kind, day, bar_seq)` | `day` `Date`; `open_time`/`close_time` µs | `canonical_symbol` | the ORDER BY key | **pull only**; a threshold change needs `TRUNCATE` then a whole-table re-pull |
| `gold.bbo_1s` | `ReplacingMergeTree(src_snapshot_id)` | as lake `gold.bbo_1s` | `toYYYYMM(second)` / `(exchange, canonical_symbol, second)` | `second` `DateTime('UTC')` | `canonical_symbol` | the ORDER BY key, but the version is an Iceberg snapshot id, so a re-pull of a recomputed second keeps an arbitrary row | **pull only** |
| `gold.ohlcv_live(bucket = <seconds>)` | parameterised `VIEW` over `gold.trades FINAL` | (exchange, symbol, bucket) — **only buckets that had a trade** | n/a | `window_start` | `canonical_symbol` | n/a | computed on read |
| `gold.bbo_live` | `VIEW` over `gold.book_top20 FINAL WHERE depth > 0` | one `book_top20` row | n/a | `second`, `snapshot_ts` | `canonical_symbol` | n/a | computed on read |

There is **no security master in ClickHouse.** `dim_instrument` and `dim_venue` exist in
the lake only; join them there.

```
$ docker exec k2-clickhouse clickhouse-client --user quant --password "$K2_QUANT_PASSWORD" \
    -q "SHOW TABLES FROM gold"
bars
bbo_1s
bbo_live
book_top20
feed_errors
ohlcv_1d
ohlcv_1h
ohlcv_1m
ohlcv_5m
ohlcv_live
q_book
q_book_errors_mv
q_book_mv
q_trades
q_trades_errors_mv
q_trades_mv
trades
```

## Symbol conventions

`config/instruments.yaml` is the only mapping; silver resolves `canonical_symbol` from it,
never from the wire. `canonical` is `BASE/QUOTE`, both uppercase. `native` is the venue's
bytes, unmodified.

| Venue | native | canonical |
|---|---|---|
| binance | `BTCUSDT` | `BTC/USDT` |
| kraken | `BTC/USD` | `BTC/USD` — **identical strings** |
| coinbase | `BTC-USD` | `BTC/USD` |

**Asking for "BTC/USD on Kraken":** filter `exchange = 'kraken' AND canonical_symbol =
'BTC/USD'` in gold, or `canonical_symbol = 'BTC/USD'` in `silver.trades_kraken`. Kraken's
native spelling happens to be the same string, so a `symbol = 'BTC/USD'` filter also works
there — and silently returns nothing on the other two venues. Filter on
`canonical_symbol`, always. In `bronze.*` there is no canonical column at all: only
`symbol`, so a bronze query must use the native spelling.

`BTC/USDT` and `BTC/USD` are different instruments, not two spellings of one
(`config/instruments.yaml` header). Kraken WS v2 spells Bitcoin `BTC/USD` and Dogecoin
`DOGE/USD`; the v1 `XBT`/`XDG` spellings are gone with the Kotlin handlers (ADR-019).

## Time semantics

Every timestamp in every table is UTC. Four distinct clocks:

| Column | Whose clock | Unit | Where |
|---|---|---|---|
| `exchange_ts` | the venue, as it stamped the event | µs (`DateTime64(6,'UTC')` / Iceberg `TIMESTAMP`) | `silver.trades_*`, `gold.trades`, ClickHouse `gold.trades` |
| `recv_ts_ns` / `recv_ts` | K2, taken **before parse**; the only clock on every frame | ns / µs | every layer from bronze up |
| `kafka_ts` | the broker (producer CreateTime) | µs | `raw.messages` |
| `snapshot_ts_ns` / `second` | the 1 Hz sampler or the replay's second boundary | ns / s | `gold.book_top20`, `gold.bbo_1s` |

`recv_ts_ns` means **two different things** depending on the table:

- on `gold.trades` (and silver trades) it is the receive time of the frame that carried
  **this trade** — for `gold.trades`, of the winning (earliest) delivery;
- on `gold.book_top20` it is the receive time of the **last frame folded into the book**
  before that second closed. It is not the second boundary and not the time of any one
  quote. To age a book snapshot, use `second`.

`ingest_ts` and `computed_at` are job wall clocks, not data clocks. `ingest_ts` identifies
the batch, not the row.

### The `+1 SECOND` rule for `bbo_1s`

`gold.bbo_1s` and `gold.book_top20` hold the book at the **end** of the second they are
labelled with. The quote in force for a trade at `exchange_ts` is therefore the row for the
*previous* second, and the as-of join is on `second + 1 s <= exchange_ts`:

```sql
FROM t ASOF JOIN q
  ON t.exchange = q.exchange AND t.canonical_symbol = q.canonical_symbol
 AND t.exchange_ts >= q.second + INTERVAL 1 SECOND
```

Joining on `second <= exchange_ts` uses a quote from up to a second in the trade's future
and reads as 77 % of Binance prints trading through the book
(`notebooks/03_asof_trades_book.ipynb`).

### Candle and bar total order

Open and close are decided by a total order over trades, spelled three ways in three
engines and identical in value:

| Product | Order | Source |
|---|---|---|
| lake `gold.ohlcv_*` | `(exchange_ts, recv_ts_ns, trade_seq)` | `docker/lake/gold.py:310,313` — `min_by`/`max_by` over `struct(...)` |
| lake `gold.bars`, `k2lake.bars()` | `(exchange_ts, recv_ts_ns, trade_seq)` | `docker/lake/bars.py:33`, `notebooks/k2lake.py:153-154` |
| ClickHouse `gold.ohlcv_live` | `(exchange_ts, recv_ts_ns, toUInt64OrZero(trade_id))` | `10-gold-tables.sql:157,160` |

`trade_seq` *is* `trade_id` read as a number (`silver.trades_*.trade_seq` comment), so the
third component is the same value on both sides — which is why the three-way parity check
compares at tolerance zero. Any statement of a two-component order is wrong.

## The `SELECT *` trap

`price`, `qty`, `open`, `high`, `low`, `close`, `bid`, `ask`, `volume` and `quote_volume`
on the ClickHouse tables are `ALIAS` columns over the `_e8` integers. ClickHouse **omits
ALIAS columns from `SELECT *`**:

```
$ ... -q "SELECT * FROM gold.trades FINAL LIMIT 1 FORMAT TSVWithNames" | head -1
exchange	symbol	canonical_symbol	trade_id	price_e8	qty_e8	side	exchange_ts	recv_ts_ns	seq	conn_id	conn_msg_seq	src_topic	src_partition	src_offset	first_seen
```

No `price`, no `qty`. The same applies to `gold.ohlcv_*`: `SELECT *` produces a candle file
with no OHLC. Aliases also do not survive a subquery, because the inner `*` never
materialised them:

```
$ ... -q "SELECT price FROM (SELECT * FROM gold.trades FINAL LIMIT 1)"
Code: 47. DB::Exception: Unknown expression identifier 'price' in scope
SELECT price FROM (SELECT * FROM gold.trades FINAL LIMIT 1). (UNKNOWN_IDENTIFIER)
```

**Spell the columns out in any export or subquery.** Filter and group on the `_e8`
integers; project the aliases last, where the cast is paid once per output row.

The lake has no aliases: `gold.*` carries `price_e8` / `qty_e8` / `open_e8` … and you divide
by `1e8` yourself, so `SELECT *` there is complete.

## Column-name divergences

Same values, different spellings. There is no table in this platform that is "column for
column" identical across both engines.

| Concept | Lake | ClickHouse |
|---|---|---|
| top-20 book levels | `bid_px_e8`, `bid_qty_e8`, `ask_px_e8`, `ask_qty_e8` (`ARRAY<BIGINT>`) | `bid_px`, `bid_qty`, `ask_px`, `ask_qty` (`Array(Int64)`) — still 1e-8 fixed point despite the name |
| trade price / quantity | `price_e8`, `qty_e8` | `price_e8`, `qty_e8` plus `price`, `qty` aliases |
| candle OHLC | `open_e8` … `close_e8` | the same, plus `open` … `close` aliases |
| BBO top of book | `bid_e8`, `ask_e8` | `bid_e8`, `ask_e8` plus `bid`, `ask` aliases |

The reload runbook maps the four array columns by name in its `INSERT … SELECT`
([`clickhouse-rebuild-from-lake.md`](../runbooks/clickhouse-rebuild-from-lake.md) § book).

## Lake vs ClickHouse: which wins

The lake is the system of record on every conflict (ADR-018, ADR-026). Three divergences
are structural, not faults:

**1. Book construction differs, and the numbers differ with it.** ClickHouse
`gold.book_top20` is the capture's own 1 Hz sampler, deduplicated to the *latest sample in
each second* (`ver = snapshot_ts_ns`). Lake `gold.book_top20` is a replay of **every**
venue frame, emitting the state at the *end* of each second. Same key, different
definitions of "the book in that second". Measured over 12:55:00–12:58:00Z on 2026-09-03,
joining `lake.gold.bbo_1s` to ClickHouse `gold.bbo_live` on `(exchange, canonical_symbol,
second)`:

```
   exchange canonical_symbol  seconds  identical  max_bid_diff
0    kraken          BTC/USD      180      143.0         37.50
1   binance         BTC/USDT      176      112.0         29.99
2  coinbase          BTC/USD      180      155.0         13.14
3    kraken          ETH/USD      180       66.0          1.50
4   binance         ETH/USDT      176      103.0          1.36
5  coinbase          ETH/USD      180       91.0          0.68
```

Up to $37.50 apart on the same second. Use the lake for any research number; ClickHouse's
book views are a live monitor.

**2. ClickHouse candles, bars and `bbo_1s` are empty until the pull runs.** They are
pull-fed by design and never computed in ClickHouse
([`10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql), above the candle
tables). For the head, use `gold.ohlcv_live(bucket = 60)` and `gold.bbo_live` — noting
that `ohlcv_live` emits only buckets that had at least one trade, so a quiet minute is a
missing row, not a flat candle.

**3. No security master in ClickHouse.** `gold.dim_instrument` / `gold.dim_venue` are lake
tables. An instrument attribute as of a trade is a lake-side `ASOF JOIN`.

## Completeness signals

What the archive knows about its own holes. None of these exist in ClickHouse.

| Signal | Where | Means |
|---|---|---|
| `seq_gap`, `missing_before` | `silver.trades_*`, `gold.trades` | trade ids the archive never received before this trade; `missing_before` counts them. `NULL` = no previous trade in the lookback, so unknowable |
| `venue_replay` | `silver.trades_*` only | an earlier delivery of the same `(symbol, trade_id)` exists. `gold.trades` is the `venue_replay = false` rows, so the flag is not carried there |
| `precision_loss` | `silver.trades_*` | price or qty needs more than 8 decimals; the 1e-8 fixed point of gold cannot hold it |
| `checksum_ok` | `silver.book_kraken`, `gold.book_top20`, `gold.bbo_1s` | Kraken's CRC32 verified by replaying the connection's book at the pair's precision. `NULL` = precision unknown or the venue publishes no checksum |
| `seq_gap` | `silver.book_binance` | `lastUpdateId` did not advance past the previous frame on this connection |
| `audit.checks` | `lake.audit.checks` | one row per check per run: `offset_continuity`, `duplicate_identifiers`, `sequence_gaps`, `venue_replay`, `unresolvable_schema_id`, `offset_gap`, `manual_purge`. `passed = false` is what makes a maintenance run exit non-zero |
| `gold.feed_errors` | ClickHouse | the served tier's counterpart: Kafka records the decoder rejected, with their bytes |

## Where parity is checked

| Check | Compares | Command |
|---|---|---|
| OHLCV | ClickHouse `ohlcv_live` on read, lake `gold.ohlcv_*`, DuckDB over silver | `make parity-ohlcv` |
| Event bars | lake `gold.bars`, DuckDB window SQL, a Python reference implementation | `make parity-bars` |

Both run at the pinned snapshot in `tests/parity/pinned.json`, so a re-run compares the
same rows.

---

## Examples

Every query below was run against the live stack on 2026-09-03 and its output pasted.
The lake examples run on the host through
[`notebooks/k2lake.py`](../../notebooks/k2lake.py):

```bash
cd notebooks && uv sync && uv run python -   # then: from k2lake import connect, pin
```

`pin(con)` creates one `pinned.<ns>_<table>` view per gold, silver and audit table at its
current snapshot id; those are the views below. `raw` and `bronze` are not pinned, so
those two examples read `lake.<ns>.<table>` directly.

ClickHouse examples use the read-only `quant` user:

```bash
set -a && . ./.env && set +a
CH="docker exec k2-clickhouse clickhouse-client --user quant --password $K2_QUANT_PASSWORD"
```

### `raw.messages`

```sql
SELECT topic, count(*) AS rows, max(kafka_ts) AS newest
FROM lake.raw.messages WHERE topic LIKE '%kraken%' GROUP BY topic
```

```
                         topic     rows                           newest
market.crypto.v3.trades.kraken    94975 2026-09-03 13:06:03.438000+00:00
   market.crypto.v3.raw.kraken 13382683 2026-09-03 13:06:04.172000+00:00
  market.crypto.v3.book.kraken   255453 2026-09-03 13:06:03.491000+00:00
```

### `bronze.kraken_trade`

Venue field names, venue nesting: Kraken sends N trades per frame in `data[]`.

```sql
SELECT recv_ts, symbol, data[1].price AS price, data[1].qty AS qty, data[1].side AS side
FROM lake.bronze.kraken_trade WHERE symbol = 'BTC/USD' ORDER BY recv_ts_ns DESC LIMIT 3
```

```
                         recv_ts  symbol   price      qty side
2026-09-03 13:06:01.766565+00:00 BTC/USD 78562.5 0.000378  buy
2026-09-03 13:05:54.490266+00:00 BTC/USD 78562.4 0.012970 sell
2026-09-03 13:05:53.317612+00:00 BTC/USD 78562.5 0.000019  buy
```

### `silver.trades_kraken`

```sql
SELECT canonical_symbol, symbol, trade_id, price, qty, side, exchange_ts, venue_replay, seq_gap
FROM pinned.silver_trades_kraken WHERE canonical_symbol = 'BTC/USD'
ORDER BY exchange_ts DESC LIMIT 3
```

```
canonical_symbol  symbol  trade_id   price      qty side                      exchange_ts  venue_replay  seq_gap
         BTC/USD BTC/USD 106704564 78562.5 0.000378  buy 2026-09-03 13:06:01.607226+00:00         False    False
         BTC/USD BTC/USD 106704561 78562.4 0.019079 sell 2026-09-03 13:05:54.335566+00:00         False    False
         BTC/USD BTC/USD 106704562 78562.4 0.000477 sell 2026-09-03 13:05:54.335566+00:00         False    False
```

Two trades share one `exchange_ts`: one frame delivered both. The third component of the
candle order breaks that tie.

### `silver.book_kraken`

The integrity check, as a tally:

```sql
SELECT checksum_ok, count(*) AS frames FROM pinned.silver_book_kraken GROUP BY checksum_ok ORDER BY 1
```

```
 checksum_ok   frames
        True 13305099
```

### `gold.trades`

```sql
SELECT exchange, canonical_symbol, trade_id, price_e8 / 1e8 AS price, qty_e8 / 1e8 AS qty,
       side, exchange_ts, seq_gap, missing_before
FROM pinned.gold_trades WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
ORDER BY exchange_ts DESC LIMIT 3
```

```
exchange canonical_symbol  trade_id   price      qty side                      exchange_ts  seq_gap  missing_before
  kraken          BTC/USD 106704564 78562.5 0.000378  buy 2026-09-03 13:06:01.607226+00:00    False               0
  kraken          BTC/USD 106704561 78562.4 0.019079 sell 2026-09-03 13:05:54.335566+00:00    False               0
  kraken          BTC/USD 106704562 78562.4 0.000477 sell 2026-09-03 13:05:54.335566+00:00    False               0
```

ClickHouse, same instrument — note `FINAL`, and the `price` / `qty` aliases named
explicitly:

```bash
$CH -q "SELECT exchange, canonical_symbol, trade_id, price, qty, side, exchange_ts
        FROM gold.trades FINAL
        WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
        ORDER BY exchange_ts DESC LIMIT 3 FORMAT PrettyCompactMonoBlock"
```

```
   ┌─exchange─┬─canonical_symbol─┬─trade_id──┬───price─┬────────qty─┬─side─┬────────────────exchange_ts─┐
1. │ kraken   │ BTC/USD          │ 106704814 │ 78501.2 │   0.000051 │ sell │ 2026-09-03 13:09:27.773053 │
2. │ kraken   │ BTC/USD          │ 106704813 │ 78501.5 │ 0.00007043 │ sell │ 2026-09-03 13:09:27.773053 │
3. │ kraken   │ BTC/USD          │ 106704812 │ 78502.4 │     0.0004 │ sell │ 2026-09-03 13:09:27.773053 │
```

### `gold.ohlcv_1m`

```sql
SELECT window_start, open_e8 / 1e8 AS open, high_e8 / 1e8 AS high, low_e8 / 1e8 AS low,
       close_e8 / 1e8 AS close, volume, trade_count
FROM pinned.gold_ohlcv_1m WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
ORDER BY window_start DESC LIMIT 3
```

```
             window_start    open    high     low   close   volume  trade_count
2026-09-03 13:06:00+00:00 78562.5 78562.5 78562.5 78562.5 0.000378            1
2026-09-03 13:05:00+00:00 78580.5 78580.5 78562.4 78562.4 1.665577          118
2026-09-03 13:04:00+00:00 78650.1 78650.1 78580.5 78580.5 0.596911           89
```

The ClickHouse `gold.ohlcv_1m` table is empty until the pull runs. The head is the view:

```bash
$CH -q "SELECT exchange, canonical_symbol, window_start, open, high, low, close, volume, trade_count
        FROM gold.ohlcv_live(bucket = 60)
        WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
        ORDER BY window_start DESC LIMIT 3 FORMAT PrettyCompactMonoBlock"
```

```
   ┌─exchange─┬─canonical_symbol─┬────────window_start─┬────open─┬────high─┬─────low─┬───close─┬─────volume─┬─trade_count─┐
1. │ kraken   │ BTC/USD          │ 2026-09-03 13:09:00 │ 78532.5 │ 78532.5 │ 78501.2 │ 78501.2 │ 0.18626765 │          43 │
2. │ kraken   │ BTC/USD          │ 2026-09-03 13:08:00 │ 78508.8 │ 78537.7 │ 78503.5 │ 78532.4 │ 1.86799297 │          80 │
3. │ kraken   │ BTC/USD          │ 2026-09-03 13:07:00 │   78513 │ 78526.1 │ 78503.5 │ 78508.8 │ 0.29410822 │          41 │
```

### `gold.bars`

```sql
SELECT bar_kind, threshold, day, bar_seq, open_e8 / 1e8 AS open, close_e8 / 1e8 AS close,
       trade_count, open_time, close_time
FROM pinned.gold_bars WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD' AND bar_kind = 'dollar'
ORDER BY day DESC, bar_seq DESC LIMIT 3
```

```
bar_kind  threshold        day  bar_seq    open   close  trade_count                        open_time                       close_time
  dollar  6000000.0 2026-09-03        0 78327.9 78562.5         2804 2026-09-03 12:47:04.413735+00:00 2026-09-03 13:06:01.607226+00:00
  dollar  6000000.0 2026-08-28       12 78754.9 79338.6         2053 2026-08-28 14:28:54.318353+00:00 2026-08-28 15:10:22.074647+00:00
  dollar  6000000.0 2026-08-28       11 78537.8 78754.9         2209 2026-08-28 14:15:26.989790+00:00 2026-08-28 14:28:54.317316+00:00
```

`bar_seq` restarts at 0 each UTC day, and the last bar of a day is open-ended until the day
is. For any other threshold use `k2lake.bars(con, "dollar", 1_000_000, symbol="BTC/USD")`
rather than a second table. ClickHouse `gold.bars` is empty until the pull runs.

### `gold.book_top20`

```sql
SELECT second, depth, bid_px_e8[1] / 1e8 AS bid, bid_qty_e8[1] / 1e8 AS bid_qty,
       ask_px_e8[1] / 1e8 AS ask, ask_qty_e8[1] / 1e8 AS ask_qty, checksum_ok
FROM pinned.gold_book_top20 WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
ORDER BY second DESC LIMIT 3
```

```
                   second  depth     bid  bid_qty     ask  ask_qty  checksum_ok
2026-09-03 13:06:03+00:00     20 78562.4 0.692551 78562.5 0.076821         True
2026-09-03 13:06:02+00:00     20 78562.4 0.081816 78562.5 0.274466         True
2026-09-03 13:06:01+00:00     20 78562.4 0.271474 78562.5 0.338266         True
```

ClickHouse, different column names and no `_e8` division built in:

```bash
$CH -q "SELECT exchange, canonical_symbol, second, depth, bid_px[1] AS bid_e8, ask_px[1] AS ask_e8, checksum_ok
        FROM gold.book_top20 FINAL
        WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
        ORDER BY second DESC LIMIT 3 FORMAT PrettyCompactMonoBlock"
```

```
   ┌─exchange─┬─canonical_symbol─┬──────────────second─┬─depth─┬────────bid_e8─┬────────ask_e8─┬─checksum_ok─┐
1. │ kraken   │ BTC/USD          │ 2026-09-03 13:09:36 │    20 │ 7849820000000 │ 7849830000000 │ true        │
2. │ kraken   │ BTC/USD          │ 2026-09-03 13:09:35 │    20 │ 7849820000000 │ 7849830000000 │ true        │
3. │ kraken   │ BTC/USD          │ 2026-09-03 13:09:34 │    20 │ 7849820000000 │ 7849830000000 │ true        │
```

Arrays are 1-based in both engines.

### `gold.bbo_1s`

```sql
SELECT second, bid_e8 / 1e8 AS bid, ask_e8 / 1e8 AS ask, round(mid, 2) AS mid,
       round(spread_bps, 3) AS spread_bps, round(imbalance, 4) AS imbalance
FROM pinned.gold_bbo_1s WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
ORDER BY second DESC LIMIT 3
```

```
                   second     bid     ask      mid  spread_bps  imbalance
2026-09-03 13:06:03+00:00 78562.4 78562.5 78562.45       0.013     0.9002
2026-09-03 13:06:02+00:00 78562.4 78562.5 78562.45       0.013     0.2296
2026-09-03 13:06:01+00:00 78562.4 78562.5 78562.45       0.013     0.4452
```

ClickHouse `gold.bbo_1s` is empty until the pull runs; `gold.bbo_live` is the head, the
same arithmetic over the topic-fed book:

```bash
$CH -q "SELECT exchange, canonical_symbol, second, bid, ask, round(spread_bps,3) AS spread_bps,
               round(imbalance,4) AS imbalance
        FROM gold.bbo_live WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
        ORDER BY second DESC LIMIT 5 FORMAT PrettyCompactMonoBlock"
```

```
   ┌─exchange─┬─canonical_symbol─┬──────────────second─┬─────bid─┬─────ask─┬─spread_bps─┬─imbalance─┐
1. │ kraken   │ BTC/USD          │ 2026-09-03 13:07:28 │   78526 │ 78526.1 │      0.013 │    0.3208 │
2. │ kraken   │ BTC/USD          │ 2026-09-03 13:07:27 │ 78523.1 │ 78523.2 │      0.013 │    0.9835 │
3. │ kraken   │ BTC/USD          │ 2026-09-03 13:07:26 │ 78523.1 │ 78523.2 │      0.013 │    0.9835 │
4. │ kraken   │ BTC/USD          │ 2026-09-03 13:07:25 │ 78523.1 │ 78523.2 │      0.013 │    0.9835 │
5. │ kraken   │ BTC/USD          │ 2026-09-03 13:07:24 │ 78523.1 │ 78523.2 │      0.013 │    0.9809 │
```

### The `+1 SECOND` join, end to end

```sql
SELECT t.exchange_ts, t.price_e8 / 1e8 AS price, q.second, q.bid_e8 / 1e8 AS bid, q.ask_e8 / 1e8 AS ask
FROM (SELECT * FROM pinned.gold_trades WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
      ORDER BY exchange_ts DESC LIMIT 3) t
ASOF JOIN (SELECT * FROM pinned.gold_bbo_1s WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD') q
  ON t.exchange_ts >= q.second + INTERVAL 1 SECOND
ORDER BY t.exchange_ts DESC
```

```
                     exchange_ts   price                    second     bid     ask
2026-09-03 13:06:01.607226+00:00 78562.5 2026-09-03 13:06:00+00:00 78562.4 78562.5
2026-09-03 13:05:54.335566+00:00 78562.4 2026-09-03 13:05:53+00:00 78562.4 78562.5
2026-09-03 13:05:54.335566+00:00 78562.4 2026-09-03 13:05:53+00:00 78562.4 78562.5
```

The 13:06:01 trade pairs with the second labelled 13:06:00 — the book as it stood at
13:06:01.000, the last state that existed before the trade printed.

### `gold.dim_instrument`

```sql
SELECT instrument_id, canonical_symbol, symbol, book_depth, tick_size, source,
       valid_from, valid_to, is_current
FROM pinned.gold_dim_instrument WHERE exchange = 'kraken' AND canonical_symbol = 'BTC/USD'
```

```
                   instrument_id canonical_symbol  symbol  book_depth  tick_size       source                valid_from                  valid_to  is_current
4419ceb35e8a17847f84167c0a7e1b13          BTC/USD BTC/USD          25        0.1 venue:kraken 1970-01-01 00:00:00+00:00 9999-12-31 23:59:59+00:00        True
```

One row today because nothing has changed yet. This is a history table: `WHERE is_current`
for the present slice, `ASOF JOIN … ON t.exchange_ts >= d.valid_from` for the attributes in
force when a trade printed (`notebooks/README.md` § SCD2). `tick_size` and the precisions
are Kraken-only, from `bronze.kraken_instrument`; `source` says which authority supplied
them.

### `gold.dim_venue`

```sql
SELECT venue_id, exchange, book_depth, instruments, source, valid_from, valid_to, is_current
FROM pinned.gold_dim_venue ORDER BY exchange
```

```
                        venue_id exchange  book_depth  instruments   source                valid_from                  valid_to  is_current
59bba357145ca539dcd1ac957abc1ec5  binance          20           12 registry 1970-01-01 00:00:00+00:00 9999-12-31 23:59:59+00:00        True
f80f21938e5248ec70b870ac1103d0dd coinbase           0           11 registry 1970-01-01 00:00:00+00:00 9999-12-31 23:59:59+00:00        True
686d22d695e2c21166a89498a3a3f198   kraken          25           11 registry 1970-01-01 00:00:00+00:00 9999-12-31 23:59:59+00:00        True
```

`book_depth = 0` for Coinbase means the venue sends the whole book and has no depth
parameter.

### `audit.checks`

```sql
SELECT run_ts, job, check_name, scope, passed, observed
FROM pinned.audit_checks ORDER BY run_ts DESC LIMIT 4
```

```
                          run_ts    job check_name                          scope  passed  observed
2026-09-03 12:58:25.158640+00:00 ingest offset_gap market.crypto.v3.raw.binance/0   False     25784
2026-09-03 12:58:25.158640+00:00 ingest offset_gap market.crypto.v3.raw.binance/2   False     14923
2026-09-03 12:58:25.158640+00:00 ingest offset_gap market.crypto.v3.raw.binance/3   False     24479
2026-09-03 12:58:25.158640+00:00 ingest offset_gap market.crypto.v3.raw.binance/4   False     10243
```

`offset_gap` rows are written by `ingest.py` **before** the skip they license: these record
25,784 Binance records that Redpanda evicted before the lake read them, on partition 0 of
`market.crypto.v3.raw.binance`. The archive knows exactly what it is missing.

## Related

- [data-inspection.md](./data-inspection.md), the ops cheat sheet: health queries per hop
- [clickhouse-rebuild-from-lake.md](../runbooks/clickhouse-rebuild-from-lake.md), the pull
  that fills the empty ClickHouse tables
- [`notebooks/README.md`](../../notebooks/README.md), the research surface and pinned reads
- [../architecture/13-schema-design.md](../architecture/13-schema-design.md), why the layers
  have the shape they do
