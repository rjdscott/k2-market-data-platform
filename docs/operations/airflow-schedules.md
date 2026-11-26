# Airflow Schedules

Airflow 3 is the only scheduler ([ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md)).
Four DAGs, all in `docker/airflow/dags/`, none paused on creation, none catching up a missed
slot, one run of each at a time. Times are UTC.

| DAG | Cron | What it runs | Limits | Runbook |
|-----|------|--------------|--------|---------|
| `lake_ingest` | `1-59/5 * * * *` | `docker/lake/ingest.py` in `k2-spark-iceberg`: Redpanda to `raw.messages` to bronze, silver and gold | task 3600 s, one retry; exit 75 (lock held) is skipped | [airflow-scheduling.md](../runbooks/airflow-scheduling.md) |
| `lake_maintenance` | `0 3 * * *` | `docker/lake/maintenance.py`: compact, expire at 7 days, remove orphans, audit, every lake table | task 7200 s, no retry | [lake-audit-failed.md](../runbooks/lake-audit-failed.md) |
| `lake_rebuild` | by hand | `docker/lake/rebuild.py` for one layer; a dry run unless told otherwise | 24 h | [airflow-scheduling.md](../runbooks/airflow-scheduling.md) |
| `dbt_marts` | `10 * * * *` | the dbt marts on Trino, one task group per domain run as `dbt_<domain>` (`dbt/selectors.yml`), with source freshness and tests; in the 04:10 run, each domain's snapshot expiry at 7 days | run 50 min, task 20 min, no retry, at most 2 tasks at once | [dbt-marts.md](../runbooks/dbt-marts.md) |

**File sources** get one generated DAG each, `<table>_ingest`, built by
`docker/airflow/dags/factory.py` from `docker/airflow/dags/file_sources.json`, which
`make generate` writes from each contract's `fileSource` property
([ADR-036](../adr/ADR-036-file-sources-land-unchanged-then-publish-after-audit.md)). None is
declared yet, so the file is `{}` and the factory builds no DAG. Each would be four tasks in a
line, at its contract's cron: `fetch` (5 min, two retries), `load`, `audit` and `publish` (20 min
each, which covers up to 600 s waiting for the writer lock; one retry, none for `audit`), each
one subcommand of `docker/lake/file_source.py`. A failed audit stops the run before `publish`;
runbook [file-source-audit-failed.md](../runbooks/file-source-audit-failed.md).

`dbt_marts` runs at minute 10, off the ingest's minutes 1, 6, 11 and the rest, and its two-task
cap leaves two of the scheduler's four LocalExecutor slots (`AIRFLOW__CORE__PARALLELISM`) to the
lake. Its maintenance hour, 04:00, comes after `lake_maintenance`'s 03:00 start.

What the scheduler parsed, from a throwaway container of the image with this repository's
`docker/airflow/dags` and `dbt/` mounted, 2026-11-25 (the DAGs paused there, as nothing in it
could reach the Spark container or should build twice):

```bash
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list -o plain 2>/dev/null | awk '{print $1}'
#   dag_id
#   dbt_marts
#   lake_ingest
#   lake_maintenance
#   lake_rebuild
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags details dbt_marts -o plain 2>/dev/null \
  | grep -E '^(timetable_summary|max_active_tasks|max_active_runs) '
#   timetable_summary                10 * * * *
#   max_active_tasks                 2
#   max_active_runs                  1
```

The DAG files are a read-only directory bind mount in the scheduler and the dag-processor, and
so is `dbt/` for `dbt_marts`: after a checkout switch, recreate both (CLAUDE.md, bind-mount
gotcha). The scheduler these replaced is archived in
[`legacy/prefect/`](../../legacy/prefect/README.md).
