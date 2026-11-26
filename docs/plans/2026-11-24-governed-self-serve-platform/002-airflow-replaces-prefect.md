# Phase P2: Airflow 3 replaces Prefect

**Depends on:** P0 (exit code 75, budget gate)
**Decision it implements:** ADR-034, written in this phase. It supersedes the orchestration clauses of [ADR-008](../../adr/ADR-008-eliminate-prefect-orchestration.md), [ADR-014](../../adr/ADR-014-spark-based-iceberg-offload.md) and [ADR-017](../../adr/ADR-017-iceberg-maintenance-pipeline.md), the "Prefect is retained" clause of [ADR-018](../../adr/ADR-018-v3-lake-first-rust-capture.md), and the 16 CPU clause of [ADR-010](../../adr/ADR-010-resource-budget.md). It leaves [ADR-022](../../adr/ADR-022-exactly-once-via-snapshot-offsets.md) untouched: the ingest code path does not change.
**Delivers:** the lake ingest and maintenance scheduled by Airflow 3, with Prefect archived to `legacy/prefect/`.
**Exit:** `make gates` passes with Airflow as the only scheduler; 24 consecutive scheduled `lake_ingest` runs succeeded with no unexpected skip; the Prefect grep below returns only the files listed under "What keeps the name"; ADR-034 carries measured CPU and memory.

## Scope

**Pull request 1: Airflow beside Prefect, ADR-034**

- Image `docker/airflow/Dockerfile`: the newest Airflow 3 release that astronomer-cosmos
  supports outside beta, pinned, with `docker-cli`. Services: api-server, scheduler,
  dag-processor, and a migrate one-shot that also creates the `airflow` database when it is
  missing (the init SQL in `docker/postgres/ddl/` runs only on a fresh volume). LocalExecutor
  with `AIRFLOW__CORE__PARALLELISM=4`. No triggerer.
- The database stays on the existing `prefect-db` service. See "What keeps the name".
  `max_connections` raised to 200, with a per-role connection limit for `airflow`, so the
  Lakekeeper catalog on the same instance cannot be starved.
- Host port from `K2_AIRFLOW_PORT` with a default that is free in the compose file, and the
  Airflow base URL built from the same variable.
- DAGs, hand-written in `docker/airflow/dags/lake.py` (three DAGs do not need a factory):
  `lake_ingest` (`1-59/5 * * * *`, `max_active_runs=1`, `catchup=False`, one retry after 60 s,
  3600 s timeout), `lake_maintenance` (`0 3 * * *`, 7200 s), `lake_rebuild` (manual).
- Dispatch is unchanged: `docker exec k2-spark-iceberg python3 /home/iceberg/lake/<script>`.
  Only `lake_ingest` maps exit 75 to skipped. `ingest.py` stays one task for all stages:
  splitting it would let maintenance take the lock between stages.
- A timeout handler kills the driver PID the task recorded inside the container, so a
  timed-out run cannot keep the lock.
- Metrics through a pinned statsd exporter into Prometheus. Dashboard: DAG run duration, run
  lateness (scheduled against started), task failures, skips, scheduler heartbeat, DAG parse
  time. Airflow 3 has no SLA-miss feature; run lateness is the panel that replaces it.
- Alerts, each with a promtool test and a runbook via `/runbook`: scheduler heartbeat lost;
  task failed; `lake_ingest` skipped three times running (a stuck lock).
- `lake-metrics` moves to its own pinned image, `docker/lake-metrics/`.
- CI `docker` matrix and Dependabot gain `docker/airflow` and `docker/lake-metrics`.
- Schedule shadow: `lake_ingest` runs `ingest.py --probe` on the real cron while Prefect
  keeps writing. `scripts/airflow-shadow-check.sh` asserts, over a 2 h window, that every
  scheduled slot ran, none failed, and Prefect's runs were not disturbed. The window is
  quoted as 2 h wherever its result is.
- ADR-034 with the budget clause; ADR-010's status line records the supersession; the compose
  summary comment and the CI ceiling move to 20 CPU / 36 GiB in this pull request.

**Pull request 2: cutover**

- Pause the Prefect schedule. Switch `lake_ingest` from probe to the real command. Run
  `make gates`. The lock already guarantees one writer, so the switch needs no data migration.
- Prefect services move behind a `legacy` compose profile. Rollback is: pause the DAG,
  `docker compose --profile legacy up -d`, resume the Prefect schedule.
- `scripts/chaos/lib.sh` pause and resume ported to `airflow dags pause` and `unpause` (only
  resume what was paused); `scripts/lake-verify.sh`; `scripts/health.sh` reading run state
  through the Airflow CLI.

**Pull request 3: archive, after 24 green runs**

- Delete the Prefect services, image, CI matrix entry and Dependabot entry. Archive
  `docker/prefect/` and `docker/lake/flows/` to `legacy/prefect/` with a README.
- Port the remaining tracked files that name Prefect: architecture chapters 01, 04, 08, 11,
  16, 17 and A1; `docs/operations/prefect-schedules.md`, which becomes
  `airflow-schedules.md`; the runbooks, each re-verified against the stack;
  `docs/development/setup.md` and `testing.md`; `README.md`, `CLAUDE.md`, `SECURITY.md`,
  `.gitignore`; `scripts/chaos/README.md` and the chaos scripts; the `runbook` and
  `release-check` skills; the `LakeIngestFailed` annotation and its test.

**What keeps the name.** The Postgres service, its role and database, and the `PREFECT_DB_*`
variables are not renamed. The role is physically named `prefect` inside every existing
volume, and a rename breaks every existing `.env` and the Lakekeeper connection string for no
functional gain. ADR-034 records this as a consequence, with a rebuild of the volume as the
trigger to rename. Dated documents (ADRs, audits, benchmarks, research, earlier plans, the
migration journey) are immutable and keep their text.

## Verification

```bash
bash scripts/airflow-shadow-check.sh                 # pull request 1, after 2 h
make gates                                           # after cutover
docker exec k2-airflow-scheduler airflow dags list
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest --state failed
bash scripts/chaos/lake-ingest-kill.sh               # still recovers under Airflow
git grep -li prefect -- ':!legacy' ':!docs/adr' ':!docs/audits' ':!docs/benchmarks' \
  ':!docs/research' ':!docs/plans' ':!docs/MIGRATION-JOURNEY.md'
```

The last command must print only `docker-compose.yml`, `.env.example`,
`docker/postgres/ddl/10-lakekeeper-db.sql` and the scripts that read `PREFECT_DB_*`.

_Phase P2 landed 2026-11-26 (AEDT), commits #189 564a282, #195 c572727 and #235: `make gates` passed with Airflow as the only scheduler (the P0 run, 01:35 to 01:55 UTC), and the first 24 scheduled `lake_ingest` runs after the switch were `success`, five of them skipped at the lock during the first maintenance. Where it differs from the text above: the cutover's `make gates` ran without the parity gates, which ran after the P0 re-pin; unpausing `lake_maintenance` ran a maintenance at once, which served as the lock test; the rollback was documented, never drilled, and went with the archive; the ceiling is 25 CPU / 48 GiB (ADR-045); the shadow check was deleted, not archived; the grep also lists files naming `prefect-db`, linking ADR-008 or ADR-034, or pointing at `legacy/prefect/` (`tests/test_no_scheduler_name.py`)._
