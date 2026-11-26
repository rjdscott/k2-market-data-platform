# Runbook: An ingest batch does not match its planned offsets (archived)

This runbook handled a v3 ingest batch whose committed rows did not match the offset range it read.
It was archived on 2026-11-26 with the v3 lake ingest pipeline it operated, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/runbooks/lake-ingest-batch-audit.md`](../../legacy/v3-lake-ingest/docs/runbooks/lake-ingest-batch-audit.md);
its alerts were removed and its commands read tables, DAGs and scripts that no longer exist, so do
not follow it against this stack. Exactly-once reads from Redpanda return with the Kafka connector
in [phase
004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)), whose
exit includes a killed connector resuming exactly once.
