#!/usr/bin/env bash
# "Is it up?" in one command: every container healthy, a message on every venue
# in the last 2 minutes, every capture doing what the registry says about its
# order book, and, on a host where the nightly backup is scheduled, a copy under
# 26 hours old. The lake checks print one SKIP line until phase 004.
#
# `make health` runs this, and scripts/dev-up.sh calls it for its steps (e) and
# (f) — one implementation, so a check added here is a check the bring-up does.
#
# The lake half exists because the probe once printed PASS with a 141-hour-stale
# lake and every ingest run failing: it only ever asked ClickHouse. Exit status
# is 1 if anything printed FAIL, 0 otherwise (a WARN is "warming up", not
# "broken"; a SKIP is a check stood down, naming the phase that restores it).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

WAIT=${K2_HEALTH_WAIT:-300}          # seconds to wait for containers to go healthy

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
# compose file it cannot read is a FAIL, not a pass. The services are the LIVE release's, its own
# compose through the pointer (ADR-058), when a releases root has one: after a rollback the checkout's
# compose can name a service that release does not have (k2-api, rolling back to alpha.3.2).
if [ -n "$ps_out" ]; then
  running=$(docker compose ps --services --status running 2>/dev/null || true)
  live_compose=${K2_RELEASES:-$HOME/k2-releases}/pointers/trino/compose/docker-compose.yml
  defined=(docker compose config --format json)
  [ -L "${live_compose%/compose/*}" ] && defined=(docker compose --project-directory . -f "$live_compose" config --format json)
  missing=$("${defined[@]}" 2>/dev/null | RUNNING="$running" python3 -c '
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
# Until ADR-057 this counted each venue's rows in ClickHouse gold.trades, and
# never failed. The hot tier is paused, so each venue is judged two ways, both
# failing: the capture's own healthcheck, every stream against the bound its
# watchdog and CaptureFeedStale share (60 s, 300 s for trades), says the venue
# is talking; the records Redpanda acknowledged from it in the last 2 minutes
# (the counter CaptureProduceStalled reads) say what it hears reaches the
# broker. Kraken's and Coinbase's heartbeats are archived too, so a quiet market
# there still delivers; Binance archives trades only. From 2026-11-24 to -26 the
# one 2-minute window with nothing delivered was a network outage on all three.
step "(2) venues, last 2 minutes"
result "(2) clickhouse gold.trades" "SKIP (the ClickHouse hot tier is paused, ADR-057: not queried; each venue is judged below from its capture and Redpanda's acknowledgements)"
# "<exchange> <records>" per venue, "" if Prometheus has none, "?" if it did not answer.
delivered=$(curl -s --get localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (exchange) (increase(k2_capture_records_delivered_total[2m]))' 2>/dev/null \
  | python3 -c 'import json, sys
for r in json.load(sys.stdin)["data"]["result"]: print(r["metric"]["exchange"], int(float(r["value"][1])))' 2>/dev/null) \
  || delivered="?"

declare -A cap_metrics=()
for venue in binance kraken coinbase; do
  svc=capture-$venue label="(2) $venue"
  cid=$(docker compose ps -q "$svc" 2>/dev/null || true)
  running=$([ -n "$cid" ] && docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)
  if [ "$running" != "true" ]; then
    bad "$label" "FAIL ($svc is not running: docs/runbooks/capture-down.md)"
    continue
  fi
  # Step (4) reads the gauges; a capture that serves none is its WARN, not this line's.
  net=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$cid" 2>/dev/null || true)
  if [ -n "$net" ]; then
    cap_metrics[$svc]=$(docker run --rm --network "$net" curlimages/curl:8.14.1 -s "http://${svc}:8082/metrics" 2>/dev/null || true)
  fi
  hc=$(docker exec "$cid" /k2-capture healthcheck 2>&1) && hc_ok=yes || hc_ok=no
  records=$(printf '%s\n' "$delivered" | awk -v v="$venue" '$1 == v { print $2 }')
  if [ "$hc_ok" = no ]; then
    bad "$label" "FAIL ($svc healthcheck: ${hc:-no answer}; docs/runbooks/capture-feed-stale.md)"
  elif [ "$delivered" = "?" ]; then
    bad "$label" "FAIL (Prometheus did not answer, so delivery to Redpanda is unchecked; healthcheck $hc)"
  elif [ -z "$records" ]; then
    bad "$label" "FAIL (Prometheus has no k2_capture_records_delivered_total for $venue: is it scraping $svc? healthcheck $hc)"
  elif [ "$records" -eq 0 ]; then
    bad "$label" "FAIL (0 records delivered to Redpanda in 2 min while the feed is live: docs/runbooks/capture-produce-stalled.md)"
  else
    result "$label" "PASS ($records records delivered to Redpanda in 2 min; healthcheck $hc)"
  fi
done

# ── (3) the lake: stood down until phase 004 ──────────────────────────────────
# The commit age came from the old pipeline's exporter (lake-metrics) and the
# last run from its 5-minute ingest DAG, both retired with it
# (legacy/v3-lake-ingest/, ADR-056). No live data lands in the lake until phase
# 004, which brings freshness back per product. Nothing here is queried.
step "(3) lake"
result "(3) lake" "SKIP (commit age and last ingest run: the old lake ingest and its exporter are retired, legacy/v3-lake-ingest/ and ADR-056; returns in phase 004)"

# ── (4) the order-book switch: process and host file agree ───────────────────
# ADR-050. Each capture reads config/instruments.yaml once, at start, and exports
# what it read as k2_capture_book_capture. A capture not recreated after a
# change leaves the two disagreeing, and nothing else would show it. (Spark's
# mounted copy was compared too while the old lake's book audit read it; that
# audit retired with the old lake ingest.)
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
  states="process $proc, host file $host"
  if [ "$proc" = missing ]; then
    bad "$label" "FAIL (capture-$venue exports no k2_capture_book_capture: an image older than ADR-050 subscribes to books whatever the file says; $states)"
    continue
  elif [ "$host" != on ] && [ "$host" != off ]; then
    bad "$label" "FAIL (config/instruments.yaml book_capture.$venue is $host, not true or false; $states)"
    continue
  fi
  if [ "$proc" != "$host" ]; then
    bad "$label" "FAIL ($states. capture-$venue was not recreated since the registry changed: docker compose up -d --force-recreate --no-deps capture-$venue)"
  else
    result "$label" "PASS (books $proc: $states)"
  fi
done

# ── (5) the nightly host copy ────────────────────────────────────────────────
# Only on a host that opted in with `make backup-install`; every other host gets a
# SKIP, not a FAIL. 26 h is a daily run plus two hours of slack.
step "(5) backup"
backup_dir=${K2_BACKUP_DIR:-$HOME/k2-backup}
backup_runbook=docs/runbooks/lake-backup-and-restore.md
if [ ! -f "$HOME/.config/systemd/user/k2-backup.timer" ]; then
  result "(5) backup" "SKIP (not set up on this host: make backup-install schedules it, $backup_runbook)"
else
  # "<age of the last success, s or NONE> <status of the last run> <error>"
  backup=$(python3 -c '
import json, sys, time
from datetime import datetime
try:
    s = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    print("NONE none"); sys.exit()
ok = s.get("last_success_utc")
age = int(time.time() - datetime.fromisoformat(ok.replace("Z", "+00:00")).timestamp()) if ok else "NONE"
print(age, s.get("status"), s.get("error") or "")' "$backup_dir/STATUS.json" 2>/dev/null || echo "NONE none")
  read -r backup_age backup_status backup_error <<< "$backup"
  if [ "$backup_age" = NONE ]; then
    bad "(5) backup" "FAIL (the timer is installed and no backup has succeeded into $backup_dir: run make backup, $backup_runbook)"
  elif [ "$backup_age" -gt $((26 * 3600)) ]; then
    bad "(5) backup" "FAIL (last successful backup $((backup_age / 3600))h ago, over 26h: journalctl --user -u k2-backup, $backup_runbook)"
  elif [ "$backup_status" != ok ]; then
    result "(5) backup" "WARN (the last run failed: ${backup_error:-no error recorded}; last success $((backup_age / 3600))h ago, FAIL at 26h)"
  else
    result "(5) backup" "PASS (last successful backup $((backup_age / 3600))h ago, $backup_dir)"
  fi
fi

step "summary"
[ "$fail" = "0" ] && result "health" "PASS" || result "health" "FAIL (see the FAIL lines above)"
exit "$fail"
