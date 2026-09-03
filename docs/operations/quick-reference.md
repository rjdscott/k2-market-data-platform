# Quick Reference

One page of the commands you actually use. Everything runs from the repo root against
`docker-compose.yml`. Load secrets into your shell first:

```bash
cp .env.example .env      # first time only, then fill in real values
set -a && . ./.env && set +a
```

## Stack

```bash
make up          # docker compose up -d  (builds images on first run)
make health      # is it up: every service healthy, every venue producing, lake still committing
make down        # stop, keep volumes
make ps          # service status
make logs        # tail everything
make test        # Python + Rust unit tests

docker compose logs -f capture-binance          # one service
docker compose up -d --build capture-binance    # rebuild after a Rust change
docker compose up -d --force-recreate --no-deps capture-binance   # after editing instruments.yaml
docker stats --no-stream                             # live CPU / RAM
```

## URLs and credentials

Every UI the platform has, and the one thing each is good for. This is the tools page;
the README links here rather than repeating it.

| URL | What it is for | Credential | First thing to look at |
|-----|----------------|------------|------------------------|
| http://localhost:3000 | Grafana, the dashboards | `admin` / `$GRAFANA_PASSWORD` | `k2-pipeline-overview`, then `k2-lake` |
| http://localhost:8080 | Redpanda Console: topics, groups, live messages | none | `market.crypto.v3.trades.binance` — values decode as **Avro** off the schema registry at `8081` |
| `http://localhost:$K2_PREFECT_PORT` (`.env`, default 4200) | Prefect: the lake schedules and their run history | none | Deployments → `lake-ingest-5min`, `lake-maintenance-daily`; Runs for the last state |
| http://localhost:18181/ui/ | Lakekeeper: browse the Iceberg catalog | none (auth is off) | Warehouses → **`k2`** → namespaces `raw`, `bronze`, `silver`, `gold`, `audit`. The warehouse is named `k2`; Spark and DuckDB address it through the *catalog* name `lake`, so a table the UI shows under `gold` is `lake.gold.trades` in a query |
| http://localhost:9001 | MinIO console: the bytes under the lake | `$MINIO_ROOT_USER` / `$MINIO_ROOT_PASSWORD` | bucket **`k2-lake`** |
| http://localhost:9090 | Prometheus: metrics and alert state | none | Alerts, then `time() - max(k2_lake_last_commit_ts_seconds)` |
| http://localhost:8123 (`/play` for a query box) | ClickHouse: the gold hot tier | `default` / `$CLICKHOUSE_PASSWORD`; research reads `quant` / `$K2_QUANT_PASSWORD` (read-only, `gold` only) | `SELECT count() FROM gold.trades FINAL`. Native protocol from the host is `--port 9002` |
| http://localhost:18080 | Spark master UI | none | mostly empty by design: the lake jobs run as **local** drivers and never register with the master. A running job's own UI is http://localhost:4040, and only while it runs |
| http://localhost:8889 | JupyterLab, the K2 research notebooks — `make notebooks` | token printed by the command | [`notebooks/README.md`](../../notebooks/README.md) |
| http://127.0.0.1:8888 | the Spark image's **vendor** Iceberg sample notebooks, not K2's | none (loopback only, deliberately) | nothing, normally — K2's notebooks are 8889 |

Also listening: Redpanda Kafka API `9092`, Admin API `9644`, Schema Registry `8081`;
ClickHouse native `9002`, Prometheus metrics `9363`; MinIO S3 API `9000`; PostgreSQL (Prefect + Lakekeeper) `15432` on localhost only.
Capture `/metrics` is on port **8082 inside the container only**, not published. There is
no `/health` endpoint: liveness is the `k2-capture healthcheck` subcommand, because the
image is distroless and has no curl.

## Redpanda

```bash
docker exec k2-redpanda rpk cluster health
docker exec k2-redpanda rpk topic list
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -p

# Peek at the live feed. Values are Confluent-framed Avro, so print the key, not the value;
# Redpanda Console decodes values.
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'

# Consumer groups: the two ClickHouse gold feeds (k2-gold-trades on trades.*, k2-gold-book
# on book.*). The lake ingest reads by offset range and has no group.
docker exec k2-redpanda rpk group list
docker exec k2-redpanda rpk group describe k2-gold-trades

# Schema registry
docker exec k2-redpanda curl -s localhost:8081/subjects | jq
```

## ClickHouse

ClickHouse serves the `gold` database, canonical, deduplicated, every venue in one schema
([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)). `gold.trades` and
`gold.book_top20` are `ReplacingMergeTree`: read them with `FINAL` when the number has to be
exact. The v2 `k2` database was dropped at the Phase E cutover on 2026-08-27
([`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md)).

```bash
CH="docker exec k2-clickhouse clickhouse-client --password $CLICKHOUSE_PASSWORD"

$CH -q "SHOW TABLES FROM gold"
$CH -q "SELECT count() FROM gold.trades FINAL"

# Trades in the last 5 minutes, by exchange
$CH -q "SELECT exchange, count() FROM gold.trades FINAL
        WHERE exchange_ts > now() - INTERVAL 5 MINUTE GROUP BY exchange"

# Latest 1m candles, computed on read over the deduplicated trades
$CH -q "SELECT exchange, canonical_symbol, window_start, open, high, low, close, volume, trade_count
        FROM gold.ohlcv_live(bucket = 60) WHERE canonical_symbol = 'BTC/USDT'
        ORDER BY window_start DESC LIMIT 10 FORMAT Pretty"

# Best bid/offer, last few seconds
$CH -q "SELECT exchange, canonical_symbol, second, bid, ask, spread_bps
        FROM gold.bbo_live WHERE canonical_symbol = 'BTC/USDT' ORDER BY second DESC LIMIT 10 FORMAT Pretty"

# Kafka Engine consumer health (q_trades, q_book); a record the feed could not decode is in gold.feed_errors
$CH -q "SELECT table, num_messages_read, num_commits, last_poll_time, exceptions.text
        FROM system.kafka_consumers WHERE database = 'gold' FORMAT Vertical"
$CH -q "SELECT count() FROM gold.feed_errors"

# Table sizes
$CH -q "SELECT table, formatReadableSize(sum(bytes)) AS size, sum(rows) AS rows
        FROM system.parts WHERE database = 'gold' AND active
        GROUP BY table ORDER BY sum(bytes) DESC FORMAT Pretty"
```

Interactive shell: `docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD"`;
research reads use `--user quant --password "$K2_QUANT_PASSWORD"` (read-only, `gold` only).

## Capture tier

```bash
# Liveness: exits non-zero if any continuous stream is past its own staleness bound
for x in binance kraken coinbase; do
  echo -n "$x: "; docker exec k2-capture-$x /k2-capture healthcheck
done

# Metrics: distroless, so read them through Prometheus rather than curl-in-container
curl -sG localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange, kind) (rate(k2_capture_records_produced_total[5m]))' | jq

docker logs --since 5m k2-capture-binance | grep -iE 'error|reconnect'
# No shell in the image, so config comes from the container spec, not `env`
docker inspect k2-capture-binance --format '{{range .Config.Env}}{{println .}}{{end}}' | grep K2_
```

## Lake tier (Iceberg / Prefect)

```bash
# Deployed schedules
docker exec k2-prefect-server prefect deployment ls
docker exec k2-prefect-server prefect flow-run ls --limit 5

# Trigger an ingest / a maintenance run now
docker exec k2-prefect-server prefect deployment run 'lake-ingest/lake-ingest-5min'
docker exec k2-prefect-server prefect deployment run 'lake-maintenance/lake-maintenance-daily'

# Or run either directly in the Spark container
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py
docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --audit-only

# Catalog health, and what it holds
curl -s localhost:18181/health | jq .
PREFIX=$(curl -fsS 'localhost:18181/catalog/v1/config?warehouse=k2' | jq -r '.defaults.prefix')
curl -fsS "localhost:18181/catalog/v1/$PREFIX/namespaces" | jq -c .
#   {"namespaces":[["raw"],["bronze"],["silver"],["gold"],["audit"]]}

# Ingest lag and per-table commit age (timestamps, aged in PromQL)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' | jq -r '.data.result[].value[1]'

# Round-trip the catalog: create, append, read, drop
docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke
```

Exactly-once bookkeeping lives in the Iceberg snapshot summary
(`k2.kafka-offsets`), not in a watermark table , 
[ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md).

## When something breaks

Start at [../runbooks/failure-recovery.md](../runbooks/failure-recovery.md); the alert
that fired names its own runbook in the annotation. Deeper query recipes are in
[data-inspection.md](./data-inspection.md); dashboards and alert definitions in
[observability.md](./observability.md).
