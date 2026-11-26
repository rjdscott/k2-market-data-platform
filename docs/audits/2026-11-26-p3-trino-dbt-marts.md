# Audit: phase P3, Trino, dbt marts and quality results, as shipped and running (2026-11-26)

**Verdict:** P3's Trino fences hold for every SQL statement probed, and the hourly `dbt_marts`
DAG builds and tests green on the live stack. The phase is not release-ready. The worst finding
sits under every fence. MinIO's S3 API and console are published on every host interface,
including the Wi-Fi interface. The live root credential is weak, and Trino uses it for all six
catalogs. So any client on the same network can rewrite or delete the producer lake and every
domain warehouse without going through Trino at all. Four more findings fail the phase as a
whole:

- Any Trino user can roll back the dbt quality record. ADR-044 excuses the table-procedure gap
  because "a mart is rebuilt by dbt", and that does not hold for an append-only record.
- The parity gate named in P3's Verification cannot pass. Its pin is a day the rebuilt lake
  does not hold.
- The lake's own 1-minute candles disagree with its trades for one window in August. No check
  can see it, and the hourly mart now shows the difference.
- ADR-034's standing trigger on ingest duration has fired: p90 is 253 s of a 300 s slot. No
  alert watches it.

Before the next tag: bind MinIO to loopback and rotate its keys, re-pin parity, and put a
detective control on the quality record.

| | |
|---|---|
| **Commit** | `fe817a8` (main when the audit started; main moved to `adc5fe4`, P4 #214, during it, and the live Spark jobs read `docker/lake/` from the main checkout, so runs after 22:19 UTC executed `adc5fe4`'s lake code) |
| **Scope** | P3 as merged in #200 to #213: `docker/trino/`, `dbt/`, `docker/airflow/dags/dbt_marts.py`, `docker/airflow/Dockerfile`, the batch audit in `docker/lake/ingest.py` and `offsets.py`, `docker/lake/metrics.py`, the P3 alert rules and their tests, `contracts/` and `platform/k2gen/` changes, ADR-035, 044, 045, 046, 047, and the P3 docs: README, chapters 04, 12, 13, 14, 17, 18, 19, `dbt/README.md`, the runbooks `dbt-marts.md` and `lake-ingest-batch-audit.md`, `docs/development/lakehouse-labs.md` |
| **Out of scope** | Pre-P3 exposures not on P3's path (Redpanda, ClickHouse, Grafana and Prometheus published on all interfaces; the Docker socket in the Airflow scheduler, ADR-034); P4 work after `fe817a8` |
| **Lens** | Security and fences, data correctness, failure modes and operations, docs against reality, engineering practice, the phase Exit line |
| **Method** | Live stack, read-only, 2026-11-25 21:50 to 22:33 UTC. Statements: SELECT, SHOW, DESCRIBE and EXPLAIN through `docker exec k2-trino trino --user <name>`. Expected-denied writes were sent only as `EXPLAIN` or against nonexistent tables, and `CALL`s with nonexistent arguments, so none could commit. Also: Prometheus and Lakekeeper GETs, Airflow `list` commands and task-log reads, `docker ps/inspect/stats/logs`, and cgroup files. Two read-only agents covered docs against reality (35 claims, 24 documented commands run as written) and engineering practice (pins, fresh clone, CI, a 10-test sample, a mutation of `metrics.py`). Every BLOCKER and HIGH below was re-checked by hand. No write was permitted or made. Docker Desktop shut down at 22:33:43 UTC, after its backend logged `secrets engine shutting down` and `daemon terminating due to cancellation`. The audit made no Docker Desktop or container lifecycle call. The stack, captures included, was down from then on, so one re-check below cites its first run |
| **Findings** | 1 BLOCKER · 4 HIGH · 9 MED · 10 LOW |

Severity, as briefed: **BLOCKER** is data loss, corruption, or a fence that does not hold,
reachable today. **HIGH** would fail under a realistic event, or is a control that silently does
nothing. No market-derived number appears below: where a comparison of market rows matters, the
query is given and the result is stated as zero or non-zero.

---

## Findings

| Severity | Location | Claim | Reality | Fix |
|----------|----------|-------|---------|-----|
| BLOCKER | `docker-compose.yml:263-264`; `docker/trino/etc/catalog/lake.properties:16-17` and each domain catalog `:17-18` | ADR-044:112-115: identity is a header "on a loopback port", the fences "stop mistakes, not a liar", and Trino "holds the root keys" | MinIO is not on loopback. `ss -ltn` lists `*:9000` and `*:9001`. From the host's Wi-Fi address, `curl http://<wlan-ip>:9000/minio/health/live` returned `200` and the console `:9001` returned `200`, while Trino `:8088` and Lakekeeper `:18181` refused. In the live `.env` the root user and password are the same 10-character string (`[ "$u" = "$p" ]` true, compared without printing). Trino's startup log prints `s3.aws-access-key` in clear once per catalog, so `docker logs k2-trino` holds the secret on 6 lines. All six catalogs, the producer lake `k2` included, use that one key, so a LAN client can delete `warehouse/k2/` or any domain prefix without going through Trino. No authenticated LAN request was sent | Publish `127.0.0.1:9000:9000` and `127.0.0.1:9001:9001`: containers reach `minio:9000` on `k2-net`, and host tools use `localhost`. Rotate the root credential. Have `scripts/dev-up.sh` (a) refuse a `.env` whose MinIO user equals its password |
| HIGH | `docker/trino/README.md:84-89`, `docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md:116-121` | The known gap: any user can run table procedures in a domain catalog, but "the lake is not exposed by it; a mart is rebuilt by dbt from the lake" | ADR-046 added a table that is not rebuilt: `<catalog>.dq.dbt_test_results`, append-only, where "rows are never deleted" (ADR-046:101). As user `auditor`, `EXPLAIN INSERT INTO market_analytics.dq.dbt_test_results ...` gave `Access Denied: Cannot insert into table` and `EXPLAIN DELETE FROM governance.dq.dbt_test_results` gave `Cannot delete from table`. `EXPLAIN ALTER TABLE governance.dq.dbt_test_results EXECUTE optimize` as the same user planned, with no Access Denied. Table procedures share one empty `checkCanExecuteTableProcedure` (ADR-044:29), so `rollback_to_snapshot` is open the same way. Any unprivileged user can drop a domain's recorded failures out of `governance.quality_results.dq_results`, and the daily expiry deletes the rolled-off snapshots after 7 days | State the gap for the quality record in an ADR-046 Outcome and in the Trino README. Add a governance dbt test that fails when any `<catalog>.dq."dbt_test_results$history"` row has `is_current_ancestor = false`, which catches a rollback within the 7-day window. Close it with OPA in P5 |
| HIGH | `tests/parity/pinned.json` (`"day": "2026-08-27"`), `scripts/parity_ohlcv.py:297-302`, `Makefile:39-42,179-180` | P3 Exit: dbt `ohlcv_1m` equals lake `gold.ohlcv_1m` "for a pinned snapshot"; P3's Verification and `make gates` run `make parity-ohlcv` | The pin names a day the rebuilt lake does not hold. Through Trino, `lake.gold.ohlcv_1m` has `0` rows where `CAST(window_start AS DATE) = DATE '2026-08-27'`, its earliest `window_start` is `2026-08-28 08:19 UTC`, and the pinned `ohlcv_1m_snapshot` is not in `"ohlcv_1m$snapshots"` (`0`). `resolve_snapshot` therefore falls back to current, side B is empty, and `not_empty` prints `PARITY: FAIL - nothing to compare` and exits 1. The only P3 parity evidence is a hand run for 2026-11-24 at the snapshots current on 2026-11-25 (`dbt/README.md:445-453`). Its ids were never pinned, so that "pinned snapshot" exists nowhere in the repo | `scripts/parity-ohlcv.sh --pin-current 2026-11-24 --dbt` on the live stack, then commit `tests/parity/pinned.json`. Check the `bars` block the same way |
| HIGH | `docker/lake/gold.py:426-431` and `:158-198`; `dbt/models/market_analytics/marts/candles/ohlcv_1m.sql` | ADR-035 and dbt/README: the mart is "the port of gold.candles()" and matches the lake's candles; the nightly `ohlcv_parity` guards the lake's candles | They do not match over the whole table, and nothing can see it. A full outer join of `market_analytics.candles.ohlcv_1m` with `lake.gold.ohlcv_1m` on `(exchange, canonical_symbol, window_start)` for every minute before 21:00 UTC finds no lake-only key. It does find keys present only in the mart, and keys where the mart has more trades, all within 2026-08-28 14:26 to 14:57 UTC, on all three venues. `lake.gold.trades` has no duplicate identifier in that window, so the lake's candles under-count its own trades there. `ohlcv_parity` compares only "yesterday" (`maintenance.py:590`), and P3's parity covers one day. One code path produces exactly this shape. `stage()` commits `gold.trades` per venue, advancing its source position, before `stage_ohlcv` MERGEs the candles. A driver killed between the two leaves those buckets uncomputed, because the next run's touched set comes from the next batch only | Recompute 2026-08-28 in the lake (`lake_rebuild` for gold). Add a dbt test in `market_analytics` comparing the mart with `lake.gold.ohlcv_1m` on minutes older than the venue's newest, so the hourly run is the whole-history check. Make `stage_ohlcv` take its touched buckets from the `gold.trades` snapshots since the last candle commit, not from the in-memory batch |
| HIGH | `docs/adr/ADR-034-airflow-3-replaces-prefect.md:465-466`; `docs/adr/ADR-047-ingest-batch-audit-after-publish.md:29-30` | "The trigger that stands is p90 `lake_ingest` duration above 240 s over a day"; ADR-035's Context quotes a 168 s median | It fired, and P3 then raised the cap 3x (#203) and added a per-run audit (#206). `airflow dags list-runs lake_ingest` for 02:11 to 22:16 UTC gives 231 finished runs, p90 253 s, max 581 s and 11 over 300 s. Two failed with `java.lang.OutOfMemoryError: Java heap space` (17:41, 18:36) before #205. The hourly median rose from 168 s at 08h to 251 s at 22h. The statsd summary agreed: `airflow_dagrun_duration_success{dag_id="lake_ingest",run_type="scheduled",quantile="0.9"}` was `254.98`. Spark peaked at 3.005 cores of its 3.0 quota over 6 h (cAdvisor). No rule reads run duration, so the first signal is `LakeIngestLagHigh` at 900 s of lag. A busier market day runs out of slot | Treat the trigger as fired: append an ADR-034 Outcome and decide the measured change, the bars recompute first (its growth through the UTC day matches the creep). Add a warning on that p90 above 240 for 1 h, runbook `lake-ingest-lag.md`, with a promtool test |
| MED | `dbt/models/market_analytics/marts/venue_quality/venue_quality_scorecard.sql:51-53` | The scorecard is how complete the lake is per venue and day; "Read it before trusting a venue's day" (`docs/products/market-analytics/venue-quality.md:4`) | It counts ingest rows only for `check_name = 'offset_gap'`. A `batch_offsets` row (#206, landed after the scorecard in #202), scoped `market.crypto.v3.raw.<venue>/<p>`, never reaches the venue's day, and neither do `unresolvable_schema_id` or `bronze_parity`. A hole the batch audit found leaves the scorecard clean until the nightly `offset_continuity` files it, under the day it ran, not the day of the hole | Count `batch_offsets` (and the other ingest checks) per venue and day, with a unit test row like the `offset_gap` ones |
| MED | `docs/runbooks/lake-ingest-batch-audit.md:90-92` | Step 2, marked verified: the newest run's log shows `stage 1: batch audit 0 failure(s)` | `find ... \| sort \| tail -1` picks the in-flight run, whose line is written only at its end. Runs take 236 to 278 s of 300 s, so the command printed nothing at 22:14 and 22:21 UTC. In an incident it would hide the `BATCH AUDIT FAILED` lines in the previous log | `... -name '*.log' \| sort \| xargs grep -h 'batch audit\|BATCH AUDIT' \| tail -5` |
| MED | `docker-compose.yml:709-711`; `docs/adr/ADR-047-ingest-batch-audit-after-publish.md:109-110` | "A passing batch leaves no row, so 'did the audit run' is answered by the task log" | The scheduler mounts only `dags/`, `dbt/` and the Docker socket, so task logs live in the container's writable layer. The scheduler was recreated at 16:58 UTC and its oldest `lake_ingest` log is from 17:12, and the 06:10 `lake_maintenance` run has no log at all. CLAUDE.md asks for exactly that recreate after a checkout switch, and each one erases the evidence ADR-047 relies on | A named volume for `/opt/airflow/logs` on the scheduler, or an ADR-047 Outcome that names a durable record |
| MED | `docs/runbooks/lake-ingest-batch-audit.md:133-135`, `:182-184` | The recovery for `LakeIngestBatchAuditFailed` (critical) is a re-read while Redpanda still holds the records (ADR-047:106-107) | Steps 5 (the re-read into `raw.messages`) and 6 (record an evicted hole) are marked `NOT YET RUN` with `<!-- VERIFY -->`. The one path that turns the alert into no loss writes the system of record, and it has never been executed. CLAUDE.md: "A runbook nobody ran is fiction" | Run step 5 against a scratch copy of `raw.messages` and step 6 against a scratch `audit.checks`, paste the outputs, and drop the markers |
| MED | `tests/test_lake_metrics.py:150-163`, `docker/lake/metrics.py:58-69` | The test guards the class of bug that kept `LakeUnresolvableSchemaId` silent: "a gauge read off a commit property nobody sets" | It greps the writers for `O.<NAME>` but never compares `metrics.py`'s duplicated string literals with `offsets.py`'s, and its four names are a hand list. Changing `metrics.py`'s `BATCH_AUDIT_FAILURES` to `"k2.batch-audit-failure"` left `test_lake_metrics.py` at 6 passed, which makes `LakeIngestBatchAuditFailed` unable to fire. Today all nine duplicated constants match | Assert every `k2.*` and job constant in `metrics.py` equals the same name in `offsets.py`, enumerated, not listed |
| MED | `docker/prometheus/prometheus.yml` (11 jobs, none for Trino); `docker/prometheus/rules/airflow-alerts.yml:75-76` | ADR-035 Consequences: "one more JVM always on"; `DbtMartsNotSucceeding` catches "a Trino that refuses or is down" | A Trino outage pages nothing for 3 h 10 min, because the only signal is `DbtMartsNotSucceeding`, which needs zero successes in 3 h plus `for: 10m`. Nothing watches the revisit trigger ADR-035 set on Trino's memory (`anon` above 4.5 GiB, `oom_kill` above 0); it was 3.52 GiB and 0. A dbt test failing every other hour never alerts, because `AirflowTaskFailed` excludes `dbt_marts`. Nothing routes any alert (ADR-028, no Alertmanager) | A `TrinoDown` rule on cAdvisor (`absent(container_last_seen{name="k2-trino"})`, or a scrape of Trino's `/metrics`), and one on `container_memory_rss{name="k2-trino"}` at the ADR-035 figure, each with a runbook row and a promtool test |
| MED | `dbt/dbt_project.yml:39`, `docker/airflow/dags/dbt_marts.py:137`, `docs/runbooks/dbt-marts.md:1-14` | The runbook covers "a run that fails" | `dbt build --select <model>` replaces the table (`on_table_exists: replace`) before running its data tests. A failed grain or envelope test therefore leaves the failing build as the table readers query, with only a `dq_results` row saying so. The runbook covers marts that are not rebuilt, not marts rebuilt wrong, and gives no step to put back the previous build | A runbook section: find the last passing build in `"<mart>$snapshots"`, then `ALTER TABLE ... EXECUTE rollback_to_snapshot(<id>)` as `dbt_<domain>`, verified on a scratch table |
| MED | `.github/workflows/ci.yml:196-202` | CI covers what runs in production | CI renders `dbt_marts` with a real `dbt ls` but never executes a cosmos operator. The path production runs is never exercised: `/opt/k2/dbt-venv`, `K2_DBT_PACKAGES`, cosmos's symlinked temp directory over the read-only mount, and the hook writing as `dbt_<domain>`. `make test-dbt` builds through `uv` with local packages against file-metastore catalogs. Only the live stack has run it | Execute one model-build task and one `<domain>_test_results_table` task through cosmos against the throwaway Trino with the local catalogs |
| MED | `docker-compose.yml:936-951`; ADR-044:112-115 | The fence a reviewer can trust is the connector's (`iceberg.security=read_only`) | Lakekeeper answers `GET /management/v1/info` with `"authz-backend":"allow-all"`. Any host process on loopback `18181`, or any container on `k2-net`, can drop or roll back a lake table, or delete a warehouse, through the REST API: ADR-044 itself removed one with `DELETE /management/v1/warehouse/<id>`. ADR-044's honest limits name Trino's header and the root keys, not this | Add the line to ADR-044's honest limits through an Outcome, and to the P5 scope |
| LOW | `dbt/models/market_analytics/marts/delayed_candles/ohlcv_delayed_20m.sql:41-47` | Only the venue's newest minute can be partial, so the horizon withholds it | The horizon is the newest minute across all of a venue's symbols. Exchange-to-receive latency over the last 6 h reached 2.015 s (kraken), 1.428 s (coinbase) and 0.526 s (binance) (`max((recv_ts_ns / 1e9) - to_unixtime(exchange_ts))` on `lake.gold.trades`). So a batch cut just after a minute boundary can hold a later minute for one symbol while missing another symbol's late trades in the minute before. In an ingest stall longer than 20 minutes, the view then serves that minute as final | Name it in the "Not covered" list, or hold back two minutes behind the horizon |
| LOW | `docker-compose.yml:275` (MinIO `memory: 1G`) | Limits sized to use (ADR-010) | `memory.peak` 1,074,634,752 bytes, which is the limit. `memory.events` showed `max 4596708` and `oom_kill 0`. `workingset_refault_file` was 250,910,821 pages, about 957 GiB re-read in 24 h, so the page cache cannot hold the hot set that Spark's ingest and Trino's hourly full scans of `gold.trades` share | Measure ingest stage times with and without the scan, then size the limit in an ADR-010 Outcome |
| LOW | `docker/lake/metrics.py:47,161`; ADR-044:146-149 | ADR-044: the next pull request teaches the readers, `metrics.py` among them | The exporter reads warehouse `k2` only. Mart and `dq` sizes and commit ages have no gauge, and nothing records the decision not to add them | One line in the ADR-035 Outcome that the exporter stays on `k2`, or per-domain gauges |
| LOW | `docker/prometheus/rules/lake-alerts.yml:477` | `LakeDiskUsageHigh` (0.80) watches the lake's disk | The gauge is the Docker VM's shared disk. It went from 0.358 to 0.537 in 24 h (`deriv(...[12h])` 0.228 a day), so at that rate the alert fires in about 1.2 days. `docker system df` shows 25.7 GB of reclaimable images after P3's rebuilds, and MinIO holds 81 MiB for `market_analytics` and 1 MiB for `governance` (`du -sm`) | Prune images in `lake-disk-usage-high.md`'s first step, and say there that the ratio is the VM's |
| LOW | `docs/architecture/19-lakehouse-and-domain-warehouses.md:189` | Spark jobs in `docker/lake/` are "the only writer" of `k2` | `SHOW SCHEMAS FROM lake` lists `scratch_p4`, with tables `wap_bronze` and `wap_silver`, committed by ad hoc Spark apps between 21:31 and 21:41 UTC. Spark holds the root key, and nothing but convention keeps other writers out | Say "by convention", or drop the scratch namespace after the spike |
| LOW | `README.md:200`, `:208` | "10 unit tests and 22 data tests" | 11 unit tests (8 `market_analytics`, 3 `governance`), live and in the models | 11, re-dated |
| LOW | `docs/architecture/19-lakehouse-and-domain-warehouses.md:168-171` | "two schemas that are not products ... `staging` ... and `dq`" | `LAYER_SCHEMAS` (`platform/k2gen/k2gen/cli.py:63-67`) also reserves `intermediate`; ch18 lists three | three |
| LOW | `docs/adr/ADR-047-ingest-batch-audit-after-publish.md:84` | "the Spark figure ... to be recorded after rollout" | Recorded in `docker/lake/README.md`; the ADR has no Outcome | Append an Outcome with the measured 0.4 to 1.2 s |
| LOW | `scripts/check-budget.py:73` | The gate fails an unpinned image | Only `FROM` lines are checked. `COPY --from=ghcr.io/astral-sh/uv:0.13.10` in `docker/airflow/Dockerfile:35` is outside it | Check `COPY --from=` references too |
| LOW | `docs/plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md:26`, `:81` | Cosmos runs "from a manifest built into the image" | `LoadMode.DBT_LS` at parse (`dbt_marts.py:135`), cached in an Airflow Variable; the landed line lists four departures, not this one | Add it to the landed line |

Every row carries `file:line` or a command with its output. A row that could not be
demonstrated was dropped, not softened.

---

## The Exit line, criterion by criterion

| Criterion | Proven on the live stack | Proven only by tests or by hand |
|---|---|---|
| The `dbt_marts` DAG builds and tests green | Yes for builds and tests: every run from 16:10 to 22:10 UTC `success` (`airflow dags list-runs dbt_marts`), 37 to 74 s each | The daily snapshot maintenance has never run in the DAG: no 04:10 run since deploy, and no `airflow_ti_finish` series exists for `*_expire_mart_snapshots`. Its statements ran by hand on scratch tables (`dbt-marts.md`) |
| dbt `ohlcv_1m` equals lake `gold.ohlcv_1m` for a pinned snapshot | No. Over the whole table they differ for 2026-08-28 14:26 to 14:57 UTC (HIGH above), and the committed pin cannot be run (HIGH above) | One hand run, 2026-11-24, at the snapshots current on 2026-11-25, not pinned (`dbt/README.md:445-453`) |
| A batch with a missing offset writes an `audit.checks` row and fires its alert without blocking the commit | No batch has failed. There is no `batch_offsets` row in `lake.audit.checks`, and `k2_lake_batch_audit_failures_total` reads `0` | `tests/test_lake_batch_audit.py` (with `_batch_offsets` mocked in every test) and the promtool test for `LakeIngestBatchAuditFailed`. The live logs show the clean path only (`stage 1: batch audit 0 failure(s) in 0.5s`). "Fires" means a Prometheus state: no Alertmanager exists |
| The dbt CI job passes | `CI success` on `fe817a8` and on every P3 merge (`gh run list --branch main`) | |

## Probes that held (method evidence, not findings)

Each was sent as a user the rules do not name (`auditor`) unless stated:

- `EXPLAIN INSERT` into `market_analytics.dq.dbt_test_results`: denied.
- `EXPLAIN INSERT` by `dbt_market_analytics` into `governance.dq.dbt_test_results`: denied.
- `EXPLAIN DELETE` by `dbt_governance` on `market_analytics.candles.ohlcv_1m`: denied.
- `EXPLAIN DELETE` by `dbt_market_analytics` on `lake.gold.ohlcv_1m`: denied.
- `EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize` as `trino`:
  `Cannot execute table procedure OPTIMIZE`.
- `CALL lake.system.rollback_to_snapshot`, `CALL market_analytics.system.rollback_to_snapshot`
  and `CALL governance.system.unregister_table`, all with nonexistent arguments:
  `Cannot execute procedure`.
- `--session <catalog>.expire_snapshots_min_retention=0s` and
  `remove_orphan_files_min_retention=0s` on `market_analytics`, `governance` and `lake`, the
  last two as `dbt_governance`: denied.
- `system.runtime.queries` showed only the caller's own queries.
- Six Lakekeeper warehouses matched six Trino catalogs.
- A definer view grants nothing extra today, because every user reads every table.

The docs agent found 31 of 35 claims true. All 24 documented read-only commands matched their
documented output except the one in the second MED row. In the 10-test sample, eight would fail
if their logic broke, `test_lake_metrics.py:150` only partly (MED above), and two are weak:
`docker/airflow/tests/test_dbt_marts_dag.py:49-51` asserts the literal `2`, and the reason given
in `platform/k2gen/tests/test_generate.py:228-231` is already handled in `init-lake.sh:143-144`.

## Not examined, and why

- **Authenticated access from another machine** to MinIO: it would have used the secret. Only
  reachability and credential weakness were shown.
- **Executing any table procedure**, a permitted write, an Airflow trigger, a scheduler restart
  mid-run, two concurrent `dbt_marts` runs, Lakekeeper or MinIO induced down, a full MinIO: all
  forbidden on the live stack. They were judged from code and the runbooks' recorded throwaway
  runs.
- **`make test-trino`, `make test-dbt`, `make test`**: forbidden heavy targets. README's
  "134 checks" for `make test-trino` was not verified.
- **Everything after 22:33 UTC**: Docker Desktop shut down, so one re-check (`EXPLAIN ... EXECUTE
  optimize` on `governance.dq.dbt_test_results`) is cited from its first run at 22:15 UTC.

---

## Resolutions

Appended after publication; the table above is never edited.

Each line was checked by reading the cited code on `origin/main` at `1feaa1a`, 2026-11-26.

- **Resolved in #218 (`adbe2d7`), the exposure:** BLOCKER `docker-compose.yml:263-264`. MinIO
  publishes `127.0.0.1:9000` and `127.0.0.1:9001` (`docker-compose.yml:290-291`), and
  `scripts/check-budget.py` fails CI on a published port off loopback;
  `docker/trino/etc/log.properties` sets `io.trino.bootstrap.catalog=WARN` and
  `scripts/trino-rules-test.sh:65-69` fails if a canary key reaches the log;
  `scripts/check-env.py`, run by `scripts/dev-up.sh:26` and `make up`, refuses a MinIO user
  equal to its password. Rotating the root credential is not on main (below).
- **Resolved in #220 (`791c77a`):** HIGH `tests/parity/pinned.json`, a pin on a day the lake
  does not hold. The pin is `2026-11-25`, with `dbt_trades_snapshot` and a `bars` block on the
  same day (`tests/parity/pinned.json:2-18`); #226 (`86504fd`) makes a pin that cannot be
  resolved stop the run.
- **Resolved in #228 (`410cc13`):** LOW `scripts/check-budget.py:73`, `COPY --from` unchecked.
  `scripts/check-budget.py:123` fails an unpinned `COPY --from` image.

## Deferred to the hardening sprint

Owner's decision, 2026-11-26: a phase audit fixes its critical and high findings at the time,
and the rest goes to the hardening sprint that runs after plan phase P5. These MED and LOW
findings are open on `origin/main` at `1feaa1a` and are scheduled for that sprint:

- MED `venue_quality_scorecard.sql:51-53`: ingest rows still count only `offset_gap`.
- MED `docs/runbooks/lake-ingest-batch-audit.md:90-92`: step 2 still reads `sort | tail -1`.
- MED `docker-compose.yml:709-711`: the scheduler still has no log volume
  (`docker-compose.yml:748-751`).
- MED `docs/runbooks/lake-ingest-batch-audit.md:133-135`, `:182-184`: steps 5 and 6 are still
  `NOT YET RUN`.
- MED `tests/test_lake_metrics.py:150-163`: still a hand list of four names, no comparison
  with `offsets.py`.
- MED `docker/prometheus/prometheus.yml`: no Trino scrape job and no Trino rule.
- MED `dbt/dbt_project.yml:39`: `docs/runbooks/dbt-marts.md` has no rollback section.
- MED `.github/workflows/ci.yml:196-202`: CI renders `dbt_marts` but executes no cosmos task.
- MED `docker-compose.yml:936-951`: Lakekeeper `allow-all` is in neither ADR-044's limits nor
  the P5 scope.
- LOW `ohlcv_delayed_20m.sql:41-47`: horizon unchanged; its "Not covered" list does not name
  the cross-symbol case.
- LOW `docker-compose.yml:275`: MinIO `memory: 1G` unchanged, no ADR-010 Outcome.
- LOW `docker/lake/metrics.py:47,161`: the exporter reads `k2` only, no recorded decision.
- LOW `docker/prometheus/rules/lake-alerts.yml:477`: `lake-disk-usage-high.md` unchanged
  since 2026-08-28.
- LOW `docs/architecture/19-lakehouse-and-domain-warehouses.md:189`: still "the only writer".
- LOW `README.md:200`: still "10 unit tests and 22 data tests" (now `README.md:205`).
- LOW `docs/architecture/19-lakehouse-and-domain-warehouses.md:168-171`: still two schemas.
- LOW `docs/adr/ADR-047-ingest-batch-audit-after-publish.md:84`: no Outcome.
- LOW `003-trino-dbt-marts.md:26`, `:81`: the landed line does not name `LoadMode.DBT_LS`.

Open BLOCKER and HIGH, which that decision does not defer:

- BLOCKER `docker-compose.yml:263-264`, the remainder: rotation of the MinIO root credential
  is a change to the live stack, is not recorded on main, and was not checked here. The P0
  audit's BLOCKER (`2026-11-26-p0-prerequisites-guardrails.md`) covers the same credential.
- HIGH `docker/trino/README.md:84-89`, the quality record: no ADR-046 Outcome, no test on
  `"dbt_test_results$history"` (`git grep is_current_ancestor` finds nothing), and the README's
  known gap (`docker/trino/README.md:92-98`) still says a mart is rebuilt by dbt.
- HIGH `docker/lake/gold.py:426-431`, the candle mismatch: no whole-history dbt test, and
  `stage_ohlcv` (`docker/lake/gold.py:344-365`) still takes its buckets from the batch. A
  recompute of 2026-08-28 was not checked (no stack query).
- HIGH `docs/adr/ADR-034-airflow-3-replaces-prefect.md:465-466`, the duration trigger: the
  ADR-034 Outcome (#221, `f14aae6`) records it fired and ADR-048 (#215, `b057cce`) is the
  response, not built on main; no rule reads `lake_ingest` run duration.
