# Release spine spikes (b), (c), (e): findings, 2026-11-26

Experiments (b), (c) and (e) of "The remaining spikes" in
`docs/plans/2026-11-26-unified-product-platform/000-clear-the-ground.md` (step N0-4). Each ran from
its `run.sh` in this directory on the build host (Docker Desktop for Linux, linuxkit 6.12.54 VM,
28 CPU), in throwaway containers named `spike-n04plat-*`, on synthetic rows, and cleaned up after
itself; every number below is printed by that script. Images: `trinodb/trino:484`; the live Spark
image, read from the `k2-spark-iceberg` container (`k2-spark-iceberg:3.5.9_1.8.1`, Spark 3.5.9 with
Iceberg 1.8.1 since N0-13; the first (b) runs used `3.5.5_1.8.1`, with the same results);
`k2-airflow:3.3.2`; `quay.io/lakekeeper/catalog:v0.13.9`; `minio/minio:RELEASE.2025-09-07T16-13-09Z`;
`alpine:3.20`.

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
