#!/usr/bin/env bash
# A second copy of what nothing can rebuild, outside Docker, on the host, into
# $K2_BACKUP_DIR (default ~/k2-backup): a pg_dump of the catalog database and of
# Airflow's, then what the live release's compiled/lake/backup-set.json lists (each
# `restore` table at its ref, each landing prefix, the releases root), copied by
# scripts/backup_lake.py in the release's tools image.
#
#   scripts/backup-lake.sh             # `make backup`; the k2-backup.timer user unit runs it daily
#   scripts/backup-lake.sh --dry-run   # what it would copy; copies and dumps nothing
#   scripts/backup-lake.sh --verify    # `make backup-verify`: the newest run against its manifest
#   scripts/backup-lake.sh --dump <postgres container> <database> <file>
#                                      # that one database's dump alone (deploy, before a migration)
#
# The live release is the one pointers/trino names under ${K2_RELEASES} (default
# ~/k2-releases), read once. The copy runs in a throwaway container of that release's
# tools image, as this user, on the catalog's network (no host port), with the backup
# storage key K2_BACKUP_S3_* (read-only on the lake, write nowhere in it), the releases
# root and scripts/ read-only, and $K2_BACKUP_DIR the one writable mount. Each run is
# runs/<UTC stamp>/, written as runs/.<stamp>.partial and renamed only once its
# manifest.json is written; the newest $K2_BACKUP_KEEP runs are kept. STATUS.json
# records the result and the restore point, the last successful run.
#
# What it does not protect against, and the restore steps:
# docs/runbooks/lake-backup-and-restore.md.
set -euo pipefail
set +x   # never trace: the backup storage key is in this script's environment
umask 077

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
KEEP=${K2_BACKUP_KEEP:-14}                    # catalog dumps and runs kept
FLOOR_GIB=${K2_BACKUP_MIN_FREE_GIB:-20}       # refuse to start below this much free space

die() { ERR=$*; echo "backup-lake: FAIL: $*" >&2; exit 1; }
ERR=""

# A database's dump, gzipped, to .partial and kept only when pg_dump finished.
PG=k2-prefect-db  # the Postgres container; --dump names another (a deploy's own project)
dump() {  # dump <database> <file>
  docker exec "$PG" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$1"' sh "$1" | gzip > "$2.partial" ||
    die "pg_dump of database $1 failed"
  [[ $(gzip -dc "$2.partial" | tail -n 5) == *"PostgreSQL database dump complete"* ]] ||
    die "the dump of database $1 is incomplete"
  mv "$2.partial" "$2"
}

MODE=copy
case "${1:-}" in
  --dry-run) MODE=dry ;;
  --verify) MODE=verify ;;
  --dump)
    [ $# -eq 4 ] || { echo "usage: $0 --dump <postgres container> <database> <file>" >&2; exit 2; }
    PG=$2
    mkdir -p "$(dirname "$4")"
    dump "$3" "$4"
    echo "backup-lake: database $3 dumped to $4"
    exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--dry-run | --verify | --dump <postgres container> <database> <file>]" >&2; exit 2 ;;
esac
[[ $KEEP =~ ^[1-9][0-9]*$ ]] || die "K2_BACKUP_KEEP must be a whole number of 1 or more"
[[ $FLOOR_GIB =~ ^[0-9]+$ ]] || die "K2_BACKUP_MIN_FREE_GIB must be a whole number"

# One variable from .env, by name, in a subshell: nothing else in .env reaches
# this script, and the value goes to a variable, never to the terminal.
[ -f "$ROOT/.env" ] || die "no .env in $ROOT"
env_var() { (set +u; . "$ROOT/.env" >/dev/null 2>&1; printf '%s' "${!1:-}"); }

# The target: the environment, then .env (so the timer and `make health` agree),
# then the default. Never inside a checkout: git would see it, `git clean` would
# delete it, and every container that mounts the checkout could reach it.
DIR=${K2_BACKUP_DIR:-$(env_var K2_BACKUP_DIR)}
DIR=$(realpath -m "${DIR:-$HOME/k2-backup}")
main=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
for top in "$ROOT" ${main:+"$(dirname "$main")"}; do
  case "$DIR/" in "$top"/*) die "K2_BACKUP_DIR $DIR is inside the checkout $top; choose a directory outside it" ;; esac
done
[[ $DIR != *[:,]* ]] || die "K2_BACKUP_DIR $DIR holds a colon or a comma, which a docker mount cannot take"

# The live release: pointers/trino -> ../<id>, read once, so both sides of the copy use one id.
RELEASES=${K2_RELEASES:-$(env_var K2_RELEASES)}
RELEASES=$(realpath -m "${RELEASES:-$HOME/k2-releases}")
id=$(readlink "$RELEASES/pointers/trino" 2>/dev/null || true)
id=${id#../}
SET=$RELEASES/$id/compiled/lake/backup-set.json
if [ "$MODE" = verify ]; then
  [[ $id =~ ^[A-Za-z0-9._-]+$ && -f $SET ]] || SET=$RELEASES/pointers/trino/compiled/lake/backup-set.json
  exec python3 "$ROOT/scripts/backup_lake.py" verify --dir "$DIR" --backup-set "$SET"
fi

mkdir -p "$DIR/catalog" "$DIR/runs"
chmod 700 "$DIR"
exec 9>"$DIR/.lock"
flock -n 9 || die "another backup-lake run holds $DIR/.lock"

STARTED=$(date -u +%FT%TZ)
T0=$SECONDS
ts=$(date -u +%Y%m%dT%H%M%SZ)
DUMP_lakekeeper="" DUMP_airflow=""

# STATUS.json, on every exit after this point: this run's result, and the restore
# point, the last successful run's manifest summary and dumps. A failed run keeps the
# previous restore point: its copy may be incomplete, so a restore must not use it.
write_status() {
  local rc=$?
  if RC=$rc ERR=$ERR DIR=$DIR STARTED=$STARTED DURATION=$((SECONDS - T0)) RUN=runs/$ts \
    DUMP_lakekeeper=$DUMP_lakekeeper DUMP_airflow=$DUMP_airflow python3 - <<'PY'
import json, os
from datetime import datetime, timezone

d = os.environ["DIR"]
path = os.path.join(d, "STATUS.json")
try:
    with open(path) as f:
        prev = json.load(f)
except (OSError, ValueError):
    prev = {}
rc = int(os.environ["RC"])
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
point = None
if rc == 0:
    with open(os.path.join(d, os.environ["RUN"], "manifest.json")) as f:
        m = json.load(f)
    tables = {t["table"]: {k: t[k] for k in ("location", "metadata_location", "snapshot_id", "ref", "objects", "bytes")}
              for t in m["tables"]}
    files = [*m["tables"], *m["landings"]]
    point = {"utc": now, "run": os.environ["RUN"], "manifest": os.environ["RUN"] + "/manifest.json",
             "release": m["release"], "tables": tables, "dumps": m["dumps"],
             "objects_total": sum(x["objects"] for x in files), "bytes_total": sum(x["bytes"] for x in files),
             "objects_copied": m["objects_copied"], "bytes_copied": m["bytes_copied"]}
status = {
    "status": "ok" if rc == 0 else "failed",
    "exit_status": rc,
    "error": None if rc == 0 else (os.environ["ERR"] or f"exit status {rc}: see the output above"),
    "started_utc": os.environ["STARTED"],
    "finished_utc": now,
    "duration_s": int(os.environ["DURATION"]),
    "last_success_utc": now if rc == 0 else prev.get("last_success_utc"),
    "backup_dir": d,
    "restore_point": point if rc == 0 else prev.get("restore_point"),
}
with open(path + ".tmp", "w") as f:
    json.dump(status, f, indent=2)
os.replace(path + ".tmp", path)
if rc == 0:
    print(f"backup-lake: OK in {status['duration_s']} s: {point['objects_copied']} files, "
          f"{point['bytes_copied']} bytes fetched this run; {point['objects_total']} files, "
          f"{point['bytes_total']} bytes in {point['run']}; {path}")
PY
  then :; else echo "backup-lake: FAIL: could not write $DIR/STATUS.json" >&2; exit 1; fi
}
[ "$MODE" = dry ] || trap write_status EXIT

# Free space on the target's filesystem, before anything is written to it.
free=$(df -B1 --output=avail "$DIR" | tail -n 1)
[ "$free" -ge $((FLOOR_GIB * 1024 ** 3)) ] ||
  die "$DIR has $((free / 1024 ** 3)) GiB free, under the floor of $FLOOR_GIB GiB (K2_BACKUP_MIN_FREE_GIB)"

# (1) The catalog database, then Airflow's, before the copy: a MinIO or catalog
# outage below never costs the night's dumps.
dumps=(--dry-run)
if [ "$MODE" = copy ]; then
  DUMP_lakekeeper=$DIR/catalog/$ts.sql.gz
  dump lakekeeper "$DUMP_lakekeeper"
  DUMP_airflow=$DIR/catalog/$ts.airflow.sql.gz
  dump airflow "$DUMP_airflow"
  dumps=(--dump "lakekeeper=catalog/$ts.sql.gz" --dump "airflow=catalog/$ts.airflow.sql.gz")
fi

[[ $id =~ ^[A-Za-z0-9._-]+$ && -f $SET && -f $RELEASES/$id/manifest.json ]] ||
  die "no live release: $RELEASES/pointers/trino does not name a release with compiled/lake/backup-set.json"

# (2) The copy, in a throwaway container of the live release's tools image on the
# catalog's network, where Lakekeeper and MinIO both are. The key reaches it as two
# environment variables, by name, never as an argument.
image=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["images"]["tools"])' "$RELEASES/$id/manifest.json") ||
  die "the release $id names no tools image"
net=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' k2-minio 2>/dev/null) ||
  die "k2-minio is not running"
cat_net=$(grep -o '[^ ]*_k2-catalog' <<<"$net" || true)
net=${cat_net:-${net%% *}}
K2_BACKUP_S3_ACCESS_KEY=$(env_var K2_BACKUP_S3_ACCESS_KEY)
K2_BACKUP_S3_SECRET_KEY=$(env_var K2_BACKUP_S3_SECRET_KEY)
[ -n "$K2_BACKUP_S3_ACCESS_KEY" ] && [ -n "$K2_BACKUP_S3_SECRET_KEY" ] ||
  die "K2_BACKUP_S3_ACCESS_KEY or K2_BACKUP_S3_SECRET_KEY is not set in .env (make engine-keys)"
export K2_BACKUP_S3_ACCESS_KEY K2_BACKUP_S3_SECRET_KEY
docker run --rm --network "$net" --user "$(id -u):$(id -g)" --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges:true \
  -e K2_BACKUP_S3_ACCESS_KEY -e K2_BACKUP_S3_SECRET_KEY \
  -v "$RELEASES:/k2/releases:ro" -v "$ROOT/scripts:/opt/k2/scripts:ro" -v "$DIR:/backup" \
  "$image" python3 /opt/k2/scripts/backup_lake.py copy --release "/k2/releases/$id" --dir /backup --run "$ts" \
  "${dumps[@]}" || die "the lake copy did not complete (runs/$ts was not written)"
[ "$MODE" = copy ] || exit 0
[ -f "$DIR/runs/$ts/manifest.json" ] || die "the copy exited 0 but runs/$ts/manifest.json is not there"

# Old dumps and runs go only now, after the copy held: a run that fails above never
# removes what the last good restore point names. The UTC names sort by time.
prune() {  # prune <dir> <glob>: keep the newest $KEEP
  local files=()
  shopt -s nullglob
  files=("$1"/$2)
  shopt -u nullglob
  [ "${#files[@]}" -le "$KEEP" ] || rm -rf -- "${files[@]:0:${#files[@]}-KEEP}"
}
prune "$DIR/catalog" '*Z.sql.gz'
prune "$DIR/catalog" '*Z.airflow.sql.gz'
prune "$DIR/runs" '[0-9]*Z'
