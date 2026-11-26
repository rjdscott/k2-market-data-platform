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
#   `launcher run --etc-dir /k2/releases/pointers/trino/compiled/trino/etc`, as A1 starts it. Until
#   step S13 emits it, a release without etc/password-authenticator.properties gets S13's three
#   lines in the copy, and the run says so. Lakekeeper gets warehouse k2 and every warehouse a
#   compiled catalog names, all on one MinIO bucket. Every known principal of the release
#   (emit/trino_rules.known_principals), and each extra user, gets a password.db line, bcrypt cost
#   10, all with one password made for this run. Writes <state>/env.json for the tools side.
# py: runs a Python script with k2gen importable, in the tools container when the lake is up, else
#   in a one-off container of the same image; the repository is /src (read-only), <state> is /work.
# down: removes every container and the network labelled with the prefix, and <state>.
#
# <state> is ${K2_THROWAWAY_ROOT:-$HOME/.k2-throwaway}/<prefix>: under $HOME, the one place Docker
# Desktop shares. Never touches the running stack: nothing joins k2-net, no port is published,
# every container and the network are named <prefix>-* and labelled k2.throwaway=<prefix>, and a
# prefix that could collide with the stack's names (k2, k2-*) is refused. Images are the stack's
# pins, read from docker-compose.yml. Every secret is random per run, reaches a container by
# variable name or as a file under <state>/secrets, and is never printed. The two files Trino
# reads (its key and password.db) are mode 644 so the image's uid 1000 can read them on any host;
# the run's secrets die with <state>.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
UV_IMAGE=ghcr.io/astral-sh/uv:0.9.25-python3.12-bookworm-slim@sha256:e47cbc1a37bc146191f85069663f98dff4644667873a0a3051b8d7effe69ea82
BCRYPT=bcrypt==5.0.0  # the pin of mk/principals.mk
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
PY=(uv run --project /src/platform/k2gen --locked -q --with "$BCRYPT" python -B)

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
      -v "$REPO:/src:ro" -v "$STATE:/work" -w /src "$UV_IMAGE" "${PY[@]}" "$@"
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
  pg_image=$(image 'postgres@sha256:'); minio_image=$(image 'minio/minio:')
  [ -n "$trino_image" ] && [ -n "$lk_image" ] && [ -n "$pg_image" ] && [ -n "$minio_image" ] || die "an image pin is missing from docker-compose.yml"

  # An aborted run's containers and network, and its lake files; <state> keeps what the caller put
  # there (a compiled release, say).
  containers_down
  chmod -R u+w "$STATE/releases" 2>/dev/null || true
  rm -rf "$STATE/releases" "$STATE/secrets" "$STATE/env.json"
  mkdir -p "$STATE/releases/pointers" "$STATE/secrets/tls" "$STATE/secrets/trino"
  chmod 700 "$STATE/secrets"
  cp -a "$release" "$STATE/releases/$id"
  local etc=$STATE/releases/$id/compiled/trino/etc
  if [ ! -f "$etc/password-authenticator.properties" ]; then
    chmod u+w "$etc"
    printf 'password-authenticator.name=file\nfile.password-file=/run/k2-secrets/trino/password.db\nfile.refresh-period=5s\n' \
      > "$etc/password-authenticator.properties"
    chmod a-w "$etc" "$etc/password-authenticator.properties"
    say "NOTE: $id has no etc/password-authenticator.properties (step S13's); the copy has S13's three lines"
  fi
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
  (umask 077; openssl rand -hex 24 > "$s/password")
  # A CA and Trino's certificate for the names clients use (Trino picks its certificate by SNI,
  # so a client connects by name, never by address: step S11's finding).
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=$P throwaway CA" -keyout "$s/ca.key" -out "$s/tls/ca.pem" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj /CN=trino -keyout "$s/tls/trino.key" -out "$s/tls/trino.csr" 2>/dev/null
  openssl x509 -req -days 1 -in "$s/tls/trino.csr" -CA "$s/tls/ca.pem" -CAkey "$s/ca.key" -CAcreateserial -out "$s/tls/trino.crt" \
    -extfile <(printf 'subjectAltName=DNS:trino,DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n') 2>/dev/null
  cat "$s/tls/trino.key" "$s/tls/trino.crt" > "$s/tls/trino.pem"
  rm -f "$s/tls/trino.key" "$s/tls/trino.csr" "$s/tls/ca.srl" "$s/ca.srl"
  chmod 755 "$s/tls" "$s/trino"; chmod 600 "$s/ca.key"; chmod 644 "$s/tls/trino.pem" "$s/tls/ca.pem"

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
    "${TOOLS_ENV[@]}" -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD -v "$REPO:/src:ro" -v "$STATE:/work" -w /src \
    --entrypoint sleep "$UV_IMAGE" infinity >/dev/null

  # Lakekeeper's warehouses, and password.db, from the tools side.
  docker exec -i "${TOOLS_ENV[@]}" -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD "$TOOLS" "${PY[@]}" - "/work/releases/$id" "$@" <<'PY'
import json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path
import bcrypt
from k2gen import docset, model
from k2gen.emit import trino_rules

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
users = sorted(set(trino_rules.known_principals(model.load(docset.read(release / "documents")))) | set(extra))
secret = Path("/work/secrets/password").read_text().strip().encode()
# Cost 10, spelt $2y$ as Trino reads it, as scripts/principal-secret.py writes it.
lines = "".join(f"{u}:$2y${bcrypt.hashpw(secret, bcrypt.gensalt(10)).decode().removeprefix('$2b$')}\n" for u in users)
Path("/work/secrets/trino/password.db").write_text(lines)
Path("/work/env.json").write_text(json.dumps({
    "release": f"/work/releases/{release.name}", "trino": "https://trino:8443", "lakekeeper": LK,
    "ca": "/work/secrets/tls/ca.pem", "passwordFile": "/work/secrets/password", "users": users,
    "warehouses": sorted(warehouses)}, indent=2) + "\n")
print(f"throwaway-lake: warehouses {', '.join(sorted(warehouses))}; {len(users)} password lines")
PY
  chmod 644 "$s/trino/password.db"

  say "Trino from $POINTER_ETC ($id)"
  # --no-healthcheck: the image's check reads /etc/trino/config.properties, not --etc-dir's (S11).
  docker run -d --label "$LABEL" --network "$NET" --network-alias trino --name "$P-trino" --cpus 2 --memory 3584m --no-healthcheck \
    -v "$STATE/releases:/k2/releases:ro" -v "$s/tls:/run/k2-secrets/tls:ro" -v "$s/trino:/run/k2-secrets/trino:ro" \
    -e K2_TRINO_SHARED_SECRET -e K2_TRINO_S3_ACCESS_KEY -e K2_TRINO_S3_SECRET_KEY \
    "$trino_image" /usr/lib/trino/bin/launcher run --etc-dir "$POINTER_ETC" >/dev/null
  local t0=$SECONDS
  until docker exec "${TOOLS_ENV[@]}" "$TOOLS" python3 -c "
import json, ssl, urllib.request
ctx = ssl.create_default_context(cafile='/work/secrets/tls/ca.pem')
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
