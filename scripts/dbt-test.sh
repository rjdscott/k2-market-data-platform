#!/usr/bin/env bash
# dbt/ with no stack and no Trino. The project has no model today: the old marts retired
# to legacy/v3-lake-ingest/ (ADR-056), and phase 002 lands the first new one. So:
#
#   1. lint: sqlfluff over the models, no warehouse needed (dbt/.sqlfluff).
#   2. dbt deps, parse and compile --target trino, then the manifest check
#      (scripts/dbt_manifest_check.py): layering, places, every mart's grain test,
#      against the marts the contracts declare. With no model and no mart, this is
#      this tree with no sources or selectors (k2gen compiles those into a release) parsing.
#      Nothing listens on the profile's host and port here, so a step that tried to
#      reach a warehouse would fail rather than touch the stack.
#   3. after step 4: the real contracts compiled into a trial release
#      (scripts/verify/ci-validate.sh), and every compiled product, one selector each, built by
#      one dbt build with its unit tests on a throwaway lake (scripts/lib/throwaway-lake.sh), as
#      its builder dbt_<fence> over HTTPS, from a container on the lake's network. Then the
#      results hook's table (dbt/macros/k2_results.sql) must hold one row per test of
#      run_results.json, same status, under the run id passed: checked on a failed build too,
#      which then fails the step. The lake is removed on exit. The products build in the order
#      their input ports give (pipeline.json), each input pinned in k2_inputs to its producer's
#      main once that producer built and passed (the wrapper pins the audited tag instead). This
#      proves the models' logic and tests on the real engine; the wrapper, the tag and the reader
#      views are scripts/verify/dbt-wrapper.sh's.
#   4. k2_input's render cases (dbt/macros/k2_input.sql, interface A7), in its own
#      project dbt/tests/k2_input/ (a symlink to the macro, so the one shipped is the
#      one tested): pinned (FOR VERSION AS OF BIGINT '<id>'), unpinned (no var), a
#      unit test with the var set (the fixture's CTE, no clause), a var without the
#      key (it parses, as another product's model must, and fails to compile), and a
#      snapshot that is null, a string, a float or a boolean (each a compiler error at
#      parse, never a guessed id); an absent input, {"absent": true} (zero rows of every
#      column the source declares, each cast to its own type), the same in a unit test (the
#      fixture's CTE), and an absent entry in any other form (a compiler error at parse).
#      No warehouse: a unit test's SQL is only
#      rendered when dbt sends it, so it is read from dbt's log of the one statement
#      it tried before the refused connection; the step refuses to run at all if
#      anything listens on the profile's port.
#
#   make test-dbt              # locally
#   make test-dbt SELECTOR=x   # one selector and the products its inputs name, transitively
#   CI job "dbt (marts)" runs the same script.
set -euo pipefail
cd "$(dirname "$0")/../dbt"

uv sync --locked -q
uv run --locked sqlfluff lint models
echo "ok lint: sqlfluff over $(find models -name '*.sql' | wc -l) model file(s), dbt/.sqlfluff"

# The profile's names (dbt/profiles.yml: password login over HTTPS, ADR-060) with values
# that reach nothing: no Trino listens on this port, so the password is a placeholder, not a
# secret, and the CA path only has to exist (a refused connection never reads it).
export K2_TRINO_HOST=127.0.0.1 K2_TRINO_INTERNAL_PORT=${K2_TRINO_TEST_PORT:-18089}
export DBT_ENV_SECRET_K2_TRINO_PASSWORD=nothing-listens K2_TRINO_CA=/dev/null
uv run --locked dbt deps -q
uv run --locked dbt parse -q
uv run --locked dbt compile -q --target trino
uv run --locked python ../scripts/dbt_manifest_check.py target/manifest.json
echo "ok parse: dbt deps, parse and compile, and the manifest check"

if (exec 3<>"/dev/tcp/$K2_TRINO_HOST/$K2_TRINO_INTERNAL_PORT") 2>/dev/null; then
  echo "FAIL k2_input: something listens on $K2_TRINO_HOST:$K2_TRINO_INTERNAL_PORT, and this step must reach no warehouse"; exit 1
fi
tmp=$(mktemp -d)
P=dbt-test-$$  # the throwaway lake's prefix, this run's own
trap 'bash ../scripts/lib/throwaway-lake.sh down "$P" >/dev/null 2>&1 || echo "dbt-test: remove the lake: bash scripts/lib/throwaway-lake.sh down $P"
  chmod -R u+w "$tmp"; rm -rf "$tmp"' EXIT
k2i() {  # k2i <out dir> <dbt command> [args]: the test project, a fresh target and log each time
  local out=$1; shift
  uv run --locked dbt "$@" --project-dir tests/k2_input --profiles-dir . --no-partial-parse \
    --target-path "$tmp/$out" --log-path "$tmp/$out"
}
fail() { echo "FAIL k2_input $1"; exit 1; }
pin='{"k2_inputs": {"up/events": {"snapshot": 42}}}'
model=compiled/k2_input_test/models/reads_input.sql
k2i pinned compile -q --vars "$pin"
grep -qxF "from \"fixture\".\"up__base\".\"events\" FOR VERSION AS OF BIGINT '42'" "$tmp/pinned/$model" \
  || fail "pinned: $(tail -1 "$tmp/pinned/$model")"
k2i unpinned compile -q
grep -qxF 'from "fixture"."up__base"."events"' "$tmp/unpinned/$model" || fail "unpinned: $(tail -1 "$tmp/unpinned/$model")"
k2i unit test -q --select test_type:unit --vars "$pin" >/dev/null 2>&1 || true  # refused connection: exits 1
grep -q 'Connection refused' "$tmp/unit/dbt.log" || fail "unit test: did not stop at the refused connection"
grep -qx 'from __dbt__cte__up__events' "$tmp/unit/dbt.log" \
  || fail "unit test: $(grep -m1 'from __dbt__cte__up__events' "$tmp/unit/dbt.log")"
missing='{"k2_inputs": {"other/table": {"snapshot": 1}}}'
k2i missing-parse parse --vars "$missing" >/dev/null 2>&1 || fail "missing key: parse refused (another product's model, unselected, names no pinned input)"
if out=$(k2i missing compile --vars "$missing" 2>&1); then
  fail "missing key: compile passed"
fi
grep -qF 'k2_input: k2_inputs is set but has no entry up/events' <<< "$out" || fail "missing key: $out"
n=0
for entry in '{"snapshot": null}' '{}' '{"snapshot": "42"}' '{"snapshot": 4.2}' '{"snapshot": true}'; do
  n=$((n + 1))
  if out=$(k2i "bad$n" parse --vars "{\"k2_inputs\": {\"up/events\": $entry}}" 2>&1); then
    fail "snapshot $entry: parse passed"
  fi
  grep -qF 'k2_input: k2_inputs entry up/events has no integer snapshot' <<< "$out" || fail "snapshot $entry: $out"
done
absent='{"k2_inputs": {"up/events": {"absent": true}}}'
k2i absent compile -q --vars "$absent"
grep -qxF 'from (select cast(null as bigint) as "id", cast(null as bigint) as "amount", cast(null as decimal(18,8)) as "price", cast(null as timestamp(6) with time zone) as "seen_at" where false)' \
  "$tmp/absent/$model" || fail "absent: $(tail -1 "$tmp/absent/$model")"
k2i absent-unit test -q --select test_type:unit --vars "$absent" >/dev/null 2>&1 || true  # refused connection: exits 1
grep -q 'Connection refused' "$tmp/absent-unit/dbt.log" || fail "absent unit test: did not stop at the refused connection"
grep -qx 'from __dbt__cte__up__events' "$tmp/absent-unit/dbt.log" \
  || fail "absent unit test: $(grep -m1 'from ' "$tmp/absent-unit/dbt.log")"
m=0
for entry in '{"absent": false}' '{"absent": "true"}' '{"absent": true, "snapshot": 42}'; do
  m=$((m + 1))
  if out=$(k2i "absent-bad$m" parse --vars "{\"k2_inputs\": {\"up/events\": $entry}}" 2>&1); then
    fail "absent $entry: parse passed"
  fi
  grep -qF 'k2_input: k2_inputs entry up/events is absent only as {"absent": true}' <<< "$out" || fail "absent $entry: $out"
done
echo "ok k2_input: pinned, unpinned, a unit test with the var set, a var without the key, $n snapshots that are not integers, absent (zero typed rows; a unit test's CTE) and $m absent entries of another form (dbt/tests/k2_input/)"

rel=$(bash ../scripts/verify/ci-validate.sh "$tmp/ci" 2>"$tmp/ci.log" | tail -1) || { cat "$tmp/ci.log"; exit 1; }
# Every selector, each after the products its input ports name (scripts/dbt_order.py); with SELECTOR,
# that one and its inputs only. An unknown one fails here, before any container starts.
order=$(python3 ../scripts/dbt_order.py "$rel" "${SELECTOR:-}") || exit 1
echo "dbt-test: building in order: $order"
bash ../scripts/lib/throwaway-lake.sh up "$P" "$rel"
state=$(bash ../scripts/lib/throwaway-lake.sh state "$P")
uv_image=$(sed -n 's/^UV_IMAGE=//p' ../scripts/lib/throwaway-lake.sh)  # the lake's tools pin, not a second one
release=$(basename "$rel") built=0 failed=()
mkdir -p "$HOME/.cache/k2-dbt-test-uv"  # uv's download cache across runs; the venv is per run
for sel in $order; do
  run_id="dbt-test/$P-$sel" out=$state/dbt-$sel
  mkdir -p "$out"
  # The wrapper's --vars (docker/dbt-runner/k2_dbt.py): each input pinned to a snapshot. No wrapper
  # runs here and no tag exists, so the pin is the producer's main once its build and tests passed.
  if ! vars=$(bash ../scripts/lib/throwaway-lake.sh py "$P" - "$sel" "$run_id" "$release" "${failed[@]}" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "/src/scripts")
from verify import catalog_drift as cd
from k2gen import model
sel, run_id, release, *failed = sys.argv[1:]
pipes = [json.loads(f.read_text()) for f in Path(f"/work/releases/{release}/compiled/products").glob("*/pipeline.json")]
me = next(d for d in pipes if d.get("build", {}).get("selector") == sel)
pins = {}
for i in me.get("inputs", []):
    domain, producer, table = i["contractId"].split(".")
    if model.fence(producer) in failed:
        sys.exit(f"dbt-test: {sel}: its input {i['contractId']} did not build and pass")
    meta = cd.Catalog("http://lakekeeper:8181/catalog", model.warehouse(domain)).table(model.base_namespace(producer), table)
    main = ((meta or {}).get("refs") or {}).get("main")
    if main is None:
        sys.exit(f"dbt-test: {sel}: its input {i['contractId']} has no snapshot")
    pins[f"{model.fence(producer)}/{table}"] = {"snapshot": main["snapshot-id"]}
print(json.dumps({"k2_inputs": pins, "k2_run_id": run_id, "k2_release": release}))
PY
  ); then
    failed+=("$sel"); continue
  fi
  # The builder's password from its mode-600 file into dbt's scrubbed variable inside the
  # container, never on a command line; the CA at the profile's default path.
  if ! docker run --rm --label "k2.throwaway=$P" --network "$P-net" --memory 1g --cpus 1 --user "$(id -u):$(id -g)" \
      -e HOME=/tmp -e UV_CACHE_DIR=/uv -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_LINK_MODE=copy -e UV_NO_PROGRESS=1 \
      -e UV_PYTHON_DOWNLOADS=never -e DBT_SEND_ANONYMOUS_USAGE_STATS=false -e K2_TRINO_HOST=trino \
      -e K2_TRINO_INTERNAL_PORT=8443 -e "K2_TRINO_USER=dbt_$sel" \
      -v "$HOME/.cache/k2-dbt-test-uv:/uv" -v "$PWD:/dbt:ro" -v "$state/releases/$release/compiled/dbt:/project:ro" -v "$out:/out" \
      -v "$state/secrets/k2/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro" \
      -v "$state/secrets/k2/principals/dbt_$sel.password:/run/k2-secrets/builder.password:ro" \
      -w /project --entrypoint sh "$uv_image" -c \
      'export DBT_ENV_SECRET_K2_TRINO_PASSWORD="$(cat /run/k2-secrets/builder.password)"; exec uv run -q --project /dbt --locked \
         dbt build --selector "$1" --vars "$2" --profiles-dir /dbt --target-path /out/target --log-path /out' \
      sh "$sel" "$vars"; then
    failed+=("$sel")
  fi
  # One row per test of the invocation, same status, under this run id, failed build or not.
  bash ../scripts/lib/throwaway-lake.sh py "$P" - "$sel" "$run_id" "/work/dbt-$sel/target/run_results.json" <<'PY' || exit 1
import json, sys
from pathlib import Path
sys.path.insert(0, "/src/scripts")
import access_verify as av
from k2gen import model
sel, run_id, results = sys.argv[1:]
trino, env = av.connect(Path("/work/env.json"))
p = next(p for p in av.load(Path(env["release"])).products if model.fence(p.name) == sel)
table = model.quote_relation(model.build_relation(p.domain, p.name, "dq_test_results"))
want = {r["unique_id"]: r["status"] for r in json.loads(Path(results).read_text())["results"]
        if r["unique_id"].split(".")[0] in ("test", "unit_test")}
rows = trino.ok(model.builder(p.name), "SELECT unique_id, status, failures, tested, release_id, dbt_invocation_id "
                f"FROM {table} WHERE run_id = {model.sql_literal(run_id)} ORDER BY unique_id") if want else []
for r in rows:
    print(f"  {table} run_id={run_id}: {' | '.join(map(str, r))}")
got = {r[0]: r[1] for r in rows}
if len(rows) != len(want) or got != want or {r[4] for r in rows} - {Path(env["release"]).name}:
    sys.exit(f"FAIL results: {table} under {run_id} holds {got}, run_results.json {want}, release {Path(env['release']).name}")
print(f"ok results: {len(rows)} row(s) in {table} under run_id {run_id}, one per test, statuses as dbt reported")
PY
  built=$((built + 1))
done
[ ${#failed[@]} = 0 ] || { echo "FAIL dbt build: ${failed[*]} (its results were checked above)"; exit 1; }
echo "ok build: $built compiled product(s) of $release built with their tests on a throwaway lake, every result recorded"
echo "dbt-test: PASS"
