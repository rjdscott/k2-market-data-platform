# ClickHouse Database Standard

**All served K2 tables live in the `gold` database.** Nothing belongs in `default`.

> The `k2` database this page standardised on from 2026-02-12 was **dropped at the v3
> Phase E cutover on 2026-08-27**. Its DDL is kept, unexecuted, in
> [`legacy/v2-clickhouse/`](../../legacy/v2-clickhouse/README.md); the current tier is
> [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md).

```
Decision 2026-02-12: Standardise on a named ClickHouse database (`k2`), never `default`
Reason: named database gives isolation, database-level grants and `BACKUP DATABASE`
Alternative considered: stay on `default` (rejected — no ownership boundary)

Decision 2026-08-27: `gold` replaces `k2` (ADR-026). Same rule, new name: the database is
the served layer's name, and the `quant` user is granted `gold` and nothing else.
```

## Tables

| Layer | Objects | Fed by |
|-------|---------|--------|
| Feeds (Kafka engine) | `gold.q_trades`, `gold.q_book` | `market.crypto.v3.trades.<ex>`, `market.crypto.v3.book.<ex>` (AvroConfluent) |
| Served | `gold.trades`, `gold.book_top20` (`ReplacingMergeTree`) | `gold.q_trades_mv`, `gold.q_book_mv`; reloadable from the lake |
| Rejects | `gold.feed_errors` | `gold.q_trades_errors_mv`, `gold.q_book_errors_mv` |
| Lake-loaded | `gold.ohlcv_{1m,5m,1h,1d}`, `gold.bbo_1s` | a pull from the lake's `gold.*` — [clickhouse-rebuild-from-lake.md](../runbooks/clickhouse-rebuild-from-lake.md) |
| Views | `gold.ohlcv_live(bucket = <seconds>)`, `gold.bbo_live` | computed on read over `FINAL` |

`SHOW TABLES FROM gold` is the authority. Column-level detail is in
[data-inspection.md](./data-inspection.md#schema-cheat-sheet).

## Rules

- Always qualify: `SELECT … FROM gold.trades FINAL`, never the bare table name. Scripts
  and JDBC sessions do not inherit a default database.
- Exact reads on `gold.trades` / `gold.book_top20` use `FINAL`; a count without it is a
  count of deliveries, not of trades.
- New tables, views and materialized views are created **in `gold`**, including the
  Kafka-engine feed tables.
- Research reads use the read-only `quant` user (`K2_QUANT_PASSWORD`), which sees `gold`
  only. Credentials come from the environment, never hardcoded — see
  [`.env.example`](../../.env.example).

## Schema files

[`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql)
is the contract and the only DDL CI applies (`make test-clickhouse`);
[`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql) attaches the feeds at
boot. Both auto-apply on a fresh volume via `/docker-entrypoint-initdb.d`; applying them to
a running server is in [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md).

## Verification

Run on 2026-08-27 against the post-cutover stack.

```bash
CH="docker exec k2-clickhouse clickhouse-client --password $CLICKHOUSE_PASSWORD"

$CH -q "SHOW DATABASES"                 # INFORMATION_SCHEMA default gold information_schema system
$CH -q "SHOW TABLES FROM gold"

# Nothing should have leaked into `default`
$CH -q "SELECT table, total_rows FROM system.tables
        WHERE database = 'default' AND total_rows > 0"

# Row counts per table
$CH -q "SELECT table, total_rows FROM system.tables
        WHERE database = 'gold' AND engine NOT LIKE '%View' AND total_rows > 0
        ORDER BY table"

# Trades, deduplicated
$CH -q "SELECT count() FROM gold.trades FINAL"
```

`default.offload_watermarks` — the v2 offload's watermark table, orphaned when `docker/offload/`
was deleted in Phase D and read by nothing — was found by this check on 2026-08-27 and dropped
the same day (`DROP TABLE default.offload_watermarks`); the leak check is clean.

## Related

- [ADR-026 — four-layer lake, gold served from ClickHouse](../adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)
- [ADR-025 — ClickHouse derived and rebuildable](../adr/ADR-025-clickhouse-derived-hot-tier.md)
- [ADR-015 — ClickHouse 24.3 LTS downgrade](../adr/ADR-015-clickhouse-lts-downgrade.md)
- [data-inspection.md](./data-inspection.md) — queries against these tables
