# Runbook: Airflow: the scheduler, a failed task, a missing DAG, a product's run

Airflow 3 schedules the platform's jobs as DAGs ([ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md)):
**one DAG per product, generated from the release** the `airflow` pointer names. This covers the
two alerts in `docker/prometheus/rules/airflow-alerts.yml`, the scheduler stopping (§1) and a task
failing (§2), a DAG missing or stuck (§3), and triggering and reading one product's run (§4). What
a failed fetch and a failed audit leave behind, and how to clear them, are
[connector-failure.md](./connector-failure.md) and
[file-source-audit-failed.md](./file-source-audit-failed.md). The four DAGs of the retired v3 lake
are archived in [`legacy/v3-lake-ingest/airflow/dags/`](../../legacy/v3-lake-ingest/airflow/dags/)
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)); the scheduler Airflow replaced is
archived in [`legacy/prefect/`](../../legacy/prefect/README.md), with no rollback to it.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Scheduler heartbeat lost | < 10 min | alert 301 s after the stop; cleared 59 s after `up` (2026-11-25) |
| 2 | A task failed | depends on the task | alert 27 s after the failure (2026-11-25, on a v3 lake DAG); not yet measured on a product DAG: the first induced failure after the deploy of this release measures it |
| 3 | A DAG missing, paused or stale | < 10 min | not yet measured on a product DAG (no product DAG has gone missing); the old checkout-mount case observed 2026-11-25: 35 min with no run, healthy within a minute of the recreate |
| 4 | Trigger and read a product's run | < 5 min | first runs on the live stack, 2026-11-27: `ecb_fx_rates` 121.7 s, `currency_master` 48 s, `quality_results` 21 s, each a success |

---

## How a product's DAG runs

What the sections below act on, from
[`docker/airflow/dags/factory.py`](../../docker/airflow/dags/factory.py),
[`oneshot.py`](../../docker/airflow/dags/oneshot.py) and `docker-compose.yml`.

- **The DAG folder is the release's.** `AIRFLOW__CORE__DAGS_FOLDER` is
  `/k2/releases/pointers/airflow/platform/docker/airflow/dags`, read through the releases root that
  `airflow-scheduler` and `airflow-dag-processor` mount whole; nothing comes from the checkout. A
  swap of the pointer reaches `factory.py` within 30 s (each file is parsed every 30 s). A dangling
  path (no pointer, or a release without `platform/`) is an empty folder: no DAG, every service
  healthy.
- **The factory** builds one DAG per entry of the pinned release's `compiled/airflow/dags.json`,
  at parse time, from `factory.py`'s own real path four levels below the release. A tree without a
  `manifest.json` there (a checkout, a release from before the run code) builds no DAG; a release
  with a missing or malformed `dags.json` raises, which Airflow lists as an import error. The DAG
  id is the product's fence (`ecb_fx_rates`), its schedule the contract's cron, `catchup` off, one
  active run at a time, `dagrun_timeout` the contract's `timeoutMinutes` when it has one. A product
  whose status is not `active` starts paused.
- **The task chain**, of which a product's entry is a subset in this order:
  `resolve >> fetch >> load >> audit >> publish >> build >> expire >> record`, and
  `expire >> maintain >> record`. A seed-only product such as `calendars` has
  `resolve >> build >> expire >> record` and `maintain`.
  - `resolve` reads the pointer once and hands `{release, releasePath}` on by XCom: every later
    task uses that release, so a deploy during a run leaves the run on its release.
  - `build` is `none_failed`: an unchanged fetch (exit 10) skips `load`, `audit` and `publish`, and
    `build` still runs; a failed or refused data task leaves it `upstream_failed`.
  - `expire` and `record` are teardowns (`all_done`): they run whatever happened and do not decide
    the run's state, so a failed audit or build still fails the run while `record` succeeds.
  - `maintain` is `all_done`, runs only when its `maintain` cron falls in the run's slot, and is
    skipped otherwise.
- **Retries**: none. The factory sets none, compose sets no `AIRFLOW__CORE__DEFAULT_TASK_RETRIES`,
  and Airflow's default is 0. `build` is set to 0 explicitly. The exits the code marks as never
  worth retrying (fetch 20; the connector's 2 and 3) fail with `AirflowFailException` even if
  retries are added.
- **Where each task runs.** `load`, `audit`, `publish`, `expire` and `maintain` are `docker exec`
  into `k2-spark-iceberg`, running the pinned release's `platform/docker/lake/` scripts; `fetch`
  and `build` are one-shot containers the scheduler starts through the Docker socket, named
  `k2-<kind>-<dagId>-<try>` (on the live stack on 2026-11-27, `k2-fetch-currency_master-1` and
  `k2-dbt-runner-ecb_fx_rates-1`), removed when they exit (`--rm`), never pulled at run time
  (`--pull never`), each with only what the release's `compiled/oneshots.json` lists for its kind,
  and labelled `com.k2.oneshot=<kind>` and `com.k2.project=<project>` (the spool helper as
  `fetch-spool`). The labels are how the catalog fence admits a running `build` on `k2-catalog`
  ([`scripts/verify/catalog-fence.sh`](../../scripts/verify/catalog-fence.sh)): a member with no
  compose labels passes only with this project's label, a kind named in
  `tests/access/catalog-fence-allow.txt`, the name `k2-<kind>-<dagId>-<try>` and the image the live
  release's `compiled/oneshots.json` gives the kind. Before that fix, release-verify on 2026-11-27
  counted a running build as an intruder (`k2-dbt-runner-ecb_fx_rates-1=/`). `fetch` runs on `k2-egress` alone with its
  DAG's directory of the `k2-spool` volume; `build` runs on `k2-catalog` with its product's builder
  and publisher passwords and `svc.runs`'s. `record` writes the run record as `svc.runs`.
- **Pools**, created by the `airflow-migrate` one-shot (`airflow pools set ...` in
  `docker-compose.yml`): `lake_spark` 1 slot (every Spark task: one driver at a time in
  `spark-iceberg`), `fetch` 1, `dbt` 2, inside LocalExecutor's 4 (`AIRFLOW__CORE__PARALLELISM`).
  `airflow pools list` on the live stack, 2026-11-27: `lake_spark` 1, `fetch` 1, `dbt` 2.
- **A scheduler that dies under a running task** leaves it running until the orphan check resets
  it: compose sets `AIRFLOW__SCHEDULER__ORPHANED_TASKS_CHECK_INTERVAL` to 30 s, against Airflow's
  300 s default (`scripts/verify/oneshot-slots.sh` measured the reset).

---

## 1. Scheduler heartbeat lost

**Symptom**, `AirflowSchedulerHeartbeatLost` fires, and `airflow jobs check` finds no alive
scheduler job. No product run is created or started: each product's next slot goes missing.

**Detection**, `AirflowSchedulerHeartbeatLost` from
`docker/prometheus/rules/airflow-alerts.yml`, over `airflow_scheduler_heartbeat`. It fires on five
minutes with no heartbeat, and also when the series is absent, which is what a down
`airflow-statsd` exporter looks like.

**Expected behaviour**, `restart: unless-stopped` brings a crashed scheduler back, and the alert
clears within a few minutes of its first heartbeat. A task running when the scheduler died is
reset by the orphan check (every 30 s, once the dead job's heartbeat is stale) and marked failed;
with no retries, its run fails and the next scheduled slot runs as usual (`catchup` off). A
one-shot container the dead scheduler started may outlive it; the next try of the same name
removes it first (`docker rm -f` of every earlier try's name, `oneshot.py`).

**Recovery**

Run on the live stack 2026-11-25, before any product DAG: the scheduler was stopped on purpose. The `up` query printed `1`, the alert fired 301 s after the stop, `airflow jobs check` printed `Found one alive job.` 9 s after `docker compose up -d`, and the alert cleared 59 s after it. The heartbeat rate reads about 0.2 a second once the 2 m window has filled.

```bash
# 1. Scheduler or exporter? 1 = exporter up, so the scheduler is the problem.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=up{job="airflow-statsd"}' | jq -r '.data.result[].value[1]'
docker compose ps airflow-scheduler airflow-statsd

# 2. Why did it stop? An OOM kill shows as exit 137 in `ps -a`.
docker compose ps -a airflow-scheduler
docker logs --tail 100 k2-airflow-scheduler

# 3. Bring back whichever one is down.
docker compose up -d --no-deps airflow-scheduler airflow-statsd

# 4. Confirm: heartbeats again (about 0.2 a second), and the scheduler job is alive.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=rate(airflow_scheduler_heartbeat[2m])' | jq -r '.data.result[].value[1]'
docker exec k2-airflow-scheduler airflow jobs check --job-type SchedulerJob --local

# 5. A run that was cut short: failed, and nothing left running.
docker exec k2-airflow-scheduler airflow dags list-runs <dag> --state failed
docker ps --filter name=k2-fetch- --filter name=k2-dbt-runner-
```

**After recreating `airflow-statsd`, restart the three services that send to it.** Observed
2026-11-25: the exporter was recreated at about 07:20 UTC and every Airflow metric stopped,
because Airflow's StatsD client resolves `airflow-statsd` once at start and kept sending to the
old address. `AirflowSchedulerHeartbeatLost` fired within minutes (its absent-series clause), and
`docker compose restart airflow-scheduler airflow-dag-processor airflow-api-server` restored all
90 metric names. Wait until nothing runs first (the operator guide's
[section 6](../operations/README.md#6-restart-or-recreate-one-service), step 1), so the restart
kills nothing:

```bash
docker compose up -d --force-recreate --no-deps airflow-statsd
docker compose restart airflow-scheduler airflow-dag-processor airflow-api-server
```

The exporter drops an `airflow_ti_running` value the scheduler has not re-sent for 2 minutes
(`docker/airflow/statsd-mapping.yml`). The mapping is a file bind mount, so a change to it takes a
recreate (the [`CLAUDE.md`](../../CLAUDE.md) bind-mount gotcha).

**Measured**, 2026-11-25, one drill: the alert fired 301 s after `docker compose stop airflow-scheduler`, the scheduler job was alive 9 s after `up -d`, and the alert cleared 59 s after it.

---

## 2. A task failed

**Symptom**, a red run in the Airflow UI at `http://localhost:${K2_AIRFLOW_PORT:-8090}`.

**Detection**, `AirflowTaskFailed`, over `airflow_ti_finish{state="failed"}`, labels `dag_id` and
`task_id`. With no retries, the first failure is the final one and fires. It fires for 15 minutes
and clears by itself; the task log is the record. No product-level alert exists yet: per-product
objective alerts are phase 002 of the
[to-release plan](../plans/2026-11-26-unified-product-platform-to-release/002-controls-alerts-release-engineering.md).

**Expected behaviour**, the next scheduled run starts whatever happened. `expire` and `record`
still ran, so the run record says what failed. Bronze `main` never moves past a failed audit, and a
dbt output never keeps a failed build's rows. What each task's failure says in its log:

| Task | Its log, from the code | Next |
|------|------------------------|------|
| `resolve` | `/k2/releases/pointers/airflow resolves to '<path>', not /k2/releases/<release id>` | §3: the pointer |
| `fetch` | `one-shot: docker run --rm --name k2-fetch-<dag>-<try> ...`, then fetch's own lines; `k2-fetch-<dag>-<try> exited <n>` (exit 20 never retried; 10 skips load, audit, publish; 30 succeeds with a warning and the audit fails the batch) | [connector-failure.md](./connector-failure.md) |
| `load`, `audit`, `publish` | `spark: docker exec k2-spark-iceberg sh -c ...`, the connector's lines, its last line `{"exit": <n>, "task": ...}`, then `<task> exited <n>`: 1 a task error, 2 refused, 3 the batch failed its audit | [file-source-audit-failed.md](./file-source-audit-failed.md) for 2 and 3 |
| `build` | `one-shot: docker run --rm --name k2-dbt-runner-<dag>-<try> ...`, dbt's output, the wrapper's last line `{"exit": <n>, "expire": [...]}`, then `k2-dbt-runner-<dag>-<try> exited <n>`: 1 tests failed and the outputs restored, 2 a build error and the outputs restored, 3 a restore, the tag commit, the view sync, the reconcile or the drift check failed, 4 a required input was never tested ([`docker/dbt-runner/k2_dbt.py`](../../docker/dbt-runner/k2_dbt.py)) | 1, 2: fix the model or the data in a release; 3: the next run reconciles first, and a second 3 is a page; 4: run the input's DAG |
| `expire` | skipped with `nothing to expire: build's result is ...` when the build left nothing; else the expire script's exit | read its log |
| `maintain` | skipped with `maintenance is not due in the run at <time>`; a build output with no good build yet prints `maintain: <table>: not built yet`, is named in the result's `notBuilt` and expires nothing, and the task goes on; else exit 1 names the ref that vanished, a refused delete, a missing retention entry, or a bronze, checks or landing table that is missing (deploy makes those, never a build) ([`docker/lake/maintain.py`](../../docker/lake/maintain.py)). Before that fix, `currency_master`'s first run on 2026-11-27 failed here with `TABLE_OR_VIEW_NOT_FOUND` | read its log |
| `record` | `run record not written for <dag>/<run>: <error>`; the run's state is unchanged | Trino or `svc.runs`'s password: `make principals-status` |

A one-shot that never started fails with `docker run ...` or the spool helper's command and its
exit code in the message: an image the host lacks, a missing network, a spool helper that failed
([connector-failure.md](./connector-failure.md)).

**Recovery**

```bash
# 1. Which runs failed, and which task in each.
docker exec k2-airflow-scheduler airflow dags list-runs <dag> --state failed
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run <dag> <run_id>

# 2. The task log: the job's own output, then its exit code. The UI shows the same.
#    Task logs are JSON lines, one event per line.
docker exec k2-airflow-scheduler sh -c \
  "find /opt/airflow/logs -path '*dag_id=<dag>*' -name '*.log' | sort | tail -1 | xargs tail -80"
```

Then the runbook the table names. A run is never rerun from the middle with a newer release:
every task reads the release `resolve` pinned, so a fix that needs a new release takes a new run
(§4).

**Measured**, 2026-11-25, one drill on a v3 lake DAG: `AirflowTaskFailed` fired 27 s after a run
failed on purpose. Not yet measured on a product DAG.

---

## 3. A DAG missing, paused or stale

**Symptom**, a product's DAG is not in `airflow dags list`, or is listed and creates no run, while
every container is healthy.

**Detection**, manual. No alert covers a DAG that never runs (the duration and freshness alerts
are the to-release plan's phase 002).

**Cause**, one of:

- **The pointer names no release with run code**: no pointer yet, or a release from before phase
  002, whose tree has no `manifest.json` four levels above `factory.py`. The folder is empty to
  Airflow, or the factory builds nothing.
- **An import error**: the pinned release's `dags.json` is missing or malformed, or an entry fails
  the factory's check (`dags.json entry <i> (<dag>): bad <field>`).
- **Paused**: a product whose status is not `active` starts paused; a deploy paused it while
  draining a DAG whose shape changed and exited 3, which leaves it paused (`scripts/deploy.py`, the
  `drain` step; the names are in `${K2_RELEASES}/deploy-paused.json`); or a person paused it. A
  deploy that reaches done unpauses only the DAGs it paused. The deploy of
  `v3.0.0-alpha.4-rc.3-48ad71fba732` on the live stack, 2026-11-27, met all five DAGs, two of them
  paused by the operator beforehand:

  ```text
    drained access_canary, calendars, currency_master, ecb_fx_rates, quality_results (the DAG code moved since v3.0.0-alpha.4-rc.2-e2dcf759de67) in 11 s; paused access_canary, calendars, ecb_fx_rates
  deploy: drain ok (12.8 s)
  ...
  deploy: v3.0.0-alpha.4-rc.3-48ad71fba732 is live and verified
    unpaused access_canary, calendars, ecb_fx_rates: the DAGs deploy paused
  ```

  The two the operator had paused were in neither list and stayed as the operator left them.
- **A stale releases-root mount**: the root was removed and created again on the host while the
  containers kept the old directory. The root is created once and never recreated for this reason
  ([`CLAUDE.md`](../../CLAUDE.md), the bind-mount gotcha; ADR-058). The checkout's
  `docker/airflow/dags/` is no longer mounted, so switching the live checkout between commits no
  longer empties the DAG folder, as it did on 2026-11-25 (35 minutes with no run, healthy within a
  minute of a recreate).

**Recovery**

```bash
# 1. What the scheduler sees: the pointer, the folder through it, the DAGs, any import error.
docker exec k2-airflow-scheduler readlink /k2/releases/pointers/airflow
docker exec k2-airflow-scheduler ls /k2/releases/pointers/airflow/platform/docker/airflow/dags
docker exec k2-airflow-scheduler airflow dags list
docker exec k2-airflow-scheduler airflow dags list-import-errors
make release-status                                  # the good release, and an unfinished deploy

# 2. Paused: by whom? A deploy's drain names its DAGs here; a rerun of that deploy, or
#    `make rollback`, unpauses them when it reaches done. Otherwise unpause it by hand.
cat "${K2_RELEASES:-$HOME/k2-releases}/deploy-paused.json"
docker exec k2-airflow-scheduler airflow dags unpause <dag>

# 3. A stale mount (the pointer reads on the host and not in the container): recreate both
#    holders of the root.
docker compose up -d --force-recreate --no-deps airflow-scheduler airflow-dag-processor
```

No pointer, or a pointer at a release without run code: deploy a release that has it
([deploy-a-release.md](./deploy-a-release.md)).

Step 1 on the live stack, 2026-11-27, release `v3.0.0-alpha.4-rc.3-48ad71fba732`: `airflow dags list`
listed five DAGs, `access_canary`, `calendars`, `currency_master`, `ecb_fx_rates` and
`quality_results`, and `airflow dags list-import-errors` printed `No data found`.

**Measured**, not yet measured on a product DAG: none has gone missing.

---

## 4. Trigger and read a product's run

**Symptom**, a run is wanted now: after a deploy, after clearing a blocked batch, to see a product
work end to end.

**Detection**, manual.

**Expected behaviour**, the run takes the release the pointer names when `resolve` starts and keeps
it to the end. A trigger of a paused DAG stays queued until it is unpaused. `fetch` takes its
`--ds` from the run's date; a file with the same sha256 as the DAG's newest earlier fetch exits 10
and the run builds without new bytes.

**Recovery**

```bash
# 1. Trigger it, and find its run id.
docker exec k2-airflow-scheduler airflow dags trigger ecb_fx_rates
docker exec k2-airflow-scheduler airflow dags list-runs ecb_fx_rates

# 2. Each task's state, while it runs and after; a one-shot shows in `docker ps` while it runs.
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run ecb_fx_rates <run_id>
docker ps --filter name=k2-fetch-ecb_fx_rates --filter name=k2-dbt-runner-ecb_fx_rates

# 3. The run record: one row per step, written as svc.runs, read here as svc.catalog through the
#    reader catalog (its one exact table rule). trino_cli is the helper of tls-and-passwords.md,
#    defined in this shell with NET=${K2_COMPOSE_PROJECT}_k2-net.
trino_cli svc.catalog "SELECT task, event, table_name, rows_written, release_id, recorded_at
  FROM governance.run_records.run_record WHERE run_id = 'ecb_fx_rates/<run_id>' ORDER BY recorded_at"
```

Order matters for the products that read each other: `calendars` first (`ecb-fx-rates` requires its
tested `target_days`, or its build exits 4), then `currency_master` and `ecb_fx_rates`.

**Measured**, the first runs on the live stack, 2026-11-27. Under
`v3.0.0-alpha.4-rc.2-e2dcf759de67`: `calendars` a success (`resolve`, `build`, `maintain` and `record`
success, `expire` skipped), `ecb_fx_rates` a success in 121.7 s with every task a success (`expire`
skipped), `access_canary` a success. Under `v3.0.0-alpha.4-rc.3-48ad71fba732`: `currency_master` a
success in 48 s, its fetch log

```text
fetch: spooled 47491 bytes, sha256 ...
fetch: <n> rows
```

and `quality_results` a success in 21 s.

---

## Retired with the v3 lake

The sections on the lake's writer lock (an ingest skipped three runs in a row) and on a catch-up
run with a bigger driver went with the DAGs and the lock they described, with their two alerts,
on 2026-11-26. They are in git history; the DAGs are in
[`legacy/v3-lake-ingest/airflow/dags/`](../../legacy/v3-lake-ingest/airflow/dags/).

---

**Last verified:** 2026-11-27 on the live stack, for the product DAGs: §3 step 1 (`dags list`,
`dags list-import-errors`), the pools, the deploy's drain and unpause (§3, release
`v3.0.0-alpha.4-rc.3-48ad71fba732`), the one-shot names, and §4's first runs of all five products.
§1 on 2026-11-26 on the live stack, read-only, before the old DAGs were removed: steps 1, 2 and 4
(the exporter `1`, both services up, the heartbeat rate `0.2`, `Found one alive job.`). Not yet: an
induced task failure on a product DAG (§2), §1 step 5, §3 steps 2 and 3 against a paused or stale
DAG, and §4 step 3's run record query in this form; those are written from `factory.py`,
`oneshot.py`, `docker-compose.yml` and the Airflow 3.3.2 CLI's own argument table
(`airflow/cli/cli_config.py`: `dags list-runs <dag_id> --state`, `dags trigger <dag_id>`,
`tasks states-for-dag-run <dag_id> <run_id>`).
