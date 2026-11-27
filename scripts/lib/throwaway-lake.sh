#!/usr/bin/env bash
# A throwaway lake for tests: Postgres, MinIO, Lakekeeper and Trino 484 started from a COMPILED
# RELEASE's etc, on a Docker network of their own, with a `tools` container (Python 3.12, k2gen
# and its locked dependencies, bcrypt) inside that network standing in for the tools one-shot.
#
#   scripts/lib/throwaway-lake.sh up    <prefix> <release dir> [extra user ...]
#   scripts/lib/throwaway-lake.sh py    <prefix> <script under the repository> [args ...]
#   scripts/lib/throwaway-lake.sh down  <prefix>
#   scripts/lib/throwaway-lake.sh state <prefix>
#   scripts/lib/throwaway-lake.sh init  <prefix>
#   scripts/lib/throwaway-lake.sh seed  <prefix> [fixture.jsonl]
#
# K2_THROWAWAY_REDPANDA=1 on up adds one Redpanda (redpanda_up below); without it, up is as before.
# seed: scripts/lib/kafka_seed.py in compose's Spark image on this lake's network, the fixture's
#   frames (default services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl) produced to
#   Kraken's raw topic as capture frames them; its one count line.
# K2_THROWAWAY_TOPIC on up and seed names that topic instead (default market.crypto.v3.raw.kraken):
#   scripts/verify/kafka-path.sh's fixture product reads a topic of its own.
#
# up: the release (a sealed <root>/<id> from `k2gen release`) is copied to <state>/releases/<id>
#   and mounted read-only at /k2/releases, `pointers/trino -> ../<id>`, and Trino runs
#   `launcher run --etc-dir /k2/releases/pointers/trino/compiled/trino/etc`, as A1 starts it.
#   The lake is made by the real docker/lake/init-lake.sh in the MinIO image, as lake-init runs it,
#   in the live layout (bucket k2-lake, warehouse/<name>, the landing bucket k2-landing): every
#   warehouse of the release's storage plan with remote signing off, and one MinIO user and policy
#   per engine (docker/lake/minio/policies/), each on this run's own K2_<ENGINE>_S3_* pair. Trino
#   runs on its own pair, never the root's, and the tools container holds every pair by name, so a
#   storage-key row of the access suite signs with the key it names.
#   Identity is the real path (ADR-060), on this run's own ${K2_SECRETS}, <state>/secrets/k2,
#   laid out as model.SECRET_FILES says: scripts/tls.sh makes the CA and Trino's certificate, and
#   scripts/principal-secret.py's own code gives every known principal of the release
#   (emit/trino_rules.known_principals) its own password and password.db line: a service its
#   principals/<id>.password, a person the password printed once, kept for the test. Two lines no
#   real path makes stand in beside them, by the same in-place write under the same lock: the
#   release canary's (deploy's, S19, at canary/<canary>.password) and each extra user's (a stranger
#   no document declares). Writes <state>/env.json for the tools side by scripts/lib/lake_env.py,
#   which its readers read it with: Trino's URL by name, the CA, and each user's password file.
#   When the release has Spark DDL (compiled/lake/ddl/spark/: the connector products' bronze,
#   landing_files and audit_checks in warehouse k2), the Spark image of the compose pin makes those
#   tables while Trino starts, through docker/lake/spark_conf.py on Spark's own pair, one JVM for
#   all of them, a table already there left as it is: every caller gets the lake a deploy leaves.
#   When a row of the release's expectations runs `via: rest`, k2-api joins (step W3-7): each such
#   row's principal gets a key by scripts/principal-secret.py's own api_key (its sha256 alone in
#   <secrets>/api/keys.json, mode 600; the key itself in <secrets>/api-keys/<id>.key, mode 600, for
#   the suite, as `make api-key TO_FILE=1` writes it, and never in api/, which k2-api mounts), and
#   the image k2-api:throwaway, built from this tree's services/k2-api/Dockerfile, runs with
#   compose's flags for service k2-api, on `pointers/k2-api -> ../<id>`, reached by name from the
#   tools container only.
# py: runs a Python script with k2gen importable, in the tools container when the lake is up, else
#   in a one-off container of the same image; <state> is /work. Of the repository, only SRC (the
#   scripts, k2gen and what it reads, the access suite) is mounted, read-only, at /src/<path>:
#   never the checkout itself, whose .env holds the live stack's secrets.
# down: removes every container and the network labelled with the prefix, and <state>.
# init: runs docker/lake/init-lake.sh again on the running lake, with this run's keys (a rerun is
#   a no-op, scripts/verify/storage-keys.sh proves).
#
# <state> is ${K2_THROWAWAY_ROOT:-$HOME/.k2-throwaway}/<prefix>: under $HOME, the one place Docker
# Desktop shares. Never touches the running stack: nothing joins k2-net, no port is published,
# every container and the network are named <prefix>-* and labelled k2.throwaway=<prefix>, and a
# prefix that could collide with the stack's names (k2, k2-*) is refused. Images are the stack's
# pins, read from docker-compose.yml. Every secret is random per run, reaches a container by
# variable name or as a file under <state>/secrets, and is never printed. Trino mounts only its
# key file and the password.db directory, read-only at their /run/k2-secrets paths; both stay mode
# 600, as on the live host, so Trino runs as the invoking user (its data directory a tmpfs), which
# reads them on any host, a CI runner's uid included. The root pair and the engines' pairs are kept
# in <state>/secrets/s3.env (mode 600) for `init`. The run's secrets die with <state>.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
UV_IMAGE=ghcr.io/astral-sh/uv:0.9.25-python3.12-bookworm-slim@sha256:e47cbc1a37bc146191f85069663f98dff4644667873a0a3051b8d7effe69ea82
MIN_GIB=6
POINTER_ETC=/k2/releases/pointers/trino/compiled/trino/etc
API_IMAGE=k2-api:throwaway  # built from this tree: never the stack's k2-api:local nor a release's pin

die() { echo "throwaway-lake: $*" >&2; exit 2; }
say() { echo "throwaway-lake: $*"; }
image() {  # image <pattern>: the first image line of docker-compose.yml matching it
  grep -oE "image: *$1[^ ]*" "$REPO/docker-compose.yml" | head -1 | sed 's/image: *//'
}

cmd=${1:-}; P=${2:-}
[ -n "$cmd" ] && [ -n "$P" ] || die "usage: $0 up|py|down|state|init|seed <prefix> ..."
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

# Every engine's pair, by name: K2_<ENGINE>_S3_ACCESS_KEY and _SECRET_KEY for each policy file
# (one definition: docker/lake/init-lake.sh reads the same directory).
S3_NAMES=(K2_S3_ENDPOINT)
[ -f "$REPO/docker/lake/minio/policies/trino.json" ] || { echo "throwaway-lake: no docker/lake/minio/policies/*.json under $REPO (a scratch copy must carry docker/lake)" >&2; exit 1; }
for f in "$REPO"/docker/lake/minio/policies/*.json; do
  e=${f##*/} e=${e%.json}
  S3_NAMES+=("K2_${e^^}_S3_ACCESS_KEY" "K2_${e^^}_S3_SECRET_KEY")
done
S3_ENV=()
for n in "${S3_NAMES[@]}"; do S3_ENV+=(-e "$n"); done

lake_init() {  # the real lake-init on this lake: its MinIO image, the release's plan through the pointer
  docker run --rm --label "$LABEL" --network "$NET" --memory 256m --cpus 0.25 \
    -v "$REPO/docker/lake:/init:ro" -v "$STATE/releases:/k2/releases:ro" \
    -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD "${S3_ENV[@]}" \
    --entrypoint /bin/bash "$(image 'ghcr.io/rjdscott/minio@sha256:')" /init/init-lake.sh >> "$STATE/lake-init.log" 2>&1 \
    || { tail -n 15 "$STATE/lake-init.log" >&2; die "docker/lake/init-lake.sh failed ($STATE/lake-init.log)"; }
}

init() {
  [ -f "$STATE/secrets/s3.env" ] || die "init: no lake is up for $P"
  # shellcheck source=/dev/null
  set -a; . "$STATE/secrets/s3.env"; set +a
  lake_init
  say "init: docker/lake/init-lake.sh ran again ($STATE/lake-init.log)"
}

spark_ddl() {  # the release's Spark tables (warehouse k2), as deploy's ddl step makes them: by Spark on its own key
  local n; n=$(find "$STATE/releases/pointers/trino/compiled/lake/ddl/spark/" -name '*.sql' 2>/dev/null | wc -l)
  [ "$n" -gt 0 ] || return 0
  say "spark-ddl: $n table(s) of warehouse k2, by Spark on its own key"
  docker run --rm -i --label "$LABEL" --network "$NET" --memory 1536m --cpus 1 --name "$P-spark" \
    -e K2_SPARK_S3_ACCESS_KEY -e K2_SPARK_S3_SECRET_KEY -v "$REPO/docker/lake:/home/iceberg/lake:ro" \
    -v "$STATE/releases:/k2/releases:ro" --entrypoint python3 "$(image 'k2-spark-iceberg:')" - \
    >> "$STATE/spark-ddl.log" 2>&1 <<'PY' || { tail -n 15 "$STATE/spark-ddl.log" >&2; die "spark-ddl failed ($STATE/spark-ddl.log)"; }
import re, sys
from pathlib import Path
sys.path.insert(0, "/home/iceberg/lake")
from spark_conf import lake_session  # the lake's one session builder, on K2_SPARK_S3_*
spark = lake_session("throwaway-spark-ddl", driver_memory="512m")
made = 0
for f in sorted(Path("/k2/releases/pointers/trino/compiled/lake/ddl/spark").glob("*.sql")):
    _, ns, table = f.stem.split(".")  # <warehouse>.<namespace>.<table>, model.ddl_path
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS lake.{ns}")
    if table in {r.tableName for r in spark.sql(f"SHOW TABLES IN lake.{ns}").collect()}:
        continue  # idempotent: a rerun on the same lake makes nothing twice
    for statement in re.split(r";\s*$", f.read_text(), flags=re.M):
        if statement.strip():
            spark.sql(statement)
    made += 1
print(f"spark-ddl: {made} table(s) made")
spark.stop()
PY
}

# docker/redpanda/init.sh's name for Kraken's raw frames, which kraken-trades reads, unless overridden
RAW_TOPIC=${K2_THROWAWAY_TOPIC:-market.crypto.v3.raw.kraken}
redpanda_up() {  # K2_THROWAWAY_REDPANDA=1: one broker and its schema registry, Kraken's raw topic, RawMessage's subject
  [ "${K2_THROWAWAY_REDPANDA:-}" = 1 ] || return 0
  local rp=$P-redpanda t0=$SECONDS
  # docker-compose.yml's service redpanda: its image, dev-container mode, one shard, one listener each
  # (redpanda:9092, :8081, spark_conf.py's defaults) and no host port; 512M for Seastar under a 1g cap.
  docker run -d --label "$LABEL" --network "$NET" --network-alias redpanda --name "$rp" --memory 1g --cpus 1 \
    -v "$REPO/schemas/avro:/schemas:ro" "$(image 'docker.redpanda.com/redpandadata/redpanda:')" \
    redpanda start --kafka-addr internal://0.0.0.0:9092 --advertise-kafka-addr internal://redpanda:9092 \
    --schema-registry-addr internal://0.0.0.0:8081 --rpc-addr redpanda:33145 --advertise-rpc-addr redpanda:33145 \
    --mode dev-container --smp 1 --memory 512M >/dev/null
  until docker exec "$rp" curl -sf http://localhost:8081/subjects >/dev/null 2>&1; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$rp")" != true ] || [ $((SECONDS - t0)) -gt 60 ]; then
      docker logs --tail 30 "$rp" >&2 || true
      die "redpanda's schema registry did not answer"
    fi
    sleep 1
  done
  docker exec "$rp" rpk topic create "$RAW_TOPIC" --partitions 3 >/dev/null
  docker exec "$rp" rpk registry schema create "$RAW_TOPIC-value" --schema /schemas/raw-message.avsc >/dev/null
  say "redpanda: $RAW_TOPIC (3 partitions), subject $RAW_TOPIC-value schema id $(docker exec "$rp" curl -sf \
    "http://localhost:8081/subjects/$RAW_TOPIC-value/versions/latest" | grep -oE '"id":[0-9]+' | cut -d: -f2), in $((SECONDS - t0)) s"
}

seed() {  # seed [fixture]: scripts/lib/kafka_seed.py in compose's Spark image, on this lake's network
  local fx=${1:-$REPO/services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl}
  [ "$(docker inspect -f '{{.State.Running}}' "$P-redpanda" 2>/dev/null)" = true ] || die "seed: no Redpanda for $P (K2_THROWAWAY_REDPANDA=1 on up)"
  [ -f "$fx" ] || die "seed: no fixture $fx"
  fx=$(cd "$(dirname "$fx")" && pwd)/$(basename "$fx")
  docker run --rm -i --label "$LABEL" --network "$NET" --memory 1536m --cpus 1 --name "$P-kafka-seed" \
    -v "$fx:/seed/frames.jsonl:ro" --entrypoint python3 "$(image 'k2-spark-iceberg:')" - /seed/frames.jsonl "$RAW_TOPIC" \
    < "$REPO/scripts/lib/kafka_seed.py"
}

api_up() {  # k2-api, when a REST row needs it (the key step above made api/keys.json)
  local k=$STATE/secrets/k2
  [ -f "$k/api/keys.json" ] || return 0
  say "k2-api: $API_IMAGE from services/k2-api/Dockerfile (Docker's layer cache: an unchanged tree rebuilds in seconds)"
  # --load: into the daemon whatever the builder (a CI runner's setup-buildx-action builder keeps images in itself).
  docker build -q --load -f "$REPO/services/k2-api/Dockerfile" -t "$API_IMAGE" "$REPO" >> "$STATE/k2-api-build.log" 2>&1 \
    || { tail -n 15 "$STATE/k2-api-build.log" >&2; die "k2-api's image did not build ($STATE/k2-api-build.log)"; }
  # docker-compose.yml's service k2-api, flag for flag: the host user with group 0, a read-only root, a
  # 16 MB /tmp, no capability, no new privileges, its two variables and its four read-only mounts; no
  # host port. The k2-site volume is the image's own empty /srv/k2-site under the read-only root, as a
  # fresh volume mounted read-only is (Docker refuses an anonymous volume read-only).
  docker run -d --label "$LABEL" --network "$NET" --network-alias=k2-api --name "$P-k2-api" --user "$(id -u):0" \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m --cap-drop ALL --security-opt no-new-privileges:true \
    --cpus 0.5 --memory 512m -e K2_TRINO_HOST=trino -e K2_TRINO_INTERNAL_PORT=8443 \
    -v "$STATE/releases:/k2/releases:ro" -v "$k/api:/run/k2-secrets/api:ro" \
    -v "$k/principals/svc.api.password:/run/k2-secrets/api.password:ro" -v "$k/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
    "$API_IMAGE" >/dev/null
  local t0=$SECONDS
  until docker exec "${TOOLS_ENV[@]}" "$TOOLS" python3 -c "
import urllib.request; urllib.request.urlopen('http://k2-api:8095/healthz', timeout=3)" 2>/dev/null; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$P-k2-api")" != true ] || [ $((SECONDS - t0)) -gt 60 ]; then
      docker logs --tail 30 "$P-k2-api" >&2 || true
      die "k2-api did not answer /healthz"
    fi
    sleep 1
  done
  say "k2-api: http://k2-api:8095 inside $NET, as svc.api on Trino"
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
  ln -s "../$id" "$STATE/releases/pointers/k2-api"  # made with the other, before any container mounts the root

  # Secrets for this run only.
  local s=$STATE/secrets
  export POSTGRES_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD K2_TRINO_SHARED_SECRET
  export LAKEKEEPER__PG_DATABASE_URL_READ LAKEKEEPER__PG_DATABASE_URL_WRITE LAKEKEEPER__PG_ENCRYPTION_KEY MC_HOST_local
  POSTGRES_PASSWORD=$(openssl rand -hex 16); MINIO_ROOT_USER=tl$(openssl rand -hex 6); MINIO_ROOT_PASSWORD=$(openssl rand -hex 20)
  K2_TRINO_SHARED_SECRET=$(openssl rand -hex 32)
  # Each engine's own pair, this run's only; init-lake.sh refuses one equal to the root's.
  local n e
  export K2_S3_ENDPOINT=http://minio:9000
  for n in "${S3_NAMES[@]:1}"; do
    case $n in
      *_ACCESS_KEY) e=${n%_S3_ACCESS_KEY} e=${e#K2_}; export "$n=${e,,}-$(openssl rand -hex 3)" ;;
      *) export "$n=$(openssl rand -hex 16)" ;;
    esac
  done
  (umask 077; for n in MINIO_ROOT_USER MINIO_ROOT_PASSWORD "${S3_NAMES[@]}"; do printf '%s=%s\n' "$n" "${!n}"; done > "$s/s3.env")
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
  for _ in $(seq 60); do docker exec "$P-minio" mc ls local >/dev/null 2>&1 && break; sleep 1; done
  for _ in $(seq 60); do docker exec "$P-lakekeeper" /home/nonroot/lakekeeper healthcheck >/dev/null 2>&1 && break; sleep 1; done
  say "lake-init: docker/lake/init-lake.sh, warehouses and engine users"
  lake_init
  docker run -d --label "$LABEL" --network "$NET" --memory 768m --cpus 1 --name "$TOOLS" --user "$(id -u):$(id -g)" \
    "${TOOLS_ENV[@]}" -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD "${S3_ENV[@]}" "${SRC_MOUNTS[@]}" -v "$STATE:/work" -w /src \
    --entrypoint sleep "$UV_IMAGE" infinity >/dev/null

  # Every user's password, from the tools side, once lake-init's warehouses are there.
  docker exec -i "${TOOLS_ENV[@]}" "$TOOLS" "${PY[@]}" - "/work/releases/$id" "$@" <<'PY'
import contextlib, importlib.util, io, json, os, re, secrets, sys, time, urllib.error, urllib.request
from pathlib import Path
import bcrypt
from k2gen import docset, model
from k2gen.emit import trino_rules
sys.path.insert(0, "/src/scripts/lib")
import lake_env

release, extra = Path(sys.argv[1]), sys.argv[2:]
LK = "http://lakekeeper:8181"

for _ in range(60):
    try:
        urllib.request.urlopen(f"{LK}/health", timeout=2)
        break
    except OSError:
        time.sleep(1)
warehouses = {"k2"} | {m.group(1) for f in (release / "compiled/trino/etc/catalog").glob("*.properties")
                       if (m := re.search(r"^iceberg\.rest-catalog\.warehouse=(\S+)$", f.read_text(), re.M))}
with urllib.request.urlopen(f"{LK}/management/v1/warehouse", timeout=10) as r:
    made = {w["name"] for w in json.load(r)["warehouses"]}
assert warehouses <= made, f"a catalog names a warehouse lake-init did not make: {sorted(warehouses - made)}"
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
    # A key per principal of a `via: rest` row, by principal-secret's api_key: its sha256 to
    # api/keys.json under its lock (what k2-api reads), the key to api-keys/<u>.key for the suite.
    rest = sorted({e.principal for e in m.expectations if "rest" in getattr(e, "via", ())})
    for u in rest:
        ps.api_key(root, u, False, True)
users = sorted(passwords)
db_users = [x.split(":", 1)[0] for x in ps.lines_of((root / ps.PASSWORD_DB).read_text()) if x]
assert sorted(db_users) == users, "one password.db line per user"
lake_env.write(Path("/work/env.json"), release=f"/work/releases/{release.name}", trino="https://trino:8443",
               lakekeeper=LK, secrets=str(root), ca=str(root / "tls/ca.pem"), passwords=passwords,
               warehouses=sorted(warehouses))
print(f"throwaway-lake: warehouses {', '.join(sorted(warehouses))}; {len(users)} users, each its own password; "
      f"{len(rest)} API key(s), for the REST rows")
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
  spark_ddl  # while Trino starts: the two JVMs' starts overlap
  redpanda_up
  until docker exec "${TOOLS_ENV[@]}" "$TOOLS" python3 -c "
import json, ssl, urllib.request
ctx = ssl.create_default_context(cafile='/work/secrets/k2/tls/ca.pem')
assert not json.load(urllib.request.urlopen('https://trino:8443/v1/info', context=ctx, timeout=3))['starting']" 2>/dev/null; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$P-trino")" != true ] || [ $((SECONDS - t0)) -gt 300 ]; then
      docker logs --tail 40 "$P-trino" 2>&1 | grep -vF -e "$MINIO_ROOT_PASSWORD" -e "$K2_TRINO_SHARED_SECRET" -e "$K2_TRINO_S3_SECRET_KEY" >&2 || true
      die "Trino did not start from $POINTER_ETC"
    fi
    sleep 2
  done
  api_up
  say "up in $((SECONDS - t0)) s: https://trino:8443 and http://lakekeeper:8181 inside $NET; <state> $STATE"
}

case "$cmd" in
  up) shift 2; up "$@" ;;
  py) shift 2; py "$@" ;;
  down) down; say "down: $P" ;;
  state) echo "$STATE" ;;
  init) init ;;
  seed) shift 2; seed "$@" ;;
  *) die "unknown command $cmd" ;;
esac
