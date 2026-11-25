# Runbook: The Airflow lake schedules: cutover, rollback, recovery

The lake ingest, maintenance and rebuild run as three Airflow 3 DAGs
([ADR-034](../adr/ADR-034-airflow-3-replaces-prefect.md), `docker/airflow/dags/lake.py`),
each a single task that runs one `docker/lake` script in the Spark container. This covers
the one-time [cutover from Prefect](#cutover-from-prefect), the
[standing policy](#standing-policy-until-the-archive) and the
[rollback to Prefect](#rollback-to-prefect), then the three alerts in
`docker/prometheus/rules/airflow-alerts.yml`: the scheduler stopping, a task failing, and
`lake_ingest` refused the writer lock run after run. It does not cover what a failing
script found: that is [lake-ingest-lag.md](./lake-ingest-lag.md),
[lake-audit-failed.md](./lake-audit-failed.md) and [lake-recovery.md](./lake-recovery.md).

**Airflow is the only scheduler of the lake writes** from the cutover on. Prefect's two
services sit behind the compose `legacy` profile, stopped: a plain `docker compose up -d`
does not start them, and nothing else depends on them. They stay until the archive pull
request of plan phase P2, after 24 consecutive green `lake_ingest` runs.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> **The cutover, the standing policy and the rollback below have not been run against the
> stack yet.** Each of their blocks carries a `VERIFY:` line naming what has to be run and
> checked. The rollback is documented, not drilled, by the owner's decision. §1 to §4 say
> what their own drills, on 2026-11-25 during the schedule shadow, did and did not cover.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Scheduler heartbeat lost | < 10 min | alert 301 s after the stop; cleared 59 s after `up` (2026-11-25) |
| 2 | A lake task failed after its retries | depends on what the script found | alert 27 s after the failure (2026-11-25) |
| 3 | `lake_ingest` skipped three runs in a row: the lock is held | < 15 min | orphan driver gone 284 ms after TERM (2026-11-25); the alert not yet induced |
| 4 | Catch up after a long stall, with a bigger driver | a manual run | not measured |

---

## Cutover from Prefect

One time, by hand, on the stack as pull request 1 left it: Prefect writing, `lake_ingest`
running the shadow's write-nothing probe, `lake_maintenance` paused. No data moves: the
lock in `docker/lake/lock.py` already allows one writer, and the ingest reads its position
from the last snapshot ([ADR-022](../adr/ADR-022-exactly-once-via-snapshot-offsets.md)),
whoever wrote it.

**Before you start.** The shadow gate passed (`bash scripts/airflow-shadow-check.sh`
printed `shadow check: PASS` over its 2 h window). Pick a time well clear of 03:00 UTC.
Steps 1 to 3 run with the stack's checkout still at pull request 1's commit, so read this
page from somewhere else: updating the checkout changes `docker/airflow/dags/lake.py`
under the running scheduler, and that is step 4.

VERIFY: run steps 1 to 3 on the live stack; check that `schedule ls` prints each schedule
`id` with `"active": true` (and `false` when run again after the pause), that the queue reads
`"is_paused": true` after its pause, that Airflow's pause prints `lake_ingest` with
`is_paused` `False` (the old state it changed), and that `pgrep` ends up printing nothing.

```bash
date -u +%FT%TZ    # the cutover's start, for ADR-034's Outcome

# 1. Pause Prefect: the ingest schedule, then the work queue. These are the commands
#    scripts/chaos/lib.sh ran before the cutover. A schedule pause deletes only future
#    runs; the paused queue stops past-due ones and lake-maintenance-daily as well.
docker exec k2-prefect-server prefect deployment schedule ls lake-ingest/lake-ingest-5min -o json
docker exec k2-prefect-server prefect deployment schedule pause lake-ingest/lake-ingest-5min <schedule-id>   # each active id
docker exec k2-prefect-server prefect work-queue pause default --pool lake
docker exec k2-prefect-server prefect work-queue inspect default --pool lake -o json   # "is_paused": true

# 2. Pause Airflow's probe as well, so nothing of Airflow's starts while its DAG file
#    changes under it.
docker exec k2-airflow-scheduler airflow dags pause lake_ingest

# 3. Wait out whatever is in flight: Prefect's last ingest, or a probe. Repeat until
#    pgrep prints nothing and no lake_ingest task is queued or running.
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance)\.py'
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest --state running
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run lake_ingest <run_id>   # for a run listed above
```

VERIFY: run step 4 on the live stack; check that both recreated containers go healthy,
that both `grep -c` print `0` (a stale bind mount would still show the probe), and that
`list-import-errors` prints no error.

```bash
# 4. Deploy the new DAG file. Both containers that mount docker/airflow/dags are
#    recreated: a directory bind mount can keep the old file (CLAUDE.md, bind-mount
#    gotcha), and recreating them is cheaper than finding out.
git pull --ff-only                         # the stack's checkout, to the merged cutover commit
docker compose up -d --force-recreate --no-deps airflow-scheduler airflow-dag-processor
docker compose ps airflow-scheduler airflow-dag-processor     # both healthy, about a minute
docker exec k2-airflow-scheduler     grep -c -- --probe /opt/airflow/dags/lake.py    # 0
docker exec k2-airflow-dag-processor grep -c -- --probe /opt/airflow/dags/lake.py    # 0
docker exec k2-airflow-dag-processor airflow dags reserialize
docker exec k2-airflow-scheduler airflow dags list-import-errors
```

VERIFY: run steps 5 and 6 on the live stack; check that a `lake_ingest` run starts within
a scheduler loop of the unpause, that its task log's `running:` line ends in
`/home/iceberg/lake/ingest.py` with no `--probe`, that the task ends `success`, and that the
commit age drops to under a minute or two after it.

```bash
# 5. Unpause lake_ingest. With catchup=False the scheduler runs the most recent slot at
#    once, then every 5 minutes.
docker exec k2-airflow-scheduler airflow dags unpause lake_ingest

# 6. Watch the first real commit: the run, its task, the log, then the lake itself.
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest | head -5
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run lake_ingest <run_id>   # ingest | success
docker exec k2-airflow-scheduler sh -c \
  "find /opt/airflow/logs -path '*dag_id=lake_ingest*' -name '*.log' | sort | tail -1 | xargs grep -E 'running:|stage 1'"
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_last_commit_ts_seconds{table="raw.messages"}' \
  | jq -r '.data.result[].value[1]'
```

VERIFY: run steps 7 and 8 on the live stack; check that the unpause starts a
`scheduled__<today>T03:00:00+00:00` run without a trigger, that the `lake_ingest` runs
during it end with the task `skipped` and none `failed`, that `make health` prints
`PASS (... skipped while lake_maintenance holds the lake lock ...)` for step (3) while it
runs, and the maintenance run's duration.

```bash
# 7. Unpause lake_maintenance. Its is_paused_upon_creation=False does nothing here: the
#    database already holds the DAG, paused by the shadow. Unpausing starts the most
#    recent 03:00 slot at once (catchup=False runs the latest slot), and that run is the
#    lock test: a second maintenance today is an ordinary one. If no run appears within
#    two minutes, trigger it: docker exec k2-airflow-scheduler airflow dags trigger lake_maintenance
docker exec k2-airflow-scheduler airflow dags unpause lake_maintenance
docker exec k2-airflow-scheduler airflow dags list-runs lake_maintenance | head -3

# 8. While it holds the lock, the ingest steps aside: exit 75, task skipped, run success.
docker exec k2-spark-iceberg pgrep -af 'lake/maintenance\.py'
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest | head -4
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run lake_ingest <run_id>   # ingest | skipped
make health
#    Once it ends: lake_maintenance's run is success and the next lake_ingest task is success.
```

VERIFY: run steps 9 to 11 on the live stack; check that both Prefect containers show
`Exited`, that `make health` ends `health: PASS`, that `make lake-verify` ends
`✓ lake-verify passed`, and the last line of each gate.

```bash
# 9. Stop Prefect. A plain `docker compose up -d` leaves the legacy profile alone, and
#    `restart: unless-stopped` keeps a stopped container stopped across a reboot.
docker compose --profile legacy stop prefect-server prefect-worker
docker compose --profile legacy ps -a prefect-server prefect-worker      # both Exited

# 10. The stack, and the lake's exit criteria, with Airflow as the only scheduler.
make health
make lake-verify

# 11. The rest of `make gates`, without parity-ohlcv: it needs the P0 re-pin, which needs
#     a complete UTC day and measures the data, not the scheduler. Both parity gates run
#     after the re-pin: make parity-ohlcv && make parity-bars
make test && make parity-bars && make check-alerts && make check-docs
date -u +%FT%TZ    # the cutover's end
```

Then replace each `VERIFY:` above with what the commands printed, and fill the `MEASURE:`
gaps in ADR-034's Outcome.

---

## Standing policy until the archive

The owner's rule from the cutover until the archive pull request: **if two scheduled
`lake_ingest` runs in a row fail (not skip), or `raw.messages` goes 20 minutes without a
commit while no `lake_maintenance` run is running, diagnose in place for up to 30 minutes
with §1 to §3, then [roll back](#rollback-to-prefect).** A skip is the lock doing its
job and does not count. Twenty minutes is tighter than `LakeIngestFailed` (30 minutes plus
its 5 for), on purpose: it starts the clock before the page does.

VERIFY: run on the live stack after the cutover; check that the first command lists runs
newest first and the second prints the commit age in seconds.

```bash
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest --state failed | head -4
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_last_commit_ts_seconds{table="raw.messages"}' \
  | jq -r '.data.result[].value[1]'                                  # over 1200 = the trigger
docker exec k2-airflow-scheduler airflow dags list-runs lake_maintenance --state running
```

---

## Rollback to Prefect

**Not exercised.** Documented, not drilled, by the owner's decision. Every block is a
`VERIFY:`. Prefect's state survives in `prefect-db` while its containers are stopped: the
deployments, the paused ingest schedule and the paused work queue as the cutover left
them. The Prefect worker re-registers both deployments when it starts
(`docker/lake/flows/deploy_lake.py`, `paused=False`), so its schedules can come back
active by themselves; the work queue does not, and nothing dispatches until step 4.

VERIFY: run steps 1 and 2 on the live stack; check that each pause prints the DAG with
`False` (its old state) and that `pgrep` and the task states end up showing nothing in
flight.

```bash
# 1. Stop Airflow's lake writes: both scheduled DAGs.
docker exec k2-airflow-scheduler airflow dags pause lake_ingest
docker exec k2-airflow-scheduler airflow dags pause lake_maintenance

# 2. Wait for the in-flight job. Repeat until pgrep prints nothing and no task listed
#    is queued or running.
docker exec k2-spark-iceberg pgrep -af 'lake/(ingest|maintenance|rebuild)\.py'
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest --state running
docker exec k2-airflow-scheduler airflow dags list-runs lake_maintenance --state running
docker exec k2-airflow-scheduler airflow tasks states-for-dag-run <dag_id> <run_id>   # for a run listed above
```

VERIFY: run step 3 on the live stack; check that both containers start, that
`prefect-server` goes healthy, and that the worker log shows both deployments registered
and the worker polling pool `lake`.

```bash
# 3. Start Prefect from the legacy profile.
docker compose --profile legacy up -d prefect-server prefect-worker
docker compose --profile legacy ps prefect-server prefect-worker
docker logs --tail 50 k2-prefect-worker
```

VERIFY: run step 4 on the live stack; check that every schedule of both deployments reads
`"active": true` and the queue `"is_paused": false`. These are the resume commands
scripts/chaos/lib.sh ran before the cutover, kept here verbatim because the helper no
longer runs them.

```bash
# 4. Resume the Prefect schedule and its work queue.
docker exec k2-prefect-server prefect deployment schedule ls lake-ingest/lake-ingest-5min -o json
docker exec k2-prefect-server prefect deployment schedule resume lake-ingest/lake-ingest-5min <schedule-id>   # each inactive id
docker exec k2-prefect-server prefect deployment schedule ls lake-maintenance/lake-maintenance-daily -o json
docker exec k2-prefect-server prefect work-queue resume default --pool lake
docker exec k2-prefect-server prefect work-queue inspect default --pool lake -o json   # "is_paused": false
```

VERIFY: run step 5 on the live stack; check that a `lake-ingest` flow run reaches
`Completed` and that the commit age falls to under a minute or two after it.

```bash
# 5. Confirm a commit. A run left late from the cutover may dispatch first: each is an
#    ordinary ingest from the committed offsets. `make health` step (3) reads Airflow and
#    reports FAIL while Prefect writes; its other steps apply.
docker exec k2-prefect-server prefect flow-run ls --flow-name lake-ingest --limit 3
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - k2_lake_last_commit_ts_seconds{table="raw.messages"}' \
  | jq -r '.data.result[].value[1]'
```

**The way back to Airflow**, once the cause is fixed: the cutover's steps 1 and 3
(pause Prefect's schedule and queue, wait out the in-flight run), then the following.

VERIFY: run on the live stack after a rollback; check that each unpause prints the DAG
with `True` (its old state), that `lake_ingest` commits as in cutover step 6, that
unpausing `lake_maintenance` starts its latest 03:00 slot at once as in cutover step 7,
and that `make health` ends `health: PASS`.

```bash
docker exec k2-airflow-scheduler airflow dags unpause lake_ingest
docker exec k2-airflow-scheduler airflow dags unpause lake_maintenance
docker compose --profile legacy stop prefect-server prefect-worker
make health
```

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
15 minutes and clears by itself; the run's log is the record.

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
lock on purpose.

**Expected behaviour**, one or two skips are the lock doing its job: a maintenance or a
manual job held it and the ingest stepped aside. Three in a row with nothing of Airflow's
running means something is holding the lock that should not be, most often a driver left
behind by a killed run (§1) or a job started by hand. It clears at the first run that gets
the lock.

**Recovery**

Run on the live stack 2026-11-25. `pgrep -af` prints each running lake job, and it also prints the `sh -c` wrapper that started it: the writer is the line that begins `python3`. `ps -o pid,etime,args` works in the Spark image. Not yet induced: three skips in a row under Airflow, which needs `lake_ingest` writing for real (after the cutover).

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
maintenance run, which the rule excludes (cutover step 8 holds it for one).

---

## 4. Catch up after a long stall, with a bigger driver

**When**, after an outage the 5-minute ingest reads `--max-offsets-per-partition` per
partition per run and cannot close the gap, or dies of heap at its 768m default. A catch-up
is a manual run with two params, not a shell loop: `driver_memory` sets
`K2_LAKE_DRIVER_MEMORY` for that run and `max_offsets_per_partition` the flag (0 reads to
latest). The Spark container is capped at 8 GiB; leave room for the 2g maintenance driver
if it could start meanwhile. The manual run takes the DAG's only slot (`max_active_runs=1`),
so no scheduled run starts beside it; one already in flight finishes first.

The `docker exec` form in [lake-ingest-lag.md](./lake-ingest-lag.md) does the same outside
Airflow, and the scheduled runs skip at the lock while it holds it.

Run on the live stack 2026-11-25 during the shadow, so the run was a probe: its `running:` line carried `--env=K2_LAKE_DRIVER_MEMORY=2g` and `--max-offsets-per-partition=1000000`, and a trigger with `"driver_memory": "2 GB"` was refused with exit 1. Not yet run: a real catch-up through this trigger, which needs the cutover.

```bash
docker exec k2-airflow-scheduler airflow dags trigger lake_ingest \
  --conf '{"driver_memory": "2g", "max_offsets_per_partition": 1000000}'
docker exec k2-airflow-scheduler airflow dags list-runs lake_ingest
```

Repeat until `LakeIngestLagHigh` clears; each run reads from where the last committed.
