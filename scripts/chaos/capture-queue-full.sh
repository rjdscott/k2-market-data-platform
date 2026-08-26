#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Fill librdkafka's producer queue until capture starts dropping records.
#
# Proves the row `capture → Redpanda / producer queue full` in
# docs/architecture/failure-modes.md — the one place in this tier where data is
# lost rather than delayed, and where the loss is bounded by a computable number.
#
# The fault is `docker pause k2-redpanda`, not `docker stop`: a paused broker
# leaves every TCP connection open and simply stops answering, so librdkafka
# keeps enqueueing instead of failing fast. That is the purest queue-full
# injection available here. `redpanda-stop.sh` covers the fail-fast case.
#
# BLAST RADIUS IS THE WHOLE STACK, NOT JUST CAPTURE. Redpanda is the single
# broker, so pausing it takes down every producer and consumer on it at once:
# all three capture containers, ClickHouse's Kafka-engine consumers, Redpanda
# Console, and Prefect. The exchange passed on the command line is only the one
# being *measured*. Worst case
# for this script is `--exchange coinbase`: 300 s to the predicted first loss,
# and with the 3x wait plus the alert's `for: 5m` the broker can be paused for
# ~28 minutes (150 s half-window + 900 s drop wait + 600 s alert wait). A `docker pause` longer than about five minutes is itself a risk on
# single-node Raft - the node cannot heartbeat its own group while frozen, and
# recovery on unpause is not instant. That is why this ends with an explicit
# `rpk cluster health` rather than assuming the broker came back clean.
#
# It also scores a prediction, and TWO caps race to be the one that binds.
# capacity-model.md §4a-4b puts the wire rate at 173.3 / 164.3 / 75.2 kB/s for
# binance / kraken / coinbase, so 32 MiB of queue is 194 / 204 / 446 s of slack
# before a record is dropped `reason=queue_full`. Independently, sink.rs sets
# `message.timeout.ms=300000`, so a record expires 300 s after enqueue whatever
# the queue is doing, dropped `reason=delivery`. Whichever is smaller is the
# first loss, and the script says which it expects before the fault and which it
# got after: binance/kraken should fill the queue first, coinbase should time
# out first. Getting the other one is a finding about the capacity model or the
# producer config, not a failed run.
#
# The 2026-08-26 run is why this is spelled out. message.timeout.ms was 30 s
# then, so the timeout always won: kraken's first drop came at 102 s against a
# predicted 204 s, and 231,744 records were lost with zero of them queue_full.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

EXCHANGE=$(parse_exchange "$@")
CONTAINER="k2-capture-$EXCHANGE"

case $EXCHANGE in
  binance)  QUEUE_SLACK=194 ;;
  kraken)   QUEUE_SLACK=204 ;;
  coinbase) QUEUE_SLACK=446 ;;
esac

# sink.rs `message.timeout.ms`. Keep in step with it — this is the second cap,
# and on some venues it is the one that binds.
MESSAGE_TIMEOUT=300

if [ "$QUEUE_SLACK" -lt "$MESSAGE_TIMEOUT" ]; then
  PREDICTED=$QUEUE_SLACK
  PREDICTED_MODE=queue_full
  PREDICTED_WHY="32 MiB fills in ${QUEUE_SLACK}s, before the ${MESSAGE_TIMEOUT}s message.timeout.ms"
else
  PREDICTED=$MESSAGE_TIMEOUT
  PREDICTED_MODE=delivery
  PREDICTED_WHY="records expire on the ${MESSAGE_TIMEOUT}s message.timeout.ms before 32 MiB fills at ${QUEUE_SLACK}s"
fi

DROPS="sum(k2_capture_produce_errors_total{exchange=\"$EXCHANGE\"})"
QUEUE_FULL="sum(k2_capture_produce_errors_total{exchange=\"$EXCHANGE\",reason=\"queue_full\"})"
DELIVERY="sum(k2_capture_produce_errors_total{exchange=\"$EXCHANGE\",reason=\"delivery\"})"
PRODUCED="sum(k2_capture_records_produced_total{exchange=\"$EXCHANGE\"})"
MESSAGES="sum(k2_capture_messages_total{exchange=\"$EXCHANGE\"})"

preflight "$CONTAINER" k2-redpanda k2-prometheus
banner "capture-queue-full.sh --exchange $EXCHANGE" \
  CaptureProduceErrors docs/runbooks/capture-down.md \
  "docker pause k2-redpanda (WHOLE STACK: all capture containers, ClickHouse, Console, Prefect); $CONTAINER fills its 32 MiB queue"

drops_before=$(prom_query "$DROPS"); drops_before=${drops_before:-0}
qf_before=$(prom_query "$QUEUE_FULL"); qf_before=${qf_before:-0}
del_before=$(prom_query "$DELIVERY"); del_before=${del_before:-0}
produced_before=$(prom_query "$PRODUCED"); produced_before=${produced_before:-0}
messages_before=$(prom_query "$MESSAGES"); messages_before=${messages_before:-0}

echo "→ pausing k2-redpanda" >&2
printf '  predicted first loss for %s: %ss, reason=%s — %s\n' \
  "$EXCHANGE" "$PREDICTED" "$PREDICTED_MODE" "$PREDICTED_WHY" >&2
docker pause k2-redpanda >/dev/null
trap 'docker unpause k2-redpanda >/dev/null 2>&1 || true' EXIT
paused_at=$SECONDS

# The early warning nobody alerts on: records_produced flattens while messages
# keeps climbing. Sample it once, halfway through the predicted slack window,
# to show the divergence exists before any alert does.
sleep $((PREDICTED / 2))
mid_produced=$(prom_query "$PRODUCED"); mid_produced=${mid_produced:-0}
mid_messages=$(prom_query "$MESSAGES"); mid_messages=${mid_messages:-0}
printf '→ mid-window: messages %s → %s, records produced %s → %s (produced should be flat)\n' \
  "$messages_before" "$mid_messages" "$produced_before" "$mid_produced" >&2

t_drop=$(wait_for_metric "$DROPS" gt "$drops_before" $((PREDICTED * 3))) \
  || die "no produce errors after ${t_drop}s — the queue did not fill; check queue.buffering.max.kbytes"
t_drop=$((t_drop + PREDICTED / 2))

# Every drop figure here is a DELTA against the pre-fault sample. These are
# lifetime counters: comparing a raw reading against zero, or against a
# different reason's total, reports a previous run's drops as this run's.
qf_mid=$(prom_query "$QUEUE_FULL"); qf_mid=${qf_mid:-0}
del_mid=$(prom_query "$DELIVERY"); del_mid=${del_mid:-0}
printf '→ first drop at %ss (predicted %ss, error %s%%)\n' \
  "$t_drop" "$PREDICTED" \
  "$(awk -v a="$t_drop" -v b="$PREDICTED" 'BEGIN { printf "%+.0f", (a - b) / b * 100 }')" >&2
printf '  queue_full %s → %s   delivery %s → %s\n' \
  "$qf_before" "$qf_mid" "$del_before" "$del_mid" >&2
if awk -v a="$qf_mid" -v b="$qf_before" 'BEGIN { exit !(a - b > 0) }'; then
  actual_mode=queue_full
else
  actual_mode=delivery
fi
if [ "$actual_mode" = "$PREDICTED_MODE" ]; then
  printf '  reason=%s, as predicted.\n' "$actual_mode" >&2
elif [ "$actual_mode" = delivery ]; then
  echo "  reason=delivery, NOT the predicted queue_full — message.timeout.ms expired" >&2
  echo "  records before 32 MiB filled. The cap that binds is time, not bytes:" >&2
  echo "  either the wire rate is below the capacity model's, or message.timeout.ms" >&2
  echo "  in sink.rs no longer matches MESSAGE_TIMEOUT at the top of this script." >&2
else
  echo "  reason=queue_full, NOT the predicted delivery — 32 MiB filled faster than" >&2
  echo "  the capacity model says it should. That is a finding about the wire rate." >&2
fi

# 600 s, not 300: the rule is `increase(k2_capture_produce_errors_total[10m]) > 0`
# with `for: 5m`, so the earliest possible firing is ~5.5 minutes after the first
# drop. A 300 s wait expired before the alert could exist and reported the alert
# as broken on every run.
t_fire=$(wait_for_alert CaptureProduceErrors 600 "$EXCHANGE") \
  || echo "→ CaptureProduceErrors did not fire within ${t_fire}s (needs increase(k2_capture_produce_errors_total[10m]) > 0, for: 5m)" >&2

echo "→ unpausing k2-redpanda" >&2
docker unpause k2-redpanda >/dev/null
trap - EXIT

t_recover=$(wait_for_metric "$PRODUCED" gt "$mid_produced" 300) \
  || die "capture did not resume producing after ${t_recover}s"

echo "→ producing again ${t_recover}s after the broker came back" >&2

# One scrape interval so the last drops land before the loss figure is read.
sleep 30
qf_after=$(prom_query "$QUEUE_FULL"); qf_after=${qf_after:-0}
del_after=$(prom_query "$DELIVERY"); del_after=${del_after:-0}
drops_after=$(prom_query "$DROPS"); drops_after=${drops_after:-0}
qf_lost=$(awk -v a="$qf_after" -v b="$qf_before" 'BEGIN { print a - b }')
del_lost=$(awk -v a="$del_after" -v b="$del_before" 'BEGIN { print a - b }')
all_lost=$(awk -v a="$drops_after" -v b="$drops_before" 'BEGIN { print a - b }')
printf '→ data lost: %s records dropped this run — %s reason=queue_full, %s reason=delivery.\n' \
  "$all_lost" "$qf_lost" "$del_lost" >&2
echo "  All three are deltas over this run's fault window only, on $EXCHANGE only." >&2
echo "  queue_full is the 32 MiB cap; delivery is the message.timeout.ms cap." >&2
echo "  Anything the two do not account for is encode or enqueue loss, and is" >&2
echo "  its own finding." >&2
echo "  Dropped means gone — no spill-to-disk, and raw was dropped with the rest," >&2
echo "  so reprocessing cannot recover it. Record the window." >&2
echo "→ total elapsed under fault: $((SECONDS - paused_at))s" >&2

# The broker was frozen for minutes on a single-node Raft cluster; whether it
# came back healthy is not an assumption this script gets to make.
echo "→ cluster health after restore:" >&2
docker exec k2-redpanda rpk cluster health 2>&1 | sed 's/^/  /' >&2 \
  || echo "  rpk cluster health did not answer — check k2-redpanda by hand before the next run." >&2

report "capture-queue-full.sh --exchange $EXCHANGE" CaptureProduceErrors "$t_fire" "$t_recover"
