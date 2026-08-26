# Phase E — Hot tier (ClickHouse, ~1 week)

**Depends on:** Phase D
**Delivers:** Rebuilds ClickHouse as a derived `hot` tier (trades, book, BBO, OHLCV) fed from Redpanda and reloadable from the lake, replacing `k2`.
**Exit:** Cutover: dashboards repointed to `hot`; verify `hot.ohlcv` == DuckDB-over-Iceberg for same window; drop `k2` db + old queue MVs; delete `.raw` JSON topics.

## Scope

- New database `hot` built alongside `k2`; DDL split `docker/clickhouse/ddl/{01-tables.sql,02-kafka.sql}` (CI applies 01 only).
- `hot.q_trades` Kafka engine `AvroConfluent` over 3 trade topics (2 consumers, thread_per_consumer) → MV → `hot.trades` `ReplacingMergeTree(recv_ts)` `ORDER BY (exchange, canonical_symbol, exchange_ts, trade_id)` `PARTITION BY toDate(exchange_ts)` TTL 7d; `kafka_partition/offset` from virtual columns if S4 passes else omit. `hot.q_book` → `hot.book_top20_1s` `ReplacingMergeTree(recv_ts)` `ORDER BY (exchange, canonical_symbol, second)` with `Array(Float64)` levels. Views: `hot.bbo_1s` (bid/ask/mid/spread_bps/imbalance/microprice), `hot.ohlcv` parameterized view over `hot.trades FINAL` (argMin/argMax/max/min/sum/count) — **compute on read**; materialize `ohlcv_1m` (AggregatingMergeTree) only if measured 7-day p99 > 1 s.
- `config.xml`: `max_memory_usage` 6 GB, explicit `max_server_memory_usage` 6.5 GB, `max_concurrent_queries` 32, `background_pool_size` 8; `users.xml` readonly `quant` profile with `do_not_merge_across_partitions_select_final=1` (mount line already in compose, commented). Grafana/notebooks use `quant`.
- CI `clickhouse-schema` job: `docker run clickhouse:24.3-alpine` with `01-tables.sql`, JSONEachRow fixtures, asserts: OHLCV correct across two insert blocks (regression for v2 bug), FINAL count == distinct, book downsample keeps latest, BBO math.
- Cutover: dashboards repointed to `hot`; verify `hot.ohlcv` == DuckDB-over-Iceberg for same window; drop `k2` db + old queue MVs; delete `.raw` JSON topics.
- `clickhouse-alerts.yml` + `ClickHouseDedupBacklog`, `ClickHouseFinalQuerySlow`; `clickhouse-overview.json` hot-tier row.

## Verification

- Every phase: `make test` (rust/python/clickhouse-schema), CI green, `docker compose up -d --build` from clean clone → all services healthy.
- Hot: CI schema tests; `hot.ohlcv` matches DuckDB over Iceberg; FINAL vs non-FINAL counts; rebuild `hot.*` from lake timed.
