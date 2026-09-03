# Runbook: Rebuild ClickHouse gold from the lake

ClickHouse's `gold` database is derived (ADR-026): the lake's `gold.*` tables are the
record, the Avro topics are the head start. This is how the served tier is reloaded , 
after a volume loss, a bad reload, or simply to make it agree with the lake, and how
the lake-computed candles get in at all.

| Situation | Do |
|---|---|
| ClickHouse volume lost / `gold` dropped | §1 schema, §2 trades + book, §3 candles, §4 verify |
| Candles missing or stale in ClickHouse | §3 only |
| ClickHouse and lake disagree on a window | §4 first, it tells you which side to believe (the lake), then §2 for that window |

**One rule.** Read the lake through `iceberg()` only ([lake-recovery.md, the one rule](./lake-recovery.md#the-one-rule-on-this-page)).
Two settings make it work on 24.3, both explained in [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md):
the gold tables are written with `write.metadata.compression-codec = none` (24.3 cannot
parse the gzip metadata Lakekeeper writes by default), and every read passes
`SETTINGS iceberg_engine_ignore_schema_evolution = 1` (the `SET IDENTIFIER FIELDS` in
`lake.sql` counts as a schema change to 24.3; no column has ever changed, so the latest
schema is the right one).

## 0. Find the table locations

MinIO paths are keyed on the table UUID, not the name. Ask the catalog:

```bash
set -a && . ./.env && set +a
P=$(curl -s "localhost:18181/catalog/v1/config?warehouse=k2" | python3 -c "import sys,json; print(json.load(sys.stdin)['defaults']['prefix'])")
loc() { curl -s "localhost:18181/catalog/v1/$P/namespaces/gold/tables/$1" | python3 -c "import sys,json; print(json.load(sys.stdin)['metadata']['location'])"; }
for t in trades ohlcv_1m ohlcv_5m ohlcv_1h ohlcv_1d; do echo "$t $(loc $t)"; done
# trades s3://k2-lake/warehouse/k2/<uuid>   -> use http://minio:9000/k2-lake/warehouse/k2/<uuid>/ in iceberg()
```

A rebuild of a lake layer (`make lake-rebuild LAYER=gold`) drops and recreates the
tables, so the UUIDs change; always look them up, never paste from an old run.

## 1. Schema

The entrypoint applies `docker/clickhouse/ddl/*.sql` only on a fresh volume; on a live
server apply by hand:

```bash
docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --multiquery < docker/clickhouse/ddl/10-gold-tables.sql
docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --multiquery < docker/clickhouse/ddl/20-gold-kafka.sql
```

The Kafka feeds resume from the consumer groups' committed offsets (or the topics'
retention on a brand-new group) and start filling `gold.trades` / `gold.book_top20`
immediately. That is the head; the body comes from the lake.

## 2. Trades from lake gold

`gold.trades` is `ReplacingMergeTree` on the logical key with the earliest delivery
winning, so loading a window the feeds already covered is idempotent: the reload and the
feed converge on the same row.

```sql
INSERT INTO gold.trades
    (exchange, symbol, canonical_symbol, trade_id, price_e8, qty_e8, side, exchange_ts,
     recv_ts_ns, seq, conn_id, conn_msg_seq, src_topic, src_partition, src_offset, first_seen)
SELECT exchange, symbol, canonical_symbol, trade_id, price_e8, qty_e8, side, exchange_ts,
       recv_ts_ns, 0, conn_id, conn_msg_seq, src_topic, src_partition, src_offset,
       18446744073709551615 - toUInt64(recv_ts_ns)
FROM iceberg('http://minio:9000/k2-lake/warehouse/k2/<trades-uuid>/', '<MINIO_ROOT_USER>', '<MINIO_ROOT_PASSWORD>')
WHERE exchange_ts >= '<from>' AND exchange_ts < '<to>'
SETTINGS iceberg_engine_ignore_schema_evolution = 1;
```

`seq` is 0 from the lake: gold.trades in the lake does not carry the venue sequence
(silver does), and nothing in ClickHouse reads it.

The book snapshots come from `lake.gold.book_top20`, replayed from every venue frame, not
the capture's 1 Hz sampler. The four level arrays carry the same 1e-8 fixed-point values
under different names — lake `bid_px_e8`/`bid_qty_e8`/`ask_px_e8`/`ask_qty_e8`, ClickHouse
`bid_px`/`bid_qty`/`ask_px`/`ask_qty` — so the column lists below must stay aligned by
hand ([data-catalog.md](../operations/data-catalog.md#column-name-divergences)):

```sql
INSERT INTO gold.book_top20
    (exchange, symbol, canonical_symbol, depth, seq, checksum_ok, bid_px, bid_qty, ask_px, ask_qty,
     exchange_ts, recv_ts_ns, snapshot_ts_ns, snapshot_ts, second, conn_id, conn_msg_seq,
     src_topic, src_partition, src_offset, ver)
SELECT exchange, symbol, canonical_symbol, depth, seq, checksum_ok, bid_px_e8, bid_qty_e8, ask_px_e8, ask_qty_e8,
       NULL, recv_ts_ns, toUnixTimestamp(second) * 1000000000 + 999999999, second, second, conn_id, conn_msg_seq,
       src_topic, src_partition, src_offset, toUInt64(toUnixTimestamp(second)) * 1000000000 + 999999999
FROM iceberg('http://minio:9000/k2-lake/warehouse/k2/<book_top20-uuid>/', '<MINIO_ROOT_USER>', '<MINIO_ROOT_PASSWORD>')
WHERE second >= '<from>' AND second < '<to>'
SETTINGS iceberg_engine_ignore_schema_evolution = 1;
```

The lake row is the state at the END of its second, so its version (`ver`, the sampler
clock) is set to the last nanosecond of that second: it out-ranks any feed sample taken
inside the second, which is the intended "lake wins" for a fully replayed book.

**Measured 2026-08-27:** 1,951,135 lake book-seconds pulled into a scratch table in
**2.914 s**; `gold.bbo_1s` (1,951,129 rows) in 0.985 s. Against the feed's own samples
over 2026-08-27 00:00–05:00 the top-of-book prices agree in 88.6 % (Binance) / 66.8 %
(Kraken) / 65.9 % (Coinbase) of seconds, the two are sampled at different instants inside
the second, so this is not a tolerance-zero check; the Kraken checksum in
`silver.book_kraken` is.

**Measured 2026-08-27** (first run, `clickhouse-client --time`): the whole lake
`gold.trades`, 10,410,270 rows, no `WHERE`, into a scratch copy of `gold.trades` in
**4.410 s**; `count()` over the same `iceberg()` source 0.012 s. `FINAL` count equalled the
delivery count (10,410,270), the lake's rows are one per logical trade already, so the
reload creates no duplicates for `ReplacingMergeTree` to collapse.

## 3. Candles from lake gold

The candle tables in ClickHouse are loaded, never computed (`gold.ohlcv_live` is the
on-read view for the head). `ReplacingMergeTree(computed_at)`: a re-pull of a bucket the
lake recomputed replaces the older row. **`computed_at` must be in the SELECT**: it is
the version column. It is not `src_snapshot_id`, which is a random 64-bit Iceberg id and
would have a re-pull keep an arbitrary one of the two rows; that column is lineage only.
`gold.bbo_1s` is the exception, still versioned on `src_snapshot_id` because
`lake.gold.bbo_1s` has no `computed_at` to use.

```sql
INSERT INTO gold.ohlcv_1m
SELECT exchange, canonical_symbol, window_start, open_e8, high_e8, low_e8, close_e8,
       volume, quote_volume, trade_count, open_time, close_time, src_snapshot_id, computed_at
FROM iceberg('http://minio:9000/k2-lake/warehouse/k2/<ohlcv_1m-uuid>/', '<MINIO_ROOT_USER>', '<MINIO_ROOT_PASSWORD>')
SETTINGS iceberg_engine_ignore_schema_evolution = 1;
-- same for ohlcv_5m, ohlcv_1h, ohlcv_1d; and gold.bbo_1s from lake.gold.bbo_1s (same column
--   names, no computed_at there)
-- gold.bars from lake.gold.bars: exchange, canonical_symbol, bar_kind, threshold, day, bar_seq,
--   open_e8, high_e8, low_e8, close_e8, volume_e8, quote_volume_e8, trade_count, open_time,
--   close_time, src_snapshot_id, computed_at
```

**After a threshold change in `config/bars.yaml`, TRUNCATE first.** Raising a threshold
makes a day's bars fewer, and a pull cannot express a deletion: the orphaned rows at the
high `bar_seq` end of each day have no newer row sharing their key, so nothing collapses
them and the table serves bars from two different thresholds.

```sql
TRUNCATE TABLE gold.bars;   -- then the INSERT above, whole table, no WHERE
```

**Measured 2026-08-28:** `gold.bars` 5,796 rows in 0.025 s (a fresh table after the DDL was
applied by hand with `clickhouse-client --multiquery`; a running ClickHouse does not re-read
`10-gold-tables.sql`).

**Measured 2026-08-27:** `ohlcv_1m` 31,324 rows in 0.030 s; `5m` / `1h` / `1d` each
≤ 0.013 s. `gold.ohlcv_1m` then spanned 2026-08-26 12:04 → 2026-08-27 05:15 UTC.

## 4. Verify: the lake wins

```bash
scripts/parity-ohlcv.sh --pin-current <YYYY-MM-DD>
```

Three computations of the same day's 1-minute candles, ClickHouse `gold.ohlcv_live`
over the (feed + reload) trades, the lake's `gold.ohlcv_1m`, DuckDB over silver with the
dedup in the query, compared at tolerance zero on open/high/low/close/count/volume at
pinned snapshots. A bucket where ClickHouse differs from the lake is a bucket the feed
missed or duplicated; §2 for that window makes them converge. A bucket where the lake
differs from DuckDB-over-silver is a lake bug: `make lake-rebuild LAYER=gold` and read
`docs/runbooks/lake-audit-failed.md` §12.

**Measured 2026-08-27** for day `2026-08-27` at `lake.gold.ohlcv_1m` snapshot
`1622213366608023449` and the silver snapshots recorded on `gold.trades`
(`tests/parity/pinned.json`): **9,866 buckets, 0 differ** on both comparisons , 
`lake.gold.ohlcv_1m` vs DuckDB-over-silver, and ClickHouse `gold.ohlcv_live` vs the lake.
The first run failed on all 29,407 buckets against ClickHouse and 3,829 against DuckDB;
both causes are recorded in `scripts/parity_ohlcv.py` (DuckDB's session time zone) and
`docker/lake/gold.py` (the open/close tie-break), and both fixes are what make the number
above reproducible.

## Revisit when

- ClickHouse is upgraded past 24.3: `iceberg_engine_ignore_schema_evolution` and the
  metadata codec workaround should both become unnecessary; test, then delete them here
  and in `lake.sql`.
- A lake `gold.book_top20` exists: add it to §2.
