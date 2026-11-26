# Release spine spikes: what the next phase's design depends on and nobody had run

**Date:** 2026-11-26 (UTC)
**Question:** six cheap experiments that phase `001` (the release and rules spine) builds on, from
"The remaining spikes" of [phase 000](../plans/2026-11-26-unified-product-platform/000-clear-the-ground.md),
row N0-4 and section D of [IMPLEMENTATION-N0-N1](./2026-11-26-unified-platform-architecture/IMPLEMENTATION-N0-N1.md),
plus (g), the mechanism behind the owner's decision O8 in
[DECISIONS-FINAL](./2026-11-26-unified-platform-architecture/DECISIONS-FINAL.md) ("readers see only tested data,
always"). Sections (a), (d), (f) and (g) are dbt and catalog experiments; (b), (c) and (e) are written by the
platform spikes step.
**Status:** findings, not decisions. Each section names the interface in IMPLEMENTATION-N0-N1 section A that it
confirms or changes; the change lands with the step that builds the interface.

## In short

- **(a)** dbt-trino puts `incremental_predicates` into the MERGE's `ON` clause, and Trino turns a predicate on the
  partition column into a partition constraint on the target scan: the MERGE read 300 of 1,000 target rows. It also
  changes the result: a late row outside the window matches nothing and is inserted as a second row with the same
  key, with dbt reporting success. Putting the partition column in the unique key gives the same pruning, by a
  dynamic filter, and stays correct.
- **(b), (c), (e):** written by the platform spikes step.
- **(d)** `k2_input`'s `FOR VERSION AS OF` and a unit test that mocks the same source run in one `dbt build`. A7's
  macro as written does not break it, only because Trino 484 silently ignores a version clause on a CTE; the macro
  in the spike skips the clause in a unit test instead. A7's invocation split is not needed, and its first
  invocation fails on a product's first run. Drop it.
- **(f)** `rollback_to_snapshot` works across a contracted table's create-or-replace (one table, every snapshot id
  kept) and restores the old rows but not the old schema: readers see the old rows through the new columns.
  `CREATE OR REPLACE TABLE t AS SELECT * FROM t FOR VERSION AS OF <id>` restores both in one commit.
- **(g)** An Iceberg tag `audited` is the marker: Trino reads it by name, filters and masks apply, it pins its
  snapshot against expiry, and one REST commit moves it (median 13.0 ms, n=20) with compare-and-set, for all of a
  product's tables at once. Trino cannot create or move it; the catalog's REST API and Spark can. Followed through an
  invoker view it is a convention: a reader who can select the table reads `main` too. Only a definer view that
  carries the policies on itself, over a table no reader can select, makes "always" true.

All four scripts ran on 2026-11-26 against Trino 484 (`trinodb/trino:484`, the compose pin) in a throwaway container
on the stack's network, over Lakekeeper warehouse `reference_data`, with dbt-core 1.12.7 and dbt-trino 1.12.0 from
`dbt/uv.lock`. Synthetic rows only; every scratch namespace (`spike_n04dbt_*`) and its files were dropped. The shared
setup is [`scripts/spikes/2026-11-26/spine-lib.sh`](../../scripts/spikes/2026-11-26/spine-lib.sh). Each `run.sh`
exits 0 when the experiment ran and printed its finding, whatever the finding is.

## (a) dbt-trino `incremental_predicates` on a partitioned Iceberg table

**Question.** Does the predicate reach the MERGE target scan, and does it change correctness when a late row falls
outside the predicate window?

**What was run.** `bash scripts/spikes/2026-11-26/spine-a-incremental-predicates/run.sh`. Three incremental `merge`
models over one source, each an Iceberg table partitioned by `event_date` (`+properties: {partitioning:
"ARRAY['event_date']"}`): `m_plain` (unique key `id`), `m_window` (unique key `id`, predicate
`DBT_INTERNAL_DEST.event_date >= DATE '2026-01-08'`) and `m_partkey` (unique key `(event_date, id)`). Batch 1: 1,000
rows, even ids, ten days, one data file a day. Batch 2: id 14 updated inside the window, id 4 corrected late
(2026-01-03, outside it), id 1001 new. Every file's id range holds 1001, so Trino's dynamic filter on `id`, which
prunes by a file's min and max, skips no file and the counts are partition pruning alone. The rows read come from the
executed MERGE's own statistics (`GET /v1/query/<id>`, the scan operators of the stage that reads the target).

**Result.** The generated MERGE ANDs the predicate into `ON`:

```sql
merge into "spike"."spike_n04dbt_a"."m_window" as DBT_INTERNAL_DEST
using "spike"."spike_n04dbt_a"."m_window__dbt_tmp" as DBT_INTERNAL_SOURCE
on (DBT_INTERNAL_DEST.event_date >= DATE '2026-01-08') and ( DBT_INTERNAL_SOURCE.id = DBT_INTERNAL_DEST.id )
when matched then update set ... when not matched then insert ...
```

| Model | `EXPLAIN` of the target scan | Target rows the executed MERGE read | After batch 2 |
|---|---|---|---|
| `m_plain` | no constraint, `dynamicFilters = {id}` | 1,000 of 1,000 | 1,001 rows, no key twice |
| `m_window` | `constraint on [event_date]`, `event_date >= 2026-01-08` (`EXPLAIN (TYPE IO)`) | 300 | 1,002 rows: **id 4 twice** (old and corrected amounts) |
| `m_partkey` | no static constraint, `dynamicFilters = {event_date, id}` | 300 (the 3 days batch 2 touches) | 1,001 rows, no key twice |

The predicate prunes, and it is a correctness hazard: a row outside the window cannot match, so `WHEN NOT MATCHED`
inserts it. The result is a duplicate key, not a lost update, and dbt reports the MERGE as a success. dbt's `unique`
test on the key counts exactly that duplicate.

**Interface.** Nothing in section A names `incremental_predicates`; A7's merge-based build is confirmed without it.
For the models N1 writes: a merge model's unique key includes the column its table is partitioned on, which is
correct by construction and prunes to the partitions a batch touches, late ones included. That holds when a row's
partition value never changes (an event's date does not).

**Escape hatch.** Where the key cannot carry the partition column: a predicate window longer than the longest
lateness the source allows, and a `unique` test on the key, so a late row past the window fails the build and the
wrapper rolls it back instead of publishing a duplicate.

**Not tested:** partition transforms (`day(ts)`, `month(d)`) instead of an identity partition; the
`delete+insert` strategy, which also applies the predicates; tables of many files per partition.

## (b)

Written by the platform spikes step.

## (c)

Written by the platform spikes step.

## (d) `k2_input`'s `FOR VERSION AS OF` beside a mocked source, in one `dbt build`

**Question.** A model reads an upstream table pinned with `FOR VERSION AS OF <snapshot id>` (id passed by var). In
the same `dbt build`, a unit test mocks that source. Does it compile and run, does the mocked input break on the
version clause, what macro shape works for the pinned, unpinned and unit-test cases, and is A7's invocation split
needed?

**What was run.** `bash scripts/spikes/2026-11-26/spine-d-k2-input/run.sh`. Upstream `events` gets two batches; the
first snapshot (ids 1 and 2) is the pin, the current snapshot adds id 3. Model `silver` selects from
`{{ k2_input('up', 'events') }}`, with a unit test mocking `source('up', 'events')` and two data tests. Five runs:
A7's macro as written ([`naive_k2_input.sql`](../../scripts/spikes/2026-11-26/spine-d-k2-input/naive_k2_input.sql))
with the var set; the spike's macro
([`project/macros/k2_input.sql`](../../scripts/spikes/2026-11-26/spine-d-k2-input/project/macros/k2_input.sql))
with the var set, without it, and with it set but missing the key; and A7's first invocation, `dbt test --select
test_type:unit`, before the model's schema exists.

**Result.**

| Run | Exit | What it showed |
|---|---|---|
| A7's macro, `--vars k2_inputs` | 0, PASS=4 | the unit test ran `from __dbt__cte__up__events FOR VERSION AS OF <id>` and passed: Trino 484 returns 1 row for `WITH c AS (SELECT 1 AS x) SELECT * FROM c FOR VERSION AS OF BIGINT '42'`, and `Iceberg snapshot ID does not exists: 42` for the same clause on a table |
| spike's macro, `--vars k2_inputs` | 0, PASS=4 | model SQL `from "spike"."spike_n04dbt_d_up"."events" FOR VERSION AS OF BIGINT '<id>'`; unit test SQL `from __dbt__cte__up__events`; the model holds 2 rows (the pin), not 3 |
| spike's macro, no vars | 0, PASS=4 | plain `source()`, 3 rows |
| spike's macro, var without the key | 2 | `k2_input: k2_inputs is set but has no entry up/events`, at parse |
| A7's split, first invocation, new product | 1 | `Schema spike_n04dbt_d not found` |

dbt renders a unit test with `model.resource_type == 'unit_test'` and the mocked source as the CTE name
`__dbt__cte__<source>__<table>`; the macro uses the first to skip the clause. Without that, the unit test passes only
because Trino ignores a version clause on a CTE, which is undocumented and may stop. The split's first invocation
fails on a product's first run for a second reason: dbt builds a unit test's scratch table in the model's schema, and
only `dbt build` creates schemas.

**Interface.** A7 changes. Step (4) becomes one `dbt build --selector <fence> --vars '{"k2_inputs": ...}'`, unit tests
included: no `--exclude test_type:unit`, and no first `dbt test` invocation. `k2_input` renders
`source() FOR VERSION AS OF BIGINT '<id>'` (the snapshot through `int`, then typed: an id under 2^31 would be an
integer literal, which Trino refuses as a table version), a plain `source()` when the var is absent or in a unit test,
and a compiler error when the var is set without the key. The var's `relation` field is not needed by the macro,
which names the table through `source()`. N1-14's "three render cases" are runs 2 to 4; the spike's project can be its
test fixture.

**Not tested:** several inputs and products in one selector; a var passed from a file; the parse time of a run whose
var changes every time (not timed).

## (e)

Written by the platform spikes step.

## (f) Rollback across a contracted table's create-or-replace (review finding F12)

**Question.** A dbt model with an enforced contract is rebuilt with a changed schema, which dbt does as a
create-or-replace and a separate insert (SPIKE-DBT 3). Can `rollback_to_snapshot` return to the pre-rebuild snapshot
across that replace? Is the old schema restored, what do readers see, and do snapshot ids survive?

**What was run.** `bash scripts/spikes/2026-11-26/spine-f-rollback-across-replace/run.sh`. Model `contracted`, enforced
contract, `on_table_exists: replace` as in `dbt/dbt_project.yml`. v1 is `(id bigint, name varchar, qty integer)`, 3
rows; v2 is `(id bigint, label varchar, qty bigint, amount decimal(18,8))`, 2 rows. A reader polled `SELECT *`
through the read-only catalog, one query about every 65 ms, while v2 was built. Then `ALTER TABLE ... EXECUTE
rollback_to_snapshot(<v1 snapshot>)` as the writer; the table's metadata is read from Lakekeeper's REST API each time.

**Result.**

```text
reader poll during the v2 build:
  from   0.104 s: 3 rows, columns id, name, qty
  from   4.919 s: 0 rows, columns id, label, qty, amount
  from   5.204 s: 2 rows, columns id, label, qty, amount
commits: v1 create (0 records), v1 insert (3), v2 replace (0), v2 insert (2); one table-uuid throughout
after rollback: current snapshot = v1's; current schema still v2 (1:id long, 4:label string, 3:qty long, 5:amount)
  reader sees: 1,,10,  2,,20,  3,,30   (id, label, qty, amount)
after CREATE OR REPLACE TABLE t AS SELECT * FROM t FOR VERSION AS OF <v1>: one commit, same uuid,
  schema (1:id long, 6:name string, 3:qty int), reader sees 1,alpha,10  2,bravo,20  3,charlie,30
```

- The replace keeps the table: Lakekeeper reports one `table-uuid` throughout, and every snapshot id, v1's included,
  stays in `$snapshots`. `FOR VERSION AS OF <v1>` reads v1's rows in v1's columns.
- A reader saw the table empty, already in v2's columns, for about 0.3 s between the two commits.
- `rollback_to_snapshot` works across the replace and restores v1's rows, but not v1's schema. Iceberg's rollback
  moves the current snapshot only; the current schema stays v2. Readers see v1's data through v2's columns: `name` is
  gone, `label` and `amount` read null, `qty` is widened. Iceberg kept `id` and `qty`'s field ids across the replace
  (it reuses ids by name), which is why those two still read.
- The escape hatch works: a create-or-replace from the table's own v1 snapshot restores schema and rows in one
  commit, same table, history kept. It rewrites the table's data, a cost that grows with the table.

**Interface.** A7 steps (1) reconcile and (5) roll back change for a build that changed a table's columns.
`rollback_to_snapshot(<snapshot_before>)` is right only while the columns are unchanged. The wrapper compares the
column list at `snapshot_before` (a `LIMIT 0` read `FOR VERSION AS OF` it) with the current one. If they differ, it
restores with `CREATE OR REPLACE TABLE <t> AS SELECT * FROM <t> FOR VERSION AS OF <snapshot_before>` as the writer,
then records `rolled_back` as before. The later Spark `expire` of the failed snapshots is unchanged. With the tag of
(g), readers never see the empty table or the half-restored one; the restore matters for the next build, which
merges into `main`.

**Not tested:** a narrowed or reordered type; incremental models with `on_schema_change`; the restore on a large
table (its time).

## (g) An "audited" marker that readers follow and that moves only after tests pass (O8)

**Question.** What mechanism gives interactive SQL readers only tested data through Trino 484 and Lakekeeper? The
candidates are an Iceberg tag or branch `audited`, a view naming a snapshot id, and a writer/reader catalog pair where
publish moves a pointer. For each: can Trino read it, who can create or move it, does a reader view work without a
definer view, how do row filters and masks behave, and what does publish cost?

**What was run.** `bash scripts/spikes/2026-11-26/spine-g-audited-marker/run.sh`. Table `t` gets a tested batch (ids 1
to 3, snapshot `s1`, tagged `audited`) and an untested build with a bad row (id 4, snapshot `s2`). Reader `p_reader`,
on the read-only catalog only, has a row filter (`region <> 'restricted'`, which hides id 3) and a mask (`secret` to
`'****'`) on `t`. Reader `p_strict` has no grant on `t`, only on a definer view carrying the same policies on itself.
The tag, a branch and a two-table tag are set through Lakekeeper's REST API (`set-snapshot-ref` with an
`assert-ref-snapshot-id` requirement). Spark ran in the stack's Spark container in a gap of the lake ingest, at 512 MB,
with no event log. Trino ran once more with `access-control.name=allow-all` to separate the connector from the rules.

**Result: reads.**

| Read, as `p_reader` unless named | Rows |
|---|---|
| `t` (its `main`) | `1:open:**** 2:open:**** 4:open:****`: the untested row |
| `t FOR VERSION AS OF 'audited'` | `1:open:**** 2:open:****` |
| `t FOR VERSION AS OF <s1>` | the same |
| invoker view over the tag, base in the read-only catalog | the same: the base table's filter and mask apply |
| invoker view naming `s1` | the same |
| invoker view whose base is in the writer catalog | `Access Denied: Cannot access catalog spike` |
| `p_strict`: definer view over the tag, base in the writer catalog, policies on the view | `1:open:**** 2:open:****` |
| `p_strict`: `t`, `t$files` | `Access Denied: Cannot select from table ...` |
| a tag never set | `Cannot find snapshot with reference name: never_set` |

**Result: who can make or move it.**

| Actor | Outcome |
|---|---|
| Trino, `ALTER TABLE t CREATE TAG ...` | syntax error: Trino 484 has no tag statement |
| Trino, `CREATE BRANCH ... IN TABLE t` under file rules | `Internal error`: `UnsupportedOperationException` in `FileBasedSystemAccessControl.checkCanCreateBranch` |
| Trino, the same with allow-all | `This connector does not support creating branches`; `FAST FORWARD` and `INSERT INTO t @ <branch>` report the branch missing, and `SHOW BRANCHES` lists none, though the catalog holds one |
| Lakekeeper REST, tag or branch | 200; Trino reads both by name |
| Spark 3.5 with Iceberg 1.8.1, `CREATE OR REPLACE TAG` then `REPLACE TAG` | exit 0, the tag at the second snapshot |

**Result: publish.**

| Step | Measured (from the host, this stack) |
|---|---|
| move the tag, one REST commit with compare-and-set | median 13.0 ms, max 28.6 ms, n=20 |
| replace a view naming a snapshot id, `CREATE OR REPLACE VIEW` as the writer | median 38.0 ms, max 54.8 ms, n=20 |
| a publisher holding a stale expected value | 409 `CatalogCommitConflicts`, the tag unchanged |
| two tables in one `POST /v1/{prefix}/transactions/commit`, one requirement stale | 409, neither table tagged; with both current, 200 and both tagged |
| the next read through the invoker view after a move | showed the new snapshot at once |
| `expire_snapshots` at a 0 s retention | kept every snapshot a ref names; the tag still reads |
| a column added, then the tag moved to it | a `SELECT *` view over the tag: `View ... is stale or in invalid state: stored view column count (4) does not match ... (5)`; a view naming the contract's columns reads on |
| a second catalog entry onto `t`'s metadata file (`POST .../register`) | 409 `Tabular with the same name already exists in the namespace`: Lakekeeper keys a table by its uuid, so two entries cannot hold one table at two points |

**Recommendation: the tag `audited`, read through a definer view that carries the table's policies, over a base
table no reader can select.**

- The wrapper publishes after a passing build, at A7 step (5). It sends one multi-table REST commit that moves
  `audited` on every output of the product, each change requiring the tag's previous audited id. A 409 means
  another writer moved it: exit 3, page. The first passing build creates the tag (the requirement `snapshot-id:
  null`). A failed build moves nothing: readers never see its rows, its empty replace window (f) or its schema. The
  rollback of `main` remains, for the next merge.
- Readers query a view per output table: `SECURITY DEFINER`, owned by the product's writer, selecting the contract's
  columns from the writer catalog's table `FOR VERSION AS OF 'audited'`. The compiler places each policy (filters,
  masks) on the view. No reader rule names the base table, which also denies `t$files`, the leak SPIKE-ACCESS found.

What it costs:

- The F12 rule "the checks library refuses definer views" becomes "a definer view only over a table that no reader
  rule names, carrying every policy of that table". The reason for the rule, policies skipped through the owner's
  rights, no longer holds then. The owner should confirm, since the brief records definer views as forbidden.
- Names change (A5, A8, A9, A11, A12): the published name readers query becomes the view, so the base table needs a
  name or namespace readers never see. PROPOSED: base tables in namespace `<product fence>__base`. The writer keeps
  `GRANT_SELECT` on its tables, which a definer view's owner needs (today's rules already give it).
- A new path: the wrapper (as `svc.runs`) reaches Lakekeeper's REST API directly, because Trino cannot move a tag.
  Lakekeeper has no authentication on this stack, so a tag is as safe as the network fence around the catalog that
  A9's probes test. When the catalog gains authentication, `svc.runs` needs its own credential.
- A view names its columns, so a contract change re-creates the view: a deploy DDL step (A1 `needs.ddl`), not a
  per-run step.
- REST and downstream products change little: A12's `/v1/data` reads the view, which is the tag's snapshot, instead of
  resolving the newest audited id itself, and `k2_input` keeps reading inputs by the snapshot id the run records hold
  (A6, A7).
- A tag pins its snapshot and files against `expire_snapshots`. That is required anyway, and the tag keeps the
  audited snapshot alive even when `main` has moved on.

If the definer rule stands, the same tag read through an invoker view is the fallback. Readers who follow it see only
tested data, with their own filters and masks, but a reader who can select the view's base table can read `main`
directly. Then "always" holds for REST and other products, not for interactive SQL. A view naming a snapshot id is
the weaker variant of either. Its publish runs through Trino with no catalog path (median 38.0 ms), but a product's
tables move one statement at a time, nothing pins the snapshot against expiry, and a stale writer is not detected.
The writer/reader catalog pair has no pointer to move: both Trino catalogs read one Lakekeeper table, and a second
entry for it is refused.

**Not tested:** Lakekeeper with authentication; the multi-table commit's time (the single-table commit is above);
resource groups for a definer view's reads; the `__base` naming end to end; a tag on a table Spark writes with a WAP
branch.
