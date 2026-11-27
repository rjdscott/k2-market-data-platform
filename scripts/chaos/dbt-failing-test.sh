#!/usr/bin/env bash
# Plan 002's failing test (Verification: "silver at its prior snapshot; rolled_back and expired
# rows"; "also polls the table as a reader through the test window and fails if a failed row was
# ever visible"): on ONE throwaway lake (prefix dbt-failing-test) from <release>, calendars and
# ECB built and published through the dbt wrapper, then ECB built again from a release of this
# tree in which gold carries one planted row, a second (newest day, USD) at rate 999: a DATA test
# (k2_unique_key on rate_date, currency) fails, and the gold unit tests, whose rows are AAA and
# BBB, do not. Each task the release's own code in the DAG factory's shape
# (scripts/lib/run-path-lib.sh).
#
#   bash scripts/chaos/dbt-failing-test.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 calendars builds through the wrapper and its audited tag moves
#   2 the good file lands, passes every check and is published to bronze main
#   3 the wrapper builds ECB on calendars' tested snapshot; a reader reads gold
#   4 the failing build exits 1, k2_unique_key failed, silver and gold are back on their prior
#     snapshots (main = the tag), a rolled_back row for each, and the XCom lists the build's snapshots
#   5 p.analyst-a, polling gold through its reader view every 50 ms from before the build to after
#     its rollback, got the tested rows on every poll and never a row of the failed build
#   6 expire.py removes the listed snapshots with the maintenance key, and the expire task's run
#     record is in the run-record table
# The run record of claim 6: with no Airflow here, this script writes the row the DAG's `record`
# task (docker/airflow/dags/factory.py, Record and rows()) writes for an expire that succeeded:
# task expire, event passed, its result as detail, through the release's own k2runs as svc.runs.
# Plan 002 names that row `expired`; the factory writes `passed` (rows() maps success to passed),
# and no code writes the event `expired`. Which of the two moves is the plan owner's call; the row
# a scheduled run writes is the live stack's run records' to prove. A killed run leaves the lake
# to `bash scripts/lib/throwaway-lake.sh down dbt-failing-test`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start dbt-failing-test "${1:-}" 6
rp_fail_release "$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/fx-reference/ecb-fx-rates/models/gold_eur_rates_daily.sql"
sql = p.read_text()
assert sql.endswith("\nfrom aged\n") and sql.count("\nfrom aged\n") == 1, "the gold model no longer ends 'from aged': move the plant"
p.write_text(sql.removesuffix("from aged\n") + "from (select * from aged union all select rate_date, currency, cast(999 as decimal(20, 10)), "
             "carried_days from aged where currency = 'USD' and rate_date = (select max(rate_date) from aged)) as aged\n")
PY
)"
rp_image
rp_lake
rp_calendars
rp_good
rp_build
probe poll fail & poller=$!
sleep 3  # the poller's first answers, the baseline, before the build starts
build "$IDF" "$ECB" rp-fail
sleep 1
touch "$RP/stop-fail"
wait "$poller"
probe state fail
claim failing
claim poll
expire=$(python3 -c 'import json, sys; lines = [x for x in open(sys.argv[1]) if x.strip()]; print(json.dumps(json.loads(lines[-1])["expire"], separators=(",", ":")))' \
  "$RP/rp-fail.build.out" 2>/dev/null)
[ -n "$expire" ] && [ "$expire" != "[]" ] && task "$IDF" rp-fail expire --expire "$expire"  # factory: an empty list skips expire
# The expire task's run record, as factory.Record writes it for a task that succeeded, from the
# task's result line, by the release's k2runs in the dbt-runner image (it holds trino) as svc.runs.
result=$(python3 -c 'import sys; print([x for x in open(sys.argv[1]) if x.strip()][-1].strip())' "$RP/rp-fail.expire.out" 2>/dev/null)
docker run -i --rm --name "$P-record" --label "k2.throwaway=$P" --pull never --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges --user "$(id -u):$(id -g)" --network "$NET" \
  -e K2_TRINO_HOST -e K2_TRINO_INTERNAL_PORT -e K2_RUNS_USER \
  --mount "type=bind,source=$STATE/releases/$IDF,target=/k2/releases/$IDF,readonly" \
  --mount "type=bind,source=$S/principals/svc.runs.password,target=/run/k2-secrets/runs.password,readonly" \
  --mount "type=bind,source=$S/tls/ca.pem,target=/run/k2-secrets/tls/ca.pem,readonly" \
  --entrypoint /opt/k2/dbt-venv/bin/python "$IMAGE" - "/k2/releases/$IDF" "$IDF" "${result:-{\}}" \
  > "$RP/rp-fail.record.out" 2> "$RP/rp-fail.record.err" <<'PY'
import importlib.util, json, sys
path, release, result = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
spec = importlib.util.spec_from_file_location("k2runs", f"{path}/platform/platform/runs/k2runs.py")
k2runs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k2runs)
def _int(v):  # factory._int
    return v if isinstance(v, int) and not isinstance(v, bool) else None
row = {"run_id": "ecb_fx_rates/rp-fail", "product_id": "fx-reference.ecb-fx-rates", "release_id": release, "task": "expire",
       "event": "passed", "rows_written": _int(result.get("rows")), "snapshot_after": _int(result.get("snapshotAfter")),
       "duration_s": None, "detail": json.dumps(result, separators=(",", ":"), default=str)}
k2runs.record(k2runs.connect(), **row)
print(f"recorded {row['task']} {row['event']}")
PY
echo $? > "$RP/rp-fail.record.rc"
probe state expired
claim expired
finish
