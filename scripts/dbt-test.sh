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
#      their made-up rows, and every data test, each of which must have its row in
#      <domain>.dq.dbt_test_results (the on-run-end hook, ADR-046). Then the
#      domain's snapshot maintenance as its writer (dbt run-operation
#      expire_mart_snapshots), which must reach every table mart of the domain and
#      its test results; and the same models run as anyone else, refused on the fence.
#   4b. A failing test still gets its row: a duplicate trade fails a source test,
#      the build exits non-zero, and the failed and skipped tests are recorded; the
#      governance view, rebuilt after, shows that failure and a failed and a raised
#      lake check, to a user who owns none of it.
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

# last_run: "<invocation id> <tests and unit tests it ran>", from the last dbt command's run_results.json.
last_run() {
  uv run --locked python -c 'import json; r = json.load(open("target/run_results.json"))
print(r["metadata"]["invocation_id"], sum(x["unique_id"].split(".")[0] in ("test", "unit_test") for x in r["results"]))'
}
# recorded <domain> <invocation id> <where>: rows of that run in the domain's results, as its writer.
recorded() { sql "dbt_$1" "SELECT count(*) FROM $1.dq.dbt_test_results WHERE invocation_id = '$2' AND writer = 'dbt_$1' AND $3" | tr -d '"'; }

for d in $(sed -n 's/^- name: //p' selectors.yml); do
  K2_TRINO_USER="dbt_$d" uv run --locked dbt build --selector "$d"
  read -r inv n < <(last_run)
  [ "$n" -gt 0 ] && [ "$(recorded "$d" "$inv" "status = 'pass'")" = "$n" ] && [ "$(recorded "$d" "$inv" "true")" = "$n" ] \
    || { echo "FAIL: dbt build --selector $d ran $n tests, $d.dq.dbt_test_results holds $(recorded "$d" "$inv" true) rows of it"; exit 1; }
  echo "ok results: all $n tests and unit tests of dbt build --selector $d recorded, passed, in $d.dq.dbt_test_results as dbt_$d"
  # Snapshot maintenance as the writer: both procedures on every table of the domain, and
  # on its test results after optimize.
  out=$(K2_TRINO_USER="dbt_$d" uv run --locked dbt run-operation expire_mart_snapshots 2>&1) || { echo "$out" | tail -20; exit 1; }
  tables=$(uv run --locked python -c 'import json, sys; m = json.load(open("target/manifest.json")); print(" ".join(
    f"\"{n["database"]}\".\"{n["schema"]}\".\"{n["alias"]}\"" for n in m["nodes"].values()
    if n["resource_type"] == "model" and n["database"] == sys.argv[1] and n["config"]["materialized"] == "table"))' "$d")
  results="\"$d\".\"dq\".\"dbt_test_results\""
  grep -qF "expire_mart_snapshots: $results optimize" <<< "$out" || { echo "FAIL: no optimize on $results"; echo "$out" | tail -20; exit 1; }
  for rel in $tables $results; do
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

# 4b. Test results end to end (ADR-046). governance came first above (selectors.yml is
# sorted), when no domain had a results table, and its view built with none.
view() { sql someone "SELECT count(*) FROM governance.quality_results.dq_results WHERE $1" | tr -d '"'; }
[ "$(view "source_system = 'dbt'")" = 0 ] || { echo "FAIL: governance's first build saw a results table"; exit 1; }
echo "ok view: governance.quality_results.dq_results built when no domain had results"
# Two trades with one identifier, inside the 6-hour window of the uniqueness test k2gen
# generates on gold.trades, fail that source test; the marts that read it are skipped.
sql "dbt_$writer" "INSERT INTO $writer.gold.trades (exchange, canonical_symbol, trade_id, exchange_ts)
  VALUES ('alpha', 'AAA/BBB', 'dup', current_timestamp), ('alpha', 'AAA/BBB', 'dup', current_timestamp)" >/dev/null
if out=$(K2_TRINO_USER=dbt_market_analytics uv run --locked dbt build --selector market_analytics 2>&1); then
  echo "FAIL: dbt build --selector market_analytics passed with a duplicate trade"; exit 1
fi
read -r inv n < <(last_run)
failed=$(recorded market_analytics "$inv" "status = 'fail' AND tested = 'lake.gold.trades' AND failures > 0")
skipped=$(recorded market_analytics "$inv" "status = 'skipped'")
[ "$failed" = 1 ] && [ "$skipped" -gt 0 ] && [ "$(recorded market_analytics "$inv" true)" = "$n" ] \
  || { echo "FAIL: a failing build recorded $failed failed and $skipped skipped of $n tests"; echo "$out" | tail -30; exit 1; }
echo "ok failure recorded: dbt build exited non-zero, and all $n results are in market_analytics.dq.dbt_test_results:" \
  "the failed source test on lake.gold.trades, $skipped skipped behind it ($(grep -m1 -oE 'record_test_results: .*' <<< "$out"))"
# A failed lake check and one that raised, as maintenance.run_audits files them.
sql "dbt_$writer" "INSERT INTO $writer.audit.checks VALUES
  (current_timestamp, 'maintenance', 'silver_parity', 'lake.silver.trades_alpha', false, 5, 'five rows differ'),
  (current_timestamp, 'maintenance', 'kraken_checksum', 'lake.silver.book_alpha', false, -1, 'check raised ValueError: boom')" >/dev/null
K2_TRINO_USER=dbt_governance uv run --locked dbt build --selector governance
want="source_system = 'dbt' AND domain = 'market-analytics' AND subject = 'lake.gold.trades' AND status = 'fail'"
[ "$(view "$want")" = 1 ] \
  && [ "$(view "source_system = 'dbt' AND domain = 'market-analytics' AND status = 'error' AND detail LIKE 'skipped%'")" = "$skipped" ] \
  && [ "$(view "source_system = 'dbt' AND domain = 'governance' AND status = 'pass'")" -gt 0 ] \
  && [ "$(view "source_system = 'lake_audit' AND subject = 'lake.silver.trades_alpha' AND status = 'fail'")" = 1 ] \
  && [ "$(view "source_system = 'lake_audit' AND subject = 'lake.silver.book_alpha' AND status = 'error'")" = 1 ] \
  || { echo "FAIL: governance.quality_results.dq_results does not show the failures"; sql someone "SELECT * FROM governance.quality_results.dq_results WHERE status <> 'pass'"; exit 1; }
echo "ok view: as someone, governance.quality_results.dq_results shows the failed source test, the $skipped skipped as error," \
  "governance's own passes, a failed and a raised lake check:"
sql someone "SELECT source_system, domain, subject, status FROM governance.quality_results.dq_results
  WHERE status <> 'pass' AND subject LIKE 'lake.%' ORDER BY 1, 4, 3" | sed 's/^/  /'
# No writer files results for another domain: each table is its own writer's alone.
for pair in "governance market_analytics" "market_analytics governance"; do
  read -r w c <<< "$pair"
  out=$(sql "dbt_$w" "INSERT INTO $c.dq.dbt_test_results (invocation_id) VALUES ('forged')" 2>&1) || true
  grep -q "Access Denied" <<< "$out" || { echo "FAIL: dbt_$w inserted into $c.dq.dbt_test_results"; echo "$out"; exit 1; }
  echo "ok fence: dbt_$w into $c.dq.dbt_test_results: $(grep -m1 -oE 'Access Denied: [^"]*' <<< "$out")"
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
