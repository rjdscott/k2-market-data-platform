# legacy/

Archived components of earlier K2 generations. Each directory is kept so the migration story ([`docs/MIGRATION-JOURNEY.md`](../docs/MIGRATION-JOURNEY.md)) and the decisions behind it can be checked against the code they describe. The rule: an archive is moved here with its source unchanged (v1 has had dependency pins bumped since, see its README), and is runnable only where its own README says so. Nothing here is part of `make test` or CI unless its README states it; today only `make test-legacy-kotlin` runs anything, and it is outside both. Every archive has the same six sections in its README: what it was, why it was archived, what replaced each piece, state at archive, running or verifying it today, known residue.

| Directory | What it was | Archived on | Decided by | Replaced by |
|---|---|---|---|---|
| [`v1/`](v1/README.md) | the Python / Kafka / Spark Streaming / DuckDB / FastAPI platform | 2026-08-26 (`ef89133`) | [ADR-001](../docs/adr/ADR-001-replace-kafka-with-redpanda.md) to [ADR-010](../docs/adr/ADR-010-resource-budget.md), [MIGRATION-JOURNEY](../docs/MIGRATION-JOURNEY.md) | v2, then v3 (see its README) |
| [`v2-kotlin/`](v2-kotlin/README.md) | three Kotlin/Ktor exchange feed handlers | 2026-08-27 (`2b20804`; ADR-019 dates it 2026-08-26) | [ADR-019](../docs/adr/ADR-019-rust-capture-tier.md) | [`services/capture-rust/`](../services/capture-rust/) |
| [`v2-offload/`](v2-offload/README.md) | the ClickHouse to Iceberg offload (runbooks only) | 2026-08-27 (`e2a2651`) | [ADR-014](../docs/adr/ADR-014-spark-based-iceberg-offload.md) and [ADR-017](../docs/adr/ADR-017-iceberg-maintenance-pipeline.md) Outcomes, [ADR-022](../docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md) | [`docker/lake/`](../docker/lake/) |
| [`v2-clickhouse/`](v2-clickhouse/README.md) | the `k2` ClickHouse medallion DDL | 2026-08-27 (`9011d94`) | [ADR-025](../docs/adr/ADR-025-clickhouse-derived-hot-tier.md), [ADR-026](../docs/adr/ADR-026-four-layer-lake-and-gold-served-from-clickhouse.md) | [`docker/clickhouse/ddl/`](../docker/clickhouse/ddl/) (`gold`) |
| [`prefect/`](prefect/README.md) | the Prefect 3 worker image and the two lake flows that scheduled ingest and maintenance | 2026-11-26 (the cutover before it: 2026-11-25, #195 `c572727`) | [ADR-034](../docs/adr/ADR-034-airflow-3-replaces-prefect.md) (and its Outcome), [ADR-008](../docs/adr/ADR-008-eliminate-prefect-orchestration.md) Outcome for why it was kept first | [`docker/airflow/`](../docker/airflow/) (Airflow 3) |

## Adding an archive

1. Move the code in its own commit, with no edits to the moved files.
2. Write `legacy/<name>/README.md` with these six sections, in this order: `What it was`, `Why it was archived`, `What replaced each piece`, `State at archive`, `Running or verifying it today`, `Known residue`. Every date, number and cause cites an ADR, a commit hash or a doc; a fact you cannot find is written as "not recorded".
3. State the exact command that verifies it today, or say nothing is runnable. Read it from the Makefile; do not paraphrase from memory.
4. Add a row to the table above and link the deciding ADR (and its Outcome section if reality diverged).
5. If it is runnable, add a Makefile target outside `make test` and say so; if it has a lockfile, decide whether CI's `skip-dirs: legacy` and Dependabot cover it and record the residue.
6. Run `make check-docs`.
