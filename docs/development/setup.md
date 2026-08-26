# Development Setup

Getting the v2 stack running locally, and the inner loops for changing Kotlin or Python
code once it is.

## Prerequisites

| Tool | Needed for | Notes |
|------|-----------|-------|
| Docker Engine + Compose v2 | everything | Budget 16 CPU / 24 GB available to Docker. The stack declares 15.0 CPU / 21.75 GB of limits |
| JDK 21 | running Kotlin tests or a handler outside Docker | Only that; `./gradlew` bootstraps Gradle itself |
| [`uv`](https://docs.astral.sh/uv/) | Python offload-flow tests | Nothing else uses Python locally |
| `jq`, `psql` | the diagnostic one-liners in the ops docs | optional |

## First run

```bash
cp .env.example .env      # then replace every change-me-in-production value
make up
make ps
```

`make up` builds three images on the first run — the Kotlin feed handler, the Prefect
worker, and the Spark image (which downloads the ClickHouse JDBC driver). Allow **several
minutes**; subsequent starts are seconds. Services come up healthy in dependency order —
Redpanda, then `redpanda-init` creates the six topics and exits, then the feed handlers.

Never commit `.env`. Everything reads its secrets from it; nothing in the repo hardcodes a
password.

### Applying the ClickHouse schema

**Known rough edge.** Only `docker/clickhouse/ddl/` is mounted into
`/docker-entrypoint-initdb.d`, so a fresh volume gets the `k2` database and the watermark
table — but **not** the bronze, silver or gold tables. There is no bootstrap script yet;
apply the DDL from [`docker/clickhouse/schema/`](../../docker/clickhouse/schema/) by hand:

```bash
set -a && . ./.env && set +a
docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" --multiquery \
  < docker/clickhouse/schema/11-bronze-coinbase.sql
```

The numbered files accumulated variants as the schema evolved — some target `default`,
some have a `-fixed` twin. The `k2.`-qualified ones are what production runs. See
[../operations/clickhouse-database-standard.md](../operations/clickhouse-database-standard.md)
before picking files, and `SHOW TABLES FROM k2` to confirm what actually landed.

## Verifying data is flowing

```bash
# 1. Raw trades reaching Redpanda
docker exec k2-redpanda rpk topic consume market.crypto.trades.binance.raw --num 3 --format '%v\n'

# 2. All three exchanges landing in silver
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count(), max(timestamp) FROM k2.silver_trades
   WHERE timestamp > now() - INTERVAL 5 MINUTE GROUP BY exchange"

# 3. Candles being produced
docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT count(), max(window_start) FROM k2.ohlcv_1m"
```

More query recipes: [../operations/data-inspection.md](../operations/data-inspection.md).

## Instrument registry

[`config/instruments.yaml`](../../config/instruments.yaml) is the single source of truth
for which symbols each handler subscribes to — 12 Binance, 11 Kraken, 11 Coinbase, in
each exchange's native format (`BTCUSDT`, `XBT/USD`, `BTC-USD`).

> **Bind-mount inode gotcha.** The file is mounted file-by-file, which pins its inode. Most
> editors save by writing a temp file and renaming it, which produces a *new* inode — the
> container keeps reading the old one. After editing:
>
> ```bash
> docker compose up -d --force-recreate --no-deps feed-handler-binance
> ```
>
> `docker restart` will **not** pick up the change. This costs an hour the first time it
> bites you.

## Kotlin inner loop

Rebuild one handler after a code change:

```bash
docker compose up -d --build feed-handler-binance
docker compose logs -f feed-handler-binance
```

Or run a handler on the host against the containerised infrastructure — faster, and you
get a debugger. Redpanda's Kafka API (9092) and schema registry (8081) are published, so
this works with the rest of the stack up:

```bash
cd services/feed-handler-kotlin
K2_EXCHANGE=binance \
K2_INSTRUMENTS_FILE=../../config/instruments.yaml \
K2_SCHEMA_PATH=../../schemas \
K2_KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
K2_KAFKA_SCHEMA_REGISTRY_URL=http://localhost:8081 \
K2_METRICS_PORT=8082 \
./gradlew run
```

Every setting in [`application.conf`](../../services/feed-handler-kotlin/src/main/resources/application.conf)
has a `K2_*` override — HOCON with env substitution, 12-factor style. Drop
`K2_INSTRUMENTS_FILE` and set `K2_SYMBOLS=BTCUSDT,ETHUSDT` for a quick throwaway run.

Stop the containerised handler first (`docker compose stop feed-handler-binance`) or both
will produce to the same topic.

Tests: `./gradlew test --no-daemon` — see [testing.md](./testing.md).

## Python offload inner loop

The offload scripts are bind-mounted into both the Spark and Prefect worker containers,
so edits take effect without a rebuild.

```bash
# Run one table's offload directly, with logs in the foreground
docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
  --source-table bronze_trades_binance \
  --target-table cold.bronze_trades_binance \
  --timestamp-col exchange_timestamp \
  --sequence-col sequence_number \
  --layer bronze

# Trigger the whole flow through Prefect
docker exec k2-prefect-server prefect deployment run 'iceberg-offload-main/iceberg-offload-15min'

# Unit tests (mocked subprocess — no stack needed)
uv run --no-project --with prefect --with psycopg2-binary --with pytest pytest tests -q
```

After changing a flow's schedule or parameters, redeploy it:

```bash
docker exec k2-prefect-worker python3 /opt/prefect/flows/deploy_production.py
```

## Logs

| Source | Where |
|--------|-------|
| Any container | `docker compose logs -f <service>` or `make logs` |
| Feed handler files | `./logs/{binance,kraken,coinbase}/` — bind-mounted out of the containers |
| Offload flow runs | `docker logs k2-prefect-worker`, plus run history at http://localhost:4200 |
| Spark job output | `docker logs k2-spark-iceberg`; the Application UI is on http://localhost:4040 while a job runs |
| ClickHouse server | `docker logs k2-clickhouse` |

## Tearing down

```bash
make down                      # stop, keep volumes and data
docker compose down -v         # also delete volumes — you will re-apply the schema
```

## Related

- [testing.md](./testing.md) — the test suites and what they do not cover
- [../operations/quick-reference.md](../operations/quick-reference.md) — URLs, ports, credentials
- [../operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md) — the full checklist for a new feed
