# v2 ClickHouse → Iceberg offload (archived)

This directory holds the operational runbooks for the v2 cold-tier offload path,
which was deleted in v3 Phase D. Nothing here is maintained, wired to an alert,
or runnable against the current stack — the code, the alert rules, the dashboard
and the tables they all talk about are gone. The runbooks are archived exactly as
they were: their relative links point at the paths they had under `docs/runbooks/`
and were not rewritten, because rewriting them would edit a record of how the
system was operated.

## What it was

Every 15 minutes a Prefect deployment (`iceberg-offload-15min`) shelled into the
shared `spark-iceberg` container and ran `docker/offload/offload_generic.py`. That
script read one ClickHouse table over JDBC above a per-table watermark, appended
the rows to a `cold.*` Iceberg table in a bind-mounted hadoop catalog
(`docker/iceberg/warehouse/`), then advanced the watermark in the PostgreSQL
`offload_watermarks` table. A second deployment (`iceberg-maintenance-daily`)
compacted, expired snapshots and audited row counts at 02:00 UTC. Nine Prometheus
rules watched it, fed by an `iceberg-metrics` exporter that derived every metric
from the watermark table. [ADR-014](../../docs/adr/ADR-014-spark-based-iceberg-offload.md)
is why it was Spark rather than a Kotlin service;
[ADR-017](../../docs/adr/ADR-017-iceberg-maintenance-pipeline.md) is the
maintenance design.

## Why it went

The lake became the system of record instead of a copy of the serving database.
The offload made Iceberg a JDBC snapshot of ClickHouse, so the archive inherited
ClickHouse's normalisation, its 7-day TTL and the JDBC driver's dropped
`Array`/`Map` columns — nothing in it was reproducible from source. v3 reads
Redpanda by offset range straight into Iceberg `raw.messages` (verbatim payloads,
never expired) and derives `bronze.*` from that, with exactly-once carried by the
Iceberg snapshot summary rather than a PostgreSQL watermark row
([ADR-018](../../docs/adr/ADR-018-v3-lake-first-rust-capture.md),
[ADR-021](../../docs/adr/ADR-021-raw-first-archive-and-lineage.md),
[ADR-022](../../docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md),
[ADR-023](../../docs/adr/ADR-023-lakekeeper-rest-catalog.md)).

The plan called for a 2-hour parallel run of both paths before deletion. It was
dropped: the Kotlin feed handlers retired in the preceding PR, which froze the
`k2.*` ClickHouse tables the offload reads, so the comparison would have measured
a frozen watermark against a live ingest. The v2 data is disposable
(`docs/research/2026-08-26-v3-requirements-clarification.md`, Q7). The reasoning
is recorded in the Outcome sections of ADR-014 and ADR-017.

## What replaced each piece

| v2 | v3 |
|---|---|
| `docker/offload/offload_generic.py` | `docker/lake/ingest.py` |
| `docker/offload/iceberg_maintenance.py` | `docker/lake/maintenance.py` |
| `docker/offload/metrics.py` (`iceberg-metrics`) | `docker/lake/metrics.py` (`lake-metrics`) |
| `offload_watermarks` in PostgreSQL | `k2.kafka-offsets` snapshot summary property |
| `docker/iceberg/ddl/`, hadoop catalog | `docker/lake/ddl/lake.sql` on Lakekeeper |
| `iceberg-offload-alerts.yml` (9 rules) | `lake-alerts.yml` (11 rules) |
| `iceberg-offload.json` dashboard | `k2-lake.json` |
| the six runbooks here | [`lake-recovery.md`](../../docs/runbooks/lake-recovery.md), [`lake-ingest-lag.md`](../../docs/runbooks/lake-ingest-lag.md) |
