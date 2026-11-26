# Runbook: Rebuild ClickHouse gold from the lake (archived)

This runbook reloaded ClickHouse `gold` from the v3 lake's gold tables. It was archived on
2026-11-26 with the v3 lake ingest pipeline it operated, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/runbooks/clickhouse-rebuild-from-lake.md`](../../legacy/v3-lake-ingest/docs/runbooks/clickhouse-rebuild-from-lake.md);
its alerts were removed and its commands read tables, DAGs and scripts that no longer exist, so do
not follow it against this stack. The lake's gold went with the pipeline, so nothing replaces it: a
resume of the paused hot tier reads Redpanda only, within 7 days
([pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md),
[ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)), and canonical trades
return to the lake as a product in [phase
004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)).
