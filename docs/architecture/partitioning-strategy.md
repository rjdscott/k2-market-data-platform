# Partitioning Strategy

Three layers of partitioning, each solving a different problem: Redpanda partitions for producer parallelism, ClickHouse partitions for TTL eviction and pruning, Iceberg partitions for file-count control on the cold tier. This is what is actually configured — the DDL is in `docker/clickhouse/schema/` and `docker/iceberg/ddl/`.

## Redpanda

| Topic pair | Partitions each | Why |
|---|---|---|
| `market.crypto.trades.binance{,.raw}` | 40 | Binance is ~100–200 trades/s across 12 pairs — an order of magnitude above the others |
| `market.crypto.trades.kraken{,.raw}` | 20 | ~1–5 trades/s across 11 pairs |
| `market.crypto.trades.coinbase{,.raw}` | 20 | Similar order to Kraken |

Six topics, 160 partitions, created explicitly by the `redpanda-init` service so counts do not drift with auto-create defaults.

These counts are provisioned for headroom, not for current load — a single ClickHouse Kafka-engine consumer (`kafka_num_consumers = 1`) keeps up with all of them today. Partition count is the one Kafka-family setting that cannot be reduced later without recreating the topic, so it is set for the volume this would handle, not the volume it handles.

Keying is by symbol, so all trades for an instrument land on one partition and stay in exchange order. Ordering across instruments is not preserved and is not needed — every downstream aggregation groups by symbol.

## ClickHouse

The hot tier partitions by date and sorts by the access path. Partitions here do double duty: `TTL` drops whole partitions instead of rewriting parts, which is why the partition granularity matches the retention granularity.

| Table | `PARTITION BY` | `ORDER BY` | `TTL` |
|---|---|---|---|
| `bronze_trades_{binance,kraken,coinbase}` | `toYYYYMMDD(exchange_timestamp)` | `(symbol, exchange_timestamp, sequence_number)` | 7 days |
| `silver_trades` | `(exchange, asset_class, toYYYYMMDD(timestamp))` | `(exchange, asset_class, canonical_symbol, timestamp)` | 30 days |
| `ohlcv_{1m,5m,15m,30m,1h,1d}` | `(exchange, toYYYYMM(window_start))` | `(exchange, canonical_symbol, window_start)` | 1–2 years |

Three things this encodes:

- **Bronze is per-exchange and partitioned by day only.** It is never queried across exchanges — that is Silver's job — so `exchange` in the partition key would be a constant. Daily partitions plus a 7-day TTL means bronze holds at most 7 parts per table.
- **Silver adds `exchange` and `asset_class` to the partition key** because it is the unified table and almost every query filters to one venue. `asset_class` is a constant (`crypto`) today — it is in the key so that adding equities or futures later isolates them without a partition rewrite.
- **Gold drops to monthly partitions.** A 1-minute candle table produces ~1,440 rows per symbol per day; daily partitions would create thousands of tiny parts for no pruning benefit. Monthly partitions with a year-plus TTL keep the part count in the low hundreds.

**Gotcha, hit for real:** ClickHouse TTL expressions require `DateTime`/`Date`, not `DateTime64`. Every timestamp column here is `DateTime64`, so the live TTLs are written `TTL toDateTime(timestamp) + INTERVAL 30 DAY`. Without the cast the `CREATE TABLE` fails outright — the uncast versions in the older `docker/clickhouse/schema/` files are superseded by the `-fixed` ones for exactly this reason.

## Iceberg

Cold-tier partitioning solves a different problem — not eviction (nothing is evicted) but keeping the file count and the metadata tree small enough that planning stays fast.

| Table | `PARTITIONED BY` |
|---|---|
| `cold.bronze_trades_{binance,kraken,coinbase}` | `days(exchange_timestamp)` |
| `cold.silver_trades` | `days(timestamp), exchange, asset_class` |
| `cold.gold_ohlcv_*` (6 tables) | `months(window_start), exchange` |

All ten tables: Parquet, zstd level 3, `write.target-file-size-bytes = 134217728` (128 MB). Measured compression on real Binance trade data is ~12:1.

The offload runs every 15 minutes, which means each cycle writes small files into the current day's partition. That is what the daily maintenance flow exists for: at 02:00 UTC it runs binpack compaction toward the 128 MB target, then expires snapshots older than 7 days ([ADR-017](../decisions/ADR-017-iceberg-maintenance-pipeline.md)). Without compaction, a 15-minute cadence produces ~96 files per partition per day.

**No symbol in any Iceberg partition spec.** Symbol is the obvious candidate and it is deliberately absent: `days × exchange × ~30 symbols` on Silver would be ~90 partitions per day, most of them holding a few hundred rows. Symbol is the sort key inside each file instead, so predicate pushdown still prunes row groups. Iceberg supports partition evolution, so `ADD PARTITION FIELD symbol` is available without rewriting data if the query pattern ever justifies it.

## What is not partitioned by

- **Not by symbol, anywhere in storage.** Volume is heavily skewed — BTC and ETH are a large fraction of all trades — so symbol partitions would be lopsided by construction, and the long tail would produce files too small to be worth opening.
- **Not by hour.** Considered for Gold; at these row counts hourly partitions would be pure metadata overhead.
- **No sub-partition on `asset_class` outside Silver.** Everything here is `crypto`. It is in the Silver spec because Silver is the layer that would carry equities or futures if the platform ever ingested them, and adding a partition field later is cheaper on an empty dimension than on a populated one.

## Verifying

```sql
-- ClickHouse: parts and rows per partition
SELECT table, partition, count() AS parts, sum(rows) AS rows
FROM system.parts
WHERE database = 'k2' AND active
GROUP BY table, partition
ORDER BY table, partition;
```

```sql
-- Iceberg: file count and sizes per partition (run in the spark-iceberg container)
SELECT partition, record_count, file_size_in_bytes
FROM cold.silver_trades.files;
```

Rule of thumb used here: flag any Iceberg partition averaging under 10 MB per file as needing compaction, and any ClickHouse table over a few hundred active parts as needing its partition granularity revisited.
