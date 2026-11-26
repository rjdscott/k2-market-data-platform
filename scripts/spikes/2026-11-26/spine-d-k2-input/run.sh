#!/usr/bin/env bash
# Spike N0-4(d): k2_input's FOR VERSION AS OF beside a unit test that mocks the same source, in
# one `dbt build` (IMPLEMENTATION-N0-N1 A7, its invocation split PROPOSED pending this).
# Upstream `events` gets two batches; the first is the pinned snapshot. Then four builds:
#   1. A7's macro as written (naive_k2_input.sql), var set: does the unit test survive the clause?
#   2. project/macros/k2_input.sql, var set: unit test, model at the pin, data tests, one build
#   3. the same, var absent: unpinned
#   4. the same, var set without the key: a compiler error
# Writes up in docs/research/2026-11-26-release-spine-spikes.md, section (d).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../spine-lib.sh"
SCHEMAS=(spike_n04dbt_d spike_n04dbt_d_up)
spine_start d 18192
W=dbt_spike UP=spike.spike_n04dbt_d_up OUT=spike.spike_n04dbt_d.silver

q $W "CREATE SCHEMA $UP" >/dev/null
q $W "CREATE SCHEMA spike.spike_n04dbt_d" >/dev/null
q $W "CREATE TABLE $UP.events (id bigint, amount bigint)" >/dev/null
q $W "INSERT INTO $UP.events VALUES (1, 100), (2, 200)" >/dev/null
s1=$(snapshot $W "$UP" events)
q $W "INSERT INTO $UP.events VALUES (3, 300)" >/dev/null   # a later batch, not audited
echo "setup: $UP.events, snapshot $s1 holds ids 1-2 (the audited pin); the current snapshot adds id 3"
pinned="{\"k2_inputs\": {\"up/events\": {\"relation\": \"$UP.events\", \"snapshot\": $s1}}}"
summary() { grep -oE 'Done\. PASS=[0-9]+ WARN=[0-9]+ ERROR=[0-9]+ SKIP=[0-9]+' <<< "$1" || echo "(no summary)"; }
unit_sql="$WORK/target/compiled/spine_d/models/_models.yml/models/silver_doubles_amount.sql"

echo; echo "== 1. A7's macro as written, dbt build --vars k2_inputs (pin $s1)"
use_project "$HERE/project" spike_n04dbt_d
cp "$HERE/naive_k2_input.sql" "$WORK/project/macros/k2_input.sql"
rc=0; out=$(dbt_ build --vars "$pinned" 2>&1) || rc=$?
echo "exit $rc; $(summary "$out")"
grep -E 'silver::silver_doubles_amount|OK created' <<< "$out" | sed 's/\x1b\[[0-9;]*m//g; s/^/  /' || true
echo "  unit test SQL: $(grep -oE 'from __dbt__cte__up__events.*' "$unit_sql" | head -1)"
echo "  Trino on a CTE: WITH c AS (SELECT 1 AS x) SELECT * FROM c FOR VERSION AS OF BIGINT '42' returns" \
  "$(q $W "WITH c AS (SELECT 1 AS x) SELECT * FROM c FOR VERSION AS OF BIGINT '42'") row(s); on a table:" \
  "$(qx $W "SELECT * FROM $UP.events FOR VERSION AS OF BIGINT '42'")"
naive_rc=$rc

echo; echo "== 2. the working macro, the same dbt build --vars k2_inputs (pin $s1)"
use_project "$HERE/project" spike_n04dbt_d
rc=0; out=$(dbt_ build --vars "$pinned" 2>&1) || rc=$?
echo "exit $rc; $(summary "$out")"
grep -E 'silver::silver_doubles_amount|OK created|PASS (not_null|unique)' <<< "$out" | sed 's/\x1b\[[0-9;]*m//g; s/^/  /'
model_rows=$(q $W "SELECT count(*) FROM $OUT")
echo "  model SQL: $(grep -oE 'from "spike"\."spike_n04dbt_d_up"\."events".*' "$WORK/target/run/spine_d/models/silver.sql" | head -1)"
echo "  unit test SQL: $(grep -oE 'from __dbt__cte__up__events.*' "$unit_sql" | head -1)"
echo "  $OUT holds $model_rows rows (the pin has 2, the current snapshot 3)"
pinned_rc=$rc

echo; echo "== 3. the working macro, dbt build with no vars"
rc=0; out=$(dbt_ build 2>&1) || rc=$?
echo "exit $rc; $(summary "$out"); $OUT holds $(q $W "SELECT count(*) FROM $OUT") rows"
echo "  model SQL: $(grep -oE 'from "spike"\."spike_n04dbt_d_up"\."events".*' "$WORK/target/run/spine_d/models/silver.sql" | head -1)"
unpinned_rc=$rc

echo; echo "== 4. the working macro, k2_inputs set without the key up/events"
rc=0; out=$(dbt_ build --vars '{"k2_inputs": {"other/table": {"snapshot": 1}}}' 2>&1) || rc=$?
echo "exit $rc: $(grep -m1 -oE 'k2_input: .*' <<< "$out")"
missing_rc=$rc

echo; echo "== 5. A7's split, its first invocation, on a product's first run (its schema not made yet)"
q $W "DROP SCHEMA spike.spike_n04dbt_d CASCADE" >/dev/null
rc=0; out=$(dbt_ test --select test_type:unit 2>&1) || rc=$?
echo "exit $rc: $(grep -m1 -oE 'message="[^"]*"' <<< "$out")"
split_rc=$rc

echo
echo "finding (d):"
if [ "$naive_rc" = 0 ]; then
  echo "  A7 as written does not break: the unit test renders 'from __dbt__cte__up__events FOR VERSION AS OF <id>' and Trino 484"
  echo "  silently ignores a version clause on a CTE (a table refuses an unknown id), so the unit test passes on its fixture."
  echo "  That rests on undocumented leniency; the macro also skips the clause when model.resource_type == 'unit_test'."
else
  echo "  A7 as written breaks the unit test (exit $naive_rc, section 1)."
fi
if [ "$pinned_rc" = 0 ] && [ "$model_rows" = 2 ] && [ "$unpinned_rc" = 0 ] && [ "$missing_rc" != 0 ]; then
  echo "  project/macros/k2_input.sql holds all three render cases in ONE dbt build: pinned (read snapshot $s1, 2 rows of 3),"
  echo "  unpinned (no var), unit test (fixture CTE, no clause); a var without the key fails at compile."
else
  echo "  The working macro did not hold every case: pinned exit $pinned_rc with $model_rows rows, unpinned exit $unpinned_rc, missing key exit $missing_rc."
fi
if [ "$split_rc" != 0 ]; then
  echo "  A7's invocation split is not needed, and its first invocation (dbt test, unit tests only) fails on a product's first run:"
  echo "  dbt makes a unit test's scratch table in the model's schema and only dbt build creates schemas. Drop the split."
fi
