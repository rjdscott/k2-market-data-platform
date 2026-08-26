#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Stop the broker under a running capture tier.
#
# Proves three rows in docs/architecture/failure-modes.md:
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
# broker is down: the encoder cannot fetch a schema id, so nothing is even
# enqueued and the 32 MiB of queue slack does not exist.
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
banner "redpanda-stop.sh --exchange $EXCHANGE cold_start=$COLD_START" \
  CaptureProduceErrors docs/runbooks/capture-down.md \
  "docker stop k2-redpanda (broker AND schema registry, one process)"

produced_before=$(prom_query "$PRODUCED"); produced_before=${produced_before:-0}
drops_before=$(prom_query "$DROPS"); drops_before=${drops_before:-0}

echo "→ stopping k2-redpanda" >&2
docker stop k2-redpanda >/dev/null
trap 'docker start k2-redpanda >/dev/null 2>&1 || true' EXIT

if [ "$COLD_START" = yes ]; then
  echo "→ --cold-start: recreating $CONTAINER with no registry to talk to" >&2
  compose up -d --force-recreate --no-deps "capture-$EXCHANGE" >/dev/null
  sleep 90
  produced_cold=$(prom_query "$PRODUCED"); produced_cold=${produced_cold:-0}
  messages_cold=$(prom_query "$MESSAGES"); messages_cold=${messages_cold:-0}
  printf '→ after 90s cold: records produced %s, messages seen %s\n' \
    "$produced_cold" "$messages_cold" >&2
  echo "  Expected: produced pinned at 0 while messages climbs. The encoder needs" >&2
  echo "  a schema id for bytes 1-4 of the Confluent frame and cannot get one, so" >&2
  echo "  no record is built and nothing is enqueued — loss starts at frame one," >&2
  echo "  with none of the 32 MiB queue slack the broker-down row gets." >&2
else
  # The cached-registry row: ids are already in-process, so records are still
  # being built and enqueued even though the registry is unreachable.
  sleep 45
  produced_mid=$(prom_query "$PRODUCED"); produced_mid=${produced_mid:-0}
  drops_mid=$(prom_query "$DROPS"); drops_mid=${drops_mid:-0}
  printf '→ after 45s: records produced %s → %s, produce errors %s → %s\n' \
    "$produced_before" "$produced_mid" "$drops_before" "$drops_mid" >&2
  echo "  Records still being built with the registry down = the cached-id row." >&2
  echo "  They are queued, not delivered; the queue-full row is where they die." >&2
fi

t_fire=$(wait_for_alert CaptureProduceErrors 900) \
  || echo "→ CaptureProduceErrors did not fire within ${t_fire}s" >&2

echo "→ starting k2-redpanda" >&2
docker start k2-redpanda >/dev/null
trap - EXIT

t_recover=$(wait_for_metric "$PRODUCED" gt "$produced_before" 600) \
  || die "capture did not resume producing after ${t_recover}s (a restart may be needed — capture-down.md §2)"
echo "→ producing again ${t_recover}s after the broker returned, with no capture restart" >&2

if [ "$COLD_START" = yes ]; then
  echo "→ restoring $CONTAINER to a normal start" >&2
  compose up -d --force-recreate --no-deps "capture-$EXCHANGE" >/dev/null
fi

report "redpanda-stop.sh --exchange $EXCHANGE cold_start=$COLD_START" \
  CaptureProduceErrors "$t_fire" "$t_recover"
