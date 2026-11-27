# Lab 1. A source and a model

**Goal.** Read `silver_rates` end to end: where its rows come from, what dbt is told about its
columns, which tests it gets without anyone writing them, and what the SQL looks like once dbt
has rendered it. Then make a change the wrong way and watch the platform refuse it.

**You will learn**

- what a dbt **source** is, and why `silver_rates` reads its bronze table through one;
- what `k2_input` is, and why another product's table is never read any other way;
- what an enforced **model contract** is, and where its column list comes from;
- which tests k2gen writes from a contract, and how to read them compiled;
- what `k2gen diff` and the version check say about a change.

## The files

| File | What it is |
|---|---|
| `contracts/fx-reference/ecb-fx-rates/product.odps.yaml` | the product: owner, purpose, input ports, output ports, connector, schedule |
| `contracts/fx-reference/ecb-fx-rates/bronze_daily.odcs.yaml` | the bronze table's contract; the connector writes it, not dbt (`build: connector`) |
| `contracts/fx-reference/ecb-fx-rates/silver_rates.odcs.yaml` | the silver table's contract: four columns, key `(rate_date, currency)`, partitioned by `year(rate_date)`, `build: dbt` |
| `contracts/fx-reference/ecb-fx-rates/models/silver_rates.sql` | the model: the one `SELECT` dbt runs to build silver |
| `contracts/fx-reference/ecb-fx-rates/unit_tests.yml` | the unit tests (lab 2) |

Open `models/silver_rates.sql` beside this page. It starts with a comment headed "What this does
and why": every model on the platform carries one, and it is the first thing a reviewer reads.

## Steps

### 1. Where the rows come from: a source

```sql
with bronze as (
  select rate_date, currency, rate, source_file_sha256
  from {{ source('ecb_fx_rates', 'bronze_daily') }}
),
```

Text in `{{ }}` is Jinja, a template language dbt renders before the SQL reaches the warehouse.
`source('ecb_fx_rates', 'bronze_daily')` names a **source**: a table dbt reads but does not
build. Every source is declared in YAML with its catalog and schema, so the model never spells
out where the table lives. Here the table is the product's own bronze, which the connector
writes. The model reads it at `main` because bronze moves only after its audit passed, so its
newest snapshot is always a tested one (`tested_ref` in
[`platform/k2gen/k2gen/model.py`](../../../platform/k2gen/k2gen/model.py)).

A table from **another** product is read differently. The gold model of the same product reads
the TARGET calendar, which the `calendars` product builds:

```sql
  from {{ k2_input('calendars', 'target_days') }}
```

`k2_input` ([`dbt/macros/k2_input.sql`](../../../dbt/macros/k2_input.sql)) is a **macro**, a
Jinja function that returns SQL. It renders the same source, and when the wrapper passes the
snapshot it resolved from that table's `audited` tag, it adds `FOR VERSION AS OF BIGINT '<id>'`,
so the build reads the tested version and not whatever the other product's builder wrote last.
k2gen's lint refuses any other way of reading another product (`sql.foreign-read` in
[`checks/sql.py`](../../../platform/k2gen/k2gen/checks/sql.py)). You will see both renderings in
step 4.

### 2. Compile the release

The validation runner CI runs on every pull request compiles the tree into a trial release:

```bash
bash scripts/verify/ci-validate.sh build/ci
```

It prints one line on stderr and, last, the release directory:

```text
k2gen release: NO PIPELINE market-analytics.hot-gold: it has no schedule, so no DAG runs it
/<your checkout>/build/ci/pr/releases/v0.0.0-ci.1-<id>
```

The `<id>` is a hash of the release's content, so it differs between trees. Keep the path:

```bash
R=$(ls -d build/ci/pr/releases/*)
ls $R/compiled/dbt $R/compiled/dbt/models/fx_reference/ecb_fx_rates
```

```text
build/ci/pr/releases/v0.0.0-ci.1-<id>/compiled/dbt:
dbt_project.yml
macros
models
seeds
selectors.yml

build/ci/pr/releases/v0.0.0-ci.1-<id>/compiled/dbt/models/fx_reference/ecb_fx_rates:
ecb_fx_rates__gold_eur_rates_daily.sql
ecb_fx_rates__silver_rates.sql
_models.yml
_unit_tests.yml
```

`ecb_fx_rates__silver_rates.sql` is your model byte for byte, renamed `<fence>__<table>` so that
names stay unique across the one project. Every other file there is generated, by
[`platform/k2gen/k2gen/emit/dbt.py`](../../../platform/k2gen/k2gen/emit/dbt.py).

### 3. What dbt is told about the model: `_models.yml`

```bash
sed -n '/name: ecb_fx_rates__silver_rates/,$p' $R/compiled/dbt/models/fx_reference/ecb_fx_rates/_models.yml
```

The part to read (trimmed):

```yaml
- name: ecb_fx_rates__silver_rates
  config:
    alias: silver_rates
    properties:
      partitioning: ARRAY['year(rate_date)']
    unique_key:
    - rate_date
    - currency
  columns:
  - name: rate_date
    data_type: date
    constraints:
    - type: not_null
    data_tests:
    - not_null
  ...
  - name: rate
    data_type: decimal(20,10)
  ...
  data_tests:
  - k2_unique_key:
      arguments:
        columns:
        - rate_date
        - currency
```

Every line comes from `silver_rates.odcs.yaml`, nothing from a person:

- **columns and `data_type`**: the contract's columns, name for name and type for type. The
  project sets `+contract: {enforced: true}` for every model (`compiled/dbt/dbt_project.yml`), which
  makes this list a **model contract**: before dbt writes the table it compares the columns the
  `SELECT` returns with this list, and refuses the build on a difference. On this stack that was
  measured for a type mismatch: the build fails before the table changes (item 3 of
  [SPIKE-DBT.md](../../research/2026-11-26-unified-platform-architecture/SPIKE-DBT.md)).
- **`not_null`** on every column the contract marks `required: true`: a **data test**, a query
  that returns the rows that break a rule, and fails when it returns any.
- **`k2_unique_key`**: the contract's two-column primary key as a test, defined in
  `compiled/dbt/macros/k2_tests.sql`. A one-column key would get dbt's own `unique`.
- **`unique_key`** and **`partitioning`**: what the incremental merge uses (lab 2).

Now `$R/compiled/dbt/models/_sources.yml`: the source `ecb_fx_rates` points at catalog `lake`,
schema `ecb_fx_rates`, with `tested_ref: main`; the source `calendars` points at
`reference_data.calendars__base` with `tested_ref: audited`.

### 4. Render the SQL

The release holds the model as you wrote it, Jinja included. To see the SQL dbt would send, run
`dbt compile` on the same project. Compiling needs no warehouse with one exception: dbt's
`is_incremental()` asks the warehouse whether the table exists. k2gen's own proof of the tree
replaces it with a macro that answers from a variable (`PROOF_MACRO` in
[`tests/test_emit_dbt.py`](../../../platform/k2gen/tests/test_emit_dbt.py)), and so do you, in a
writable copy of the tree, never in a product:

```bash
make dbt-dev K2_DBT_DEV=$PWD/build/lab
printf '%s\n' "{% macro is_incremental() %}{{ return(var('lab_incremental', false)) }}{% endmacro %}" \
  > build/lab/compiled/dbt/macros/lab_is_incremental.sql
export K2_TRINO_HOST=127.0.0.1 K2_TRINO_INTERNAL_PORT=18089 DBT_ENV_SECRET_K2_TRINO_PASSWORD=nothing-listens K2_TRINO_CA=/dev/null
L=$PWD/build/lab
uv run --project dbt --locked -q dbt --no-use-colors compile --project-dir $L/compiled/dbt --profiles-dir dbt \
  --target-path $L/target-false --log-path $L/logs --no-partial-parse --selector ecb_fx_rates \
  --vars '{"lab_incremental": false}'
```

`make dbt-dev` writes the same `compiled/dbt/` the release holds. The `export` line is the one
[`scripts/dbt-test.sh`](../../../scripts/dbt-test.sh) uses: values for the profile's names that
reach nothing, since no Trino listens on port 18089. `--selector ecb_fx_rates` is the product's
selector from `selectors.yml`, the one the wrapper builds. dbt prints, among its lines:

```text
Running with dbt=1.12.7
Registered adapter: trino=1.12.0
Found 6 models, 2 seeds, 1 operation, 31 data tests, 3 sources, 520 macros, 7 unit tests
```

The rendered model is under `target-false/compiled/k2/models/fx_reference/ecb_fx_rates/`:

```bash
grep -n '"bronze_daily"' $L/target-false/compiled/k2/models/fx_reference/ecb_fx_rates/ecb_fx_rates__silver_rates.sql
```

```text
13:  from "lake"."ecb_fx_rates"."bronze_daily"
```

The source became a three-part name. Now pin the calendar the way the wrapper does, with the
`k2_inputs` variable, and look at gold:

```bash
uv run --project dbt --locked -q dbt --no-use-colors compile --project-dir $L/compiled/dbt --profiles-dir dbt \
  --target-path $L/target-pinned --log-path $L/logs --no-partial-parse --selector ecb_fx_rates \
  --vars '{"lab_incremental": false, "k2_inputs": {"calendars/target_days": {"snapshot": 42}}}'
grep -n '"target_days"' $L/target-pinned/compiled/k2/models/fx_reference/ecb_fx_rates/ecb_fx_rates__gold_eur_rates_daily.sql
```

```text
15:  from "reference_data"."calendars__base"."target_days" FOR VERSION AS OF BIGINT '42'
```

Without the variable the same line has no `FOR VERSION AS OF`: a developer's unpinned read. The
wrapper always sets it (`dbt_argv` in `k2_dbt.py`).

### 5. The generated tests, compiled

The tests compile beside the models, one file each:

```bash
ls $L/target-false/compiled/k2/models/fx_reference/ecb_fx_rates/_models.yml/ | grep silver
cat $L/target-false/compiled/k2/models/fx_reference/ecb_fx_rates/_models.yml/k2_unique_key_ecb_fx_rates__silver_rates_rate_date__currency.sql
```

```text
k2_unique_key_ecb_fx_rates__silver_rates_rate_date__currency.sql
not_null_ecb_fx_rates__silver_rates_currency.sql
not_null_ecb_fx_rates__silver_rates_rate_date.sql
not_null_ecb_fx_rates__silver_rates_rate.sql
not_null_ecb_fx_rates__silver_rates_source_file_sha256.sql
select "rate_date", "currency", count(*) as row_count
from "fx_reference_build"."ecb_fx_rates__base"."silver_rates"
group by "rate_date", "currency"
having count(*) > 1
```

Each test is a query for bad rows: any row back fails it. Note the relation: tests read the base
table the build just wrote, `main`, before any reader can see it (lab 3).

## Exercise: a column the contract does not carry

You will add a derived column, `rate_year`, to the model only, see what each gate says, then
finish the change. Work in a scratch worktree on a scratch branch, so the change never leaves
your machine:

```bash
git worktree add -b lab/silver-rate-year build/lab-tree HEAD
cd build/lab-tree
```

**1. Change the model only.** In `contracts/fx-reference/ecb-fx-rates/models/silver_rates.sql`,
end the final select like this:

```diff
   cast(rate as decimal(20, 10)) as rate,
-  source_file_sha256
+  source_file_sha256,
+  cast(year(rate_date) as integer) as rate_year
 from ranked
```

**2. Compile it, and diff it against step 2's release.**

```bash
bash scripts/verify/ci-validate.sh build/ci-column
uv run --project platform/k2gen --locked -q k2gen diff ../ci/pr/releases/* build/ci-column/pr/releases/*
```

The compile passes: k2gen does not parse SQL columns. The diff says (trimmed to its tables):

```text
Nothing beyond the pointer swap: every changed file is read through the pointer.

### Files: 0 added, 0 removed, 11 changed

| changed | `compiled/catalog/products/fx-reference.ecb-fx-rates.json` | none |
  ... five more catalog pages ...
| changed | `compiled/dbt/models/fx_reference/ecb_fx_rates/ecb_fx_rates__silver_rates.sql` | none |
| changed | `compiled/trino/rules.json` | none |
| changed | `documents/contracts/fx-reference/ecb-fx-rates/models/silver_rates.sql` | none |
| changed | `documents/provenance.json` | none |
| changed | `provenance.json` | none |
```

Read it as a reviewer. The catalog pages, `rules.json` and the provenance files change in every
release: they carry the release's issue time and its canary login, `k2_release_<hash>` (diff two
`rules.json` files and that is the only line that moves). What matters is the rest: the model's SQL
changed and **`_models.yml` did not**, and neither did the reader view under
`compiled/trino/views/`. A model whose columns moved without its contract is the half-done
change.

**3. What refuses it.** The build. dbt renders five columns where the enforced contract lists
four, and refuses to write the table. That needs Trino, so it is **run at a merge point**:

```bash
make test-dbt
```

[`scripts/dbt-test.sh`](../../../scripts/dbt-test.sh) builds each product's selector on a
throwaway lake and, for a product whose build failed, ends with its line 135:

```text
FAIL dbt build: ecb_fx_rates (its results were checked above)
```

with dbt's own contract message above it. On the platform the wrapper would exit 2 (a build
error): the contract check runs before the table is written, the tag never moves, and no reader
sees anything change (lab 3).

**4. Finish the change: the contract.** In `silver_rates.odcs.yaml`, after the
`source_file_sha256` column:

```yaml
      - name: rate_year
        physicalType: integer
        description: The year of rate_date, the partition the row is in.
```

Compile again, then run the version check against step 2's documents. `k2gen check --previous`
compares two document sets and holds every changed document to a Semantic Version bump
(major, minor or patch: [`checks/versions.py`](../../../platform/k2gen/k2gen/checks/versions.py)):

```bash
bash scripts/verify/ci-validate.sh build/ci-contract
uv run --project platform/k2gen --locked -q k2gen check --set build/ci-contract/pr/set --previous ../ci/pr/set
```

```text
error versions.minor-bump-missing contracts/fx-reference/ecb-fx-rates/product.odps.yaml: $.version: 1.0.0 again, but a minor change: silver_rates.odcs.yaml $.schema[0].properties[4]: column rate_year (integer) added nullable; make it 1.1.0 or higher
error versions.minor-bump-missing contracts/fx-reference/ecb-fx-rates/silver_rates.odcs.yaml: $.version: 1.0.0 again, but a minor change: $.schema[0].properties[4]: column rate_year (integer) added nullable; make it 1.1.0 or higher
```

A column added as nullable is **compatible**: no reader's query breaks, so it is a minor bump. A
column dropped, renamed, retyped, or added as `required` is **breaking**, a major bump. Notice
that `ci-validate.sh` passed: it checks with `--previous none`, as a first release. The version
rule bites at `make release`, which checks against the release deployed now
([`scripts/deploy.py`](../../../scripts/deploy.py), `compile_release`); running the command above
yourself is how a reviewer sees it before then.

**5. Bump the versions.** `version: 1.0.0` to `version: 1.1.0` at the top of both
`silver_rates.odcs.yaml` and `product.odps.yaml`. Then:

```bash
bash scripts/verify/ci-validate.sh build/ci-fixed
uv run --project platform/k2gen --locked -q k2gen check --set build/ci-fixed/pr/set --previous ../ci/pr/set && echo check passed
uv run --project platform/k2gen --locked -q k2gen diff ../ci/pr/releases/* build/ci-fixed/pr/releases/*
```

The check prints nothing and exits 0. The diff now lists 17 changed files, among them
`compiled/dbt/models/fx_reference/ecb_fx_rates/_models.yml`, `compiled/lake/contract_schemas.json`,
`compiled/trino/views.json` and `compiled/trino/views/fx_reference.ecb_fx_rates.silver_rates.sql`,
whose one line now ends its column list with `"rate_year"`. That is the whole change: model,
contract, version, and the reader view following the contract.

**6. Clean up.**

```bash
cd ../..
chmod -R u+w build/lab-tree/build && git worktree remove --force build/lab-tree && git branch -D lab/silver-rate-year
```

## Why it works

The contract is the one place a table's columns are written. k2gen derives dbt's column list, the
tests, the reader view and the version rules from it, so a model cannot drift from its contract
without one of them noticing: the build refuses a model that disagrees with its column list, and
the version check refuses a contract that changed without saying how much.

## Check yourself

1. `silver_rates` reads its bronze through `source()`, and gold reads the calendar through
   `k2_input()`. Why would `source('calendars', 'target_days')` in a model be refused, and what
   would it read if it were not?
2. You make `rate_year` `required: true`. Which bump does the version check now ask for, and why?
