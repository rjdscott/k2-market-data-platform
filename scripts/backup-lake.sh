#!/usr/bin/env bash
# A second copy of what nothing can rebuild, outside Docker, on the host:
# each table in TABLES as its own Iceberg files (its data/ and metadata/
# objects, mirrored from MinIO), plus a pg_dump of the catalog database and of
# Airflow's, into $K2_BACKUP_DIR (default ~/k2-backup).
#
#   scripts/backup-lake.sh             # `make backup`; the k2-backup.timer user unit runs it daily
#   scripts/backup-lake.sh --dry-run   # list what it would copy; writes only empty directories
#
# Read-only against the stack: GETs to the catalog on its loopback port, reads
# from MinIO through a throwaway `mc` container on the compose network, and
# pg_dump through `docker exec`. Additive only: an Iceberg object is written
# once under a unique key and never changed, so a file already in the copy is
# never replaced or removed, and an object whose content differs from its copy
# stops the run (mc refuses to overwrite; this script turns that into a failure).
# A re-run copies only what is new. STATUS.json records the result and the
# restore point, the metadata file per table that a restore registers.
#
# What it does not protect against, and the restore steps:
# docs/runbooks/lake-backup-and-restore.md.
set -euo pipefail
set +x   # never trace: the MinIO credential is in this script's environment
umask 077

# The tables nothing can rebuild, <namespace>.<table> in warehouse k2: the raw
# archive (ADR-021, the system of record), the audit history, and the two SCD2
# dimensions whose history has no source (docker/lake/ddl/lake.sql). Every other
# lake table is a function of raw.messages and is rebuilt, not restored.
# ponytail: warehouse k2 only; the per-domain dq.dbt_test_results are not copied.
TABLES="raw.messages audit.checks gold.dim_instrument gold.dim_venue"

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
CATALOG=http://127.0.0.1:18181/catalog/v1
KEEP=${K2_BACKUP_KEEP:-14}                    # catalog dumps kept, per database
FLOOR_GIB=${K2_BACKUP_MIN_FREE_GIB:-20}       # refuse to start below this much free space

die() { ERR=$*; echo "backup-lake: FAIL: $*" >&2; exit 1; }
ERR=""

DRY=0
case "${1:-}" in
  --dry-run) DRY=1 ;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
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

MINIO_ROOT_USER=$(env_var MINIO_ROOT_USER)
MINIO_ROOT_PASSWORD=$(env_var MINIO_ROOT_PASSWORD)
[ -n "$MINIO_ROOT_USER" ] && [ -n "$MINIO_ROOT_PASSWORD" ] || die "MINIO_ROOT_USER or MINIO_ROOT_PASSWORD is not set in .env"
# Exported for mc_run's `-e NAME`, which takes the value from this environment.
export MINIO_ROOT_USER MINIO_ROOT_PASSWORD

mkdir -p "$DIR/lake" "$DIR/catalog"
chmod 700 "$DIR"
exec 9>"$DIR/.lock"
flock -n 9 || die "another backup-lake run holds $DIR/.lock"

STARTED=$(date -u +%FT%TZ)
T0=$SECONDS
ROWS=()            # name|location|metadata location|objects|bytes|objects this run|bytes this run
DUMP_lakekeeper="" DUMP_airflow=""

# STATUS.json, on every exit after this point: this run's result, and the restore
# point, the last successful run's tables and dumps. A failed run keeps the
# previous restore point: its copy may be incomplete, so a restore must not use it.
write_status() {
  local rc=$?
  if RC=$rc ERR=$ERR DIR=$DIR STARTED=$STARTED DURATION=$((SECONDS - T0)) \
    DUMP_lakekeeper=$DUMP_lakekeeper DUMP_airflow=$DUMP_airflow python3 - "${ROWS[@]}" <<'PY'
import json, os, sys
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
tables = {}
for row in sys.argv[1:]:
    name, loc, meta, n, b, dn, db = row.split("|")
    tables[name] = {"location": loc, "metadata_location": meta, "objects_total": int(n),
                    "bytes_total": int(b), "objects_this_run": int(dn), "bytes_this_run": int(db)}
dumps = {}
for db in ("lakekeeper", "airflow"):
    f = os.environ.get(f"DUMP_{db}")
    if f:
        dumps[db] = {"file": os.path.relpath(f, d), "bytes": os.path.getsize(f)}
point = {"utc": now, "tables": tables, "dumps": dumps,
         **{k: sum(t[k] for t in tables.values())
            for k in ("objects_total", "bytes_total", "objects_this_run", "bytes_this_run")}}
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
    print(f"backup-lake: OK in {status['duration_s']} s: {point['objects_this_run']} files, "
          f"{point['bytes_this_run']} bytes copied this run; {point['objects_total']} files, "
          f"{point['bytes_total']} bytes held; {path}")
PY
  then :; else echo "backup-lake: FAIL: could not write $DIR/STATUS.json" >&2; exit 1; fi
}
[ "$DRY" = 1 ] || trap write_status EXIT

# Free space on the target's filesystem, before anything is written to it.
free=$(df -B1 --output=avail "$DIR" | tail -n 1)
[ "$free" -ge $((FLOOR_GIB * 1024 ** 3)) ] ||
  die "$DIR has $((free / 1024 ** 3)) GiB free, under the floor of $FLOOR_GIB GiB (K2_BACKUP_MIN_FREE_GIB)"

# mc <args>, in a throwaway container on k2-minio's network, as this user so the
# files are ours, with $DIR/lake at /backup. The credential reaches it as two
# environment variables; the alias is set inside, from them.
net=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' k2-minio 2>/dev/null) ||
  die "k2-minio is not running"
net=${net%% *}
mc_run() {
  docker run --rm --network "$net" --user "$(id -u):$(id -g)" --cpus 1 --memory 512m \
    -e MC_CONFIG_DIR=/tmp/.mc -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD -v "$DIR/lake:/backup" \
    --entrypoint /bin/bash minio/minio:RELEASE.2025-09-07T16-13-09Z -c \
    'mc alias set k2 http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 ||
       { echo "mc: MinIO did not accept the credential or did not answer" >&2; exit 1; }
     exec mc "$@"' mc "$@"
}

# (1) Each table's location and current metadata file, from the catalog: read
# first, so every file that metadata names already exists when the copy starts.
prefix=$(curl -fsS "$CATALOG/config?warehouse=k2" 2>/dev/null |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["defaults"]["prefix"])' 2>/dev/null) ||
  die "the catalog did not answer on 127.0.0.1:18181 (is k2-lakekeeper up?)"
declare -A LOC META
for t in $TABLES; do
  # The response is never printed: its config block can carry storage settings.
  read -r loc meta < <(curl -fsS "$CATALOG/$prefix/namespaces/${t%%.*}/tables/${t#*.}" 2>/dev/null |
    python3 -c 'import json, sys; d = json.load(sys.stdin); print(d["metadata"]["location"], d["metadata-location"])' 2>/dev/null) ||
    die "the catalog has no table $t"
  # An unauthenticated catalog's answer decides what is read: keep it in the lake's bucket.
  [[ $loc == s3://k2-lake/warehouse/* && $loc != *..* && $meta == "$loc"/metadata/* ]] ||
    die "$t: the catalog gave a location outside s3://k2-lake/warehouse/"
  LOC[$t]=$loc META[$t]=$meta
done

if [ "$DRY" = 1 ]; then
  for t in $TABLES; do
    mkdir -p "$DIR/lake/$t"
    echo "== $t: ${LOC[$t]} -> $DIR/lake/$t (current metadata ${META[$t]##*/})"
    echo "   '<' would be copied; '!' differs from the copy and would stop the run"
    mc_run diff "k2/${LOC[$t]#s3://}" "/backup/$t"
  done
  exit 0
fi

# (2) The catalog database, then Airflow's, before the copy: every metadata file a
# dumped pointer names exists already, so the copy below holds it. Written to
# .partial and kept only when pg_dump finished.
ts=$(date -u +%Y%m%dT%H%M%SZ)
dump() {  # dump <database> <file>
  docker exec k2-prefect-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$1"' sh "$1" | gzip > "$2.partial" ||
    die "pg_dump of database $1 failed"
  [[ $(gzip -dc "$2.partial" | tail -n 5) == *"PostgreSQL database dump complete"* ]] ||
    die "the dump of database $1 is incomplete"
  mv "$2.partial" "$2"
}
prune() {  # prune <dir> <glob>: keep the newest $KEEP; the UTC names sort by time
  local files=()
  shopt -s nullglob
  files=("$1"/$2)
  shopt -u nullglob
  [ "${#files[@]}" -le "$KEEP" ] || rm -f -- "${files[@]:0:${#files[@]}-KEEP}"
}
DUMP_lakekeeper=$DIR/catalog/$ts.sql.gz
dump lakekeeper "$DUMP_lakekeeper"
DUMP_airflow=$DIR/catalog/$ts.airflow.sql.gz
dump airflow "$DUMP_airflow"
prune "$DIR/catalog" '*Z.sql.gz'
prune "$DIR/catalog" '*Z.airflow.sql.gz'

# (3) Each table's objects, additive. mc exits 0 when it refuses an overwrite and
# prints a JSON error line instead, so the lines are what decide.
tally() { find "$1" -type f -printf '%s\n' | { n=0 b=0; while read -r s; do n=$((n + 1)) b=$((b + s)); done; echo "$n $b"; }; }
for t in $TABLES; do
  dest=$DIR/lake/$t
  mkdir -p "$dest"
  read -r n0 b0 < <(tally "$dest")
  out=$(mc_run mirror --json "k2/${LOC[$t]#s3://}" "/backup/$t" 2>&1) || {
    printf '%s\n' "$out" | tail -n 5 >&2; die "$t: mc mirror failed"; }
  errors=$(printf '%s\n' "$out" | grep '"status":"error"' || true)
  if [ -n "$errors" ]; then
    printf '%s\n' "$errors" | head -n 20 >&2
    die "$t: $(printf '%s\n' "$errors" | wc -l) object(s) differ from the copy or could not be read; nothing was overwritten"
  fi
  [ -f "$dest/${META[$t]#"${LOC[$t]}"/}" ] || die "$t: the current metadata file ${META[$t]##*/} is not in the copy"
  read -r n b < <(tally "$dest")
  echo "backup-lake: $t: $((n - n0)) files, $((b - b0)) bytes copied; $n files, $b bytes held"
  ROWS+=("$t|${LOC[$t]}|${META[$t]}|$n|$b|$((n - n0))|$((b - b0))")
done
