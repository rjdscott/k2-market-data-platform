# Sourced by the spine-*/run.sh spikes (docs/research/2026-11-26-release-spine-spikes.md).
# A throwaway Trino, the image docker-compose.yml pins, on the stack's network, with two
# catalogs over Lakekeeper warehouse reference_data: `spike` (writable, the shape of
# docker/trino/etc/catalog/reference_data.properties) and `spike_ro` (the same with
# iceberg.security=read_only, the reader catalog of IMPLEMENTATION-N0-N1 A8). Scratch
# namespaces are spike_n04dbt_*, dropped on exit with the container and the work directory.
# dbt runs on the host from dbt/'s lock (dbt-core 1.12.7, dbt-trino 1.12.0). Synthetic rows only.
#
#   source spine-lib.sh; spine_start <tag> <port> <rules.json>; q <user> "<sql>"; dbt_ <args>
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
IMAGE=$(grep -oE 'trinodb/trino:[^ ]+' "$REPO/docker-compose.yml" | head -1)
NET=$(docker inspect k2-lakekeeper -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
# Under $HOME: Docker Desktop shares it with its VM, and not /tmp.
mkdir -p "$HOME/.cache"; WORK=$(mktemp -d "$HOME/.cache/k2-spine-XXXXXX")
NAME=""
SCHEMAS=()

# Heavy starts wait for 6 GiB free on the host (other jobs share it), at most 10 minutes.
wait_mem() {
  for _ in $(seq 1 60); do
    awk '/MemAvailable/{exit !($2/1048576 >= 6)}' /proc/meminfo && return 0
    echo "waiting: under 6 GiB available"; sleep 10
  done
  echo "FAIL: host memory stayed under 6 GiB available"; exit 1
}

drop_schemas() {
  for s in "${SCHEMAS[@]}"; do
    docker exec "$NAME" trino --user dbt_spike --execute "DROP SCHEMA IF EXISTS spike.$s CASCADE" >/dev/null 2>&1 \
      || echo "WARN: could not drop spike.$s"
  done
}
spine_cleanup() {
  if [ -n "$NAME" ] && docker inspect "$NAME" >/dev/null 2>&1; then
    drop_schemas
    docker rm -f "$NAME" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap spine_cleanup EXIT

# The default rules: dbt_spike writes catalog spike; everyone else reads spike_ro only.
DEFAULT_RULES='{
  "catalogs": [{"catalog": "system", "allow": "read-only"},
               {"user": "dbt_spike", "catalog": "spike|spike_ro", "allow": "all"},
               {"catalog": "spike_ro", "allow": "read-only"}],
  "schemas": [{"user": "dbt_spike", "catalog": "spike", "owner": true}, {"owner": false}],
  "tables": [{"user": "dbt_spike", "catalog": "spike",
              "privileges": ["SELECT", "INSERT", "DELETE", "UPDATE", "OWNERSHIP", "GRANT_SELECT"]},
             {"privileges": ["SELECT"]}]
}'

# spine_start <tag> <host port> [rules.json]: start spike-n04dbt-<tag> and wait for it.
spine_start() {
  NAME="spike-n04dbt-$1"; PORT=$2
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  mkdir -p "$WORK/etc/catalog"
  cp "$REPO/docker/trino/etc/log.properties" "$REPO/docker/trino/etc/node.properties" "$WORK/etc/"
  sed 's/^-Xm\([sx]\).*/-Xm\11280M/' "$REPO/docker/trino/etc/jvm.config" > "$WORK/etc/jvm.config"
  printf '%s\n' coordinator=true node-scheduler.include-coordinator=true http-server.http.port=8080 \
    discovery.uri=http://localhost:8080 query.max-history=2000 > "$WORK/etc/config.properties"
  printf '%s\n' access-control.name=file security.config-file=/etc/trino/rules.json > "$WORK/etc/access-control.properties"
  if [ -n "${3:-}" ]; then cp "$3" "$WORK/etc/rules.json"; else echo "$DEFAULT_RULES" > "$WORK/etc/rules.json"; fi
  cp "$REPO/docker/trino/etc/catalog/reference_data.properties" "$WORK/etc/catalog/spike.properties"
  { cat "$WORK/etc/catalog/spike.properties"; echo iceberg.security=read_only; } > "$WORK/etc/catalog/spike_ro.properties"
  chmod -R a+rX "$WORK"
  # The storage keys come from the running Trino by name, never printed or written down.
  MINIO_ROOT_USER=$(docker exec k2-trino printenv MINIO_ROOT_USER)
  MINIO_ROOT_PASSWORD=$(docker exec k2-trino printenv MINIO_ROOT_PASSWORD)
  export MINIO_ROOT_USER MINIO_ROOT_PASSWORD
  wait_mem
  docker run -d --name "$NAME" --network "$NET" --cpus 2 --memory 2g -p "127.0.0.1:$PORT:8080" \
    -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD -v "$WORK/etc":/etc/trino:ro "$IMAGE" >/dev/null
  spine_wait
  drop_schemas   # left by an aborted run
}

# spine_wait: until the throwaway Trino answers (after a start or a `docker restart`).
spine_wait() {
  for _ in $(seq 1 90); do
    q trino 'SELECT 1' >/dev/null 2>&1 && return 0
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || break
    sleep 2
  done
  echo "FAIL: $NAME did not start"; docker logs "$NAME" 2>&1 | tail -30; exit 1
}

# q <user> <sql>: one statement through the throwaway Trino, CSV out.
# The CLI's status line on stderr (CREATE TABLE, INSERT: 1 row) is shown only when the statement fails.
q() {
  docker exec "$NAME" trino --user "$1" --output-format CSV_UNQUOTED --execute "$2" 2>"$WORK/q.err" \
    || { cat "$WORK/q.err" >&2; return 1; }
}
# qx <user> <sql>: a statement that may be refused; prints "succeeded" or "refused: <Trino's message>".
qx() {
  local out
  if out=$(docker exec "$NAME" trino --user "$1" --execute "$2" 2>&1); then echo succeeded; return; fi
  echo "refused: $(grep -m1 -oE 'Query [^ ]+ failed: .*' <<< "$out" | sed 's/^Query [^ ]* failed: //' || echo "${out%%$'\n'*}")"
}

# dbt_ <args>: dbt from dbt/'s lock against the throwaway Trino, project in $WORK/project.
dbt_() {
  DBT_TARGET_PATH="$WORK/target" DBT_LOG_PATH="$WORK/logs" DBT_SEND_ANONYMOUS_USAGE_STATS=false DO_NOT_TRACK=1 \
    uv run --locked -q --project "$REPO/dbt" \
    dbt "$@" --project-dir "$WORK/project" --profiles-dir "$WORK/project"
}

# use_project <dir> <schema>: the project copied to $WORK/project, so dbt writes nothing into the
# checkout, with a profile on the throwaway Trino as dbt_spike, default schema <schema>.
use_project() {
  rm -rf "$WORK/project"; cp -r "$1" "$WORK/project"
  cat > "$WORK/project/profiles.yml" <<EOF
spine:
  target: trino
  outputs:
    trino: {type: trino, method: none, http_scheme: http, host: 127.0.0.1, port: $PORT, user: dbt_spike,
            database: spike, schema: $2, timezone: UTC, threads: 1}
EOF
}

# py <args>: Python with dbt/'s environment, which carries the trino client.
py() { uv run --locked -q --project "$REPO/dbt" python "$@"; }

# snapshot <user> <catalog.schema> <table> [ref]: the snapshot a ref (default main) names.
snapshot() { q "$1" "SELECT snapshot_id FROM $2.\"$3\$refs\" WHERE name = '${4:-main}'"; }

# Lakekeeper's REST catalog on the port the stack publishes (no authentication on this stack).
LK=http://127.0.0.1:18181/catalog/v1
lk_prefix() { curl -sf "$LK/config?warehouse=reference_data" | python3 -c 'import json, sys; print(json.load(sys.stdin)["defaults"]["prefix"])'; }
# lk_table <namespace> <table>: the table's metadata as the catalog holds it, JSON.
lk_table() { curl -sf "$LK/$(lk_prefix)/namespaces/$1/tables/$2"; }
