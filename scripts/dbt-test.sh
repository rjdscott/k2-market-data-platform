#!/usr/bin/env bash
# dbt/ against a throwaway Trino, the image tag docker-compose.yml uses, with no stack:
#
#   1. lint: sqlfluff over the models, no warehouse needed (dbt/.sqlfluff).
#   2. a Trino with docker/trino/etc (rules.json) over the local catalogs k2gen
#      generates (docker/trino/test/local, one shared metastore, as make test-trino
#      uses them), on 127.0.0.1:${K2_TRINO_TEST_PORT:-18089}. Every lake table in
#      dbt/models/_sources.yml is created empty, in the Trino types listed there,
#      through a domain catalog, so `lake.<schema>.<table>` resolves read-only.
#   3. dbt deps, parse and compile --target trino, then the manifest check
#      (scripts/dbt_manifest_check.py): layering, places, every mart's grain test.
#   4. dbt build --selector <domain>, as dbt_<domain>, for every selector in
#      dbt/selectors.yml: the views and tables on empty sources, the unit tests on
#      their made-up rows, and every data test.
#
#   make test-dbt              # locally
#   CI job "dbt" runs the same script.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=$(grep -oE 'trinodb/trino:[^ ]+' docker-compose.yml | head -1)
NAME=k2-dbt-test
PORT=${K2_TRINO_TEST_PORT:-18089}

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

cd dbt
uv sync --locked -q
uv run --locked sqlfluff lint models
echo "ok lint: sqlfluff, dbt/.sqlfluff"

cleanup
docker run -d --name "$NAME" --cpus 2 --memory 4g -p "127.0.0.1:$PORT:8080" \
  -v "$PWD/../docker/trino/etc":/etc/trino:ro -v "$PWD/../docker/trino/test/local":/etc/trino/catalog:ro "$IMAGE" >/dev/null
sql() { docker exec "$NAME" trino --user "$1" --execute "$2"; }
for _ in $(seq 1 60); do
  sql trino 'SELECT 1' >/dev/null 2>&1 && break
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || { docker logs "$NAME" 2>&1 | tail -40; exit 1; }
  sleep 2
done
sql trino 'SELECT 1' >/dev/null || { echo "FAIL: Trino did not start in 120 s"; docker logs "$NAME" 2>&1 | tail -40; exit 1; }

# The source tables, empty. Any domain catalog will do: they share the metastore `lake` reads.
writer=$(sed -n 's/^- name: //p' selectors.yml | head -1)
stmts=$(uv run --locked python - "$writer" <<'EOF'
import sys, yaml
catalog = sys.argv[1]
for src in yaml.safe_load(open("models/_sources.yml"))["sources"]:
    assert src["database"] == "lake", src
    print(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{src['schema']}")
    for t in src["tables"]:
        cols = ", ".join(f"{c['name']} {c['data_type']}" for c in t["columns"])
        print(f"CREATE TABLE {catalog}.{src['schema']}.{t['name']} ({cols})")
EOF
)
while IFS= read -r stmt; do
  out=$(sql "dbt_$writer" "$stmt" 2>&1) || { echo "FAIL fixture: $stmt"; echo "$out"; exit 1; }
  echo "ok fixture: ${stmt:0:100}"
done <<< "$stmts"

export K2_TRINO_HOST=127.0.0.1 K2_TRINO_PORT=$PORT
uv run --locked dbt deps -q
uv run --locked dbt parse -q
uv run --locked dbt compile -q --target trino
uv run --locked python ../scripts/dbt_manifest_check.py target/manifest.json
for d in $(sed -n 's/^- name: //p' selectors.yml); do
  K2_TRINO_USER="dbt_$d" uv run --locked dbt build --selector "$d"
  # The fence through dbt: the same models run as anyone else are refused.
  if out=$(K2_TRINO_USER=dbt_someone_else uv run --locked dbt run --selector "$d" 2>&1); then
    echo "FAIL: dbt_someone_else built domain $d"; exit 1
  fi
  grep -q "Access Denied" <<< "$out" || { echo "FAIL: dbt run as dbt_someone_else failed, but not on the fence"; echo "$out" | tail -20; exit 1; }
  echo "ok fence: dbt run --selector $d as dbt_someone_else: $(grep -m1 -oE 'Access Denied: [^"]*' <<< "$out")"
done
echo "dbt-test: PASS ($IMAGE)"
