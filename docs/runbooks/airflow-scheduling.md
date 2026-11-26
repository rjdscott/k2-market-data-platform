# Runbook: The Airflow lake schedules: recovery

The lake ingest, maintenance and rebuild run as three Airflow 3 DAGs
([ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md), `docker/airflow/dags/lake.py`),
each a single task that runs one `docker/lake` script in the Spark container. This covers
the four alerts in `docker/prometheus/rules/airflow-alerts.yml`: the scheduler stopping, a
task failing, `lake_ingest` refused the writer lock run after run, and `lake_ingest` not
running at all (§5, a stale DAG folder or a forgotten pause), and the catch-up after a long
stall (§4). It does not cover what a failing script found: that is
[lake-ingest-lag.md](./lake-ingest-lag.md), [lake-audit-failed.md](./lake-audit-failed.md)
and [lake-recovery.md](./lake-recovery.md). The schedules and limits are in
[airflow-schedules.md](../operations/airflow-schedules.md); pausing, triggering and reading
runs are in the [operator guide](../operations/README.md#6-restart-or-recreate-one-service)
and the [quick reference](../operations/quick-reference.md).

**Airflow is the only scheduler of the lake writes.** The scheduler it replaced is archived
([`legacy/prefect/`](../../legacy/prefect/README.md)) and there is no rollback to it: nothing
in the compose file starts it. If Airflow is down, the lake can be run by hand with the
`docker exec` forms in [lake-ingest-lag.md](./lake-ingest-lag.md) while §1 to §5 bring it back.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Scheduler heartbeat lost | < 10 min | alert 301 s after the stop; cleared 59 s after `up` (2026-11-25) |
| 2 | A lake task failed after its retries | depends on what the script found | alert 27 s after the failure (2026-11-25) |
| 3 | `lake_ingest` skipped three runs in a row: the lock is held | < 15 min | orphan driver gone 284 ms after TERM (2026-11-25); the alert fired once, falsely, after the first maintenance run (fixed); not induced by a real stall |
| 4 | Catch up after a long stall, with a bigger driver | a manual run | task 2.9 min to drain the stalled backlog, 2 g driver (2026-11-25); the run first waited for the scheduled run before it |
| 5 | `lake_ingest` not running: a stale DAG folder, a forgotten pause | < 25 min | observed 2026-11-25 before the alert existed: 35 min with no run, then healthy within a minute of the recreate |

---

## 1. Scheduler heartbeat lost

**Symptom**, no new DAG runs: the Airflow UI shows the last `lake_ingest` run minutes or
hours old, and the "Run lateness" panel on the K2 Airflow dashboard stops updating.

**Detection**, `AirflowSchedulerHeartbeatLost` from
`docker/prometheus/rules/airflow-alerts.yml`, over `airflow_scheduler_heartbeat`. It fires
on five minutes with no heartbeat, and also when the series is absent, which is what a
down `airflow-statsd` exporter looks like. Once Airflow owns the ingest, `LakeIngestFailed`
is the critical backstop 35 minutes after the last commit.

**Expected behaviour**, `restart: unless-stopped` brings a crashed scheduler back, and the
alert clears within a few minutes of its first heartbeat. A task that was running when the
scheduler died is not killed with it: its driver runs in `k2-spark-iceberg`, outside the
scheduler's container, and finishes or keeps the lock (§3). Airflow marks the orphaned try
failed after `task_instance_heartbeat_timeout` (300 s) and retries it once.

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
docker compose up -d airflow-scheduler airflow-statsd

# 4. Confirm: heartbeats again (about 0.2 a second), and the scheduler job is alive.
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=rate(airflow_scheduler_heartbeat[2m])' | jq -r '.data.result[].value[1]'
docker exec k2-airflow-scheduler airflow jobs check --job-type SchedulerJob --local
```

Then check §3's first command: if the scheduler died mid-run, a driver may still hold the
lock.

**Measured**, 2026-11-25, one drill: the alert fired 301 s after `docker compose stop airflow-scheduler`, the scheduler job was alive 9 s after `up -d`, and the alert cleared 59 s after it.

---

## 2. A lake task failed after its retries

**Symptom**, a red run in the Airflow UI for `lake_ingest`, `lake_maintenance` or
`lake_rebuild`.

**Detection**, `AirflowTaskFailed`, over `airflow_ti_finish{state="failed"}`. A try that is
retried ends `up_for_retry` and does not fire; only the final failure does. It fires for
15 minutes and clears by itself; the run's log is the record. It leaves out `dbt_marts`, whose
failures are [dbt-marts.md](./dbt-marts.md)'s (`DbtMartsNotSucceeding` after 3 hours,
`DbtMartsSnapshotExpiryStale` after 26 for the daily expiry). A file source's DAG,
`<table>_ingest`, fires it too; for its `load`, `audit` or `publish` task go to
[file-source-audit-failed.md](./file-source-audit-failed.md) instead.

**Expected behaviour**, `lake_ingest` retries once after 60 s, and the next slot runs five
minutes later whatever happened: the ingest resumes from the offsets in its last snapshot
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)), so a failed run loses
nothing and needs no re-run. `lake_maintenance` and `lake_rebuild` do not retry: a failed
audit is a finding.

**Recovery**

Run on the live stack 2026-11-25 against a failed run made on purpose (a `lake_rebuild` dry run with a layer name the script rejects): the run listed as `failed`, the log ended with the script's own error line and `Task failed with exception`, `AirflowTaskFailed` fired 27 s later, and `pgrep` found no process left. Task logs are JSON lines, one event per line.

```bash
# 1. Which runs failed, and when.
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest --state failed
docker exec k2-airflow-scheduler airflow dags list-runs lake_maintenance --state failed

# 2. The task log: the script's own stdout and stderr, then its exit code. The UI shows
#    the same at http://localhost:${K2_AIRFLOW_PORT:-8090}.
docker exec k2-airflow-scheduler sh -c \
  "find /opt/airflow/logs -path '*dag_id=lake_ingest*' -name '*.log' | sort | tail -1 | xargs tail -80"

# 3. A timed-out try must not leave its driver behind. Nothing listed = clean.
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'
```

Read the exit line and go where it points:

- `ingest.py exited 1` with a Spark exception, or `failOnDataLoss`:
  [lake-ingest-lag.md](./lake-ingest-lag.md), §3 for retention.
- `maintenance.py exited 1` after an `audits:` table: [lake-audit-failed.md](./lake-audit-failed.md).
- `Error response from daemon: No such container: k2-spark-iceberg`, or the Docker socket
  refused: [lake-recovery.md](./lake-recovery.md); bring `spark-iceberg` back and the next
  slot runs.
- `Timeout` or `AirflowTaskTimeout`: the run passed its `execution_timeout` (3600 s for the
  ingest, 7200 s for maintenance). The task's `on_kill` stopped the driver; step 3 above
  confirms it. A slow ingest is a backlog: §4.

**Measured**, 2026-11-25, one drill: `AirflowTaskFailed` fired 27 s after a run failed on purpose.

---

## 3. `lake_ingest` skipped three runs in a row

**Symptom**, `lake_ingest` runs shown as skipped in the UI, one per slot, and
`raw.messages` not committing.

**Detection**, `AirflowLakeIngestSkippedRepeatedly`, over
`airflow_ti_finish{dag_id="lake_ingest",state="skipped"}`. A skip is `ingest.py` exiting 75:
another lake writer holds `/tmp/k2-lake-ingest.lock` in `k2-spark-iceberg`. It does not fire
while Airflow's own `lake_maintenance` or `lake_rebuild` is running, because both hold the
lock on purpose, nor for a 15-minute grace after one ends. Running is the scheduler's
`airflow_ti_running` gauge for either DAG. The grace is there because the skips taken under
the holder stay in the alert's 15-minute window until the first run after it has finished:
up to 5 minutes to the next slot, plus that first run, which reads the backlog the hold
built (3.5 minutes on 2026-11-25), plus about a minute of scrape and evaluation. That is
9.5 minutes; 15 adds margin and matches the window, so the old skips have aged out by the
time the grace lapses even if the first run fails. For up to 15 minutes after the
`airflow-statsd` exporter restarts the skip count reads low and the alert stays quiet: a lock
held across the restart fires that much later.

The exporter drops an `airflow_ti_running` value the scheduler has not re-sent for 2 minutes
(`docker/airflow/statsd-mapping.yml`; the scheduler re-sends a running task every 30 s). A
scheduler restarted just after a holder ended never sends its 0, and without that ttl the
last 1 muted both lake alerts until the exporter restarted. The mapping is a file bind mount,
so a change to it takes a recreate (the [`CLAUDE.md`](../../CLAUDE.md) bind-mount gotcha).
A recreate restarts every Airflow counter from zero, and the lake rules allow for it: this
alert errs quiet for up to 15 minutes and §5's is quiet for 15, both cases in
`docker/prometheus/rules/tests/airflow-alerts_test.yml`.

Run on the live stack 2026-11-25 (read-only, after `027980f` was deployed): the query
`airflow_ti_running` returned no series with no task running. The two-minute drop of a
stale value was not timed, and the recreate was not repeated for this note: not
demonstrated.

```bash
docker compose up -d --force-recreate --no-deps airflow-statsd
```

After recreating `airflow-statsd`, restart the three services that send to it. Observed
2026-11-25: the exporter was recreated at about 07:20 UTC and every Airflow metric stopped,
because Airflow's StatsD client resolves `airflow-statsd` once at start and kept sending to
the old address. `AirflowSchedulerHeartbeatLost` fired within minutes (its absent-series
clause), and `docker compose restart airflow-scheduler airflow-dag-processor
airflow-api-server` restored all 90 metric names. Pause the ingest DAGs first, with the
pause commands above, and wait until no task is running, so the restart kills nothing:

```bash
docker compose restart airflow-scheduler airflow-dag-processor airflow-api-server
```

**Observed 2026-11-25**, before the grace existed: a false firing. The first
`lake_maintenance` under Airflow ran 06:10:21 to 06:33:58 UTC, and the `lake_ingest` runs for
the 06:11 to 06:31 slots skipped, five in a row, as they should. The alert went pending at
06:35 and fired at 06:36 for about a minute: the holder test read 1 throughout the run and
dropped the moment maintenance ended, while the window still held three of the skips and the
06:36 run, the first to get the lock, had not finished. That run succeeded 3.5 minutes after
it started. Nothing was wrong with the lock.

**Expected behaviour**, one or two skips are the lock doing its job: a maintenance or a
manual job held it and the ingest stepped aside. Three in a row with nothing of Airflow's
running means something is holding the lock that should not be, most often a driver left
behind by a killed run (§1) or a job started by hand. It clears at the first run that gets
the lock.

**Recovery**

Run on the live stack 2026-11-25. `pgrep -af` prints each running lake job, and it also prints the `sh -c` wrapper that started it: the writer is the line that begins `python3`. `ps -o pid,etime,args` works in the Spark image. Not yet induced: three skips in a row outside a maintenance run.

```bash
# 1. Every lake writer is a python3 process running one of these scripts. The flock
#    belongs to one of them.
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'

# 2. Which of them Airflow started: its wrapper writes the driver's PID to a file
#    named for the task try. A PID listed in step 1 and not here was started by hand.
docker exec k2-spark-iceberg sh -c 'for f in /tmp/k2-airflow-*.pid; do [ -e "$f" ] && echo "$f $(cat "$f")"; done'

# 3. How long it has been running. An ingest past an hour has outlived its timeout.
docker exec k2-spark-iceberg ps -o pid,etime,args -p <pid>

# 4. Is the Airflow try it belongs to still running?
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest --state running
```

Then one of:

- **A job started by hand** (a runbook step, `make lake-rebuild`, a chaos script): let it
  finish. If it will run for many cycles, pause the DAG so the skips stop, and unpause it
  after:
  `docker exec k2-airflow-scheduler airflow dags pause lake_ingest`, then
  `docker exec k2-airflow-scheduler airflow dags unpause lake_ingest`.
- **A driver no Airflow try owns** (its run already failed, or the scheduler was
  restarted under it): stop it. A killed ingest commits nothing or everything, never half
  ([lake-recovery.md §5](./lake-recovery.md#5-ingest-killed-mid-run)), and the next slot
  resumes from the last committed offsets.

Run on the live stack 2026-11-25 against a driver left behind on purpose (its parent shell killed): the driver was gone 284 ms after the TERM and the next scheduled run succeeded.

```bash
docker exec k2-spark-iceberg kill -TERM <pid>
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'   # nothing left
```

**Measured**, not yet: no lock has been held across three slots under Airflow outside a
maintenance run, which the rule excludes (the nightly `lake_maintenance` holds it for several slots).

---

## 4. Catch up after a long stall, with a bigger driver

**When**, after an outage the 5-minute ingest reads `--max-offsets-per-partition` per
partition per run and cannot close the gap, or dies of heap at its 1536m default. A catch-up
is a manual run with two params, not a shell loop: `driver_memory` sets
`K2_LAKE_DRIVER_MEMORY` for that run and `max_offsets_per_partition` the flag (0 reads to
latest). The Spark container is capped at 8 GiB; leave room for the 2g maintenance driver
if it could start meanwhile. The manual run takes the DAG's only slot (`max_active_runs=1`),
so no scheduled run starts beside it; one already in flight finishes first.

The `docker exec` form in [lake-ingest-lag.md](./lake-ingest-lag.md) does the same outside
Airflow, and the scheduled runs skip at the lock while it holds it.

Run on the live stack 2026-11-25 during the shadow, so the run was a probe: its `running:` line carried `--env=K2_LAKE_DRIVER_MEMORY=2g` and `--max-offsets-per-partition=1000000`, and a trigger with `"driver_memory": "2 GB"` was refused with exit 1. A real catch-up through this trigger ran after the cutover: `manual__2026-11-25T07:22:45`, task 07:24:23 to 07:27:20, `success`, with `driver_memory` 2g and `max_offsets_per_partition` 1000000 (ADR-034 Outcome).

```bash
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest \
  --conf '{"driver_memory": "2g", "max_offsets_per_partition": 1000000}'
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest
```

Repeat until `LakeIngestLagHigh` clears; each run reads from where the last committed.

---

## 5. `lake_ingest` not running: a stale DAG folder, or a forgotten pause

**Symptom**, no new `lake_ingest` run in the UI or in `list-runs`, the `raw.messages` commit
age climbing, and every container healthy. Nothing else looks wrong.

**Detection**, `AirflowLakeIngestNotRunning`: no `lake_ingest` task has finished, in any
state, for 15 minutes held for 5, while none is running and no Airflow `lake_maintenance`
or `lake_rebuild` holds the lock or has in the last 15 minutes. That is §3's grace, for the
same reason: a `lake_ingest` paused under a long rebuild and unpaused as it ends has no
finish until the first run after it, up to 5 minutes to the slot plus the run. It is also
quiet for 15 minutes after the `airflow-statsd` exporter restarts, when the old process's
count and the new one can match by chance; if nothing finishes after the restart it fires
20 minutes after it. `LakeIngestFailed` follows 35 minutes after the last
commit, and `make health` step (3) fails with "no lake_ingest run finished in the last
30 min". A deliberate pause longer than 20 minutes (a deep `make lake-verify`, a chaos
run) fires it as well: that is the forgotten-pause case caught early.

**Cause**, switching the live checkout between commits that add or remove a mounted
directory. `./docker/airflow/dags` is a directory bind mount held by `airflow-scheduler`
and `airflow-dag-processor`; a checkout that removes the directory and puts it back gives
the host a new directory, and both containers keep the old one, empty. The dag-processor
then marks the DAGs stale and the scheduler stops creating their runs. This is the
[`CLAUDE.md`](../../CLAUDE.md) bind-mount gotcha, for a directory. A DAG paused by hand and
not unpaused looks the same from outside.

**Observed 2026-11-25**, before this alert existed. At about 05:06 UTC the live checkout
was switched to a commit that did not contain `docker/airflow/dags/`, and back. Both
Airflow containers saw an empty directory; `airflow dags details lake_ingest` showed
`is_stale True` and `next_dagrun_logical_date` stuck at the 05:11 slot. The scheduler
created no run from 05:11 to 05:46, eight slots. Every container stayed healthy and no
alert fired: `airflow_scheduler_heartbeat` kept rising, `airflow_dagbag_size` stayed 3,
`airflow_dag_processing_last_run_seconds_ago` stayed about 27 and
`airflow_dag_processing_import_errors` 0. The one metric that showed it was
`sum(increase(airflow_ti_finish{dag_id="lake_ingest"}[15m]))` at 0, about 3 normally,
which is what this alert reads. The recreate in step 3a fixed it: the DAG was no longer
stale within a minute, and exactly one run was created, for the latest slot, with no
replay of the eight missed.

**Expected behaviour**, nothing recovers by itself: a stale mount stays stale until its
containers are recreated, and a paused DAG stays paused.

**Recovery**. Steps 1 and 3a are what ran on 2026-11-25.

Run on the live stack 2026-11-25 (read-only, healthy mount): both containers and the host
listed `lake.py` at 9473 bytes. A stale mount listing no `lake.py` was seen only on the
day's fault, not in this listing. Step 3b's unpause ran once, at the Airflow cutover on 2026-11-25, and printed
`lake_ingest | True`; it has not been run against a fault.

```bash
# 1. Stale, or paused?
docker exec k2-airflow-scheduler airflow dags details lake_ingest | grep -E 'is_stale|is_paused|next_dagrun'

# 2. What the containers see against what the host has. lake.py missing inside, or an
#    older one, is the stale mount.
docker exec k2-airflow-scheduler     ls -la /opt/airflow/dags
docker exec k2-airflow-dag-processor ls -la /opt/airflow/dags
ls -la docker/airflow/dags

# 3a. Stale: recreate both holders of the mount.
docker compose up -d --force-recreate --no-deps airflow-scheduler airflow-dag-processor

# 3b. Paused, and nothing deliberate (a chaos run, lake-verify) still holds the pause:
docker exec k2-airflow-scheduler airflow dags unpause lake_ingest

# 4. Confirm: not stale, then one run for the latest slot within a scheduler loop.
docker exec k2-airflow-scheduler airflow dags details lake_ingest | grep -E 'is_stale|next_dagrun'
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest | head -3
```

**Measured**, 2026-11-25, one occurrence: 35 minutes with no run (05:11 to 05:46 UTC),
and back within a minute of the recreate. The alert itself has not yet fired on a fault.
