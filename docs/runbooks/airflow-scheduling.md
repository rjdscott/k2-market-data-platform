# Runbook: Airflow: the scheduler, a failed task, a stale DAG folder

Airflow 3 schedules the platform's jobs as DAGs ([ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md)).
**It runs with no DAG while the platform is rebuilt**: the four DAGs of the v3 lake (its ingest,
maintenance, rebuild and dbt marts) retired with that pipeline on 2026-11-26 and are archived in
[`legacy/v3-lake-ingest/airflow/dags/`](../../legacy/v3-lake-ingest/airflow/dags/)
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)), and
[phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md) of the
unified platform plan brings one DAG per product. This covers the two alerts left in
`docker/prometheus/rules/airflow-alerts.yml`, the scheduler stopping (§1) and a task failing
(§2), and a stale DAG folder (§3). The scheduler it replaced is archived
([`legacy/prefect/`](../../legacy/prefect/README.md)) and there is no rollback to it.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Scheduler heartbeat lost | < 10 min | alert 301 s after the stop; cleared 59 s after `up` (2026-11-25) |
| 2 | A task failed after its retries | depends on the task | alert 27 s after the failure (2026-11-25, on a v3 lake DAG); no DAG to fail until phase 002 |
| 3 | A stale DAG folder | < 25 min, once there are DAGs | observed 2026-11-25: 35 min with no run, then healthy within a minute of the recreate |

---

## 1. Scheduler heartbeat lost

**Symptom**, `AirflowSchedulerHeartbeatLost` fires, and `airflow jobs check` finds no alive
scheduler job. With no DAG yet, nothing else shows it: no run goes missing.

**Detection**, `AirflowSchedulerHeartbeatLost` from
`docker/prometheus/rules/airflow-alerts.yml`, over `airflow_scheduler_heartbeat`. It fires
on five minutes with no heartbeat, and also when the series is absent, which is what a
down `airflow-statsd` exporter looks like.

**Expected behaviour**, `restart: unless-stopped` brings a crashed scheduler back, and the
alert clears within a few minutes of its first heartbeat. A task running when the scheduler
died is marked failed after `task_instance_heartbeat_timeout` (300 s) and retried as its DAG
says.

**Recovery**

Run on the live stack 2026-11-25: the scheduler was stopped on purpose. The `up` query printed `1`, the alert fired 301 s after the stop, `airflow jobs check` printed `Found one alive job.` 9 s after `docker compose up -d`, and the alert cleared 59 s after it. The heartbeat rate reads about 0.2 a second once the 2 m window has filled.

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
```

**After recreating `airflow-statsd`, restart the three services that send to it.** Observed
2026-11-25: the exporter was recreated at about 07:20 UTC and every Airflow metric stopped,
because Airflow's StatsD client resolves `airflow-statsd` once at start and kept sending to
the old address. `AirflowSchedulerHeartbeatLost` fired within minutes (its absent-series
clause), and `docker compose restart airflow-scheduler airflow-dag-processor
airflow-api-server` restored all 90 metric names. Wait until nothing runs first (the operator
guide's [section 6](../operations/README.md#6-restart-or-recreate-one-service), step 1), so the
restart kills nothing:

```bash
docker compose up -d --force-recreate --no-deps airflow-statsd
docker compose restart airflow-scheduler airflow-dag-processor airflow-api-server
```

The exporter drops an `airflow_ti_running` value the scheduler has not re-sent for 2 minutes
(`docker/airflow/statsd-mapping.yml`). The mapping is a file bind mount, so a change to it takes
a recreate (the [`CLAUDE.md`](../../CLAUDE.md) bind-mount gotcha).

**Measured**, 2026-11-25, one drill: the alert fired 301 s after `docker compose stop airflow-scheduler`, the scheduler job was alive 9 s after `up -d`, and the alert cleared 59 s after it.

---

## 2. A task failed after its retries

**Not runnable until phase 002**: there is no DAG, so no task. The detection is in place.

**Symptom**, a red run in the Airflow UI.

**Detection**, `AirflowTaskFailed`, over `airflow_ti_finish{state="failed"}`. A try that is
retried ends `up_for_retry` and does not fire; only the final failure does. It fires for
15 minutes and clears by itself; the run's log is the record.

**Expected behaviour**, the next scheduled run starts whatever happened. What a failed run of a
product leaves behind, and how to recover it, is phase 002's connector-failure runbook.

**Recovery**

```bash
# 1. Which runs failed, and when.
docker exec k2-airflow-scheduler airflow dags list-runs <dag> --state failed

# 2. The task log: the job's own output, then its exit code. The UI shows the same at
#    http://localhost:${K2_AIRFLOW_PORT:-8090}. Task logs are JSON lines, one event per line.
docker exec k2-airflow-scheduler sh -c \
  "find /opt/airflow/logs -path '*dag_id=<dag>*' -name '*.log' | sort | tail -1 | xargs tail -80"
```

**Measured**, 2026-11-25, one drill on a v3 lake DAG: `AirflowTaskFailed` fired 27 s after a run
failed on purpose.

---

## 3. A stale DAG folder

**Symptom**, once there are DAGs: no new run in the UI or in `list-runs`, and every container
healthy. Nothing else looks wrong. With no DAG, a stale folder changes nothing.

**Detection**, manual. The alert that caught this for the v3 ingest DAG retired with it.

**Cause**, switching the live checkout between commits that add or remove a mounted
directory. `./docker/airflow/dags` is a directory bind mount held by `airflow-scheduler`
and `airflow-dag-processor`; a checkout that removes the directory and puts it back gives
the host a new directory, and both containers keep the old one, empty. The dag-processor
then marks the DAGs stale and the scheduler stops creating their runs. This is the
[`CLAUDE.md`](../../CLAUDE.md) bind-mount gotcha, for a directory. A DAG paused by hand and
not unpaused looks the same from outside.

**Observed 2026-11-25**: the live checkout was switched to a commit without
`docker/airflow/dags/` and back. Both Airflow containers saw an empty directory, the DAG showed
`is_stale True`, and the scheduler created no run for 35 minutes while every container stayed
healthy and no alert fired. The recreate in step 2 fixed it within a minute, with exactly one
run for the latest slot and no replay of the missed ones.

**Recovery**

```bash
# 1. What the containers see against what the host has. A file missing inside, or an older
#    one, is the stale mount. Import errors, if any, are listed by the second command.
docker exec k2-airflow-scheduler     ls -la /opt/airflow/dags
docker exec k2-airflow-dag-processor ls -la /opt/airflow/dags
ls -la docker/airflow/dags
docker exec k2-airflow-scheduler airflow dags list-import-errors

# 2. Stale: recreate both holders of the mount.
docker compose up -d --force-recreate --no-deps airflow-scheduler airflow-dag-processor

# 3. A DAG paused by hand: unpause it.
docker exec k2-airflow-scheduler airflow dags unpause <dag>
```

**Measured**, 2026-11-25, one occurrence: 35 minutes with no run, and back within a minute of
the recreate.

---

## Retired with the v3 lake

The sections on the lake's writer lock (an ingest skipped three runs in a row) and on a catch-up
run with a bigger driver went with the DAGs and the lock they described, with their two alerts,
on 2026-11-26. They are in git history; the DAGs are in
[`legacy/v3-lake-ingest/airflow/dags/`](../../legacy/v3-lake-ingest/airflow/dags/).

---

**Last verified:** 2026-11-26 on the live stack, read-only, before the old DAGs were removed: §1
steps 1, 2 and 4 (the exporter `1`, both services up, the heartbeat rate `0.2`, `Found one alive
job.`) and §3 step 1 (the same files in both containers and on the host, `No data found` for
import errors). §1 step 3, the statsd recreate and restart, §2, and §3 steps 2 and 3 were not run
in this pass.
