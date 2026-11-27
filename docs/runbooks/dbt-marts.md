# Runbook: The dbt marts are not being rebuilt (archived)

This runbook brought back the hourly dbt marts of the v3 lake when they stopped succeeding. It was
archived on 2026-11-26 with the v3 lake ingest pipeline it operated, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/runbooks/dbt-marts.md`](../../legacy/v3-lake-ingest/docs/runbooks/dbt-marts.md);
its alerts were removed and its commands read tables, DAGs and scripts that no longer exist, so do
not follow it against this stack. What replaces the marts' schedule is the dbt runner and its
wrapper, with run records, built in [phase
002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) of the unified
platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)): a product's `build`
task in its own DAG, [airflow-scheduling.md](./airflow-scheduling.md) section 2.
