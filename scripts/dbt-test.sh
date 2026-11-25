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
#      their made-up rows, and every data test. Then the domain's snapshot
#      maintenance as its writer (dbt run-operation expire_mart_snapshots), which
#      must reach every table mart of the domain; and the same models run as
#      anyone else, refused on the fence.
#   5. No marts at all: the sources and selectors k2gen writes then, with no
#      models, still parse, so removing the last mart leaves a working project
#      (and a dbt_marts DAG with no task group).
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
  # Snapshot maintenance as the writer: both procedures on every table of the domain.
  out=$(K2_TRINO_USER="dbt_$d" uv run --locked dbt run-operation expire_mart_snapshots 2>&1) || { echo "$out" | tail -20; exit 1; }
  tables=$(uv run --locked python -c 'import json, sys; m = json.load(open("target/manifest.json")); print(" ".join(
    f"\"{n["database"]}\".\"{n["schema"]}\".\"{n["alias"]}\"" for n in m["nodes"].values()
    if n["resource_type"] == "model" and n["database"] == sys.argv[1] and n["config"]["materialized"] == "table"))' "$d")
  [ -n "$tables" ] || { echo "FAIL: domain $d has no table mart to maintain"; exit 1; }
  for rel in $tables; do
    for proc in expire_snapshots remove_orphan_files; do
      grep -qF "expire_mart_snapshots: $rel $proc at 7d" <<< "$out" || { echo "FAIL: no $proc on $rel"; echo "$out" | tail -20; exit 1; }
    done
    echo "ok maintenance: $rel, expire_snapshots and remove_orphan_files at 7d as dbt_$d"
  done
  # The fence through dbt: the same models run as anyone else are refused.
  if out=$(K2_TRINO_USER=dbt_someone_else uv run --locked dbt run --selector "$d" 2>&1); then
    echo "FAIL: dbt_someone_else built domain $d"; exit 1
  fi
  grep -q "Access Denied" <<< "$out" || { echo "FAIL: dbt run as dbt_someone_else failed, but not on the fence"; echo "$out" | tail -20; exit 1; }
  echo "ok fence: dbt run --selector $d as dbt_someone_else: $(grep -m1 -oE 'Access Denied: [^"]*' <<< "$out")"
done

# 5. Zero marts: what k2gen writes for no marts, this project's macros, no models.
empty=$(mktemp -d)
trap 'cleanup; rm -rf "$empty"' EXIT
cp -r dbt_project.yml packages.yml package-lock.yml profiles.yml macros "$empty"/
mkdir "$empty/models"
(cd ../platform/k2gen && uv run --locked -q python -c 'import sys; from pathlib import Path; from k2gen import generate
out = Path(sys.argv[1])
(out / "models/_sources.yml").write_text(generate._dbt_sources([], {}, {}))
(out / "selectors.yml").write_text(generate._dbt_selectors([]))' "$empty")
grep -qx 'sources: \[\]' "$empty/models/_sources.yml" && grep -qx 'selectors: \[\]' "$empty/selectors.yml"
K2_DBT_PACKAGES="$PWD/dbt_packages" uv run --locked dbt parse -q --project-dir "$empty" --profiles-dir "$empty"
echo "ok zero marts: dbt parse with sources: [] and selectors: [] and no models"
echo "dbt-test: PASS ($IMAGE)"
