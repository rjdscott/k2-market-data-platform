# ADR-034: Airflow 3 replaces Prefect; the default profile's ceiling is 20 CPU / 36 GiB

**Status:** Accepted
**Date:** 2026-11-25
**Author:** Rob Scott
**Category:** Orchestration, Infrastructure

---

## Context

The lake has two scheduled jobs and one manual one. Prefect runs the two as flows that add
no logic: each is `docker exec k2-spark-iceberg python3 /home/iceberg/lake/<script>` over
the mounted Docker socket (`docker/lake/flows/lake_flows.py`). Mutual exclusion is a
`flock` the scripts take (`docker/lake/lock.py`); an ingest that finds it held exits 75.
Exactly-once state is in the Iceberg snapshot summary
([ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md)). Nothing about data position
lives in the orchestrator, which is what makes it replaceable.

[ADR-031](ADR-031-governed-self-serve-platform.md) decided that Airflow 3 replaces Prefect
for the platform layer, and that the default profile gets a larger budget on the record.
[ADR-008](ADR-008-eliminate-prefect-orchestration.md) had rejected Airflow as heavier than
Prefect under 16 CPU; that budget is what this ADR moves.

Two incidents on this stack on 2026-11-24 set requirements the orchestrator must meet,
whichever it is:

- **A killed worker left a run marked running**, and that run held the deployment's only
  concurrency slot for hours. When the slot freed, months-old queued runs replayed back to
  back. A scheduler must not let a dead run hold the slot, must not queue a run per missed
  interval, and must not replay intervals at all: the ingest reads from its committed
  offsets, so a late run reads what a punctual one would have.
- **After a long stall, a normal ingest could not catch up at its default driver heap.**
  The catch-up was a shell loop of `docker exec` with a bigger `K2_LAKE_DRIVER_MEMORY` and
  `--max-offsets-per-partition`. The fix to the heap is not this ADR's, but a catch-up has
  to be something the orchestrator can run with parameters.

A third force is the timeout. Prefect's `subprocess.run(..., timeout=...)` kills the
`docker exec` client. The driver inside the Spark container keeps running and keeps the
lock, so the run that timed out still blocks every run after it (plan risk table).

The budget: 14.60 CPU / 25.625 GiB declared at steady state across 15 services, 16.10 CPU /
27.125 GiB at the bootstrap peak across 19, which was 0.10 CPU over ADR-010's 16 CPU at every
`docker compose up` ([ADR-010](ADR-010-resource-budget.md), last Outcome). The engine on the
reference host reports 28 CPUs and 53,149,339,648 bytes (49.50 GiB) of memory
(`docker info --format '{{.NCPU}} {{.MemTotal}}'`, 2026-11-25).

---

## Decision

**We will schedule the lake with Airflow 3.3.2 in place of Prefect, as three hand-written
DAGs under a LocalExecutor that keep today's dispatch, and set the default profile's ceiling
to 20 CPU / 36 GiB at the bootstrap peak, because the platform layer needs the orchestrator
ADR-031 chose and the two incidents above need guarantees Prefect's deployment did not
give.** Scope: the lake schedules and the budget clause. The ingest code path does not
change, so ADR-022 is untouched.

It lands in three pull requests (plan phase P2). This one runs Airflow beside Prefect in a
schedule shadow: `lake_ingest` runs `ingest.py --probe`, which writes nothing and takes no
lock, on Prefect's cron, and `lake_maintenance` starts paused. The cutover switches the
probe to the real command and pauses Prefect; the archive removes Prefect.

What the DAGs guarantee (`docker/airflow/dags/lake.py`, tested in
`docker/airflow/tests/test_lake_dags.py`):

- **One run at a time, no replay.** `max_active_runs=1` and `catchup=False` on every DAG,
  and the cron strings resolve to Airflow 3's `CronTriggerTimetable`, which has no data
  interval to fill in. The scheduler creates no run while the slot is taken, so nothing
  queues behind a slow one. On first start, or after a stop, it runs the latest slot once.
- **A dead run cannot hold the slot.** A task whose process stops heartbeating is failed
  after `task_instance_heartbeat_timeout` (300 s), and every DAG has a `dagrun_timeout`
  just above its task's tries, so a run stuck in any state ends.
- **A timeout stops the driver.** The task runs the script under a shell that records the
  driver's PID in the Spark container. Airflow calls the operator's `on_kill` on
  `execution_timeout` and on SIGTERM (Airflow 3.3.2 `task_runner.py`); it sends TERM, then
  KILL after 10 s, to that PID. The Spark JVM exits when its driver does: 2 s after a TERM
  and after a KILL, measured in the Spark image. The flock goes with the driver's process.
- **Exit 75 is skipped for `lake_ingest` only.** Exit 2 is not mapped: Python returns it
  for a missing script and argparse for a bad flag.
- **A catch-up is a manual run with params.** `driver_memory` maps to
  `K2_LAKE_DRIVER_MEMORY` and `max_offsets_per_partition` to the flag; unset means the
  script's own default. Both are validated at trigger time.
- `lake_ingest` keeps Prefect's `1-59/5 * * * *`, one retry after 60 s and 3600 s;
  `lake_maintenance` keeps `0 3 * * *` and 7200 s with no retry; `lake_rebuild` is manual,
  24 h, and defaults to `--dry-run`.

**Privilege.** `prefect-worker` holds `/var/run/docker.sock` as root. Whoever holds that
socket controls the Docker engine, and can start a privileged container with the host's
filesystem mounted, so it is root on the engine whatever the uid inside. Airflow gets the
same socket and no more: only `airflow-scheduler`, where LocalExecutor runs tasks, mounts it,
as the image's uid 50000 with the socket's group added (`K2_DOCKER_GID`). The api-server and
the dag-processor do not. The UI that can trigger a DAG is published on 127.0.0.1 only and
behind SimpleAuthManager's generated admin password; Prefect's is published on every
interface and unauthenticated.

**Metadata database.** Airflow uses an `airflow` database and role on the existing
`prefect-db`. `max_connections` goes from 100 to 200 and the `airflow` role is capped at 100
(`docker/airflow/init-db.sh`), so Airflow fails on its own connection limit before it can
take the connections Prefect and the Lakekeeper catalog use, both as the `prefect` role.

**Metrics.** Airflow sends statsd with InfluxDB-style tags and no legacy names
(`statsd_influxdb_enabled`, `legacy_names_on=False`) to `prom/statsd-exporter`, which turns
the tags into labels with no mapping file. Three alerts in
`docker/prometheus/rules/airflow-alerts.yml`, each with a promtool test and a section of
[airflow-scheduling.md](../runbooks/airflow-scheduling.md); a `K2 Airflow` dashboard with
run duration, run lateness, failures, skips, heartbeat and parse time. Airflow 3 has no
SLA-miss feature; run lateness (`dagrun.schedule_delay`) replaces it.

**Budget.** This ADR supersedes ADR-010's 16 CPU / 40 GB clause and nothing else in it. The
default profile's ceiling is 20 CPU / 36 GiB, summed over every default-profile service's
`deploy.resources.limits`, one-shots included, and `scripts/check-budget.py` fails CI over
it. GiB because compose limits are GiB. The `governance` profile is outside it.

### How the versions were chosen (2026-11-25)

Every image tag and pip package is pinned. Each version was read from its source that day,
not from memory:

| Component | Pinned | Newest | Why this one |
|---|---|---|---|
| Airflow | 3.3.2 (PyPI, 2026-09-17) | 3.4.0 (2026-11-06) | astronomer-cosmos 1.16.0 (2026-11-03, its latest) added 3.4 to its test matrix as the pre-release 3.4.0b1, tracking a moving constraints branch (cosmos pull request 3049). Its compatibility policy's newest Runtime series in maintenance is 3.3. So 3.3 is the newest minor cosmos supports outside beta, and 3.3.2 its newest patch |
| Image | `apache/airflow:slim-3.3.2-python3.12` | | Python 3.12 is the default of the `slim-3.3.2` tag and the Prefect worker's Python; cosmos needs 3.11 or later. The slim image ships `docker-ce-cli` 29.8.1 and `psql`, which the build asserts |
| Extras | `apache-airflow[postgres,statsd]==3.3.2` | | with `constraints-3.3.2/constraints-3.12.txt`, Airflow's published set for that release and Python: providers-postgres 7.0.2, psycopg2-binary 2.9.13, statsd 4.0.1 |
| statsd exporter | `prom/statsd-exporter:v0.31.0` | v0.31.0 (2026-09-02) | latest release |
| lake-metrics base | `python:3.12.16-slim-trixie` | | newest 3.12 patch on Docker Hub; the exporter's Python stays the minor it has run on |
| prometheus-client | 0.26.0 | 0.26.0 (2026-07-24) | latest release, up from 0.21.1; `metrics.py --self-check` and `--serve` pass on it |

Cosmos itself is not installed here; dbt arrives in P3. It decides the version now so that
P3 does not force a downgrade. Dependabot ignores `apache/airflow`: its tag, the pip pin and
the constraints URL move together, by hand.

---

## Rationale

- **The incidents are scheduler semantics, and Airflow's are the ones needed.** A slot that
  only frees when a heartbeat times out, no runs created behind a taken slot, and a
  timetable with no intervals to replay are defaults or one keyword each.
- **The kill belongs where the task is.** An operator `on_kill` runs in the task process on
  both a timeout and a SIGTERM. `BashOperator`'s `on_kill` would kill the `docker exec`
  client, which is the failure above.
- **LocalExecutor fits the workload.** Three single-task DAGs, at most one ingest and one
  maintenance at once. Four slots cover a manual rebuild beside them.
- **Bootstrap, not steady state.** The one-shots run beside everything else at
  `docker compose up`; ADR-010 gated the steady sum and was over at every boot.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Keep Prefect and fix the slot lease and the backlog in it | ADR-031 chose Airflow for the platform layer; fixing Prefect and then replacing it is the work twice |
| Airflow 3.4.0 | cosmos 1.16.0 supports it only as a pre-release; the plan's escape hatch is one minor back |
| CeleryExecutor | a broker and a worker service for a workload of four slots |
| A triggerer and deferrable tasks | nothing here defers; it is a service and a budget line for nothing |
| One task per ingest stage | maintenance could take the lock between stages; the stages stay one process |
| A Docker socket proxy that allows only `exec` | one more service; it narrows the API but not what the Spark container can do. Revisit trigger below |
| A second PostgreSQL for Airflow | a service and its memory; the role cap gives the isolation that matters |
| A statsd mapping file over the legacy names | legacy names embed `dag_id` in the name and arrive beside the tagged ones, twice per event |

---

## Consequences

**Easier:** a catch-up is `airflow dags trigger lake_ingest --conf '{...}'`; a stuck lock
and a lost scheduler page; the orchestrator dependency that dbt needs in P3 is in place.

**Harder:** five services where Prefect has two, and a parallel run while both exist: the
Prefect pair (1.5 CPU / 1.5 GiB) leaves at the archive pull request. During the shadow two
drivers share the Spark container at each slot, Prefect's ingest and Airflow's probe.
Airflow's logs live in the scheduler container and go with it on a recreate. The UI password
is regenerated on every api-server recreate.

**Committed to:** every lake DAG keeps `max_active_runs=1`, `catchup=False`, an
`execution_timeout` and a `dagrun_timeout`, enforced by the DAG tests in `make test` and CI;
the 20 CPU / 36 GiB bootstrap ceiling in CI; the plan's names kept (below).

**What keeps the name.** The PostgreSQL service `prefect-db`, its `prefect` role and
database and the `PREFECT_DB_*` variables keep their names. The role is physically named
`prefect` in every existing volume, and renaming it breaks every existing `.env` and the
Lakekeeper connection string for no functional gain. The trigger to rename is the next time
the `postgres-data` volume is rebuilt from nothing (a fresh install or an engine wipe), when
a rename costs a find-and-replace and no migration.

**Other ADRs.** ADR-010's status line records the supersession of its budget clause in this
pull request. The orchestration clauses of ADR-008, ADR-014 and ADR-017, and ADR-018's
"Prefect is retained", are superseded at the cutover, when Airflow becomes the only
scheduler: until then Prefect still schedules the writes those ADRs describe, and their
status lines move with that pull request.

**Budget, declared** (`python3 scripts/check-budget.py`, 2026-11-25):

| | Services | CPU | GiB |
|---|---|---|---|
| Before, steady | 15 | 14.60 | 25.625 |
| Before, bootstrap peak | 19 | 16.10 | 27.125 |
| This pull request, steady | 19 | 17.70 | 31.688 |
| This pull request, bootstrap peak | 24 | **19.70** | **34.188** |
| After the archive, steady | 17 | 16.20 | 30.188 |

The additions are the plan's estimates: api-server 1.0 / 1.5 GiB, scheduler 1.5 / 3 GiB,
dag-processor 0.5 / 1 GiB, statsd exporter 0.1 / 64 MiB, `prefect-db` memory +0.5 GiB, and an
`airflow-migrate` one-shot of 0.5 / 1 GiB. Headroom at the peak is 0.30 CPU and 1.812 GiB.

**Budget, measured.** From the live stack over the shadow, 2026-11-25 02:13 to 04:27 UTC:
`docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}'` sampled every 30 s
(270 samples), mean and peak taken per container. The window includes the nightly
maintenance (03:00 to 03:32 UTC, still Prefect's), which is where the `spark-iceberg` peaks are.

| Service | Limit | Mean CPU | Peak CPU | Peak memory |
|---|---|---|---|---|
| `airflow-api-server` | 1.0 / 1.5 GiB | 0.00 | 0.03 | 261 MiB |
| `airflow-scheduler` (during probe runs) | 1.5 / 3 GiB | 0.02 | 0.94 | 429 MiB |
| `airflow-dag-processor` | 0.5 / 1 GiB | 0.03 | 0.52 | 281 MiB |
| `airflow-statsd` | 0.1 / 64 MiB | 0.00 | 0.03 | 21 MiB |
| `airflow-migrate` (one run, fresh database) | 0.5 / 1 GiB | not sampled | not sampled | not sampled |
| `prefect-db` | 1.0 / 1.5 GiB | 0.13 | 0.93 | 386 MiB |
| `spark-iceberg` (ingest and probe in one slot, and the maintenance) | 2.0 / 8 GiB | 1.62 | 2.12 | 4,719 MiB |

CPU is in cores. `airflow-migrate` ran to completion in under 30 s, between two samples. The
`dag-processor` peak sits at its limit for one sample at start-up; its mean is 0.03. Every
Airflow service peaks under a third of its memory limit, so the limits are generous on this
evidence; they stay as the plan set them until the cutover has run real ingests, and a
reduction then is an appended Outcome.

Peak connections per role on `prefect-db` over the same window, from
`SELECT usename, count(*) FROM pg_stat_activity GROUP BY 1` sampled every 3 min: `airflow` 7
against its cap of 100, `prefect` (Prefect and the Lakekeeper catalog) 21, of 200 total.

**Risks:** the probe and Prefect's ingest start in the same minute in a 2 CPU container, so
the shadow could slow the writer it watches; `scripts/airflow-shadow-check.sh` fails if any
Prefect run in the window did not complete. The skipped-three-times alert is blind for one
15-minute window after an exporter restart (the rule file says why).

**Revisit when:** astronomer-cosmos releases with Airflow 3.4 supported outside beta (then
move to the newest 3.4 patch); a measured peak above passes 75% of its limit, or the
`airflow` role passes 75 connections; the default bootstrap peak would pass 20 CPU or
36 GiB, which P3's Trino estimate does unless P3 trims first; a second person gets access to
the host, which is the trigger for a socket proxy and for an auth manager with real users.

---

## References

- Plan: [phase P2](../plans/2026-11-24-governed-self-serve-platform/002-airflow-replaces-prefect.md)
  of the [governed self-serve platform](../plans/2026-11-24-governed-self-serve-platform/README.md)
- Umbrella: [ADR-031](ADR-031-governed-self-serve-platform.md); superseded clause:
  [ADR-010](ADR-010-resource-budget.md); earlier rejection: [ADR-008](ADR-008-eliminate-prefect-orchestration.md)
- Runbook: [airflow-scheduling.md](../runbooks/airflow-scheduling.md)
- Code: `docker/airflow/`, `docker/lake-metrics/`, `docker/prometheus/rules/airflow-alerts.yml`,
  `scripts/airflow-shadow-check.sh`, `scripts/check-budget.py`

---

## Outcome (2026-11)

The cutover pull request (plan phase P2, pull request 2) switched `lake_ingest` from the
probe to the real command, unpaused `lake_maintenance` and stopped Prefect. The Decision
holds. What landed differently from the text above and from the plan:

**Prefect left the default profile at the cutover, not at the archive.** `prefect-server`
and `prefect-worker` moved behind a compose `legacy` profile, which a plain
`docker compose up -d` does not start, and their containers were removed: the rollback
creates them again from that profile, with Prefect's state still in `prefect-db`
([airflow-scheduling.md](../runbooks/airflow-scheduling.md#rollback-to-prefect)). So the
Prefect pair's 1.5 CPU / 1.5 GiB left the default sums at the cutover, not at the archive,
and the archive changes none of them. Declared limits after it, with cAdvisor (0.10 CPU /
128 MiB, merged after this ADR) included (`python3 scripts/check-budget.py`, 2026-11-25):

| | Services | CPU | GiB |
|---|---|---|---|
| Default, steady | 18 | 16.30 | 30.312 |
| Default, bootstrap peak (gated) | 23 | 18.30 | 32.812 |
| With `--profile legacy`, bootstrap peak (reported) | 25 | 19.80 | 34.312 |

Headroom at the gated peak is 1.70 CPU and 3.188 GiB. The rollback with Airflow still up
fits inside the ceiling, which is why it needs no budget change. With Prefect stopped,
`airflow-scheduler` is the only running container that holds the Docker socket.

**The gate order.** The plan runs `make gates` after the switch. The cutover ran before
P0's parity re-pin, because that gate needs a complete UTC day and measures the data, not
the scheduler: at the cutover `make gates` ran without `parity-ohlcv`, and both parity
gates run after the re-pin.

**Unpausing `lake_maintenance` ran a maintenance at once.** The shadow created it paused,
and `is_paused_upon_creation` is read only for a DAG the database has never seen, so the
cutover unpaused it by hand. With `catchup=False` the next run is the most recent past
cron slot, which is the "runs the latest slot once" above: checked offline on 3.3.2, a
`0 3 * * *` DAG unpaused at 05:07 UTC had its next run at 03:00 the same day. The cutover
used that run as its lock test, where the plan had a manual trigger.

**The rollback is documented, not drilled**, by the owner's decision, with a standing
policy beside it: two scheduled `lake_ingest` runs failing in a row, or 20 minutes without
a `raw.messages` commit outside a maintenance run, gets up to 30 minutes of diagnosis in
place and then the rollback.

**The shadow surfaced a failure no alert covered.** On 2026-11-25 a switch of the live
checkout to a commit without `docker/airflow/dags/` and back left the DAG folder's bind
mount stale: both Airflow containers saw it empty, the DAGs were marked stale, and no
`lake_ingest` run was created from 05:11 to 05:46 UTC while every container stayed healthy
and every Airflow series but the task counters looked normal. A recreate of the scheduler
and the dag-processor fixed it within a minute, with one run for the latest slot and no
replay. `AirflowLakeIngestNotRunning` now covers it
([airflow-scheduling.md §5](../runbooks/airflow-scheduling.md#5-lake_ingest-not-running-a-stale-dag-folder-or-a-forgotten-pause)),
and `airflow-scheduler` waits for `lake-ddl`, so the unpaused `lake_maintenance` cannot
run before the tables exist on a fresh clone.

**`scripts/airflow-shadow-check.sh` stayed** to the archive: the cutover's precondition
cites it, and after the cutover its third check fails by design.

The switch ran on 2026-11-25, before the pull request merged, with the stack on the branch
tip (`943df7a`); the alert grace period and the statsd ttl (`027980f`) were added and
deployed afterwards. The live run surfaced two findings, the stale DAG folder mount during
the shadow and the false `AirflowLakeIngestSkippedRepeatedly` firing after the first
maintenance run, and both were fixed before merge.

Measured at the switch, filled from the live run:

- Cutover 06:01:49Z to about 06:57Z; the stack's commit during the switch was `943df7a`.
- The first real `lake_ingest` run was `scheduled__2026-11-25T06:06:00+00:00`, 06:06:00 to
  06:09:30, `stage 1: committing 404815 offsets`; the commit age read 204 s just after it
  and `make health` printed `last commit 36s ago`.
- The unpause started `lake_maintenance` `scheduled__2026-11-25T03:00:00+00:00`, 06:10:21 to
  06:33:58 (23.6 minutes), `success`. Five `lake_ingest` runs (06:11 to 06:31) ended with
  the task `skipped`, none `failed`; 06:36 and 06:41 were `success`.
- After Prefect's containers were removed, `make health` ended `health: PASS` and `make
  lake-verify` ended `✓ lake-verify passed` (snapshot and `COUNT(*)` both 166156820 rows).
- `AirflowLakeIngestNotRunning` loaded after the Prometheus reload of step 4b. It was not
  among the alerts that fired over the cutover window; `LakeIngestLagHigh` fired during
  maintenance (existing behaviour: maintenance holds the lock longer than that alert's 15
  minutes) and `AirflowLakeIngestSkippedRepeatedly` fired falsely for about a minute.
- `make test`, `make parity-bars` (`parity: ok`, run at the current snapshot because its pin
  was stale), `make check-alerts` (8 SUCCESS) and `make check-docs` passed. `make
  parity-ohlcv` and a re-pinned `make parity-bars` are not yet run: they need the P0 re-pin,
  which needs a complete UTC day.
- A catch-up through the trigger ran after the cutover: `manual__2026-11-25T07:22:45`, task
  07:24:23 to 07:27:20, `success`, with `driver_memory` 2g and
  `max_offsets_per_partition` 1000000, `stage 1: committing 273039 offsets`.
- The rollback to Prefect was not exercised (owner's decision, 2026-11-25).
