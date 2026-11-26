# Runbook: Back up the lake's irreplaceable tables, and restore them

`scripts/backup-lake.sh` (`make backup`, and nightly once `make backup-install` has run) keeps a
second copy of what nothing can rebuild in a directory on the host, outside Docker. This page
says what that copy holds and what it does not, how to run, schedule and check it, and how to
restore from it. It does not cover rebuilding the derived lake layers, which is
[lake-recovery.md](./lake-recovery.md) and `make lake-rebuild`, nor the ClickHouse hot tier,
which is [clickhouse-rebuild-from-lake.md](./clickhouse-rebuild-from-lake.md).

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
| `lake/raw.messages/`, `lake/audit.checks/`, `lake/gold.dim_instrument/`, `lake/gold.dim_venue/` | Each table's Iceberg files: every object under the table's location in MinIO, its `data/` and `metadata/`, at the same relative keys. The raw archive ([ADR-021](../adr/ADR-021-raw-first-archive-and-lineage.md)), the audit history, and the two type-2 dimensions whose history has no source. The list is `TABLES` at the top of the script |
| `catalog/<UTC time>.sql.gz` | `pg_dump` of the `lakekeeper` database, the catalog: the pointer from each table name to its current metadata file. The newest 14 are kept (`K2_BACKUP_KEEP`) |
| `catalog/<UTC time>.airflow.sql.gz` | `pg_dump` of Airflow's metadata database, run history and pause states. The newest 14 |
| `STATUS.json` | The last run's result, and `restore_point`: for each table its location and the metadata file to register, and the two dump files, all from the last run that **succeeded** |

The copy is additive: an Iceberg file is written once under a unique key, so a file already in
the copy is never replaced or deleted. An object in MinIO whose bytes differ from its copy stops
the run instead of overwriting it. Files the nightly compaction replaced and snapshot expiry then
deleted from MinIO stay in the copy, so the copy only grows. The first copy was larger than
the live table: 25.3 GiB for `raw.messages` against 13.7 GiB of data files in its current
snapshot (Trino, `SELECT sum(file_size_in_bytes) FROM lake.raw."messages$files"`, 2026-11-26
04:41 UTC), because object storage still holds the files of snapshots not yet expired, and
the copy takes every object under the table's location. Nothing prunes it yet; the 20 GiB free-space floor (`K2_BACKUP_MIN_FREE_GIB`)
stops a run before it fills the disk.

**Not copied**, on purpose:

- bronze, silver, gold (except the two dimensions above), the books, the bars and the dbt marts.
  Each is a function of `raw.messages` and is rebuilt from it, not restored.
- each data domain's `dq.dbt_test_results`, the history of dbt test outcomes. A lost MinIO
  volume loses that history.
- ClickHouse, Redpanda, Grafana, Prometheus, and the legacy Prefect database.
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
- **The last day.** The copy is up to a day old (daily at 04:30 UTC, plus the run). A restore
  gets back what Redpanda still holds after it, 48 hours or 2 GiB per raw partition, whichever
  comes first (`docker/redpanda/init.sh`); anything captured after the copy and already evicted
  is gone.

A fuller design (a dedicated system user, copies every six hours, checksums, restore drills,
scoped storage keys) is parked for the hardening sprint and is not built.

---

## 1. Run, schedule and check the copy

```bash
# Once, by hand. Read-only against the stack; prints a line per table and a summary.   ✅ verified
make backup
#   backup-lake: raw.messages: <n> files, <bytes> bytes copied; <n> files, <bytes> bytes held
#   ...
#   backup-lake: OK in <s> s: ... ; /home/<you>/k2-backup/STATUS.json

# What it would copy, without copying: '<' is new, '!' differs and would stop the run.   ✅ verified
bash scripts/backup-lake.sh --dry-run

# Schedule it: daily at 04:30 UTC, after the 03:00 UTC lake_maintenance. Run this from the
# main checkout, not a worktree: the unit runs the script from the directory it was installed from.
make backup-install
systemctl --user list-timers k2-backup.timer      # NEXT is the next 04:30 UTC

# Check it. `make health` step (5) is PASS, WARN (the last run failed, the last success is
# under 26 h old) or FAIL (no success for 26 h), and SKIP on a host where the timer is not installed.
make health
python3 -m json.tool ~/k2-backup/STATUS.json | head -20
journalctl --user -u k2-backup --since yesterday   # a timer run's output
```

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
| `... GiB free, under the floor of 20 GiB` | Free space on that disk, [lake-disk-usage-high.md](./lake-disk-usage-high.md) for the host side |
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

> **Not yet drilled; a drill on a scratch stack is planned in the hardening sprint.** Every
> step below is written from the Lakekeeper 0.13.9 catalog OpenAPI
> (`http://127.0.0.1:18181/api-docs/catalog/v1/openapi.json`) and the copy's layout; no restore
> has been run. Read the whole section before starting, and stop at the first surprise.

**Symptom**, a table's files are gone from MinIO, a table was dropped, or the catalog database
is lost: Trino or the ingest fails on a missing metadata or data file, or the catalog no longer
lists the table.

**Detection**, `LakeIngestFailed`, `LakeScrapeErrors` or `LakeExporterStalled` usually fire
first ([lake-recovery.md](./lake-recovery.md)); a dropped table is otherwise manual.

**Expected behaviour**, nothing restores itself. Worse: `lake-ddl` runs on every
`docker compose up` and creates a missing table again, empty, which the ingest would start
filling from Redpanda's oldest record. Stop the writers before anything else.

**Recovery**

```bash
# 0. Stop the writers: pause all four DAGs and wait for idle, exactly as the operator guide's
#    section 6 steps 0 to 2 (docs/operations/README.md). Do not `docker compose up` until step 3.

# 1. The restore point: per table, the location to copy back to and the metadata file to register.
python3 -c "
import json, os
p = json.load(open(os.path.expanduser('~/k2-backup/STATUS.json')))['restore_point']
print('taken', p['utc'])
for t, r in p['tables'].items(): print(t, r['location'], r['metadata_location'])
for db, d in p['dumps'].items(): print(db, d['file'])"
```

**3a. The table's files are gone from MinIO, or the table was dropped; the catalog database is
intact.** Put the files back at their own keys (every path inside Iceberg metadata is absolute),
then point the catalog at the restore point's metadata file.

```bash
# 2. Copy one table back. Without --overwrite, mc never replaces an object already in MinIO.
T=raw.messages
LOC=$(python3 -c "import json, os, sys; print(json.load(open(os.path.expanduser('~/k2-backup/STATUS.json')))['restore_point']['tables'][sys.argv[1]]['location'])" "$T")
NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' k2-minio)
docker run --rm --network "$NET" -e MC_CONFIG_DIR=/tmp/.mc -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD \
  -v "$HOME/k2-backup/lake:/backup:ro" --entrypoint /bin/bash minio/minio:RELEASE.2025-09-07T16-13-09Z -c \
  'mc alias set k2 http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null &&
   mc mb --ignore-existing k2/k2-lake && mc mirror "/backup/$0" "k2/$1"' "$T" "${LOC#s3://}"

# 3. Register the restore point's metadata file under the table's name. "overwrite": true
#    replaces the pointer when the catalog still lists the name (a broken table, or the empty
#    one lake-ddl made); false when it does not. 409 means the name exists: use true.
META=$(python3 -c "import json, os, sys; print(json.load(open(os.path.expanduser('~/k2-backup/STATUS.json')))['restore_point']['tables'][sys.argv[1]]['metadata_location'])" "$T")
P=$(curl -s "localhost:18181/catalog/v1/config?warehouse=k2" | python3 -c "import sys, json; print(json.load(sys.stdin)['defaults']['prefix'])")
curl -sS -X POST "localhost:18181/catalog/v1/$P/namespaces/${T%%.*}/register" \
  -H 'content-type: application/json' \
  -d "{\"name\": \"${T#*.}\", \"metadata-location\": \"$META\", \"overwrite\": true}" \
  | python3 -c "import json, sys; d = json.load(sys.stdin); print(d.get('metadata-location') or d)"
#   prints the metadata file just registered; a namespace that is gone is a 404, create it
#   first: curl -sS -X POST "localhost:18181/catalog/v1/$P/namespaces" -d '{"namespace": ["raw"]}'
```

> **Never drop a table to make room for the register without `?purgeRequested=false`.**
> Lakekeeper treats a drop that omits it as a purge and deletes the table's files, which after
> step 2 are the restored files (its OpenAPI says so for `DELETE .../tables/{table}`).

```bash
# 4. Verify: the catalog points at the restore point, and Trino reads the table.
curl -s "localhost:18181/catalog/v1/$P/namespaces/${T%%.*}/tables/${T#*.}" | python3 -c "import json, sys; print(json.load(sys.stdin)['metadata-location'])"
#   the same file as $META
docker exec k2-trino trino --user reviewer --execute 'SELECT max(committed_at) FROM lake.raw."messages$snapshots"'
#   the table's newest commit time; after a restore, at or before restore_point.utc
```

Repeat steps 2 to 4 for each table in `TABLES` that was lost.

**3b. The catalog database is lost; MinIO is intact.**

```bash
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

The dump's pointers are as old as the copy. Every commit since is still in MinIO, but no table
points at it: each table reads as it was at the copy. For the tables in `TABLES`, register the
newest metadata file in MinIO instead with step 3 of 3a (list them with
`mc ls k2/k2-lake/warehouse/k2/<table uuid>/metadata/`); the highest version number is
normally the newest commit, but a failed commit can leave a file the catalog never accepted, so
check its `metadata-log` ends with the file before it. The derived tables are rebuilt in step 5.
Airflow's database restores the same way, as database `airflow`, owner `airflow`
(`createdb -U "$POSTGRES_USER" -O airflow airflow`), with `airflow-scheduler`,
`airflow-dag-processor` and `airflow-api-server` stopped.

**What happens next, and what is lost** (3a and 3b):

```bash
# 5. Unpause the DAGs (operator guide section 6 step 5).
```

The ingest resumes from the offsets recorded in the restored snapshot (`k2.kafka-offsets`) and
re-reads from Redpanda everything after them that Redpanda still holds. If Redpanda has already
evicted part of that window, it stops with `failOnDataLoss` and the evicted ranges are recorded
and skipped with `--accept-data-loss`, [lake-ingest-lag.md](./lake-ingest-lag.md) section 3.
**What is lost is every record captured after the restore point that Redpanda no longer
retains**, recorded as `offset_gap` rows in `lake.audit.checks`. The derived layers recorded raw
snapshot ids the restored table may not have, and after a lost MinIO volume their files are
gone too: rebuild them, `make lake-rebuild LAYER=bronze`, then `silver`, `gold`, `books` and
`bars`, then ClickHouse from the lake.

**Measured**, nothing yet: not drilled.

---

## Failure modes / incidents

- None yet.

---

**Last verified:** 2026-11-26 against `410cc13` plus this change, on the live stack. Run: the
script itself (`make backup` runs it) once, from a worktree, and the dry run before and after it;
in section 3, the read-only commands only (step 1, the catalog GETs, the Trino read, the
`warehouse` query). The units were checked with `systemd-analyze --user verify`, not installed.
Not run: `make backup-install`, section 2's fixes, and every step of section 3 that writes.
