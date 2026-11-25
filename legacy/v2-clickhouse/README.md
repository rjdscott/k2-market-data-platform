# legacy/v2-clickhouse: the v2 ClickHouse medallion, retired

The DDL, migration trail and validation SQL of the `k2` ClickHouse database that v2 ran. Kept verbatim and unexecuted. See [`../README.md`](../README.md) for the archive rules.

## What it was

The v2 serving and transformation tier ([ADR-003](../../docs/adr/ADR-003-clickhouse-warm-storage.md), [ADR-009](../../docs/adr/ADR-009-medallion-in-clickhouse.md)). ClickHouse Kafka-engine queue tables read the exchange JSON from the `.raw` Redpanda topics; a normalising materialized view wrote per-exchange bronze tables (`bronze_trades_{binance,kraken,coinbase}`), cascading MVs fed `silver_trades`, and further MVs built `SummingMergeTree` OHLCV tables for six timeframes (1m, 5m, 15m, 30m, 1h, 1d). As built it was three layers, not four, with no raw layer in ClickHouse ([ADR-009](../../docs/adr/ADR-009-medallion-in-clickhouse.md) Outcome).

- **Inputs:** the three `market.crypto.trades.<exchange>.raw` topics, written by the Kotlin feed handlers ([`../v2-kotlin/`](../v2-kotlin/README.md)).
- **Outputs:** `k2.*` tables read by the offload ([`../v2-offload/`](../v2-offload/README.md)) and by ad hoc queries.
- **When it ran:** ADR-003 and ADR-009 record it implemented in 2026-02 ([MIGRATION-JOURNEY](../../docs/MIGRATION-JOURNEY.md) timeline puts the ClickHouse foundation on 2026-02-10 and the MV pipeline on 2026-02-11); it was dropped on 2026-08-27 (below). The ClickHouse version was 24.3 LTS ([ADR-015](../../docs/adr/ADR-015-clickhouse-lts-downgrade.md)).

| Path | What it was |
|---|---|
| `01-k2-schema.sql` | the as-built bootstrap: queues, normalising MVs, bronze per exchange, silver, gold candles |
| `schema/` | the v1 to v2 migration trail, per-exchange onboarding (its own `README.md` says nothing in it runs) |
| `validation/` | the Kraken integration checks |

## Why it was archived

Two causes, in order.

1. **Its producers stopped.** The Kotlin handlers retired in [ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md) (Outcome, "Retirement"). They were the only writers of the `.raw` topics, so `k2.*` stopped gaining rows and its TTLs kept expiring what was left.
2. **The design was superseded.** v3 makes ClickHouse a derived, rebuildable hot tier fed straight from Redpanda, and the lake the system of record ([ADR-025](../../docs/adr/ADR-025-clickhouse-derived-hot-tier.md), [ADR-026](../../docs/adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md); status lines of ADR-009 and ADR-003 record which clauses are superseded). The `k2` database was dropped at the Phase E cutover on 2026-08-27 ([plan 004](../../docs/plans/2026-08-26-v3-quant-research-platform/004-phase-e-hot-tier.md)). `9011d94`'s message records it: `DROP DATABASE k2`, `CLICKHOUSE_DB=gold`, the v2 DDL moved here, the six v2 topics deleted. The v2 data was treated as disposable ([ADR-014](../../docs/adr/ADR-014-spark-based-iceberg-offload.md) Outcome cites research Q7).

The OHLCV post-mortem it embodies, that `argMin`/`argMax` resolved per insert block so a minute spanning two blocks kept whichever block's open survived the merge, is what `gold.ohlcv_live` and the lake's `gold.ohlcv_*` are built not to repeat (see [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md)).

## What replaced each piece

| v2 | Successor |
|---|---|
| `01-k2-schema.sql` (queues, bronze, silver, OHLCV MVs) | [`docker/clickhouse/ddl/10-gold-tables.sql`](../../docker/clickhouse/ddl/10-gold-tables.sql) and [`20-gold-kafka.sql`](../../docker/clickhouse/ddl/20-gold-kafka.sql) |
| `k2` database | `gold` database, served indefinitely from ClickHouse ([ADR-026](../../docs/adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md)) |
| per-exchange bronze and `silver_trades` in ClickHouse | lake `bronze.*` and `silver.*` per venue, in [`docker/lake/ddl/lake.sql`](../../docker/lake/ddl/lake.sql) |
| `schema/` migration trail | none: the live DDL is a single bootstrap; history lives in git and the ADRs |
| `validation/validate-kraken-integration.sql` | none: the gold DDL has its own assertions under `make test-clickhouse` |

## State at archive

Moved here in `9011d94` ("feat(phase-e): cutover", #102, 2026-08-27), first contained in tag `v3-phase-e`. `01-k2-schema.sql` was a pure rename from `docker/clickhouse/ddl/` (0 lines changed in the commit's stat). `git diff --stat 9011d94 HEAD -- legacy/v2-clickhouse` is empty, so the archived copy is unmodified.

## Running or verifying it today

Nothing is runnable. The files are not executed by `make test`, CI or any compose service, and the `k2` database they create no longer exists on the live server. `make test-clickhouse` tests the `gold` DDL only, not this directory. Executing `01-k2-schema.sql` against the live server would recreate Kafka-engine queues on topics that were deleted.

## Known residue

- `schema/README.md` is stale on purpose and was not rewritten: it still says the live bootstrap is `docker/clickhouse/ddl/01-k2-schema.sql`, which moved here.
- Live-tree mentions of this path: `CLAUDE.md`, `docs/architecture/10-clickhouse-gold.md`, `13-schema-design.md`, `14-partitioning-strategy.md`, `docs/operations/clickhouse-database-standard.md`, `data-inspection.md`, `quick-reference.md`, `docs/development/setup.md`, `docs/runbooks/redpanda.md`.
- No dependencies, so no advisories.
