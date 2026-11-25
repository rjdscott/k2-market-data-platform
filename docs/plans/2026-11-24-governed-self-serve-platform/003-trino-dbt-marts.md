# Phase P3: Trino, dbt marts and quality results

**Depends on:** P1, P2
**Decision it implements:** ADR-035 (Trino and dbt for marts; the Spark and dbt boundary), written in this phase. It departs from [ADR-006](../../adr/ADR-006-spark-batch-only.md), which rejected Trino as a replacement for Spark in batch ETL: here Trino serves and builds set-based marts and Spark keeps the ETL. It lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §6 for SQL serving and scopes [ADR-029](../../adr/ADR-029-research-production-parity-contract.md) §3 for marts, appending an Outcome to each.
**Delivers:** a SQL engine over the lake, a dbt project with its first consumer marts and one ported gold aggregate proven equal to Spark, a batch audit on the ingest, and one place to read quality results.
**Exit:** the `dbt_marts` DAG builds and tests green; dbt `ohlcv_1m` equals lake `gold.ohlcv_1m` on open, high, low, close, trade count and volume for a pinned snapshot; a batch with a missing offset writes an `audit.checks` row and fires its alert without blocking the commit; the dbt CI job passes.

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
  and entitlement checks.
- ADR-035, including the measured figures for the budget.

**Pull request 2: dbt and the first marts**

- `dbt/` with its own `pyproject.toml` and `uv.lock`: `dbt-trino`, pinned. Run by Airflow through astronomer-cosmos from a manifest built into
  the image; `dbt deps` runs at image build, never at task time.
- `profiles.yml` has one target, `trino`. Targets for other engines are added by the plan
  that brings an engine to run them against; a target that is only parsed proves little.
  Models use dbt's cross-database macros where they cost nothing, and the guide says which
  models would port unchanged and which would not.
- Layout: `staging/` (one view per source table), `intermediate/`, `marts/`. Marts are
  materialised as tables in namespace `marts`.
  - `venue_quality_scorecard`: per venue and day, from `lake.audit.checks`.
  - `ohlcv_delayed_20m`: rows older than 20 minutes only.
  - `ohlcv_1m`: the port of `gold.candles()` (`docker/lake/gold.py:316`), the worked example.
    It uses `min_by` and `max_by`, which not every warehouse has, and is tagged as such.
- `k2gen` emitter for dbt sources and source tests.
- Tests: generic tests, unit tests on the bucketing, source freshness. The maintained
  dbt-expectations fork if it supports the pinned adapter; otherwise the same checks as
  generic tests, and ADR-035 says which.
- Parity: `scripts/parity_ohlcv.py` gains a side read through Trino at the same snapshot id
  (`FOR VERSION AS OF`). `quote_volume` joins the comparison only if `typeof` shows both
  engines produce the same decimal.
- CI job `dbt`: `dbt deps`, `dbt parse`, `dbt compile --target trino`
  against the CI Trino, a pinned SQL linter with layering rules (a mart may not select from
  a source), and a manifest check that fails a mart with no uniqueness test on its grain.
- ADR-029 Outcome: marts are covered by dbt unit tests and a reference computation where one
  is cheap, not by a three-engine parity check. `ohlcv_1m` has the full check.
- `maintenance.py` takes its table lists from the contracts, so `marts` tables are compacted
  and expired like every other table.

**Pull request 3: ingest batch audit and quality results**

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
whose product table becomes generated from the contracts; runbooks for a failed batch audit
and a failed dbt build.

## Verification

```bash
docker exec k2-airflow-scheduler airflow dags test dbt_marts
make parity-ohlcv
make test-python                                  # batch audit: a short batch records and still commits
(cd dbt && uv run dbt deps && uv run dbt parse)
make generate && test -z "$(git status --porcelain)"
make gates
```

_Phase P3 landed 2026-11-26 (AEDT), commits #200 7e70fb3, #201 e4c66db, #202 8452529, #204 61e25c2, #206 c0cb1bc. Where it differs from the text above: the marts live in one warehouse per domain, not a namespace `marts` (ADR-044); `governance.dq_results` is `governance.quality_results.dq_results`, fed by each domain's dbt run through Trino, not by a Spark task (ADR-046); the batch audit's decision is ADR-047, and ADR-036 stays reserved; `maintenance.py` does not maintain the marts, each domain's writer does through Trino (ADR-035 Outcome)._
