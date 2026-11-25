# Runbook: The dbt marts are not being rebuilt

The domain marts on Trino are rebuilt every hour at minute 10 by the Airflow DAG `dbt_marts`
(`docker/airflow/dags/dbt_marts.py`, [ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md),
[ADR-044](../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)): one task group per
domain with marts, every task run as that domain's writer `dbt_<domain>`, the `governance`
group after all the others however they ended, and once a day, in the 04:10 run, the domain's
snapshot maintenance. Every dbt invocation that runs a test records its results in its domain's
`dq.dbt_test_results` ([ADR-046](../adr/ADR-046-governance-domain-and-quality-results.md)), a
table each domain's first task, `<domain>_test_results_table`, creates if it is absent; a task
Airflow never starts records nothing. This covers a run that fails, a DAG that is
not running, and a failed maintenance. It does not cover the lake behind the marts: a stale
source is [lake-ingest-lag.md](./lake-ingest-lag.md), Airflow itself is
[airflow-scheduling.md](./airflow-scheduling.md).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

> Every command below was run on 2026-11-25 before the DAG was deployed: the Airflow ones in a
> throwaway container of the new image (`k2-airflow:dbt-test`, the dags and `dbt/` of this
> branch mounted read-only, a SQLite metadata database) on the compose network against the
> live Trino, the rest on the live stack. The container's name there replaced
> `k2-airflow-scheduler`; the image, the CLI and the paths are the scheduler's.

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | No run has succeeded for 3 hours | the next run after the cause is fixed | induced: Trino unreachable from Airflow, the run failed in 10 s; 37 s from Trino reachable again to a successful manual run (2026-11-25, throwaway container). The alert's timing is proven by its promtool tests, not observed |
| 2 | The daily snapshot maintenance failed or did not run | the next day's 04:10 run, or one run-operation | not induced; the run-operation by hand took under 1 s for two tables (2026-11-25). The alert's timing is proven by its promtool tests, not observed |

---

## 1. No `dbt_marts` run has succeeded for 3 hours

**Symptom.** The table marts stop moving: `market_analytics.candles.ohlcv_1m` and the scorecard
keep their last build. The delayed view reads the lake when queried and keeps up, but its tests
stop running. Nothing else in the stack is affected: a mart is read by Trino's clients only,
and the lake does not depend on it.

**Detection.** `DbtMartsNotSucceeding` (`docker/prometheus/rules/airflow-alerts.yml`), warning:
no run of `dbt_marts` has ended in success for 3 hours, held 10 minutes. Its input is the count
of successful runs, which is absent until the first success:

```bash
curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=sum(airflow_dagrun_duration_success_count{dag_id="dbt_marts"})'
#   {"status":"success","data":{"resultType":"vector","result":[]}}
#   (before the first success; afterwards one sample, the count since the exporter started)
```

A single failed task does not alert: `AirflowTaskFailed` leaves `dbt_marts` out, because the
next hour rebuilds every mart whole.

**Expected behaviour.** Nothing recovers a cause by itself, but nothing needs undoing either:
`max_active_runs=1`, no catch-up, and each model is a `CREATE OR REPLACE` from the lake, so the
first run after the cause is gone puts every mart back to current. A failed node stops its
descendants (`upstream_failed`) and nothing else. Those descendants run no dbt, so their tests
leave no row in `dq.dbt_test_results` or `governance.quality_results.dq_results` for that run:
not a skip, nothing. A test with no row for two hours is how to spot it
([dbt/README.md](../../dbt/README.md#test-results-every-domains-then-one-view) has the query).

**Recovery.** Which runs failed, then which task:

```bash
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list-runs dbt_marts -o plain 2>/dev/null | head -3
#   dag_id     run_id                                    state    run_after                         ...
#   dbt_marts  recovered_after_trino_back                success  2026-11-25T15:47:13.667583+00:00  ...
#   dbt_marts  induced_trino_unreachable                 failed   2026-11-25T15:46:38.201278+00:00  ...

docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler \
  airflow tasks states-for-dag-run dbt_marts induced_trino_unreachable 2>/dev/null | grep -oE 'market_analytics[^ ]* +\| [a-z_]+'
#   market_analytics.ohlcv_1m_model_build                | upstream_failed
#   market_analytics.ohlcv_delayed_20m_model_build       | upstream_failed
#   market_analytics.stg_audit__checks_model_build       | failed
#   market_analytics.stg_gold__trades_model_build        | upstream_failed
#   market_analytics.venue_quality_scorecard_model_build | upstream_failed
#   market_analytics.gold_trades.source                  | failed
#   market_analytics.gold_trades.test                    | upstream_failed
#   market_analytics_expire_mart_snapshots               | upstream_failed
#   market_analytics_maintenance_hour                    | upstream_failed
```

That listing predates `market_analytics_test_results_table`, which now runs before every task
of the domain: with Trino unreachable it is that task that fails, and every task of the group is
`upstream_failed` (by the DAG's structure, `docker/airflow/tests/test_dbt_marts_dag.py`; not
induced).

Then what dbt said, from the failed task's log in the scheduler (LocalExecutor runs every task
there; the UI shows the same log):

```bash
docker exec k2-airflow-scheduler sh -c 'grep -ho "\"event\":\"[^\"]*" \
  "/opt/airflow/logs/dag_id=dbt_marts/run_id=induced_trino_unreachable/task_id=market_analytics.gold_trades.source/attempt=1.log" \
  | grep -A2 "Encountered an error"'
#   "event":"\u001b[0m15:46:43  [\u001b[31mERROR\u001b[0m]: Encountered an error:
#   "event":"Database Error
#   "event":"  failed to execute: HTTPConnectionPool(host='trino', port=8080): Max retries exceeded with url: /v1/statement (Caused by NameResolutionError(\
```

What the error says, and where to go:

| dbt says | Cause | Do |
|---|---|---|
| `HTTPConnectionPool(host='trino', port=8080)` | Trino down or unreachable | `docker compose ps trino`; Trino's health check; then rerun |
| `Access Denied: Cannot create ...` | a task ran as the wrong user: the fence works | check `K2_TRINO_USER` in the task's env against `dbt/selectors.yml`; do not loosen `docker/trino/etc/rules.json` |
| the failed task is `<domain>.gold_trades.source`, the source freshness | the lake ingest is behind the contract's 30 minutes (`gold.trades.ingest_ts`) | [lake-ingest-lag.md](./lake-ingest-lag.md); the marts follow once it commits |
| `Failure in test <name>` | a mart's grain, envelope or a source test failed on real data | a finding, and already a row: `SELECT * FROM <domain>.dq.dbt_test_results WHERE status <> 'pass' ORDER BY run_started_at DESC`, and in `governance.quality_results.dq_results`. Read the test in `dbt/models/**/_*__models.yml`, rerun it by hand (below) |
| every test `PASS`, then `Exited because of keyboard interrupt` and `Encountered an error: Database Error` | the `on-run-end` hook could not record the results (`dbt/macros/record_test_results.sql`): the domain's catalog is missing, the writer may not write it, `dq.dbt_test_results` no longer has the hook's columns, or two hand runs created the missing table at once (`Table already exists`; the DAG creates it first, so only outside it). The task fails; the tests' rows of that run are lost, the marts are built | the message names the catalog or the table. A catalog missing from Trino: [the rollout](#governance-rollout); a changed column: alter every domain's table to the hook's shape, or the governance view stops building too. Do not drop a results table to clear it: it is the record |
| `Catalog 'governance' not found` | catalog `governance` is not in Trino yet: the checkout is ahead of the stack | [the rollout](#governance-rollout); the other domains still build (`populate_cache: false`) |
| `Query exceeded per-node memory limit` | a query outgrew Trino's 844.80MB per-node memory | ADR-035's revisit trigger: measure, then raise inside ADR-045's headroom |
| the DAG is not in the list at all, or `list-import-errors` names `dbt_marts.py` | a stale bind mount of `docker/airflow/dags` or `dbt/`; or an image older than the checkout (no `cosmos` module, no `/opt/k2/dbt-venv`, or dbt and its packages behind `dbt/uv.lock` and `dbt/package-lock.yml`) | rebuild the image, then recreate the three Airflow services (below). The tag stays `k2-airflow:3.3.2`, so only a build replaces it |

Reproduce one domain by hand from the host, as its writer, against the same Trino:

```bash
cd dbt
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt source freshness --selector market_analytics
#   1 of 1 PASS freshness of gold.trades ........................................... [PASS in 1.05s]
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt build --selector market_analytics
#   record_test_results: 24 test result(s) into market_analytics.dq.dbt_test_results
#   Done. PASS=31 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=31
K2_TRINO_USER=dbt_governance uv run --locked dbt build --selector governance   # after the others
#   Done. PASS=13 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=13         (make test-dbt's Trino)
cd ..
```

The hook's failure, on the stack on 2026-11-25, as a user whose catalog does not exist (every
test passed, nothing was written):

```bash
cd dbt
K2_TRINO_USER=dbt_nobody uv run --locked dbt test --select ohlcv_1m --exclude test_type:unit
#   5 of 5 PASS ohlcv_1m_unique_on_its_grain ....................................... [PASS in 0.10s]
#   Exited because of keyboard interrupt
#   Done. PASS=5 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=5
#   [ERROR]: Encountered an error:
#   Database Error
#     TrinoUserError(type=USER_ERROR, name=CATALOG_NOT_FOUND, message="line 3:5: Catalog 'nobody' not found", ...)
echo $?
#   2
cd ..
```

dbt prints `keyboard interrupt` because the hook's error reaches it while it runs the hooks; no
key was pressed.

Whether the image the stack runs is older than the checkout's `dbt/`: the image test that
`make test-airflow` runs, against that image. Run on 2026-11-25 against the image the stack
ran then, built before `dbt_marts`:

```bash
docker run --rm --network none -v "$PWD":/repo:ro k2-airflow:3.3.2 \
  python -m unittest discover -s /repo/docker/airflow/tests -p 'test_dbt_image.py' 2>&1 | tail -1
#   FAILED (errors=2)        an image built from this checkout prints OK
```

An image older than the checkout, or a stale mount: rebuild (a no-op from the layer cache
when nothing changed), then recreate the services that run it. `--dry-run` printed this on
2026-11-25; run it without the flag:

```bash
docker compose --dry-run build airflow-migrate
#   Image k2-airflow:3.3.2 Building
#   airflow-migrate ==> ==> naming to k2-airflow:3.3.2
#   Image airflow-migrate Built
docker compose --dry-run up -d --force-recreate --no-deps airflow-api-server airflow-scheduler airflow-dag-processor
#   Container k2-airflow-dag-processor Created
#   Container k2-airflow-scheduler Created
#   Container k2-airflow-api-server Started
#   ...
```

Recreate between two `lake_ingest` runs: the scheduler's stop stops a running ingest's driver
([airflow-scheduling.md](./airflow-scheduling.md)).

Once the cause is fixed, do not wait for minute 10:

```bash
docker exec k2-airflow-scheduler airflow dags trigger dbt_marts
docker exec k2-airflow-scheduler airflow dags unpause dbt_marts   # if it was paused
```

**Measured.** Induced on 2026-11-25 in the throwaway container: its network to Trino
disconnected, a triggered run failed in 10 s (15:46:39 to 15:46:49 UTC) on the two root tasks,
the rest `upstream_failed`; reconnected, a triggered run succeeded 37 s later (the run itself
33 s, 15:47:14 to 15:47:48). Successful runs there took 31 to 40 s. The alert was not waited
for; its 3-hour window, the 10-minute hold, the never-succeeded case and the exporter-restart
case are `docker/prometheus/rules/tests/airflow-alerts_test.yml`.

---

## 2. The daily snapshot maintenance failed

**Symptom.** `<domain>_expire_mart_snapshots` failed in the 04:10 run. A rebuild adds one
snapshot per table per hour, so a mart keeps more than 7 days of history until the next
success; nothing is wrong with its rows.

**Detection.** `DbtMartsSnapshotExpiryStale` (`docker/prometheus/rules/airflow-alerts.yml`),
warning: a domain's `<domain>_expire_mart_snapshots` has not succeeded for 26 hours, held 10
minutes, so it fires about two hours after the first 04:10 run whose expiry failed, was skipped
after a failed build, or did not run. Neither other rule sees it: `AirflowTaskFailed` leaves
`dbt_marts` out and the day's other runs keep `DbtMartsNotSucceeding` quiet. The task's state
in the 04:10 run: section 1's commands.

**Expected behaviour.** The next day's 04:10 run retries it. It runs, as the domain's writer,
`ALTER TABLE ... EXECUTE expire_snapshots(retention_threshold => '7d')` and then
`remove_orphan_files(retention_threshold => '7d')` on every table mart of the domain, and the
same on its `dq.dbt_test_results` after `optimize`, because that table is appended to, a small
file per tested node per run (`dbt/macros/expire_mart_snapshots.sql`). 7 days is the Iceberg connector's floor, and the
session properties that would lower it are denied to every user (ADR-044): a shorter retention
fails, by design.

**Recovery.** Run it by hand as the writer:

```bash
cd dbt
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt run-operation expire_mart_snapshots
#   expire_mart_snapshots: "market_analytics"."candles"."ohlcv_1m" expire_snapshots at 7d
#   expire_mart_snapshots: "market_analytics"."candles"."ohlcv_1m" remove_orphan_files at 7d
#   expire_mart_snapshots: "market_analytics"."venue_quality"."venue_quality_scorecard" expire_snapshots at 7d
#   expire_mart_snapshots: "market_analytics"."venue_quality"."venue_quality_scorecard" remove_orphan_files at 7d
#   expire_mart_snapshots: "market_analytics"."dq"."dbt_test_results" optimize
#   expire_mart_snapshots: "market_analytics"."dq"."dbt_test_results" expire_snapshots at 7d
#   expire_mart_snapshots: "market_analytics"."dq"."dbt_test_results" remove_orphan_files at 7d
cd ..
```

The statements on a scratch table, as the writer, and what the floor does (run 2026-11-25 in
schema `market_analytics.scratch_dbt`, then dropped):

```bash
docker exec k2-trino trino --user dbt_market_analytics --execute \
  "ALTER TABLE market_analytics.scratch_dbt.t EXECUTE remove_orphan_files(retention_threshold => '7d')"
#   "processed_manifests_count","2"
#   "active_files_count","12"
#   "scanned_files_count","12"
#   "deleted_files_count","0"
#   "deleted_bytes","0"
docker exec k2-trino trino --user dbt_market_analytics --execute \
  "ALTER TABLE market_analytics.scratch_dbt.t EXECUTE expire_snapshots(retention_threshold => '1d')"
#   Query ... failed: Retention specified (1.00d) is shorter than the minimum retention configured
#   in the system (7.00d). ...
docker exec k2-trino trino --user dbt_market_analytics \
  --session market_analytics.expire_snapshots_min_retention=0s --execute "SELECT 1"
#   Query ... failed: Access Denied: Cannot set catalog session property expire_snapshots_min_retention
```

**Measured.** Not induced. The run-operation above logged all four mart statements within one
second (15:28:19 UTC, 2026-11-25), and with the results table's three, all seven between
18:30:10 and 18:30:11 UTC the same day; `optimize` left that table one data file.

---

## Governance rollout

Catalog `governance` exists in Trino only once its warehouse does and Trino has restarted with
the generated `docker/trino/etc/catalog/governance.properties`. Until then the governance group
fails with `Catalog 'governance' not found` and every other domain builds. Once, after the pull
request that added the domain ([ADR-046](../adr/ADR-046-governance-domain-and-quality-results.md))
is merged, in the live checkout. The outputs marked "before" are the stack's on 2026-11-25,
read-only; the rollout itself ran on 2026-11-25 from 19:29 UTC, and the outputs marked "after"
and the two at the end are that run's.

```bash
# 1. No dbt_marts run while Trino restarts.
docker exec k2-airflow-scheduler airflow dags pause dbt_marts
# 2. The merged main: the warehouse list, the catalog file, rules.json, the DAG and the models.
git checkout main && git pull --ff-only
# 3. lake-init creates the warehouses in docker/lake/generated/warehouses.txt that are missing
#    (it only creates, ADR-044), then exits.
docker compose up --force-recreate --no-deps lake-init
# 4. The warehouses.
curl -s 'http://localhost:18181/management/v1/warehouse?project-id=00000000-0000-0000-0000-000000000000' \
  | jq -r '.warehouses[].name' | sort
#   before: fx_reference k2 market_analytics market_capture reference_data (one per line)
#   after: governance among them
# 5. Trino with the new catalog and rules.json: down while it starts.
docker compose up -d --force-recreate --no-deps trino
timeout 180 sh -c 'until docker exec k2-trino trino --execute "SELECT 1" >/dev/null 2>&1; do sleep 5; done'
docker exec k2-trino trino --execute 'SHOW CATALOGS'
#   before: "fx_reference" "lake" "market_analytics" "market_capture" "reference_data" "system"
#   after: "governance" among them
# 6. The DAG parses, with the governance group and a results-table task per domain. No image
#    rebuild: the change moves no dbt pin (the image test above, against k2-airflow:3.3.2 with
#    this branch mounted, printed OK on 2026-11-25).
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list-import-errors 2>/dev/null
#   before: No data found
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow tasks list dbt_marts 2>/dev/null \
  | grep -E '^governance|_test_results_table$'
#   before: nothing (the live DAG listed market_analytics.* and its two maintenance tasks only)
#   after: governance.* and governance_test_results_table, market_analytics_test_results_table
# 7. Back on, and a run now rather than at minute 10.
docker exec k2-airflow-scheduler airflow dags unpause dbt_marts
docker exec k2-airflow-scheduler airflow dags trigger dbt_marts
```

When that run has ended (`airflow dags list-runs dbt_marts`, section 1):

```bash
docker exec k2-trino trino --execute 'SHOW SCHEMAS FROM governance'
docker exec k2-trino trino --execute "SELECT source_system, status, count(*) > 0
  FROM governance.quality_results.dq_results GROUP BY 1, 2 ORDER BY 1, 2"
```

The rollout's run (`manual__2026-11-25T19:30:44`, success in 82 s) printed:

```
"dq"
"information_schema"
"quality_results"
"staging"
"system"

"dbt","pass","true"
"lake_audit","fail","true"
"lake_audit","pass","true"
```

Trino is down while it restarts at step 5; `lake_ingest` does not use it and kept running.


---

## Failure modes / incidents

- **2026-11-25**, written with the DAG, before its first deployment; no incident yet.
- **2026-11-25**, the test-results hook: a `market_analytics` build from a checkout with the
  governance models failed on a Trino without catalog `governance` (`Catalog 'governance' not
  found`), because dbt listed every schema of the project first. `populate_cache: false` fixed it
  before merge; a domain's run now reads its own catalog only.

---

**Last verified:** 2026-11-25 against branch `feat/dbt-airflow-marts` on the live Trino 484, the
Airflow commands in a throwaway `k2-airflow:dbt-test` container; the test-results rows, the
hook's failure and the maintenance of `dq.dbt_test_results` against branch
`feat/governance-dq-results` on the live Trino, the governance build on `make test-dbt`'s; the
rollout on the live stack 2026-11-25 (main at `61e25c2`), every step and output above.
