# Data inspection (archived)

This page held the operator's health queries for each hop of the data: Redpanda, ClickHouse
`gold` and the v3 Iceberg lake. It was archived on 2026-11-26 with the v3 lake ingest pipeline
whose tables it queried, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/operations/data-inspection.md`](../../legacy/v3-lake-ingest/docs/operations/data-inspection.md);
those lake tables were dropped, so its lake queries no longer run. The Redpanda and capture
checks that still apply are in [quick-reference.md](./quick-reference.md). Checks over the lake
return with the first live product, as `make product-verify`, in
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)).
