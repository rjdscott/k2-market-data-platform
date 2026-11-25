# Runbook: Recover the Airflow lake schedules

The lake ingest, maintenance and rebuild run as three Airflow 3 DAGs
([ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md), `docker/airflow/dags/lake.py`),
each a single task that runs one `docker/lake` script in the Spark container. This covers
the three alerts in `docker/prometheus/rules/airflow-alerts.yml`: the scheduler stopping,
a task failing, and `lake_ingest` refused the writer lock run after run. It does not cover
what a failing script found: that is [lake-ingest-lag.md](./lake-ingest-lag.md),
[lake-audit-failed.md](./lake-audit-failed.md) and [lake-recovery.md](./lake-recovery.md).

**Schedule shadow.** Until the cutover pull request, `lake_ingest` runs `ingest.py --probe`
(reads the topics, writes nothing, takes no lock) and `lake_maintenance` is paused. Prefect
is still the writer. During the shadow a failure here costs no data, and §3 cannot fire.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **No command on this page has been run against the stack yet.** Each block carries a
> `VERIFY:` line naming what has to be run and checked before this page can be trusted. No
> failure has been induced, so no MTTR is measured.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Scheduler heartbeat lost | < 10 min | not measured, no failure induced yet |
| 2 | A lake task failed after its retries | depends on what the script found | not measured, no failure induced yet |
| 3 | `lake_ingest` skipped three runs in a row: the lock is held | < 15 min | not measured, no failure induced yet |
| 4 | Catch up after a long stall, with a bigger driver | a manual run | not measured |

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

VERIFY: run all of this block on the live stack; check that the `up` query prints `1`
for a running exporter, that `docker compose ps` lists both services, and that the job
check prints `Found one alive job.` after the restart.

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

**Measured**, not yet: no scheduler has been stopped on purpose.

---

## 2. A lake task failed after its retries

**Symptom**, a red run in the Airflow UI for `lake_ingest`, `lake_maintenance` or
`lake_rebuild`.

**Detection**, `AirflowTaskFailed`, over `airflow_ti_finish{state="failed"}`. A try that is
retried ends `up_for_retry` and does not fire; only the final failure does. It fires for
15 minutes and clears by itself; the run's log is the record.

**Expected behaviour**, `lake_ingest` retries once after 60 s, and the next slot runs five
minutes later whatever happened: the ingest resumes from the offsets in its last snapshot
([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)), so a failed run loses
nothing and needs no re-run. `lake_maintenance` and `lake_rebuild` do not retry: a failed
audit is a finding.

**Recovery**

VERIFY: run all of this block on the live stack after a failed run exists (one can be made
by stopping `spark-iceberg` across a slot); check the run id format the first command
prints, the `find` path under `/opt/airflow/logs`, and that `pgrep` finds nothing once the
run has ended.

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

**Measured**, not yet: no failure has been induced under Airflow.

---

## 3. `lake_ingest` skipped three runs in a row

**Symptom**, `lake_ingest` runs shown as skipped in the UI, one per slot, and
`raw.messages` not committing.

**Detection**, `AirflowLakeIngestSkippedRepeatedly`, over
`airflow_ti_finish{dag_id="lake_ingest",state="skipped"}`. A skip is `ingest.py` exiting 75:
another lake writer holds `/tmp/k2-lake-ingest.lock` in `k2-spark-iceberg`. It does not fire
while Airflow's own `lake_maintenance` or `lake_rebuild` is running, because both hold the
lock on purpose.

**Expected behaviour**, one or two skips are the lock doing its job: a maintenance or a
manual job held it and the ingest stepped aside. Three in a row with nothing of Airflow's
running means something is holding the lock that should not be, most often a driver left
behind by a killed run (§1) or a job started by hand. It clears at the first run that gets
the lock.

**Recovery**

VERIFY: run all of this block on the live stack; check that `pgrep -af` prints the PID and
script of a running lake job, that the pidfile loop prints the `/tmp/k2-airflow-*.pid`
files of Airflow-started drivers, and that `ps -o pid,etime,args` works in the Spark image.
Induce it once by holding the lock with a manual `maintenance.py --audit-only` while
`lake_ingest` runs for real (after the cutover).

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

VERIFY: run on the live stack against a real orphan driver; check that the driver exits
within 10 s of the TERM and that the next `lake_ingest` run succeeds.

```bash
docker exec k2-spark-iceberg kill -TERM <pid>
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'   # nothing left
```

**Measured**, not yet: the alert cannot fire during the shadow, and no lock has been held
across three slots under Airflow.

---

## 4. Catch up after a long stall, with a bigger driver

**When**, after an outage the 5-minute ingest reads `--max-offsets-per-partition` per
partition per run and cannot close the gap, or dies of heap at its 768m default. A catch-up
is a manual run with two params, not a shell loop: `driver_memory` sets
`K2_LAKE_DRIVER_MEMORY` for that run and `max_offsets_per_partition` the flag (0 reads to
latest). The Spark container is capped at 8 GiB; leave room for the 2g maintenance driver
if it could start meanwhile. The manual run takes the DAG's only slot (`max_active_runs=1`),
so no scheduled run starts beside it; one already in flight finishes first.

**During the shadow** the DAG runs the probe and these params change nothing. Use the
`docker exec` form in [lake-ingest-lag.md](./lake-ingest-lag.md) until the cutover.

VERIFY: run on the live stack after the cutover; check that the run's log shows
`--env=K2_LAKE_DRIVER_MEMORY=2g` and `--max-offsets-per-partition=1000000` in its `running:`
line, and that a value outside the params' pattern (`"2 GB"`) is refused at trigger time.

```bash
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest \
  --conf '{"driver_memory": "2g", "max_offsets_per_partition": 1000000}'
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest
```

Repeat until `LakeIngestLagHigh` clears; each run reads from where the last committed.
