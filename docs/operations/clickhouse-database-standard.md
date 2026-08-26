# ClickHouse Database Standard

**All K2 platform tables live in the `k2` database.** Nothing belongs in `default`.

```
Decision 2026-02-12: Standardise on the `k2` ClickHouse database
Reason: named database gives isolation, database-level grants and `BACKUP DATABASE k2`
Cost: one-off rename of ~1.1M rows from `default.*`, plus a docs sweep
Alternative considered: stay on `default` (rejected — no ownership boundary)
```

## Tables

| Layer | Tables | Fed by |
|-------|--------|--------|
| Kafka Engine | one `*_queue` table per exchange | Redpanda `market.crypto.trades.<exchange>.raw` |
| Bronze | `k2.bronze_trades_{binance,kraken,coinbase}` | a normalizing MV that parses the raw JSON |
| Silver | `k2.silver_trades` | `k2.bronze_{exchange}_to_silver_mv`, one per exchange |
| Gold | `k2.ohlcv_{1m,5m,15m,30m,1h,1d}` | `k2.ohlcv_<tf>_mv`, reading `silver_trades.timestamp` |

Queue-table and bronze-MV names are not uniform across exchanges — they accreted as each
exchange was added. `SHOW TABLES FROM k2` is the authority; the silver and gold names
above are consistent.

Column-level detail is in [data-inspection.md](./data-inspection.md#schema-cheat-sheet).

## Rules

- Always qualify: `SELECT … FROM k2.silver_trades`, never the bare table name. The
  container sets `CLICKHOUSE_DB: k2`, but scripts and JDBC sessions do not always inherit it.
- New tables and materialized views are created **in `k2`**, including queue tables.
- Application config passes the database explicitly:

  ```python
  client = clickhouse_connect.get_client(host="clickhouse", database="k2")
  ```

- Credentials come from the environment (`CLICKHOUSE_PASSWORD`), never hardcoded.
  See [`.env.example`](../../.env.example).

## Schema files

DDL lives in [`docker/clickhouse/schema/`](../../docker/clickhouse/schema/), numbered in
the order it was applied. Files are **not** auto-run on container start — only
[`docker/clickhouse/ddl/`](../../docker/clickhouse/ddl/) is mounted into
`/docker-entrypoint-initdb.d`. A fresh volume needs the schema applied by hand; see
[../development/setup.md](../development/setup.md).

Some numbered files exist in both a plain and a `-fixed` variant, and a few early ones
target `default`. The `k2`-qualified files are the ones that reflect production. The
per-exchange bronze files (`11-bronze-coinbase.sql` and friends) are the cleanest
reference for the current pattern.

## Verification

```bash
CH="docker exec k2-clickhouse clickhouse-client --password $CLICKHOUSE_PASSWORD"

$CH -q "SHOW DATABASES"
$CH -q "SHOW TABLES FROM k2"

# Nothing should have leaked into `default`
$CH -q "SELECT table, total_rows FROM system.tables
        WHERE database = 'default' AND total_rows > 0"

# Row counts per layer
$CH -q "SELECT table, total_rows FROM system.tables
        WHERE database = 'k2' AND engine NOT LIKE '%View' AND total_rows > 0
        ORDER BY table"
```

## Related

- [ADR-009 — medallion architecture in ClickHouse](../decisions/ADR-009-medallion-in-clickhouse.md)
- [ADR-011 — multi-exchange bronze architecture](../decisions/ADR-011-multi-exchange-bronze-architecture.md)
- [ADR-015 — ClickHouse 24.3 LTS downgrade](../decisions/ADR-015-clickhouse-lts-downgrade.md)
- [data-inspection.md](./data-inspection.md) — queries against these tables
