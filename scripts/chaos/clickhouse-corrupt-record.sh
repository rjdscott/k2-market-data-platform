#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Put one un-framed record on an Avro topic ClickHouse consumes and prove the
# feed neither stalls nor loses it: the record lands in gold.feed_errors with
# its bytes, the partition keeps moving, and ClickHouseKafkaMessagesFailed says
# it happened.
#
# Proves one row in docs/architecture/16-failure-modes.md:
#   ClickHouse (served tier) / undecodable record on a feed topic
#
# This is the failure the first live feed hit twice on 2026-08-27 (docker/
# clickhouse/README.md): under the default mode a JSON frame stalled
# trades.kraken partition 0 forever, and under kafka_skip_broken_messages a
# schema-id-0 frame still stalled partition 10, because a registry 404 is not a
# "broken message" to that setting. kafka_handle_error_mode = 'stream' is what
# this script asserts.
#
# LEAVES ONE RECORD on the topic, permanently within retention, and one row in
# gold.feed_errors. Both are the correct record of what happened.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

TOPIC="${K2_CHAOS_TOPIC:-market.crypto.v3.trades.kraken}"
GROUP=k2-gold-trades
CH=k2-clickhouse
MARKER="k2-chaos-$(date -u +%s)"
ch() { docker exec "$CH" clickhouse-client --password "${CLICKHOUSE_PASSWORD:?set CLICKHOUSE_PASSWORD (set -a; . ./.env)}" -q "$1" 2>/dev/null; }

preflight "$CH" k2-redpanda k2-prometheus
banner "clickhouse-corrupt-record.sh topic=$TOPIC" \
  ClickHouseKafkaMessagesFailed docs/runbooks/clickhouse-rebuild-from-lake.md \
  "produce one JSON record on $TOPIC; the feed must skip it into gold.feed_errors and keep consuming"

failed_before=$(prom_query 'sum(ClickHouseProfileEvents_KafkaMessagesFailed)'); failed_before=${failed_before:-0}
errors_before=$(ch "SELECT count() FROM gold.feed_errors")
echo "→ before: KafkaMessagesFailed=$failed_before, feed_errors rows=$errors_before" >&2

echo "→ producing {\"chaos\":\"$MARKER\"} on $TOPIC" >&2
start=$SECONDS
printf '{"chaos":"%s"}\n' "$MARKER" | docker exec -i k2-redpanda rpk topic produce "$TOPIC" >/dev/null

t_landed=""
for _ in $(seq 1 60); do
  n=$(ch "SELECT count() FROM gold.feed_errors WHERE raw LIKE '%$MARKER%'" || echo 0)
  if [ "${n:-0}" -ge 1 ]; then t_landed=$((SECONDS - start)); break; fi
  sleep 2
done
[ -n "$t_landed" ] || die "the record did not reach gold.feed_errors within 120s — is kafka_handle_error_mode = 'stream' on gold.q_trades?"
echo "→ in gold.feed_errors after ${t_landed}s: $(ch "SELECT concat(topic, '/', toString(partition), '@', toString(offset), ': ', substring(error, 1, 60)) FROM gold.feed_errors WHERE raw LIKE '%$MARKER%'")" >&2

# the partition keeps moving: the group's lag on every partition of the topic is 0.
# Measured BEFORE waiting on the alert, so the recovery time is the feed's, not
# the rule evaluation cadence's (the first run reported 123 s for a 3 s recovery).
t_recover=""
for _ in $(seq 1 60); do
  lag=$(docker exec k2-redpanda rpk group describe "$GROUP" 2>/dev/null | awk -v t="$TOPIC" '$1 == t && $6 ~ /^[0-9]+$/ {s += $6} END {print s + 0}')
  if [ "${lag:-1}" -eq 0 ]; then t_recover=$((SECONDS - start)); break; fi
  sleep 2
done
[ -n "$t_recover" ] || die "consumer group $GROUP still lags on $TOPIC after 120s — the record stalled the feed"
t_fire=$(wait_for_alert ClickHouseKafkaMessagesFailed 120) && echo "→ ClickHouseKafkaMessagesFailed fired after ${t_fire}s" >&2 || { echo "→ alert did not fire within ${t_fire}s" >&2; t_fire=none; }

failed_after=$(prom_query 'sum(ClickHouseProfileEvents_KafkaMessagesFailed)'); failed_after=${failed_after:-0}
echo "→ lag 0 on $TOPIC after ${t_recover}s; KafkaMessagesFailed $failed_before -> $failed_after" >&2
report "clickhouse-corrupt-record.sh topic=$TOPIC" ClickHouseKafkaMessagesFailed "$t_fire" "$t_recover"
