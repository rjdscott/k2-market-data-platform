#!/usr/bin/env bash
# Drop a lake table or view, every table and view in a lake namespace, or a MinIO
# bucket, after a verified copy of its objects on the host.
#
#   make drop TARGET=<warehouse>.<namespace>[.<table>]   # a table or view, or a whole namespace
#   make drop TARGET=bucket:<name>                       # a bucket, every object version in it
#   make drop TARGET=... DRY_RUN=1                       # every check and the listing; copies and drops nothing
#
# In order, stopping at the first failure, so nothing is dropped unless every step
# before it held:
#   1. resolve what the target holds from the catalog: each table's and view's
#      location, refused unless it sits inside its warehouse's storage prefix and
#      no other table or view in that warehouse shares it or nests in it;
#   2. list its objects in MinIO, and check the backup disk has room for them; a
#      bucket under object lock is refused while any version in it is retained or
#      on legal hold, naming the latest expiry (governance mode is never bypassed);
#   2b. `make backup` (scripts/backup-lake.sh), the backup set's one copy path;
#      a backup that does not complete refuses the drop;
#   3. mc mirror them to $K2_BACKUP_DIR/drop/<UTC date>/<target>/, and copy every
#      object version of a versioned bucket to <target>.versions/<key>/<version id>;
#   4. compare object count and bytes, MinIO against the copy, then list MinIO
#      again: no key, size or etag may have changed since the copy began (an
#      overwrite of the same size changes only the etag);
#   5. drop: each table and view through the catalog with purge, then the
#      namespace; or `mc rb --force` for a bucket; then wait until the catalog no
#      longer has it and no object is left.
# Every call to the stack runs in one throwaway container on its network: curl
# for the catalog, mc for MinIO. The MinIO credential reaches that container as
# one environment variable, never as an argument, and is never printed.
# Restore and failures: docs/runbooks/drop-and-rebuild.md.
set -euo pipefail
set +x   # never trace: the MinIO credential is in this script's environment
umask 077

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
IMAGE=ghcr.io/rjdscott/minio@sha256:a1a8bd4ac40ad7881a245bab97323e18f971e4d4cba2c2007ec1bedd21cbaba2   # the image, and its mc, that scripts/backup-lake.sh pins
FLOOR_GIB=${K2_BACKUP_MIN_FREE_GIB:-20}          # free space the copy must leave on the backup disk
WAIT_S=${K2_DROP_PURGE_WAIT_S:-900}              # how long the catalog's purge may take to empty a location

die() { echo "drop: FAIL: $*" >&2; exit 1; }
refuse() {
  echo "drop: REFUSED: $*" >&2
  echo "usage: make drop TARGET=<warehouse>.<namespace>[.<table>] | TARGET=bucket:<name> [DRY_RUN=1]" >&2
  exit 2
}

# The target's shape, before anything touches the stack. A name is letters, digits
# and underscores; a bucket, lower-case letters, digits and hyphens.
T=${TARGET:-}
DRY=${DRY_RUN:-0}
[[ $DRY == [01] ]] || refuse "DRY_RUN must be 0 or 1"
ID='[A-Za-z0-9_]+'
WH="" NS="" TB="" BUCKET=""
if [[ $T =~ ^bucket:([a-z0-9][a-z0-9-]{1,61}[a-z0-9])$ ]]; then
  BUCKET=${BASH_REMATCH[1]}
elif [[ $T =~ ^($ID)\.($ID)(\.($ID))?$ ]]; then
  WH=${BASH_REMATCH[1]} NS=${BASH_REMATCH[2]} TB=${BASH_REMATCH[4]}
elif [[ $T =~ ^$ID$ ]]; then
  refuse "TARGET=$T names a whole warehouse; drop.sh drops a namespace, a table, a view or a bucket, never a warehouse"
elif [ -z "$T" ]; then
  refuse "TARGET is empty"
else
  refuse "TARGET=$T is not <warehouse>.<namespace>[.<table>] or bucket:<name>"
fi
[[ $FLOOR_GIB =~ ^[0-9]+$ ]] || die "K2_BACKUP_MIN_FREE_GIB must be a whole number"
[[ $WAIT_S =~ ^[0-9]+$ ]] || die "K2_DROP_PURGE_WAIT_S must be a whole number of seconds"

# .env, one variable at a time in a subshell, as scripts/backup-lake.sh reads it.
[ -f "$ROOT/.env" ] || die "no .env in $ROOT"
env_var() { (set +u; . "$ROOT/.env" >/dev/null 2>&1; printf '%s' "${!1:-}"); }

# The copy goes where backup-lake.sh puts its own: the environment, then .env, then
# ~/k2-backup; never inside a checkout, where `git clean` would delete it.
DIR=${K2_BACKUP_DIR:-$(env_var K2_BACKUP_DIR)}
DIR=$(realpath -m "${DIR:-$HOME/k2-backup}")
main=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
for top in "$ROOT" ${main:+"$(dirname "$main")"}; do
  case "$DIR/" in "$top"/*) die "K2_BACKUP_DIR $DIR is inside the checkout $top; choose a directory outside it" ;; esac
done
[[ $DIR != *,* ]] || die "K2_BACKUP_DIR $DIR holds a comma, which docker's --mount cannot take"
DAY=$(date -u +%F)
DAYDIR=$DIR/drop/$DAY

MINIO_ROOT_USER=$(env_var MINIO_ROOT_USER)
MINIO_ROOT_PASSWORD=$(env_var MINIO_ROOT_PASSWORD)
[ -n "$MINIO_ROOT_USER" ] && [ -n "$MINIO_ROOT_PASSWORD" ] || die "MINIO_ROOT_USER or MINIO_ROOT_PASSWORD is not set in .env"
export MINIO_ROOT_USER MINIO_ROOT_PASSWORD
MC_HOST_k2=$(python3 -c 'import os, urllib.parse as u; e = os.environ; print("http://%s:%s@minio:9000" % (u.quote(e["MINIO_ROOT_USER"], safe=""), u.quote(e["MINIO_ROOT_PASSWORD"], safe="")))')
export MC_HOST_k2

net=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' k2-minio 2>/dev/null) ||
  die "k2-minio is not running"
# The catalog's network, where both Lakekeeper and MinIO are (ADR-059 part 7); before
# the cutover to releases Lakekeeper is on MinIO's only network.
cat_net=$(grep -o '[^ ]*_k2-catalog' <<<"$net" || true)
net=${cat_net:-${net%% *}}
# The copy must not live in MinIO's own storage, where dropping a bucket could delete it.
while IFS= read -r src; do
  [ -n "$src" ] || continue
  case "$DIR/" in "$(realpath -m "$src")"/*) die "K2_BACKUP_DIR $DIR is inside MinIO's storage $src" ;; esac
done < <(docker inspect -f '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' k2-minio)

# All of the logic that reads JSON, in one place: python3 -c "$PY" <command> ...
PY=$(cat <<'PY'
import json, os, subprocess, sys, urllib.parse as up

def fail(msg):
    sys.exit(f"drop: FAIL: {msg}")

def get(c, path):
    r = subprocess.run(["docker", "exec", c, "curl", "-fsS", f"http://lakekeeper:8181/{path}"],
                       capture_output=True, text=True)
    if r.returncode:   # the response is never printed: a table's config block can carry storage settings
        fail(f"the catalog answered GET /{path.split('?')[0]} with: {r.stderr.strip()[-200:]}")
    return json.loads(r.stdout)

def pages(c, path, key):
    out, token = [], None
    while True:
        d = get(c, path + (("&" if "?" in path else "?") + "pageToken=" + up.quote(token, safe="") if token else ""))
        out += d[key]
        token = d.get("next-page-token")
        if not token:
            return out

def nsq(parts):
    return up.quote("\x1f".join(parts), safe="")

def resolve(c, wh, ns, tb):
    """Each table and view the target holds: kind, name, location, metadata file, catalog path."""
    found = [w for w in get(c, "management/v1/warehouse")["warehouses"] if w["name"] == wh]
    if not found:
        fail(f"the catalog has no warehouse {wh}")
    sp = found[0]["storage-profile"]
    base = f"s3://{sp['bucket']}" + (f"/{sp['key-prefix'].strip('/')}" if sp.get("key-prefix", "").strip("/") else "")
    p = get(c, f"catalog/v1/config?warehouse={up.quote(wh)}")["defaults"]["prefix"]
    # Every table and view in the warehouse, to prove none shares the target's location.
    # ponytail: this warehouse only; another warehouse's tables sit under its own prefix.
    spaces, todo = [], [[]]
    while todo:
        parent = todo.pop()
        for n in pages(c, f"catalog/v1/{p}/namespaces" + (f"?parent={nsq(parent)}" if parent else ""), "namespaces"):
            spaces.append(tuple(n))
            todo.append(n)
    objects = {}
    for n in spaces:
        for kind in ("table", "view"):
            for ident in pages(c, f"catalog/v1/{p}/namespaces/{nsq(n)}/{kind}s", "identifiers"):
                path = f"catalog/v1/{p}/namespaces/{nsq(n)}/{kind}s/{up.quote(ident['name'], safe='')}"
                d = get(c, path + ("?snapshots=refs" if kind == "table" else ""))
                objects[(n, ident["name"])] = (kind, d["metadata"]["location"].rstrip("/"), d["metadata-location"], path)
    if (ns,) not in spaces:
        fail(f"the catalog has no namespace {wh}.{ns}")
    if tb:
        mine = [k for k in objects if k == ((ns,), tb)]
        if not mine:
            fail(f"the catalog has no table or view {wh}.{ns}.{tb}")
    else:
        if any(len(n) > 1 and n[0] == ns for n in spaces):
            fail(f"{wh}.{ns} holds nested namespaces, which drop.sh does not drop")
        mine = sorted(k for k in objects if k[0] == (ns,))
    for k in mine:
        kind, loc, meta, path = objects[k]
        name = f"{wh}.{ns}.{k[1]}"
        rest = loc[len(base) + 1:]
        if not (loc.startswith(base + "/") and rest and ".." not in rest.split("/") and "" not in rest.split("/")):
            fail(f"{name}: the catalog gave the location {loc}, which is not inside the warehouse's prefix {base}/")
        if not meta.startswith(loc + "/metadata/"):
            fail(f"{name}: its metadata file {meta} is not under its location {loc}")
        for o, (_, oloc, _, _) in objects.items():
            if o != k and (oloc == loc or oloc.startswith(loc + "/") or loc.startswith(oloc + "/")):
                fail(f"{name}: its location {loc} is shared with {wh}.{'.'.join(o[0])}.{o[1]} at {oloc}")
        print("\t".join((kind, name, loc, meta, path)))
    if not tb:
        print("\t".join(("namespace", f"{wh}.{ns}", "-", "-", f"catalog/v1/{p}/namespaces/{nsq((ns,))}")))

def buckets(c):
    """The buckets that hold a warehouse."""
    for w in get(c, "management/v1/warehouse")["warehouses"]:
        print(w["storage-profile"]["bucket"])

def entries(raw, versions):
    """mc ls --json lines -> {path in the copy: (size, etag)}. Any error line fails the run.
    The etag is what tells a same-size overwrite apart; the copy on disk has none, so it
    is compared by count and bytes, and the etag only listing against listing."""
    out = {}
    with open(raw) as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("status") != "success":
                fail(f"listing failed: {json.dumps(d.get('error', d))[:300]}")
            if d.get("type") != "file" or (versions and d.get("isDeleteMarker")):
                continue
            if not d.get("etag"):   # without it a same-size overwrite passes unseen: fail closed
                fail(f"listing has no etag for {d.get('key')}")
            out[f"{d['key']}/{d['versionId']}" if versions else d["key"]] = (d["size"], d["etag"])
    return out

cmd, *a = sys.argv[1:]
if cmd == "resolve":
    resolve(*a)
elif cmd == "buckets":
    buckets(*a)
elif cmd == "count":            # count <listing> <versions 0|1>: objects and bytes
    e = entries(a[0], a[1] == "1")
    print(len(e), sum(s for s, _ in e.values()))
elif cmd == "list":             # list <listing> <versions 0|1>: the dry run's listing
    for k, (s, _) in sorted(entries(a[0], a[1] == "1").items()):
        print(f"   {s:>14}  {k}")
elif cmd == "keys":             # keys <listing>: key NUL version id NUL, one pair per object version
    for k in sorted(entries(a[0], True)):
        key, vid = k.rsplit("/", 1)
        sys.stdout.write(f"{key}\0{vid}\0")
elif cmd == "compare":          # compare <listing> <versions 0|1> <copy dir>: objects and bytes, each side
    e = entries(a[0], a[1] == "1")
    have = {k: os.path.getsize(os.path.join(a[2], k)) for k in e if os.path.isfile(os.path.join(a[2], k))}
    print(len(e), sum(s for s, _ in e.values()), len(have), sum(have.values()))
elif cmd == "same":             # same <listing> <listing> <versions 0|1>: exit 1 if any key, size or etag differs
    sys.exit(0 if entries(a[0], a[2] == "1") == entries(a[1], a[2] == "1") else 1)
elif cmd == "has":              # has <listing> <key>
    sys.exit(0 if a[1] in entries(a[0], False) else 1)
elif cmd == "versioned":        # versioned <mc version info --json>: exit 0 if versioning was ever on
    sys.exit(0 if json.loads(a[0]).get("versioning", {}).get("status") in ("Enabled", "Suspended") else 1)
elif cmd == "lock":             # lock <mc retention info --default --json>: "<mode> <validity>", or "off"
    d = json.loads(a[0] or "{}")
    if d.get("status") == "success":
        print(f"{d.get('mode') or 'none'} {d.get('validity') or 'none'}" if d.get("enabled") == "Enabled" else "off")
    elif "does not support locking" in json.dumps(d.get("error", {})):
        print("off")
    else:
        fail(f"could not read the bucket's object-lock configuration: {json.dumps(d.get('error', d))[:300]}")
elif cmd == "held":             # held <retention listing> <legal hold listing>: what object lock still protects
    import datetime as dt
    now, until, n, holds = dt.datetime.now(dt.timezone.utc), [], 0, 0
    for path, field in ((a[0], "until"), (a[1], "legalhold")):
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("status") != "success":
                    if "Unable to find any object" in json.dumps(d.get("error", {})):
                        continue   # an empty prefix: nothing to protect
                    fail(f"could not read object-lock state: {json.dumps(d.get('error', d))[:300]}")
                if field == "legalhold":
                    holds += d.get("legalhold") == "ON"
                elif d.get("mode") in ("GOVERNANCE", "COMPLIANCE") and d.get("until") \
                        and dt.datetime.fromisoformat(d["until"]) > now:
                    n += 1
                    until.append((dt.datetime.fromisoformat(d["until"]), d["until"], d["mode"]))
    latest = max(until)[1] if until else "-"
    print(n, latest, ",".join(sorted({m for *_, m in until})) or "-", holds)
elif cmd == "manifest":         # manifest <file> <target> <row>...: what was copied, for a restore
    rows = [dict(zip(("kind", "name", "location", "metadata_location", "copy"), r.split("\t")[:5]),
                 objects=int(r.split("\t")[5]), bytes=int(r.split("\t")[6])) for r in a[2:]]
    with open(a[0], "w") as f:
        json.dump({"target": a[1], "utc": os.environ["UTC"], "objects": rows}, f, indent=2)
PY
)
py() { python3 -c "$PY" "$@"; }

# One throwaway container on the stack's network for every call this run makes.
# It stops itself after a day if this script is killed before the trap runs.
C=k2-drop-$$
WORK=$(mktemp -d)
trap 'docker rm -f "$C" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
mounts=()
if [ "$DRY" = 0 ]; then
  mkdir -p "$DAYDIR"
  chmod 700 "$DIR"
  mounts=(--mount "type=bind,src=$DAYDIR,dst=/backup")
fi
docker run -d --rm --name "$C" --network "$net" --user "$(id -u):$(id -g)" --cpus 1 --memory 512m \
  -e MC_CONFIG_DIR=/tmp/.mc -e MC_HOST_k2 "${mounts[@]}" --entrypoint sleep "$IMAGE" 86400 >/dev/null ||
  die "could not start the helper container (a K2_BACKUP_DIR outside the directories Docker shares fails here)"
mcx() { docker exec "$C" mc "$@"; }
http() { docker exec "$C" curl -s -o /dev/null -w '%{http_code}' "$@"; }

# (1) What the target holds: kind, name, location, metadata file, catalog path.
if [ -n "$BUCKET" ]; then
  out=$(py buckets "$C") || exit 1
  ! grep -qxF "$BUCKET" <<<"$out" || die "bucket $BUCKET holds a lake warehouse; drop its namespaces instead"
  mcx ls "k2/$BUCKET" >/dev/null 2>&1 || die "MinIO has no bucket $BUCKET"
  OBJS=("bucket	$T	s3://$BUCKET	-	-")
else
  out=$(py resolve "$C" "$WH" "$NS" "$TB") || exit 1
  mapfile -t OBJS <<<"$out"
fi

# (2) Each object's listing, and the room the copy needs.
need=0
COPY=() VERS=()
for i in "${!OBJS[@]}"; do
  IFS=$'\t' read -r kind name loc meta path <<<"${OBJS[$i]}"
  [ "$kind" != namespace ] || continue
  COPY[i]=/backup/$T
  [ -n "$TB$BUCKET" ] || COPY[i]=/backup/$T/${name##*.}
  b=${loc#s3://}; b=${b%%/*}
  info=$(mcx version info --json "k2/$b" 2>/dev/null) || die "$name: MinIO did not answer for bucket $b"
  VERS[i]=0
  vn=0 vb=0
  if py versioned "$info"; then
    [ "$kind" = bucket ] || die "$name: bucket $b keeps object versions, which a copy of the current objects misses and a purge leaves behind"
    VERS[i]=1
    # Object lock (WORM) needs versioning, so only a versioned bucket can hold a
    # retained version. Checked before the copy: `mc rb` cannot delete a version
    # under retention or a legal hold, and this script never bypasses governance mode.
    lock=$(py lock "$(mcx retention info --default --json "k2/$b" 2>/dev/null || true)") || exit 1
    if [ "$lock" != off ]; then
      # mc exits 1 on a bucket with no object version; anything else non-zero is no answer.
      for q in retention:r legalhold:h; do
        mcx "${q%:*}" info --recursive --versions --json "k2/$b" > "$WORK/$i.${q#*:}" 2>/dev/null ||
          grep -q 'Unable to find any object' "$WORK/$i.${q#*:}" || die "$name: could not read its object ${q%:*}"
      done
      res=$(py held "$WORK/$i.r" "$WORK/$i.h") || exit 1
      read -r rn until modes holds <<<"$res"
      [ "$rn" = 0 ] ||
        die "$name: object lock (bucket default $lock): $rn object version(s) under $modes retention until $until at the latest, which no delete may remove before then; drop.sh never bypasses governance mode. Run again after $until; nothing was copied or dropped"
      [ "$holds" = 0 ] ||
        die "$name: object lock: $holds object version(s) under a legal hold, which has no expiry; run again once its owner has cleared it; nothing was copied or dropped"
      echo "drop: $name: object lock on (bucket default $lock), no version retained or held now"
    fi
    mcx ls --recursive --versions --json "k2/${loc#s3://}/" > "$WORK/$i.v" || die "$name: could not list its object versions"
    res=$(py count "$WORK/$i.v" 1) || exit 1
    read -r vn vb <<<"$res"
  fi
  mcx ls --recursive --json "k2/${loc#s3://}/" > "$WORK/$i.a" || die "$name: could not list $loc"
  res=$(py count "$WORK/$i.a" 0) || exit 1
  read -r n bytes <<<"$res"
  need=$((need + bytes + vb))
  if [ "$kind" != bucket ]; then
    py has "$WORK/$i.a" "${meta#"$loc"/}" || die "$name: its current metadata file is not at $loc; the catalog and storage disagree"
  fi
  echo "drop: $name: $loc: $n objects, $bytes bytes$([ "${VERS[i]}" = 0 ] || echo "; $vn object versions, $vb bytes")"
  if [ "$DRY" = 1 ]; then
    py list "$WORK/$i.a" 0
    [ "${VERS[i]}" = 0 ] || { echo "   object versions, as <key>/<version id>:"; py list "$WORK/$i.v" 1; }
    echo "drop: would copy them to $DAYDIR/${COPY[i]#/backup/}/, then drop $kind $name"
  fi
done
probe=$DIR
while [ ! -e "$probe" ]; do probe=$(dirname "$probe"); done
free=$(df -B1 --output=avail "$probe" | tail -n 1)
[ $((free - need)) -ge $((FLOOR_GIB * 1024 ** 3)) ] ||
  die "the copy needs $need bytes and $DIR has $free bytes free; that would leave under the floor of $FLOOR_GIB GiB (K2_BACKUP_MIN_FREE_GIB); nothing was copied or dropped"
echo "drop: room: the copy needs $need bytes; $free bytes free at $probe"

if [ "$DRY" = 1 ]; then
  [ -n "$TB$BUCKET" ] || echo "drop: would then drop namespace $WH.$NS"
  echo "drop: DRY RUN: nothing copied, nothing dropped"
  exit 0
fi

# (2b) The nightly backup itself, the one copy of the backup set (`make backup`), taken
# now: a drop never runs without a complete backup from just before it.
bash "$ROOT/scripts/backup-lake.sh" ||
  die "the backup (scripts/backup-lake.sh) did not complete; nothing was copied or dropped"

# (3) The copy. mc exits 0 when it refuses to overwrite a differing file and prints a
# JSON error line instead, so the lines decide.
for i in "${!COPY[@]}"; do
  IFS=$'\t' read -r kind name loc meta path <<<"${OBJS[$i]}"
  out=$(mcx mirror --json "k2/${loc#s3://}/" "${COPY[i]}/" 2>&1) || { printf '%s\n' "$out" | tail -n 5 >&2; die "$name: mc mirror failed"; }
  errors=$(printf '%s\n' "$out" | grep '"status":"error"' || true)
  if [ -n "$errors" ]; then
    printf '%s\n' "$errors" | head -n 20 >&2
    die "$name: $(printf '%s\n' "$errors" | wc -l) object(s) not copied: mc does not replace a file already in $DAYDIR/${COPY[i]#/backup/} (an earlier copy taken today) when the object in MinIO differs or is newer; nothing was overwritten or dropped"
  fi
  if [ "${VERS[i]}" = 1 ]; then
    # ponytail: one mc cp per object version; fine for a scratch bucket, slow past a few thousand.
    while IFS= read -r -d '' key && IFS= read -r -d '' vid; do
      mcx cp --json --version-id "$vid" "k2/$BUCKET/$key" "${COPY[i]}.versions/$key/$vid" >/dev/null ||
        die "$name: could not copy version $vid of $key"
    done < <(py keys "$WORK/$i.v")
  fi
done

# (4) Compare, MinIO against the copy, then MinIO against the listing the copy started from.
ROWS=()
for i in "${!COPY[@]}"; do
  IFS=$'\t' read -r kind name loc meta path <<<"${OBJS[$i]}"
  host=$DAYDIR/${COPY[i]#/backup/}
  res=$(py compare "$WORK/$i.a" 0 "$host") || exit 1
  read -r n b cn cb <<<"$res"
  [ "$n $b" = "$cn $cb" ] || die "$name: $n objects, $b bytes in MinIO but $cn objects, $cb bytes in the copy $host; nothing was dropped"
  line="drop: $name: copied $loc to $host: $n objects, $b bytes in MinIO; $cn objects, $cb bytes in the copy"
  if [ "${VERS[i]}" = 1 ]; then
    res=$(py compare "$WORK/$i.v" 1 "$host.versions") || exit 1
    read -r vn vb vcn vcb <<<"$res"
    [ "$vn $vb" = "$vcn $vcb" ] || die "$name: $vn object versions, $vb bytes in MinIO but $vcn, $vcb bytes in the copy $host.versions; nothing was dropped"
    line="$line; $vn object versions, $vb bytes in MinIO; $vcn, $vcb bytes in $host.versions"
  fi
  mcx ls --recursive --json "k2/${loc#s3://}/" > "$WORK/$i.b" || die "$name: could not list $loc again"
  py same "$WORK/$i.a" "$WORK/$i.b" 0 || die "$name: $loc changed while it was copied; stop what writes to it and run again; nothing was dropped"
  if [ "${VERS[i]}" = 1 ]; then
    mcx ls --recursive --versions --json "k2/${loc#s3://}/" > "$WORK/$i.w" || die "$name: could not list its versions again"
    py same "$WORK/$i.v" "$WORK/$i.w" 1 || die "$name: its object versions changed while they were copied; nothing was dropped"
  fi
  echo "$line: match"
  ROWS+=("$kind	$name	$loc	$meta	$host	$n	$b")
done
manifest=$DAYDIR/$T.$(date -u +%H%M%SZ).json
UTC=$(date -u +%FT%TZ) py manifest "$manifest" "$T" "${ROWS[@]}"
echo "drop: manifest $manifest"

# (5) Drop, then wait for the catalog's purge to empty each location.
for o in "${OBJS[@]}"; do
  IFS=$'\t' read -r kind name loc meta path <<<"$o"
  case $kind in
    bucket)
      mcx rb --force "k2/$BUCKET" >/dev/null || die "$name: mc rb failed"
      ! mcx ls "k2/$BUCKET" >/dev/null 2>&1 || die "$name: bucket $BUCKET is still there after mc rb"
      echo "drop: $name: bucket removed" ;;
    namespace)
      code=$(http -X DELETE "http://lakekeeper:8181/$path")
      [ "$code" = 204 ] || die "$name: the catalog answered $code to the namespace drop (409: something was added to it)"
      [ "$(http "http://lakekeeper:8181/$path")" = 404 ] || die "$name: the catalog still lists the namespace"
      echo "drop: $name: namespace dropped" ;;
    *)
      code=$(http -X DELETE "http://lakekeeper:8181/$path?purgeRequested=true")
      [ "$code" = 204 ] || die "$name: the catalog answered $code to the drop"
      [ "$(http "http://lakekeeper:8181/$path")" = 404 ] || die "$name: the catalog still lists it after the drop"
      t0=$SECONDS
      while :; do
        mcx ls --recursive --json "k2/${loc#s3://}/" > "$WORK/left" || die "$name: dropped from the catalog, but MinIO did not answer the check that its files went"
        res=$(py count "$WORK/left" 0) || exit 1
        read -r left _ <<<"$res"
        [ "$left" -gt 0 ] || break
        [ $((SECONDS - t0)) -lt "$WAIT_S" ] ||
          die "$name: dropped from the catalog, but $left objects are still at $loc after $WAIT_S s; the copy holds them (docs/runbooks/drop-and-rebuild.md)"
        sleep 2
      done
      echo "drop: $name: dropped with purge; 0 objects left at $loc after $((SECONDS - t0)) s" ;;
  esac
done
echo "drop: OK: $T dropped; the copy is in $DAYDIR"
