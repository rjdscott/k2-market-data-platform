# Data Inspection

How to look at the data at every hop: Redpanda → ClickHouse bronze → silver → gold, and
Redpanda → the Iceberg lake. Every query below matches the as-built schema; column names
differ between layers, so copy them rather than guessing.

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
| Lake archive | `lake.raw.messages` | `kafka_ts` | `topic`, `partition`, `offset`, `ingest_ts`, `key`, `schema_id`, `payload`, `headers` |
| Lake bronze | `lake.bronze.trades` | `exchange_ts` | `exchange`, `symbol`, `canonical_symbol`, `trade_id`, `price`, `qty`, `side`, `recv_ts_ns`, `seq`, `conn_id`, `conn_msg_seq`, `src_topic`/`src_partition`/`src_offset`, `ingest_ts` |
| Lake bronze | `lake.bronze.book_snapshots_l2` | `snapshot_ts` | as above plus `depth`, `checksum_ok`, `bids`, `asks`, `snapshot_ts_ns` |
| Lake audit | `lake.audit.checks` | `run_ts` | `job`, `check_name`, `scope`, `passed`, `observed`, `detail` |

ClickHouse bronze tables carry **no** `exchange` column — the exchange is the table. Silver
adds it during unification. Gold windows have a `window_start` only; there is no
`window_end`.

The **lake** tables invert that: one `bronze.trades` for all three venues with `exchange`
as the leading partition field ([ADR-024](../adr/ADR-024-unified-bronze-tables-in-the-lake.md)),
and `raw.messages` holding the Kafka value byte for byte, framing included, as the system
of record ([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)). `price`/`qty` are
`DECIMAL(28,10)` — the wire's int64 at 1e-8 divided by 1e8, exact and unrounded.

## Redpanda

```bash
docker exec k2-redpanda rpk topic list

# LIVE — the v3 topics. Values are Confluent-framed Avro, so rpk prints binary;
# the key is the useful thing here (canonical symbol on trades/book, wire symbol on raw).
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -p
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'

# To read a v3 VALUE, use Redpanda Console (http://localhost:8080) — it resolves the
# schema id against the registry. rpk will not. Or read the schema itself:
docker exec k2-redpanda curl -s localhost:8081/subjects | jq
docker exec k2-redpanda curl -s \
  localhost:8081/subjects/market.crypto.v3.trades.binance-value/versions/latest \
  | jq -r '.schema | fromjson'

# FROZEN v2 — retained data only, no producer since 2026-08-26 (ADR-019). Still readable,
# and still plain JSON, which is why these are the easy ones to eyeball:
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 3 --format '%v\n' | jq
docker exec k2-redpanda rpk topic consume market.crypto.trades.kraken.raw \
  --num 3 --format 'p=%p o=%o t=%d %v\n'

# Consumer groups (one per ClickHouse Kafka Engine table, all on frozen v2 topics).
# The names do not match the exchanges — Binance's is `clickhouse_bronze_offload_test`;
# docs/runbooks/redpanda.md has the mapping.
docker exec k2-redpanda rpk group list
docker exec k2-redpanda rpk group describe clickhouse_bronze_offload_test
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

## Lake tier — Iceberg via Spark

The lake lives on a **Lakekeeper REST catalog** over MinIO
([ADR-023](../adr/ADR-023-lakekeeper-rest-catalog.md)) — one catalog, named `lake`, with the
`raw`, `bronze` and `audit` namespaces. Its configuration lives in exactly one place,
[`docker/lake/spark_conf.py`](../../docker/lake/spark_conf.py); every v3 Spark job gets its
session from `lake_session()`, so query it the same way rather than reassembling a dozen
`--conf` flags:

```bash
# Round-trip the catalog first — create, append, read, drop. Proves the session
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
SHOW TABLES IN lake.bronze;

SELECT exchange, count(*) FROM lake.bronze.trades GROUP BY exchange;

-- Snapshot history, with the committed Kafka offsets that make it exactly-once
SELECT snapshot_id, committed_at, operation,
       summary['added-records']    AS rows,
       summary['k2.kafka-offsets'] AS offsets
FROM lake.raw.messages.snapshots ORDER BY committed_at DESC LIMIT 10;

-- File layout — nightly maintenance compacts raw.messages toward 256 MB,
-- bronze.* toward 128 MB
SELECT partition, count(*) AS files,
       round(avg(file_size_in_bytes) / 1048576, 1) AS avg_mb
FROM lake.raw.messages.files GROUP BY partition ORDER BY partition DESC LIMIT 10;

-- What the nightly audit found
SELECT run_ts, job, check_name, scope, passed, observed, detail
FROM lake.audit.checks ORDER BY run_ts DESC LIMIT 20;
```

**Reading the lake from ClickHouse: `iceberg()` only.** The
`s3('…/data/*.parquet')` glob is banned — it reads the object listing rather than the
current Iceberg metadata and returns files no live snapshot references, which fails as a
plausible number rather than as an error. See
[lake-recovery.md](../runbooks/lake-recovery.md).

## Hot vs. lake reconciliation

The lake accumulates history past the ClickHouse TTL (7 days bronze, 30 days silver), so
lake row counts exceeding the hot tier is expected. Two things are worth comparing on an
overlapping window:

```bash
# Hot side
$CH -q "SELECT exchange, count() FROM k2.silver_trades WHERE timestamp >= '2026-08-26 00:00:00' GROUP BY exchange"
```

```sql
-- Lake side, same window
SELECT exchange, count(*) FROM lake.bronze.trades
WHERE exchange_ts >= TIMESTAMP '2026-08-26 00:00:00' GROUP BY exchange;
```

**During the Phase C parallel run these are two independently captured paths** — the
ClickHouse tier reads the v2 Kotlin topics, the lake reads the v3 capture topics — so an
exact match is not the expectation and a small divergence is not a finding.
`scripts/parity/compare_trades.py` is the tool that compares them properly, on
`(exchange, symbol, trade_id)` with a fixed-point tolerance; it is the evidence
[ADR-019](../adr/ADR-019-rust-capture-tier.md)'s Kotlin retirement rests on.

What *is* a hard invariant is internal to the lake, and the nightly audit checks it:
`raw.messages` holds an unbroken offset run per `(topic, partition)`, and no two
`bronze.*` rows share their identifier fields. See
[prefect-schedules.md](./prefect-schedules.md) and
[lake-audit-failed.md](../runbooks/lake-audit-failed.md).

## Storage and export

```bash
# Table sizes
$CH -q "SELECT table, formatReadableSize(sum(bytes)) AS size, sum(rows) AS rows
        FROM system.parts WHERE database = 'k2' AND active
        GROUP BY table ORDER BY sum(bytes) DESC FORMAT Pretty"

# CSV / JSON export
$CH -q "SELECT * FROM k2.ohlcv_1h WHERE window_start > now() - INTERVAL 7 DAY FORMAT CSVWithNames" > ohlcv_1h.csv
$CH -q "SELECT * FROM k2.silver_trades LIMIT 1000 FORMAT JSONEachRow" > trades.jsonl

# Sample a topic to disk. The v2 raw topics are frozen but retained, and they are the only
# ones whose values are plain JSON — a v3 sample needs an Avro-aware reader.
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw \
  --num 10000 --format '%v\n' > binance_raw_sample.jsonl
```

## Related

- [quick-reference.md](./quick-reference.md) — the short version of this page
- [observability.md](./observability.md) — metrics and alerts rather than row-level data
- [../runbooks/failure-recovery.md](../runbooks/failure-recovery.md) — when inspection shows a gap
