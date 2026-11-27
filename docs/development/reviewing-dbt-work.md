# Reviewing dbt work

What to read in a pull request that changes a dbt product: a model, its contract and YAML, its
unit tests, and what the validation runner says about it. Each item says why it is there, so you
can tell when it does not apply. The [dbt labs](dbt-labs/README.md) teach the mechanics this page
assumes; the terms are defined in their README.

The platform checks a great deal for you, and the last section says exactly what, so that review
time goes on what no check can judge: whether the logic is right, and whether the tests would
notice if it were not.

## Start with "What this does and why"

Every model file opens with a comment headed `What this does and why`, and every dbt pull request
carries a section of the same name ([the template](../../.github/pull_request_template.md)). Read
it first and hold the code to it. It states, in plain sentences:

- **the grain**: what one row is ("one row per fixing date and currency");
- **the rule**: what the model decides, such as which file wins or how far a rate is carried;
- **the why**: the fact about the source or the reader that makes the rule necessary;
- **the materialization and its reason**: a table rebuilt whole and why that is cheap enough, or an
  incremental merge and what makes it correct.

`models/silver_rates.sql` and `models/gold_eur_rates_daily.sql` under
`contracts/fx-reference/ecb-fx-rates/` are the house examples. A model you cannot explain from its
comment is not ready, however green its checks.

## The model

| Read | Why |
|---|---|
| The grain in the comment matches the contract's `primaryKey` | k2gen turns the key into the uniqueness test (`k2_unique_key`, or dbt's `unique` for one column); a grain the key does not state is a grain nothing tests |
| Another product's table is read only as `k2_input('<fence>', '<table>')`, and only for a port in `product.odps.yaml`'s `inputPorts` | `k2_input` pins the read to the snapshot the wrapper resolved from that table's tested ref; `source()` of another product would read whatever its builder wrote last. The lint refuses it (`sql.foreign-read`); the port is what makes the wrapper pin it, and a `k2_input` with no port is a compiler error when the wrapper runs it |
| The product's own bronze and seeds through `source()` and `ref()`, its own models through `ref()` | the same rule for its own tables: nothing written by hand as `catalog.schema.table` (a warning, `sql.relation-unmanaged`) |
| No `env_var` anywhere | the build's environment holds the builder's password; anything a model renders can land in SQL, a table or a log. The lint refuses it and the compiler refuses a tree that holds it |
| `config(...)` sets only `materialized`, `unique_key`, `incremental_strategy`, `on_schema_change`, `view_security` | where a table lives, its properties, grants and how it is replaced are the compiler's, from the contract; the lint refuses the rest (`sql.config-key`) |
| An incremental model's `is_incremental()` filter keeps every row a late or corrected file can change | the merge only sees what the filter lets through; a filter on the wrong column drops corrections without any test failing. Ask which rows a later run must revisit and check the filter keeps them (`silver_rates` keeps every file at least as new as its newest day) |
| No `incremental_predicates` | it narrows the merge's `ON` clause, and a row outside the window is inserted a second time under the same key ([spike (a)](../research/2026-11-26-release-spine-spikes.md)). Refused by the lint; the key holds the partition column instead (`docs.incremental-key-misses-partition`) |
| Time zones are named, never offsets: `at_timezone(with_timezone(..., 'Europe/Berlin'), 'UTC')` | an hour offset is wrong for half the year. The session runs in UTC (`timezone: UTC` in [`dbt/profiles.yml`](../../dbt/profiles.yml)) and every stored timestamp is `timestamp(6) with time zone`; a conversion is explicit, with a unit test on each side of a clock change (`gold_uses_cest_in_summer`) |
| Money, prices and rates are `decimal(p, s)`, cast from the published string, never `double` | a float rounds: `1.1` is not exactly representable. The cast's precision and scale equal the contract's type (`decimal(20, 10)` in silver) |
| `current_timestamp`, `now()` or random values only where the comment says why | they make two builds of the same inputs differ, so a test cannot pin them; the access canary uses the clock on purpose and says so |
| Window functions and joins state their order and their match | `row_number()` without a full tie-break may pick a different row among ties on each run; `silver_rates` breaks a tie on the sha256 "only so the choice is repeatable" |

## The contract and the YAML

The model's YAML is generated: `compiled/dbt/models/<domain>/<fence>/_models.yml` comes from the
contract, so review the contract (`<table>.odcs.yaml`), never a hand-written `schema.yml`.

| Read | Why |
|---|---|
| Every column the model returns is in the contract, same name, same type, same order of intent | the contract is enforced: dbt refuses a build whose columns differ. A change to the model's `SELECT` with no change to the contract fails at the build, not in review ([lab 1](dbt-labs/lab-1-a-source-and-a-model.md)) |
| `primaryKey` and `primaryKeyPosition` say the grain | the key becomes the uniqueness test and, for an incremental model, the merge's `unique_key` |
| `required: true` where a null would be a bug | each one is a `not_null` test and a constraint; a column left optional is a column nothing checks |
| Every column has a `description` a reader can act on | the catalog page and the dbt docs show it; k2gen refuses an undescribed column (`docs.column-undescribed`) |
| `partitioning` names a column that never changes for a row | the key must hold its source column; a value that moves would leave the old row in its old partition |
| `quality` rules are ones that fail when the logic breaks | each becomes a dbt test. A rule that cannot fail (a row count above zero on a table that is never empty) is noise |
| The version bump matches the change: major for a dropped, renamed, retyped or newly required column, a removed output or a tightened policy; minor for a nullable column or a new output; patch for anything else | readers' queries break on a major change only. Run the check yourself, below: CI's runner does not |
| `grants.yaml` and `policies` changes are read line by line | they decide who reads what. Every new reader outside the owning team needs the product's `terms` recorded first (`docs.terms-missing`) |

## The unit tests

| Read | Why |
|---|---|
| Each rule in the model's comment has a case, and each branch of `is_incremental()` has one | a rule without a case can change silently; k2gen refuses a test of an incremental model that does not say which branch it runs (`docs.unit-test-branch-unstated`) |
| The edges are there: ties, nulls, the first and last day of a window, both sides of a clock change, a value at a limit (the fifth and sixth carried day) | logic breaks at its edges; the middle rarely tells you anything |
| The expected rows can be worked out by hand from the given rows and the description | a test whose expectation was pasted from the model's own output proves only that the model ran |
| For an incremental test, the expected rows are what the model hands the merge | that is what dbt compares; the comment at the top of `unit_tests.yml` says so |
| Every value is synthetic: codes like `AAA`, round numbers, plain dates | the repository publishes no real market data or rates ([README](../../README.md), data terms); a unit test proves logic, so it never needs a real value |

## What the validation runner tells you

CI runs [`scripts/verify/ci-validate.sh`](../../scripts/verify/ci-validate.sh) on every pull
request: `k2gen docset`, `k2gen check` and a trial `k2gen release` of the tree, then `k2gen diff`
against the release of the newest `v3.*` tag, posted as the job's summary.

Read the summary in three parts:

- **What deploying it needs**: services to recreate, a Trino restart, a DAG drain, DDL. A model,
  contract or test change alone should need nothing: "Nothing beyond the pointer swap".
- **Files**: the changed paths. For a dbt change, the lines that matter are the product's
  `compiled/dbt/models/.../_models.yml` (its contract as dbt sees it), its `.sql` and
  `_unit_tests.yml`, `compiled/trino/views/<domain>.<fence>.<table>.sql` (the reader view's
  column list), `compiled/trino/views.json`, `compiled/lake/contract_schemas.json`, and
  `compiled/trino/rules.json` beyond its one canary line. Every release changes the catalog
  pages (their issue time), the canary login in `rules.json` and the provenance files; ignore those.
  The diff runs from the newest tag, so it also lists everything merged since; read the
  product's own lines.
- **Storage plan**: namespaces and landing prefixes added or removed.

A model's SQL changing while its `_models.yml` and its reader view stay the same means the columns
did not move. If the SQL's columns did move, that is the half-done change of lab 1.

**Breaking or compatible** is not in the diff: it is the version rule of `k2gen check`. The runner
checks with `--previous none`, as a first release; the rule bites at `make release`, against the
deployed release. To see it during review, compile the base branch and the pull request and
compare their document sets, from a checkout of the pull request that is up to date with its
base (an older k2gen can fail to read a newer release: `no entry of model.NEEDS_BY_PREFIX
covers it`):

```bash
git worktree add --detach build/base-tree <base branch>
(cd build/base-tree && bash scripts/verify/ci-validate.sh build/ci)
bash scripts/verify/ci-validate.sh build/ci-pr
uv run --project platform/k2gen --locked -q k2gen check --set build/ci-pr/pr/set --previous build/base-tree/build/ci/pr/set
uv run --project platform/k2gen --locked -q k2gen diff build/base-tree/build/ci/pr/releases/* build/ci-pr/pr/releases/*
```

A missing bump prints, for example, `error versions.minor-bump-missing ... column rate_year
(integer) added nullable; make it 1.1.0 or higher`, and the diff is then the pull request's
change alone. Then run the three suites the template names; `make test-dbt` needs a container,
so it runs at a merge point if it cannot run now.

## What the platform guarantees, so you need not check it by eye

The wrapper ([`docker/dbt-runner/k2_dbt.py`](../../docker/dbt-runner/k2_dbt.py),
[lab 3](dbt-labs/lab-3-a-failing-test-that-rolls-back.md)) and the compiler hold these on every
build; each is proved by a test you can name:

- **Readers see only tested snapshots.** Readers read a view over the `audited` tag, which moves
  only after every test of the build passed, in one compare-and-set commit for all outputs
  ([ADR-061](../adr/ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md);
  `docker/dbt-runner/test_k2_dbt.py`; the polling reader of `scripts/verify/dbt-wrapper.sh`).
- **A failed build is undone**: every output whose `main` moved is restored, with a `rolled_back`
  record; a run that died is restored by the next run's reconcile.
- **Inputs are pinned** at their tested snapshots, read twice to rule out a publish in between; a
  required input that was never tested stops the run before dbt starts.
- **The tests run**: unit tests and data tests in the one `dbt build`, and every result is written
  to the builder's `dq_test_results`, pass or fail.
- **The columns are the contract's**: the enforced contract refuses a build whose columns differ,
  and the reader view's column list follows the contract and the tagged snapshot.
- **The builder cannot reach further**: it writes only its own `<fence>__base`, reads only its
  declared inputs, and cannot touch a view, a tag or a grant (Trino's rules, compiled from the
  documents).

What none of this guarantees: that the logic is right, that the grain is the one the reader
needs, that a type is the right type, or that the tests would fail if the logic broke. That is the
review.

## Red flags

- A model whose comment does not say what one row is.
- `source()` of another product, a hand-written `catalog.schema.table`, or `k2_input` for a table
  that is not an input port.
- `env_var`, `run_query`, `adapter.execute`, a `pre_hook` or `post_hook`, or `{% call statement %}`
  in a model: the lint catches most; ask why any is there.
- `double` or `real` for an amount, a price or a rate; a decimal whose scale is narrower than the
  source's.
- An hour added or subtracted for a time zone.
- An incremental filter on a timestamp of arrival (when a row landed) for data that can be
  corrected later.
- A unit test whose expected rows match the model's output to the last digit with no case a person
  would have thought of: ties, nulls, edges.
- A contract change with no version bump, or a breaking change labelled minor.
- A test removed or loosened in the same pull request that changes the logic it tested.
- A real currency's real rate, or any value that looks copied from a published source, in a test
  or a fixture.
