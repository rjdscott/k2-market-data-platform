# Quick Reference

One page of the commands you actually use. Everything runs from the repo root against
`docker-compose.yml`. Load secrets into your shell first:

```bash
cp .env.example .env      # first time only — then fill in real values
set -a && . ./.env && set +a
```

## Stack

```bash
make up          # docker compose up -d  (builds images on first run)
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

| Service | URL | Credentials |
|---------|-----|-------------|
| Redpanda Console | http://localhost:8080 | none |
| Grafana | http://localhost:3000 | `admin` / `$GRAFANA_PASSWORD` |
| Prometheus | http://localhost:9090 | none |
| Prefect | http://localhost:4200 | none |
| Lakekeeper (Iceberg REST catalog) | http://localhost:18181 | none (`/health`, `/catalog/v1/...`) |
| MinIO Console | http://localhost:9001 | `$MINIO_ROOT_USER` / `$MINIO_ROOT_PASSWORD` |
| ClickHouse HTTP | http://localhost:8123 | `default` / `$CLICKHOUSE_PASSWORD` |
| Spark Master UI | http://localhost:18080 | none |
| Spark Application UI | http://localhost:4040 | only while a job is running |

Also listening: Redpanda Kafka API `9092`, Admin API `9644`, Schema Registry `8081`;
ClickHouse native `9002`, Prometheus metrics `9363`; MinIO S3 API `9000`; PostgreSQL (Prefect + Lakekeeper) `15432` on localhost only.
Capture `/metrics` is on port **8082 inside the container only** — not published. There is
no `/health` endpoint: liveness is the `k2-capture healthcheck` subcommand, because the
image is distroless and has no curl.

## Redpanda

```bash
docker exec k2-redpanda rpk cluster health
docker exec k2-redpanda rpk topic list
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -p

# Peek at the live feed (the six market.crypto.trades.* v2 topics are frozen — no producer).
# v3 values are Confluent-framed Avro, so print the key, not the value; Console decodes values.
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'

# Consumer groups (ClickHouse Kafka Engine, all on frozen v2 topics — lag should sit at 0).
# The names do not match the exchanges: Binance's group is `clickhouse_bronze_offload_test`.
# See docs/runbooks/redpanda.md for the mapping.
docker exec k2-redpanda rpk group list
docker exec k2-redpanda rpk group describe clickhouse_bronze_offload_test

# Schema registry
docker exec k2-redpanda curl -s localhost:8081/subjects | jq
```

## ClickHouse

The `k2` database is **frozen** — it holds history and gains no rows, and its TTLs keep
expiring them ([../architecture/README.md](../architecture/README.md)). These queries all
still work; a `WHERE timestamp > now() - INTERVAL 5 MINUTE` will just be empty.

```bash
CH="docker exec k2-clickhouse clickhouse-client --password $CLICKHOUSE_PASSWORD"

$CH -q "SHOW TABLES FROM k2"
$CH -q "SELECT count() FROM k2.bronze_trades_binance"

# Trades in the last 5 minutes, by exchange
$CH -q "SELECT exchange, count() FROM k2.silver_trades
        WHERE timestamp > now() - INTERVAL 5 MINUTE GROUP BY exchange"

# Latest 1m candles
$CH -q "SELECT exchange, canonical_symbol, window_start, open_price, high_price,
               low_price, close_price, volume, trade_count
        FROM k2.ohlcv_1m ORDER BY window_start DESC LIMIT 10 FORMAT Pretty"

# Kafka Engine consumer health
$CH -q "SELECT table, num_messages_read, num_commits, last_exception
        FROM system.kafka_consumers WHERE database = 'k2' FORMAT Vertical"

# Table sizes
$CH -q "SELECT table, formatReadableSize(sum(bytes)) AS size, sum(rows) AS rows
        FROM system.parts WHERE database = 'k2' AND active
        GROUP BY table ORDER BY sum(bytes) DESC FORMAT Pretty"
```

Interactive shell: `docker exec -it k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD"`

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
#   {"namespaces":[["raw"],["bronze"],["audit"],["scratch"]]}

# Ingest lag and per-table commit age (timestamps, aged in PromQL)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' | jq -r '.data.result[].value[1]'

# Round-trip the catalog: create, append, read, drop
docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke
```

Exactly-once bookkeeping lives in the Iceberg snapshot summary
(`k2.kafka-offsets`), not in a watermark table —
[ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md).

## When something breaks

Start at [../runbooks/failure-recovery.md](../runbooks/failure-recovery.md); the alert
that fired names its own runbook in the annotation. Deeper query recipes are in
[data-inspection.md](./data-inspection.md); dashboards and alert definitions in
[observability.md](./observability.md).
