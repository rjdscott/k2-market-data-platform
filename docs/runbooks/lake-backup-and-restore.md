# Runbook: Back up what nothing can rebuild, and restore it

`make backup` (and nightly once `make backup-install` has run) keeps a second copy of what the live
release says nothing can rebuild, in a directory on the host, outside Docker. This page says what
that copy holds and what it does not, how to run, schedule and check it, and what a restore from it
takes. It does not cover the ClickHouse hot tier, which is paused
([pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md)), nor the copy
`make drop` takes before it drops ([drop-and-rebuild.md](./drop-and-rebuild.md)).

**What is copied is compiled, not listed by hand.** Each table's contract says `backup: restore`
or `backup: rebuild` (a `customProperties` entry of its ODCS document under `contracts/`), and
k2gen compiles the release's `compiled/lake/backup-set.json`
([`contracts/schema/backup-set.schema.json`](../../contracts/schema/backup-set.schema.json)). The
backup reads the set of the **live** release, the one `${K2_RELEASES}/pointers/trino` names, once
per run. Its entry kinds:

| Kind | What a run copies |
|------|-------------------|
| `table`, mode `restore` | the table's files at its ref (`main` for bronze and platform tables, the `audited` tag for a dbt output): its current metadata file, the snapshot's manifest list, every manifest, every live data and delete file, and the snapshot's statistics files |
| `table`, mode `rebuild` | nothing: the manifest lists it under `rebuild`, since it is rebuilt from its inputs |
| `landing` | every object under `k2-landing/<domain>/<product>/`, the bytes as they arrived |
| `lakekeeper-db` | the catalog database's `pg_dump`, which carries the views, their owners and the `audited` tags |
| `releases` | the releases root, as one tar that keeps its links and modes |

The set compiled from this branch's `contracts/` (`bash scripts/verify/ci-validate.sh <dir>`, which
starts no container, then `compiled/lake/backup-set.json` of the trial release it prints): the
landings of `fx-reference/ecb-fx-rates/` and `reference-data/currency-master/`; `restore` tables
`governance.run_records.run_record`, `k2.ecb_fx_rates.bronze_daily` (the 90-day file forgets, so a
lost landing cannot be fetched again), and each file product's `landing_files` and
`audit_checks`; `rebuild` for every dbt output and for `k2.currency_master.bronze_currencies`;
plus `lakekeeper-db` and `releases`.

**How it runs** ([`scripts/backup-lake.sh`](../../scripts/backup-lake.sh), then
[`scripts/backup_lake.py`](../../scripts/backup_lake.py)): (1) `pg_dump` of the `lakekeeper` and
`airflow` databases through `docker exec k2-prefect-db`, each checked for pg_dump's end marker;
(2) the copy, in a throwaway container of the live release's `tools` image, as you, on the
catalog's network (`<project>_k2-catalog`, found among `k2-minio`'s networks: neither Lakekeeper
nor MinIO has a host port), with the **backup storage key** `K2_BACKUP_S3_ACCESS_KEY` and
`K2_BACKUP_S3_SECRET_KEY` (`make engine-keys` writes them into `.env`; MinIO policy
[`docker/lake/minio/policies/backup.json`](../../docker/lake/minio/policies/backup.json): get and
list on every warehouse prefix and on `k2-landing`, nothing else). The script reads the two key
names alone from `.env`, by name, and passes them by name. Every storage call is a GET: the backup
writes nothing to the lake. The catalog is unauthenticated, so every path it or the metadata
names must sit under the table's location, or the run fails.

**Run every command from the repo root of the live checkout with `set -a && . ./.env && set +a` loaded.**

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | A copy of the live backup set | < 5 min | 1 s, the copy's own line (`backup-lake: OK in 1 s`), live, 2026-11-27, run `runs/20261127T100533Z`: 60 files |
| 2 | Check the newest copy | < 1 min | `backup-verify: OK: 0 problem(s)` on that run, live, 2026-11-27; its time not recorded |
| 3 | A failed or stale copy | next run | not induced; the error paths are exercised by `tests/test_backup_lake.py` |
| 4 | Restore | none yet | **not yet drilled: the restore drill is phase 004's** ([plan 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md), "The first restore drill") |

---

## What is on disk, and what is not

Into `$K2_BACKUP_DIR` (default `~/k2-backup`; set it in `.env` so the timer and `make health` read
the same one; the script refuses a directory inside the checkout):

| Path | What |
|------|------|
| `runs/<UTC stamp>/` | One complete run, stamp `YYYYMMDDTHHMMSSZ`. Written as `runs/.<stamp>.partial/` and renamed only once its `manifest.json` is written, so a directory without the leading dot is a complete copy; a run that failed or was killed leaves a `.partial`, removed by the next run |
| `runs/<stamp>/objects/<bucket>/<key>` | Each copied object at its own bucket and key. A file the previous complete run holds at the same key and size is hard-linked, not fetched again (Iceberg and landing objects are written once under a unique key), so each run is whole on its own and costs only what changed |
| `runs/<stamp>/releases.tar` | The releases root, its links and modes kept (top directory `k2-releases`) |
| `runs/<stamp>/manifest.json` | `run`, `release` (the live release's id), `tag`, `backup_set_sha256`; `tables` (each with `table`, `ref`, `location`, `metadata_location`, `snapshot_id`, `objects`, `bytes` and `files`, every relative path with its size); `rebuild` (the tables not copied); `landings` (`bucket`, `prefix`, `objects`, `bytes`, `files`); `releases` and `dumps` (each `file`, `bytes`, `sha256`); `objects_copied` and `bytes_copied` (fetched this run, links not counted) |
| `catalog/<stamp>.sql.gz`, `catalog/<stamp>.airflow.sql.gz` | The `lakekeeper` and `airflow` dumps of each run, named in that run's `manifest.json` |
| `STATUS.json` | The last run's `status`, `error`, `duration_s`, and `restore_point`: the last **successful** run's `run`, `manifest`, `release`, `tables` (location, metadata file, snapshot id, ref, objects, bytes), `dumps` and totals. A failed run keeps the previous restore point |
| `lake/<table>/` | Not this script's any more: the four tables of the retired v3 lake as the script before phase 002 last copied them, on 2026-11-26. Kept as they are, unpruned; nothing restores them (they would bring back a table phase 000 retired) |
| `drop/<date>/` | Not this script's: the copies `make drop` takes ([drop-and-rebuild.md](./drop-and-rebuild.md)) |

The newest 14 runs and the newest 14 of each dump are kept (`K2_BACKUP_KEEP`); older ones go only
after a run holds, so a failed run never removes what the restore point names. A run refuses to
start under 20 GiB free on the target's disk (`K2_BACKUP_MIN_FREE_GIB`).

**Not copied:**

- Every `rebuild` table: each dbt product's silver and gold, the governance products' outputs, and
  bronze whose landing can be fetched again (`currency-master`). Nothing rebuilds them by command
  yet: `make rebuild` is phase 004's.
- `${K2_SECRETS}` (default `~/k2-secrets`: the CA, Trino's `password.db`, every password file,
  k2-api's keys) and `.env` (the engine keys, MinIO's root pair, `LAKEKEEPER_ENCRYPTION_KEY`).
  Keep your own copy elsewhere: the catalog stores each warehouse's storage credential encrypted
  with `LAKEKEEPER_ENCRYPTION_KEY`, so a restored catalog dump needs the same key.
- The volumes `k2-spool` (fetch's spool; the landing holds the bytes) and `k2-site` (the catalog
  site and dbt docs: `make site` writes them again).
- ClickHouse, Redpanda, Grafana, Prometheus and Alertmanager.

**What it protects against:** a lost or wiped `minio-data` or `postgres-data` volume, a bad command
that deletes objects, a dropped or broken table, a lost catalog database, a lost releases root.

**What it does not protect against, plainly:**

- **The disk.** The copy is on the same physical disk as Docker's disk image. A failed disk, a
  stolen or destroyed machine, loses both.
- **Anything running as you.** The copy is ordinary files in your home directory. Any process
  running as your user can delete or rewrite it, and so can any container holding Docker Desktop's
  socket (the Airflow scheduler and cAdvisor mount it), because Docker Desktop shares your home
  directory with its VM. The same is true of the scripts and the units they run from.
- **Damage after the copy.** `make backup-verify` re-checks every file's size and the sha256 of
  each dump and of the releases tar; the copied objects themselves carry no checksum, so a file
  damaged in place at its own size is noticed only by a restore.
- **The last day.** The copy is up to a day old (daily at 04:30 UTC); what changed after it is not
  in it.

---

## 1. Run, schedule and check the copy

```bash
# By hand. Read-only against the stack.
make backup

# What it would copy, without copying or dumping: the same table, landing and rebuild lines.
bash scripts/backup-lake.sh --dry-run

# Schedule it: daily at 04:30 UTC. Run this from the main checkout, not a worktree: the unit
# runs the script from the directory it was installed from.
make backup-install
systemctl --user list-timers k2-backup.timer      # NEXT is the next 04:30 UTC

# Check it: the newest complete run against its manifest, and against the live backup set.
make backup-verify
make health                                       # step (5): PASS, WARN or FAIL, as below
python3 -m json.tool ~/k2-backup/STATUS.json | head -20
journalctl --user -u k2-backup --since yesterday  # a timer run's output
```

What `make backup` prints, from `scripts/backup_lake.py` `run_copy` and the script's status writer,
one line per entry of the set: `backup-lake: <table> at <ref>: snapshot <id>, <n> files` per
`restore` table, `backup-lake: landing s3://<bucket>/<prefix>: <n> objects` per landing,
`backup-lake: <table>: rebuild, not copied` per `rebuild` table, then the run's totals. On the live
stack on 2026-11-27, after the gzip fix (Failure modes, below), its last lines:

```text
backup-lake: <table>: rebuild, not copied
...
backup-lake: run runs/20261127T100533Z: 60 objects, 426190 bytes fetched
backup-lake: OK in 1 s: 60 files, 426190 bytes fetched this run; ...
```

`--dry-run` prints the same entry lines and `backup-lake: DRY RUN: nothing copied`. `make
backup-verify` (`scripts/backup-lake.sh --verify`, which runs `backup_lake.py verify` on the host
and needs no container) prints `backup-verify: run <stamp>: release <id> (tag <tag>)`, then
`backup-verify: table <table> at <ref>: snapshot <id>, <n> files, <bytes> bytes` per table,
`backup-verify: landing s3://<bucket>/<prefix>: <n> objects, <bytes> bytes` per landing, `backup-verify:
<name>: <file>, <bytes> bytes, sha256 matches` per dump and for the releases tar, and last
`backup-verify: OK: 0 problem(s)`. On that live run, 2026-11-27, one table line for each of
`governance.run_records.run_record`, `k2.currency_master.audit_checks`,
`k2.currency_master.landing_files`, `k2.ecb_fx_rates.audit_checks`, `k2.ecb_fx_rates.bronze_daily`
and `k2.ecb_fx_rates.landing_files`, then:

```text
backup-verify: table <table> at main: snapshot <id>, <n> files, <bytes> bytes
...
backup-verify: landing s3://k2-landing/fx-reference/ecb-fx-rates/: 1 objects, 69492 bytes
...
backup-verify: releases: releases.tar, 5365760 bytes, sha256 matches
backup-verify: OK: 0 problem(s)
```

The first `...` stands for the other five tables' lines, the second for the two database dumps' lines. It fails (exit 1) on a missing
file, a size that differs, a sha256 that differs, and a `restore` table, `releases` or
`lakekeeper-db` entry of the **live** set that the run does not hold. With no complete run yet, as
run on an empty scratch directory on 2026-11-27 (`python3 scripts/backup_lake.py verify --dir <empty dir> --backup-set <a trial release's backup-set.json>`):

```text
backup-verify: FAIL: no complete backup run under <empty dir>/runs
backup-verify: FAIL: 1 problem(s)
```

`make health` step (5), from `scripts/health.sh`: `SKIP (not set up on this host: ...)` where the
timer is not installed; `PASS (last successful backup <n>h ago, <dir>)`; `WARN (the last run failed:
<error>; last success <n>h ago, FAIL at 26h)`; `FAIL (last successful backup <n>h ago, over 26h:
...)`; `FAIL (the timer is installed and no backup has succeeded into <dir>: ...)`.

- **A user timer runs only while you are logged in.** `Persistent=true` runs a missed copy at the
  next login. Docker Desktop must be running too, or the run fails and step (5) shows it.
- **Remove it:** `systemctl --user disable --now k2-backup.timer && rm ~/.config/systemd/user/k2-backup.{service,timer}`.

---

## 2. The copy failed or is stale

**Symptom**, `make health` step (5) prints `WARN (the last run failed: ...)` or one of its two
`FAIL` lines; or `make backup-verify` exits 1.

**Detection**, manual only: `make health` and `make backup-verify`. No Prometheus alert covers
this.

**Expected behaviour**, nothing retries before the next 04:30 UTC. A failed run leaves
`restore_point` at the last successful run and its copy as a `.partial` the next run removes, so a
restore never uses a partial copy.

**Recovery**

```bash
# 1. Why: the error of the last run, and the timer run's output.
python3 -c "import json, os; s = json.load(open(os.path.expanduser('~/k2-backup/STATUS.json'))); print(s['status'], s['finished_utc'], s['error'])"
journalctl --user -u k2-backup -n 30
```

The messages are `scripts/backup-lake.sh`'s `backup-lake: FAIL: ...` and, for the copy,
`scripts/backup_lake.py`'s:

| Error | Fix |
|-------|-----|
| `<dir> has <n> GiB free, under the floor of 20 GiB (K2_BACKUP_MIN_FREE_GIB)` | Free space on that disk (`df -h ~`; a bloated Docker disk image is [docker-desktop-disk.md](./docker-desktop-disk.md)) |
| `another backup-lake run holds <dir>/.lock` | A run is in progress: `systemctl --user status k2-backup.service` |
| `pg_dump of database <db> failed`, `the dump of database <db> is incomplete` | `docker ps --filter name=k2-prefect-db`: the database container is down or restarting |
| `no live release: .../pointers/trino does not name a release with compiled/lake/backup-set.json` | No release is deployed, or the live one is from before phase 002: `make release-status` |
| `the release <id> names no tools image` | The live release's manifest lacks the image the copy runs in: deploy a release that has one |
| `k2-minio is not running` | The stack is down: start it ([operator guide](../operations/README.md), section 2) |
| `K2_BACKUP_S3_ACCESS_KEY or K2_BACKUP_S3_SECRET_KEY is not set in .env (make engine-keys)` | `make engine-keys`, then the deploy that gives MinIO the user ([deploy-a-release.md](./deploy-a-release.md)) |
| `<table>: the catalog did not answer GET /...` | Lakekeeper is down: `docker logs --tail 100 k2-lakekeeper` |
| `s3://...: storage answered HTTP 403`, `listing s3://...: ...` | The backup key is refused: MinIO has no user for it, or its policy is stale; lake-init creates both (`docker logs k2-lake-init`) |
| `<table>: <uri> is outside the table's location <loc>` | The catalog or a metadata file names a path outside the table: stop, this is an incident to investigate before any restore |
| `the lake copy did not complete (runs/<stamp> was not written)` | The line above it is the copy's own error |
| `no .env in <root>`, `K2_BACKUP_DIR <dir> is inside the checkout ...` | Run from the live checkout; move `K2_BACKUP_DIR` outside it |
| `mounts denied` | `K2_BACKUP_DIR` is not shared with Docker Desktop; move it under your home directory |

```bash
# 2. Run it again by hand, and check the result.
make backup && make backup-verify && make health
```

**Measured**, not induced.

---

## 3. Restore from the copy

> **Not yet drilled: the restore drill is phase 004's**, which also brings `make restore` and
> `make rebuild` ([plan 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)).
> Every step below is written from the copy's layout, the backup set's schema ("a restore is
> followed by the catalog drift check, then make access-verify") and the table restore that
> [drop-and-rebuild.md](./drop-and-rebuild.md) section 3 drilled on scratch objects on 2026-11-26;
> no restore from `runs/` has been run. Read the whole section before starting, and stop at the
> first surprise.

**Symptom**, objects, a table, the catalog database or the releases root are lost.

**Detection**, manual: `make health` step (1) (`lakekeeper` or `minio` unhealthy), a failing
`make release-verify`, a read that fails on a missing file.

**Expected behaviour**, nothing restores itself. The backup key is read-only, so it cannot write
anything back: the steps below write with MinIO's root pair, from a throwaway container of MinIO's
own image on the catalog's network, as `make drop` and its restore do. Stop the product's DAG first
(`docker exec k2-airflow-scheduler airflow dags pause <dag>`), so nothing writes to a table while
it comes back.

What a restore does, in order:

1. **Choose the run**: the restore point in `STATUS.json`, which is the newest complete run, and
   check it with `make backup-verify`.
2. **The catalog database**, only if it is lost: the run's `lakekeeper` dump (section 3b). The
   dump is taken before the copy, seconds earlier, so a table committed between the two points at
   an older metadata file than the manifest's `metadata_location`; compare the two after step 3b,
   and stop if they differ (the drill decides that case).
3. **Each `restore` table**: its files back to their own keys from `runs/<stamp>/objects/`, never
   overwriting, then, if the catalog lacks it, the table registered at the manifest's
   `metadata_location` (every path inside Iceberg metadata is absolute, so the files must go back
   to the same keys).
4. **Each landing**: its objects back under `k2-landing/<domain>/<product>/`.
5. **The releases root**, only if it is lost: `releases.tar` extracted where `K2_RELEASES` points,
   then every container that mounts it recreated (a directory mount pins the old directory: the
   bind-mount gotcha in [`CLAUDE.md`](../../CLAUDE.md)).
6. **Check**: `make release-verify`, which runs the catalog drift check and the live access suite;
   then unpause the DAGs. A `rebuild` table is not restored: until `make rebuild` exists, it is
   absent or as it was.

**3a. A table or a landing.**

```bash
B=${K2_BACKUP_DIR:-$HOME/k2-backup}
RUN=$(python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['restore_point']['run'])" "$B/STATUS.json")
make backup-verify                                  # the same run, OK

NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' k2-minio | grep -o '[^ ]*_k2-catalog')
IMG=$(docker inspect -f '{{.Config.Image}}' k2-minio)
lk() { docker run --rm --network "$NET" --entrypoint curl "$IMG" -sS "$@"; }
mirror() {  # mirror <dir in the run> <bucket/prefix>: the copy back, never replacing an object
  docker run --rm --network "$NET" -e MC_CONFIG_DIR=/tmp/.mc -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD \
    --mount "type=bind,src=$1,dst=/copy,readonly" --entrypoint /bin/bash "$IMG" -c \
    'export MC_HOST_k2="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000"; mc mirror --json /copy "k2/$0" | tail -n 1' "$2"
}

# A table: its location and metadata file from the run's manifest, its files back.
T=k2.ecb_fx_rates.bronze_daily
read -r LOC META < <(python3 -c "
import json, sys
t = next(t for t in json.load(open(sys.argv[1]))['tables'] if t['table'] == sys.argv[2])
print(t['location'], t['metadata_location'])" "$B/$RUN/manifest.json" "$T")
mirror "$B/$RUN/objects/${LOC#s3://}" "${LOC#s3://}"

# Then, if the catalog does not hold it (404), registered at the copied metadata file.
WH=$(cut -d. -f1 <<<"$T") NS=$(cut -d. -f2 <<<"$T") TB=$(cut -d. -f3 <<<"$T")
P=$(lk "http://lakekeeper:8181/catalog/v1/config?warehouse=$WH" | python3 -c "import json, sys; print(json.load(sys.stdin)['defaults']['prefix'])")
lk -o /dev/null -w '%{http_code}\n' "http://lakekeeper:8181/catalog/v1/$P/namespaces/$NS/tables/$TB"
lk -X POST "http://lakekeeper:8181/catalog/v1/$P/namespaces/$NS/register" -H 'content-type: application/json' \
  -d "{\"name\": \"$TB\", \"metadata-location\": \"$META\"}" | python3 -c "import json, sys; d = json.load(sys.stdin); print(d.get('metadata-location') or d)"

# A landing: every object under its prefix back.
mirror "$B/$RUN/objects/k2-landing/fx-reference/ecb-fx-rates" "k2-landing/fx-reference/ecb-fx-rates"

# Check, then let the product run again.
make release-verify
docker exec k2-airflow-scheduler airflow dags unpause <dag>
```

**3b. The catalog database is lost; MinIO is intact.**

```bash
# 1. The restore point's dump files.
python3 -c "
import json, os
p = json.load(open(os.path.expanduser('~/k2-backup/STATUS.json')))['restore_point']
print('taken', p['utc'], 'release', p['release'])
for db, d in p['dumps'].items(): print(db, d['file'])"

# 2. Stop the catalog, and confirm the database really is gone or empty before replacing it:
#    a live catalog prints its warehouses here, and then this is not the section to follow.
docker compose stop lakekeeper
docker exec k2-prefect-db sh -c 'psql -U "$POSTGRES_USER" -d lakekeeper -Atc "SELECT warehouse_name FROM warehouse"'

# 3. Replace it with the restore point's dump (the file from step 1), then start the catalog.
docker exec k2-prefect-db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists lakekeeper && createdb -U "$POSTGRES_USER" lakekeeper'
gzip -dc ~/k2-backup/catalog/<stamp>.sql.gz \
  | docker exec -i k2-prefect-db sh -c 'psql -U "$POSTGRES_USER" -d lakekeeper -v ON_ERROR_STOP=1 -q'
docker compose start lakekeeper
```

The dump's pointers are as old as the dump. Airflow's database restores the same way, as database
`airflow`, owner `airflow` (`createdb -U "$POSTGRES_USER" -O airflow airflow`), with
`airflow-scheduler`, `airflow-dag-processor` and `airflow-api-server` stopped.

**3c. The releases root is lost.** Extract into a missing directory only, never over a live root:

```bash
R=${K2_RELEASES:-$HOME/k2-releases}
[ ! -e "$R" ] && tar -xf "$B/$RUN/releases.tar" -C "$(dirname "$R")" && ls "$(dirname "$R")/k2-releases/pointers"
docker compose up -d --force-recreate --no-deps prometheus airflow-scheduler airflow-dag-processor spark-iceberg trino k2-api
make release-verify
```

The tar's top directory is `k2-releases`; a `K2_RELEASES` of another name takes a rename after the
extract.

**Measured**, nothing yet: not drilled.

---

## Failure modes / incidents

- **2026-11-26**, the script before phase 002 copied four fixed tables, by hand-written list; its
  first copy took 1 min 41 s from a worktree with the stack live. Those tables retired with the v3
  lake; their copies stay under `lake/`.
- **2026-11-27, live**, the first `make backup` of real tables failed in `json.loads`: the stack's
  engines write Iceberg metadata gzip-compressed (`<v>.gz.metadata.json`), and the copy read every
  metadata file as plain JSON. Fixed in `092ba53` (`scripts/backup_lake.py`: the gzip magic bytes
  decide, not the name; a test in `tests/test_backup_lake.py`). The next run, `runs/20261127T100533Z`,
  copied and verified (section 1).

---

**Last verified:** 2026-11-27 on the live stack, run `runs/20261127T100533Z`, for section 1's
`make backup` and `make backup-verify` (the lines above). Also on 2026-11-27, without touching the
stack: `bash scripts/verify/ci-validate.sh` (the backup set above, from its trial
release), `python3 scripts/backup_lake.py verify` on an empty scratch directory (the two lines in
section 1), `bash scripts/backup-lake.sh` with a bad flag (its usage line). Not yet: `--dry-run`,
`make backup-install` and the timer, every fix in section 2 but the gzip one, and section 3: the
restore is phase 004's drill.
