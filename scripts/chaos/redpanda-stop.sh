#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Stop the broker under a running capture tier.
#
# Proves three rows in docs/architecture/16-failure-modes.md:
#   Redpanda / broker down                      — default mode
#   schema registry / down mid-run (cached ids) — the opening seconds of the same run
#   schema registry / down at process start     — --cold-start
#
# On Redpanda the schema registry is not a separate service: it is the same
# process, on :8081 (`--schema-registry-addr` in docker-compose.yml). So the two
# registry rows are not independently injectable here, and the honest thing is
# to say which parts of one run stand for which row rather than pretend to two
# faults. Default mode shows the cached case (records keep being produced after
# the broker is gone, because the ids are already in-process). --cold-start
# shows the uncached case by force-recreating a capture container while the
# broker is down: `Sink::warm_up` cannot fetch a schema id, and since that is
# fatal the process exits before opening the socket and crash-loops. Nothing is
# enqueued, the 32 MiB of queue slack does not exist, and the alert that names
# it is CaptureDown - not CaptureProduceErrors, which needs a produce to fail
# and there are none.
#
# BLAST RADIUS IS THE WHOLE STACK, NOT JUST CAPTURE. Redpanda is the single
# broker and the single schema registry, so stopping it takes down every
# producer and consumer at once: all three capture containers, ClickHouse's
# Kafka-engine consumers, Redpanda Console, and Prefect. The exchange passed on
# the command line is only the one being *measured*. The broker is down for the alert's `for: 5m` plus the
# wait, ~15 minutes worst case (the 900 s `wait_for_alert` is the binding cap). The run ends with an explicit
# `rpk cluster health` rather than assuming a clean return.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

EXCHANGE=$(parse_exchange "$@")
COLD_START=no
for arg in "$@"; do
  if [ "$arg" = "--cold-start" ]; then COLD_START=yes; fi
done

CONTAINER="k2-capture-$EXCHANGE"
PRODUCED="sum(k2_capture_records_produced_total{exchange=\"$EXCHANGE\"})"
MESSAGES="sum(k2_capture_messages_total{exchange=\"$EXCHANGE\"})"
DROPS="sum(k2_capture_produce_errors_total{exchange=\"$EXCHANGE\"})"

preflight "$CONTAINER" k2-redpanda k2-prometheus
# Which alert this run is scored against. A cold start never reaches a produce,
# so CaptureProduceErrors cannot fire; the container crash-loops instead.
ALERT=CaptureProduceErrors
[ "$COLD_START" = yes ] && ALERT=CaptureDown

banner "redpanda-stop.sh --exchange $EXCHANGE cold_start=$COLD_START" \
  "$ALERT" docs/runbooks/capture-down.md \
  "docker stop k2-redpanda — broker AND schema registry, one process, WHOLE STACK (all capture containers, ClickHouse, Console, Prefect)"

produced_before=$(prom_query "$PRODUCED"); produced_before=${produced_before:-0}
drops_before=$(prom_query "$DROPS"); drops_before=${drops_before:-0}

echo "→ stopping k2-redpanda" >&2
docker stop k2-redpanda >/dev/null
# In --cold-start the capture container is *also* force-recreated into a broken
# state below, so the trap has to restore both. It stays armed until after that
# second restore, not just until the broker is back: a `die` between the two
# would otherwise leave the venue pinned in its cold state with no registry
# contact and no counter moving.
RESTORE_CAPTURE=no
restore() {
  docker start k2-redpanda >/dev/null 2>&1 || true
  if [ "$RESTORE_CAPTURE" = yes ]; then
    compose up -d --force-recreate --no-deps "capture-$EXCHANGE" >/dev/null 2>&1 || true
  fi
}
trap restore EXIT

if [ "$COLD_START" = yes ]; then
  echo "→ --cold-start: recreating $CONTAINER with no registry to talk to" >&2
  RESTORE_CAPTURE=yes
  compose up -d --force-recreate --no-deps "capture-$EXCHANGE" >/dev/null
  sleep 90
  produced_cold=$(prom_query "$PRODUCED"); produced_cold=${produced_cold:-0}
  messages_cold=$(prom_query "$MESSAGES"); messages_cold=${messages_cold:-0}
  produced_baseline=$produced_cold
  printf '→ after 90s cold: records produced %s, messages seen %s\n' \
    "$produced_cold" "$messages_cold" >&2
  echo "  Expected: BOTH at 0, and the container restarting. Sink::warm_up fetches" >&2
  echo "  every subject's schema before the first connect and a failure is fatal," >&2
  echo "  so with no registry the process exits at startup and never opens the" >&2
  echo "  socket — loss starts before frame one, with none of the 32 MiB queue" >&2
  echo "  slack the broker-down row gets. Restarts so far:" >&2
  docker inspect -f '  {{.RestartCount}} restarts, last exit {{.State.ExitCode}}' \
    "$CONTAINER" >&2 2>/dev/null || true
else
  # The cached-registry row: ids are already in-process, so records are still
  # being built and enqueued even though the registry is unreachable.
  sleep 45
  produced_mid=$(prom_query "$PRODUCED"); produced_mid=${produced_mid:-0}
  drops_mid=$(prom_query "$DROPS"); drops_mid=${drops_mid:-0}
  produced_baseline=$produced_mid
  printf '→ after 45s: records produced %s → %s, produce errors %s → %s\n' \
    "$produced_before" "$produced_mid" "$drops_before" "$drops_mid" >&2
  echo "  Records still being built with the registry down = the cached-id row." >&2
  echo "  They are queued, not delivered; the queue-full row is where they die." >&2
fi

t_fire=$(wait_for_alert "$ALERT" 900 "$EXCHANGE") \
  || echo "→ $ALERT did not fire within ${t_fire}s" >&2

echo "→ starting k2-redpanda" >&2
docker start k2-redpanda >/dev/null

# Baseline is the MID-OUTAGE sample, not the pre-fault one. records_produced
# counts local enqueue, not delivery (sink.rs), so in default mode it climbs
# throughout the outage and `gt produced_before` is already true before the
# broker is even restarted — a recovery time of ~0 that measures nothing. In
# --cold-start the baseline is the cold sample, which is the meaningful 0.
#
# What this measures in default mode is therefore ENQUEUE resuming past its
# mid-outage level, on $EXCHANGE, over this run's window. Delivery recovery is
# the broker's side of it and is what `rpk cluster health` below reports.
t_recover=$(wait_for_metric "$PRODUCED" gt "$produced_baseline" 600) \
  || die "capture did not resume producing after ${t_recover}s (a restart may be needed — capture-down.md §2)"
echo "→ enqueueing past the mid-outage level ${t_recover}s after the broker returned, with no capture restart" >&2

if [ "$COLD_START" = yes ]; then
  echo "→ restoring $CONTAINER to a normal start" >&2
  compose up -d --force-recreate --no-deps "capture-$EXCHANGE" >/dev/null
  RESTORE_CAPTURE=no
fi
trap - EXIT

echo "→ cluster health after restore:" >&2
docker exec k2-redpanda rpk cluster health 2>&1 | sed 's/^/  /' >&2 \
  || echo "  rpk cluster health did not answer — check k2-redpanda by hand before the next run." >&2

report "redpanda-stop.sh --exchange $EXCHANGE cold_start=$COLD_START" \
  "$ALERT" "$t_fire" "$t_recover"
