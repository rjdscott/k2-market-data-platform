# legacy/v2-clickhouse — the v2 ClickHouse medallion, retired

The `k2` database that v2 ran (Kafka-engine queues over the `.raw` JSON topics →
per-exchange bronze → `silver_trades` → `SummingMergeTree` OHLCV) was dropped at
the v3 Phase E cutover on 2026-08-27 ([ADR-026](../../docs/adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md),
[plan 004](../../docs/plans/2026-08-26-v3-quant-research-platform/004-phase-e-hot-tier.md)).
Nothing had produced to its topics since the Kotlin handlers retired (ADR-019);
the v3 served tier is `docker/clickhouse/ddl/10-gold-tables.sql`.

| Path | What it was |
|---|---|
| `01-k2-schema.sql` | the as-built bootstrap: queues, normalising MVs, bronze per exchange, silver, gold candles |
| `schema/` | the v1 → v2 migration trail, per-exchange onboarding |
| `validation/` | the Kraken integration checks |

Kept verbatim and unexecuted. The OHLCV post-mortem it embodies — `argMin`/`argMax`
resolved per insert block, so a minute spanning two blocks kept whichever block's open
survived the merge — is what `gold.ohlcv_live` and the lake's `gold.ohlcv_*` are built
not to repeat (`docker/clickhouse/README.md`).
