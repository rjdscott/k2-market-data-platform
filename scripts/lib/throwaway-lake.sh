#!/usr/bin/env bash
# A throwaway lake for tests: Postgres, MinIO, Lakekeeper and Trino 484 started from a COMPILED
# RELEASE's etc, on a Docker network of their own, with a `tools` container (Python 3.12, k2gen
# and its locked dependencies, bcrypt) inside that network standing in for the tools one-shot.
#
#   scripts/lib/throwaway-lake.sh up    <prefix> <release dir> [extra user ...]
#   scripts/lib/throwaway-lake.sh py    <prefix> <script under the repository> [args ...]
#   scripts/lib/throwaway-lake.sh down  <prefix>
#   scripts/lib/throwaway-lake.sh state <prefix>
#
# up: the release (a sealed <root>/<id> from `k2gen release`) is copied to <state>/releases/<id>
#   and mounted read-only at /k2/releases, `pointers/trino -> ../<id>`, and Trino runs
#   `launcher run --etc-dir /k2/releases/pointers/trino/compiled/trino/etc`, as A1 starts it.
#   Lakekeeper gets warehouse k2 and every warehouse a compiled catalog names, all on one MinIO
#   bucket. Identity is the real path (ADR-060), on this run's own ${K2_SECRETS}, <state>/secrets/k2,
#   laid out as model.SECRET_FILES says: scripts/tls.sh makes the CA and Trino's certificate, and
#   scripts/principal-secret.py's own code gives every known principal of the release
#   (emit/trino_rules.known_principals) its own password and password.db line: a service its
#   principals/<id>.password, a person the password printed once, kept for the test. Two lines no
#   real path makes stand in beside them, by the same in-place write under the same lock: the
#   release canary's (deploy's, S19, at canary/<canary>.password) and each extra user's (a stranger
#   no document declares). Writes <state>/env.json for the tools side by scripts/lib/lake_env.py,
#   which its readers read it with: Trino's URL by name, the CA, and each user's password file.
# py: runs a Python script with k2gen importable, in the tools container when the lake is up, else
#   in a one-off container of the same image; <state> is /work. Of the repository, only SRC (the
#   scripts, k2gen and what it reads, the access suite) is mounted, read-only, at /src/<path>:
#   never the checkout itself, whose .env holds the live stack's secrets.
# down: removes every container and the network labelled with the prefix, and <state>.
#
# <state> is ${K2_THROWAWAY_ROOT:-$HOME/.k2-throwaway}/<prefix>: under $HOME, the one place Docker
# Desktop shares. Never touches the running stack: nothing joins k2-net, no port is published,
# every container and the network are named <prefix>-* and labelled k2.throwaway=<prefix>, and a
# prefix that could collide with the stack's names (k2, k2-*) is refused. Images are the stack's
# pins, read from docker-compose.yml. Every secret is random per run, reaches a container by
# variable name or as a file under <state>/secrets, and is never printed. Trino mounts only its
# key file and the password.db directory, read-only at their /run/k2-secrets paths; both stay mode
# 600, as on the live host, so Trino runs as the invoking user (its data directory a tmpfs), which
# reads them on any host, a CI runner's uid included. The run's secrets die with <state>.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
UV_IMAGE=ghcr.io/astral-sh/uv:0.9.25-python3.12-bookworm-slim@sha256:e47cbc1a37bc146191f85069663f98dff4644667873a0a3051b8d7effe69ea82
MIN_GIB=6
POINTER_ETC=/k2/releases/pointers/trino/compiled/trino/etc

die() { echo "throwaway-lake: $*" >&2; exit 2; }
say() { echo "throwaway-lake: $*"; }
image() {  # image <pattern>: the first image line of docker-compose.yml matching it
  grep -oE "image: *$1[^ ]*" "$REPO/docker-compose.yml" | head -1 | sed 's/image: *//'
}

cmd=${1:-}; P=${2:-}
[ -n "$cmd" ] && [ -n "$P" ] || die "usage: $0 up|py|down|state <prefix> ..."
[[ "$P" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || die "prefix $P: lower-case letters, digits and hyphens"
[[ "$P" == k2 || "$P" == k2-* ]] && die "prefix $P could collide with the running stack's k2-* names"
STATE=${K2_THROWAWAY_ROOT:-$HOME/.k2-throwaway}/$P
NET=$P-net
LABEL=k2.throwaway=$P
TOOLS=$P-tools

# The container side of `py`: uv's environment off the read-only repository, as the host's user.
TOOLS_ENV=(-e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache -e UV_PROJECT_ENVIRONMENT=/tmp/venv
  -e UV_PYTHON_DOWNLOADS=never -e PYTHONDONTWRITEBYTECODE=1 -e UV_NO_PROGRESS=1)
PY=(uv run --project /src/platform/k2gen --locked -q --with bcrypt==5.0.0 python -B)  # mk/principals.mk's pin, literal for the pin gate
# What a script run here reads of the repository: scripts/, k2gen (platform/k2gen) and the run
# record DDL it emits (platform/runs), the schemas it validates against, dbt's macros it compiles,
# and the access suite's proofs (tests/access).
SRC=(scripts platform contracts/schema dbt/macros tests/access)
SRC_MOUNTS=()
for d in "${SRC[@]}"; do SRC_MOUNTS+=(-v "$REPO/$d:/src/$d:ro"); done

containers_down() {
  local ids
  ids=$(docker ps -aq --filter "label=$LABEL")
  [ -z "$ids" ] || docker rm -f -v $ids >/dev/null
  docker network rm "$NET" >/dev/null 2>&1 || true
}

down() {
  containers_down
  if [ -d "$STATE" ]; then chmod -R u+w "$STATE" 2>/dev/null || true; rm -rf "$STATE"; fi
  rmdir "$(dirname "$STATE")" 2>/dev/null || true  # the root too, once no prefix is left in it
  left=$(docker ps -aq --filter "label=$LABEL" | wc -l)
  [ "$left" = 0 ] && ! docker network inspect "$NET" >/dev/null 2>&1 && [ ! -e "$STATE" ] \
    || die "down: $left containers, the network or $STATE left"
}

py() {
  mkdir -p "$STATE"
  if [ "$(docker inspect -f '{{.State.Running}}' "$TOOLS" 2>/dev/null)" = true ]; then
    docker exec -i "${TOOLS_ENV[@]}" "$TOOLS" "${PY[@]}" "$@"
  else
    docker run --rm -i --label "$LABEL" --user "$(id -u):$(id -g)" --memory 512m --cpus 1 "${TOOLS_ENV[@]}" \
      "${SRC_MOUNTS[@]}" -v "$STATE:/work" -w /src "$UV_IMAGE" "${PY[@]}" "$@"
  fi
}

up() {
  local release=${1:?release dir}; shift
  [ -f "$release/manifest.json" ] && [ -d "$release/compiled/trino/etc" ] || die "$release is not a compiled release"
  local id; id=$(basename "$release")
  local gib; gib=$(awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo)
  [ "$gib" -ge "$MIN_GIB" ] || die "MemAvailable ${gib} GiB, under ${MIN_GIB} GiB: not started"
  local trino_image lk_image pg_image minio_image
  trino_image=$(image 'trinodb/trino:'); lk_image=$(image 'quay.io/lakekeeper/catalog:')
  pg_image=$(image 'postgres@sha256:'); minio_image=$(image 'ghcr.io/rjdscott/minio@sha256:')
  [ -n "$trino_image" ] && [ -n "$lk_image" ] && [ -n "$pg_image" ] && [ -n "$minio_image" ] || die "an image pin is missing from docker-compose.yml"

  # An aborted run's containers and network, and its lake files; <state> keeps what the caller put
  # there (a compiled release, say).
  containers_down
  chmod -R u+w "$STATE/releases" 2>/dev/null || true
  rm -rf "$STATE/releases" "$STATE/secrets" "$STATE/env.json"
  mkdir -p "$STATE/releases/pointers"
  (umask 077; mkdir -p "$STATE/secrets")
  cp -a "$release" "$STATE/releases/$id"
  [ -f "$STATE/releases/$id/compiled/trino/etc/password-authenticator.properties" ] \
    || die "$id has no compiled/trino/etc/password-authenticator.properties (emit/trino_identity.py)"
  ln -s "../$id" "$STATE/releases/pointers/trino"

  # Secrets for this run only.
  local s=$STATE/secrets
  export POSTGRES_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD K2_TRINO_SHARED_SECRET K2_TRINO_S3_ACCESS_KEY K2_TRINO_S3_SECRET_KEY
  export LAKEKEEPER__PG_DATABASE_URL_READ LAKEKEEPER__PG_DATABASE_URL_WRITE LAKEKEEPER__PG_ENCRYPTION_KEY MC_HOST_local
  POSTGRES_PASSWORD=$(openssl rand -hex 16); MINIO_ROOT_USER=tl$(openssl rand -hex 6); MINIO_ROOT_PASSWORD=$(openssl rand -hex 20)
  K2_TRINO_SHARED_SECRET=$(openssl rand -hex 32); K2_TRINO_S3_ACCESS_KEY=$MINIO_ROOT_USER; K2_TRINO_S3_SECRET_KEY=$MINIO_ROOT_PASSWORD
  LAKEKEEPER__PG_DATABASE_URL_READ="postgres://lk:$POSTGRES_PASSWORD@postgres:5432/lakekeeper"
  LAKEKEEPER__PG_DATABASE_URL_WRITE=$LAKEKEEPER__PG_DATABASE_URL_READ; LAKEKEEPER__PG_ENCRYPTION_KEY=$(openssl rand -hex 16)
  MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000"
  # This run's ${K2_SECRETS}: the CA and Trino's certificate by the operator's own command.
  K2_SECRETS=$s/k2 bash "$REPO/scripts/tls.sh" init >/dev/null

  say "network $NET; Postgres, MinIO, Lakekeeper"
  docker network create --label "$LABEL" "$NET" >/dev/null
  local run=(run -d --label "$LABEL" --network "$NET" --memory 256m --cpus 0.5)
  docker "${run[@]}" --name "$P-postgres" --network-alias postgres -e POSTGRES_USER=lk -e POSTGRES_PASSWORD \
    -e POSTGRES_DB=lakekeeper "$pg_image" >/dev/null
  docker "${run[@]}" --name "$P-minio" --network-alias minio -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD -e MC_HOST_local \
    "$minio_image" server /data >/dev/null
  for _ in $(seq 60); do docker exec "$P-postgres" pg_isready -U lk -d lakekeeper >/dev/null 2>&1 && break; sleep 1; done
  local lk_env=(-e LAKEKEEPER__PG_DATABASE_URL_READ -e LAKEKEEPER__PG_DATABASE_URL_WRITE -e LAKEKEEPER__PG_ENCRYPTION_KEY)
  docker run --rm --label "$LABEL" --network "$NET" --memory 256m "${lk_env[@]}" "$lk_image" migrate >/dev/null
  docker "${run[@]}" --name "$P-lakekeeper" --network-alias lakekeeper "${lk_env[@]}" -e LAKEKEEPER__BASE_URI=http://lakekeeper:8181 \
    -e LAKEKEEPER__AUDIT__TRACING__ENABLED=false "$lk_image" serve >/dev/null
  for _ in $(seq 60); do docker exec "$P-minio" mc mb --ignore-existing local/lake >/dev/null 2>&1 && break; sleep 1; done
  docker run -d --label "$LABEL" --network "$NET" --memory 768m --cpus 1 --name "$TOOLS" --user "$(id -u):$(id -g)" \
    "${TOOLS_ENV[@]}" -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD "${SRC_MOUNTS[@]}" -v "$STATE:/work" -w /src \
    --entrypoint sleep "$UV_IMAGE" infinity >/dev/null

  # Lakekeeper's warehouses, and every user's password, from the tools side.
  docker exec -i "${TOOLS_ENV[@]}" -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD "$TOOLS" "${PY[@]}" - "/work/releases/$id" "$@" <<'PY'
import contextlib, importlib.util, io, json, os, re, secrets, sys, time, urllib.error, urllib.request
from pathlib import Path
import bcrypt
from k2gen import docset, model
from k2gen.emit import trino_rules
sys.path.insert(0, "/src/scripts/lib")
import lake_env

release, extra = Path(sys.argv[1]), sys.argv[2:]
LK = "http://lakekeeper:8181"

def call(method, url, body=None):
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

for _ in range(60):
    try:
        urllib.request.urlopen(f"{LK}/health", timeout=2)
        break
    except OSError:
        time.sleep(1)
assert call("POST", f"{LK}/management/v1/bootstrap", {"accept-terms-of-use": True}) in (200, 204), "bootstrap"
warehouses = {"k2"} | {m.group(1) for f in (release / "compiled/trino/etc/catalog").glob("*.properties")
                       if (m := re.search(r"^iceberg\.rest-catalog\.warehouse=(\S+)$", f.read_text(), re.M))}
for w in sorted(warehouses):
    body = {"warehouse-name": w, "project-id": "00000000-0000-0000-0000-000000000000",
            "storage-profile": {"type": "s3", "bucket": "lake", "key-prefix": w, "endpoint": "http://minio:9000",
                                "region": "local-01", "path-style-access": True, "flavor": "s3-compat",
                                "sts-enabled": False, "remote-signing-enabled": False},
            "storage-credential": {"type": "s3", "credential-type": "access-key",
                                   "aws-access-key-id": os.environ["MINIO_ROOT_USER"],
                                   "aws-secret-access-key": os.environ["MINIO_ROOT_PASSWORD"]}}
    status = call("POST", f"{LK}/management/v1/warehouse", body)
    assert status in (200, 201), f"warehouse {w}: HTTP {status}"
# Passwords by scripts/principal-secret.py's own functions (its CLI reads the working tree's
# principals; these are the release's): a service's to principals/<id>.password, a person's
# printed once and kept here for the test, each a bcrypt line of password.db in place under its lock.
spec = importlib.util.spec_from_file_location("principal_secret", "/src/scripts/principal-secret.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
root = Path("/work/secrets/k2")
m = model.load(docset.read(release / "documents"))
canary = model.canary_user(m.provenance.set_sha256)
known = set(trino_rules.known_principals(m))
passwords = {}


def keep(path, secret):  # a password file, mode 600, written as principal-secret writes one
    with ps.locked(path) as fd:
        ps.write(fd, secret)
    return str(path)


def line(user):  # a line no real path here makes: deploy's canary line (S19), a stranger's
    secret = secrets.token_urlsafe(32)
    hashed = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(ps.BCRYPT_COST)).decode()
    with ps.locked(root / ps.PASSWORD_DB) as db:
        ps.write(db, ps.read(db) + f"{user}:$2y${hashed.removeprefix('$2b$')}\n")
    return secret


with contextlib.redirect_stderr(io.StringIO()):
    for u in sorted(known - {canary}):
        try:
            model.password_mount(u)
        except ValueError:  # a person
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ps.password(root, u, False, False)
            passwords[u] = keep(root / "throwaway" / f"{u}.password", out.getvalue().strip())
        else:
            ps.password(root, u, False, True)
            passwords[u] = str(root / model.password_file(u))
    passwords[canary] = keep(root / model.canary_password_file(m.provenance.set_sha256), line(canary))
    for u in sorted(set(extra) - known):
        passwords[u] = keep(root / "throwaway" / f"{u}.password", line(u))
users = sorted(passwords)
db_users = [x.split(":", 1)[0] for x in ps.lines_of((root / ps.PASSWORD_DB).read_text()) if x]
assert sorted(db_users) == users, "one password.db line per user"
lake_env.write(Path("/work/env.json"), release=f"/work/releases/{release.name}", trino="https://trino:8443",
               lakekeeper=LK, secrets=str(root), ca=str(root / "tls/ca.pem"), passwords=passwords,
               warehouses=sorted(warehouses))
print(f"throwaway-lake: warehouses {', '.join(sorted(warehouses))}; {len(users)} users, each its own password")
PY

  say "Trino from $POINTER_ETC ($id)"
  # --no-healthcheck: the image's check reads /etc/trino/config.properties, not --etc-dir's (S11).
  # The key file alone, never tls/ (ca.key is there), and password.db by its directory, which
  # principal-secret.py rewrites in place (model.SECRET_FILES).
  docker run -d --label "$LABEL" --network "$NET" --network-alias=trino --name "$P-trino" --cpus 2 --memory 3584m --no-healthcheck \
    --user "$(id -u):$(id -g)" --tmpfs /data/trino:mode=1777 \
    -v "$STATE/releases:/k2/releases:ro" -v "$s/k2/tls/trino.pem:/run/k2-secrets/tls/trino.pem:ro" \
    -v "$s/k2/trino:/run/k2-secrets/trino:ro" \
    -e K2_TRINO_SHARED_SECRET -e K2_TRINO_S3_ACCESS_KEY -e K2_TRINO_S3_SECRET_KEY \
    "$trino_image" /usr/lib/trino/bin/launcher run --etc-dir "$POINTER_ETC" >/dev/null
  local t0=$SECONDS
  until docker exec "${TOOLS_ENV[@]}" "$TOOLS" python3 -c "
import json, ssl, urllib.request
ctx = ssl.create_default_context(cafile='/work/secrets/k2/tls/ca.pem')
assert not json.load(urllib.request.urlopen('https://trino:8443/v1/info', context=ctx, timeout=3))['starting']" 2>/dev/null; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$P-trino")" != true ] || [ $((SECONDS - t0)) -gt 300 ]; then
      docker logs --tail 40 "$P-trino" 2>&1 | grep -vF -e "$MINIO_ROOT_PASSWORD" -e "$K2_TRINO_SHARED_SECRET" >&2 || true
      die "Trino did not start from $POINTER_ETC"
    fi
    sleep 2
  done
  say "up in $((SECONDS - t0)) s: https://trino:8443 and http://lakekeeper:8181 inside $NET; <state> $STATE"
}

case "$cmd" in
  up) shift 2; up "$@" ;;
  py) shift 2; py "$@" ;;
  down) down; say "down: $P" ;;
  state) echo "$STATE" ;;
  *) die "unknown command $cmd" ;;
esac
