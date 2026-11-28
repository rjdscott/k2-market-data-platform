# Runbook: Back up what nothing can rebuild, and restore it

`make backup` (and nightly once `make backup-install` has run) keeps a second copy of what the live
release says nothing can rebuild, in a directory on the host, outside Docker. This page says what
that copy holds and what it does not, how to run, schedule and check it, and how `make restore` and
`make restore-drill` put it back. It does not cover the ClickHouse hot tier, which is paused
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

The set compiled from this branch's `contracts/` (the k2gen golden
[`platform/k2gen/tests/golden/lake/repository/compiled/lake/backup-set.json`](../../platform/k2gen/tests/golden/lake/repository/compiled/lake/backup-set.json),
which `make test-k2gen` compares with what k2gen compiles): the landings of `fx-reference/ecb-fx-rates/`,
`market-capture/kraken-instruments/` and `reference-data/currency-master/`; `restore` tables
`governance.run_records.run_record` and `governance.run_records.holds`, `k2.ecb_fx_rates.bronze_daily`
(the 90-day file forgets, so a lost landing cannot be fetched again), `k2.kraken_instruments.bronze_pairs`,
the Kafka landing `k2.kraken_trades.landing` (the only copy of every record the product has read: the
raw topic is a buffer of at most 48 hours, `docker/redpanda/init.sh`), and each product's `landing_files` or `audit_checks`; `rebuild` for every dbt
output, for `k2.kraken_trades.bronze_trade` (restaged from its landing) and for
`k2.currency_master.bronze_currencies`; plus `lakekeeper-db` and `releases`.

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
| 4 | `make restore` | none yet | last verified: throwaway, 2026-11-28 (`restore-path.sh`, the restore inside the drill: 31.7 s); live: not yet |
| 5 | `make restore-drill` | none yet | last verified: throwaway, 2026-11-28 (`bash scripts/verify/restore-path.sh <release dir>`, `19 PASS, 0 FAIL in 671 s`; the drill 57.9 s with its checks and damage, claim 10); live: not yet. The rebuilds it leaves are [rebuild-a-product.md](./rebuild-a-product.md)'s, unmeasured at live size |

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

- Every `rebuild` table: each dbt product's silver and gold, the governance products' outputs, a
  Kafka product's bronze, and bronze whose landing can be fetched again (`currency-master`). `make
  rebuild` builds the dbt outputs and a Kafka product's bronze again
  ([rebuild-a-product.md](./rebuild-a-product.md)); nothing rebuilds a file product's bronze from its
  landing.
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

> **Built on the phase 004 exit branch, not in the live release.** `make restore` and `make
> restore-drill` (`scripts/restore.py`, `mk/restore.mk`) and the restore key they write with came after
> `v3.0.0-alpha.6-rc.1`, whose lake-init makes no restore user. Last verified: throwaway, 2026-11-28
> (`bash scripts/verify/restore-path.sh <release dir>`, `19 PASS, 0 FAIL in 671 s`); live: not yet.
> The throwaway had no Airflow (`--no-airflow`) and no Trino port on the host (`--holds-in-network`), so
> the pause, the drain and the selective unpause, and the holds written from the host, are proven by
> `tests/test_restore.py` against fakes only. Every message quoted is the code's own text.

**Symptom**, objects, a table, a landing or the catalog database are lost: `make health` step (1)
shows `lakekeeper` or `minio` unhealthy, `make release-verify` fails, a read fails on a missing file.

**Detection**, manual only: no Prometheus alert covers a lost table.

**Expected behaviour**, nothing restores itself, and only the newest complete run is ever restored.
`make restore` generates every step from the live release's `compiled/lake/backup-set.json`, entry by
entry, as the backup copied it, and every product is held while its tables come back. It writes with
its own storage key, `K2_RESTORE_S3_ACCESS_KEY` and `K2_RESTORE_S3_SECRET_KEY`
([`docker/lake/minio/policies/restore.json`](../../docker/lake/minio/policies/restore.json): get and put
on every warehouse prefix and on `k2-landing`, list there, no delete), passed by name to the two phases
that write and to nothing else; never MinIO's root pair. A put never replaces an object.

**Before the first restore on a stack:**

- `make engine-keys` writes the restore pair into `.env`. It must run **before the first deploy of a
  release that carries the restore key**: that release's compose file refuses lake-init without them
  (`set K2_RESTORE_S3_ACCESS_KEY in .env (make engine-keys)`), and that deploy's lake-init makes the
  MinIO user.
- `make password PRINCIPAL=svc.operator TO_FILE=1`, once per host ([hold-a-product.md](./hold-a-product.md)
  section 0): the restore holds every product as `svc.operator`, from the host.
- **Same host only.** The catalog dump's warehouse credentials are encrypted with
  `LAKEKEEPER_ENCRYPTION_KEY`, and the restore uses the key the running Lakekeeper already has in its
  environment. A restore after the loss of the host (that key, `.env`, `${K2_SECRETS}`) is not proven by
  anything here.

### 3a. `make restore`

**Recovery**, not yet run on the live stack:

```bash
make backup-verify            # the newest complete run, OK
make restore                  # RUN=<stamp> only to name that same run: an older one is refused
```

What it does, in order, stopping at the first refusal (exit 2) or failure (exit 1)
(`scripts/restore.py`, header):

1. **Checks that touch nothing**: both storage keys set; the live release's own files holding what the
   restore needs, its lake-init's restore user (`platform/docker/lake/minio/policies/restore.json`) and
   the holds table (`compiled/lake/ddl/trino/governance.run_records.holds.sql`); `svc.operator`'s file
   there; no `make rebuild` one-shot running; and the run newest, complete, verified as `make
   backup-verify` checks it, and holding the live backup set's tables and landings.
2. **Every DAG quiet**: each DAG that is not paused is paused (only those are ever unpaused here),
   then every DAG drained. Then **the restore key proven against MinIO**, before the first write: in each
   scope it writes (each warehouse a `restore` table sits in, and `k2-landing`), a listing, and a PUT with
   `If-None-Match: *` of a file the run holds, at its own key with its own bytes. The key has no delete
   and no admin right, so it can neither clean up a probe object nor read its own policy, and a listing
   alone proves MinIO knows it but not that it may put. MinIO authorizes the put before it evaluates the
   precondition, so an object that is there answers 412 and nothing is written; one that is not (a lost
   lake) is put, the very bytes the restore puts there. Iceberg and landing objects are written once
   under unique keys, so even a store that ignored the precondition would rewrite the same bytes.
3. **The catalog database**, only when it is lost: Postgres answers no warehouse row, or says the
   database or its table does not exist. Then Lakekeeper is stopped, the run's dump replayed, and
   Lakekeeper started. Any other answer (Postgres restarting, too many clients) replaces nothing and
   fails.
4. **The plan**, read-only with the backup key: refused unless every `restore` table the catalog holds
   is at the backup's location; each table's action decided (below), and so which products are left for
   rebuild: the owner of a table registered, re-pointed or recreated, and every product downstream of
   one by the release's compiled inputs.
5. **The run records' namespace** put back first (the holds and the run record), since a hold can be
   written only once its table is whole.
6. **A `restore` hold on every product**, refused while any product has a rebuild lock open.
7. **The rest**: each `restore` table's files back at their own keys (Iceberg metadata holds absolute
   paths), then registered, or re-pointed (dropped without purge, registered again) when the catalog's
   entry is the backup's or older or not whole, or kept when newer than the backup and whole; each
   landing's objects back; each `rebuild` table that is not whole dropped without purge and recreated
   empty with its schema, partitioning, sort order and properties. Whole: its metadata file and every
   manifest list, manifest, data and delete file of its `main` and `audited` snapshots, in one listing
   of its location.
8. **The drift check with the run records**: each `audited` tag equal to its newest `audited` run
   record, nothing else different but the tables recreated; else exit 1, every hold kept and every DAG
   left paused.
9. **The release**: every restore hold released but those of the products left for rebuild, which
   only `make rebuild` ends once its full refresh passes; the DAGs this restore paused unpaused but
   theirs.

Its lines, in that order (`<n>` and the names are the run's own):

```text
restore: run <stamp> (release <id>, tag <tag>) onto live release <id>
restore: <n> DAG(s) paused here (<m> were paused already); every DAG drained
  key <bucket>/warehouse/<name>: listed; a put of <uri> refused as existing (412): put reach proven, nothing written
  key k2-landing: listed; a put of <uri> refused as existing (412): put reach proven, nothing written
catalog database: present, not replaced
  table governance.run_records.holds: <n> file(s) put back, <m> already there; in the catalog at the backup's metadata
  held <product> (restore)
  table <table>: <n> file(s) put back, <m> already there; registered at the backup's metadata
  landing s3://k2-landing/<domain>/<product>/: <n> object(s) put back, <m> already there
  rebuild <table>: whole: kept
  rebuild <table>: its files are gone: dropped without purge, recreated empty, left for its rebuild
restore: the audited tags equal the run records; <n> pending, <m> table(s) recreated
  released <product> (restore)
  kept the restore hold on <product>: left for rebuild; `make rebuild PRODUCT=<product>` ends it once it passes
restore: unpaused <dags>, which this restore paused
restore: rebuild 1 of <n>: make rebuild PRODUCT=<product> (its <tables> recreated)
restore: each product above stays held (restore), its DAG paused, until its make rebuild passes, in that order
restore: OK in <s> s: run <stamp>
```

On a lost lake a key line says `listed; <uri> was not there and is put back, the run's own bytes: put
reach proven`. A lost catalog database prints `catalog database: replayed from <stamp>.sql.gz; Lakekeeper healthy, with
the encryption key its environment holds` instead of `present, not replaced`. A product downstream of
one recreated says `(downstream of one recreated)`. Then run the printed `make rebuild` lines in that
order ([rebuild-a-product.md](./rebuild-a-product.md) section 2), then `make release-verify`, and
[verify-a-kafka-product.md](./verify-a-kafka-product.md) for each Kafka product.

Refused (exit 2, `restore: REFUSED: <why>`); nothing in the lake changed, and the DAGs it paused are
unpaused again unless the catalog database was already replayed:

| Why | Do |
|---|---|
| `the live release <id> cannot be restored onto: its lake-init makes no restore MinIO user (no platform/docker/lake/minio/policies/restore.json); deploy a release that has them`, or `... it has no holds table (no compiled/lake/ddl/trino/governance.run_records.holds.sql); ...` | the live release predates the restore key or the holds (rc.1): deploy a later one, after `make engine-keys` |
| ``lake --phase key: MinIO does not grant the restore key the reach the restore needs (no user or policy for it: `make engine-keys`, then deploy a release whose lake-init makes the restore user): <scope>: the restore key cannot list it (...)``, or `<scope>: the restore key's put answered HTTP <n>` | `make engine-keys` wrote the pair but no lake-init has given it to MinIO since: `make lake-init` (lake-init again on the live release, as deploy runs it), then run again; on a release that predates the restore key, deploy a later one first |
| `no complete backup run under <dir>/runs` | `make backup`, if the lake still holds what it should; else there is nothing to restore from |
| `run <stamp> is older than the newest complete run <stamp>: only the newest is restored` | drop `RUN=` |
| `run <stamp> is incomplete: ...`, `run <stamp> does not hold what the live backup set names: ...` | the newest copy is damaged, or the live release names tables the run lacks: a fresh `make backup` if the lake is intact, else stop and investigate |
| `no password file for svc.operator at <path>: ...` | section "Before the first restore" |
| `make rebuild runs (k2-dbt-runner-<dag>-rebuild): restore after it` | wait for it |
| `the lake's tables are not the ones the backup holds: <table>: the catalog's is at <location>, the backup's at <location>` | another table has that name: an incident to investigate, never to restore over |
| `<product> has a rebuild lock open (make rebuild runs, or was killed: make rebuild PRODUCT=<product> UNLOCK=1)` | [rebuild-a-product.md](./rebuild-a-product.md) section 3 |

Failed (exit 1, `restore: FAIL: <type>: <why>`):

| Why | State left |
|---|---|
| `no storage key: K2_RESTORE_S3_ACCESS_KEY and K2_RESTORE_S3_SECRET_KEY not set (make engine-keys)` | nothing changed |
| `cannot tell whether the catalog database is lost (psql exit <n>: ...); nothing was replaced` | the DAGs it paused stay paused: fix Postgres, run again |
| `<uri>: the store holds <n> bytes there, the backup <m>; never overwritten` | holds kept, DAGs paused: an object the backup does not know at a backup key; investigate |
| `restore: FAIL: <n> difference(s) besides the tables left for rebuild; every hold kept, every DAG left paused` | the drift lines above it name each |

After a failure past step 5 it prints `restore: stopped after the run records came back: every hold this
restore took stays, and the DAGs it paused stay paused (<dags>); fix the cause and run make restore
again`. A second `make restore` on a restored lake puts nothing back, moves nothing and ends no hold
left for rebuild (`restore-path.sh` claim 16).

`make restore` replays the catalog database only. Airflow's dump (`catalog/<stamp>.airflow.sql.gz`) is
not replayed by any command here, and no replay of it has been run. The releases root is section 3c.

**Measured**, throwaway only, 2026-11-28: the restore inside the drill (3b) in 31.7 s.

### 3b. `make restore-drill`

**When**, to prove a restore on a stack whose data is disposable (before `v3.0.0`, the lab rule), after
a manual `make backup`. It destroys the lake and restores it.

**Expected behaviour**, it checks before it damages, in order: every check of 3a step 1; the restore key
proven against MinIO as in 3a step 2 (on an intact lake every probe answers 412, so nothing is written);
nothing committed since the run (each `restore` table's catalog metadata the run's, each landing's listing the
run's, no run record written since the run began) and no rebuild lock open; when it destroys a `rebuild` table, the live release's own `k2-dbt` taking
`--full-refresh`, asked of the dbt-runner image its `compiled/oneshots.json` pins
([rebuild-a-product.md](./rebuild-a-product.md) section 0: a sealed release carries no copy of the
wrapper); then its statement of what it destroys, refused without `CONFIRM=<the live release id>`; then the pause and drain, the same check
again, and a restore hold on every product. Then the damage, with MinIO's root pair from `.env` in
MinIO's own image (the damage only, never the restore): the catalog database dropped and made empty,
and every object deleted at each `restore` table's location, each landing prefix, and each `rebuild`
table's location in warehouses `k2` and `governance` that `make rebuild` can rebuild (a file product's
bronze is spared). Then 3a from step 3. The domain warehouses are left, so their `audited` tags come back
from the dump and are held to the run records.

**Recovery**, not yet run on the live stack:

```bash
make backup && make backup-verify
make restore-drill                                   # refused: prints what it destroys, and the CONFIRM it needs
make restore-drill CONFIRM=<live release id>
```

Without `CONFIRM` (exit 2):

```text
restore: REFUSED: the drill destroys: the catalog database (back from <stamp>.sql.gz); <n> restore table(s) and <m> landing prefix(es) (back from run <stamp>, each table at its backed-up ref only); rebuild table(s) <tables> (not in the run: recreated empty, their products and every product downstream held until make rebuild passes); left as they are: <tables> file product bronze (nothing rebuilds a file product's bronze) and every table outside warehouses k2 and governance. To go ahead: CONFIRM=<id> (the live release)
```

Refused too, before any pause or damage: `the run cannot put back what the drill would destroy (take a
fresh make backup): ...` (something committed or landed since the run, or a rebuild lock open), and
`the drill's damage needs MINIO_ROOT_USER and MINIO_ROOT_PASSWORD in the environment (.env loaded)`, 3a's
key and release refusals, and `the drill destroys rebuild tables that only make rebuild brings back, and
release <id>'s k2-dbt (<image>) takes no --full-refresh (step W1-15a): a rebuild would replace bronze and
then have its build refused; deploy a release whose dbt-runner has it`. With
`CONFIRM`, its own lines are `drill: the drill destroys: ...`, `drill: damage done in <s> s: the catalog
database emptied; <n> location(s) emptied`, then 3a's lines from the catalog replay, then `drill: restore
passed in <s> s (checks, damage and restore <s> s)`. The products it leaves for rebuild are the owners
of the rebuild tables it destroys and everything downstream of them (on the throwaway: kraken-trades,
canonical-trades and candles; on a stack, its statement names the tables). They stay held until
[rebuild-a-product.md](./rebuild-a-product.md) section 2 rebuilds them, and kraken-trades' restage reads
its whole landing, unmeasured at live size.

A stack whose lake-init has not made the restore user (a release before the restore key, or `make
engine-keys` run without the deploy after it) is refused at the key step, before any pause or damage
(finding E1 of the phase 004 audit, resolved in `145a81a`).

**Measured**, throwaway only, 2026-11-28 (`restore-path.sh`): the restore 31.7 s, 57.9 s with the
checks and the damage (claim 10), the restore key first proven in each of its three scopes with a put
the pinned MinIO refused as existing (`412`), nothing written; each restore table registered at the backup's metadata with its rows as
before the damage, every landing object back key for key (claims 11, 12); kraken-trades' bronze recreated
empty, currency-master's spared (13); every product held before any table came back, kraken-trades and
everything downstream kept held with the rebuild order printed (14); the drift check clean (15); then the
three rebuilt in order and `ohlcv_1m` holding every trade it held before (17). Refused with the lake as it
was: without `CONFIRM`, after a commit since the run, with a backup set naming a table the run lacks,
with a release whose lake-init makes no restore user, and with a restore secret MinIO does not hold
(7); a restore with no product held puts nothing back (8); Postgres stopped replaces nothing (9).

### 3c. The releases root is lost

`make restore` never restores the releases root. Extract it by hand into a missing directory only,
never over a live root, from the restore point's run:

```bash
B=${K2_BACKUP_DIR:-$HOME/k2-backup}
RUN=$(python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['restore_point']['run'])" "$B/STATUS.json")
R=${K2_RELEASES:-$HOME/k2-releases}
[ ! -e "$R" ] && tar -xf "$B/$RUN/releases.tar" -C "$(dirname "$R")" && ls "$(dirname "$R")/k2-releases/pointers"
docker compose up -d --force-recreate --no-deps prometheus airflow-scheduler airflow-dag-processor spark-iceberg trino k2-api
make release-verify
```

The tar's top directory is `k2-releases`; a `K2_RELEASES` of another name takes a rename after the
extract.

**Measured**, nothing yet: never run.

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
stack: `python3 scripts/backup_lake.py verify` on an empty scratch directory (the two lines in
section 1), `bash scripts/backup-lake.sh` with a bad flag (its usage line). Section 3a and 3b on a
throwaway lake, 2026-11-28: `bash scripts/verify/restore-path.sh <release dir>`, `19 PASS, 0 FAIL in 671
s`, at `fc353b9`. The backup set above read from the k2gen golden at the phase 004 exit branch. Not yet: `--dry-run`,
`make backup-install` and the timer, every fix in section 2 but the gzip one, and on the live stack
`make restore`, `make restore-drill` and section 3c.
