# Runbook: Lake ingest is behind (archived)

This runbook recovered the v3 lake ingest when it fell behind Redpanda: a stopped scheduler, a
data-loss refusal, small files piling up. It was archived on 2026-11-26 with the v3 lake ingest
pipeline it operated, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/runbooks/lake-ingest-lag.md`](../../legacy/v3-lake-ingest/docs/runbooks/lake-ingest-lag.md);
its alerts were removed and its commands read tables, DAGs and scripts that no longer exist, so do
not follow it against this stack. No live crypto data reaches the lake until the Kafka connector
lands `kraken-trades` in [phase
004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md));
[connector-failure.md](./connector-failure.md), written in [phase
002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) for the file
products, and the Kafka runbooks of phase 004 replace it.
