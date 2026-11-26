# Prefect scheduler for the lake (archived)

This directory holds the Prefect 3 worker image and the two flows that scheduled the v3 lake until the Airflow cutover on 2026-11-25. Nothing here is built, started or wired to an alert, and nothing in `make test` or CI touches it. The files are kept for reference and moved exactly as they were: the paths they mention (`/opt/prefect/lake-flows`, `docker/lake/`) are the ones they had in the live tree and were not rewritten. See [`../README.md`](../README.md) for the archive rules.

```text
legacy/prefect/
  flows/deploy_lake.py   registers the two deployments (was docker/lake/flows/)
  flows/lake_flows.py    the two flows (was docker/lake/flows/)
  image/Dockerfile       the worker image (was docker/prefect/)
  image/memo_store.toml  Prefect's own block-registration memo, written into the bind-mounted /root/.prefect
  image/storage/.gitkeep keeps the bind-mount directory in git
```

## What it was

Two compose services, `prefect-server` and `prefect-worker`, on the `prefecthq/prefect:3-python3.12` image (pinned as `3.9.5-python3.12`, the same digest, by #228 `410cc13` just before the archive), with run state in the `prefect` database of `prefect-db`. At start the worker created a process work pool `lake`, ran `deploy_lake.py` to upsert two deployments, then polled the pool. Each deployment ran a flow that did one thing, `docker exec k2-spark-iceberg python3 /home/iceberg/lake/<script>` over the mounted Docker socket (`flows/lake_flows.py`):

| Deployment | Cron (UTC) | Script | Retry | Timeout |
|---|---|---|---|---|
| `lake-ingest-5min` | `1-59/5 * * * *` | `ingest.py` | one, after 60 s | 3600 s |
| `lake-maintenance-daily` | `0 3 * * *` | `maintenance.py` | none | 7200 s |

Both set `concurrency_limit=1`. The flows added no lake logic: position and exactly-once state live in the Iceberg snapshot summary ([ADR-022](../../docs/adr/ADR-022-exactly-once-via-snapshot-offsets.md)) and mutual exclusion is the `flock` in `docker/lake/lock.py`, which is why the orchestrator was replaceable ([ADR-034](../../docs/adr/ADR-034-airflow-3-replaces-prefect.md) Context).

- **Inputs:** the cron schedule and the two scripts' own state. **Outputs:** the scripts' commits to the lake, and a Prefect flow-run record.
- **When it ran:** Prefect entered the repo in `13ad92d` (2026-02-18, the v2 cold-tier phase), kept after [ADR-008](../../docs/adr/ADR-008-eliminate-prefect-orchestration.md) tried to remove it (its Outcome explains why: the offload and maintenance jobs had state, fan-out and a need for run history), and was re-pointed at these two lake deployments in `e2a2651` (2026-08-27, Phase D). It stopped writing at the Airflow cutover, 2026-11-25.

## Why it was archived

[ADR-034](../../docs/adr/ADR-034-airflow-3-replaces-prefect.md) records the decision, and its Context records two incidents on this stack on 2026-11-24 that set requirements an orchestrator must meet:

- **A killed worker left a run marked running**, and that run held the deployment's only concurrency slot for hours. When the slot freed, months-old queued runs replayed back to back. The ingest reads from its committed offsets, so a late run reads what a punctual one would have; replaying intervals only stacks drivers.
- **The stall let the bus evict unread records.** `docker/redpanda/init.sh` (lines 127 to 135) records it: the orchestrator fault stalled the ingest for 7 h and one `raw.kraken` partition lost 6,351,400 records; while the queued maintenance runs held the writer lock for another hour, a further 1,386,717 were evicted. Both are `offset_gap` rows in `lake.audit.checks`. The retention cap was raised the next day because of it (same file).

A third cause is mechanical. Prefect's `subprocess.run(..., timeout=...)` kills the `docker exec` client; the driver inside the Spark container keeps running and keeps the lock, so a run that timed out still blocks every run after it (ADR-034 Context).

Policy and cost, from the same ADR:

- [ADR-031](../../docs/adr/ADR-031-governed-self-serve-platform.md) had already chosen Airflow 3 for the platform layer. Keeping Prefect and fixing the slot lease and the backlog in it, then replacing it, was "the work twice" (ADR-034, Alternatives Considered).
- ADR-008 had rejected Airflow as heavier than Prefect under 16 CPU. ADR-034 moved that budget to 20 CPU / 36 GiB (since ADR-045, 25 CPU / 48 GiB); the Prefect pair's declared limits were 1.5 CPU / 1.5 GiB.
- Privilege and exposure: `prefect-worker` held `/var/run/docker.sock` as root, and Prefect's UI was unauthenticated and, until #218 `adbe2d7` put every published port on 127.0.0.1, published on every interface. Airflow's UI is behind a generated admin password; `airflow-scheduler` holds the socket (cAdvisor mounts it read-only, which does not narrow a socket).

Measured figures for what replaced it (peak memory per Airflow service, the 2 s JVM exit after a driver TERM, connections per role) are in ADR-034's Consequences. The cause of the 2026-11-24 fault inside Prefect itself, a bug or a configuration, is not recorded: `init.sh` calls it an orchestrator fault and ADR-034 a killed worker; neither records a cause inside Prefect.

## What replaced each piece

| Prefect | Airflow |
|---|---|
| `flows/lake_flows.py` (`lake_ingest`, `lake_maintenance`) | `docker/airflow/dags/lake.py`: `lake_ingest`, `lake_maintenance`, `lake_rebuild` |
| `flows/deploy_lake.py`, upserting deployments at worker start | none: the dag-processor parses `docker/airflow/dags/` |
| `prefect-worker` (process worker, `docker exec` dispatch) | `airflow-scheduler` (LocalExecutor, four slots) |
| `prefect-server` UI on `K2_PREFECT_PORT` (4200), unauthenticated | `airflow-api-server` on `127.0.0.1:${K2_AIRFLOW_PORT}` |
| `concurrency_limit=1` | `max_active_runs=1`, `catchup=False`, `dagrun_timeout`; the `flock` still covers every path |
| `subprocess.run(timeout=...)` | `execution_timeout` plus an `on_kill` that stops the recorded driver PID |
| a failed flow run, seen in the UI | the four `Airflow*` alerts in `docker/prometheus/rules/airflow-alerts.yml`, the K2 Airflow dashboard |
| `image/Dockerfile` (also carried `prometheus-client` for the exporter) | `docker/airflow/Dockerfile` and `docker/lake-metrics/Dockerfile` |
| `docs/operations/prefect-schedules.md` (deleted at the archive) | [`airflow-schedules.md`](../../docs/operations/airflow-schedules.md), [`airflow-scheduling.md`](../../docs/runbooks/airflow-scheduling.md) |
| the `prefect` database on `prefect-db` | unchanged: the service, the `prefect` role and the `PREFECT_DB_*` variables keep their names, now also holding the `airflow` and `lakekeeper` databases (ADR-034, "What keeps the name") |

## State at archive

- **Cutover:** 2026-11-25 (ADR-034 Outcome): `lake_ingest` switched from the probe to the real command, `lake_maintenance` unpaused, Prefect stopped and its containers removed. Merged as #195 `c572727`; the shadow before it as #189 `564a282`.
- **Archive:** 2026-11-26, the pull request that added this README; `git log --diff-filter=A --oneline -- legacy/prefect/README.md` names its commit. On its branch the move is one commit of 5 pure renames and no edits (`git show --stat -M100%` on it prints `0 insertions(+), 0 deletions(-)`), and the compose services, the CI build leg, the Dependabot entry and the shadow check were deleted in the commit after it.
- **Before archive:** the plan required 24 consecutive green scheduled `lake_ingest` runs ([plan 002](../../docs/plans/2026-11-24-governed-self-serve-platform/002-airflow-replaces-prefect.md)). The first 24 after the switch, `scheduled__2026-11-25T06:06:00+00:00` to `scheduled__2026-11-25T08:06:00+00:00`, were all `success`; five of them (06:11 to 06:31) were a skipped task while the first `lake_maintenance` held the writer lock, which is the lock working (`airflow dags list-runs lake_ingest -o json`, read 2026-11-26; ADR-034 Outcome). The first nightly `lake_maintenance` that Airflow started on time, `scheduled__2026-11-26T03:00:00+00:00`, ran 03:00:00 to 03:44:30 UTC, `success`.
- **Prefect's last scheduled run, its image digest and its resolved patch version:** not recorded. The tag `3-python3.12` moves.
- **Measured Prefect CPU and memory:** not recorded; ADR-034 quotes only the declared limits.

## Running or verifying it today

Nothing is wired to start it, and no command here runs it. The compose services were deleted, so `docker compose up` cannot build or start the image, `deploy_lake.py` needs a Prefect server to register against, and `lake_flows.py` imports `prefect`, which no maintained environment installs. Not run: this was not built or executed when it was archived, so it is not shown to still work.

What can be checked from the files: the move changed nothing (`git log --follow --oneline -- legacy/prefect/flows/lake_flows.py`, then `git show --stat -M100%` on the move commit), and the last compose definitions and the rollback procedure are in git history at `410cc13`, the main commit this archive was cut from:

```bash
git show 410cc13:docker-compose.yml | sed -n '/^  prefect-server:/,/^  # Airflow 3/p'
git show 410cc13:docs/runbooks/airflow-scheduling.md | sed -n '/^## Rollback to Prefect/,/^## 1\./p'
```

Bringing it back is not a supported path. It would be: move `image/` back to `docker/prefect/` and `flows/` to `docker/lake/flows/`, restore the two services from the first command, pause the Airflow lake DAGs, then follow the procedure the second command prints. That procedure was documented at the cutover and never drilled ([ADR-034](../../docs/adr/ADR-034-airflow-3-replaces-prefect.md) Outcomes, 2026-11 and 2026-11-26).

## Known residue

- `prefect-db` (container `k2-prefect-db`), its `prefect` role and database, and the `PREFECT_DB_*` variables keep the name. The trigger to rename is the next time the `postgres-data` volume is rebuilt from nothing (ADR-034 Consequences). On an existing volume the `prefect` database still holds Prefect's run state; it was not dropped.
- Immutable records cite the old paths and names: ADR-008, ADR-014, ADR-017, ADR-018, ADR-022, ADR-034, `docs/audits/`, `docs/research/`, `docs/plans/` and `docs/MIGRATION-JOURNEY.md`. ADR-034's 2026-11 Outcome links a runbook anchor, `airflow-scheduling.md#rollback-to-prefect`, that no longer exists: the section went with the services, and ADR-034's 2026-11-26 Outcome says where to find it.
- `legacy/v2-offload/runbooks/` still link `prefect-schedules.md`, which is deleted (`airflow-schedules.md` replaces it); they are archived unmodified.
- Dependabot has no entry for `legacy/prefect/image`, CI's Trivy scan skips `legacy/` (`skip-dirs: legacy`), and `scripts/check-budget.py` does not read this Dockerfile. Its `FROM prefecthq/prefect:3.9.5-python3.12` and `prometheus-client==0.21.1` are therefore not tracked for advisories.
- `scripts/check-docs.sh` link and word gates do not scan this README (they cover `legacy/v2-kotlin` only); its links were checked by hand.
- `tests/test_no_scheduler_name.py` fails if the scheduler's name returns to the live tree outside the kept names.
