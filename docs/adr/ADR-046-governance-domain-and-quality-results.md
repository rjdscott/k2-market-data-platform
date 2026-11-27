# ADR-046: Governance is a domain like any other; quality results are recorded by each domain's own writer and unified by a governance view

**Status:** Accepted
**Date:** 2026-11-25
**Author:** Rob Scott
**Category:** Ownership, Data quality

---

## Context

Quality results live in two places that cannot see each other. The lake's checks are rows of
`lake.audit.checks`, written by Spark: `maintenance.AUDITS` nightly and the ingest's findings
(`docker/lake/maintenance.py`, `docker/lake/ingest.py`). dbt's tests on the marts print to a log
and leave a `run_results.json` behind, and nothing keeps either. Phase P3's last pull request
owes "one place to read quality results"
([phase file](../plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md)).

The forces:

- **The fences of [ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md).** Trino writes
  only domain catalogs, each only as its writer `dbt_<domain>`; nobody writes `lake`
  (`iceberg.security=read_only`). Every user reads everything.
- **A dbt run is one user.** `profiles.yml` takes the user from `K2_TRINO_USER`, and the
  `dbt_marts` DAG runs each domain's task group as that domain's writer. A run can write its own
  catalog and no other.
- **Airflow runs one dbt invocation per node.** cosmos renders `dbt build --select <model>`,
  `dbt test --select source:<table>` and `dbt source freshness` per node, each in a temporary
  directory of symlinks it deletes afterwards (`docker/airflow/dags/dbt_marts.py`). No run leaves
  a `run_results.json` behind for a later task to read.
- **The plan's wording is superseded.** It has `governance.dbt_test_results` "appended by a
  Spark task from dbt's `run_results.json`" and a view `governance.dq_results`. ADR-044 already
  recorded that every object Trino owns lives in a domain catalog, and the maintainer's
  direction of 2026-11-25 is that governance is a platform-owned domain stored the same way.
  That reverses [ADR-033](ADR-033-data-mesh-four-domains.md)'s "The platform owns the road, not
  the products", which is why it is decided here (ADR-033's and ADR-044's Outcomes).
- **One audit framework for the lake.** `maintenance.AUDITS` writing `lake.audit.checks` stays
  the only one. dbt's tests are about marts and the sources they read; they do not become lake
  audits.

Verified in dbt-core 1.12.7's source and then on a throwaway Trino 484 (`make test-dbt`,
2026-11-25): `RunTask.execute_with_hooks` calls `after_run`, and with it the `on-run-end`
hooks, after the nodes whatever their status, and passes them dbt's `results`; `dbt build`,
`dbt test` and `dbt source freshness` run the hooks, `dbt compile` does not.

---

## Decision

**We will make `governance` a fifth domain, owned by `platform-team` and stored like every other
domain, have every domain's dbt runs record their own test results in their own catalog as their
own writer, and unify those with `lake.audit.checks` in one view in the governance catalog,
because then no writer ever writes outside its own catalog: the only step that crosses domains
is a read, which every user already has.** Scope: quality results. Control evidence and lineage
join this domain in later phases with their own decisions.

**The domain.** `contracts/domains.yaml` entry `governance`, consumer-aligned, owner
`platform-team`; `make generate` gives it warehouse and catalog `governance` and writer
`dbt_governance` exactly as ADR-044 gives any domain. The platform team now owns one domain
besides the road: its products are records about the other domains' data, which they never read.
`lake-audit` stays in `market-capture`: its rows are written by the lake's jobs about the lake's
tables, and ownership follows who can answer for the data (ADR-033).

**Recorded by each writer.** `dbt_project.yml`'s `on-run-end` hook
(`dbt/macros/record_test_results.sql`) inserts one row per data test and unit test of the
invocation into `<catalog>.dq.dbt_test_results`, the profile's own catalog, creating the schema
and table if absent: invocation id, run start, writer, the test's id and name, the model or
source relation it tests, dbt's status (pass, fail, warn, error, skipped), failures, execution
time and dbt's message cut at 1000 characters. An invocation with no test writes nothing; a
failed test is recorded like a passing one, and so is every test the same invocation skipped
behind a failure. A test Airflow never started is not recorded: `dbt_marts` runs one invocation
per node, and a node downstream of a failed task is `upstream_failed` and runs no dbt, so its
tests leave no row. A test with no recent row is that signal (`dbt/README.md` has the query).
The table is created, if absent, by `create_test_results_table`, which the hook calls and which
`dbt_marts` also runs as the domain's first task (Risks).

**`dq` is platform plumbing, like `staging`.** ADR-044's "one schema per data product" holds
for products. Every domain catalog also has the platform's schemas: `staging` and `intermediate`
for dbt's layers, and now `dq`. `k2gen validate` refuses a product named after any of them
(`LAYER_SCHEMAS` in `platform/k2gen/k2gen/cli.py`).

**Unified by a view.** Product `quality-results`, mart
`governance.quality_results.dq_results`, a view: `lake.audit.checks` (maintenance and ingest
rows, one per run, scope and check, the worst where a rebuild filed one several times;
`silver_flags`, which cannot fail, and the operator, verify and replay rows, a failed replay
included, left out as not checks of the platform's data) and
every domain's `dq.dbt_test_results`, in one shape: `checked_at`, `source_system` (`lake_audit`
or `dbt`), `domain`, `subject`, `check_name`, `status`, `detail`. `status` is pass, fail, warn or
error: a lake check that raised is error, and so is a dbt test that errored or was skipped.
`domain` is whose record it is: the writer's domain for a dbt test, `market-capture` for a lake
check. The branches are found when the view is built: one per catalog holding a model of the
project, so a domain with marts is in once its results table exists, and one with no table
yet is left out rather than breaking the view. A branch keeps only its own writer's rows.

**Built last.** `dbt_marts` builds the governance task group after every other domain's has
finished, however it ended (`domains_built`, trigger rule `all_done`), so the view is rebuilt
and tested on this run's results. A definer view: a reader reads the base tables as its owner,
`dbt_governance`, whom `rules.json` lets read every catalog.

**Kept small.** The daily `expire_mart_snapshots` also compacts the results table
(`optimize`), then expires and orphan-cleans it at the 7-day floor. Rows are never deleted
([ADR-028](ADR-028-non-goals-and-honest-limits.md) section 12). Each domain's table gains its
tests' count of rows every hourly run, so 24 times that a day, and a commit per tested node per
run. dbt's up-front listing of every schema of the project is off (`populate_cache: false`), so
a domain's run reads no other domain's catalog metadata.

---

## Rationale

- **No fence moves.** Each table has one writer, its domain's, the same identity that owns the
  marts it tests. A shared table would need the first cross-domain write rule in `rules.json`.
- **The hook is where the facts are.** `results` holds every test of the invocation in memory at
  its end, whatever cosmos does with the directory afterwards; a later task has no file to read.
- **Shown, not assumed.** `make test-dbt` fails a source test on purpose (two trades with one
  identifier) in one `dbt build --selector`, sees it exit non-zero, reads the failed row and the
  ones that invocation skipped from
  `market_analytics.dq.dbt_test_results`, and reads them, with a failed and a raised lake check,
  through the view as a user who owns none of it. A hook made to skip non-passing results fails
  that check.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Spark appends dbt's `run_results.json` to a lake namespace `governance` (the plan) | cosmos deletes each invocation's directory, so there is no file to append; a Spark task per run on the ingest's 3.0 CPU container; dbt's facts in the producer lake, a second kind of writer in Spark's warehouse |
| One shared results table every writer may insert into | needs `INSERT` for every `dbt_<domain>` in one catalog, the only cross-domain write rule ADR-044 would have; any writer could file, or delete, another domain's results |
| dbt artifacts uploaded to object storage, read as files | a new path writing the bucket with today's root keys and no fence until P5; Trino reads files only through another catalog and connector; the rows still need a view beside `lake.audit.checks` |
| `dbt_artifacts` (Brooklyn Data) | its README lists Trino and 3.0.0 needs dbt 1.10 or newer; its hook uploads invocations, executions and the manifest's models, sources and tests on every invocation into about twelve models, which with one invocation per node is a manifest upload per node per hour. It would sit in each domain's catalog under the same fences, so the layout is this one, plus a pinned dependency for one table that one macro here writes. Not installed here |
| Elementary | Trino joined its CLI and CI in its pull request 2135, and a metadata model failed on a Trino Delta Lake catalog in its issue 1648, put down to dbt-trino; it brings its own schema of models and a Python CLI for its report. The pick when a quality report UI is wanted. Not installed here |

---

## Consequences

**Easier:** one query for every quality result on the platform; a new domain's results are
recorded on its first run with no change anywhere; a failure is a row with its message, not a
log line that rotates away.

**Harder:** the results tables carry no contract, so the product's `reads` names only
`audit.checks` and the manifest check sees only that. A test that did not run because its
task never started leaves nothing, so "no failures" in the view is not "everything passed": a
reader checks that each test has a recent row. A lake check a rebuild filed several times under
one key is one row in the view; its other rows are read in `lake.audit.checks`. The view's list of tables is fixed when it
is built, so a domain's first results appear at the next governance build (the same run, by the
ordering). The tables' shape is one macro's: a column change must reach every domain's table at
once or the view stops building. A hook that cannot write fails a task whose tests all passed,
and dbt reports it as `Exited because of keyboard interrupt` then `Database Error`
([runbook](../../legacy/v3-lake-ingest/docs/runbooks/dbt-marts.md)). Views are Trino-only (ADR-035).

**Committed to:** the `on-run-end` hook in `dbt_project.yml`; `dq` as a reserved schema in every
domain catalog; governance built after the other domains; each domain's results table created
by a task of its own before the domain's other tasks; `populate_cache: false`.

**Risks:** two tasks of one domain can write its results table at once (`max_active_tasks=2`).
Measured on 2026-11-25 on a throwaway Trino 484 over `make test-dbt`'s local catalogs (Trino's
`TESTING_FILE_METASTORE`), from the repo root:

```bash
docker run -d --name k2-race --cpus 2 --memory 4g -v "$PWD/docker/trino/etc":/etc/trino:ro \
  -v "$PWD/docker/trino/test/local":/etc/trino/catalog:ro trinodb/trino:484
q() { docker exec k2-race trino --user dbt_market_analytics --execute "$1"; }
timeout 120 sh -c 'until docker exec k2-race trino --execute "SELECT 1" >/dev/null 2>&1; do sleep 2; done'
q 'CREATE SCHEMA market_analytics.dq'
for i in $(seq 20); do q "CREATE TABLE IF NOT EXISTS market_analytics.dq.r$i (i int)" & q "CREATE TABLE IF NOT EXISTS market_analytics.dq.r$i (i int)" & wait; done 2>&1 | grep -c 'Table already exists'
#   3            pairs of the 20 where one create failed, "Failed to create table ...: Table already exists"
q 'CREATE TABLE market_analytics.dq.t (i int)'
for _ in $(seq 8); do q 'INSERT INTO market_analytics.dq.t VALUES 1, 2, 3' & done; wait
q 'SELECT count(*), (SELECT count(*) FROM market_analytics.dq."t$snapshots") FROM market_analytics.dq.t'
#   "24","9"     every concurrent append committed: the create's snapshot and eight more
docker rm -f k2-race
```

The appends rest on Iceberg's optimistic retry and held there. The first create did not:
`IF NOT EXISTS` does not cover a create that loses the race, and dbt's Jinja has no way to catch
the error and go on (dbt-common's environment loads `jinja2.ext.do`, `loopcontrols` and dbt's
own materialization, docs and test tags, and no `try`). So `dbt_marts` creates each domain's
table in a task of its own, `<domain>_test_results_table`, before every other task of the
domain, and in the DAG the hook's own create only ever finds the table there. Two hand runs
started together on a domain with no table yet can still race; one of them then fails.
That local metastore is not Lakekeeper's REST catalog, and neither result was measured through
Lakekeeper: concurrent appends to one table there may conflict where they did not here, and
nothing above shows they are safe.

**Revisit when** any of these holds: a reader needs model executions or invocations, not only
tests (then `dbt_artifacts` per domain, above); a domain's table passes 1,000,000 rows (`SELECT
count(*) FROM <catalog>.dq.dbt_test_results`; then partition it by day, or decide a row
retention, which reopens ADR-028 section 12); the governance task group's build fails with
`EXCEEDED_LOCAL_MEMORY_LIMIT`; a `dbt_marts` task fails on a concurrent commit to a results
table (`CommitFailedException` or `Table already exists` in its log); or control evidence or lineage, phases P5 and P6,
land their first table in this domain.

---

## References

- [ADR-033](ADR-033-data-mesh-four-domains.md), whose line "The platform owns the road, not the
  products" this supersedes for the governance domain; [ADR-044](ADR-044-producer-lake-and-domain-mart-warehouses.md),
  the layout and fences used unchanged; [ADR-035](ADR-035-trino-and-dbt-for-marts.md), the engine.
- Plan: [phase P3](../plans/2026-11-24-governed-self-serve-platform/003-trino-dbt-marts.md),
  pull request 3; its "Spark task" wording is superseded here, not edited.
- Code: `dbt/macros/record_test_results.sql`, `dbt/macros/expire_mart_snapshots.sql`,
  `dbt/models/governance/`, `contracts/governance/quality-results/product.yaml`,
  `docker/airflow/dags/dbt_marts.py`, `scripts/dbt-test.sh`
- Upstream: dbt-core 1.12.7 `dbt/task/runnable.py` and `dbt/task/run.py`; the
  [`dbt_artifacts` README](https://github.com/brooklyn-data/dbt_artifacts) and Elementary's
  [`dbt-data-reliability`](https://github.com/elementary-data/dbt-data-reliability), read
  2026-11-25

---

## Outcome (2026-11-27, quality results recorded per product)

The mechanism of this decision retired with the old pipeline in phase 000: the `dbt_marts` DAG, its
per-domain `dq.dbt_test_results` tables, `lake.audit.checks` and the view
`governance.quality_results.dq_results` are archived in `legacy/v3-lake-ingest/`. Its principle held, one
level down: each writer records its own tests' results beside its own outputs, and governance only reads.

What replaced it, from phase 002:

- **Per product, not per domain.** Every product is built by its own builder `dbt_<fence>`
  ([ADR-062](ADR-062-dbt-builds-silver-and-gold-per-product-through-one-wrapper.md)), and the compiled
  project's on-run-end hook, `k2_results` (`dbt/macros/k2_results.sql`), writes every data and unit test
  of the invocation, passed, failed, errored or skipped, to `<domain>_build.<fence>__base.dq_test_results`
  in one `INSERT`, with this decision's columns plus `run_id` and `release_id`, which join a result to the
  product's run records. One dbt invocation builds a whole product, so the per-node gap of cosmos is gone:
  a test that ran is recorded; a build Airflow never started still records nothing.
- **A product, built like any other.** `governance.quality-results` (`contracts/governance/quality-results/`)
  is a dbt table, `test_results`, hourly at minute 50, whose union of every product's results table is
  compiled by k2gen from the release's products (`k2_test_results`, `platform/k2gen/k2gen/emit/dbt.py`),
  so a new product is in it from its first build with no change here. Readers read it through its view
  over the tested tag, like any output (ADR-061).
- **Not in it yet:** the bronze audits' results, `lake.<fence>.audit_checks`, written by Spark.
- **Self-attested.** The builder owns its whole base namespace, results table included, so any
  query it runs could insert or delete result rows; the union's `writer` column is a value the writer sets. The
  results are the builder's own word until the wrapper writes them from `run_results.json` as a platform
  principal, which is deferred (ADR-062, Consequences).

`scripts/verify/dbt-wrapper.sh` plants a failing test and reads its row back, and checks that every run
that ran dbt wrote its rows (2026-11-27).
