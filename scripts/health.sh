#!/usr/bin/env bash
# "Is it up?" in one command: every container healthy, a message on every venue
# in the last 2 minutes, and a lake that is still committing.
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
PREFECT_PORT=${K2_PREFECT_PORT:-4200}

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

for svc in capture-binance capture-kraken capture-coinbase; do
  cid=$(docker compose ps -q "$svc" 2>/dev/null || true)
  [ -z "$cid" ] && continue || true
  running=$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)
  [ "$running" != "true" ] && continue || true
  net=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$cid" 2>/dev/null || true)
  [ -z "$net" ] && continue || true
  metrics=$(docker run --rm --network "$net" curlimages/curl:8.14.1 -s "http://${svc}:8082/metrics" 2>/dev/null || true)
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
  bad "(3) lake commit age" "FAIL (Prometheus has no k2_lake_last_commit_ts_seconds — lake-metrics down, or the lake has never committed)"
elif [ "$age" -gt "$LAKE_MAX_AGE" ]; then
  bad "(3) lake commit age" "FAIL (last commit ${age}s ago = $((age / 3600))h — the lake is stale, see docs/runbooks/lake-ingest-failure.md)"
else
  result "(3) lake commit age" "PASS (last commit ${age}s ago)"
fi

# Last ingest run that actually reached a terminal state. Scheduled and running
# ones are excluded on purpose: a queue full of PENDING runs is what a stalled
# worker looks like, and it says nothing about whether the last one worked.
run_json=$(curl -s --max-time 10 -X POST "localhost:${PREFECT_PORT}/api/flow_runs/filter" \
  -H 'Content-Type: application/json' \
  -d '{"limit":1,"sort":"START_TIME_DESC","flows":{"name":{"any_":["lake-ingest"]}},"flow_runs":{"state":{"type":{"any_":["COMPLETED","FAILED","CRASHED"]}}}}' 2>/dev/null || true)
run_line=$(printf '%s' "$run_json" | python3 -c '
import json, sys
from datetime import datetime, timezone
try:
    runs = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not runs:
    print("NONE"); sys.exit(0)
r = runs[0]
started = datetime.fromisoformat(r["start_time"].replace("Z", "+00:00"))
print(r["state_type"], int((datetime.now(timezone.utc) - started).total_seconds()), r["name"])
' 2>/dev/null || true)
case "$run_line" in
  "")     bad "(3) last lake-ingest run" "FAIL (Prefect API on localhost:${PREFECT_PORT} did not answer — is K2_PREFECT_PORT right?)" ;;
  NONE)   bad "(3) last lake-ingest run" "FAIL (no lake-ingest run has ever finished)" ;;
  *)      read -r state age_s name <<< "$run_line"
          if [ "$state" = "COMPLETED" ] && [ "$age_s" -le "$LAKE_MAX_AGE" ]; then
            result "(3) last lake-ingest run" "PASS ($name COMPLETED ${age_s}s ago)"
          elif [ "$state" = "COMPLETED" ]; then
            bad "(3) last lake-ingest run" "FAIL ($name COMPLETED but ${age_s}s ago — the 5-min schedule has stopped firing)"
          else
            bad "(3) last lake-ingest run" "FAIL ($name $state ${age_s}s ago — logs: http://localhost:${PREFECT_PORT}/runs)"
          fi ;;
esac

step "summary"
[ "$fail" = "0" ] && result "health" "PASS" || result "health" "FAIL (see the FAIL lines above)"
exit "$fail"
