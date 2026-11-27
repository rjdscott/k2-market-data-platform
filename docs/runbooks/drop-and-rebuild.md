# Runbook: Drop a lake namespace, table or bucket, after a verified copy

`scripts/drop.sh` (`make drop`) is the way to delete a lake table, a view, a namespace or a
MinIO bucket on this stack: it copies the target's objects to the host, compares the copy with
MinIO, and only then drops. This page says what it refuses, how to run it, what to do when it
stops, and how to put a dropped table back from its copy. It does not cover the nightly copy of
the irreplaceable tables, which is [lake-backup-and-restore.md](./lake-backup-and-restore.md),
nor rebuilding a product in place, which returns as `make rebuild` in
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
(the v3 lake's rebuild retired with it, archived in `legacy/v3-lake-ingest/`).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Dry run | < 1 min | 5 s for `k2.scratch_p4` (two tables); 13 s for `k2.gold`, the namespace with the most objects; 2026-11-26 |
| 2 | Drop a namespace | < 5 min plus the copy | 12.5 s for a scratch namespace holding a table and a view; the catalog's purge emptied their locations 2 s and 4 s after each drop; 2026-11-26 |
| 3 | Drop a bucket | < 1 min plus the copy | 1.8 s for a versioned scratch bucket, every object version copied; 2026-11-26 |
| 4 | Restore a table from its copy | < 5 min | drilled 2026-11-26 on a scratch table: its synthetic rows read back through Trino; not timed |

---

## What it does, in order

`TARGET=<warehouse>.<namespace>[.<table>]` names a table or a view, or every table and view in
a namespace; `TARGET=bucket:<name>` names a bucket. Each step must hold before the next runs:

1. **Resolve** from the catalog what the target holds: each table's and view's location and
   current metadata file. It reads every table and view in the warehouse to prove that none
   shares the target's location or nests in it.
2. **List** each location's objects in MinIO and check the backup disk has room: the copy must
   leave `K2_BACKUP_MIN_FREE_GIB` (20 by default, the same floor `make backup` keeps) free.
3. **Copy** with `mc mirror` to `$K2_BACKUP_DIR/drop/<UTC date>/<target>/` (one directory per
   table or view for a namespace). A versioned bucket also gets every object version, including
   the noncurrent ones `mc mirror` skips, as `<target>.versions/<key>/<version id>`.
4. **Compare** object count and bytes, MinIO against the copy, then list MinIO again: no key,
   size or etag may have changed since the copy began (the etag is what shows an overwrite of
   the same size; the copy on disk has none). Write `<target>.<HHMMSS>Z.json` beside the copy, the
   manifest a restore reads: each object's location, metadata file, copy, count and bytes.
5. **Drop**: each table and view with `DELETE .../tables/<table>?purgeRequested=true` (or
   `.../views/<view>`), then the namespace; or `mc rb --force` for a bucket. Every warehouse's
   delete profile is `hard`, so the catalog queues a `tabular_purge` task that deletes every
   object under the location; the script waits, up to `K2_DROP_PURGE_WAIT_S` (900 s), until
   none is left.

Every call to the stack runs in one throwaway container, `k2-drop-<pid>`, on the catalog's network
`<project>_k2-catalog` (Lakekeeper has no host port since 2026-11-27; before the cutover to releases,
MinIO's only network), from the image `make backup` pins: curl for the catalog, mc for MinIO. The MinIO credential is in
that container's environment only.

**It refuses**, before anything is copied: an empty or malformed `TARGET`; a whole warehouse
(`TARGET=k2`); a bucket that holds a warehouse (`bucket:k2-lake`); a location outside its
warehouse's prefix, or shared with another table or view; a table whose current metadata file is
not in storage; a lake object in a versioned bucket; a namespace with nested namespaces; and a
`K2_BACKUP_DIR` inside the checkout or inside MinIO's storage.

**What it does not do**

- **Stop the writers.** A write during the copy stops the run at step 4, but a write between
  that check and the drop is lost. Stop what writes to the target first. Until phase 002 no DAG
  writes the lake; check that nothing runs, as the [operator guide](../operations/README.md)
  section 6 step 1 does.
- **Keep the target gone.** Nothing in the stack creates a lake table or namespace at start
  since the v3 lake retired: its `lake-ddl` one-shot is gone, and `lake-init` creates only the
  bucket and the warehouses (`docker/lake/init-lake.sh`, the list in the storage plan of the
  release it reads, `compiled/lake/storage-plan.json`). A dropped warehouse would come back on the
  next run of `lake-init`;
  `make drop` refuses one anyway.
- **Prune the copies.** Nothing deletes `$K2_BACKUP_DIR/drop/`. Delete a day's directory by hand
  once nothing needs it.

---

## 1. Drop a target

`K2_BACKUP_DIR` is read as `make backup` reads it, the environment, then `.env`, then
`~/k2-backup`. It must be in a directory Docker Desktop shares with its VM, your home directory
by default: `/tmp` is not, and the run stops at the helper container's start.

```bash
# What would be copied and dropped, object by object. Read-only; writes nothing.   ✅ verified
# The output is from 2026-11-26, for k2.scratch_p4, dropped since: name a namespace that exists.
make drop TARGET=k2.scratch_p4 DRY_RUN=1
#   drop: k2.scratch_p4.wap_bronze: s3://k2-lake/warehouse/k2/<uuid>: <n> objects, <bytes> bytes
#                1209  data/00000-29-<uuid>-0-00001.parquet
#      ... one line per object ...
#   drop: would copy them to ~/k2-backup/drop/<date>/k2.scratch_p4/wap_bronze/, then drop table k2.scratch_p4.wap_bronze
#   drop: room: the copy needs <bytes> bytes; <bytes> bytes free at ~/k2-backup
#   drop: would then drop namespace k2.scratch_p4
#   drop: DRY RUN: nothing copied, nothing dropped

# The drop. A namespace: every table and view in it, then the namespace.   ✅ verified on a scratch namespace
make drop TARGET=k2.spike_drop_n05
#   drop: k2.spike_drop_n05.synthetic_rows: s3://k2-lake/warehouse/k2/<uuid>: 11 objects, 28052 bytes
#   drop: k2.spike_drop_n05.synthetic_view: s3://k2-lake/warehouse/k2/<uuid>: 1 objects, 480 bytes
#   drop: room: the copy needs 28532 bytes; <bytes> bytes free at ~/k2-backup
#   drop: k2.spike_drop_n05.synthetic_rows: copied s3://... to ~/k2-backup/drop/<date>/k2.spike_drop_n05/synthetic_rows: 11 objects, 28052 bytes in MinIO; 11 objects, 28052 bytes in the copy: match
#   drop: k2.spike_drop_n05.synthetic_view: copied ... 1 objects, 480 bytes in MinIO; 1 objects, 480 bytes in the copy: match
#   drop: manifest ~/k2-backup/drop/<date>/k2.spike_drop_n05.<HHMMSS>Z.json
#   drop: k2.spike_drop_n05.synthetic_rows: dropped with purge; 0 objects left at s3://... after 2 s
#   drop: k2.spike_drop_n05.synthetic_view: dropped with purge; 0 objects left at s3://... after 4 s
#   drop: k2.spike_drop_n05: namespace dropped
#   drop: OK: k2.spike_drop_n05 dropped; the copy is in ~/k2-backup/drop/<date>

# One table, and a bucket.   ✅ verified on scratch objects
make drop TARGET=k2.spike_drop_n05.synthetic_rows
make drop TARGET=bucket:spike-drop-n05
#   drop: bucket:spike-drop-n05: copied s3://spike-drop-n05 to ...: 2 objects, 34 bytes in MinIO; 2 objects, 34 bytes in the copy; 4 object versions, 61 bytes in MinIO; 4, 61 bytes in ....versions: match
#   drop: bucket:spike-drop-n05: bucket removed
```

**Check it is gone**, from the catalog, from Trino and from storage. Each command reaches the
stack from a throwaway container on the catalog's network, as `scripts/drop.sh` does: the catalog
has no published port. Trino is reached with the `trino_cli` helper of
[tls-and-passwords.md](./tls-and-passwords.md) (HTTPS and a password since 2026-11-27), defined in
the same shell after the `NET` line below: Trino is on the catalog's network too.
<!-- VERIFY: the NET line (MinIO is on both networks after the cutover) and both trino_cli lines of this runbook, on the live stack after the cutover; the commands below ran 2026-11-26 in the old shape -->

```bash
NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' k2-minio | grep -o '[^ ]*_k2-catalog')
IMG=ghcr.io/rjdscott/minio:RELEASE.2025-09-07T16-13-09Z@sha256:a1a8bd4ac40ad7881a245bab97323e18f971e4d4cba2c2007ec1bedd21cbaba2
lk() { docker run --rm --network "$NET" --entrypoint curl "$IMG" -sS "$@"; }
mcrun() { docker run --rm --network "$NET" -e MC_CONFIG_DIR=/tmp/.mc -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD --entrypoint /bin/bash "$IMG" -c 'export MC_HOST_k2="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000"; mc "$@"' mc "$@"; }
P=$(lk "http://lakekeeper:8181/catalog/v1/config?warehouse=k2" | python3 -c "import json, sys; print(json.load(sys.stdin)['defaults']['prefix'])")

lk -o /dev/null -w '%{http_code}\n' "http://lakekeeper:8181/catalog/v1/$P/namespaces/spike_drop_n05"   # 404
trino_cli svc.platform 'SHOW SCHEMAS FROM lake'                                                       # not listed
mcrun ls --recursive k2/k2-lake/warehouse/k2/01a21830-6e2f-7a63-a7ff-66792a90b027/ | wc -l             # 0: a location from the drop log
mcrun ls k2                                                                                             # the bucket is not listed
```

---

## 2. A drop stopped

**Symptom**, `make drop` prints `drop: REFUSED: ...` (exit 2) or `drop: FAIL: ...` (exit 1).

**Detection**, manual only: the command's own output. No alert covers a drop.

**Expected behaviour**, every stop before step 5 leaves the catalog and MinIO as they were and
says `nothing was dropped` where a copy had started. A partial copy is kept; the next run copies
only what is missing into the same day's directory (mc never overwrites there) and compares
again. Only a stop during step 5 leaves the target half dropped.

**Recovery**

| Message | Fix |
|---------|-----|
| `REFUSED: TARGET ...`, `names a whole warehouse` | The shape is wrong: `<warehouse>.<namespace>[.<table>]` or `bucket:<name>`; a warehouse is never dropped |
| `holds a lake warehouse` | Drop the warehouse's namespaces, one by one |
| `not inside the warehouse's prefix`, `is shared with` | Do not drop. The catalog points at storage outside its warehouse, or that another table or view uses: find out why first |
| `current metadata file is not at` | The catalog and MinIO disagree about the table; investigate before dropping anything |
| `under the floor` | Free space on that disk (`df -h ~`; a bloated Docker disk image is [docker-desktop-disk.md](./docker-desktop-disk.md)), or set `K2_BACKUP_DIR` to a disk with room |
| `could not start the helper container` | `K2_BACKUP_DIR` is not shared with Docker Desktop (`/tmp` is not); choose a directory under your home directory |
| `... in the copy ...; nothing was dropped` | The copy is short: run again. Twice in a row is a disk problem on the host |
| `object(s) not copied: mc does not replace a file already in <dir>` | The same target was copied earlier the same day, and its objects in MinIO have changed since (a restored table's files are newer than their copy). The earlier copy is kept as it was. Move it aside, `mv <dir> <dir>.earlier`, and run again |
| `changed while it was copied` | Something writes to the target: stop it (section 1 of this page, what it does not do), then run again |
| `answered 409 to the namespace drop` | A table or view was added to the namespace during the run: run again, which drops it too |
| `dropped from the catalog, but <n> objects are still at <location> after 900 s` | The catalog's purge has not finished. The copy holds every object. Check the purge task below; once it is `SUCCESS` and the location is empty, run `make drop` again for the rest of the namespace. If it `FAILED`, delete what is left by hand, then run again |

```bash
# The catalog's purge tasks, newest first: status and table. With lk from section 1.
WID=$(lk http://lakekeeper:8181/management/v1/warehouse | python3 -c "import json, sys; print(next(w['id'] for w in json.load(sys.stdin)['warehouses'] if w['name'] == 'k2'))")
lk -X POST "http://lakekeeper:8181/management/v1/warehouse/$WID/task/list" -H 'content-type: application/json' -d '{"queue-name": ["tabular_purge"], "page-size": 10}' | python3 -c "import json, sys; [print(t['status'], '.'.join(t['entity-name']), t['created-at']) for t in json.load(sys.stdin)['tasks']]"
#   SUCCESS spike_drop_n05.synthetic_rows 2026-11-26T14:14:31.937793Z

# Only after a FAILED purge, with the drop log's "copied ... : match" line for that location:
# delete what is left under it. The table is no longer in the catalog, so nothing else will.
mcrun rm --recursive --force k2/k2-lake/warehouse/k2/01a21830-6e2f-7a63-a7ff-66792a90b027/
```

**Measured**, the stops are exercised by `tests/test_drop.py`. Seen on the stack, 2026-11-26:
`could not start the helper container` with `K2_BACKUP_DIR` under `/tmp`; and `object(s) not
copied` when a restored table and bucket were dropped again the same day, cleared by the `mv` in
seconds. A purge that never finished has not occurred; the `mcrun rm` above was run only on an
already empty location.

---

## 3. Restore a dropped table from its copy

**Symptom**, a table was dropped and is needed back.

**Detection**, manual only.

**Expected behaviour**, nothing restores itself: the purge deleted the table's files, and the
copy `make drop` took is the only one, unless the table is one `make backup` copies too. Every
path inside Iceberg metadata is absolute, so the files go back to the same keys and the catalog is
pointed at the same metadata file.

**Recovery**, with `NET`, `IMG`, `lk` and `P` from section 1. Stop what would write to the
table first, as in section 1.

```bash
# 1. The newest copy of the table: its location, metadata file and copy directory.
T=k2.spike_drop_n05.synthetic_rows
read -r LOC META COPY < <(python3 -c "
import glob, json, os, sys
rows = [o for f in sorted(glob.glob(sys.argv[1] + '/drop/*/*.json'), key=os.path.getmtime)
        for o in json.load(open(f))['objects'] if o['name'] == sys.argv[2]]
print(rows[-1]['location'], rows[-1]['metadata_location'], rows[-1]['copy'])" "${K2_BACKUP_DIR:-$HOME/k2-backup}" "$T")
NS=$(cut -d. -f2 <<<"$T") TB=$(cut -d. -f3 <<<"$T")

# 2. The files back to their own keys. Without --overwrite, mc never replaces an object in MinIO.
docker run --rm --network "$NET" -e MC_CONFIG_DIR=/tmp/.mc -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD \
  --mount "type=bind,src=$COPY,dst=/copy,readonly" --entrypoint /bin/bash "$IMG" -c \
  'export MC_HOST_k2="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000"; mc mirror --json /copy "k2/$0" | tail -n 1' "${LOC#s3://}"
#   {"status":"success","total":28052,"transferred":28052,...}

# 3. The namespace, if the drop took it, then the table registered at the metadata file it had.
lk -X POST "http://lakekeeper:8181/catalog/v1/$P/namespaces" -H 'content-type: application/json' \
  -d "{\"namespace\": [\"$NS\"]}" | python3 -c "import json, sys; d = json.load(sys.stdin); print(d.get('namespace') or d)"
lk -X POST "http://lakekeeper:8181/catalog/v1/$P/namespaces/$NS/register" -H 'content-type: application/json' \
  -d "{\"name\": \"$TB\", \"metadata-location\": \"$META\"}" | python3 -c "import json, sys; d = json.load(sys.stdin); print(d.get('metadata-location') or d)"
#   prints the metadata file just registered, the same as $META

# 4. Read it back.
trino_cli svc.platform "SELECT count(*) FROM lake.$NS.$TB"
```

A **view** is not registered back: create it again from its SQL, which the copy keeps.

```bash
gzip -dc ~/k2-backup/drop/<date>/k2.spike_drop_n05/synthetic_view/metadata/*.gz.metadata.json \
  | python3 -c "import json, sys; v = json.load(sys.stdin); print([r['sql'] for r in v['versions'][-1]['representations']])"
#   ['SELECT id FROM lake.spike_drop_n05.synthetic_rows WHERE id > 1']
```

A **bucket**'s current objects go back into a new bucket of the same name, from the newest copy.
Its noncurrent versions stay as files under `<target>.versions/<key>/<version id>`; no command
here puts them back as versions.

```bash
B=spike-drop-n05
COPY=$(ls -d "${K2_BACKUP_DIR:-$HOME/k2-backup}"/drop/*/"bucket:$B" | tail -n 1)
mcrun mb "k2/$B"
docker run --rm --network "$NET" -e MC_CONFIG_DIR=/tmp/.mc -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD \
  --mount "type=bind,src=$COPY,dst=/copy,readonly" --entrypoint /bin/bash "$IMG" -c \
  'export MC_HOST_k2="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000"; mc mirror --json /copy "k2/$0" | tail -n 1' "$B"
mcrun ls --recursive "k2/$B"
```

**Measured**, drilled 2026-11-26 on scratch objects, then each dropped again with `make drop`:
the table `k2.spike_drop_n05.synthetic_rows`, steps 1 to 4 as written, its four synthetic rows
read back; and the bucket `spike-drop-n05`, its current objects back. Not timed.

---

## Failure modes / incidents

- None yet.

---

**Last verified:** 2026-11-26 against `04b5cc9` plus this change, on the live stack, from a
worktree. Run: every command on this page, on scratch objects of its own (`k2.spike_drop_n05`,
bucket `spike-drop-n05`), plus the dry run of `k2.scratch_p4`, with `K2_BACKUP_DIR` set to a
temporary directory under the home directory. The `mcrun rm` of section 2 ran only on an empty
location.

---

## 4. A bucket under object lock

**Symptom**, `make drop TARGET=bucket:<name>` stops in step 2, before any copy, dry run or not:

```
drop: FAIL: bucket:scratch-p4-landing: object lock (bucket default GOVERNANCE 1DAYS): 19 object version(s) under COMPLIANCE,GOVERNANCE retention until 2026-11-27T06:00:18.47Z at the latest, which no delete may remove before then; drop.sh never bypasses governance mode. Run again after 2026-11-27T06:00:18.47Z; nothing was copied or dropped
```

or the same with `under a legal hold, which has no expiry`.

**Why.** Object lock (write once, read many) keeps every object version written to the bucket
until its own `until` time: `GOVERNANCE` lets a holder of the bypass permission delete it early,
`COMPLIANCE` lets nobody, and a legal hold lasts until someone clears it. `mc rb` cannot remove
such a version, so the drop would copy everything and then fail at the last step. That happened
on 2026-11-26 with `bucket:scratch-p4-landing`: the copy matched, `mc rb` answered
`WORM protected`, nothing was lost. The check now runs first, and `drop.sh` never passes a
bypass flag.

**Recovery.** Wait until the time the message names, then run the same command again. To see
the lock yourself, with `mcrun` from section 1, read-only:

```bash
mcrun retention info --default --json k2/scratch-p4-landing      # ✅ verified
#   {"op":"info","enabled":"Enabled","mode":"GOVERNANCE","validity":"1DAYS","status":"success"}
mcrun retention info --recursive --versions --json k2/scratch-p4-landing | python3 -c '
import json, sys, datetime as d
r = [x for x in map(json.loads, sys.stdin) if x.get("until")]
print(len(r), "versions retained,", sorted({x["mode"] for x in r}), "the latest until", max((x["until"] for x in r), key=d.datetime.fromisoformat))'
#   19 versions retained, ['COMPLIANCE', 'GOVERNANCE'] the latest until 2026-11-27T06:00:18.47Z
mcrun legalhold info --recursive --versions --json k2/scratch-p4-landing | grep -c '"legalhold":"ON"'
#   0: no version is on legal hold (grep exits 1 when it counts none)
```

**Last verified:** 2026-11-26, from a worktree: the dry run above against the live stack, with
the worktree's `scripts/drop.sh` run under the live checkout's path so it read `.env` in place
(`bash -c "$(cat scripts/drop.sh)" <live checkout>/scripts/drop.sh`); the bucket's version
listing was the same before and after. `tests/test_drop.py` covers a retained version, a legal
hold, an expired retention and a lock state MinIO does not answer.
