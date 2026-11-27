#!/usr/bin/env bash
# Plan 002's deploy mid-run (Verification: "the run finishes on R-1 and its run record says R-1"):
# against the LIVE stack, run by the operator after the first deploy of the release under test.
# A run of <dag_id> is triggered on the live release R-1; once its `resolve` task has pinned R-1
# and a later task runs, `make deploy RELEASE=<target>` swaps the pointers under it. The run must
# finish on R-1 (resolve hands every later task the path it pinned, docker/airflow/dags/factory.py),
# and the next run must resolve the target.
#
#   bash scripts/chaos/deploy-mid-run.sh <dag_id> <target release id> [--dry-run]
#
# Preconditions, each checked and the run refused (exit 2) otherwise:
#   the target is compiled under ${K2_RELEASES:-~/k2-releases} and is not the live release (the
#     trino and airflow pointers both name R-1); the scheduler k2-airflow-scheduler runs; the DAG
#     exists, is not paused and has no run in progress;
#   `make deploy RELEASE=<target> PLAN=1` says no drain (scripts/deploy.py drain_need: "no Trino
#     restart and no DAG's task list changes") and recreates nothing. A draining deploy pauses the
#     DAG and waits for its run instead of overtaking it, and a recreated scheduler or Spark
#     container kills the run's tasks: neither is this test. A documentation-only or rules-only
#     release is the pair this needs.
# Then, each command printed before it runs: trigger the DAG, wait for resolve to succeed and a
# later task to run, deploy, wait for the run to end; trigger again and wait. Claims, one PASS or
# FAIL line each, then `<n> PASS, <m> FAIL`:
#   1 the deploy ran to completion while the run was in progress
#   2 the run succeeded
#   3 every run-record row of the run carries R-1
#   4 the next run succeeded and its rows carry the target
# The run records are read as svc.catalog (governance.run_records.run_record, the one table it may
# read there), with its password file under ${K2_SECRETS:-~/k2-secrets} and the CA there, over TLS
# on localhost:${K2_TRINO_PORT:-8443}. No secret is printed or passed on a command line.
# --dry-run checks the preconditions it can and prints the plan; it triggers and deploys nothing.
#
# Written before its first live run (2026-11-27), from the deploy, factory and Airflow CLI code it
# drives, and unrun: the operator watches the first run. Watch for: the trigger's run id format in
# this Airflow version (`airflow dags trigger -r`), the run reaching a later task before the deploy
# begins (a fetch exiting 10 skips load, audit and publish: the build is then the later task), a
# deploy that refuses for a reason PLAN=1 did not show, and the deploy's own verify; if it fails, the
# deploy rolls back and claim 1 says so. Then record the run in scripts/chaos/README.md.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
DAG=${1:-} TARGET=${2:-} DRY=0
[ "${3:-}" = --dry-run ] && DRY=1
if [ -z "$DAG" ] || [ -z "$TARGET" ] || { [ -n "${3:-}" ] && [ "$DRY" = 0 ]; }; then
  echo "usage: $0 <dag_id> <target release id> [--dry-run]" >&2; exit 2
fi
[[ "$DAG" =~ ^[a-z][a-z0-9]*(_[a-z0-9]+)*$ ]] || { echo "deploy-mid-run: $DAG is not a DAG id" >&2; exit 2; }
[[ "$TARGET" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "deploy-mid-run: $TARGET is not a release id" >&2; exit 2; }
ROOT=${K2_RELEASES:-$HOME/k2-releases}
SECRETS=${K2_SECRETS:-$HOME/k2-secrets}
SCHED=k2-airflow-scheduler
RUN="manual__deploy-mid-run-$(date -u +%Y%m%dT%H%M%SZ)"
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
T0=$SECONDS
n=0 fails=0
claim() { n=$((n + 1)); if [ "$1" = 0 ]; then echo "PASS $n $2"; else fails=$((fails + 1)); echo "FAIL $n $2"; fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }  # a test as claim takes it
refuse() { echo "deploy-mid-run: refused: $*" >&2; exit 2; }
say() { echo "+ $*"; }  # each command, as typed, before it runs
airflow() { docker exec "$SCHED" airflow "$@"; }
live() { readlink "$ROOT/pointers/$1" 2>/dev/null | sed 's|^\.\./||'; }

plan() {
  echo "deploy-mid-run: the plan for $DAG, R-1 $(live airflow) to $TARGET"
  echo "  docker exec $SCHED airflow dags trigger $DAG -r $RUN"
  echo "  (wait: resolve success and a later task running: airflow dags states-for-dag-run $DAG $RUN -o json)"
  echo "  make deploy RELEASE=$TARGET"
  echo "  (wait: the run ends: airflow dags list-runs $DAG -o json)"
  echo "  (read: SELECT DISTINCT release_id FROM governance.run_records.run_record WHERE run_id = '$DAG/$RUN', as svc.catalog)"
  echo "  docker exec $SCHED airflow dags trigger $DAG -r ${RUN}-next, then the same wait and read"
}

task_states() {  # task_states <run>: "<task> <state>" lines
  airflow dags states-for-dag-run "$DAG" "$1" -o json 2>/dev/null \
    | python3 -c 'import json, sys; [print(t["task_id"], t["state"]) for t in json.load(sys.stdin)]' 2>/dev/null
}
run_state() {  # run_state <run>: the run's state, or nothing
  airflow dags list-runs "$DAG" -o json 2>/dev/null \
    | python3 -c 'import json, sys; print(next((r["state"] for r in json.load(sys.stdin) if r["run_id"] == sys.argv[1]), ""))' "$1" 2>/dev/null
}
wait_end() {  # wait_end <run>: until success or failed, 60 min at most; prints the state
  local s t0=$SECONDS
  while s=$(run_state "$1"); [ "$s" != success ] && [ "$s" != failed ] && [ $((SECONDS - t0)) -lt 3600 ]; do sleep 10; done
  echo "$s"
}
releases_of() {  # releases_of <run>: the distinct release ids of its run records, as svc.catalog over TLS
  python3 - "$DAG/$1" "$SECRETS" "${K2_TRINO_PORT:-8443}" <<'PY'
import sys
from pathlib import Path
sys.path[:0] = ["scripts", "scripts/lib"]
from access_verify import Trino  # the access suite's client: HTTPS, the password read from its file
run, secrets, port = sys.argv[1:4]
t = Trino(f"https://localhost:{port}", f"{secrets}/tls/ca.pem",
          lambda u: (Path(secrets) / "principals" / f"{u}.password").read_text().strip())
quoted = run.replace("'", "''")
print(" ".join(sorted(r[0] for r in t.ok("svc.catalog", "SELECT DISTINCT release_id FROM governance.run_records.run_record "
                                                       f"WHERE run_id = '{quoted}'"))))
PY
}

# ---- preconditions --------------------------------------------------------------------------
[ "$DRY" = 1 ] && plan
OLD=$(live airflow)
[ -f "$ROOT/$TARGET/manifest.json" ] || refuse "$ROOT/$TARGET is not a compiled release"
if [ -z "$OLD" ] || [ "$OLD" != "$(live trino)" ]; then refuse "the airflow and trino pointers do not name one live release ($OLD, $(live trino))"; fi
[ "$OLD" != "$TARGET" ] || refuse "$TARGET is the live release already"
[ "$(docker inspect -f '{{.State.Running}}' "$SCHED" 2>/dev/null)" = true ] || refuse "$SCHED is not running"
airflow dags list -o json 2>/dev/null | python3 -c '
import json, sys
d = next((x for x in json.load(sys.stdin) if x["dag_id"] == sys.argv[1]), None)
sys.exit(0 if d and str(d["is_paused"]) == "False" else 1)' "$DAG" || refuse "DAG $DAG is not in Airflow, or is paused"
[ -z "$(airflow dags list-runs "$DAG" --state running -o json 2>/dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin) or "")' 2>/dev/null)" ] \
  || refuse "DAG $DAG has a run in progress"
say "make deploy RELEASE=$TARGET PLAN=1"
make -s deploy RELEASE="$TARGET" PLAN=1 > "$W/plan.log" 2>&1 || { cat "$W/plan.log" >&2; refuse "PLAN=1 failed"; }
grep -q "drain: no Trino restart and no DAG's task list changes" "$W/plan.log" \
  || { grep -E 'drain:|Trino restart' "$W/plan.log" >&2; refuse "this pair drains (scripts/deploy.py drain_need): a drain pauses the DAG instead of overtaking its run"; }
grep -q "recreate, one at a time: nothing" "$W/plan.log" \
  || { grep 'recreate, one at a time' "$W/plan.log" >&2; refuse "this pair recreates a service, which would stop the run's tasks"; }
echo "deploy-mid-run: preconditions hold: R-1 $OLD, target $TARGET, no drain, nothing recreated"
[ "$DRY" = 1 ] && { echo "deploy-mid-run: --dry-run: nothing triggered, nothing deployed"; exit 0; }

# ---- the run, the deploy under it -------------------------------------------------------------
say "docker exec $SCHED airflow dags trigger $DAG -r $RUN"
airflow dags trigger "$DAG" -r "$RUN" > "$W/trigger.log" 2>&1 || { cat "$W/trigger.log" >&2; refuse "the trigger failed"; }
t0=$SECONDS
until states=$(task_states "$RUN") && grep -q '^resolve success$' <<< "$states" \
      && grep -E ' (running|queued)$' <<< "$states" | grep -qv '^resolve '; do
  s=$(run_state "$RUN")
  if [ "$s" = success ] || [ "$s" = failed ]; then refuse "the run ended ($s) before a task after resolve ran: nothing to overtake"; fi
  [ $((SECONDS - t0)) -lt 900 ] || refuse "no task after resolve ran within 900 s"
  sleep 3
done
echo "deploy-mid-run: $RUN pinned $OLD; running: $(grep -E ' (running|queued)$' <<< "$states" | tr '\n' ' ')"
say "make deploy RELEASE=$TARGET"
make -s deploy RELEASE="$TARGET" > "$W/deploy.log" 2>&1
code=$?
tail -3 "$W/deploy.log"
claim "$code" "the deploy to $TARGET ran to completion (exit $code), begun while $RUN ran a task after resolve"
state=$(wait_end "$RUN")
claim "$(is [ "$state" = success ])" "the run $RUN succeeded ($state)"
got=$(releases_of "$RUN")
claim "$(is [ "$got" = "$OLD" ])" "every run-record row of $DAG/$RUN carries R-1 $OLD (found: ${got:-no rows})"

say "docker exec $SCHED airflow dags trigger $DAG -r ${RUN}-next"
airflow dags trigger "$DAG" -r "${RUN}-next" > "$W/trigger2.log" 2>&1 || { cat "$W/trigger2.log" >&2; refuse "the second trigger failed"; }
state=$(wait_end "${RUN}-next")
got=$(releases_of "${RUN}-next")
claim "$(is [ "$state:$got" = "success:$TARGET" ])" "the next run ${RUN}-next succeeded ($state) and its rows carry $TARGET (found: ${got:-no rows})"
echo "deploy-mid-run: $((n - fails)) PASS, $fails FAIL in $((SECONDS - T0)) s"
[ "$fails" = 0 ] && [ "$n" = 4 ]  # every claim reached and passed
