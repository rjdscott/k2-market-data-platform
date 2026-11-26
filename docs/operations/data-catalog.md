# Data catalog (archived)

This page described every table of the v3 lake and of the ClickHouse hot tier: grain,
partitioning, time columns, dedup keys and one runnable example each. It was archived on
2026-11-26 with the v3 lake ingest pipeline whose tables it described, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/operations/data-catalog.md`](../../legacy/v3-lake-ingest/docs/operations/data-catalog.md);
those lake tables were dropped, so its lake queries no longer run. What replaces it is generated
from each product's contract ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)): the
pages `make generate` writes under `docs/products/`, and the catalog site
served by `k2-api` from
[phase 003](../plans/2026-11-26-unified-product-platform/003-serving-and-teaching.md) of the
unified platform plan.
