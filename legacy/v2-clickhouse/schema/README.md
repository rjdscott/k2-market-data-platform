# ClickHouse Schema — Migration History

**Status**: 📚 Historical reference. **Nothing in this directory runs.**

## Where the schema actually comes from

The live bootstrap is a single idempotent file:

```
docker/clickhouse/ddl/01-k2-schema.sql
```

It is mounted at `/docker-entrypoint-initdb.d` (see the `clickhouse` service in
`docker-compose.yml`) and runs automatically on a **fresh** ClickHouse volume,
creating the whole `k2` medallion pipeline in dependency order:

| Layer | Objects |
|---|---|
| Kafka queues | `trades_{binance,kraken,coinbase}_queue` |
| Bronze | `bronze_trades_{binance,kraken,coinbase}` + `bronze_trades_*_mv` |
| Silver | `silver_trades` + `bronze_{exchange}_to_silver_mv` |
| Gold | `ohlcv_{1m,5m,15m,30m,1h,1d}` + `ohlcv_*_mv` |

25 objects total. Every statement is `IF NOT EXISTS`, so re-running it is a no-op.

**If you change the schema, change `ddl/01-k2-schema.sql`.** Do not add files here.

## What this directory is

`01-*.sql` … `12-*.sql` are the migration trail that got the platform from v1 to
the current shape: the `default` → `k2` database move, the v1 → v2 silver/gold
cutover, and per-exchange onboarding (Kraken, then Coinbase). They are kept so the
evolution is auditable, and because a few of them document *why* a column looks
the way it does.

They are **not** a usable bootstrap — several assume tables an earlier manual step
created, some were retro-edited by a global rename, and the `-fixed` variants
predate the v2 bronze cutover. Read them for history, not for truth.

## Related

- `../ddl/` — what actually runs on container init
- `../../lake/ddl/lake.sql` — the Iceberg lake tables. They are **not** derived
  from the DDL here and do not have to match it: v3 Phase D deleted the offload
  that copied ClickHouse into Iceberg, and the lake is now the system of record
  ([ADR-021](../../../docs/adr/ADR-021-raw-first-archive-and-lineage.md))
