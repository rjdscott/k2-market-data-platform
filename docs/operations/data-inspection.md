# Data Inspection

How to look at the data at every hop: Redpanda → ClickHouse `gold`, and Redpanda → the
Iceberg lake. Every query below matches the as-built schema and was run on 2026-08-27
against the post-cutover stack; column names differ between layers, so copy them rather
than guessing.

> The v2 ClickHouse medallion (`k2.bronze_trades_*`, `k2.silver_trades`, `k2.ohlcv_*`) and
> the six `market.crypto.trades.<ex>[.raw]` topics were **dropped on 2026-08-27** at the
> Phase E cutover. Their DDL is kept in [`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md);
> the served tier is described in [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md).

Load credentials into your shell first, then set a shorthand:

```bash
set -a && . ./.env && set +a
CH="docker exec k2-clickhouse clickhouse-client --password $CLICKHOUSE_PASSWORD"
```

`clickhouse-client` also accepts `--password` with no argument to prompt interactively.
Research reads go through the read-only `quant` user: `--user quant --password "$K2_QUANT_PASSWORD"`.

## Schema cheat sheet

| Layer | Table(s) | Time column | Key columns |
|-------|----------|-------------|-------------|
| Served trades | `gold.trades` (`ReplacingMergeTree`, read with `FINAL`) | `exchange_ts` | `exchange`, `symbol`, `canonical_symbol`, `trade_id`, `price_e8`/`qty_e8` (Int64 at 1e-8) with `price`/`qty` Decimal aliases, `side`, `recv_ts_ns`, `seq`, `conn_id`, `conn_msg_seq`, `src_topic`/`src_partition`/`src_offset` |
| Served book | `gold.book_top20` (`ReplacingMergeTree`, read with `FINAL`) | `second` (`snapshot_ts`) | `exchange`, `canonical_symbol`, `depth`, `seq`, `checksum_ok`, `bid_px`/`bid_qty`/`ask_px`/`ask_qty` (Array(Int64), best first) |
| Rejects | `gold.feed_errors` | `seen_at` | `topic`, `partition`, `offset`, `error`, `raw` |
| Candles, from the lake | `gold.ohlcv_{1m,5m,1h,1d}` | `window_start` | `exchange`, `canonical_symbol`, `open`/`high`/`low`/`close` (aliases over `*_e8`), `volume`, `quote_volume`, `trade_count`, `open_time`, `close_time`, `src_snapshot_id` |
| BBO, from the lake | `gold.bbo_1s` | `second` | `exchange`, `canonical_symbol`, `bid`/`ask` (aliases), `mid`, `spread_bps`, `imbalance`, `microprice` |
| Views, on read | `gold.ohlcv_live(bucket = <seconds>)`, `gold.bbo_live` | `window_start` / `second` | same columns as the tables above, computed over `gold.trades FINAL` / `gold.book_top20 FINAL` |
| Lake archive | `lake.raw.messages` | `kafka_ts` | `topic`, `partition`, `offset`, `ingest_ts`, `key`, `schema_id`, `payload`, `headers` |
| Lake bronze | `lake.bronze.<venue>_<msgtype>` (7 tables) | per venue | the venue's own field names and JSON types, one row per frame, [`docker/lake/README.md`](../../docker/lake/README.md#bronze-per-venue-phase-e-adr-026) |
| Lake silver | `lake.silver.trades_<venue>`, `lake.silver.book_<venue>` | `exchange_ts` / `snapshot_ts` | typed, one row per trade / per frame, canonical symbol, replay / gap / checksum flags |
| Lake gold | `lake.gold.trades`, `lake.gold.dim_*`, `lake.gold.ohlcv_*`, `lake.gold.book_top20`, `lake.gold.bbo_1s` | `exchange_ts` / `window_start` / `second` | the canonical layer ClickHouse `gold` is loaded from |
| Lake audit | `lake.audit.checks` | `run_ts` | `job`, `check_name`, `scope`, `passed`, `observed`, `detail` |

Numbers are the wire's fixed point: `*_e8` Int64 at 1e-8, exact. The `price`/`qty`/`open`…
columns are `ALIAS` expressions that yield the exact `Decimal(38,10)` on read, so
`SELECT price` works but costs a cast per row; group and filter on the `_e8` columns. The
lake's `DECIMAL(28,10)` columns are the same value, divided by 1e8 once, at write.

## Redpanda

```bash
docker exec k2-redpanda rpk topic list

# LIVE: the v3 topics. Values are Confluent-framed Avro, so rpk prints binary;
# the key is the useful thing here (canonical symbol on trades/book, wire symbol on raw).
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -p
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'

# To read a v3 VALUE, use Redpanda Console (http://localhost:8080): it resolves the
# schema id against the registry. rpk will not. Or read the schema itself:
docker exec k2-redpanda curl -s localhost:8081/subjects | jq
docker exec k2-redpanda curl -s \
  localhost:8081/subjects/market.crypto.v3.trades.binance-value/versions/latest \
  | jq -r '.schema | fromjson'

# Consumer groups: k2-gold-trades (gold.q_trades on trades.*) and k2-gold-book
# (gold.q_book on book.*). The lake ingest reads by offset range and has no group.
docker exec k2-redpanda rpk group list
docker exec k2-redpanda rpk group describe k2-gold-trades
```

## Trades: `gold.trades`

```bash
# Volume and freshness, per exchange (FINAL: one row per logical trade)
$CH -q "SELECT exchange, count() AS trades, min(exchange_ts) AS earliest, max(exchange_ts) AS latest
        FROM gold.trades FINAL GROUP BY exchange ORDER BY exchange"

# Are all three exchanges flowing right now?
$CH -q "SELECT exchange, count() AS trades, max(exchange_ts) AS latest
        FROM gold.trades FINAL
        WHERE exchange_ts > now() - INTERVAL 15 MINUTE
        GROUP BY exchange ORDER BY exchange"

# Most recent rows
$CH -q "SELECT exchange, canonical_symbol, price, qty, side, exchange_ts, src_partition, src_offset
        FROM gold.trades FINAL ORDER BY exchange_ts DESC LIMIT 10 FORMAT Pretty"

# Busiest instruments
$CH -q "SELECT exchange, canonical_symbol, count() AS trades
        FROM gold.trades FINAL WHERE exchange_ts > now() - INTERVAL 1 HOUR
        GROUP BY exchange, canonical_symbol ORDER BY trades DESC LIMIT 15 FORMAT Pretty"

# Venue-to-K2 lag: the receive clock against the venue clock. This is the v3 successor of
# the v2 ingestion-lag number in latency-budgets.md; it measures the exchange RTT plus the
# capture parse, not the ClickHouse hop.
$CH -q "SELECT exchange,
               quantile(0.50)(recv_ts_ns / 1e6 - toUnixTimestamp64Milli(exchange_ts)) AS p50_ms,
               quantile(0.99)(recv_ts_ns / 1e6 - toUnixTimestamp64Milli(exchange_ts)) AS p99_ms,
               count() AS n
        FROM gold.trades FINAL
        WHERE exchange_ts > now() - INTERVAL 1 HOUR
        GROUP BY exchange FORMAT Pretty"

# Deliveries vs trades: the gap is venue replays and topic overlap, collapsed by FINAL
$CH -q "SELECT count(), (SELECT count() FROM gold.trades FINAL) FROM gold.trades"

# Kafka Engine health: a non-empty exceptions.text is the thing to look for; a record the
# decoder rejected is in gold.feed_errors with its bytes
$CH -q "SELECT table, consumer_id, num_messages_read, num_commits, last_poll_time, exceptions.text
        FROM system.kafka_consumers WHERE database = 'gold' FORMAT Vertical"
$CH -q "SELECT seen_at, topic, partition, offset, error FROM gold.feed_errors ORDER BY seen_at DESC LIMIT 10"
```

## Candles and BBO: `gold.ohlcv_*`, `gold.ohlcv_live`, `gold.bbo_*`

```bash
# Latest 1m candles, computed on read over the deduplicated trades: any bucket in seconds
$CH -q "SELECT exchange, canonical_symbol, window_start, open, high, low, close, volume, trade_count
        FROM gold.ohlcv_live(bucket = 60)
        WHERE canonical_symbol = 'BTC/USDT' AND window_start > now() - INTERVAL 1 HOUR
        ORDER BY window_start DESC LIMIT 20 FORMAT Pretty"

# The lake-computed candles (the record; loaded by pull, see clickhouse-rebuild-from-lake.md)
$CH -q "SELECT exchange, canonical_symbol, window_start, open, close, volume, trade_count
        FROM gold.ohlcv_1m FINAL ORDER BY window_start DESC LIMIT 20 FORMAT Pretty"

# Candle coverage across the four loaded timeframes
for tf in 1m 5m 1h 1d; do
  echo -n "ohlcv_$tf: "
  $CH -q "SELECT concat(toString(count()), ' candles, latest ', toString(max(window_start)))
          FROM gold.ohlcv_$tf FINAL"
done

# Best bid/offer off the 1 Hz book, with mid / spread / imbalance / microprice
$CH -q "SELECT exchange, canonical_symbol, second, bid, ask, spread_bps, imbalance
        FROM gold.bbo_live WHERE canonical_symbol = 'BTC/USDT' ORDER BY second DESC LIMIT 10 FORMAT Pretty"
```

`gold.ohlcv_live` is a view over `gold.trades FINAL`, so a minute that arrived in two insert
blocks is still one correct candle, the v2 `SummingMergeTree` candles could not promise
that, which is why the v3 tables are loaded from the lake and never aggregated here
([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)).

## Lake tier: Iceberg via Spark

The lake lives on a **Lakekeeper REST catalog** over MinIO
([ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md)), one catalog, named `lake`, with the
`raw`, `bronze`, `silver`, `gold` and `audit` namespaces. Its configuration lives in exactly one place,
[`docker/lake/spark_conf.py`](../../docker/lake/spark_conf.py); every v3 Spark job gets its
session from `lake_session()`, so query it the same way rather than reassembling a dozen
`--conf` flags:

```bash
# Round-trip the catalog first: create, append, read, drop. Proves the session
# and the snapshot-property mechanism the offsets ride on.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke

# An ad-hoc query through the same builder
docker exec -i k2-spark-iceberg python3 - <<'PY'
import sys; sys.path.insert(0, "/home/iceberg/lake")
from spark_conf import lake_session
spark = lake_session("k2-adhoc")
spark.sql("SELECT topic, count(*) FROM lake.raw.messages GROUP BY topic").show(truncate=False)
spark.stop()
PY
```

Queries worth having:

```sql
SHOW TABLES IN lake.bronze;      -- seven per-venue tables
SHOW TABLES IN lake.silver;
SHOW TABLES IN lake.gold;

SELECT exchange, count(*) FROM lake.gold.trades GROUP BY exchange;

-- Snapshot history, with the committed Kafka offsets that make it exactly-once
SELECT snapshot_id, committed_at, operation,
       summary['added-records']    AS rows,
       summary['k2.kafka-offsets'] AS offsets
FROM lake.raw.messages.snapshots ORDER BY committed_at DESC LIMIT 10;

-- File layout, nightly maintenance compacts raw.messages toward 256 MB,
-- bronze.* toward 128 MB
SELECT partition, count(*) AS files,
       round(avg(file_size_in_bytes) / 1048576, 1) AS avg_mb
FROM lake.raw.messages.files GROUP BY partition ORDER BY partition DESC LIMIT 10;

-- What the nightly audit found
SELECT run_ts, job, check_name, scope, passed, observed, detail
FROM lake.audit.checks ORDER BY run_ts DESC LIMIT 20;
```

**Reading the lake from ClickHouse: `iceberg()` only.** The
`s3('…/data/*.parquet')` glob is banned, it reads the object listing rather than the
current Iceberg metadata and returns files no live snapshot references, which fails as a
plausible number rather than as an error. See
[lake-recovery.md](../runbooks/lake-recovery.md).

## ClickHouse vs. lake reconciliation

ClickHouse `gold` is derived from the same topics the lake archives, and the lake's
`gold.trades` is what a reload puts back into it, so on an overlapping window the two must
agree **exactly**, the lake wins on conflict ([ADR-026](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)).
They did on 2026-08-27: 1,978,901 / 273,060 / 33,246 (binance / coinbase / kraken) on
00:00–05:00Z, both sides ([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md#measured-2026-08-27-first-live-apply-commit-of-pr-98)).

```bash
# ClickHouse side
$CH -q "SELECT exchange, count() FROM gold.trades FINAL
        WHERE exchange_ts >= '2026-08-27 00:00:00' AND exchange_ts < '2026-08-27 05:00:00' GROUP BY exchange"
```

```sql
-- Lake side, same window
SELECT exchange, count(*) FROM lake.gold.trades
WHERE exchange_ts >= TIMESTAMP '2026-08-27 00:00:00' AND exchange_ts < TIMESTAMP '2026-08-27 05:00:00'
GROUP BY exchange;
```

A difference means the ClickHouse feed skipped or double-counted something, check
`gold.feed_errors` first, then reload the window from the lake
([clickhouse-rebuild-from-lake.md](../runbooks/clickhouse-rebuild-from-lake.md)).

What *is* a hard invariant internal to the lake, checked by the nightly audit:
`raw.messages` holds an unbroken offset run per `(topic, partition)`, and no two
`bronze.*` rows share their identifier fields. See
[prefect-schedules.md](./prefect-schedules.md) and
[lake-audit-failed.md](../runbooks/lake-audit-failed.md).

## Storage and export

```bash
# Table sizes
$CH -q "SELECT table, formatReadableSize(sum(bytes)) AS size, sum(rows) AS rows
        FROM system.parts WHERE database = 'gold' AND active
        GROUP BY table ORDER BY sum(bytes) DESC FORMAT Pretty"

# CSV / JSON export
$CH -q "SELECT * FROM gold.ohlcv_1h FINAL WHERE window_start > now() - INTERVAL 7 DAY FORMAT CSVWithNames" > ohlcv_1h.csv
$CH -q "SELECT * FROM gold.trades FINAL ORDER BY exchange_ts DESC LIMIT 1000 FORMAT JSONEachRow" > trades.jsonl

# Sample a topic to disk. Values are Confluent-framed Avro, so a sample needs an Avro-aware
# reader; the lake's raw.messages holds the same bytes, queryable through Spark.
docker exec k2-redpanda rpk topic consume market.crypto.v3.raw.binance --num 100 -f '%k\t%o\n' > binance_raw_keys.tsv
```

## Related

- [quick-reference.md](./quick-reference.md), the short version of this page
- [observability.md](./observability.md), metrics and alerts rather than row-level data
- [../runbooks/failure-recovery.md](../runbooks/failure-recovery.md), when inspection shows a gap
