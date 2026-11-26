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
make health      # is it up: every service healthy, every venue producing, lake still committing
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
```

## Lake tier (Iceberg, scheduled by Airflow)

```bash
# The DAGs and their state; the last runs of the ingest
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list-runs lake_ingest | head -5

# Trigger an ingest now (it takes the DAG's only slot, so no scheduled run starts beside it)
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest

# Or run a job by hand in the Spark container, logs in the foreground. Each takes the lake's
# writer lock, so a scheduled ingest that lands meanwhile is skipped (exit 75), not doubled.
docker exec k2-spark-iceberg python3 /home/iceberg/lake/ingest.py
docker exec k2-spark-iceberg python3 /home/iceberg/lake/maintenance.py --audit-only

# Catalog health, and what it holds
curl -s localhost:18181/health | jq .
PREFIX=$(curl -fsS 'localhost:18181/catalog/v1/config?warehouse=k2' | jq -r '.defaults.prefix')
curl -fsS "localhost:18181/catalog/v1/$PREFIX/namespaces" | jq -c .
#   {"namespaces":[["raw"],["bronze"],["silver"],["gold"],["audit"],["scratch_p4"]]}   (2026-11-26; scratch_p4 is the P4 spike's scratch namespace)

# Ingest lag and per-table commit age (timestamps, aged in PromQL)
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' | jq -r '.data.result[].value[1]'

# Round-trip the catalog: create, append, read, drop
docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke
```

Exactly-once bookkeeping lives in the Iceberg snapshot summary
(`k2.kafka-offsets`), not in a watermark table , 
[ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md).

## Query the lake with Trino

One Trino node reads every lake table through the same Lakekeeper catalog as Spark and
DuckDB ([`docker/trino/README.md`](../../docker/trino/README.md),
[ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md)). Catalog `lake` is warehouse `k2`,
read-only at the connector, and its schemas are the lake's namespaces: `lake.gold.trades` is
`gold.trades`. Each data domain in `contracts/domains.yaml` has a catalog and warehouse of its
own for its marts, named from the domain id (`market-analytics` is `market_analytics`), whose
tables only its writer `dbt_<domain>` creates, changes or drops with SQL statements, and which
everyone reads; table procedures are not fenced per user
([ADR-044](../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)). `market_analytics`
holds the dbt marts, one schema per product, rebuilt by the Airflow DAG `dbt_marts` every hour
at minute 10 ([`dbt/`](../../dbt/README.md), [runbook](../runbooks/dbt-marts.md));
`governance` holds the quality-results view, built after the others; the other three are empty. The user is whatever `--user` says, not authenticated. The session
time zone is the client's: the CLI inside the container sends UTC, a client on the host sends
the host's zone unless told (`--timezone UTC`).

```bash
# These ran against k2-trino on the live stack on 2026-11-25.
docker exec k2-trino trino --execute 'SHOW SCHEMAS FROM lake'
#   "audit" "bronze" "gold" "information_schema" "raw" "silver" "system"   (2026-11-26: "scratch_p4" too)
docker exec -it k2-trino trino --catalog lake --schema gold          # interactive
docker exec k2-trino trino --execute 'SELECT count(*) FROM lake.gold.ohlcv_1m'

# Time travel by snapshot id, the way the parity gates pin
docker exec k2-trino trino --execute "SELECT snapshot_id FROM lake.gold.\"ohlcv_1m\$refs\" WHERE name = 'main'"
docker exec k2-trino trino --execute 'SELECT count(*) FROM lake.gold.ohlcv_1m FOR VERSION AS OF <snapshot id>'

# Nothing through catalog lake commits, table procedures included
docker exec k2-trino trino --execute 'EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize'
#   ... failed: Access Denied: Cannot execute table procedure OPTIMIZE on gold.ohlcv_1m

# One fence per domain. Ran against k2-trino on the live stack on 2026-11-25.
docker exec k2-trino trino --execute 'SHOW CATALOGS'
#   "fx_reference" "lake" "market_analytics" "market_capture" "reference_data" "system"
docker exec k2-trino trino --user dbt_market_analytics --execute 'CREATE TABLE reference_data.s.t (x integer)'
#   ... failed: Access Denied: Cannot create table reference_data.s.t

# The marts. Ran against k2-trino on the live stack on 2026-11-25.
docker exec k2-trino trino --execute 'SHOW SCHEMAS FROM market_analytics'
#   "candles" "delayed_candles" "information_schema" "staging" "system" "venue_quality"
docker exec k2-trino trino --execute 'DESCRIBE market_analytics.venue_quality.venue_quality_scorecard'
#   exchange, utc_date, trades, trades_after_gap, trade_ids_missing, trades_gap_unknown, day_audits,
#   day_audits_failed, whole_table_audits, whole_table_audits_failed, raw_frames_lost,
#   trade_records_lost, book_records_lost
docker exec k2-trino trino --execute \
  "SELECT max(close_time) <= current_timestamp - INTERVAL '20' MINUTE FROM market_analytics.delayed_candles.ohlcv_delayed_20m"
#   "true"

# Quality results (ADR-046): every dbt test a domain's runs ran, in its own catalog. What did
# not pass, newest first. Ran against k2-trino on the live stack on 2026-11-25.
docker exec k2-trino trino --execute "SELECT run_started_at, tested, test_name, status, message
  FROM market_analytics.dq.dbt_test_results WHERE status <> 'pass' ORDER BY run_started_at DESC LIMIT 20"
#   (no rows: every recorded test passed)
# And every result in one place, the lake's audits too, through the governance view. Ran
# against k2-trino on the live stack on 2026-11-25, after the governance rollout.
docker exec k2-trino trino --execute "SELECT checked_at, source_system, domain, subject, check_name, status, detail
  FROM governance.quality_results.dq_results WHERE status <> 'pass' ORDER BY checked_at DESC LIMIT 20"
#   rows of source_system lake_audit only, checks offset_gap and duplicate_identifiers, status
#   fail: the lake's recorded findings from earlier outages. No dbt row: every test passed.

# Rebuild them now rather than at minute 10 (the DAG runs each domain as its writer).
# Ran on 2026-11-25 in a throwaway container of the same image, before the DAG's rollout.
docker exec k2-airflow-scheduler airflow dags trigger dbt_marts
```


## When something breaks

Start at the [operator guide, section 9](./README.md#9-when-something-looks-wrong); the alert
that fired names its own runbook in the annotation ([index](../runbooks/README.md)). Deeper query recipes are in
[data-inspection.md](./data-inspection.md); dashboards and alert definitions in
[observability.md](./observability.md).
