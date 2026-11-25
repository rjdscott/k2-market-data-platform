# Runbook: The dbt marts are not being rebuilt

The domain marts on Trino are rebuilt every hour at minute 10 by the Airflow DAG `dbt_marts`
(`docker/airflow/dags/dbt_marts.py`, [ADR-035](../adr/ADR-035-trino-and-dbt-for-marts.md),
[ADR-044](../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)): one task group per
domain with marts, every task run as that domain's writer `dbt_<domain>`, and once a day, in
the 04:10 run, the domain's snapshot maintenance. This covers a run that fails, a DAG that is
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
| 2 | The daily snapshot maintenance failed | the next day's 04:10 run, or one run-operation | not induced; the run-operation by hand took under 1 s for two tables (2026-11-25) |

---

## 1. No `dbt_marts` run has succeeded for 3 hours

**Symptom.** The marts stop moving: `market_analytics.candles.ohlcv_1m` and the scorecard keep
their last build, the delayed view shows that build minus 20 minutes. Nothing else in the
stack is affected: a mart is read by Trino's clients only, and the lake does not depend on it.

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
descendants (`upstream_failed`) and nothing else.

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
| `Failure in test <name>` | a mart's grain, envelope or a source test failed on real data | a finding: read the test in `dbt/models/**/_*__models.yml`, rerun it by hand (below) |
| `Query exceeded per-node memory limit` | a query outgrew Trino's 844.80MB per-node memory | ADR-035's revisit trigger: measure, then raise inside ADR-045's headroom |
| the DAG is not in the list at all | stale bind mount of `docker/airflow/dags` or `dbt/` | recreate the scheduler and the dag-processor (CLAUDE.md, bind-mount gotcha) |

Reproduce one domain by hand from the host, as its writer, against the same Trino:

```bash
cd dbt
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt source freshness --selector market_analytics
#   1 of 1 PASS freshness of gold.trades ........................................... [PASS in 1.05s]
K2_TRINO_USER=dbt_market_analytics uv run --locked dbt build --selector market_analytics
#   Done. PASS=25 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=25
cd ..
```

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

**Detection.** The run fails, so a maintenance that keeps failing is `DbtMartsNotSucceeding`
only if the builds fail too; one failed 04:10 run alone does not alert. Manual check: the task's
state in the 04:10 run (section 1's commands).

**Expected behaviour.** The next day's 04:10 run retries it. It runs, as the domain's writer,
`ALTER TABLE ... EXECUTE expire_snapshots(retention_threshold => '7d')` and then
`remove_orphan_files(retention_threshold => '7d')` on every table mart of the domain
(`dbt/macros/expire_mart_snapshots.sql`). 7 days is the Iceberg connector's floor, and the
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

**Measured.** Not induced. The run-operation above logged all four statements within one
second (15:28:19 UTC, 2026-11-25).

---

## Failure modes / incidents

- **2026-11-25**, written with the DAG, before its first deployment; no incident yet.

---

**Last verified:** 2026-11-25 against branch `feat/dbt-airflow-marts` on the live Trino 484, the
Airflow commands in a throwaway `k2-airflow:dbt-test` container.
