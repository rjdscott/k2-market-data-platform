#!/usr/bin/env bash
# Shared helpers for the K2 chaos scripts. Sourced, never executed.
#
# Everything here observes; nothing here breaks anything. The fault lives in the
# script that sources this file, so each of those reads as one page: name the
# alert, break it, measure, restore. Keeping the observation code out of them is
# what makes "measure" identical across scripts and therefore comparable.
#
# Two dependencies, both already on this host: `jq` and `docker`. Everything is
# observed through Prometheus on its published port - the capture image is
# distroless and its :8082 is not published, so Prometheus is the only reader of
# it, and reading the same numbers the alerts read is the point rather than a
# limitation.

PROM="${K2_CHAOS_PROM:-http://localhost:9090}"

CHAOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
RESULTS_DIR="${K2_CHAOS_RESULTS_DIR:-$CHAOS_DIR/results}"

# ── observation ─────────────────────────────────────────────────────────────

stamp() { date -u +%FT%TZ; }

# compose <args...> — always the repo-root compose file, whatever the cwd.
compose() { docker compose -f "$CHAOS_DIR/../../docker-compose.yml" "$@"; }

# prom_query <expr> -> the first sample's value, or empty. Instant vectors only;
# a scalar or a multi-series result is the caller's problem to avoid.
#
# The trailing `|| true` is load-bearing. Every caller runs under `set -euo
# pipefail`, and this is called *mid-fault* - with a container paused, a broker
# stopped, or Prometheus itself under load. One transient curl failure would
# otherwise abort the script between "break it" and "restore it". Failing soft
# to an empty string is correct here because every caller already treats empty
# as "no reading yet": `wait_for_metric` keeps polling, the samplers default
# with `${x:-0}`.
prom_query() {
  curl -sf --get "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null \
    | jq -r '.data.result[0].value[1] // empty' || true
}

# alert_state <name> <exchange> -> firing | pending | "" for THAT VENUE only.
# "firing" wins if any instance of the alert is firing for the venue, because
# one stale stream firing is the alert firing.
#
# The venue filter is not optional. Matching on `alertname` alone means
# `capture-kill.sh --exchange binance` returns "firing" off a kraken outage, and
# every measurement downstream of that wait is then a number about the wrong
# venue. An alert can name its venue in either of two labels, so both are
# accepted:
#   * `exchange` - the sample's own label, on everything built from a
#     k2_capture_* series, including alerts that aggregate with
#     `sum by (exchange)` and so drop the scrape job.
#   * `job` (`capture-<exchange>`) - the scrape target's label, which is all
#     `up`-based alerts such as CaptureDown carry.
# Neither label is present on both kinds, which is why the match is an `or`.
#
# The lake alerts have no venue at all - `raw.messages` is one table fed by every
# exchange - so an empty or omitted <exchange> means "any instance of this alert".
# That is not a loosening of the capture rule: a lake alert firing is a lake alert
# firing, and there is no wrong venue to attribute it to.
alert_state() {
  curl -sf "$PROM/api/v1/alerts" 2>/dev/null \
    | jq -r --arg n "$1" --arg ex "${2:-}" '
        [ .data.alerts[]
          | select(.labels.alertname == $n)
          | select($ex == "" or .labels.exchange == $ex
                   or .labels.job == "capture-" + $ex)
          | .state ]
        | (map(select(. == "firing"))[0] // .[0] // empty)' || true
}

# wait_for_alert <name> <timeout_s> [exchange] -> prints elapsed seconds; 1 on timeout.
# Non-zero on timeout is a real outcome, not an error: some faults are designed
# to self-heal inside the alert's `for:` window and never fire.
wait_for_alert() {
  local name=$1 timeout=$2 exchange=${3:-} start elapsed
  start=$SECONDS
  while :; do
    if [ "$(alert_state "$name" "$exchange")" = "firing" ]; then
      echo "$((SECONDS - start))"
      return 0
    fi
    elapsed=$((SECONDS - start))
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "$elapsed"
      return 1
    fi
    sleep 2
  done
}

# wait_for_alert_clear <name> <timeout_s> [exchange] -> prints elapsed seconds;
# 1 on timeout. A timeout here is NOT a recovery time: see capture-pause.sh for
# what a caller must do with it.
wait_for_alert_clear() {
  local name=$1 timeout=$2 exchange=${3:-} start elapsed
  start=$SECONDS
  while :; do
    if [ -z "$(alert_state "$name" "$exchange")" ]; then
      echo "$((SECONDS - start))"
      return 0
    fi
    elapsed=$((SECONDS - start))
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "$elapsed"
      return 1
    fi
    sleep 2
  done
}

# wait_for_metric <expr> <lt|le|gt|ge|eq> <value> <timeout_s>
#   -> prints elapsed seconds; 1 on timeout. Float-safe: awk, not [ -lt ].
wait_for_metric() {
  local expr=$1 cmp=$2 target=$3 timeout=$4 start elapsed value
  start=$SECONDS
  while :; do
    value=$(prom_query "$expr" || true)
    if [ -n "$value" ] && awk -v a="$value" -v b="$target" -v c="$cmp" 'BEGIN {
          exit !((c == "lt" && a <  b) || (c == "le" && a <= b) ||
                 (c == "gt" && a >  b) || (c == "ge" && a >= b) ||
                 (c == "eq" && a == b))
        }'; then
      echo "$((SECONDS - start))"
      return 0
    fi
    elapsed=$((SECONDS - start))
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "$elapsed"
      return 1
    fi
    sleep 2
  done
}

# wait_healthy <container> <timeout_s> — block until Docker calls it healthy.
#
# The replacement for `sleep 20` after a `docker start`. A fixed sleep is wrong
# in both directions: dead time when the container is back in 3 s, and a FALSE
# FAILURE when it is not, because the assertion that follows then reads an empty
# string out of a container still starting and reports it as the fault under
# test. Both scripts that restart a container assert on its output immediately
# afterwards, so this is the difference between measuring the fault and
# measuring the restart.
#
# `.State.Health.Status`, not `.State.Running`: running means the process
# started, healthy means it is answering. Every container this is called on
# declares a healthcheck; one that did not would report `<no value>` forever and
# time out, which is the honest outcome rather than a silent pass.
wait_healthy() {
  local name=$1 timeout=$2 start status
  start=$SECONDS
  while :; do
    status=$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || true)
    if [ "$status" = healthy ]; then
      echo "→ $name healthy after $((SECONDS - start))s" >&2
      return 0
    fi
    if [ $((SECONDS - start)) -ge "$timeout" ]; then
      die "$name is '$status' after ${timeout}s, not healthy — the stack did not come back"
    fi
    sleep 2
  done
}

# ── the scheduled ingest ────────────────────────────────────────────────────
#
# Every lake fault below races the `1-59/5 * * * *` lake-ingest-5min deployment.
# Two things go wrong if it lands inside the window: `docker/lake/ingest.py`'s
# flock makes the second run exit 2, which reads as the fault having broken the
# ingest; and the before/after row-count assertions in lake-lakekeeper-stop.sh
# and lake-minio-stop.sh see a legitimate commit and call it a partial one.
#
# The Prefect 3.6 CLI pauses a SCHEDULE, by id, not a deployment — `prefect
# deployment pause` does not exist, and `--all` would take out lake-maintenance
# as well. So: read the schedule ids, pause each, and resume them from a trap.

LAKE_DEPLOYMENT="${K2_LAKE_DEPLOYMENT:-lake-ingest/lake-ingest-5min}"
PREFECT_CONTAINER="${K2_PREFECT_CONTAINER:-k2-prefect-server}"
SPARK_CONTAINER="${K2_SPARK_CONTAINER:-k2-spark-iceberg}"
LAKE_SCHEDULE_IDS=""

# _lake_schedules -> whitespace-separated ids of the ACTIVE schedules for
# LAKE_DEPLOYMENT.
#
# `active` matters because the ids are also what the trap resumes. A schedule
# the maintainer had already paused by hand is not ours to un-pause, and
# recording it here means a chaos run ends by starting a 5-minute ingest that
# was deliberately off. Filtering on the way in is the fix — pause_lake_ingest
# and resume_lake_ingest then act on exactly the set this run turned off.
_lake_schedules() {
  docker exec "$PREFECT_CONTAINER" \
    prefect deployment schedule ls "$LAKE_DEPLOYMENT" -o json 2>/dev/null \
    | python3 -c 'import json,sys
print(" ".join(s["id"] for s in json.load(sys.stdin) if s.get("active")))' \
      2>/dev/null || true
}

# pause_lake_ingest — never fatal. A stack where the deployment is not
# registered is a legitimate one to run chaos against; it just has nothing to
# pause, and saying so beats dying.
pause_lake_ingest() {
  local id
  LAKE_SCHEDULE_IDS=$(_lake_schedules)
  if [ -z "$LAKE_SCHEDULE_IDS" ]; then
    echo "→ NOTE: no ACTIVE schedule for $LAKE_DEPLOYMENT — nothing to pause." >&2
    echo "  If the 5-minute ingest IS running, this test may race it." >&2
    return 0
  fi
  for id in $LAKE_SCHEDULE_IDS; do
    docker exec "$PREFECT_CONTAINER" \
      prefect deployment schedule pause "$LAKE_DEPLOYMENT" "$id" >/dev/null 2>&1 \
      || echo "→ WARNING: could not pause schedule $id" >&2
  done
  echo "→ paused $LAKE_DEPLOYMENT for the duration of this run" >&2
  # Pausing the schedule does not stop a run already in flight, and every lake
  # writer takes the same flock (docker/lake/lock.py) — an ingest this script
  # starts while one is running exits 2 at the lock, which the first
  # lake-corrupt-payload run misread as "blocked by the bad record" (2026-08-27).
  local waited=0
  while docker exec "$SPARK_CONTAINER" pgrep -f 'lake/(ingest|maintenance)\.py' >/dev/null 2>&1; do
    [ "$waited" -eq 0 ] && echo "→ waiting for the in-flight lake job to finish" >&2
    sleep 5; waited=$((waited + 5))
    [ "$waited" -ge 300 ] && die "a lake job has held the lock for 5 min — not starting another under it"
  done
}

# resume_lake_ingest — idempotent, so it is safe in a trap that may fire twice.
resume_lake_ingest() {
  local id
  [ -n "$LAKE_SCHEDULE_IDS" ] || return 0
  for id in $LAKE_SCHEDULE_IDS; do
    docker exec "$PREFECT_CONTAINER" \
      prefect deployment schedule resume "$LAKE_DEPLOYMENT" "$id" >/dev/null 2>&1 \
      || echo "→ WARNING: could not resume schedule $id — resume it by hand" >&2
  done
  LAKE_SCHEDULE_IDS=""
  echo "→ resumed $LAKE_DEPLOYMENT" >&2
}

# ── reporting ───────────────────────────────────────────────────────────────

# report <script> <expected-alert> <t_fire_s> <t_recover_s>
# One line per run, appended to scripts/chaos/results/<date>.tsv. These are
# evidence, so they are committed rather than gitignored; the FMEA row in
# docs/architecture/16-failure-modes.md is updated BY HAND from this file, with the
# date, so that a published recovery number always names the run it came from.
#
# The two time cells are not always numbers, and that is deliberate: `none` (the
# alert did not fire, which is the pass condition for some faults), `skip`, and
# `unmeasured` (the wait timed out, so no recovery time was observed) are all
# real outcomes. A timeout must never be arithmetic'd into a duration and
# published as one - that is how `t_fresh + 300` becomes a "measured" MTTR.
report() {
  local file header
  file="$RESULTS_DIR/$(date -u +%F).tsv"
  header=$'ts\tscript\texpected_alert\tt_fire_s\tt_recover_s'
  mkdir -p "$RESULTS_DIR"
  [ -s "$file" ] || printf '%s\n' "$header" > "$file"
  printf '%s\t%s\t%s\t%s\t%s\n' "$(stamp)" "$1" "$2" "$3" "$4" | tee -a "$file"
  printf 'result appended to %s\n' "$file" >&2
}

# banner <script> <expected-alert> <runbook> <one-line description>
banner() {
  cat >&2 <<EOF

── $1 ─────────────────────────────────────────────
  fault      : $4
  expects    : $2
  runbook    : $3
  started    : $(stamp)
  NOT for CI : this breaks the running stack (requirements clarification Q3)

EOF
}

# ── preflight ───────────────────────────────────────────────────────────────

die() { printf 'chaos: %s\n' "$*" >&2; exit 1; }

require_running() {
  docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -qx true \
    || die "$1 is not running. Bring the stack up first: make up"
}

preflight() {
  command -v jq >/dev/null 2>&1 || die "jq not found"
  command -v docker >/dev/null 2>&1 || die "docker not found"
  curl -sf "$PROM/-/healthy" >/dev/null 2>&1 || die "Prometheus not reachable at $PROM"
  local c
  for c in "$@"; do require_running "$c"; done
}

# parse_exchange "$@" -> echoes the --exchange value, default kraken.
parse_exchange() {
  local ex=kraken
  while [ $# -gt 0 ]; do
    case $1 in
      --exchange)
        [ $# -ge 2 ] || die "--exchange needs a value (binance|kraken|coinbase)"
        ex=$2; shift 2 ;;
      --exchange=*) ex=${1#*=}; shift ;;
      *) shift ;;
    esac
  done
  case $ex in
    binance|kraken|coinbase) echo "$ex" ;;
    *) die "unknown exchange '$ex' (binance|kraken|coinbase)" ;;
  esac
}
