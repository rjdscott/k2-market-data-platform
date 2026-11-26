#!/usr/bin/env bash
# dbt/ with no stack and no Trino. The project has no model today: the old marts retired
# to legacy/v3-lake-ingest/ (ADR-056), and phase 002 lands the first new one. So:
#
#   1. lint: sqlfluff over the models, no warehouse needed (dbt/.sqlfluff).
#   2. dbt deps, parse and compile --target trino, then the manifest check
#      (scripts/dbt_manifest_check.py): layering, places, every mart's grain test,
#      against the marts the contracts declare. With no model and no mart, this is
#      the zero-marts project k2gen writes (sources: [], selectors: []) still parsing.
#      Nothing listens on the profile's host and port here, so a step that tried to
#      reach a warehouse would fail rather than touch the stack.
#   3. dbt build --selector <domain> with its unit tests, on a throwaway Trino: SKIP,
#      with a line saying so, until phase 002 brings a model to build.
#   4. k2_input's render cases (dbt/macros/k2_input.sql, interface A7), in its own
#      project dbt/tests/k2_input/ (a symlink to the macro, so the one shipped is the
#      one tested): pinned (FOR VERSION AS OF BIGINT '<id>'), unpinned (no var), a
#      unit test with the var set (the fixture's CTE, no clause), a var without the
#      key, and a snapshot that is null, a string, a float or a boolean (each a
#      compiler error at parse, never a guessed id). No warehouse: a unit test's SQL is only
#      rendered when dbt sends it, so it is read from dbt's log of the one statement
#      it tried before the refused connection; the step refuses to run at all if
#      anything listens on the profile's port.
#
#   make test-dbt              # locally
#   CI job "dbt (marts)" runs the same script.
set -euo pipefail
cd "$(dirname "$0")/../dbt"

uv sync --locked -q
uv run --locked sqlfluff lint models
echo "ok lint: sqlfluff over $(find models -name '*.sql' | wc -l) model file(s), dbt/.sqlfluff"

export K2_TRINO_HOST=127.0.0.1 K2_TRINO_PORT=${K2_TRINO_TEST_PORT:-18089}
uv run --locked dbt deps -q
uv run --locked dbt parse -q
uv run --locked dbt compile -q --target trino
uv run --locked python ../scripts/dbt_manifest_check.py target/manifest.json
echo "ok parse: dbt deps, parse and compile, and the manifest check"

echo "SKIP dbt build with unit tests: no model to build until phase 002 lands the first (ADR-056)"

if (exec 3<>"/dev/tcp/$K2_TRINO_HOST/$K2_TRINO_PORT") 2>/dev/null; then
  echo "FAIL k2_input: something listens on $K2_TRINO_HOST:$K2_TRINO_PORT, and this step must reach no warehouse"; exit 1
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
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
if out=$(k2i missing parse --vars '{"k2_inputs": {"other/table": {"snapshot": 1}}}' 2>&1); then
  fail "missing key: parse passed"
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
echo "ok k2_input: pinned, unpinned, a unit test with the var set, a var without the key, and $n snapshots that are not integers (dbt/tests/k2_input/)"
echo "dbt-test: PASS"
