# Lab 2. An incremental merge with a unit test

**Goal.** Understand how `silver_rates` updates itself without rebuilding from scratch, why its
key holds the partition column, and how its unit tests pin that logic. Then write one.

**You will learn**

- what an **incremental** model and the **merge** strategy are;
- why a merge's unique key must hold the column the table is partitioned on;
- how `is_incremental()` changes the SQL dbt sends, seen in both compiled forms;
- what a dbt **unit test** is, and how the ones in `unit_tests.yml` are laid out;
- what the platform checks about a unit test before dbt ever runs it.

You need lab 1's `build/ci` release and `build/lab` tree, with its `lab_is_incremental.sql` macro.

## Steps

### 1. Incremental, merge, unique key

A model is **materialized** as something: `table` means dbt rebuilds the whole table on every run
(gold does this: `{{ config(materialized='table') }}`). `silver_rates` says

```sql
{{ config(materialized='incremental', incremental_strategy='merge') }}
```

**Incremental**: the first run builds the table from the full `SELECT`; every later run computes
only new or changed rows and folds them into the table that is already there. **Merge** is how it
folds them: one SQL `MERGE` statement that, for each incoming row, updates the existing row with
the same **unique key** or inserts it when there is none. The key is not in the model: k2gen sets
`unique_key` from the contract's primary key, `(rate_date, currency)`, as you saw in
`_models.yml` in lab 1. The model may not set its own placement, contract or predicates
(`ALLOWED_CONFIG` in [`checks/sql.py`](../../../platform/k2gen/k2gen/checks/sql.py)).

Why bother for a table this size: the ECB file repeats the last 90 days, so each landed file adds
about 90 days of rows to bronze that silver already holds. The merge keeps one row per day and
currency however many files repeat it.

### 2. Why the key holds the partition column

Silver is partitioned by `year(rate_date)`: its data files are grouped by year, so a query that
names a year reads only that year's files. A merge can be told to compare only some partitions,
with dbt's `incremental_predicates` setting. The platform's spike measured what that does
(experiment (a) of [the release spine spikes](../../research/2026-11-26-release-spine-spikes.md)):
dbt-trino puts the predicate into the `MERGE`'s `ON` clause, so a late row outside the window
matches nothing and is **inserted a second time with the same key**, and dbt reports success.
Putting the partition column in the unique key instead gave the same pruning, by Trino's dynamic
filter, and stayed correct.

So the platform has two rules, each a refusal you can find in k2gen:

- `sql.incremental-predicates`: a model may not set `incremental_predicates`
  ([`checks/sql.py`](../../../platform/k2gen/k2gen/checks/sql.py));
- `docs.incremental-key-misses-partition`: an incremental table's key holds every partition
  source column ([`checks/docs.py`](../../../platform/k2gen/k2gen/checks/docs.py)).
  `rate_date` is the source of `year(rate_date)`, and it is in the key.

What the spike measured and what it did not: it partitioned by an identity column (`event_date`),
and its own "Not tested" line lists partition transforms such as `year()`. The correctness half
holds for silver regardless: a row's `rate_date` never changes, so a key that holds it can never
match a row in another partition, and the merge cannot insert a key twice. Whether Trino prunes the
`year(rate_date)` partitions during this merge has not been measured.

### 3. How `is_incremental()` changes the SQL

The model has one block that renders only on a later run:

```sql
  {% if is_incremental() %}
  where f.file_as_of >= (select coalesce(max(rate_date), date '1970-01-01') from {{ this }})
  {% endif %}
```

`is_incremental()` is true when the table already exists and the run is not a full refresh;
`{{ this }}` is the model's own table. Compile the product a second time with the variable your
lab macro reads set to true, and compare the two renderings:

```bash
export K2_TRINO_HOST=127.0.0.1 K2_TRINO_INTERNAL_PORT=18089 DBT_ENV_SECRET_K2_TRINO_PASSWORD=nothing-listens K2_TRINO_CA=/dev/null
L=$PWD/build/lab
uv run --project dbt --locked -q dbt --no-use-colors compile --project-dir $L/compiled/dbt --profiles-dir dbt \
  --target-path $L/target-true --log-path $L/logs --no-partial-parse --selector ecb_fx_rates \
  --vars '{"lab_incremental": true}'
M=compiled/k2/models/fx_reference/ecb_fx_rates/ecb_fx_rates__silver_rates.sql
diff $L/target-false/$M $L/target-true/$M
```

```text
28a29,32
>   -- Only files at least as new as the newest fixing already merged: the newest file repeats the
>   -- last 90 days, so it brings every change an older file could, and older files add nothing.
>   where f.file_as_of >= (select coalesce(max(rate_date), date '1970-01-01') from "fx_reference_build"."ecb_fx_rates__base"."silver_rates")
>   
```

That is the only difference. The first run reads all of bronze; a later run keeps only files whose
newest fixing is at least as new as the newest day silver holds, since the newest file repeats the
last 90 days. k2gen's own proof compiles every incremental model both ways and fails if the two are
equal (`test_dbt_parses_compiles_and_selects_the_tree` in
[`tests/test_emit_dbt.py`](../../../platform/k2gen/tests/test_emit_dbt.py)), run by:

```bash
make test-dbt-tree
```

```text
29 passed
```

(pytest's last line also gives the time.) What dbt does with the incremental `SELECT` at build
time, the `MERGE ... USING ... ON (<key columns equal>) WHEN MATCHED THEN UPDATE ... WHEN NOT
MATCHED THEN INSERT`, needs Trino; its generated form is quoted in experiment (a).

### 4. What a unit test is

A **data test** (lab 1) checks the real table after it is built. A **unit test** checks the
model's logic before the build: dbt runs the model's SQL with each input replaced by rows written
in YAML, and compares the result with the expected rows written beside them. It proves the logic,
never the data, so its rows are invented: `AAA`, `BBB`, round numbers. This repository publishes
no real market data, and a unit test is no exception.

Open `contracts/fx-reference/ecb-fx-rates/unit_tests.yml` and read
`silver_rates_incremental_run_adds_no_duplicate`:

| Key | What it says |
|---|---|
| `model` | the dbt name, `<fence>__<table>`: `ecb_fx_rates__silver_rates` |
| `overrides: macros: is_incremental: true` | run the later-run branch; `false` runs the first-run branch |
| `given: - input: this` | the rows the table already holds, read by `{{ this }}`; only an incremental test has it |
| `given: - input: source('ecb_fx_rates', 'bronze_daily')` | the bronze rows, the rate a string, as bronze stores it |
| `format: sql`, `rows: \|` | each input's rows as a `SELECT` with every column cast to its type. Rows written as YAML mappings would make dbt look the column types up in the warehouse, and bronze, or `this` before the first build, may not exist where the test runs (the comment in the file, and commit `7593ed7`) |
| `expect: rows` | what the model returns: for an incremental test, the rows it hands the `MERGE`, not the merged table |

Read its rows against the model: the table holds 2 February; file `zz` ends 30 January, older than
that, so its row is dropped by the `is_incremental()` filter; `aa` and `bb` pass, and on the day
both hold, `bb`, the newer file, wins. The other four tests in the file pin the first-run branch,
the exact decimal cast (`"1.2300"` becomes `1.23`), the five-day carry in gold, and the summer time
offset.

## Exercise: a third file with a newer rate

Write a test for a newer file that corrects a day silver already holds. In a scratch worktree:

```bash
git worktree add -b lab/silver-unit-test build/lab-tree HEAD
cd build/lab-tree
```

Append to `contracts/fx-reference/ecb-fx-rates/unit_tests.yml`:

```yaml

  - name: silver_rates_a_newer_file_corrects_a_merged_day
    model: ecb_fx_rates__silver_rates
    description: >-
      The table holds up to 3 February. File cc ends on 4 February and gives 3 February another
      rate: cc is newer than bb, so its rate goes to the MERGE; file aa ends before 3 February and
      adds nothing.
    overrides:
      macros:
        is_incremental: true
    given:
      - input: this
        format: sql
        rows: |
          select date '2026-02-03' as rate_date, cast('AAA' as varchar) as currency, cast(2.1 as decimal(20, 10)) as rate, cast('bb' as varchar) as source_file_sha256
      - input: source('ecb_fx_rates', 'bronze_daily')
        format: sql
        rows: |
          select date '2026-02-02' as rate_date, cast('AAA' as varchar) as currency, cast('2.0000' as varchar) as rate, cast('aa' as varchar) as source_file_sha256
          union all select date '2026-02-03', 'AAA', '2.1000', 'bb'
          union all select date '2026-02-03', 'AAA', '2.1500', 'cc'
          union all select date '2026-02-04', 'AAA', '2.2000', 'cc'
    expect:
      rows:
        - {rate_date: "2026-02-03", currency: AAA, rate: 2.15, source_file_sha256: cc}
        - {rate_date: "2026-02-04", currency: AAA, rate: 2.2, source_file_sha256: cc}
```

Work the expected rows out yourself before you trust them: the newest day in `this` is 3 February;
`aa` ends 2 February and is filtered out; `bb` and `cc` both hold 3 February and `cc` ends later,
so its 2.15 wins. On the live platform a published rate that a later file changes fails the bronze
audit `values_unchanged` first ([`docker/lake/connector.py`](../../../docker/lake/connector.py)),
so this case pins the model's rule, not a day you expect to see.

**1. Leave out the branch, once.** Delete the three `overrides` lines and compile:

```bash
bash scripts/verify/ci-validate.sh build/ci-unit-bad
```

```text
error docs.unit-test-branch-unstated contracts/fx-reference/ecb-fx-rates/unit_tests.yml: $.unit_tests[5].overrides.macros.is_incremental: ecb_fx_rates__silver_rates is incremental, so its unit test states which branch it tests, true or false; dbt refuses to parse a unit test of an incremental model without it
```

and nothing is compiled. Put the three lines back.

**2. Compile, and check the version.**

```bash
bash scripts/verify/ci-validate.sh build/ci-unit
uv run --project platform/k2gen --locked -q k2gen check --set build/ci-unit/pr/set --previous ../ci/pr/set
```

```text
error versions.version-reused contracts/fx-reference/ecb-fx-rates/product.odps.yaml: $.version: 1.0.0 again, but a change: unit_tests.yml changed; make it 1.0.1 or higher
```

A product's version covers every file in its directory, tests included, so a new test is a patch
bump. Set `version: 1.0.1` in `product.odps.yaml`, compile into `build/ci-unit-bumped`, and the
same check exits 0.

**3. See dbt find it.**

```bash
make dbt-dev K2_DBT_DEV=$PWD/build/lab
L=$PWD/build/lab
uv run --project dbt --locked -q dbt --no-use-colors ls --project-dir $L/compiled/dbt --profiles-dir dbt \
  --target-path $L/target --log-path $L/logs --no-partial-parse --resource-types unit_test --select ecb_fx_rates__silver_rates
```

```text
Found 6 models, 2 seeds, 1 operation, 31 data tests, 3 sources, 519 macros, 8 unit tests
unit_test:k2.silver_rates_a_newer_file_corrects_a_merged_day
unit_test:k2.silver_rates_casts_trailing_zeros_exactly
unit_test:k2.silver_rates_incremental_run_adds_no_duplicate
unit_test:k2.silver_rates_newest_file_wins
```

Eight unit tests now, four of them on silver. (519 macros, not 520: this fresh tree has no lab
macro, which a unit test does not need, since it overrides `is_incremental` itself.)

**4. See the SQL a unit test runs.** Try it, with the `export` line from lab 1 still set. No
Trino answers, so it errors, but dbt's log keeps the one statement it tried to send:

```bash
uv run --project dbt --locked -q dbt --no-use-colors test --project-dir $L/compiled/dbt --profiles-dir dbt \
  --target-path $L/target --log-path $L/logs --no-partial-parse --select silver_rates_a_newer_file_corrects_a_merged_day
grep -n '__dbt__cte__' $L/logs/dbt.log
```

```text
1 of 1 ERROR ecb_fx_rates__silver_rates::silver_rates_a_newer_file_corrects_a_merged_day  [ERROR in 0.62s]
...
<line>:with  __dbt__cte__silver_rates as (
<line>:),  __dbt__cte__ecb_fx_rates__bronze_daily as (
<line>:  from __dbt__cte__ecb_fx_rates__bronze_daily
<line>:  where f.file_as_of >= (select coalesce(max(rate_date), date '1970-01-01') from __dbt__cte__silver_rates)
```

That is how a unit test works: dbt puts each `given` input in a CTE, `__dbt__cte__<name>`, swaps
it in where the model reads that input (`{{ this }}` became `__dbt__cte__silver_rates`), renders
the branch the override chose (the `is_incremental()` filter is there), and runs the result on the
warehouse to compare with `expect`. The statement it tried first, around those lines, is a
`create table ... __dbt_tmp as (select * from (<the model>) as __dbt_sbq where false limit 0)`:
dbt-trino reads the model's output columns from an empty table. Both need Trino. (The run also
ends with `k2_results: no single database builds market_analytics__base`, from the results hook:
the profile's default user is not this product's builder. The wrapper always runs as
`dbt_<fence>` and passes the `k2_run_id` variable the hook writes on every row.)

**5. Run it: at a merge point.**

```bash
make test-dbt
```

[`scripts/dbt-test.sh`](../../../scripts/dbt-test.sh) builds the product with `dbt build`, unit
tests included, on a throwaway lake, then reads back one results row per test. Its lines, from the
script: a row per test as `<table> run_id=<run id>: <unique_id> | <status> | <failures> | <tested>
| <release id> | <invocation id>`, then `ok results: <n> row(s) in <table> under run_id <run id>,
one per test, statuses as dbt reported`, and at the end `dbt-test: PASS`. Your test's row is the
one whose `unique_id` ends `silver_rates_a_newer_file_corrects_a_merged_day`; its status is
`pass`, or `fail` with dbt's diff of expected against actual rows in the results table's `message`.

**6. Clean up.**

```bash
cd ../..
chmod -R u+w build/lab-tree/build && git worktree remove --force build/lab-tree && git branch -D lab/silver-unit-test
```

## Why it works

The merge is correct by construction: the key is the contract's, k2gen puts it on the model, and
the lint and the docs check keep anyone from narrowing the merge or leaving the partition column
out. The unit tests pin the branch logic on rows small enough to work out by hand, and the platform
refuses a test of an incremental model that does not say which branch it runs.

## Check yourself

1. A colleague adds `incremental_predicates` to make the merge faster. Which check refuses it, and
   what wrong result did the spike show it would cause?
2. Your test drops file `aa` in the `is_incremental()` filter. On a real later run, why does
   dropping a file that ends before the table's newest day lose nothing?
