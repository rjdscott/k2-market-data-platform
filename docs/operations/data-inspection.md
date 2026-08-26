# Data Inspection

How to look at the data at every hop: Redpanda → ClickHouse bronze → silver → gold →
Iceberg cold. Every query below matches the as-built schema; column names differ
between layers, so copy them rather than guessing.

Load credentials into your shell first, then set a shorthand:

```bash
set -a && . ./.env && set +a
CH="docker exec k2-clickhouse clickhouse-client --password $CLICKHOUSE_PASSWORD"
```

`clickhouse-client` also accepts `--password` with no argument to prompt interactively.

## Schema cheat sheet

| Layer | Table(s) | Time column | Key columns |
|-------|----------|-------------|-------------|
| Bronze | `k2.bronze_trades_{binance,kraken,coinbase}` | `exchange_timestamp` | `sequence_number`, `symbol`, `price`, `quantity`, `quote_volume`, `event_time`, `kafka_offset`, `kafka_partition`, `ingestion_timestamp` |
| Silver | `k2.silver_trades` | `timestamp` | `exchange`, `canonical_symbol`, `side`, `trade_id`, `price`, `quantity`, `is_valid`, `ingestion_timestamp`, `processed_at` |
| Gold | `k2.ohlcv_{1m,5m,15m,30m,1h,1d}` | `window_start` | `exchange`, `canonical_symbol`, `open_price`, `high_price`, `low_price`, `close_price`, `volume`, `quote_volume`, `trade_count` |
| Cold | `k2.cold.bronze_trades_*`, `k2.cold.silver_trades`, `k2.cold.gold_ohlcv_*` | as above | Iceberg, queried through Spark |

Bronze tables carry **no** `exchange` column — the exchange is the table. Silver adds it
during unification. Gold windows have a `window_start` only; there is no `window_end`.

`cold.silver_trades` drops `trade_conditions`, `vendor_data` and `validation_errors` —
Spark's JDBC reader cannot deserialize ClickHouse `Array(String)` / `Map(String,String)`.

## Redpanda

```bash
docker exec k2-redpanda rpk topic list
docker exec k2-redpanda rpk topic describe market.crypto.trades.binance.raw -p

# Raw JSON straight off the wire
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 3 --format '%v\n' | jq

# With partition/offset metadata
docker exec k2-redpanda rpk topic consume market.crypto.trades.kraken.raw \
  --num 3 --format 'p=%p o=%o t=%d %v\n'

# Normalized topics carry Avro — bytes without a deserializer. Read the schema instead:
docker exec k2-redpanda curl -s localhost:8081/subjects | jq
docker exec k2-redpanda curl -s \
  localhost:8081/subjects/market.crypto.trades.binance-value/versions/latest \
  | jq -r '.schema | fromjson'

# Consumer groups (one per ClickHouse Kafka Engine table)
docker exec k2-redpanda rpk group list
docker exec k2-redpanda rpk group describe clickhouse_bronze_binance_consumer
```

## Bronze — per-exchange ingest

```bash
# Volume and freshness for all three exchanges
for t in binance kraken coinbase; do
  echo "== $t"
  $CH -q "SELECT count() AS trades,
                 min(exchange_timestamp) AS earliest,
                 max(exchange_timestamp) AS latest,
                 uniqExact(symbol) AS symbols
          FROM k2.bronze_trades_$t"
done

# Most recent rows
$CH -q "SELECT symbol, price, quantity, exchange_timestamp, kafka_partition, kafka_offset
        FROM k2.bronze_trades_binance
        ORDER BY exchange_timestamp DESC LIMIT 10 FORMAT Pretty"

# Kafka Engine health — a non-empty last_exception is the thing to look for
$CH -q "SELECT table, consumer_id, num_messages_read, num_commits,
               last_poll_time, last_exception
        FROM system.kafka_consumers WHERE database = 'k2' FORMAT Vertical"
```

## Silver — unified trades

```bash
# Are all three exchanges flowing?
$CH -q "SELECT exchange, count() AS trades, max(timestamp) AS latest
        FROM k2.silver_trades
        WHERE timestamp > now() - INTERVAL 15 MINUTE
        GROUP BY exchange ORDER BY exchange"

# Busiest instruments
$CH -q "SELECT exchange, canonical_symbol, count() AS trades
        FROM k2.silver_trades WHERE timestamp > now() - INTERVAL 1 HOUR AND is_valid
        GROUP BY exchange, canonical_symbol ORDER BY trades DESC LIMIT 15 FORMAT Pretty"

# End-to-end ingestion lag — this is the number in latency-budgets.md
$CH -q "SELECT exchange,
               quantile(0.50)(dateDiff('millisecond', timestamp, ingestion_timestamp)) AS p50_ms,
               quantile(0.99)(dateDiff('millisecond', timestamp, ingestion_timestamp)) AS p99_ms,
               count() AS n
        FROM k2.silver_trades
        WHERE timestamp > now() - INTERVAL 1 HOUR
        GROUP BY exchange FORMAT Pretty"

# Anything failing validation?
$CH -q "SELECT exchange, count() FROM k2.silver_trades
        WHERE NOT is_valid AND timestamp > now() - INTERVAL 1 DAY GROUP BY exchange"
```

## Gold — OHLCV candles

```bash
# Latest 1m candles
$CH -q "SELECT exchange, canonical_symbol, window_start,
               open_price, high_price, low_price, close_price, volume, trade_count
        FROM k2.ohlcv_1m
        WHERE window_start > now() - INTERVAL 1 HOUR
        ORDER BY window_start DESC LIMIT 20 FORMAT Pretty"

# Candle coverage across all six timeframes
for tf in 1m 5m 15m 30m 1h 1d; do
  echo -n "ohlcv_$tf: "
  $CH -q "SELECT concat(toString(count()), ' candles, latest ', toString(max(window_start)))
          FROM k2.ohlcv_$tf"
done
```

`ohlcv_*` tables are `SummingMergeTree` on `(volume, quote_volume, trade_count)`. Rows
for the same `(exchange, canonical_symbol, window_start)` merge in the background, so a
freshly written window can show duplicate partial rows. Add `FINAL` — or a
`GROUP BY ... sum()` — when exact per-window totals matter:

```bash
$CH -q "SELECT canonical_symbol, window_start, sum(volume) AS volume, sum(trade_count) AS trades
        FROM k2.ohlcv_1m WHERE window_start > now() - INTERVAL 10 MINUTE
        GROUP BY canonical_symbol, window_start ORDER BY window_start DESC FORMAT Pretty"
```

## Cold tier — Iceberg via Spark

The offload uses a **hadoop catalog** (no REST catalog service). The warehouse is a
directory bind-mounted from `docker/iceberg/warehouse` into the Spark container at
`/home/iceberg/warehouse`. Reproduce the offload job's session config to query it:

```bash
docker exec -it k2-spark-iceberg spark-sql \
  --conf spark.sql.catalog.k2=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.k2.type=hadoop \
  --conf spark.sql.catalog.k2.warehouse=/home/iceberg/warehouse \
  --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions
```

Then:

```sql
SHOW TABLES IN k2.cold;

SELECT count(*) FROM k2.cold.silver_trades;
SELECT exchange, count(*) FROM k2.cold.silver_trades GROUP BY exchange;

-- Snapshot history (what each 15-minute offload appended)
SELECT snapshot_id, committed_at, operation, summary['added-records']
FROM k2.cold.silver_trades.snapshots ORDER BY committed_at DESC LIMIT 10;

-- File layout — the daily maintenance flow compacts these to ~128 MB
SELECT count(*) AS files, sum(file_size_in_bytes) / 1024 / 1024 AS mb
FROM k2.cold.bronze_trades_binance.files;
```

Same config from PySpark — see [`docker/offload/offload_generic.py`](../../docker/offload/offload_generic.py)
for the canonical session builder.

## Warm vs. cold reconciliation

Cold accumulates history past the ClickHouse TTL (7 days bronze, 30 days silver), so
cold row counts exceeding hot is expected. What should match is the overlapping window:

```bash
# Hot side
$CH -q "SELECT count() FROM k2.silver_trades WHERE timestamp >= '2026-02-18 00:00:00'"
```

```sql
-- Cold side, same window
SELECT count(*) FROM k2.cold.silver_trades WHERE timestamp >= TIMESTAMP '2026-02-18 00:00:00';
```

Gold tables can differ by a few percent because ClickHouse background part merges are
still in flight when the offload snapshot is taken. The daily audit in
[prefect-schedules.md](./prefect-schedules.md) treats that as expected and only fails on
missing tables or errors.

## Storage and export

```bash
# Table sizes
$CH -q "SELECT table, formatReadableSize(sum(bytes)) AS size, sum(rows) AS rows
        FROM system.parts WHERE database = 'k2' AND active
        GROUP BY table ORDER BY sum(bytes) DESC FORMAT Pretty"

# CSV / JSON export
$CH -q "SELECT * FROM k2.ohlcv_1h WHERE window_start > now() - INTERVAL 7 DAY FORMAT CSVWithNames" > ohlcv_1h.csv
$CH -q "SELECT * FROM k2.silver_trades LIMIT 1000 FORMAT JSONEachRow" > trades.jsonl

# Sample a topic to disk
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 10000 --format '%v\n' > binance_raw_sample.jsonl
```

## Related

- [quick-reference.md](./quick-reference.md) — the short version of this page
- [observability.md](./observability.md) — metrics and alerts rather than row-level data
- [runbooks/failure-recovery.md](./runbooks/failure-recovery.md) — when inspection shows a gap
