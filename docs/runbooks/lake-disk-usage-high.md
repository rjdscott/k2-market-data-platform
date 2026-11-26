# Runbook: Host disk above 80 %: the lake is filling it (archived)

This runbook answered the two lake disk alerts, read from the v3 lake's metrics exporter, with a
capacity decision rather than a deletion. It was archived on 2026-11-26 with the v3 lake ingest
pipeline it operated, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/runbooks/lake-disk-usage-high.md`](../../legacy/v3-lake-ingest/docs/runbooks/lake-disk-usage-high.md);
its alerts were removed and its commands read tables, DAGs and scripts that no longer exist, so do
not follow it against this stack. The lake metrics and alerts return in [phase
004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)); until
then `df -h /` on the host is the check, and [docker-desktop-disk.md](./docker-desktop-disk.md)
covers a bloated Docker disk image.
