# ADR-062: dbt builds each product's silver and gold, through one wrapper that publishes only tested data

**Status:** Accepted
**Date:** 2026-11-27
**Author:** Platform
**Category:** Transformation / Data quality
**Decided by:** the owner, with [ADR-056](ADR-056-unified-data-product-platform.md) parts 2 and 4 on
2026-11-26 (silver to marts in dbt-core on Trino, readers held to tested data); the mechanism as built in
[plan 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md), the number held
for it by [`phase-001-interfaces.md`](../development/phase-001-interfaces.md).
**Supersedes:** the boundary table of [ADR-035](ADR-035-trino-and-dbt-for-marts.md), its status line only.
**Evidence:** [`scripts/verify/dbt-wrapper.sh`](../../scripts/verify/dbt-wrapper.sh) on a throwaway lake
(21 PASS, 0 FAIL on 2026-11-27); experiments (d), (f) and (g) of
[`2026-11-26-release-spine-spikes.md`](../research/2026-11-26-release-spine-spikes.md);
`docker/dbt-runner/test_k2_dbt.py`.

---

## In plain words

Every product's cleaned (silver) and business-grain (gold) tables are now SQL models in the product's
own documents, built by dbt on Trino, one product at a time. One small program, the wrapper, runs each
build: it pins the exact tested version of every input, runs the models and their tests in one go,
undoes every table whose tests failed, and only after all tests pass moves the marker readers follow.
Readers therefore never see a row that has not passed its tests. What it does not guarantee: the test
results are recorded by the same identity that built the tables, so they are the builder's own word
until the wrapper records them itself. Nothing is left for the owner to decide.

---

## Context

### dbt, for a reader new to it

**dbt** (dbt-core 1.12.7 with the dbt-trino 1.12.0 adapter, `dbt/pyproject.toml`) turns `SELECT`
statements into tables. Its vocabulary:

- A **model** is one `.sql` file holding one `SELECT`. dbt **materialises** it: as a table (drop and
  create), **incremental** (merge only new or changed rows into the existing table), or a view.
- A **project** is a directory of models, tests and settings (`dbt_project.yml`); a **profile**
  (`profiles.yml`) says which database to connect to and as whom; the **adapter** is the plug-in that
  speaks that database's SQL, here Trino's.
- Models name their inputs through **Jinja**, a template language dbt renders before running the SQL:
  `{{ source('x', 't') }}` is a table dbt did not build, `{{ ref('m') }}` another model. dbt runs Jinja
  in a **sandbox** that refuses access to Python internals. **Vars** are values passed on the command
  line (`--vars '{...}'`) that Jinja can read.
- A **data test** is a query that returns the rows breaking a rule (a key that is not unique, a value out
  of range); no rows is a pass. A **unit test** gives a model fixed input rows and the output it must
  produce, with no real data: it tests the logic, not the table. A **model contract** makes dbt refuse
  to build a table whose columns or types differ from those declared.
- **`dbt build`** runs, in dependency order, every model, seed (a small CSV loaded as a table) and test
  that a **selector** (a named set of nodes, `selectors.yml`) picks; a model's tests run right after it,
  and a failed test skips everything downstream of it. At the end it writes **`run_results.json`**, one
  entry per node with its status, and runs any **on-run-end hook**, SQL the project asks dbt to run
  after the nodes. Each invocation has an **invocation id**.

### What the platform needs from it

- **Products, not domains.** ADR-056 makes every dataset a product with its own documents and pipeline.
  [ADR-035](ADR-035-trino-and-dbt-for-marts.md)'s boundary put silver and gold in Spark and only
  consumer marts in dbt, one run per domain; that layout retired with the old pipeline in phase 000.
- **Readers see only tested data.** Trino 484 writes only a table's `main` (its current version), so
  for seconds in every build `main` holds untested rows.
  [ADR-061](ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md) puts readers behind a
  view over the Iceberg tag `audited`, the **tested-data marker**; something must move that tag, only
  after the tests pass, and undo a failed build before the next one merges into it.
- **Inputs at a tested version.** A product that reads another must read the version that passed its
  tests, not `main`, and every model of one build must read the same version.
- **A killed build leaves its query running.** Stopping the dbt container stops the client, not the
  query: Trino keeps running a statement until its client timeout, `query.client.timeout`, default five
  minutes. Measured (`dbt-wrapper.sh`, row Q1, 2026-11-27): an `INSERT` into an output as the builder,
  its client gone after 2 s, still committed about 25 s later, and Trino reported the query failed only
  after about 300 s. A commit like that, landing after a later run's tests and before its publish, would
  put an untested snapshot under the tag.

---

## Decision

**We will build every product's silver and gold with dbt-core on Trino, from models in the product's own
documents, one product per invocation, through one wrapper, `k2-dbt build`, that resolves tested input
snapshots, runs one `dbt build` with unit tests inside it, restores each output of a failed build, and
moves the tested-data marker only after every test passed, because it is the only arrangement measured to
keep untested rows from every reader while each product stays its own unit.** Scope: dbt outputs. Bronze
stays Spark's write-audit-publish (ADR-036); logic SQL cannot express is a registered code transform on
Spark (ADR-056).

### 1. Where a model lives

In the product's directory under `contracts/<domain>/<product>/`: `models/<table>.sql` per output, its
ODCS contract (the columns of record), `unit_tests.yml`, any `seeds/`. `k2gen release` compiles every
dbt product of a release into one dbt project, `compiled/dbt/` (`platform/k2gen/k2gen/emit/dbt.py`): each
model byte for byte, a model contract and generic tests from its ODCS contract, one selector per product,
and the platform's macros. The project holds no credential and no `env_var`. A model reads an input only
through `{{ k2_input('<producer fence>', '<table>') }}` (`dbt/macros/k2_input.sql`), which renders
`source() FOR VERSION AS OF BIGINT '<id>'` from the var `k2_inputs`, a plain `source()` inside a unit
test, and a compiler error when the var is set without that input. `k2gen check`'s SQL lint refuses a
read outside the declared inputs, `env_var`, time travel in model SQL, and any hook or statement that
writes a table other than `{{ this }}`, the model's own.

### 2. Two identities per product

The **builder** `dbt_<fence>` owns only the product's base namespace `<domain>_build.<fence>__base` and
reads its declared inputs; dbt runs as it and holds only its password. The **publisher** `pub_<fence>`
owns the reader views. Only the wrapper is meant to move the tag, after a passing build, in one commit
to Lakekeeper's REST API that requires `main` and the tag to be where the tests read them. That API has
no authentication, so Lakekeeper does not enforce who moves it: any member of network `k2-catalog`
can. What protects the tag is that network fence ([ADR-059](ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)
part 7) and the commit's compare-and-set requirements, and the wrapper's drift check names a tag no
publish recorded at that product's next build. The wrapper also logs in as `svc.runs` to read and
write **run records** (`governance.run_records.run_record`, one row per output per event, written by
`platform/runs/k2runs.py`, which the release carries).

### 3. The wrapper, in order

`docker/dbt-runner/k2_dbt.py`, run by Airflow as a one-shot container with exactly seven environment
names, three password files and the CA (`docker/airflow/dags/oneshot.py`):

1. **Stop orphans, then reconcile.** List the builder's queries still running (Trino's
   `GET /v1/query`, as the builder, which sees only its own) and cancel each (`DELETE /v1/query/<id>`);
   refuse to go on if any outlives 60 s (`ORPHAN_WAIT`). Only then read state: an output whose `main`
   is not its tag (an earlier run died) is restored to the tag; a tag ahead of the run records gets the
   missing `audited` row; each view is put back to the tagged columns; the catalog drift check must find
   nothing in this product's namespaces.
2. **Resolve inputs.** Read every input's tested snapshot from its `t$refs` as `svc.runs`, the whole set
   twice; a publish between the two reads restarts it, three times at most. A required input never
   tested exits 4.
3. **Record `started`** per output, with its `main` as `snapshot_before`.
4. **One `dbt build --selector <product>`**, `--vars` carrying `k2_inputs`, the run id and the release,
   as the builder, unit tests inside it (experiment (d): a separate unit-test invocation first fails on a
   product's first run, because only `dbt build` creates the schema a unit test needs). dbt's target and
   logs go to the container's `/tmp`, its only writable path. Any non-zero exit is a failed build.
5. **On a failure**, every output whose `main` moved goes back to `snapshot_before`, as the builder:
   `rollback_to_snapshot` when the columns are unchanged, else `CREATE OR REPLACE TABLE ... AS SELECT *
   FROM ... FOR VERSION AS OF <id>`, restating the contract's partitioning and sort (a rollback restores
   rows but not schema, experiment (f); a replace without the partitioning makes the table
   unpartitioned on Trino 484). Each gets a `rolled_back` row; the failed snapshots are handed to the
   Spark `expire` task by id.
6. **On a pass**, as the publisher: each moved `main` must be this dbt invocation's own commit, else
   refuse (part 4). Then narrow each view whose columns change, one compare-and-set commit of the tag for
   all outputs, widen the views, and record `audited`. A stale expectation (409) moves nothing and leaves
   views narrowed; the next reconcile finishes the job.

Exits: 0 published; 1 tests failed, outputs restored; 2 build error, outputs restored; 3 a restore, the
commit, a view sync, the reconcile or the drift check failed (pages); 4 a required input never tested.

### 4. The orphan guard has two ends

Cancel-first (step 1) stops an earlier run's query before this run reads anything. The tie (step 6)
catches one that slips through: Trino's Iceberg connector writes the committing query's id into each
snapshot's summary (`trino_query_id`), and dbt reports each node's query id in `run_results.json`; the
snapshot under the tag must carry one of those ids. Where either side lacks an id, the commit time must
fall inside the dbt invocation, a weaker check the log names.

### 5. Test results

The compiled project's on-run-end hook, `k2_results` (`dbt/macros/k2_results.sql`), writes every data
and unit test of the invocation, passed, failed, errored or skipped, to
`<domain>_build.<fence>__base.dq_test_results`, as the builder, keyed by run id, invocation id and
release. `governance.quality-results` unions every product's table, its union compiled by k2gen from
the release's products.

```mermaid
sequenceDiagram
  participant W as k2-dbt (wrapper)
  participant T as Trino
  participant L as Lakekeeper
  W->>T: as builder: cancel my running queries, wait until none
  W->>T: reconcile: main back to the tag; views to the tagged columns
  W->>T: as svc.runs: each input's tested snapshot, read twice
  W->>T: record started
  W->>T: as builder: one dbt build, k2_inputs pinned, unit tests inside
  alt any test or model failed
    W->>T: restore each moved output; record rolled_back
  else every test passed
    W->>T: main is this invocation's commit (query id)
    W->>T: as publisher: narrow views
    W->>L: one compare-and-set commit moves audited
    W->>T: widen views; record audited
  end
```

*Reading guide.* Top to bottom is one run. Readers follow the tag, so nothing a reader sees changes
before the commit to Lakekeeper.

---

## Rationale

Measured on a throwaway lake by `scripts/verify/dbt-wrapper.sh` on 2026-11-27 (21 PASS, 0 FAIL), with
the wrapper in its own image, run as Airflow runs it:

- A reader polling the view every 50 ms through a build whose test failed never saw one of its rows, and
  `main` went back to the tag.
- A tag moved by another publisher just before the commit gave a real 409 from Lakekeeper: exit 3, the
  view never widened.
- An added and then a retyped column narrowed the view, moved the tag and widened it, every poll
  answered; a failed build after a schema change was restored by create-or-replace with the tag's rows,
  schema and partitioning.
- An orphaned `INSERT` (row Q1, above) was cancelled by the next run's first step, and no snapshot of it
  existed after the run; every publish matched its tested snapshot's `trino_query_id` to one dbt
  reported, none falling back to the time window.
- 200 polls of each of three ways to read the tag (the base by tag name, by snapshot id, and the reader
  view), across four rounds of dbt's create-or-replace that dropped and retyped columns on `main`,
  always returned the tagged snapshot's columns (row Q2a).
- dbt's Jinja sandbox refused four escapes to the operating system at compile, each an error and never
  the command's output (row Q3). That sandbox is what stands between a product's model code and the
  publisher's and `svc.runs`'s password files in the same container.

Per product, because a product's fault then stops only its own pipeline, the failure ADR-056 was written
to end, and its builder can be denied every other product's base.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| Spark for silver and gold (ADR-035's boundary) | Spark bypasses Trino's rules and holds storage keys, so a model would run beside two pairs; set-based models gain nothing from it; Spark's one-slot pool would serialise every product's build behind landings |
| One shared dbt project run as one job for every product | One failing model would hold every product; one identity would write every base; the tag of one product could not move without all. The compiled project is shared, but each invocation selects one product |
| SQLMesh (0.236.4, Apache 2.0) | A second transform framework beside dbt for what one wrapper does; its incremental model is by time range, not by Iceberg snapshot; the research of 2026-11-26 ranked it "not now" ([`entry-C.md`](../research/2026-11-26-unified-platform-architecture/entry-C.md), [`entry-B.md`](../research/2026-11-26-unified-platform-architecture/entry-B.md)) |
| dbt Fusion | Never: no Trino adapter was found, it is newer than two years, and it is distributed under a proprietary licence beside an Apache subset ([`REVIEW.md`](../research/2026-11-26-unified-platform-architecture/REVIEW.md) of 2026-11-26, row 19 and the Fusion row); dbt-core 1.x stays Apache 2.0 |
| Unit tests in a first invocation, then the build | Fails on a product's first run (experiment (d)) |
| Rollback only by `rollback_to_snapshot` | Restores rows, not schema, after a column change (experiment (f)) |

---

## Consequences

**Easier:** a new product's transformation is SQL files, a contract and tests in its own directory; a
failed test is undone before any reader could see it; every run leaves records that say which snapshots
it read and wrote, and the next run can finish what a dead one left.

**Harder:** one container start, one dbt parse of the whole compiled project and two Trino identities per
build; a restore after a column change rewrites the table; a product cannot be built by a plain `dbt
build` and published by hand.

**Committed to:** dbt-core on Trino, never Fusion; models only in product documents; `k2_input` for every
input; one invocation per product; the wrapper as the only thing that moves `audited`.

**What is deliberately not guaranteed:**

- **Quality results are self-attested.** The builder writes `dq_test_results`, and it owns its whole base
  namespace, so any query it runs could insert or delete result rows. Readers of
  `governance.quality-results` and the catalog site's test counts read what the builder recorded. The fix
  is the wrapper writing the results from `run_results.json` as a platform principal, with the builder
  denied the table; it is deferred.
- **Hooks may write only `{{ this }}`, by lint, not by Trino.** The builder owns every table of its base
  namespace, so Trino would let a hook write any of them; `k2gen check`'s token-level lint is what holds
  a hook or statement to the model's own table (a downstream model's hook runs after an upstream model's
  tests, so a write to the upstream table would be untested). A write by a query no dbt node reported is
  also refused at publish by the query-id tie.
- **Run records do not name the bronze snapshot** a silver model read through its own bronze `source()`,
  only the inputs `k2_input` pinned.
- **CI compiles with `--previous none`**, so a breaking contract change without a major version bump is
  refused only at `make release`, against the previous release.

**Revisit when:** Trino and dbt-trino can write an Iceberg branch, so a build can stay off `main` and the
tag dance shrinks to a fast-forward; the wrapper records test results as a platform principal (then the
first item above closes); a product's build is measured to take longer than its schedule interval; a
product's logic cannot be written as one `SELECT` per table, which makes it a registered code transform.

---

## References

- Code: [`docker/dbt-runner/k2_dbt.py`](../../docker/dbt-runner/k2_dbt.py),
  [`dbt/macros/k2_input.sql`](../../dbt/macros/k2_input.sql),
  [`dbt/macros/k2_results.sql`](../../dbt/macros/k2_results.sql),
  [`platform/k2gen/k2gen/emit/dbt.py`](../../platform/k2gen/k2gen/emit/dbt.py),
  [`platform/k2gen/k2gen/checks/sql.py`](../../platform/k2gen/k2gen/checks/sql.py),
  [`platform/runs/k2runs.py`](../../platform/runs/k2runs.py).
- The frozen contract: [`phase-001-interfaces.md`](../development/phase-001-interfaces.md), "The dbt wrapper".
- [ADR-035](ADR-035-trino-and-dbt-for-marts.md) (the boundary this supersedes),
  [ADR-056](ADR-056-unified-data-product-platform.md) parts 2 and 4,
  [ADR-061](ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md) (the view and the tag).
- The dbt labs: [`docs/development/dbt-labs/`](../development/dbt-labs/).

---

## Outcome (2026-11-27, the tie of part 4 knows every statement of the build)

Part 4's tie, by the query ids dbt reports alone, refused a clean build on the live stack on 2026-11-27:
`governance/quality-results` built its table with no rows. dbt-trino 1.12.0 builds a table under an
enforced contract in two statements, `CREATE OR REPLACE TABLE`, which commits an empty snapshot, then
`INSERT`, and `run_results.json` names only the `INSERT`. An insert of no rows commits nothing, so a table
that builds empty has the `CREATE`'s snapshot as `main`, committed by a query dbt never reported.

So each run of the wrapper now gives dbt a fresh Trino client tag, `k2-dbt-<128-bit nonce>`, made after
cancel-first; `dbt/profiles.yml` puts it on every statement of the invocation (`client_tags`, from
`K2_DBT_CLIENT_TAG`). Before it publishes, the wrapper lists the builder's queries through Trino's REST
API (`GET /v1/query`, which it already reads for cancel-first) and keeps those that carry this run's tag.
A moved `main` must have been committed by one of those or by a query dbt reported. Still refused: an
earlier run's leftover query, a retry of the same run id included, since it carries an earlier tag, and
any write that carries no tag of this run and that dbt did not report. Where the snapshot names no query
id, the commit time decides, as part 4 says.

**Its limit**, as `docker/dbt-runner/k2_dbt.py` states it: "Trino lists a finished query for
query.min-expire-age (15 min) and after that only while it lists fewer than query.max-history (100), so a
build whose untagged-by-dbt commit is older than that at publish is refused, never passed; raise
min-expire-age (trino_etc.py) when a build is measured near 15 min." The failure is a refused build, never
an untested publish.

**Provenance:** `scripts/verify/run-path.sh` claim 9 builds every dbt product of the release through the
wrapper on a throwaway lake, `currency-master`'s bronze empty there so a table of no rows is built
(`9 PASS, 0 FAIL in 263 s`, 2026-11-27); `docker/dbt-runner/test_k2_dbt.py` pins the empty table
(`test_an_empty_table_published_by_its_create_which_dbt_never_reports_is_its_own_commit`) and the earlier
run's orphan (`test_each_run_has_its_own_client_tag_so_an_earlier_runs_orphan_is_refused`).

**Revisit when**, besides the triggers above: a build is measured near 15 minutes from its first
statement to its publish.
