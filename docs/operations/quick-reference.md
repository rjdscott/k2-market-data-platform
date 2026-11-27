# Quick Reference

The per-component commands on one page. Starting, stopping, health, the consoles and their
logins, recreating one service and rolling out a change are the
[operator guide](./README.md); this page assumes the stack is up. Everything runs from the
root of the live checkout with the secrets loaded:

```bash
set -a && . ./.env && set +a
```

## Stack

```bash
make health      # is it up: every service healthy, every venue delivering to Redpanda
make ps          # service status (docker compose ps)
make logs        # follow every service's output
docker compose logs -f capture-binance               # one service
docker stats --no-stream                             # live CPU / RAM per container
```

`make up` and `make dev-up` refuse this host's stack until its MinIO credential is replaced;
start or recreate services by name meanwhile, under the pause the operator guide describes
([Read this first](./README.md#read-this-first-the-state-of-this-host),
[section 6](./README.md#6-restart-or-recreate-one-service)). `make test` and `make gates` start
throwaway containers of their own: not on a host short of memory while the stack runs.

Every console and port, with what it is for and where its login comes from:
[operator guide, section 4](./README.md#4-consoles-and-endpoints).

## Redpanda

```bash
docker exec k2-redpanda rpk cluster health
docker exec k2-redpanda rpk topic list
docker exec k2-redpanda rpk topic describe market.crypto.v3.trades.binance -p

# Peek at the live feed. Values are Confluent-framed Avro, so print the key, not the value;
# Redpanda Console decodes values.
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance -n 3 -f '%p %o %k\n'

# Consumer groups: the two ClickHouse gold feeds (k2-gold-trades on trades.*, k2-gold-book
# on book.*); no members, state Empty, while ClickHouse is paused (ADR-057). Nothing else
# reads the topics until phase 004.
docker exec k2-redpanda rpk group list
docker exec k2-redpanda rpk group describe k2-gold-trades

# Schema registry
docker exec k2-redpanda curl -s localhost:8081/subjects | jq
```

## ClickHouse

**Paused** behind the compose profile `hot-gold` ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)): every command in this
section needs that profile running. Starting it, and its 7-day limit:
[pause-and-resume-a-serving-product.md](../runbooks/pause-and-resume-a-serving-product.md).

While it runs, ClickHouse serves the `gold` database, canonical, deduplicated, every venue in one schema
([`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)). `gold.trades` and
`gold.book_top20` are `ReplacingMergeTree`: read them with `FINAL` when the number has to be
exact. The v2 `k2` database was dropped at the Phase E cutover on 2026-08-27 (phase names:
[glossary](../adr/glossary.md#phases-a-to-g))
([`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md)).

```bash
# A function, not a variable: zsh does not split "$CH" into words, so `CH="docker exec ..."; $CH`
# fails there with "command not found".
ch() { docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" "$@"; }

ch -q "SHOW TABLES FROM gold"
ch -q "SELECT count() FROM gold.trades FINAL"

# Trades in the last 5 minutes, by exchange
ch -q "SELECT exchange, count() FROM gold.trades FINAL
        WHERE exchange_ts > now() - INTERVAL 5 MINUTE GROUP BY exchange"

# Latest 1m candles, computed on read over the deduplicated trades
ch -q "SELECT exchange, canonical_symbol, window_start, open, high, low, close, volume, trade_count
        FROM gold.ohlcv_live(bucket = 60) WHERE canonical_symbol = 'BTC/USDT'
        ORDER BY window_start DESC LIMIT 10 FORMAT Pretty"

# Best bid/offer, last few seconds
ch -q "SELECT exchange, canonical_symbol, second, bid, ask, spread_bps
        FROM gold.bbo_live WHERE canonical_symbol = 'BTC/USDT' ORDER BY second DESC LIMIT 10 FORMAT Pretty"

# Kafka Engine consumer health (q_trades, q_book); a record the feed could not decode is in gold.feed_errors
ch -q "SELECT table, num_messages_read, num_commits, last_poll_time, exceptions.text
        FROM system.kafka_consumers WHERE database = 'gold' FORMAT Vertical"
ch -q "SELECT count() FROM gold.feed_errors"

# Table sizes
ch -q "SELECT table, formatReadableSize(sum(bytes)) AS size, sum(rows) AS rows
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

# The order-book switch (ADR-050): the file, and what each capture read at start
grep -A3 '^book_capture:' config/instruments.yaml
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=k2_capture_book_capture' \
  | jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'       # 1 on, 0 off; make health step (4) compares

# The 15-minute gate after recreating one capture: gaps plus reconnects, 0 per venue
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=sum by (exchange) (increase(k2_capture_gaps_total[15m])) + on (exchange) sum by (exchange) (increase(k2_capture_reconnects_total[15m]))' \
  | jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'
```

Changing the switch, and the order its first rollout needs: [README.md, section 6](README.md#pause-or-resume-one-venues-order-book).

## Lake tier (Iceberg)

The v3 lake ingest pipeline that wrote this tier is retired and archived in
[`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/), and its namespaces were dropped
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)). Until
[phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) brings
the first product's DAG, the catalog, the object store and Airflow run with nothing in them.

```bash
# The DAGs and their state: none until phase 002.
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list -o json 2>/dev/null | tail -n 1
#   []

```

Lakekeeper has had no host port since 2026-11-27: it has no authentication, so its network
`k2-catalog` is its fence ([ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)
part 7). Ask it from a throwaway container on that network, the route `scripts/backup-lake.sh`
takes (compose prefixes the network with the project name, hence the `grep`). Not yet run on the
live stack in this shape. <!-- VERIFY: run the block below on the live stack after the cutover and paste what it printed -->

```bash
# Catalog health, and what it holds
net=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' k2-minio | grep -o '[^ ]*_k2-catalog')
catalog() {  # catalog <path>: a GET to Lakekeeper by name, with no credential
  docker run --rm --network "$net" --cpus 0.5 --memory 128m \
    --entrypoint curl ghcr.io/rjdscott/minio:RELEASE.2025-09-07T16-13-09Z@sha256:a1a8bd4ac40ad7881a245bab97323e18f971e4d4cba2c2007ec1bedd21cbaba2 -fsS "http://lakekeeper:8181/$1"
}
catalog health
PREFIX=$(catalog 'catalog/v1/config?warehouse=k2' | jq -r '.defaults.prefix')
catalog "catalog/v1/$PREFIX/namespaces" | jq -c .
#   {"namespaces":[]} once the v3 lake's namespaces are dropped (printed on 2026-11-26, from the host port since removed)
```

`docker/lake/offsets.py` keeps the exactly-once bookkeeping the v3 ingest wrote into each Iceberg
snapshot summary ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)), for the Kafka
connector of phase 004; nothing calls it until then.

## Query the lake with Trino

One Trino node reads the lake through the same Lakekeeper catalog as Spark
([chapter 22](../architecture/22-access-and-identity.md),
[ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md)). Since 2026-11-27 it runs from the deployed
release's etc (`/k2/releases/pointers/trino/compiled/trino/etc`), serves HTTPS only on
`127.0.0.1:${K2_TRINO_PORT:-8443}` under the local CA, and takes password logins only
([ADR-060](../adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md)); its Web UI is off.
Catalog `lake` is warehouse `k2`, read-only at the connector, and its schemas are the lake's
namespaces. Each data domain has a read-only reader catalog named from its id (`market-analytics`
is `market_analytics`) and a writable `<domain>_build` over the same warehouse; the compiled rules
let only a product's builder and publisher write there (chapter 22). Every catalog is empty until
phase 002.

The client runs in a one-off container on Trino's network, by name, verified against `ca.pem`,
with the password read from its file inside the container. The helper is
[tls-and-passwords.md](../runbooks/tls-and-passwords.md)'s, which ran it on throwaway lakes on
2026-11-26; on the live stack `NET` is the project's `<project>_k2-net`, since compose prefixes
the network name. Not yet run on the live stack.
<!-- VERIFY: run trino_cli svc.platform 'SHOW CATALOGS' on the live stack after the cutover and paste the output -->

```bash
NET=${K2_COMPOSE_PROJECT}_k2-net K2_SECRETS=${K2_SECRETS:-$HOME/k2-secrets}   # with .env loaded
trino_cli() {  # trino_cli <svc.* principal> <SQL>: Trino's own CLI, by name, verified against ca.pem
  docker run --rm --network "$NET" --user "$(id -u):$(id -g)" -e K2_USER="$1" -e K2_SQL="$2" \
    -v "$K2_SECRETS/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
    -v "$K2_SECRETS/principals/$1.password:/run/k2-secrets/login.password:ro" --entrypoint bash trinodb/trino:484 \
    -c 'TRINO_PASSWORD=$(cat /run/k2-secrets/login.password) exec trino --server https://trino:8443 \
          --truststore-path /run/k2-secrets/tls/ca.pem --user "$K2_USER" --password --execute "$K2_SQL"'
}
trino_cli svc.platform 'SHOW CATALOGS'
trino_cli svc.platform 'SHOW SCHEMAS FROM lake'

# Nothing through catalog lake commits, table procedures included. Needs a table, so not
# runnable until phase 002 (on 2026-11-25, against a v3 table, the CLI of the day printed the denial below).
trino_cli svc.platform 'EXPLAIN ALTER TABLE lake.<namespace>.<table> EXECUTE optimize'
#   ... failed: Access Denied: Cannot execute table procedure OPTIMIZE on <namespace>.<table>
```

`make test-trino` proves the same fences on a throwaway Trino, tables included.


## When something breaks

Start at the [operator guide, section 9](./README.md#9-when-something-looks-wrong); the alert
that fired names its own runbook in the annotation ([index](../runbooks/README.md)). Dashboards
and alert definitions are in [observability.md](./observability.md).
