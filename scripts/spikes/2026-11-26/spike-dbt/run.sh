#!/usr/bin/env bash
# Spike 2026-11-26: dbt-core 1.12.7 and dbt-trino 1.12.0 (the pins in dbt/uv.lock) on the
# stack's Trino 484, through Lakekeeper. Write-up:
# docs/research/2026-11-26-unified-platform-architecture/SPIKE-DBT.md
#
#   bash scripts/spikes/2026-11-26/spike-dbt/run.sh
#
# Writes only the scratch schema reference_data.spike_dbt_20261126, as the writer
# dbt_reference_data, and drops it on exit. Synthetic rows only. Needs the stack's
# k2-trino and uv. Prints one line per finding; exits 1 if a finding no longer holds.
#
# Added after the spike, for the delete-file bound in
# docs/benchmarks/2026-11-26-product-run-cost.md: the delete files a MERGE leaves, what
# optimize leaves, and an insert-only merge (phase 4 of models/inc_merge.sql).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
TRINO=k2-trino
WRITER=dbt_reference_data
S=reference_data.spike_dbt_20261126
WORK=$(mktemp -d)
cp -r "$HERE"/dbt_project.yml "$HERE"/profiles.yml "$HERE"/models "$HERE"/snapshots "$WORK"/
export DBT_SEND_ANONYMOUS_USAGE_STATS=false DBT_PROFILES_DIR="$WORK" DBT_PROJECT_DIR="$WORK"

sql() { docker exec "$TRINO" trino --user "$WRITER" --output-format TSV --execute "$1" 2>&1; }
# rows <query>: the result on one line, columns joined by ',' and rows by ';'
rows() { sql "$1" | tr '\t' ',' | paste -sd';'; }
# dbt <args>: the repo's locked dbt environment on the spike project; output in $WORK/dbt.out
dbt() { uv run --project "$ROOT/dbt" --locked -q dbt "$@" > "$WORK/dbt.out" 2>&1; }
deletes() { sql "SELECT count(*) FROM $S.\"$1\$files\" WHERE content <> 0"; }
# stats: min / median / max of the numbers on stdin
stats() { sort -n | awk '{a[NR] = $1} END {m = NR % 2 ? a[(NR + 1) / 2] : (a[NR / 2] + a[NR / 2 + 1]) / 2
  printf "%.2f / %.2f / %.2f s (n = %d)", a[1], m, a[NR], NR}'; }

fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }
same() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1: want [$2], have [$3]"; fi; }
drop() { sql "DROP SCHEMA IF EXISTS $S CASCADE" >/dev/null || true; }
cleanup() { drop; rm -rf "$WORK"; }
trap cleanup EXIT
trap 'echo "FAIL at line $LINENO; the last dbt output:"; tail -20 "$WORK/dbt.out" 2>/dev/null' ERR
drop

# 1. Incremental merge on a unique key.
dbt run --select inc_merge --vars '{phase: 1}'
dbt run --select inc_merge --vars '{phase: 2}'
grep -q MERGE "$WORK/dbt.out" && ok "1 the second run logged MERGE" || bad "1 no MERGE in the second run"
same "1 merge: overlapping rows updated, no duplicates" "1,a,10;2,b2,21;3,c2,31;4,d,40" \
  "$(rows "SELECT id, name, amount FROM $S.inc_merge ORDER BY id")"
d2=$(deletes inc_merge)

# 2. on_schema_change='append_new_columns'.
dbt run --select inc_merge --vars '{phase: 3}'
same "2 append_new_columns: column added, old rows null" "1,a,10,-;2,b2,21,-;3,c2,31,-;4,d3,41,x;5,e,50,y" \
  "$(rows "SELECT id, name, amount, coalesce(extra, '-') FROM $S.inc_merge ORDER BY id")"
d3=$(deletes inc_merge)
sql "ALTER TABLE $S.inc_merge EXECUTE optimize" >/dev/null
dopt=$(deletes inc_merge)
dbt run --select inc_merge --vars '{phase: 4}'
d4=$(deletes inc_merge)
echo "     delete files on inc_merge: $d2 after the first update merge, $d3 after the second," \
  "$dopt after optimize, $d4 after an insert-only merge"
[ "$d2" -ge 1 ] && [ "$dopt" = 0 ] && [ "$d4" = 0 ] \
  && ok "delete files: an updating merge writes them, optimize clears them, an insert-only merge writes none" \
  || bad "delete files: want >= 1 after an update merge, 0 after optimize and 0 after an insert-only merge"

# 3. An enforced contract: a type mismatch fails before the table changes; a rebuild is two commits.
dbt run --select contracted
dbt run --select contracted
same "3 a contracted rebuild is two commits, the first empty" "0;1" \
  "$(rows "SELECT summary['total-records'] FROM $S.\"contracted\$snapshots\" ORDER BY committed_at DESC LIMIT 2" \
    | awk -F';' '{print $2 ";" $1}')"
if dbt run --select contracted --vars '{bad_type: true}'; then
  bad "3 a contract type mismatch built"
else
  grep -qi contract "$WORK/dbt.out" && ok "3 a contract type mismatch fails" || bad "3 failed, but not on the contract"
fi
same "3 the contracted table is unchanged" "1,1.5" "$(rows "SELECT id, amount FROM $S.contracted")"

# 4. Snapshots (type-2 history), both strategies, no override.
dbt run --select snap_src --vars '{snap_phase: 1}' && dbt snapshot --vars '{snap_phase: 1}'
dbt run --select snap_src --vars '{snap_phase: 2}' && dbt snapshot --vars '{snap_phase: 2}'
for t in snap_ts snap_check; do
  same "4 $t: four versions, one closed" "4,1" "$(rows "SELECT count(*), count(dbt_valid_to) FROM $S.$t")"
done
vtype() { sql "SELECT data_type FROM reference_data.information_schema.columns
  WHERE table_schema = 'spike_dbt_20261126' AND table_name = '$1' AND column_name = 'dbt_valid_from'"; }
ts=$(vtype snap_ts); ck=$(vtype snap_check)
case "$ts|$ck" in
  "$ck|$ts") bad "4 the two strategies now give one type: $ts" ;;
  timestamp\(6\)*\|timestamp\(6\)*) ok "4 validity columns timestamp(6), and the strategies differ: timestamp $ts, check $ck" ;;
  *) bad "4 validity columns are not timestamp(6): timestamp $ts, check $ck" ;;
esac

# 5. Publish, test, roll back: a failing data test exits 1; the writer's rollback restores the rows.
dbt build --select pub_tbl
good=$(sql "SELECT snapshot_id FROM $S.\"pub_tbl\$snapshots\" ORDER BY committed_at DESC LIMIT 1")
if dbt build --select pub_tbl --vars '{bad: true}'; then
  bad "5 the failing test passed"
else
  grep -q "FAIL 1 unique_pub_tbl_id" "$WORK/dbt.out" && ok "5 the failing data test exits 1" || bad "5 failed for another reason"
fi
same "5 the bad rows are published" "3" "$(sql "SELECT count(*) FROM $S.pub_tbl")"
seen=$(sql "ALTER TABLE $S.pub_tbl EXECUTE rollback_to_snapshot($good);
  SELECT round(to_unixtime(current_timestamp) - to_unixtime(max(committed_at)), 1) FROM $S.\"pub_tbl\$snapshots\"" | tail -1)
same "5 rollback_to_snapshot as the writer restores the rows (bad rows visible ${seen} s)" "1,good1;2,good2" \
  "$(rows "SELECT id, v FROM $S.pub_tbl ORDER BY id")"

# 6. One dbt build of one model with two tests, wall clock: through uv, then the venv's dbt directly.
for how in uv direct; do
  for _ in 1 2 3 4 5; do
    t0=$(date +%s.%N)
    if [ "$how" = uv ]; then dbt build --select pub_tbl; else "$ROOT/dbt/.venv/bin/dbt" build --select pub_tbl -q > "$WORK/dbt.out" 2>&1; fi
    awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN {print b - a}'
  done | stats > "$WORK/t_$how"
done
ok "6 dbt build --select pub_tbl, min / median / max: through uv $(cat "$WORK/t_uv"); direct $(cat "$WORK/t_direct")"

[ "$fail" = 0 ] && echo "spike-dbt: every finding of SPIKE-DBT.md holds" || echo "spike-dbt: a finding changed, see FAIL above"
exit "$fail"
