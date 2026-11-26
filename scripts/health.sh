#!/usr/bin/env bash
# "Is it up?" in one command: every container healthy, a message on every venue
# in the last 2 minutes, a lake that is still committing, and every capture doing
# what the registry says about its order book.
#
# `make health` runs this, and scripts/dev-up.sh calls it for its steps (e) and
# (f) — one implementation, so a check added here is a check the bring-up does.
#
# The lake half is the point: before it existed the probe printed PASS with a
# 141-hour-stale lake and every ingest run failing, because it only ever asked
# ClickHouse. Exit status is 1 if anything printed FAIL, 0 otherwise (a WARN is
# "warming up", not "broken").
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

WAIT=${K2_HEALTH_WAIT:-300}          # seconds to wait for containers to go healthy
LAKE_MAX_AGE=${K2_HEALTH_LAKE_MAX_AGE:-1800}   # 5-min ingest; 30 min is 6 missed runs

fail=0
step()   { printf '\n== %s ==\n' "$*"; }
result() { printf '%s: %s\n' "$1" "$2"; }
bad()    { printf '%s: %s\n' "$1" "$2"; fail=1; }

if [ ! -f docker-compose.yml ] || [ ! -f .env ]; then
  bad "preflight" "FAIL (run from the repo root — needs docker-compose.yml and .env)"
  exit 1
fi
set -a
# shellcheck disable=SC1091 # .env is generated locally from .env.example
. ./.env
set +a
# airflow_cli, airflow_task_in_flight and LAKE_DAG, the ones the chaos scripts
# and lake-verify pause the ingest with.
# shellcheck source=chaos/lib.sh
. scripts/chaos/lib.sh

# ── (1) every container healthy ──────────────────────────────────────────────
step "(1) container health (<= $((WAIT / 60)) min)"
deadline=$((SECONDS + WAIT))
while :; do
  ps_out=$(docker compose ps --format '{{.Name}} {{.Health}}' 2>/dev/null || true)
  # An empty listing is "nothing is running", not "everything is healthy". It is
  # also what you get from a directory whose compose project name differs from
  # the running stack's — a git worktree, say.
  if [ -z "$ps_out" ]; then
    bad "(1) health" "FAIL (no containers in project '$(docker compose config --format json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["name"])' 2>/dev/null || echo '?')' — is the stack up?)"
    break
  fi
  unhealthy=$(printf '%s\n' "$ps_out" | grep -v ' healthy$' | grep -v ' $' || true)
  if [ -z "$unhealthy" ]; then
    result "(1) health" "PASS (all services healthy or have no healthcheck)"
    break
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    bad "(1) health" "FAIL (still unhealthy after $((WAIT / 60)) min)"
    echo "$unhealthy"
    break
  fi
  sleep 15
done

# A long-running service whose container has exited is not in `ps` above, so it
# passed there: Prometheus stayed Exited (255) after the 2026-11-25 VM restart and
# only step (3) noticed, as a missing metric. Long-running is every service compose
# defines without restart "no" (the one-shots exit by design). Fails closed: a
# compose file it cannot read is a FAIL, not a pass.
if [ -n "$ps_out" ]; then
  running=$(docker compose ps --services --status running 2>/dev/null || true)
  missing=$(docker compose config --format json 2>/dev/null | RUNNING="$running" python3 -c '
import json, os, sys
svc = json.load(sys.stdin)["services"]
up = set(os.environ["RUNNING"].split())
print(" ".join(sorted(n for n, d in svc.items() if d.get("restart", "no") != "no" and n not in up)))' 2>/dev/null) || missing="?"
  if [ "$missing" = "?" ]; then
    bad "(1) long-running services" "FAIL (could not read the services from docker compose config)"
  elif [ -n "$missing" ]; then
    bad "(1) long-running services" "FAIL (not running: $missing; start each with docker compose up -d --no-deps <service>)"
  else
    result "(1) long-running services" "PASS (every one is running)"
  fi
fi

# ── (2) a message on every venue in the last 2 minutes ───────────────────────
# 2>/dev/null, not 2>&1: rpk and clickhouse-client both write notices to stderr
# ("<jemalloc>: Number of CPUs detected is not deterministic..."), and a merged
# stream turns the first word of a warning into a venue name.
step "(2) venues, last 2 minutes"
ch_out=$(docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
  "SELECT exchange, count() FROM gold.trades FINAL WHERE exchange_ts > now() - INTERVAL 2 MINUTE GROUP BY exchange" 2>/dev/null) || true
if [ -z "$ch_out" ]; then
  result "(2) clickhouse gold.trades" "WARN (no rows in the last 2 minutes — capture may be warming up)"
else
  while IFS=$'\t' read -r exch cnt; do
    if [ -n "${cnt:-}" ] && [ "$cnt" -gt 0 ] 2>/dev/null; then
      result "(2) $exch" "PASS ($cnt trades/2min)"
    else
      result "(2) $exch" "WARN (0 trades/2min)"
    fi
  done <<< "$ch_out"
fi

declare -A cap_metrics=()
for svc in capture-binance capture-kraken capture-coinbase; do
  cid=$(docker compose ps -q "$svc" 2>/dev/null || true)
  [ -z "$cid" ] && continue || true
  running=$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)
  [ "$running" != "true" ] && continue || true
  net=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$cid" 2>/dev/null || true)
  [ -z "$net" ] && continue || true
  metrics=$(docker run --rm --network "$net" curlimages/curl:8.14.1 -s "http://${svc}:8082/metrics" 2>/dev/null || true)
  cap_metrics[$svc]=$metrics
  lastts=$(printf '%s\n' "$metrics" | grep '^k2_capture_last_message_ts_seconds' || true)
  if [ -n "$lastts" ]; then
    result "(2) $svc metrics" "PASS ($lastts)"
  else
    result "(2) $svc metrics" "WARN (no k2_capture_last_message_ts_seconds yet — may be warming up)"
  fi
done

# ── (3) the lake is still committing ─────────────────────────────────────────
step "(3) lake"
age=$(curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=time() - max(k2_lake_last_commit_ts_seconds)' 2>/dev/null \
  | python3 -c 'import json,sys;r=json.load(sys.stdin)["data"]["result"];print(int(float(r[0]["value"][1])) if r else "")' 2>/dev/null || true)
if [ -z "$age" ]; then
  bad "(3) lake commit age" "FAIL (Prometheus has no k2_lake_last_commit_ts_seconds: Prometheus down, lake-metrics down, or the lake has never committed)"
elif [ "$age" -gt "$LAKE_MAX_AGE" ]; then
  bad "(3) lake commit age" "FAIL (last commit ${age}s ago = $((age / 3600))h: the lake is stale, see docs/runbooks/lake-ingest-lag.md section 2)"
else
  result "(3) lake commit age" "PASS (last commit ${age}s ago)"
fi

# The last finished lake_ingest runs, through the Airflow CLI in the scheduler
# (ADR-034): the scheduled ones of the last LAKE_MAX_AGE, newest first, so none
# at all means the 5-min schedule has stopped firing, which is also what a
# stale DAG folder looks like (runbook §5): healthy containers, no new runs.
# Queued and running runs
# are excluded on purpose: they say nothing about whether the last one worked.
#
# A run whose only task skipped is `success` to Airflow, so a success is read
# again at the task: success, or skipped (ingest.py exit 75, another lake
# writer holds the lock). One or two skips are the lock doing its job, and any
# number while Airflow's lake_maintenance or lake_rebuild runs is the nightly
# window; three in a row with neither running is a stuck lock, the case
# AirflowLakeIngestSkippedRepeatedly pages on. A holder that ended in the last
# 15 minutes still accounts for them, the alert's grace: its skips stay the
# newest finished runs until the first run after it has finished.
runbook=docs/runbooks/airflow-scheduling.md
# holder_ended_recently <dag_id> -> 0 if a run of <dag_id> ended in the last 15 min.
holder_ended_recently() {
  airflow_cli dags list-runs "$1" -o json | python3 -c '
import json, sys, time
from datetime import datetime
sys.exit(not any(r["end_date"] and time.time() - datetime.fromisoformat(r["end_date"]).timestamp() < 900
                 for r in json.load(sys.stdin)))' 2>/dev/null
}
since=$(date -u -d "@$(( $(date +%s) - LAKE_MAX_AGE ))" +%FT%T+00:00)
runs=$(airflow_cli dags list-runs "$LAKE_DAG" -o json --start-date "$since" | python3 -c '
import json, sys
done = [r for r in json.load(sys.stdin) if r["state"] in ("success", "failed")][:3]
print("\n".join(r["run_id"] + " " + r["state"] for r in done) or "NONE")' 2>/dev/null || true)
skips=0 last=""
while read -r run_id state; do
  [ -n "${run_id:-}" ] && [ "$run_id" != NONE ] || break
  if [ "$state" = success ]; then
    state=$(airflow_cli tasks states-for-dag-run "$LAKE_DAG" "$run_id" -o json \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["state"])' 2>/dev/null || echo unknown)
  fi
  if [ "$state" = skipped ]; then skips=$((skips + 1)); else last="$state $run_id"; break; fi
done <<< "$runs"
holder=""
if [ "$skips" -gt 0 ] && [ -z "$last" ]; then
  for dag in lake_maintenance lake_rebuild; do
    if airflow_task_in_flight "$dag"; then holder="while $dag holds the lake lock"; break; fi
    if holder_ended_recently "$dag"; then holder="under $dag, which ended in the last 15 min"; break; fi
  done
fi
label="(3) last $LAKE_DAG run"
if [ -z "$runs" ]; then
  bad "$label" "FAIL (the Airflow CLI in $AIRFLOW_CONTAINER did not answer: is airflow-scheduler up? $runbook §1)"
elif [ "$runs" = NONE ]; then
  bad "$label" "FAIL (no $LAKE_DAG run finished in the last $((LAKE_MAX_AGE / 60)) min: the DAG is stale or paused, or the scheduler stopped, $runbook §5 and §1)"
elif [ "${last%% *}" = success ]; then
  result "$label" "PASS (${last#* } success$([ "$skips" -gt 0 ] && echo ", then $skips skipped: the lake lock was held"))"
elif [ -n "$last" ]; then
  bad "$label" "FAIL (${last#* } ${last%% *}: $runbook §2)"
elif [ -n "$holder" ]; then
  result "$label" "PASS ($skips skipped $holder, as it should)"
elif [ "$skips" -ge 3 ]; then
  bad "$label" "FAIL ($skips skipped in a row and no Airflow maintenance or rebuild running: the lake lock is stuck, $runbook §3)"
else
  result "$label" "WARN ($skips skipped in a row: another lake writer holds the lock; FAIL at 3, $runbook §3)"
fi

# ── (4) the order-book switch: process, host file and Spark's copy agree ─────
# ADR-050. Each capture reads config/instruments.yaml once, at start, and exports
# what it read as k2_capture_book_capture; the lake's book-audit skip reads the
# Spark container's mounted copy. A capture not recreated after a change, or a
# stale mount, leaves the three disagreeing, and nothing else would show it.
step "(4) order-book switch (ADR-050)"
# book_capture's venue/value pairs from a registry on stdin. Python's re, not
# PyYAML: the host need not have it, and the section is a flat map.
book_switch() {
  python3 -c '
import re, sys
m = re.search(r"^book_capture:[ \t]*\n((?:[ \t]+.*\n?)*)", sys.stdin.read(), re.M)
for venue, value in re.findall(r"^[ \t]+([a-z]+):[ \t]*([^\s#]+)", m.group(1) if m else "", re.M):
    print(venue, value)' 2>/dev/null
}
host_sw=$(book_switch 2>/dev/null < config/instruments.yaml)
spark_sw=$(docker exec k2-spark-iceberg cat /home/iceberg/config/instruments.yaml 2>/dev/null | book_switch)
# on/off from a registry value, "?" for anything that is not a YAML boolean.
as_state() { case "$1" in true|True|TRUE) echo on ;; false|False|FALSE) echo off ;; "") echo missing ;; *) echo "?$1" ;; esac; }
for venue in binance kraken coinbase; do
  label="(4) $venue book_capture"
  metrics=${cap_metrics[capture-$venue]:-}
  if [ -z "$metrics" ]; then
    result "$label" "WARN (no metrics from capture-$venue: not running or warming up, switch not compared)"
    continue
  fi
  gauge=$(printf '%s\n' "$metrics" | awk -v v="$venue" '$1 == "k2_capture_book_capture{exchange=\"" v "\"}" { print $2 }')
  case "$gauge" in 1|1.0) proc=on ;; 0|0.0) proc=off ;; "") proc=missing ;; *) proc="?$gauge" ;; esac
  host=$(as_state "$(printf '%s\n' "$host_sw" | awk -v v="$venue" '$1 == v { print $2 }')")
  spark=$(as_state "$(printf '%s\n' "$spark_sw" | awk -v v="$venue" '$1 == v { print $2 }')")
  states="process $proc, host file $host, spark-iceberg's copy $spark"
  if [ "$proc" = missing ]; then
    bad "$label" "FAIL (capture-$venue exports no k2_capture_book_capture: an image older than ADR-050 subscribes to books whatever the file says; $states)"
    continue
  elif [ "$host" != on ] && [ "$host" != off ]; then
    bad "$label" "FAIL (config/instruments.yaml book_capture.$venue is $host, not true or false; $states)"
    continue
  fi
  problems=""
  if [ "$proc" != "$host" ]; then
    problems="capture-$venue was not recreated since the registry changed: docker compose up -d --force-recreate --no-deps capture-$venue"
  fi
  if [ "$spark" != "$host" ]; then
    problems="${problems:+$problems; }spark-iceberg's mounted copy is stale, and the audit skip reads it: when no ingest runs, docker compose up -d --force-recreate --no-deps spark-iceberg"
  fi
  if [ -n "$problems" ]; then
    bad "$label" "FAIL ($states. $problems)"
  else
    result "$label" "PASS (books $proc: $states)"
  fi
done

step "summary"
[ "$fail" = "0" ] && result "health" "PASS" || result "health" "FAIL (see the FAIL lines above)"
exit "$fail"
