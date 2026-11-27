# Runbook: Back up the lake's irreplaceable tables, and restore them

`scripts/backup-lake.sh` (`make backup`, and nightly once `make backup-install` has run) keeps a
second copy of what nothing can rebuild in a directory on the host, outside Docker. This page
says what that copy holds and what it does not, how to run, schedule and check it, and how to
restore from it. It does not cover the ClickHouse hot tier, which is paused
([pause-and-resume-a-serving-product.md](./pause-and-resume-a-serving-product.md)).

The catalog has no host port since 2026-11-27 (it has no authentication, so its network is its
fence: [ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)
part 7). The script asks it from a throwaway `minio/minio` container running `curl` on
`<project>_k2-catalog`, the network it finds among `k2-minio`'s (MinIO's only network before the
cutover to releases), at `http://lakekeeper:8181/catalog/v1`, and copies with `mc` from a
throwaway container on the same network. The `pg_dump`s run through `docker exec k2-prefect-db`.

> **The four tables it copied retired with the v3 lake ingest pipeline on 2026-11-26** and were
> dropped with that lake's namespaces ([`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/),
> [ADR-056](../adr/ADR-056-unified-data-product-platform.md)). `TABLES` at the top of the script
> is empty since, so `make backup` copies no table and prints a SKIP line naming
> [phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md), which
> compiles the backup set from each product's contract and writes the runbook for backing up
> and restoring a file product. The copies taken before stay on the host, unpruned.

**Run every command from the repo root of the live checkout with `set -a && . ./.env && set +a` loaded.**

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | First copy | < 40 min | 1 min 41 s for 25.3 GiB (27,176,482,035 bytes), 2026-11-26 04:33:53 to 04:35:34 UTC, from a worktree, the stack live and the ingest running |
| 2 | Nightly copy | < 5 min | not yet measured: a dry run a minute after the first copy listed only the files of the one ingest commit made since, and none that differ |
| 3 | Restore | none yet | **not yet drilled**: no restore has been run |

---

## What is copied, and what is not

Into `$K2_BACKUP_DIR` (default `~/k2-backup`):

| Path | What |
|------|------|
| `lake/<table>/` | Each table's Iceberg files: every object under the table's location in MinIO, its `data/` and `metadata/`, at the same relative keys. Today the four tables of the retired v3 lake as last copied before the drop, on 2026-11-26: the raw archive ([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)), the audit history and the two type-2 dimensions, all retired with it. The list is `TABLES` at the top of the script, empty until phase 002 |
| `catalog/<UTC time>.sql.gz` | `pg_dump` of the `lakekeeper` database, the catalog: the pointer from each table name to its current metadata file, from each run that took one. The newest 14 are kept (`K2_BACKUP_KEEP`) |
| `catalog/<UTC time>.airflow.sql.gz` | `pg_dump` of Airflow's metadata database, run history and pause states. The newest 14 |
| `STATUS.json` | The last run's result, and `restore_point`: for each table its location and the metadata file to register, and the two dump files, all from the last run that **succeeded** |
| `drop/<date>/` | Not this script's: the copies `make drop` takes before it drops ([drop-and-rebuild.md](./drop-and-rebuild.md)), the v3 lake's namespaces among them |

The copy is additive: an Iceberg file is written once under a unique key, so a file already in
the copy is never replaced or deleted. An object in MinIO whose bytes differ from its copy stops
the run instead of overwriting it. Files a compaction replaced and snapshot expiry then deleted
from MinIO stay in the copy, so the copy only grows: the first copy, 25.3 GiB for the raw
archive table (since retired), was larger than its 13.7 GiB of data files in the current snapshot,
because the copy takes every object under the table's location, the files of snapshots not yet
expired included. Nothing prunes it yet; the 20 GiB free-space floor (`K2_BACKUP_MIN_FREE_GIB`)
stops a run before it fills the disk.

**Not copied**, on purpose:

- the rest of the retired v3 lake, its other layers and marts: each was derived from the raw
  archive and rebuilt from it, not restored.
- ClickHouse, Redpanda, Grafana, Prometheus, and the retired scheduler's database.
- `.env`. Keep your own copy somewhere else: the catalog stores the warehouses' storage
  credential encrypted with `LAKEKEEPER_ENCRYPTION_KEY`, so a restored catalog dump needs the
  same key.

**What this protects against:** a lost or wiped Docker volume (`minio-data` or `postgres-data`;
the engine's volumes were lost once, on 2026-08-28, [fresh-install.md](./fresh-install.md)), a
bad command that deletes objects, a dropped or broken table, and a lost catalog database.

**What it does not protect against, plainly:**

- **The disk.** The copy is on the same physical disk as the Docker disk image. A failed disk,
  a stolen or destroyed machine, loses both.
- **Anything running as you.** The copy is ordinary files in your home directory. Any process
  running as your user can delete or rewrite it, and so can any container holding Docker
  Desktop's socket (the Airflow scheduler and cAdvisor mount it), because Docker Desktop shares
  your home directory with its VM. The same is true of the script and the systemd units it runs
  from, which live in the checkout and in `~/.config`.
- **A bad copy.** Nothing checksums the files. The run checks only that each table's current
  metadata file arrived; a file damaged on disk afterwards is not noticed until a restore.
- **The last day.** The copy is up to a day old (daily at 04:30 UTC, plus the run); what
  changed after it is not in it.

A fuller design (a dedicated system user, copies every six hours, checksums, restore drills,
scoped storage keys) is parked for the hardening sprint and is not built.

---

## 1. Run, schedule and check the copy

```bash
# Once, by hand. Read-only against the stack. Until phase 002 it copies no table: its SKIP line
# names that phase.
make backup

# What it would copy, without copying: '<' is new, '!' differs and would stop the run.
bash scripts/backup-lake.sh --dry-run

# Schedule it: daily at 04:30 UTC. Run this from the main checkout, not a worktree: the unit
# runs the script from the directory it was installed from.
make backup-install
systemctl --user list-timers k2-backup.timer      # NEXT is the next 04:30 UTC

# Check it. `make health` step (5) is PASS, WARN (the last run failed, the last success is
# under 26 h old) or FAIL (no success for 26 h), and SKIP on a host where the timer is not installed.
make health
python3 -m json.tool ~/k2-backup/STATUS.json | head -20
journalctl --user -u k2-backup --since yesterday   # a timer run's output
```

The two `make backup` commands are written for the script as the retirement leaves it; they were
run, and printed a line per table, against the four tables on 2026-11-26 before it. The timer
was not installed on this host on 2026-11-26 (`list-timers` printed `0 timers listed.`), so
`make health` step (5) printed its SKIP; `STATUS.json` read back the last copy before the drop
(15:00 UTC, status `ok`, the four tables).

- **Another directory:** set `K2_BACKUP_DIR` in `.env`, so the timer and `make health` read the
  same one. It must be outside the checkout (the script refuses otherwise) and in a directory
  Docker Desktop shares with its VM, your home directory by default; elsewhere `docker run`
  fails with `mounts denied`.
- **A user timer runs only while you are logged in.** `Persistent=true` runs a missed copy at
  the next login. Docker Desktop must be running too, or the run fails and step (5) shows it.
- **Remove it:** `systemctl --user disable --now k2-backup.timer && rm ~/.config/systemd/user/k2-backup.{service,timer}`.

---

## 2. The nightly copy failed or is stale

**Symptom**, `make health` step (5) prints `WARN (the last run failed: ...)` or
`FAIL (last successful backup <n>h ago, over 26h ...)`.

**Detection**, manual only: `make health`. No Prometheus alert covers this, and none routes
anywhere ([ADR-028](../adr/ADR-028-non-goals-and-honest-limits.md)).

**Expected behaviour**, nothing retries before the next 04:30 UTC. A failed run leaves
`restore_point` at the last successful run, so the copy a restore would use is never a partial
one.

**Recovery**

```bash
# 1. Why: the error of the last run, and the timer run's output.
python3 -c "import json, os; s = json.load(open(os.path.expanduser('~/k2-backup/STATUS.json'))); print(s['status'], s['finished_utc'], s['error'])"
journalctl --user -u k2-backup -n 30
```

| Error | Fix |
|-------|-----|
| `... GiB free, under the floor of 20 GiB` | Free space on that disk (`df -h ~`; a bloated Docker disk image is [docker-desktop-disk.md](./docker-desktop-disk.md)) |
| `k2-minio is not running`, `the catalog did not answer` | The stack is down: start it ([operator guide](../operations/README.md), section 2) |
| `pg_dump of database ... failed` | `docker ps --filter name=k2-prefect-db`: the database container is down |
| `another backup-lake run holds ... .lock` | A run is in progress: `systemctl --user status k2-backup.service` |
| `mounts denied` | `K2_BACKUP_DIR` is not shared with Docker Desktop; move it under your home directory |
| `object(s) differ from the copy` | An object key's bytes changed, which Iceberg never does, or a file in the copy is damaged. Do not overwrite: the script printed each key. Compare the two sizes (`ls -l` on the copy, `mc stat` in MinIO); a copy truncated by a killed run can be deleted from the copy and copied again, anything else is an incident to investigate first |

```bash
# 2. Run it again by hand, and check step (5) is PASS.
make backup && make health
```

**Measured**, not induced; the error paths are exercised by `tests/test_backup_lake.py`.

---

## 3. Restore from the copy

> **Not yet drilled; a drill is phase 004's** (its first restore drill). Every step below is
> written from the Lakekeeper 0.13.9 catalog OpenAPI
> (`/api-docs/catalog/v1/openapi.json`, read on the host port 18181 the catalog lost on 2026-11-27;
> it is now served only to containers on `k2-catalog`) and the copy's layout; no restore
> has been run. Read the whole section before starting, and stop at the first surprise.

**3a. A table: not until phase 002.** No table is in the backup set, so there is none to restore.
The copies of the four retired tables stay on disk as they were; registering one would bring a
retired table back into the live catalog, which phase 000 removed on purpose. The steps that
restored them, a `mc mirror` back to the table's keys and a register of its metadata file, are
in git history and in [drop-and-rebuild.md](./drop-and-rebuild.md) section 3, which restores a
table from the copy `make drop` took.

**3b. The catalog database is lost; MinIO is intact.**

**Symptom**, Lakekeeper fails at start or lists no warehouse; Trino's catalogs fail.

**Detection**, manual: `make health` step (1) (`lakekeeper` unhealthy). No alert covers it.

**Expected behaviour**, nothing restores itself.

> **A dump taken before the v3 lake's namespaces were dropped (2026-11-26) still lists the
> dropped tables**, whose files the drop purged. Restore only a later dump (`ls ~/k2-backup/catalog/`).
> While the catalog holds no table (until phase 002) there is a simpler path: an empty
> `lakekeeper` database (step 3's first line), then `docker compose up -d --force-recreate
> --no-deps lakekeeper-migrate lakekeeper lake-init`, which recreates the schema and then the
> warehouses of the storage plan in the release the trino pointer names
> (`compiled/lake/storage-plan.json`, read by `docker/lake/init-lake.sh`). **Not run.**

```bash
# 1. The restore point: the dump files of the last successful run.
python3 -c "
import json, os
p = json.load(open(os.path.expanduser('~/k2-backup/STATUS.json')))['restore_point']
print('taken', p['utc'])
for db, d in p['dumps'].items(): print(db, d['file'])"

# 2. Stop the catalog, and confirm the database really is gone or empty before replacing it:
#    a live catalog prints its warehouses here, and then this is not the section to follow.
docker compose stop lakekeeper
docker exec k2-prefect-db sh -c 'psql -U "$POSTGRES_USER" -d lakekeeper -Atc "SELECT warehouse_name FROM warehouse"'

# 3. Replace it with the restore point's dump (the file from step 1), then start the catalog.
docker exec k2-prefect-db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists lakekeeper && createdb -U "$POSTGRES_USER" lakekeeper'
gzip -dc ~/k2-backup/catalog/<UTC time>.sql.gz \
  | docker exec -i k2-prefect-db sh -c 'psql -U "$POSTGRES_USER" -d lakekeeper -v ON_ERROR_STOP=1 -q'
docker compose start lakekeeper
```

The dump's pointers are as old as the copy. Airflow's database restores the same way, as
database `airflow`, owner `airflow` (`createdb -U "$POSTGRES_USER" -O airflow airflow`), with
`airflow-scheduler`, `airflow-dag-processor` and `airflow-api-server` stopped. Nothing resumes
afterwards: no job writes the lake until phase 002.

**Measured**, nothing yet: not drilled.

---

## Failure modes / incidents

- None yet.

---

**Last verified:** 2026-11-26 against `410cc13`, on the live stack: the script itself (`make
backup` runs it) once, from a worktree, and the dry run before and after it, both over the four
tables before they retired; in section 3, the read-only commands only. Rewritten on 2026-11-26
for the retirement: run read-only then, section 3b's step 1, the `warehouse` query, `list-timers`,
`STATUS.json` and `journalctl --user -u k2-backup`. The units were checked with
`systemd-analyze --user verify`, not installed. Not run: `make backup-install`, `make backup`
with the empty `TABLES` (written for the after-state), section 2's fixes, and every step of
section 3 that writes.
