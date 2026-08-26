#!/usr/bin/env bash
# Shared helpers for the K2 chaos scripts. Sourced, never executed.
#
# Everything here observes; nothing here breaks anything. The fault lives in the
# script that sources this file, so each of those reads as one page: name the
# alert, break it, measure, restore. Keeping the observation code out of them is
# what makes "measure" identical across scripts and therefore comparable.
#
# Two dependencies, both already on this host: `jq` and `docker`. Prometheus is
# reached over the published port; capture /metrics is reached from inside the
# compose network via a curl sidecar, because the capture image is distroless
# and the metrics port is not published.

PROM="${K2_CHAOS_PROM:-http://localhost:9090}"
NET="${K2_CHAOS_NETWORK:-k2-market-data-platform_k2-net}"
CURL_IMAGE="${K2_CHAOS_CURL_IMAGE:-curlimages/curl:8.11.1}"

CHAOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
RESULTS_DIR="${K2_CHAOS_RESULTS_DIR:-$CHAOS_DIR/results}"

# ── observation ─────────────────────────────────────────────────────────────

stamp() { date -u +%FT%TZ; }

# compose <args...> — always the repo-root compose file, whatever the cwd.
compose() { docker compose -f "$CHAOS_DIR/../../docker-compose.yml" "$@"; }

# prom_query <expr> -> the first sample's value, or empty. Instant vectors only;
# a scalar or a multi-series result is the caller's problem to avoid.
prom_query() {
  curl -sf --get "$PROM/api/v1/query" --data-urlencode "query=$1" \
    | jq -r '.data.result[0].value[1] // empty'
}

# alert_state <name> -> firing | pending | "" . "firing" wins if any instance of
# the alert is firing, because one stale stream firing is the alert firing.
alert_state() {
  curl -sf "$PROM/api/v1/alerts" \
    | jq -r --arg n "$1" '
        [.data.alerts[] | select(.labels.alertname == $n) | .state]
        | (map(select(. == "firing"))[0] // .[0] // empty)'
}

# wait_for_alert <name> <timeout_s> -> prints elapsed seconds; 1 on timeout.
# Non-zero on timeout is a real outcome, not an error: some faults are designed
# to self-heal inside the alert's `for:` window and never fire.
wait_for_alert() {
  local name=$1 timeout=$2 start elapsed
  start=$SECONDS
  while :; do
    if [ "$(alert_state "$name")" = "firing" ]; then
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

# wait_for_alert_clear <name> <timeout_s> -> prints elapsed seconds; 1 on timeout.
wait_for_alert_clear() {
  local name=$1 timeout=$2 start elapsed
  start=$SECONDS
  while :; do
    if [ -z "$(alert_state "$name")" ]; then
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

# metrics <container> -> that container's raw /metrics exposition.
# k2-capture-kraken -> http://capture-kraken:8082/metrics (the compose hostname).
metrics() {
  local svc=${1#k2-}
  docker run --rm --network "$NET" "$CURL_IMAGE" -s "http://$svc:8082/metrics"
}

# ── reporting ───────────────────────────────────────────────────────────────

# report <script> <expected-alert> <t_fire_s> <t_recover_s>
# One line per run, appended to scripts/chaos/results/<date>.tsv. These are
# evidence, so they are committed rather than gitignored; the FMEA row in
# docs/architecture/failure-modes.md is updated BY HAND from this file, with the
# date, so that a published recovery number always names the run it came from.
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
      --exchange) ex=$2; shift 2 ;;
      --exchange=*) ex=${1#*=}; shift ;;
      *) shift ;;
    esac
  done
  case $ex in
    binance|kraken|coinbase) echo "$ex" ;;
    *) die "unknown exchange '$ex' (binance|kraken|coinbase)" ;;
  esac
}
