# Airflow schedules (archived)

This page listed the four Airflow DAGs of the v3 lake (the ingest, the nightly maintenance, the
rebuild and the hourly dbt marts), with their crons, limits and runbooks. It was archived on
2026-11-26 with the v3 lake ingest pipeline those DAGs ran, and is kept unchanged at
[`legacy/v3-lake-ingest/docs/operations/airflow-schedules.md`](../../legacy/v3-lake-ingest/docs/operations/airflow-schedules.md).
Airflow still runs, with no DAG. What replaces the four is one DAG per product, generated from
its contract and pinned to one release, from
[phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) of the
unified platform plan ([ADR-056](../adr/ADR-056-unified-data-product-platform.md)).
