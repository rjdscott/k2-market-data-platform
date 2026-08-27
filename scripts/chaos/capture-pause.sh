#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Freeze one capture container (SIGSTOP, via `docker pause`).
#
# Proves three rows in docs/architecture/16-failure-modes.md:
#   capture / SIGSTOP                      — the freeze itself
#   capture · coinbase / sequence_num gap  — --exchange coinbase
#   capture · binance / lastUpdateId regression — --exchange binance
# and reproduces the *signal* of the `exchange / venue maintenance` row: a
# process that is nominally up while last_message_ts stops advancing. It does
# not reproduce that row's cause; nothing local can silence a live venue.
#
# The freeze is held until CaptureDown fires (`up == 0` for 2m). It is NOT held
# for CaptureFeedStale: a paused container stops answering scrapes, Prometheus
# stale-marks every series from that target within a scrape or two, and
# `time() - <absent>` is an empty vector — the staleness rule cannot fire on
# this injection by construction (failure-modes.md, SIGSTOP row). CaptureDown
# is the alert a frozen process actually produces. By the time it fires the
# venue has usually closed the socket server-side. That is
# what makes the reconnect real rather than a resume: on unpause the client
# reconnects, and the venue reports the lost window in its own dialect —
# Coinbase as a sequence_num skip, Binance as a lastUpdateId regression, Kraken
# not at all (v2 does not sequence its book stream; only CRC32 would notice).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

EXCHANGE=$(parse_exchange "$@")
CONTAINER="k2-capture-$EXCHANGE"
# Freshness is scored over the >=1 Hz streams only. `min()` takes the OLDEST
# stream, and `trade`/`market_trades` are legitimately silent for up to 300 s
# (main.rs CONTINUOUS) — including them means a quiet market holds this gate open
# past its timeout and the script dies before it can `report`, turning a market
# state into a failed chaos run.
FRESH="time() - min(k2_capture_last_message_ts_seconds{exchange=\"$EXCHANGE\",stream!~\"trade|market_trades\"})"
GAPS="sum(k2_capture_gaps_total{exchange=\"$EXCHANGE\"})"
RECONNECTS="sum(k2_capture_reconnects_total{exchange=\"$EXCHANGE\"})"

preflight "$CONTAINER" k2-prometheus
banner "capture-pause.sh --exchange $EXCHANGE" \
  CaptureDown docs/runbooks/capture-down.md \
  "docker pause $CONTAINER until the scrape target reads down"

gaps_before=$(prom_query "$GAPS"); gaps_before=${gaps_before:-0}
reconnects_before=$(prom_query "$RECONNECTS"); reconnects_before=${reconnects_before:-0}

echo "→ pausing $CONTAINER" >&2
docker pause "$CONTAINER" >/dev/null
# Always unpause, including on a failed wait or a Ctrl-C. A chaos script that
# can leave the stack broken is a fault of its own.
trap 'docker unpause "$CONTAINER" >/dev/null 2>&1 || true' EXIT

t_fire=$(wait_for_alert CaptureDown 300 "$EXCHANGE") \
  || die "CaptureDown did not fire within ${t_fire}s — check up{job=\"capture-$EXCHANGE\"} went to 0"
echo "→ CaptureDown fired after ${t_fire}s" >&2

echo "→ unpausing" >&2
docker unpause "$CONTAINER" >/dev/null
trap - EXIT

t_fresh=$(wait_for_metric "$FRESH" lt 60 180) || die "no fresh frames after ${t_fresh}s"

# A timeout on the clear is not a recovery time, and adding it to t_fresh does
# not make it one: `t_fresh + <the timeout>` is a constant wearing a
# measurement's clothes, and this number is hand-copied into the FMEA and the
# runbook MTTR tables. If the alert does not clear, the run says so and the
# recovery cell reads `unmeasured`.
if t_clear=$(wait_for_alert_clear CaptureDown 300 "$EXCHANGE"); then
  t_recover=$((t_fresh + t_clear))
else
  t_recover=unmeasured
  echo "→ CaptureDown for $EXCHANGE was still set ${t_clear}s after frames came back." >&2
  echo "  Recovery NOT measured. CaptureDown is up{job=\"capture-$EXCHANGE\"} == 0, so" >&2
  echo "  frames arriving is not what clears it — Prometheus has to scrape the target" >&2
  echo "  successfully again and the alert then has to leave firing. Check the target" >&2
  echo "  in Prometheus (/targets) and that the container is unpaused. Do not publish" >&2
  echo "  a recovery time for this run." >&2
fi

# The interesting part is not that it recovered - it is how the venue reported
# the hole. Give the counters a scrape interval to land before reading them.
sleep 30
gaps_after=$(prom_query "$GAPS"); gaps_after=${gaps_after:-0}
reconnects_after=$(prom_query "$RECONNECTS"); reconnects_after=${reconnects_after:-0}

if [ "$t_recover" = unmeasured ]; then
  echo "→ frames fresh in ${t_fresh}s, alert NOT cleared within ${t_clear}s" >&2
else
  echo "→ frames fresh in ${t_fresh}s, alert cleared in a further ${t_clear}s" >&2
fi
printf '→ gaps %s → %s   reconnects %s → %s\n' \
  "$gaps_before" "$gaps_after" "$reconnects_before" "$reconnects_after" >&2
case $EXCHANGE in
  coinbase) echo "  expect gaps to increase: sequence_num is connection-wide (ADR-027)." >&2
            echo "  expect a memory spike on the rebuild: the BTC-USD level2 snapshot is 5.2 MB / 44k levels." >&2 ;;
  binance)  echo "  expect gaps to increase: lastUpdateId regresses. No rebuild — the next" >&2
            echo "  partial-depth frame is itself a complete top-20 (ADR-027)." >&2 ;;
  kraken)   echo "  expect gaps to stay flat: Kraken v2 does not sequence its book stream." >&2
            echo "  A drifted book would surface as CaptureChecksumFailure instead." >&2 ;;
esac
echo "→ data lost: every frame $EXCHANGE sent while frozen. Public feeds do not replay." >&2

report "capture-pause.sh --exchange $EXCHANGE" CaptureDown "$t_fire" "$t_recover"
