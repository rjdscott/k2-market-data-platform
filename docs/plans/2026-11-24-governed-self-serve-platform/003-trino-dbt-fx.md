# Phase P3: Trino, dbt marts, the FX domain and quality results

**Depends on:** P1, P2
**Decision it implements:** ADR-035 (Trino and dbt for marts; the Spark and dbt boundary), ADR-036 (layered quality; where write-audit-publish applies), ADR-037 (FX reference domain), all written in this phase. ADR-035 departs from [ADR-006](../../adr/ADR-006-spark-batch-only.md), which rejected Trino as a replacement for Spark in batch ETL: here Trino serves and builds set-based marts and Spark keeps the ETL. It lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §6 for SQL serving and scopes [ADR-029](../../adr/ADR-029-research-production-parity-contract.md) §3 for marts. ADR-037 lifts ADR-028 §4. Each appends an Outcome to the ADR it touches.
**Delivers:** a SQL engine over the lake, a dbt project with consumer marts and one ported gold aggregate proven equal to Spark, a fourth domain fed by XML behind a fail-closed audit, and one place to read quality results.
**Exit:** the `dbt_marts` DAG builds and tests green; dbt `ohlcv_1m` equals lake `gold.ohlcv_1m` on open, high, low, close, trade count and volume for a pinned snapshot; a corrupted FX file fails its audit, `main` keeps its prior snapshot id, and `k2lake.pin()` still returns that id; the dbt CI job passes.

## Scope

**Pull request 1: Trino, ADR-035**

- Spike first, on the running stack: Trino, pinned, against the Lakekeeper REST catalog
  (warehouse `k2`, static MinIO keys, path-style access, region `local-01`). Read
  `gold.trades`; create a table and a view in a scratch namespace; read that table from
  DuckDB; confirm session time zone UTC. The view result decides whether the delayed product
  is a view or a scheduled table.
- `docker/trino/`: one node, heap capped below the container limit, limits declared, host
  port from `K2_TRINO_PORT`. Catalog `lake`. Trino writes only namespace `marts`, added to
  `docker/lake/init-lake.sh`.
- CI job `trino`: the same pinned image with a `memory` catalog named `lake`, used by the dbt
  and entitlement checks below.
- ADR-035, including the measured figures for the budget.

**Pull request 2: dbt and marts**

- `dbt/` with its own `pyproject.toml` and `uv.lock`: `dbt-trino`, `dbt-athena` and
  `dbt-redshift`, pinned. Run by Airflow through astronomer-cosmos from a manifest built into
  the image; `dbt deps` runs at image build, never at task time.
- `profiles.yml` targets `trino` (runs), `athena` and `redshift` (placeholder settings).
- Marts in `marts`, materialised as tables:
  - `ohlcv_1d_fx`: daily OHLCV valued in each reporting currency listed in the mart's
    config (AUD, EUR, GBP and JPY to start) through `silver.fx_rates`. Trades are priced in
    USD and USDT, so every valuation is a derived cross and none is a plain lookup.
  - `venue_quality_scorecard`: per venue and day, from `lake.audit.checks`.
  - `ohlcv_delayed_20m`: rows older than 20 minutes only.
  - `ohlcv_1m`: the port of `gold.candles()` (`docker/lake/gold.py:316`), the worked example.
    It uses `min_by` and `max_by`, which Redshift lacks, so it is tagged Trino-only.
- `k2gen` emitter for dbt sources and source tests.
- Tests: generic tests, unit tests on the bucketing and the FX join, source freshness at
  `warn` with a four-day threshold (FX does not publish on weekends or TARGET holidays). The
  maintained dbt-expectations fork if it supports the pinned adapter; otherwise the same
  checks as generic tests, and ADR-035 says which.
- Parity: `scripts/parity_ohlcv.py` gains a side read through Trino at the same snapshot id
  (`FOR VERSION AS OF`). `quote_volume` joins the comparison only if `typeof` shows both
  engines produce the same decimal.
- CI job `dbt`: `dbt deps`, `dbt parse` for all three targets, `dbt compile --target trino`
  against the CI Trino. Stated plainly in the guide: `parse` shows the project loads for
  Athena and Redshift; it does not prove the SQL runs there.
- ADR-029 Outcome: marts are covered by dbt unit tests and the nightly reconciliation, not by
  a three-engine parity check. `ohlcv_1m` is the exception and has one.

**Pull request 3: FX domain, write-audit-publish, ADR-036, ADR-037**

- Spike first: a branch write and fast-forward from Spark on a scratch table.
- `notebooks/k2lake.py:84` and `scripts/parity_ohlcv.py:66-69` resolve the snapshot `main`
  points at, with a test, before any branch exists.
- `docker/lake/fx.py`: fetch with the standard library; parse with the standard library
  after asserting the bundled expat is 2.4.1 or newer (the image has no pip layer, and that
  version already refuses entity expansion). Takes the writer lock blocking, as maintenance
  does, with a bounded wait.
- Source: the ECB euro reference rates, chosen over the alternatives surveyed (RBA, Federal
  Reserve H.10, Bank of Canada, Bank of England, community wrappers) because it is the only
  one with no key, one file shape for a day, 90 days and the full history since 1999, 29
  currencies at one fixing, and terms that allow reuse with attribution. ADR-037 records the
  survey. The daily file feeds the schedule; the history file feeds the backfill.
- `bronze.ecb_fx_daily`: the XML verbatim as a string, plus one row per currency exactly as
  published (units of currency per euro, as a decimal string).
- `silver.fx_rates`: one row per date and currency pair, typed. The published euro rates are
  kept, and a USD-based cross is derived for every currency (`AUD per EUR / USD per EUR`),
  with a `derived` flag. The ECB's terms require a modified figure to be labelled, and both
  legs are fixed at the same moment (about 14:10 CET), so the cross is exact to the rounding
  of its inputs.
- The ECB publishes on TARGET working days only and crypto trades every day. Non-publishing
  days carry the last published rate forward with a `carried` flag, so a weekend candle is
  valued and a reader can see how.
- DDL emitted by `k2gen` into `docker/lake/ddl/generated/`. Never `raw.messages`. The source,
  the fixing time and "reference rate, not for transactions" are stated in the contract's
  description and reach the catalog from there.
- Write-audit-publish: write to a branch created from `main` at the start of the run, audit
  (row count, currencies present, rates positive, date not in the future, a business day not
  skipped), fast-forward `main`. A failed audit leaves the branch and writes an
  `audit.checks` row; `main` does not move.
- A committed copy of the 90-day XML file (about 70 KB, attributed to the ECB) seeds the tables on a fresh clone, so the marts build with
  no network. Tests use the fixture and a corrupted one; none reaches the network.
- DAG `fx_ingest`, weekdays after the ECB publication time, with a backfill parameter. It is
  the first DAG built by `docker/airflow/dags/factory.py` from generated config.
- `maintenance.py` takes its table lists from the contracts, so FX and `marts` are compacted
  and expired like every other table.
- `scripts/chaos/fx-bad-file.sh`: feeds the corrupted fixture and checks the three exit
  conditions.

**Pull request 4: ingest batch audit and quality results**

- Stage 1 of the ingest checks the batch it just wrote, per topic and partition: row count
  equals the offset range, and the first and last offsets match. A failure writes an
  `audit.checks` row and raises an alert with a runbook. It never blocks the commit:
  Redpanda's retention is shorter than a blocked archive could wait.
- `governance.dq_results`: a Trino view over `lake.audit.checks` and
  `governance.dbt_test_results`, the latter appended by a Spark task from dbt's
  `run_results.json`. `maintenance.AUDITS` remains the only audit framework.
- Reconciliation as Airflow tasks is already true after P2: the nightly candle and bar
  parity audits run inside `lake_maintenance`. The pinned-snapshot scripts stay a release
  gate, because they run on the host.

**Documents.** Chapters 04, 09, 12, 13, 14, 18 and A1; `docs/operations/data-catalog.md`,
whose product table becomes generated from the contracts; runbooks for a failed FX audit, a
failed batch audit and a failed dbt build.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make parity-ohlcv
docker exec k2-airflow-scheduler airflow dags test fx_ingest
bash scripts/chaos/fx-bad-file.sh
(cd dbt && uv run dbt deps && uv run dbt parse --target athena && uv run dbt parse --target redshift)
make generate && test -z "$(git status --porcelain)"
make gates
```
