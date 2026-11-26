# Audit: phase P2, Airflow 3 replaces the Prefect scheduler, as shipped and running (2026-11-26)

**Verdict:** The scheduling guarantees P2 bought hold on the live record. 289 scheduled
`lake_ingest` runs finished between the switch and this audit, and none overlapped another.
Nothing queued behind a slow run and no missed interval was replayed. Every skip happened under a
maintenance. Two restarts of the scheduler in the middle of a run each recovered with one
retry. The alert rules load and their promtool tests pass, and `AirflowTaskFailed` fired on a
real failure today. The Exit line's four criteria hold, with the qualifications the landed line
itself states. Two HIGH findings sit around the scheduler rather than in it:

- Nothing bounds the run logs. Every scheduled ingest leaves an uncompressed Spark event log,
  44 MiB at the median, in the Spark container's writable layer. That came to 3,948 MiB in 7.3
  hours, about 13 GiB a day, on the disk Redpanda and MinIO write to, and nothing deletes them.
- `make test` and `make gates` rebuild and retag `k2-airflow:3.3.2`, which is the tag the live
  services start from. The image the running containers were created from is gone from the engine.
  The tag now names a build from an unrecorded tree. So the next routine recreate starts the
  scheduler, which holds the Docker socket, on whatever tree last ran the tests.

Four MED findings follow. The three Airflow services each generated their own Fernet key, so
encrypted values are readable in one container only and are lost at a recreate. The critical
`LakeIngestFailed` now fires every night, because maintenance outlasts it. Runbook §2 prints the
wrong run's log. The Airflow image carries two CRITICAL advisories that no scanner sees.
Today's two failed scheduled runs in a row (05:36 and 05:41 UTC, an ingest defect that #238 fixed)
fired `AirflowTaskFailed`, and the alert reached nothing but the Prometheus UI.

| | |
|---|---|
| **Commit** | `bb6e77f` (origin/main, 2026-11-26). The lake scripts are bind-mounted from the main checkout, also at `bb6e77f`. The Airflow containers were created 2026-11-25 16:58 UTC and restarted 22:39 UTC; `k2-spark-iceberg` was created 23:51 UTC |
| **Scope** | Plan `docs/plans/2026-11-24-governed-self-serve-platform/002-airflow-replaces-prefect.md` (claims, Exit line, landed line), ADR-034 and its Outcomes, `docker/airflow/` (image, DAGs, `init-db.sh`, statsd mapping, tests), the Airflow services and `x-airflow-env` in `docker-compose.yml`, `docker/lake/lock.py` and the exit-code path to task states, `docker/prometheus/rules/airflow-alerts.yml`, the K2 Airflow dashboard, `docs/runbooks/airflow-scheduling.md`, `docs/operations/airflow-schedules.md`, `scripts/health.sh`, `scripts/chaos/lib.sh`, `legacy/prefect/`, and the live behaviour of `lake_ingest`, `lake_maintenance`, `lake_rebuild` and `dbt_marts` since the cutover |
| **Out of scope** | The dbt models and Trino fences (the P3 audit), the lake scripts' own logic beyond the lock and exit codes, P0's guardrails, and every finding already in the [P0](./2026-11-26-p0-prerequisites-guardrails.md) and [P3](./2026-11-26-p3-trino-dbt-marts.md) audits, which are cited, not repeated |
| **Lens** | Claims against the live stack, failure modes of the scheduler and its alerts, secrets and privilege, log and metadata retention, pinned versions and known advisories |
| **Method** | Live stack strictly read-only, 2026-11-26 06:55 to 07:20 UTC. Run history from `docker exec k2-airflow-scheduler airflow dags list-runs <dag> -o json` and read-only `SELECT`s on the `airflow` and `prefect` databases (`task_instance`, `task_instance_history`, `log`, `variable`, `flow_run`). Also task logs and `docker logs`, Prometheus queries (`ALERTS`, cAdvisor, statsd series, dashboard expressions), `docker inspect`, `docker diff`, `docker ps -s`, `docker system df -v`, `df` and `ls` inside containers, and unauthenticated `curl` GETs on `127.0.0.1:8090`. `scripts/health.sh` was run once from the main checkout; it is read-only. Secrets were compared in-process: the Fernet keys were hashed and compared and decryption tried, printing only booleans and exception names. No secret was printed or recorded. Advisories: OSV `querybatch` over the 150 distributions in the scheduler's Python and the 64 in the dbt venv, and GitHub's advisory database for `apache-airflow`. Gates run: `make check-alerts` (8 rule groups and 4 test files, all `SUCCESS`), `python3 scripts/check-budget.py` (PASS), and `python3 scripts/check-env.py --env-file .env` (variable names only). No DAG was paused, triggered or cleared, and no container was restarted |
| **Severities** | As the P0 and P3 audits use them. BLOCKER: data loss, corruption, or a fence that does not hold, reachable today. HIGH: a failure under a realistic event, a control that silently does nothing, or a foot-gun. MED, LOW |
| **Findings** | 0 BLOCKER · 2 HIGH · 4 MED · 6 LOW |

---

## Findings

| Severity | Location | Claim | Reality | Fix |
|----------|----------|-------|---------|-----|
| HIGH | `docker/spark/Dockerfile:28-31`; `docs/operations/README.md:771` | The Spark event logs of every lake job are kept "until `spark-iceberg` is removed". The Dockerfile keeps the base image's `eventLog` settings | Nothing else bounds them. `spark-defaults.conf` in `k2-spark-iceberg` sets `spark.eventLog.enabled true` and `spark.eventLog.dir /home/iceberg/spark-events`. It has no `eventLog.compress` and no `spark.history.fs.cleaner.*`. A HistoryServer runs in the container (`docker top`), but its cleaner is off by default. The directory is in the writable layer, not a mount: `docker inspect` lists only `/home/iceberg/lake` and two config files. Since the container was created at 23:51:25 UTC there are 136 files, 3,948 MiB in all (`ls -l` summed at 07:10 UTC). Of the 87 files over 10 MiB, 85 are one per ingest try, 31 to 53 MiB each with a median of 44 MiB; the other two are the 03:00 maintenance (216 MiB) and the `make gates` `lake-verify` at 01:53 (69 MiB). Each full hour outside the maintenance hour added 424 to 684 MiB, and the 7.3 hours average 541 MiB an hour: about 13 GiB a day. `df` in the container shows an overlay of 259G with 110G available. That is the disk `minio-data` (64 GB) and `redpanda-data` (35 GB) live on, and `k2_lake_disk_used_ratio` reads 0.579. The ratio fell from 0.546 at 23:06 to 0.523 at 00:06, when the 23:51 recreate removed the previous container and its logs. A recreate is the only thing that has ever cleared them: `git grep spark-events` finds documentation only. At this rate the logs alone fill the free space in about 8 days of uptime, and sooner beside the disk's own growth (the P3 audit's LOW on `lake-alerts.yml:477`). A full disk stops Redpanda, MinIO and Postgres writing, and then the captures cannot publish. The venues do not replay | In the Dockerfile's `spark-defaults.conf` step, append `spark.history.fs.cleaner.enabled true`, `spark.history.fs.cleaner.maxAge 2d` and `spark.eventLog.compress true`. The HistoryServer that already runs then deletes them. State the bound at `docs/operations/README.md:771` |
| HIGH | `Makefile:75-82`; `docker-compose.yml:559`, `:591`, `:634`, `:686` | `Makefile:75-76`: "`AIRFLOW_IMAGE=k2-airflow:<other tag>` tests a branch without retagging the image the running stack was started from" | The default retags it. `AIRFLOW_IMAGE ?= k2-airflow:3.3.2`, and `test-airflow` runs `docker build -t $(AIRFLOW_IMAGE)` from whatever tree it is run in. `make test` and `make gates` both run it. The tag has already moved off the running image, by this or by another build; nothing records which. The three Airflow containers (created 2026-11-25 16:58 UTC) run image `2cb77bd56eb6`, which `docker images -a` no longer lists. `k2-airflow:3.3.2` is now `cd678c5939dc`, built 2026-11-26 05:36:42 UTC, and its last layer (`COPY docker/airflow/init-db.sh`) differs. For the capture and statsd containers, the running image id and the tag's are the same. So the next recreate starts on whichever tree last built the tag, a branch worktree included. CLAUDE.md prescribes that recreate of `airflow-scheduler` and `airflow-dag-processor` after every checkout switch. The scheduler holds the Docker socket, and `airflow-migrate` runs `airflow db migrate` from the same tag. The image that runs today cannot be started again | `AIRFLOW_IMAGE ?= k2-airflow:test` in the Makefile, so that only `docker compose build` writes the live tag. Label the build with its commit (`--label org.opencontainers.image.revision=$(git rev-parse HEAD)`), as the capture image stamps `K2_GIT_SHA` |
| MED | `docker-compose.yml:90-114` (`x-airflow-env`); `.env.example:64-73`; `scripts/check-env.py:38-48` | `docker-compose.yml:85-89`: "The scheduler, the api-server and the dag-processor must agree on every line here". The JWT and API secrets are shared through `.env` | The Fernet key, which encrypts Variables and Connections in the metadata database, is on no line. No `AIRFLOW__CORE__FERNET_KEY` exists anywhere, so each container generated its own in `/opt/airflow/airflow.cfg`, in its writable layer (`docker diff` shows `A /opt/airflow/airflow.cfg` in all three). Compared in-process, the scheduler's key differs from the api-server's and from the dag-processor's. Both rows of `variable` (`cosmos_cache__dbt_marts__governance` and `__market_analytics`) are `is_encrypted = t`. They decrypt in the dag-processor only and raise `InvalidToken` in the scheduler and the api-server, which serves the UI and the tasks' execution API. A recreate writes a new key, so every encrypted value is unreadable after one. The `airflow` dump `scripts/backup-lake.sh` takes is ciphertext whose key is kept nowhere. Today there are 0 connections, so nothing is lost yet | Put one key in `.env` as `AIRFLOW_FERNET_KEY`, add it to `.env.example` and to `CREDENTIALS` in `check-env.py`, and set `AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY}` in `x-airflow-env`. Recreate the three services, then delete the two cosmos cache variables so the dag-processor rewrites them under the shared key |
| MED | `docker/prometheus/rules/lake-alerts.yml:38-41`; `docker/prometheus/rules/airflow-alerts.yml:44-45`; `docs/runbooks/airflow-scheduling.md:41-42` | "`LakeIngestFailed` is the critical backstop once Airflow owns the ingest, 35 minutes after the last commit". The skip rule leaves maintenance out because "a nightly page for it would be one nobody reads" (`airflow-alerts.yml:107-109`) | The critical rule has no such exclusion, and maintenance now outlasts it. `lake_maintenance` `scheduled__2026-11-26T03:00:00` held the lock from 03:00:00 to 03:44:30 UTC; the first one under Airflow, on 2026-11-25, took 23.6 minutes. `k2_lake_last_commit_ts_seconds{table="raw.messages"}` stayed at 03:04:34. The nine `lake_ingest` runs from 03:01 to 03:41 skipped, as designed. `ALERTS` shows `LakeIngestFailed` (critical) firing 03:40 to 03:46 and `LakeIngestLagHigh` 03:21 to 03:46. Every maintenance longer than about 35 minutes now raises the data-loss alert, which teaches the reader to ignore it | While `airflow:lake_lock_holder_in_flight` is 1, have `LakeIngestFailed` wait for `lake_maintenance`'s 7500 s `dagrun_timeout` instead of 1800 s. Do not mute it. Add a promtool case for a 45-minute maintenance |
| MED | `docs/runbooks/airflow-scheduling.md:106-109` | §2, for a failed run, step 2: "The task log: the script's own stdout and stderr, then its exit code", by `find ... \| sort \| tail -1 \| xargs tail -80` | The command prints the newest log, not the failed run's. Run at 07:11:13 UTC, it chose `run_id=scheduled__2026-11-26T07:11:00+00:00/task_id=ingest/attempt=1.log`, a run 13 s into its slot. Step 1 at the same time listed the failed runs as `scheduled__2026-11-26T05:41` and `05:36`. A `manual__` run sorts before every `scheduled__` one, so a failed catch-up is never chosen at all. This is the pattern of the P3 audit's MED on `lake-ingest-batch-audit.md:90-92`, here in the runbook that `AirflowTaskFailed` links | Read the log by the run id step 1 printed: `docker exec k2-airflow-scheduler sh -c "tail -n 80 '/opt/airflow/logs/dag_id=lake_ingest/run_id=<run_id>/task_id=ingest/'attempt=*.log"`, and paste a real output |
| MED | `docker/airflow/Dockerfile:14`, `:24-25`; `.github/dependabot.yml:28-34`; `SECURITY.md:65-68` | "Dependabot and a Trivy scan in CI surface known CVEs ... Trivy scans the repository's lockfiles and fails CI on a CRITICAL with a fix available" | The scheduler's Python environment has no lockfile in the repository, because its pins are the remote `constraints-3.3.2` file. Dependabot ignores `apache/airflow`, and no image is scanned (the P0 audit's MED on `ci.yml:262-278`), so nothing looks at it. OSV (`POST api.osv.dev/v1/querybatch` over the 150 installed distributions, 2026-11-26) lists advisories on 11 packages. Two are CRITICAL, from the same compromised CA root: `certifi` 2026.7.22 (GHSA-6q48-6c7w-9gw2) and the CA store of the `uv` 0.12.10 package (GHSA-hq85-5cpj-mr62). Sixteen are HIGH: `virtualenv` 21.7.9 (10), `urllib3` 2.7.0 (2), `dnspython` 2.8.0 (2), `msgpack` 1.2.2 and `filelock` 3.32.6. Airflow 3.3.2 itself has none: `gh api /advisories -f ecosystem=pip -f affects=apache-airflow@3.3.2` is empty, and the newest Airflow advisories (2026-08-12) are fixed in 3.3.1. 3.3.2 is the newest 3.3 patch. The dbt venv (64 packages, `dbt/uv.lock`, which Trivy reads) has none. The exposure found is low: tasks talk plain HTTP inside `k2-net`, telemetry is off, and no runtime path makes an outbound TLS connection. The 3.4.0 constraints pin fixed versions of all but `dnspython` GHSA-vvf4-9x63-8xvv. ADR-034's trigger for 3.4, cosmos support outside beta, has not fired: cosmos 1.16.0 is still the newest | After the constrained install, `pip install --no-cache-dir certifi==2026.10.23 urllib3==2.8.0 msgpack==1.2.4`. Add a Trivy `image` scan of the built `k2-airflow:3.3.2` to the CI docker job's airflow leg, with `exit-code: 1` on a fixable CRITICAL |
| LOW | `docs/runbooks/airflow-scheduling.md:44-48` | A try orphaned by a scheduler that died is marked "failed after `task_instance_heartbeat_timeout` (300 s)" and retried once | The scheduler went down mid-run twice. Both times the new one reset the try on its first adoption pass and started try 2 at once, without the 60 s retry delay. The scheduler log at 16:58:53 reads `Reset the following 1 orphaned TaskInstances`, 46 s after the container was created. In `task_instance_history`, try 1 of `scheduled__2026-11-25T22:31` ends `failed` at 22:39:31, 6 s after the scheduler came back from the VM loss, and try 2 starts the same second. The reset spends the run's only retry | Say that the restarted scheduler resets the try at start, and that this uses the retry |
| LOW | `docs/adr/ADR-034-airflow-3-replaces-prefect.md:167-168`; `scripts/health.sh:146` | "a stuck lock and a lost scheduler page". `health.sh`: the case `AirflowLakeIngestSkippedRepeatedly` "pages on" | Nothing pages. `curl 127.0.0.1:9090/api/v1/alertmanagers` returns `"activeAlertmanagers":[]`, and Grafana provisions no alerting. Today `lake_ingest` failed two scheduled runs in a row, `scheduled__2026-11-26T05:36` and `05:41`, four tries in all (`AnalysisException: [DATATYPE_MISMATCH...]`, fixed by #238). `AirflowTaskFailed` fired from 05:42 to 06:01 UTC and reached the Prometheus UI only. The limit itself is recorded in ADR-028 §10 and `docs/architecture/11-observability.md:116`, and the P3 audit's MED counted it. This row is only that ADR-034 says otherwise | Append an ADR-034 Outcome line: alerts are shown, not paged (ADR-028 §10). `health.sh:146`: "fires on" |
| LOW | `docker/airflow/init-db.sh:26` | n/a | Nothing cleans the metadata database: `airflow db migrate` is the only `airflow db` command in the tree, and `git grep 'db clean'` finds nothing. After 29 hours it is 13 MB (`pg_database_size`), with 353 DAG runs and 2,129 audit-log rows, so it grows by about 10 MB a day. That is harmless for months, but no decision or trigger is recorded | Run `airflow db clean --clean-before-timestamp <90 days ago> --yes` monthly from a runbook step, or record a size trigger in an ADR-034 Outcome |
| LOW | `legacy/prefect/README.md:65`, `:79` | ":65 Prefect's last scheduled run ... not recorded". `:79` gives the steps to bring it back | The database the archive kept records it: `SELECT max(start_time) FROM flow_run` in `prefect` returns 2026-11-25 06:01:05 UTC. The same database, 606 MB, still holds three `SCHEDULED` runs of `lake-maintenance-daily`, for 03:00 on 2026-11-26, 27 and 28. A restored worker would start the past-due ones as late runs, which is the replay class of ADR-034's first incident, and the steps do not say to clear them | Record the last run. Add "delete the `SCHEDULED` flow runs first" to the steps, or drop the `prefect` database after a dump to the backup directory |
| LOW | `docs/plans/2026-11-24-governed-self-serve-platform/002-airflow-replaces-prefect.md:79`, `:87` | Verification: `bash scripts/chaos/lake-ingest-kill.sh # still recovers under Airflow`. The landed line lists where P2 differs from its text | The chaos step was not run under Airflow, and the landed line does not say so. `scripts/chaos/results/` holds `2026-08-26.tsv` and `2026-08-27.tsv` only, and the last `lake-ingest-kill.sh` row is `2026-08-27T03:06:17Z`, under Prefect. Pull requests #189, #195 and #235 do not mention it. The live record stands in for it: 14 first tries failed and were retried under Airflow, 8 of them heap failures and 2 of them killed by a restart, and all but 4 runs then ended `success` | Run it once, commit its row and cite it, or name the omission beside the landed line |
| LOW | `docs/operations/README.md:769`; `docker/lake/spark_conf.py:138` | Task logs are kept "until the scheduler container is removed" | That is their only bound. `du -sm /opt/airflow/logs/*` reads 77 MiB for `lake_ingest`, 9 for `dbt_marts` and 5 for `lake_maintenance`: 91 MiB in 14.2 hours, about 150 MiB a day. 289 of the 770 lines in one ingest attempt's log are Spark console progress bars (`[Stage N: ...]`). The P3 audit's MED, that no volume holds these logs, is the other half: a volume would keep this growth for good | `spark.ui.showConsoleProgress false` in `lake_session`. With the P3 volume, a daily `find /opt/airflow/logs -name '*.log' -mtime +14 -delete` |

Every row carries `file:line` or a command with its output. A row that could not be
demonstrated was dropped.

---

## The Exit line, criterion by criterion

| Criterion | Proven on the live stack | Proven only by tests or by hand, or not at all |
|---|---|---|
| `make gates` passes with Airflow as the only scheduler | Consistent. The `log` table holds a `lake_ingest` and `lake_maintenance` pause at 01:41:44 and an unpause at 01:53:54 UTC, which is `lake-verify` inside the 01:35 to 01:55 `make gates` run that the landed line cites | Not re-run here: it needs the live stack and runs `test-airflow`, which retags the live image (second HIGH) |
| 24 consecutive scheduled `lake_ingest` runs succeeded with no unexpected skip | Yes. The 24 scheduled runs from `scheduled__2026-11-25T06:06` to `08:06` are all `success`: 19 with the task `success` and 5 (06:11 to 06:31) `skipped` while the first maintenance held the lock, 06:10:21 to 06:33:58. The 06:46 slot has no run, because `lake_ingest` was paused 06:45:14 to 06:55:32 for the cutover (`log` table) | |
| The Prefect grep prints only the kept files | Yes, with the documented widening. The grep prints 27 files, and a scan of each found no mention outside the kept tokens (`prefect-db`, `PREFECT_DB_*`, `legacy/prefect`, the two ADR file names). `tests/test_no_scheduler_name.py` enforces it | |
| ADR-034 carries measured CPU and memory | Yes (`ADR-034:207-230`, the shadow). Against its 75% trigger, the peak working set over the window Prometheus holds is: scheduler 866 MiB of 3 GiB, dag-processor 511 MiB of 1 GiB, api-server 292 MiB of 1.5 GiB, `prefect-db` 393 MiB of 1.5 GiB, statsd exporter 45.6 MiB of 64 MiB (71%, RSS 22.6 MiB). The `airflow` role holds 6 connections of its 100, and `max_connections` is 200. No trigger has fired | |

## Live behaviour since the cutover

| Question | Answer | Evidence |
|---|---|---|
| Run history, durations against the slot | 289 scheduled `lake_ingest` runs finished in 298 slots, `scheduled__2026-11-25T06:06` to `2026-11-26T06:51`. Median 198 s, p90 253 s, max 755 s (one run whose two tries spanned the VM loss), and 16 over the 300 s slot. On 2026-11-26 from 00:00 there were 82 runs, median 156 s, p90 207 s. The duration trigger is the P3 audit's HIGH, not repeated | `airflow dags list-runs lake_ingest -o json`, end minus start per run |
| Failures and retries | 4 runs ended `failed`: 17:41 and 18:36 on 2026-11-25 (Java heap, before #205), and 05:36 and 05:41 on 2026-11-26 (the defect #238 fixed). 14 first tries failed and were retried after 2026-11-25 16:56: 8 heap, 3 the #238 defect, 2 scheduler restarts and 1 `SocketException: Connection reset`. None is the Lakekeeper commit race that `lake.py:167-168` gives as the reason for the retry | `task_instance_history`; attempt logs |
| Slots with no run, lateness | 9 slots had no run: 5 while a retried run outlasted its slot, 1 while a manual catch-up held the slot, 1 during the VM loss, and 2 while the DAG was paused (the cutover, and `make gates`). Nothing replayed. Lateness (start minus slot) has a median of 0.6 s, p90 87 s and max 314 s. The dashboard's lateness panel tracks it: `airflow_dagrun_schedule_delay` p99 read 116.7 s for the 05:51 run, which started 116.7 s late | the run list; the `log` table; Prometheus |
| Overlapping runs, skips | No two `lake_ingest` runs overlapped. There were 14 skipped tasks, all inside a maintenance: 06:11 to 06:31 on 2026-11-25 and 03:01 to 03:41 on 2026-11-26. `AirflowLakeIngestSkippedRepeatedly` did not fire on 2026-11-26 | the run list; `ALERTS` over 7 h |
| Zombie tasks | None was failed by the heartbeat timeout. Two tries were reset as orphans when the scheduler started (LOW above) | `docker logs k2-airflow-scheduler` |
| Scheduler restart mid-run | The try is reset on the new scheduler's first pass and retried at once. At the 16:58 recreate, try 2 took the lock and ended `success`, so the first try's driver was gone by 16:58:53. That fits `on_kill` running within the 45 s `stop_grace_period`, but is not proven: that try's log went with the old container (the P3 audit's MED). At the VM loss (22:33) the driver died with the VM, and try 2 succeeded at 22:43:35 | `task_instance_history`; the scheduler log |
| Scheduler and dag-processor health | `airflow jobs check` prints `Found one alive job.` for both, `list-import-errors` prints `No data found`, and the dag-processor logged 0 error lines in 8 h. The heartbeat rate is 0.2 a second | the commands named |
| Metadata database growth | LOW above. Nothing cleans it | `pg_database_size`, `pg_stat_user_tables` |
| Log retention | First HIGH (Spark event logs) and the last LOW (task logs) | `ls`, `du`, `df` |
| Does a failed run alert, and is the alert delivered | It alerts: `AirflowTaskFailed` fired 05:42 to 06:01 UTC for today's two failed runs, within a minute of the final try. It is delivered nowhere (LOW above, ADR-028 §10) | `ALERTS`; `/api/v1/alertmanagers` |
| `dbt_marts` | 16 runs since 2026-11-25 16:59, all `success`. Its 26 skipped tasks are the maintenance branch outside 04:10. It never delayed the ingest at minute 11: from a `lake_ingest` run's start to its first try's start averaged 0.0 s at minute 11 and at every other minute (max 0.1 s) | `task_instance` joined to `dag_run` |

## Security posture

| Item | Recorded at | Live check | Verdict |
|---|---|---|---|
| Docker socket in `airflow-scheduler` | ADR-034:88-95; the P0 audit's posture table | Only the scheduler mounts it: `/var/run/docker.sock` is absent in the api-server, and the dag-processor mounts `dags` and `dbt` only. It runs as `uid=50000 gid=0`, and the socket is `root:root 660`. Since P3 the container also runs cosmos and dbt-core: the dbt venv's 64 Python distributions beside Airflow's 150 | Accept, as ADR-034 does. Its revisit trigger (a second person on the host) stands |
| Airflow API and UI | ADR-034:92-95 | `ss -ltn` shows `127.0.0.1:8090` only. Unauthenticated GETs to `/api/v2/dags`, `/api/v2/variables`, `/api/v2/config` and `/execution/variables/x` return 401 | Accept |
| Fernet key | not recorded | three different per-container keys | MED above |
| JWT and API secret keys | `x-airflow-env`, `check-env.py:46-47` | `check-env.py --env-file .env` reports nothing for the three Airflow secrets | Accept |
| The Postgres superuser that hosts the `airflow` database | the P0 audit's HIGH on `scripts/check-env.py:1-3`, open | `check-env.py --env-file .env` prints `FAIL ... PREFECT_DB_PASSWORD equals PREFECT_DB_USER` (and the MinIO pair): the live password is the one the fixed check refuses | The P0 remainder; not repeated |
| UI admin password in the api-server log | the P0 audit's LOW | unchanged | Accept |
| Versions | ADR-034:115-131 | Airflow 3.3.2 is the newest 3.3 patch and has no published advisory. cosmos 1.16.0 is the newest. The transitive advisories are the MED above | MED above |

## Not examined, and why

- `make gates`, `make test` and `make test-airflow`: heavy on a memory-short host, and
  `test-airflow` would retag the live image (second HIGH).
- A timeout or a SIGTERM through `on_kill` on the live stack. No task timed out, and no retained
  log holds a SIGTERM, so the kill path is shown only by `docker/airflow/tests/test_lake_dags.py`
  and runbook §3's drill.
- What cosmos does when its cache variable cannot be decrypted: read in `cosmos/dbt/graph.py`,
  not exercised.
- The Airflow image's operating-system packages (Debian, `docker-ce-cli` 29.8.1): no image
  scanner was run.
- Task logs from before 2026-11-25 16:58 UTC, which went with the previous containers. Run
  states from before then were read from the database.

Observed outside this scope, for the owner: `scripts/health.sh` step (5) printed
`backup: SKIP (not set up on this host: make backup-install schedules it ...)`. The daily copy
that #231 added, which also dumps the `airflow` database, is not installed on the live host.
The P0 audit records #231 as the partial resolution of its BLOCKER.

---

## Fix now, and deferred

Owner's standing decision: at a phase boundary only critical and high findings are fixed at the
time. Everything else goes to the hardening sprint after the infrastructure-as-code phase.

**Fix now (HIGH):**

- HIGH `docker/spark/Dockerfile:28-31`: bound the Spark event logs (HistoryServer cleaner, 2
  days, compressed).
- HIGH `Makefile:75-82`: `make test` and `make gates` must stop writing `k2-airflow:3.3.2`.
  Label the build with its commit.

**Deferred to the hardening sprint:**

- MED `docker-compose.yml:90-114`: one shared Fernet key.
- MED `docker/prometheus/rules/lake-alerts.yml:38-41`: `LakeIngestFailed` during maintenance.
- MED `docs/runbooks/airflow-scheduling.md:106-109`: §2 reads the log by run id.
- MED `docker/airflow/Dockerfile:24-25`: override `certifi`, `urllib3` and `msgpack`; scan the
  image.
- LOW `docs/runbooks/airflow-scheduling.md:44-48`: orphan reset at start.
- LOW `docs/adr/ADR-034-airflow-3-replaces-prefect.md:167-168`: "page".
- LOW `docker/airflow/init-db.sh:26`: metadata cleanup or a recorded trigger.
- LOW `legacy/prefect/README.md:65`, `:79`: the last run, and the queued runs.
- LOW plan 002 `:79`, `:87`: the chaos step under Airflow.
- LOW `docs/operations/README.md:769`: task log growth and progress bars.

---

## Resolutions

Appended after publication; the table above is never edited.
