# ADR-017: Iceberg Daily Maintenance Pipeline Design

**Status:** Accepted — Implemented (2026-02); the PostgreSQL watermark and audit stores it maintains are **superseded by [ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)** for the v3 lake (the compact → expire → audit ordering is kept); implementation deleted 2026-08-27, see Outcome
**Date:** 2026-02-18
**Author:** Principal Data Engineer

---

## Context

The Iceberg cold tier accumulates Parquet data files and snapshot metadata indefinitely. Without maintenance:

- **File fragmentation**: Each 15-minute offload cycle commits ~30-50 small files per table. After 30 days, a single table accumulates 2,880+ files, causing S3 list operations to slow proportionally.
- **Snapshot accumulation**: Iceberg retains every snapshot by default. 96 snapshots/day × 10 tables = 960 snapshot entries/day, with orphaned data files that waste MinIO storage.
- **No integrity signal**: Without a periodic count comparison, a silent offload regression (bug, schema drift, watermark corruption) could go undetected for days.

---

## Decision

**Implement a Prefect 3.x daily maintenance flow** (`iceberg_maintenance_flow.py`) that runs at 02:00 UTC and orchestrates three sequential stages across all 10 cold tables:

1. **Compact** — rewrite fragmented Parquet files (binpack, 128 MB target)
2. **Expire** — remove snapshots older than 7 days, retaining the last 3
3. **Audit** — compare ClickHouse vs Iceberg row counts for the previous 24h UTC window; persist results to PostgreSQL

---

## Key Design Choices

### Sequential per-table execution (not parallel)

All 10 tables are processed sequentially via `ThreadPoolTaskRunner(max_workers=1)`.

**Reason**: Compaction and offload share the same `k2-spark-iceberg` container. Two concurrent Spark sessions double JVM heap pressure with diminishing returns — the bottleneck is I/O to MinIO, not CPU. Sequential execution uses 4 GB heap reliably; concurrent execution risks OOM at 02:00 UTC when the offload flow may still be running.

**Trade-off**: Full maintenance window is ~25–30 minutes (estimated 2-3 min/table for compact, 30s for expire). This fits comfortably before the business day.

### Compact before expire (ordering rationale)

Compaction creates new Iceberg snapshots (committing rewritten files). If expiry ran first, those fresh compacted snapshots would fall within the expire window and be immediately eligible for deletion on the next cycle, defeating the purpose.

**Order**: compact → expire → audit ensures:
1. Freshly compacted snapshots survive the retention window
2. Pre-compaction small-file snapshots are cleaned up
3. Audit validates the result after all maintenance operations

### Audit window: previous 24h UTC (not total counts)

Comparing total row counts is misleading: Iceberg accumulates all historical data while ClickHouse has a 30-day TTL, so Iceberg will always have more total rows. Instead, the audit compares counts for a fixed 24h UTC window (previous calendar day, truncated to whole hours).

**Running at 02:00 UTC** means the audit covers the previous full calendar day (00:00 → 23:59 UTC), with a 2-hour buffer for any late-arriving data.

### Status thresholds

| Delta (iceberg − clickhouse) | Status |
|------------------------------|--------|
| ≤ ±1% | `ok` |
| > ±1% and ≤ −5% (iceberg below CH) | `warning` |
| < −5% (iceberg significantly below CH) | `missing_data` |
| iceberg > clickhouse | `ok` (cold accumulates all history) |

The 5% `missing_data` threshold is deliberately coarse to avoid false positives from minor replication timing differences. A 1-2% delta is expected; >5% indicates a genuine offload gap.

### Failure policy: log-and-continue for tables, fail-fast for audit

- **Compaction/expiry failures**: logged as `status=failed` in the result dict; remaining tables continue. The overall flow status becomes `partial` but does not raise.
- **Audit failures**: if `missing_count > 0` or `error_count > 0`, the flow raises `RuntimeError` → Prefect marks the run as Failed → an operator is alerted.

**Rationale**: A compaction failure is recoverable (files remain readable, just larger). Missing audit data indicates potential data loss and demands immediate investigation.

### Prefect 3.x vs standalone cron

Using Prefect (rather than a systemd cron or simple scheduler) gives:
- Retry-on-failure per task (`retries=1`)
- Run history and log persistence in the Prefect UI
- Consistent deployment pattern with the offload flow
- Alerting via Prefect flow run state (Failed → notification)

---

## Alternatives Considered

| Alternative | Reason Rejected |
|-------------|----------------|
| **Parallel compaction** | Shared Spark JVM, OOM risk at 02:00 UTC |
| **Total row count audit** | Misleading due to ClickHouse TTL vs Iceberg accumulation |
| **Separate maintenance cron (systemd)** | No retry logic, no UI, inconsistent with offload flow pattern |
| **Spark Structured Streaming maintenance** | No benefit for batch-only cold tier; adds runtime complexity |
| **7 snapshots retained** | 3 is sufficient for time-travel use cases; 7 retains 7× the data without benefit |

---

## Implementation

| File | Purpose |
|------|---------|
| `docker/offload/iceberg_maintenance.py` | PySpark script: `compact`, `expire`, `audit` subcommands |
| `docker/offload/flows/iceberg_maintenance_flow.py` | Prefect flow: tasks + sub-flows + main orchestration |
| `docker/offload/flows/deploy_maintenance.py` | Deployment script for `iceberg-maintenance-daily` |
| `docker/offload/flows/prefect.yaml` | Updated with `iceberg-maintenance-daily` deployment |
| `tests/unit/test_iceberg_maintenance_flow.py` | 28 unit tests (all mock subprocess.run or sub-flows) |

---

## Verification

- [x] `uv run pytest tests/unit/test_iceberg_maintenance_flow.py` → 28/28 passing
- [x] `_ALL_TABLES` in flow == `_AUDIT_TABLE_CONFIG` keys in script (enforced by test)
- [x] Deploy schedule: `iceberg-maintenance-daily` deployed via `prefect deploy`, status=READY, cron=`0 2 * * *` UTC
- [x] Run manual audit: completed 2026-02-18 — 20 rows in `maintenance_audit_log` (4 OK, 3 WARNING, 3 MISSING_DATA)
- [x] Confirm `maintenance_audit_log` entries created: bronze/silver tables `status=ok`; gold OHLCV discrepancies expected (ClickHouse background part merges vs Iceberg accumulation)
- [x] Confirm compaction works: `cold.bronze_trades_kraken` — 39 files → 3 files, 2.1 MB rewritten, 0 failures (2026-02-18)

---

## Outcome (2026-08-27)

Deleted with the offload it maintained, in v3 Phase D. The design outlived the
implementation: `docker/lake/maintenance.py` runs the same three stages in the same order
— compact, then expire, then audit — for the same reasons this ADR gives, and the ordering
argument is the part that transferred unchanged. What did not transfer is where the
results live. The audits no longer write to a PostgreSQL `maintenance_audit_log`; they
write to the `audit.checks` Iceberg table alongside the data they check, which removes the
second store this design depended on
([ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)). Compaction is binpack on
`raw.messages` and a sort rewrite on `bronze.*`, and the audits are stronger than the
row-count comparison here: offset continuity per topic/partition, duplicates on the
identifier fields, and sequence gaps.

One prediction in the Context section is worth reading back. It warned that a silent
offload regression could go undetected for days without a periodic count comparison. The
regression that actually mattered was not silent-and-gradual but structural — the archive
was a copy of a serving database — and no row-count audit between the two would ever have
caught it, because both sides agreed. That is the argument for auditing against the source
of record rather than against the tier immediately upstream.

The plan called for a 2-hour parallel run before deletion. It was dropped for the reason
recorded in [ADR-014](ADR-014-spark-based-iceberg-offload.md)'s Outcome. The runbooks are
archived in `legacy/v2-offload/`.
