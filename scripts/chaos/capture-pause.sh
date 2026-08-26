#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Freeze one capture container (SIGSTOP, via `docker pause`).
#
# Proves three rows in docs/architecture/failure-modes.md:
#   capture / SIGSTOP                      — the freeze itself
#   capture · coinbase / sequence_num gap  — --exchange coinbase
#   capture · binance / lastUpdateId regression — --exchange binance
# and reproduces the *signal* of the `exchange / venue maintenance` row: a
# process that is nominally up while last_message_ts stops advancing. It does
# not reproduce that row's cause; nothing local can silence a live venue.
#
# The freeze is held until CaptureFeedStale fires (60s staleness + `for: 2m`),
# by which point the venue has usually closed the socket server-side. That is
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
FRESH="time() - min(k2_capture_last_message_ts_seconds{exchange=\"$EXCHANGE\"})"
GAPS="sum(k2_capture_gaps_total{exchange=\"$EXCHANGE\"})"
RECONNECTS="sum(k2_capture_reconnects_total{exchange=\"$EXCHANGE\"})"

preflight "$CONTAINER" k2-prometheus
banner "capture-pause.sh --exchange $EXCHANGE" \
  CaptureFeedStale docs/runbooks/capture-feed-stale.md \
  "docker pause $CONTAINER until the feed reads stale"

gaps_before=$(prom_query "$GAPS"); gaps_before=${gaps_before:-0}
reconnects_before=$(prom_query "$RECONNECTS"); reconnects_before=${reconnects_before:-0}

echo "→ pausing $CONTAINER" >&2
docker pause "$CONTAINER" >/dev/null
# Always unpause, including on a failed wait or a Ctrl-C. A chaos script that
# can leave the stack broken is a fault of its own.
trap 'docker unpause "$CONTAINER" >/dev/null 2>&1 || true' EXIT

t_fire=$(wait_for_alert CaptureFeedStale 300) \
  || die "CaptureFeedStale did not fire within ${t_fire}s — check the metric is labelled {exchange,stream}"
echo "→ CaptureFeedStale fired after ${t_fire}s" >&2

echo "→ unpausing" >&2
docker unpause "$CONTAINER" >/dev/null
trap - EXIT

t_fresh=$(wait_for_metric "$FRESH" lt 60 180) || die "no fresh frames after ${t_fresh}s"
t_clear=$(wait_for_alert_clear CaptureFeedStale 300) || echo "→ alert still set after ${t_clear}s" >&2
t_recover=$((t_fresh + t_clear))

# The interesting part is not that it recovered - it is how the venue reported
# the hole. Give the counters a scrape interval to land before reading them.
sleep 30
gaps_after=$(prom_query "$GAPS"); gaps_after=${gaps_after:-0}
reconnects_after=$(prom_query "$RECONNECTS"); reconnects_after=${reconnects_after:-0}

echo "→ frames fresh in ${t_fresh}s, alert cleared in a further ${t_clear}s" >&2
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

report "capture-pause.sh --exchange $EXCHANGE" CaptureFeedStale "$t_fire" "$t_recover"
