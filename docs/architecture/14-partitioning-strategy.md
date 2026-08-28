# 14. Partitioning Strategy

> **You will learn** partition specs, sort orders, file sizes and ClickHouse keys, with the arithmetic.
> **Read this if** anyone whose query is slow or whose table is growing.
> **Before this** chapter 09, 10.

Two tiers partition data in v3, and they solve different problems. **Redpanda partitions
buy producer and consumer parallelism, and decide what stays ordered.** **Iceberg
partitions control file count and prune scans; they evict nothing, because nothing in the
lake is ever evicted** ([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)). The
third tier, ClickHouse, serves the lake's gold layer; see [ClickHouse](#clickhouse) below.

What follows is what the DDL and the topic bootstrap actually configure:
[`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql) and
[`docker/redpanda/init.sh`](../../docker/redpanda/init.sh).

```mermaid
flowchart TB
  CAP["k2-capture ×3<br/>key = canonical symbol"]
  RP[("Redpanda · 9 v3 topics<br/>12 partitions each")]
  RAW[("raw.messages<br/>days(kafka_ts), topic")]
  BR[("bronze.&lt;venue&gt;_&lt;msg&gt;<br/>days(recv_ts) · sorted by symbol")]
  SV[("silver.trades_&lt;venue&gt; · silver.book_&lt;venue&gt;<br/>days(exchange_ts) · days(recv_ts)")]
  GD[("gold.trades · ohlcv_* · book_top20 · bbo_1s<br/>exchange + days/months")]
  Q["DuckDB · ClickHouse<br/>prune: partition then sort order"]
  CAP --> RP --> RAW --> BR --> SV --> GD --> Q
```

---

## Redpanda

Nine v3 topics, **12 partitions each**, `market.crypto.v3.{raw,trades,book}.<exchange>`
for Binance, Kraken and Coinbase. Verified on the running stack, 2026-08-26:

```console
$ docker exec k2-redpanda rpk topic list
NAME                               PARTITIONS  REPLICAS
market.crypto.v3.book.binance      12          1
market.crypto.v3.raw.binance       12          1
market.crypto.v3.trades.binance    12          1
…
```

The six v2 topics (`market.crypto.trades.<ex>{,.raw}`, 40/20/20 partitions) were deleted at
the Phase E cutover on 2026-08-27; the cluster now carries the nine v3 topics only, 108
partitions. Until then the count was `9 × 12 = 108` for v3 plus `2 × (40 + 20 + 20)` for v2.

### The key is the symbol, not the exchange

Trades and book snapshots are keyed on the **canonical symbol** (`BTC/USD`); raw frames
are keyed on the **venue-native symbol** (`BTCUSDT`, `XBT/USD`, `BTC-USD`), because the
raw archive must not carry a normalisation decision, `raw-message.avsc`'s `symbol` field
says so explicitly, and its value is read out of the payload. Frames that belong to no
single instrument, heartbeats, subscription acknowledgements, error envelopes, carry no
key at all and spread round-robin, which is correct: they have no ordering relationship
to anything (`services/capture-rust/src/record.rs:163`).

Keying on the symbol means every record for one instrument lands on one partition and
stays in the order the venue sent it. Ordering *across* instruments is not preserved and
is not needed: every downstream aggregation groups by symbol, and cross-instrument
ordering would in any case be a claim about three separate WebSocket connections that no
single clock can back.

**Why not key by exchange, because v2 did, and it cost two thirds of the fan-out.** The
v2 raw producer passes the exchange name as the key:

```kotlin
// legacy/v2-kotlin/src/main/kotlin/com/k2/feedhandler/KafkaProducerService.kt:155
val record = ProducerRecord(topic, exchange, json)
```

One key value per topic hashes to one partition, so `market.crypto.trades.kraken.raw` and
`market.crypto.trades.coinbase.raw` have 20 partitions each and use **one**. The
partition count reads like headroom and is not, the topic is single-threaded end to end,
and no consumer group can ever parallelise past one member. This is on
[ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md)'s list of eight things v2 got
wrong, and it is the cheapest of them to fix: change the key.

**Why not one partition per symbol** (34 instruments → 34 partitions). It buys nothing
that 12 does not: ordering is already per-symbol, because the hash puts each symbol on
exactly one partition. What it costs is real, partition count is the one Kafka-family
setting that cannot be reduced without recreating the topic, every partition is a set of
files and an entry in every metadata exchange, and each additional consumer-group member
is a rebalance participant. 12 is chosen as headroom over the six-ish concurrent readers
this stack will plausibly want, at a metadata cost a single broker with `--smp 1` does not
notice.

**Skew is real and is accepted here.** BTC-quoted majors dominate volume, so the
partitions holding them are hotter than the tail. At the predicted ~700 frames/s in
([capacity model §2c](15-capacity-model.md#2c-raw-frames-in-and-records-out)) against a
broker sized for ~100 K msg/s ([ADR-010](../adr/ADR-010-resource-budget.md)'s "2 cores
handles 100K msg/s", the figure rank 6 of [capacity model
§7](15-capacity-model.md#7-bottleneck-prediction) uses to put broker CPU at ~113× today's
rate), per-partition skew is not near anything that binds. The fix if it ever binds, more
partitions, is available only at topic-recreation cost, so it is a decision to make once
with headroom rather than to tune.

### Retention is not partitioning, but it interacts

`raw.*` topics carry 48 h *and* a 512 MiB-per-partition byte cap (`12 × 3 × 512 MiB ≈
18 GiB`, inside a 20 GB budget); `trades.*` and `book.*` carry 7 d and no byte cap. The
arithmetic and the reasoning are in `docker/redpanda/init.sh`. **The byte cap binds
first**: measured 7.0 h on `raw.kraken/0` on 2026-08-26
([capacity model §4d](15-capacity-model.md#4d-retention--disk)), not 48 h, because keyed
partitions are not evenly loaded. It matters to partitioning because the byte cap is
**per partition**: raising the partition count raises the disk floor proportionally.

---

## Iceberg

Twenty-five tables across five namespaces, `raw`, `bronze`, `silver`, `gold`, `audit`.
Applied by `docker/lake/apply_ddl.py` from
[`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql).

| Table | `PARTITIONED BY` | Local sort order | Target file size | Metrics on |
|---|---|---|---|---|
| `raw.messages` | `days(kafka_ts), topic` | `topic, partition, offset` | 256 MB | `offset`, `kafka_ts`, `partition` |
| `bronze.<venue>_<msg>` ×7 | `days(recv_ts)` | `symbol, recv_ts_ns` | 128 MB | `symbol`, `recv_ts`, `src_offset` |
| `silver.trades_<venue>` ×3 | `days(exchange_ts)` | `canonical_symbol, exchange_ts` | 128 MB | `canonical_symbol`, `exchange_ts`, `recv_ts`, `trade_seq`, `src_offset` |
| `silver.book_<venue>` ×3 | `days(recv_ts)` | `canonical_symbol, recv_ts_ns` | 128 MB | `canonical_symbol`, `recv_ts`, `src_offset` |
| `gold.trades` | `exchange, days(exchange_ts)` | `canonical_symbol, exchange_ts` | 128 MB | `canonical_symbol`, `exchange_ts`, `trade_seq`, `src_offset` |
| `gold.ohlcv_{1m,5m,1h}` | `exchange, months(window_start)` | none | 128 MB | `canonical_symbol`, `window_start` |
| `gold.ohlcv_1d` | `exchange` | none | 128 MB | `canonical_symbol`, `window_start` |
| `gold.bars` | `exchange, months(open_time)` | none | 128 MB | `canonical_symbol`, `open_time` |
| `gold.book_top20` | `exchange, days(second)` | `canonical_symbol, second` | 128 MB | `canonical_symbol`, `second` |
| `gold.bbo_1s` | `exchange, days(second)` | none | 128 MB | `canonical_symbol`, `second` |
| `gold.dim_instrument`, `gold.dim_venue`, `gold.book_state` | `exchange` | none | 128 MB | none |
| `audit.checks` | `days(run_ts)` | none | 128 MB | default |

`gold.bars` partitions on `months(open_time)` rather than on its `day` column because
DuckDB 1.4.4's Iceberg reader returned zero rows for any predicate on a `DATE` partition
source (`day = DATE '2026-08-26'` → 0 of 4,463 on 2026-08-28) while the same predicate on a
`TIMESTAMP` source prunes correctly; `day` stays a plain column and filters on it work.

The seven bronze tables are `binance_trade`, `binance_depth20`, `kraken_trade`,
`kraken_book`, `kraken_instrument`, `coinbase_market_trades`, `coinbase_level2`.
`gold.ohlcv_1d` drops the time field from its spec because a whole year of daily candles
for 34 instruments is one small file per venue; a monthly transform on it would produce
twelve.

Every table: Parquet + zstd, `format-version = 2`, `write.distribution-mode = hash`,
copy-on-write for delete/update/merge. Every table but `audit.checks` also sets
`write.metadata.metrics.default = none` and re-enables metrics per column, as the last
table column above records. `audit.checks` is the exception: its DDL sets no metrics
property at all, so it keeps Iceberg's `truncate(16)` default on every column, a table
of a few rows a night whose columns are all short strings pays nothing for it, and there
is no `payload`-shaped column to make the default expensive. Every `gold` table also sets
`write.metadata.compression-codec = none`, because ClickHouse 24.3's `iceberg()` cannot
read the gzip-compressed `metadata.json` Lakekeeper writes by default, and `gold` is the
layer ClickHouse pulls.

### `raw.messages`: time first, topic second

`days(kafka_ts)` leads because every access pattern the archive has is time-bounded, a
backfill, a replay window, a completeness audit for a day, an incremental read between two
snapshots. `topic` follows because a per-venue or per-stream read then prunes to a third
or a ninth of the files without touching the time bound.

The **sort order carries the offset range**. Files are locally sorted by `(topic,
partition, offset)`, so per-file min/max bounds on `offset` are tight, and the offset
continuity audit, which asks whether consecutive ingest snapshots' ranges abut per
`(topic, partition)`, including across the day seam
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)), opens a handful of
files instead of scanning a day. The DDL writes `WRITE DISTRIBUTED BY PARTITION LOCALLY
ORDERED BY …` rather than a bare `WRITE ORDERED BY`, because the bare form silently
switches `write.distribution-mode` to `range` and buys a sampling job on every write.

**Metrics are off by default and on for three columns.** Iceberg's default is
`truncate(16)` on every column, which for `payload` means per-file bounds over frames up
to 5.2 MB (Coinbase's `level2` subscribe snapshot, spike S5) that no query will ever
filter on. Turning metrics on only for `offset`, `kafka_ts` and `partition` is a
statement about what prunes: the archive prunes on coordinates, never on anything inside
the frame. That is a direct consequence of `payload` being opaque bytes
([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)).

### `bronze.*` and `silver.*`: no exchange field, and which clock the day comes from

Bronze is one table per venue × message, so `exchange` is the table name and the partition
is `days(recv_ts)`, the one clock every frame carries: Binance's depth stream has no venue
timestamp at all ([ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md)), and a
nullable column cannot carry a partition. Silver trades partition on `days(exchange_ts)`
because every venue stamps a trade, and that is the axis research reads on; silver books
keep `days(recv_ts)` for the same reason bronze does. `gold` is the first layer where
`exchange` becomes a partition field, because gold is the first layer that is
cross-venue: one table, three values, an exact two-thirds file skip for 3× the partition
count.

The cost is stated where it lands: an as-of join from book snapshots to trades crosses
two different clocks, and any query doing it must say which.

### Why symbol is a sort key and not a partition field

This is the load-bearing partitioning decision in the lake, so the argument is worth
having in full.

**Partitioning by symbol would produce skew by construction.** The registry holds **34
(exchange, symbol) pairs**, binance 12, kraken 11, coinbase 11, 23 distinct canonical
symbols across them ([`config/instruments.yaml`](../../config/instruments.yaml)), and the
tables are already one per venue, so adding `symbol` splits a venue-day into 11 or 12
partitions. The three `silver.trades_<venue>` tables write **0.25 GB/day between them**
and the three `silver.book_<venue>` tables **2.1 GB/day**
([capacity model §4c](15-capacity-model.md#4c-per-lake-table-per-day)). Split evenly that
is `0.25 ÷ 3 ÷ 11 ≈ 7.6 MB` per symbol per day on trades and `2.1 ÷ 3 ÷ 11 ≈ 64 MB` on
books, both under the 128 MB target and trades an order of magnitude under it. And it
would not split evenly: BTC-quoted majors would hold most of it;
the tail would hold partitions of a few hundred rows each. Those are files too small to
be worth opening, a metadata tree larger than the data it indexes,
and a compaction job that can never reach its 128 MB target because there is not 128 MB
in the partition to reach it with.

**The sort order does the same job at no file-count cost.** Files are locally sorted by
`(symbol, …)`, so each file's `symbol` min/max bounds cover a narrow, contiguous slice of
the alphabet. A single-instrument scan skips every file whose bounds exclude it, and
Parquet's row-group statistics narrow it again inside the files it does open. Metrics are
explicitly enabled on `symbol` (bronze) and `canonical_symbol` (silver, gold) for exactly
this reason.

**The honest difference: partition pruning is exact, sort-order pruning is statistical.**
A partition filter is evaluated on metadata and is guaranteed; a sort-order skip depends
on how tightly the writer clustered the data, and it degrades if compaction falls behind
long enough for unsorted small files to accumulate. That failure shows up as *slow
queries*, not as an error, which is why the daily maintenance job sort-rewrites the last
two days of `bronze.*` rather than only binpacking them.

**And it is reversible.** Iceberg supports partition evolution: `ALTER TABLE …
ADD PARTITION FIELD symbol` applies to new data without rewriting old data. If
single-symbol queries open most of a day partition, that is the answer, and it costs a DDL
statement rather than a migration.

### Why not `hours()`

Considered for `raw.messages`, which is the only table with the volume to argue for it.
At the predicted 6.5 GB/day across 9 topics, `hours(kafka_ts), topic` is ~216 partitions
a day, ~79,000 a year, on a single host holding the catalog metadata for all of it. What
it would buy is a tighter time prune, and the day partition plus the `(topic, partition,
offset)` sort order already narrows an intraday read to a handful of files. Hourly is
metadata for a pruning gain that is already paid for. The arithmetic is redone in
[`17-scale-out-path.md`](17-scale-out-path.md) §5.3, where it comes out differently: an hourly
partition reaches one 256 MB target file at **8.5×** today's rate, and at the **200×** PB
case it is the right spec.

### File size: 256 MB for raw, 128 MB everywhere else

`raw.messages` targets 256 MB because it is the high-volume table, 6.47 GB/day predicted
of the 13.6 GB/day all lake tables write between them
([capacity model §4c](15-capacity-model.md#4c-per-lake-table-per-day)), so **raw is 48 %
of predicted lake growth, 62 % measured** (3.77 GB of 6.09 GB on 2026-08-27,
[B4](../benchmarks/2026-08-27.md#b4)). Larger files mean fewer manifest entries per
snapshot and fewer object-store round trips per scan. Every other table targets 128 MB
because at a few hundred MB/day per table a 256 MB target would never be reached and
compaction would produce one undersized file per partition either way, with no benefit.

Both are targets, not guarantees. The ingest runs every 5 minutes, so each cycle writes
small files into the current day's partition, ~288 commits per day per table before
maintenance. Nightly compaction is what converges them
([ADR-017](../adr/ADR-017-iceberg-maintenance-pipeline.md)'s compact → expire → audit
ordering, carried into `docker/lake/maintenance.py`). Without it, small files accumulate
and both partition pruning and sort-order pruning get slower without getting wrong.

### What is not partitioned by, anywhere

- **Not by symbol.** Skew, above.
- **Not by hour.** File and manifest count, above.
- **Not by `conn_id`.** It is high-cardinality and unbounded, a new value on every
  reconnect, and it is a join key, not a filter.
- **Not by `schema_id`.** It changes only when a contract evolves, which is rare by
  design ([ADR-020](../adr/ADR-020-avro-fixed-point-contracts.md)); as a partition field
  it would be a near-constant that occasionally splits a day in two.

---

## ClickHouse

**Rewritten at the Phase E cutover, 2026-08-27.** The v2 hot tier (`k2.*`, 7-day TTL,
`SummingMergeTree` candles) is gone, [legacy/v2-clickhouse/](../../legacy/v2-clickhouse/README.md)
keeps its DDL, and `git log -p --follow -- docs/architecture/14-partitioning-strategy.md` this page's
earlier description. What serves now is the `gold` database of
[ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md):
[`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql).
No table has a TTL.

| Table | Engine | `PARTITION BY` | `ORDER BY` | Why |
|---|---|---|---|---|
| `gold.trades` | `ReplacingMergeTree(first_seen)` | `toYYYYMM(exchange_ts)` | `(exchange, canonical_symbol, exchange_ts, trade_id)` | the logical trade is the key; a venue replay or a feed/reload overlap collapses under `FINAL` to the **earliest delivery** (`first_seen` = inverted receive time). Monthly partitions keep `FINAL` a per-partition merge (the `quant` profile sets `do_not_merge_across_partitions_select_final`) and keep part counts flat at ~10 M rows/day |
| `gold.book_top20` | `ReplacingMergeTree(ver)` | `toYYYYMM(second)` | `(exchange, canonical_symbol, second)` | one row per venue-symbol-second; the later sample in a second wins, and a lake reload (state at the end of the second, `ver` = last nanosecond) out-ranks the feed's mid-second sample |
| `gold.ohlcv_{1m,5m,1h,1d}` | `ReplacingMergeTree(computed_at)` | `toYYYYMM(window_start)` | `(exchange, canonical_symbol, window_start)` | loaded from the lake, never computed here; a re-pull of a recomputed bucket replaces the row. The version is the lake run's wall clock, not `src_snapshot_id`: an Iceberg snapshot id is random, so keying the merge on it keeps an arbitrary row |
| `gold.bbo_1s` | `ReplacingMergeTree(src_snapshot_id)` | `toYYYYMM(second)` | `(exchange, canonical_symbol, second)` | loaded from the lake; the one table still versioned on the snapshot id, because `lake.gold.bbo_1s` carries no `computed_at` to use |
| `gold.bars` | `ReplacingMergeTree(computed_at)` | `toYYYYMM(day)` | `(exchange, canonical_symbol, bar_kind, day, bar_seq)` | loaded from the lake; a touched day arrives whole with a newer `computed_at` and replaces its rows. Raising a threshold makes the day's rows fewer, which a pull cannot express: `TRUNCATE` + re-pull (runbook §3) |
| `gold.feed_errors` | `MergeTree` | | `(topic, partition, offset)` | every record AvroConfluent could not decode, with its bytes |

**Why not partition by exchange, as the lake does.** ClickHouse's partition is a merge
boundary and a `FINAL` boundary, not a pruning device the way an Iceberg partition is;
`exchange` leads the `ORDER BY` instead, so a per-venue scan is a primary-key range and the
sparse index prunes it. Three venues in one monthly partition merge as one set of parts;
three per-venue partitions would triple the part count for the same rows.

**Why the sort key carries `exchange_ts` before `trade_id`.** A backtest reads a symbol's
trades in time order; the key makes that a sequential read of one primary-key range, and
`ohlcv_live(bucket)` groups on `toStartOfInterval(exchange_ts, …)` over the same order. The
trade id is last because it only has to make the key unique, and the venues number trades
sequentially per symbol so ties in `exchange_ts` resolve in venue order.

**OHLCV on read.** `gold.ohlcv_live(bucket = <seconds>)` is a parameterised view over
`gold.trades FINAL`; the materialised candles are the lake's and are loaded. The v2 tier
materialised candles into a `SummingMergeTree` whose open/close were resolved per insert
block, which is the wrong number when a minute spans two blocks; `make test-clickhouse`
asserts the view gets that minute right on every PR.

---

## Verifying

```bash
# Redpanda: partition counts, and whether the fan-out is actually being used.
docker exec k2-redpanda rpk topic list
docker exec k2-redpanda rpk topic describe -p market.crypto.v3.raw.binance
```

```sql
-- Iceberg: files, sizes and record counts per partition. Run in k2-spark-iceberg.
SELECT partition, count(*) AS files,
       sum(record_count) AS rows,
       round(avg(file_size_in_bytes) / 1048576, 1) AS avg_mb
FROM lake.raw.messages.files
GROUP BY partition ORDER BY partition;
```

```sql
-- Is the sort order doing its job? Tight symbol bounds per file mean a
-- single-symbol scan prunes. Wide bounds everywhere mean compaction is behind.
SELECT file_path, lower_bounds, upper_bounds
FROM lake.bronze.binance_trade.files LIMIT 20;
```

Rules of thumb used here, and what each one means if it trips:

| Signal | Threshold | What it says |
|---|---|---|
| Average file size in a settled partition | < 10 MB | compaction is behind, or the partition spec is too fine |
| Files per day partition, `raw.messages`, after maintenance | > ~50 | the 256 MB target is not being reached, check the compaction job ran |
| Manifest count per snapshot | > ~100 | planning time is growing; rewrite manifests |
| Single-symbol scan opening > 10 % of a day partition's files | | sort-order pruning is not working; `ADD PARTITION FIELD symbol` becomes the answer ([ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md)) |

Scored once, 2026-08-27 ([B4](../benchmarks/2026-08-27.md#b4)): `raw.messages` **278 files
for 3.77 GB**; per-venue bronze **2 to 12 files each**. The thresholds themselves are still
design expectations.

---

## Related

- [ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md), why `raw.messages` is never evicted, and why it prunes on coordinates only
- [ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md), the offset continuity the raw sort order exists to serve
- [ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md), the unified bronze decision and the symbol-in-sort-order trade-off
- [ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md), the ClickHouse tier serves gold, indefinitely, from the lake
- [ADR-027](../adr/ADR-027-book-snapshot-and-sequencing.md), why the book table's time axis is the sampler clock
- [capacity-model.md](15-capacity-model.md), the bytes/day predictions every file-size argument above rests on
- [scale-out-path.md](17-scale-out-path.md), the same partition arithmetic redone at PB scale
- [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql), the DDL this page describes, with per-column commentary
