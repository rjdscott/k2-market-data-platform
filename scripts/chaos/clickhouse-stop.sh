#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Stop ClickHouse, hold it down past ClickHouseDown's `for: 2m`, bring it back,
# and measure how long until the served tier is answering and its Kafka feeds
# are re-assigned.
#
# Proves one row in docs/architecture/16-failure-modes.md:
#   ClickHouse (served tier) / container down
#
# What is at stake is nothing: gold is derived. The feeds resume from the
# consumer groups' committed offsets, so the minutes the server was down are
# consumed on return (bounded by the topics' 7-day retention), and anything
# older than that is a pull from the lake (docs/runbooks/clickhouse-rebuild-
# from-lake.md). The script asserts the row count is unchanged across the
# outage — a restart must not lose committed parts — and reports the two times.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

HOLD=${K2_CHAOS_HOLD:-150}
CH=k2-clickhouse
ch() { docker exec "$CH" clickhouse-client --password "${CLICKHOUSE_PASSWORD:?set CLICKHOUSE_PASSWORD (set -a; . ./.env)}" -q "$1" 2>/dev/null; }

preflight "$CH" k2-redpanda k2-prometheus
banner "clickhouse-stop.sh --hold $HOLD" \
  ClickHouseDown docs/runbooks/clickhouse-rebuild-from-lake.md \
  "docker stop $CH, held down for ${HOLD}s; feeds must resume from committed offsets"

rows_before=$(ch "SELECT count() FROM gold.trades")
consumers_before=$(ch "SELECT count() FROM system.kafka_consumers WHERE database = 'gold'")
echo "→ before: gold.trades $rows_before rows, $consumers_before consumers" >&2

echo "→ docker stop $CH" >&2
docker stop "$CH" >/dev/null
trap 'compose up -d clickhouse >/dev/null 2>&1 || true' EXIT
t_fire=$(wait_for_alert ClickHouseDown $((HOLD + 120))) && fired=yes || fired=no
if [ "$fired" = yes ]; then
  echo "→ ClickHouseDown fired after ${t_fire}s" >&2
else
  echo "→ ClickHouseDown did not fire within ${t_fire}s" >&2; t_fire=none
fi
[ "$HOLD" -gt 0 ] && sleep "$HOLD" || true

echo "→ restoring" >&2
start=$SECONDS
compose up -d clickhouse >/dev/null
trap - EXIT
wait_healthy "$CH" 180 || die "ClickHouse not healthy after 180s"
t_healthy=$((SECONDS - start))
# the feeds: every consumer back with an assignment
for _ in $(seq 1 60); do
  assigned=$(ch "SELECT count() FROM system.kafka_consumers WHERE database = 'gold' AND length(assignments.topic) > 0" || echo 0)
  [ "${assigned:-0}" -ge "$consumers_before" ] && break
  sleep 2
done
t_recover=$((SECONDS - start))
rows_after=$(ch "SELECT count() FROM gold.trades")
echo "→ healthy after ${t_healthy}s; $assigned/$consumers_before consumers assigned after ${t_recover}s" >&2
echo "→ gold.trades $rows_before -> $rows_after rows across the outage" >&2
[ "$rows_after" -ge "$rows_before" ] || die "gold.trades lost rows across a restart: $rows_before -> $rows_after"
report "clickhouse-stop.sh --hold $HOLD" ClickHouseDown "$t_fire" "$t_recover"
