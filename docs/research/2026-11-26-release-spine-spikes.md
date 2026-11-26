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
- **(b)** Spark expires a named snapshot of a Trino-written table and deletes its files; `older_than` must be passed
  with the ids, or the table's maximum snapshot age expires more. The smallest key that works is get plus delete on
  the table's prefix. The catalog signs storage requests for Spark on every warehouse today, so a key with no
  permission at all still deleted: scoped keys mean nothing until remote signing is turned off per warehouse.
- **(c)** On this host a release pointer renamed on the host is never seen inside a container; renamed inside the
  VM, from a container, it is seen in 0.1 to 0.2 s. Trino applied new rules and groups within its 5 s refresh; the
  DAG processor took about 30 s. The layout rule: mount the releases root once, relative links, swap from a
  container with a fresh temporary name, resolve the pointer once per run.
- **(e)** One shared writable catalog fences writer A out of writer B's table (40 of 40 statements denied) only
  while A has no SELECT on it and no CALL grant: SELECT alone lets A run `optimize`, `expire_snapshots` and
  `rollback_to_snapshot` there. A product reads its inputs through the read-only reader catalog; no catalog per
  product is needed.
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

**Question.** Can Spark 3.5 with Iceberg 1.8.1 expire exactly one named snapshot of a table Trino 484
wrote through Lakekeeper, with a non-root storage key scoped to that table? Are its files deleted,
does Trino read the table afterwards, and what is the minimum storage permission set? This is
N1-33's `expire` task after the dbt wrapper rolls a failed build back (A7 step 5).

**What was run.** `spine-b-expire-by-id/run.sh`. A throwaway Trino writes `spike_n04plat_b.t`
(format version 2; Trino adds a statistics file per snapshot): appends S1 and S2, then seven
orphans O0 to O6, each an append rolled back to S2 with `ALTER TABLE ... EXECUTE
rollback_to_snapshot`. Five MinIO users, each with one policy on the table's key prefix, are made
with the root key inside a throwaway `mc` container (keys passed by variable name, never printed).
One Spark session (`local[1]`, 512m driver) holds one catalog per key and runs `CALL
<cat>.system.expire_snapshots(table => 'spike_n04plat_b.t', snapshot_ids => array(<id>L))`. Then
`mc stat` on every file the orphan added and Trino reads the table. The whole run is done twice:
on the live Lakekeeper (warehouse `fx_reference`, which holds no live namespace) as configured,
and on a throwaway Lakekeeper with its own Postgres whose one warehouse, on scratch bucket
`spike-n04plat-b`, sets `remote-signing-enabled: false`. Live phase n = 4 runs, unsigned phase
n = 3 runs (the last of each on Spark 3.5.9), the same outcome every time; the table is the last run.

**Result.**

| Key on the table prefix | Live warehouse (remote signing on) | Warehouse with remote signing off |
|---|---|---|
| none (a policy on another bucket) | ok; snapshot gone; data, manifest, manifest list deleted | `S3Exception: Forbidden` after the metadata commit: snapshot gone, all 3 files left |
| `GetObject` | ok, files deleted | ok with every deleted count 0: snapshot gone, all 3 files left, no error |
| `GetObject`, `DeleteObject` | ok, files deleted | ok (deleted data 1, manifest 1, manifest list 1, statistics 1), nothing left |
| `DeleteObject` | ok, files deleted | `S3Exception: Forbidden` after the commit: snapshot gone, all 3 files left |
| `GetObject`, `PutObject`, `DeleteObject`, `ListBucket` | ok, files deleted | ok, nothing left |

- **Exactly the named snapshot, only with `older_than`.** A by-id call also applies the table's
  maximum snapshot age (`history.expire.max-snapshot-age-ms`, Iceberg default 5 days). With it set
  to 1 ms, expiring O6 by id alone also expired S1 and the table's first snapshot (2 others); with
  `older_than => TIMESTAMP '1970-01-01 00:00:00'` added, O5 went alone (0 others).
- The current snapshot is refused by id: `Cannot expire <S2>. Still referenced by refs: [main]`.
- Trino afterwards: `main` is still S2, `count(*), sum(id)` is still `[4, 10]`, S1's data file is
  kept (S2 reads it), and `FOR VERSION AS OF` an expired id is refused (`INVALID_ARGUMENTS: Iceberg
  snapshot ID does not exists`) for 7 of 7 orphans.
- **The key decides nothing on today's warehouses.** Lakekeeper's LoadTable answer for the live
  warehouse carries `s3.remote-signing-enabled=true` and a signer URI whatever the client sends
  in `X-Iceberg-Access-Delegation` (none, `vended-credentials`, `remote-signing`), so Spark's
  S3FileIO has Lakekeeper sign every request with the warehouse's storage credential, which
  `docker/lake/init-lake.sh` sets to the MinIO root key. The key with no permission at all
  expired and deleted. The scratch warehouse with signing off returns no signing keys, and there
  the key is what MinIO checks.
- No secret in Spark's output (0 of 6 checked values). A dropped live table's files are purged by
  Lakekeeper asynchronously (3 s and 7 s in the last two runs).

**Interface it confirms or changes.**

- N1-33 `expire` (and A7's "Spark `expire_snapshots` by id on the rolled-back ids"): **confirmed**,
  with two rules. Always pass `older_than` with `snapshot_ids` (the epoch, or the product's
  retention floor), or one call can take snapshots the product promised to keep. Check the
  procedure's own answer: `deleted_manifest_lists_count` must be at least the number of ids
  expired, because a key that cannot delete reports success with zeros and an unreadable manifest
  fails only after the snapshot is already gone; either way the files are orphaned.
- The maintenance key of A8 / N1-11 (`K2_MAINT_S3_*`, `K2_SPARK_S3_*`): the minimum for expire is
  `s3:GetObject` and `s3:DeleteObject` on the table prefixes; no `PutObject`, no `ListBucket`.
- **Changed:** "one MinIO key per engine while Lakekeeper vends no credentials" (plan README risk
  row, phase `002`; N1-11) does not hold for Spark as the warehouses are set up. Lakekeeper vends
  no credentials but signs. N1-11 must create every warehouse with `remote-signing-enabled: false`
  in its storage profile (the body in `init-lake.sh`'s `ensure_warehouse`, plus an update of the
  six existing warehouses) for a Spark key to mean anything; until then the storage authority for
  Spark, and for any client that honours the catalog's table config, is the root key behind
  Lakekeeper, whose REST port N1-40 unpublishes.

**Escape hatch.** If remote signing has to stay on, the scoped key is not a control and the
fallback in the plan's risk table applies: Trino `expire_snapshots` as `svc.platform` with the
session retention floor lowered, which (e) shows the `catalog_session_properties` rule can admit
for that principal only. It is weaker than the primary path: it expires every snapshot older than
the threshold, not by id, so it also drops the rollback history the run records point at. Not
tested: Trino's own reads and writes with a scoped key (Trino uses its static key and was given
the root key here); the signer endpoint's checks on the paths it is asked to sign.

## (c)

**Question.** With a releases root holding release directories and pointer symlinks, mounted
read-only as a directory into Trino and an Airflow dag-processor on this Docker Desktop host, does
each container see a pointer swapped with `ln -s` and `mv -T`; how long until Trino applies the new
rules and groups and the dag-processor parses the new DAG set; does anything go stale; what happens
to reads in flight; and which layout rule follows (A1, N1-20, N1-32)?

**What was run.** `spine-c-pointer-swap/run.sh`. A root laid out as A1: `<id>/` (built as
`.<id>.tmp`, sealed `a-w`, renamed) beside `pointers/{trino,airflow} -> ../<id>`, the second
release built after the containers started. A throwaway Trino 484 reads `security.config-file` and
the file group provider's `file.group-file` through `pointers/trino`, each with a 5 s refresh
(N1-21's value); a throwaway Airflow 3.3.2 dag-processor (SQLite metadata DB in the container)
has `DAGS_FOLDER` through `pointers/airflow`, `refresh_interval` 30 s and
`min_file_process_interval` 30 s (the live stack runs Airflow's defaults, 300 s and 30 s). Each
release moves user `u_rules` and group member `u_group` to a different catalog, changes the
description of `common.py`, and carries one DAG file of its own. Three ways to swap, 5 swaps each:
on the host; on the host followed by one `ls /k2/releases/pointers` in a helper container; inside a
helper container that mounts the root read-write. Both containers are polled by path lookups only
(`readlink`, `cat`, `SHOW CATALOGS` as each user, the `dag` table), never a listing of
`pointers/`; median poll period 0.28 s, the resolution of every time below. Then reads in flight,
a pointer mounted directly, an absolute link target, and a mounted directory deleted and recreated.

**Result.** Seconds from the swap, n = 5 per row; "never" is not seen within 120 s.

| Seen in the container | host | host, then one listing | inside the VM |
|---|---|---|---|
| Trino: new link | never (5 of 5) | 0.1 / 0.2 / 0.2 | 0.1 / 0.1 / 0.2 |
| Trino: new rules applied | never | 0.1 / 0.2 / 4.7 | 0.1 / 0.1 / 0.2 |
| Trino: new groups applied | never | 0.1 / 0.2 / 4.7 | 0.1 / 0.1 / 0.2 |
| Airflow: new link | never | 0.1 / 0.1 / 0.2 | 0.1 / 0.1 / 0.2 |
| Airflow: changed DAG file reparsed | never | 29.9 / 30.0 / 30.2 | 29.8 / 29.9 / 30.2 |
| Airflow: new DAG file active | never | 29.9 / 30.0 / 30.2 | 29.8 / 29.8 / 30.2 |
| Airflow: removed DAG file marked stale | never | 29.9 / 30.0 / 30.1 | 29.8 / 29.8 / 29.9 |

Cells are min / median / max.

- **A rename done on the host is not seen through the mount.** Docker Desktop's file sharing
  (`fakeowner` over the host's `/home`) keeps the old entry for a path lookup: in 5 of 5 host swaps
  neither container saw the new link within 120 s, and a hand-run probe still read the old target
  after 600 s (it printed `not visible after 600s`; the command is below). The cache is shared
  by every container on the VM: a fresh container read the old link too, and one directory
  listing of `pointers/` in any container fixed it for all at once.
  The VM side also kept resolving the temporary name the host had renamed away: a later in-VM
  `ln -s ../<id> pointers/.trino.new` failed with `ln: pointers/.trino.new/<id>: Permission
  denied`, linking into the release that stale name still pointed at. A swap made inside the VM
  was seen everywhere at the first poll.
  ```bash
  D=$(mktemp -d ~/spike-n04plat-dbg.XXXX); chmod 755 $D; mkdir $D/r1 $D/r2 $D/pointers
  echo one > $D/r1/marker; echo two > $D/r2/marker; ln -s ../r1 $D/pointers/p; chmod -R a+rX $D
  docker run -d --name spike-n04plat-dbg --network none -m 32m -v $D:/k:ro alpine:3.20 sleep 900
  cd $D && ln -s ../r2 pointers/.p.new && mv -T pointers/.p.new pointers/p; t0=$(date +%s)
  while :; do now=$(( $(date +%s) - t0 )); v=$(docker exec spike-n04plat-dbg cat /k/pointers/p/marker)
    [ "$v" = two ] && { echo "visible after ${now}s"; break; }
    [ $now -ge 600 ] && { echo "not visible after ${now}s"; break; }; sleep 1; done
  docker rm -f spike-n04plat-dbg; rm -rf $D
  ```

- Trino applies a visible swap within its refresh period: 0.1 to 4.7 s (n = 10), the reload
  happening at the first access check once 5 s have passed since the last. Rules and groups
  switched together in 10 of 10 swaps (0.0 s apart at this resolution), but they reload on two
  timers, so a window of up to 5 s with one release's rules and the other's groups is not ruled
  out. No probe error, no flap back to the old release, no `ERROR` in Trino's log.
- The dag-processor picks up a visible swap at its next bundle refresh, 30 s here: the new DAG
  appears, the removed one is marked stale and the changed one is reparsed in the same pass. With
  the live default `refresh_interval` of 300 s a new DAG would wait up to 5 minutes (Airflow's
  `should_skip_refresh`; not measured). The `dag` table stores `fileloc` as the path through the
  pointer (`/k2/releases/pointers/airflow/.../common.py`), never the release id. No import error.
- In flight: a file opened before the swap read the old release to the end (sha256 equal); a DAG
  listing taken before the swap and opened through the pointer after it mixed releases (`common.py`
  from the new release, the old release's own file `ENOENT`); the same names opened through the
  path resolved before the swap (`realpath`) read the old release consistently.
- A pointer mounted directly (`-v <root>/pointers/trino:/pinned:ro`) was resolved at container
  create and stayed on the old release after the swap while the root mount showed the new one. An
  absolute link target (the host path) is a dangling link inside the container (`No such file or
  directory`). A mounted directory deleted and recreated on the host read as missing in the
  container 1, 10 and 30 s later: the recorded incident in `CLAUDE.md`, reproduced.

**Interface it confirms or changes.** A1's layout is **confirmed** (one root, `<id>/` sealed and
renamed into place, relative `pointers/<service> -> ../<id>`, `mv -T`), with this release layout
rule, which N1-20 and N1-32 must follow:

1. Every service mounts the releases root, a directory created once and never removed, renamed or
   replaced (only entries inside it change), read-only at `/k2/releases`. Never mount a pointer or
   a release directory.
2. Pointer targets are relative (`../<id>`).
3. **Changed:** the swap runs on the VM side of the file sharing, not on the host: `make deploy`
   moves the pointers inside a container that mounts the root read-write (a `tools` one-shot),
   with a fresh temporary name per swap (`ln -s ../<id> pointers/.<service>.<unique>` then `mv -T`).
   A host-side swap leaves every container on R-1 indefinitely while the host shows R.
4. `make release-verify` (N1-20) reads each pointer inside each service container and asks Trino
   through the canary user; it is the check that would catch a swap the containers never saw.
5. A reader resolves the pointer once (`realpath` at the start, N1-32's `resolve_release`) and
   uses the resolved release path for the whole run; an open file survives a swap, a path through
   the pointer does not.
6. **Changed (N1-32):** set `AIRFLOW__DAG_PROCESSOR__REFRESH_INTERVAL` (30 s measured here) or a
   new product's DAG takes up to 300 s to appear; the run record takes its release from
   `resolve_release`, since `fileloc` names the pointer, not the release.

**Escape hatch.** Not needed for Trino or Airflow on this host once the swap runs inside the VM.
The plan's escape still works if that ever fails: a pointer mounted directly is resolved at
container create, so recreating a service after a host-side swap pins it to the new release, at a
restart per deploy. Not tested: Prometheus's rule reload through a pointer; the swap on a Linux
host with a native Docker engine (no file sharing in between), where the host-side swap is
expected to be seen at once.

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

**Question.** In one writable Trino catalog shared by two writer identities under file-based
rules, can writer A run `rollback_to_snapshot`, `optimize`, `expire_snapshots`, `DROP`, `INSERT`,
the `system.*` `CALL` procedures or the `$` metadata tables on writer B's table when A owns only
its own schema? Which rule set fences A out of B completely while each keeps full control of its
own, or is one writer catalog per product needed (N1-22, A8, and section D, item 2 of
`IMPLEMENTATION-N0-N1.md`)?

**What was run.** `spine-e-writer-fence/run.sh`. A throwaway Trino 484 through the live Lakekeeper
with three Iceberg catalogs: `spikew` and `a_build` over warehouse `market_capture`, `b_build` over
`fx_reference` (neither holds a live namespace). Rules are rewritten between phases with a 1 s
refresh period. 40 statements: `SELECT`, time travel, the 11 metadata tables (`$snapshots`,
`$files`, `$history`, `$manifests`, `$all_manifests`, `$partitions`, `$refs`, `$properties`,
`$metadata_log_entries`, `$entries`, `$all_entries`), `SHOW CREATE`, `DESCRIBE`, `SHOW STATS`,
`INSERT`, `DELETE`, `UPDATE`, `MERGE`, `ADD COLUMN`, `SET PROPERTIES`, `COMMENT`, `ANALYZE`,
`CREATE TABLE` in the schema, `CREATE VIEW` over the table, `optimize`, `optimize_manifests`,
`expire_snapshots` (7 d, and 0 s with the session floor lowered), `remove_orphan_files`,
`drop_extended_stats`, `rollback_to_snapshot`, `CALL system.rollback_to_snapshot`, `CALL
system.unregister_table`, `TRUNCATE`, `CREATE OR REPLACE`, `RENAME`, `DROP TABLE`, `DROP SCHEMA
CASCADE`. Each runs as A against B's table and against A's own; the platform user reads a
fingerprint of the target (main snapshot, snapshot count, rows, columns, comment) before and after.
Also read from the 484 jars with `javap`: `checkCanExecuteTableProcedure` is an empty method in both
`FileBasedSystemAccessControl` and the connector-level `FileBasedAccessControl`; `OpaAccessControl`
implements it. Five full runs, the same outcome every time; the numbers are the last.

**Result.**

| Phase, rules | A on B's table | A on A's own table |
|---|---|---|
| R1: one shared catalog; A owns schema `ea` (`owner: true`) with every table privilege there, B the same on `eb`; `CALL` for the platform user only; retention session properties denied to writers | **40 of 40 denied**, B's table unchanged | 37 ran; 3 denied by design: the two `CALL`s and the 0 s retention floor |
| R1, a reader with `allow: read-only` on the catalog and `SELECT` on B's table | ran `optimize`, `optimize_manifests`, `expire_snapshots` (7 d), `remove_orphan_files`, `drop_extended_stats`, `rollback_to_snapshot`; 3 changed B's table | |
| R1s: as R1, plus A has `SELECT` on B's table (an input read through the writable catalog) | the same 6 procedures ran, `rollback_to_snapshot` moved B's table; `INSERT`, `DROP`, `$snapshots` denied | |
| R1b: as R1, plus writers may `EXECUTE` the catalog's `system.*` procedures | `CALL system.rollback_to_snapshot` ran and moved B's table with no privilege on it; `unregister_table` denied (it checks drop) | both ran |
| R2: one writer catalog per product (A reaches only `a_build`, B only `b_build`) | 40 of 40 denied through `b_build` (`Cannot access catalog`), 40 of 40 denied on B's table in A's own warehouse through `a_build` | 37 ran, the same 3 denied |
| R1, the platform user with `expire_snapshots_min_retention=0s` in its session | `expire_snapshots(retention_threshold => '0s')` ran and dropped B's old snapshots | |

- Why R1 holds: Trino 484 checks `ALTER TABLE ... EXECUTE` against `SELECT` on the table (A's
  attempts were refused with `Cannot select from table`); the file rules' own table-procedure check
  is a no-op. So `SELECT` on a table in a writable catalog is the right to optimize, expire and
  roll it back, and no privilege at all is a complete fence. The metadata tables follow the same
  rule: schema-wide rules (`table` defaulting to `.*`) give A its own `t$snapshots`, and nothing
  gives it B's.
- `allow: read-only` on a catalog does not stop table procedures; only `allow: none` (A8's rule
  for readers on `_build`) or `iceberg.security=read_only` at the connector (SPIKE-ACCESS) does.
- R4, the escape hatch's cost: with `catalog.management=dynamic` the platform user (catalog rule
  `allow: owner`) created an Iceberg writer catalog with `CREATE CATALOG` in 0.1 s, no restart;
  writer A was denied on it by the same rules file; the stored catalog file kept
  `s3.aws-access-key` and `s3.aws-secret-key` as `${ENV:...}` references, not the secret, and
  the catalog came back after a container restart (`SHOW SCHEMAS` listed 4). The catalog store
  is a writable `etc/catalog`, so it cannot live inside a sealed release.

**Interface it confirms or changes.** N1-22 and A8 are **confirmed** for one `<domain>_build`
catalog shared by the domain's writers, with this exact rule set, which `emit_trino_rules.py`
must generate and `make test-trino` must prove with the matrix above:

1. Each writer `dbt_<fence>` is `owner: true` of its own namespace only, with `SELECT, INSERT,
   DELETE, UPDATE, OWNERSHIP, GRANT_SELECT` on that namespace only (schema-wide, so its own
   metadata tables work).
2. A writer holds **no privilege, not even `SELECT`, on any other product's table in a writable
   catalog**. **Changed:** phase `001`'s "a writer `dbt_<product>` writes only its namespace and
   selects only its input ports" must read its input ports through the reader catalog `<domain>`
   (`iceberg.security=read_only`), never through `_build`; so A7's `k2_inputs` relation names the
   reader catalog, and a `k2gen check` refuses any grant on another product's table in `_build`.
3. No procedure rule gives a writer `EXECUTE`: `system.rollback_to_snapshot` checks nothing on the
   table it names. Writers roll back with `ALTER TABLE ... EXECUTE rollback_to_snapshot`.
4. `catalog_session_properties` denies `expire_snapshots_min_retention` and
   `remove_orphan_files_min_retention` to every writer and reader; only `svc.platform` may set them
   (today's rule denies them to everyone).
5. Readers get `allow: none` on every `_build` catalog (A8 as written; `read-only` is not enough).

**Escape hatch.** Not needed under the rules above. If a writer ever must read another product
through a writable catalog, the fence is one writer catalog per product over a warehouse that
holds only that product (R2): a per-product catalog over a shared warehouse fences only by the
same table rules. R4 shows such a catalog can be added at run time without the restart that
section D, item 2 of `IMPLEMENTATION-N0-N1.md` priced in. The other fence that checks table
procedures per table is OPA access control (checked in the bytecode only, not run). Not tested:
a writer's view that reads another product (no view was made over B's table, as A was refused);
Trino's password authentication, which phase `001` adds.

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
