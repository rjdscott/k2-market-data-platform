# Runbook: Recover the lake tier, and rebuild the hot tier from it (archived)

This runbook recovered the v3 lake (Lakekeeper or MinIO down, an ingest killed mid-run, a replay
from Redpanda) and rebuilt ClickHouse from the lake. It was archived on 2026-11-26 with the v3 lake
ingest pipeline it operated, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/runbooks/lake-recovery.md`](../../legacy/v3-lake-ingest/docs/runbooks/lake-recovery.md);
its alerts were removed and its commands read tables, DAGs and scripts that no longer exist, so do
not follow it against this stack. What replaces it is the backup, restore and rebuild-from-empty
runbook, with `make rebuild` and `make restore`, in [phase
004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)).
