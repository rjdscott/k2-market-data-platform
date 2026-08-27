#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# SIGKILL one capture container.
#
# Proves the row `capture (any venue) / SIGKILL` in
# docs/architecture/16-failure-modes.md.
#
# Two behaviours in one fault, and the runbook predicts both:
#   --hold 0    SIGKILL only. `restart: unless-stopped` brings it back inside one
#               scrape interval, CaptureDown never fires, and that IS the pass
#               condition (capture-down.md §1: "a single crash self-heals in well
#               under the alert's 2-minute window and never fires").
#   --hold N    SIGKILL, then `docker stop` so the restart policy stays out of the
#               way for N seconds. This is what exercises the alert path. Default
#               150s: just past the alert's `for: 2m`.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

EXCHANGE=$(parse_exchange "$@")
HOLD=150
while [ $# -gt 0 ]; do
  case $1 in
    --hold)
      [ $# -ge 2 ] || die "--hold needs a value in seconds"
      HOLD=$2; shift 2 ;;
    --hold=*) HOLD=${1#*=}; shift ;;
    *) shift ;;
  esac
done

CONTAINER="k2-capture-$EXCHANGE"
UP="up{job=\"capture-$EXCHANGE\"}"
# >=1 Hz streams only. `min()` takes the OLDEST stream, and `trade`/
# `market_trades` are legitimately silent for up to 300 s (main.rs CONTINUOUS);
# including them lets a quiet market time this gate out and `die` before `report`.
FRESH="time() - min(k2_capture_last_message_ts_seconds{exchange=\"$EXCHANGE\",stream!~\"trade|market_trades\"})"

preflight "$CONTAINER" k2-prometheus
banner "capture-kill.sh --exchange $EXCHANGE --hold $HOLD" \
  CaptureDown docs/runbooks/capture-down.md \
  "SIGKILL $CONTAINER, held down for ${HOLD}s"

restarts_before=$(docker inspect -f '{{.RestartCount}}' "$CONTAINER")

echo "→ SIGKILL $CONTAINER" >&2
docker kill --signal=KILL "$CONTAINER" >/dev/null

if [ "$HOLD" -gt 0 ]; then
  # The container is already restarting under `unless-stopped`; a manual stop is
  # what keeps it down long enough for `for: 2m` to elapse. The fault was still
  # a SIGKILL - this only suppresses the auto-restart while we watch the alert.
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
  # `docker stop` deliberately defeats `restart: unless-stopped`, so from here
  # until the restore below nothing brings this venue back on its own. Two to
  # four minutes of that is spent inside `wait_for_alert`. A Ctrl-C, a
  # Prometheus blip or a broken pipe in that window would otherwise leave the
  # venue dark indefinitely - the exact fault this directory promises it cannot
  # cause (README: "a chaos script that can leave the stack broken is a fault of
  # its own").
  trap 'compose up -d "capture-$EXCHANGE" >/dev/null 2>&1 || true' EXIT
fi

t_fire=$(wait_for_alert CaptureDown $((HOLD + 120)) "$EXCHANGE") && fired=yes || fired=no
if [ "$fired" = yes ]; then
  echo "→ CaptureDown fired after ${t_fire}s" >&2
else
  echo "→ CaptureDown did not fire within ${t_fire}s — expected when --hold is short:" >&2
  echo "  Docker restarted the container inside the 2m window (capture-down.md §1)." >&2
  t_fire=none
fi

echo "→ restoring" >&2
compose up -d "capture-$EXCHANGE" >/dev/null
trap - EXIT

# Recovered means two things, and both have to hold: Prometheus can scrape it
# again, and a frame has actually arrived since. `up == 1` alone would pass on a
# process that came back and never reconnected to the venue.
t_up=$(wait_for_metric "$UP" eq 1 180) || die "still not scrapeable after ${t_up}s"
t_fresh=$(wait_for_metric "$FRESH" lt 60 180) || die "no fresh frames after ${t_fresh}s"
t_recover=$((t_up + t_fresh))

restarts_after=$(docker inspect -f '{{.RestartCount}}' "$CONTAINER")
echo "→ scrapeable in ${t_up}s, fresh frames in a further ${t_fresh}s" >&2
echo "→ docker restart count ${restarts_before} → ${restarts_after}" >&2
echo "→ data lost: every frame $EXCHANGE sent while down. Public feeds do not replay." >&2

report "capture-kill.sh --exchange $EXCHANGE --hold $HOLD" CaptureDown "$t_fire" "$t_recover"
