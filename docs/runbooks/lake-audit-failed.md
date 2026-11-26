# Runbook: A lake audit failed (archived)

This runbook investigated a failed nightly audit of the v3 lake: offset continuity, duplicate
identifiers and venue sequence gaps. It was archived on 2026-11-26 with the v3 lake ingest pipeline
it operated, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/runbooks/lake-audit-failed.md`](../../legacy/v3-lake-ingest/docs/runbooks/lake-audit-failed.md);
its alerts were removed and its commands read tables, DAGs and scripts that no longer exist, so do
not follow it against this stack. What replaces it is the failed-audit runbook of [phase
002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) of the unified
platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)), over each product's own
checks and run records.
