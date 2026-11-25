# v2 ClickHouse to Iceberg offload (archived)

This directory holds only the operational runbooks of the v2 cold-tier offload path. The code, alert rules, dashboard and tables they describe were deleted in v3 Phase D. Nothing here is maintained or wired to an alert. The runbooks are archived exactly as they were: their relative links point at the paths they had under `docs/runbooks/` and were not rewritten, because rewriting them would edit a record of how the system was operated. See [`../README.md`](../README.md) for the archive rules.

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

- **Inputs:** the ClickHouse `k2.*` tables ([`../v2-clickhouse/`](../v2-clickhouse/README.md)), which the Kotlin handlers fed ([`../v2-kotlin/`](../v2-kotlin/README.md)).
- **Outputs:** `cold.*` Iceberg tables and the `offload_watermarks` rows.
- **When it ran:** the cold tier shipped in the 2026-02-12 to 2026-02-18 window ([MIGRATION-JOURNEY](../../docs/MIGRATION-JOURNEY.md) timeline, Phase 5). [ADR-014](../../docs/adr/ADR-014-spark-based-iceberg-offload.md) Outcome says it "ran as designed for six months" and was deleted on 2026-08-27. The exact first-run date is not recorded.

## Why it was archived

The lake became the system of record instead of a copy of the serving database.
The offload made Iceberg a JDBC snapshot of ClickHouse, so the archive inherited
ClickHouse's normalisation, its 7-day TTL and the JDBC driver's dropped
`Array`/`Map` columns, and nothing in it was reproducible from source. v3 reads
Redpanda by offset range straight into Iceberg `raw.messages` (verbatim payloads,
never expired) and derives `bronze.*` from that, with exactly-once carried by the
Iceberg snapshot summary rather than a PostgreSQL watermark row
([ADR-018](../../docs/adr/ADR-018-v3-lake-first-rust-capture.md),
[ADR-021](../../docs/adr/ADR-021-raw-first-archive-and-lineage.md),
[ADR-022](../../docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md),
[ADR-023](../../docs/adr/ADR-023-lakekeeper-rest-catalog.md)). The mechanism itself was not the problem: the watermark held and killed runs resumed without duplicates ([ADR-014](../../docs/adr/ADR-014-spark-based-iceberg-offload.md) Outcome).

The plan called for a 2-hour parallel run of both paths before deletion. It was
dropped: the Kotlin feed handlers retired in the preceding PR, which froze the
`k2.*` ClickHouse tables the offload reads, so the comparison would have measured
a frozen watermark against a live ingest. The v2 data is disposable
([`docs/research/2026-08-26-v3-requirements-clarification.md`](../../docs/research/2026-08-26-v3-requirements-clarification.md), Q7). The reasoning
is recorded in the Outcome sections of [ADR-014](../../docs/adr/ADR-014-spark-based-iceberg-offload.md) and [ADR-017](../../docs/adr/ADR-017-iceberg-maintenance-pipeline.md).
Deleted with it, per ADR-014's Outcome: `docker/offload/`, `docker/iceberg/`, the `offload_watermarks` table, the `iceberg-metrics` and `iceberg-init` compose services, 9 alert rules, one Grafana dashboard, 28 tests, and the `clickhouse-jdbc` and `psycopg2` dependencies from the Spark image.

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

## State at archive

The runbooks were added in `e2a2651` ("feat(v3): Phase D", #77, 2026-08-27), contained in tag `v3-phase-d`; the same commit deleted everything else listed above. `git diff --stat e2a2651 HEAD -- legacy/v2-offload` is empty, so the archived copy is unmodified.

## Running or verifying it today

Nothing is runnable. There are no scripts here, only six markdown runbooks (`iceberg-offload-failure`, `-lag`, `-monitoring`, `-performance`, `-watermark-recovery`, `iceberg-scheduler-recovery`), and every system they operate was deleted. The commands inside them target `spark-iceberg`, Prefect deployments and a PostgreSQL table that no longer exist. Use the live runbooks in the replacement table instead.

## Known residue

- The runbooks' internal links are broken by design (see the opening paragraph).
- Live-tree mentions: `CLAUDE.md`, `docs/MIGRATION-JOURNEY.md`, `docs/operations/observability.md`, `docs/operations/README.md`, `docs/runbooks/README.md`, ADR-014, ADR-017, ADR-019, ADR-022 and the Phase D plan.
- No dependencies, so no advisories.
