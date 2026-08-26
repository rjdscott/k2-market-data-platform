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
# the three Kotlin feed handlers, ClickHouse's Kafka-engine consumers, Redpanda
# Console, and Prefect. Capture is only the tier being *measured*. Worst case
# for this script is `--exchange coinbase`: 446 s of predicted slack, and with
# the 3x wait plus the alert's `for: 5m` the broker can be paused for ~36
# minutes (223 s half-window + 1,338 s drop wait + 600 s alert wait). A `docker pause` longer than about five minutes is itself a risk on
# single-node Raft - the node cannot heartbeat its own group while frozen, and
# recovery on unpause is not instant. That is why this ends with an explicit
# `rpk cluster health` rather than assuming the broker came back clean.
#
# It also scores a prediction. capacity-model.md §4a-4b puts the wire rate at
# 173.3 / 164.3 / 75.2 kB/s for binance / kraken / coinbase, so 32 MiB of queue
# is 194 / 204 / 446 seconds of slack. The script prints predicted vs measured;
# a large gap is a finding about the capacity model, not a failed run.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

EXCHANGE=$(parse_exchange "$@")
CONTAINER="k2-capture-$EXCHANGE"

case $EXCHANGE in
  binance)  PREDICTED=194 ;;
  kraken)   PREDICTED=204 ;;
  coinbase) PREDICTED=446 ;;
esac

DROPS="sum(k2_capture_produce_errors_total{exchange=\"$EXCHANGE\"})"
QUEUE_FULL="sum(k2_capture_produce_errors_total{exchange=\"$EXCHANGE\",reason=\"queue_full\"})"
PRODUCED="sum(k2_capture_records_produced_total{exchange=\"$EXCHANGE\"})"
MESSAGES="sum(k2_capture_messages_total{exchange=\"$EXCHANGE\"})"

preflight "$CONTAINER" k2-redpanda k2-prometheus
banner "capture-queue-full.sh --exchange $EXCHANGE" \
  CaptureProduceErrors docs/runbooks/capture-down.md \
  "docker pause k2-redpanda (WHOLE STACK: feed handlers, ClickHouse, Console, Prefect); $CONTAINER fills its 32 MiB queue"

drops_before=$(prom_query "$DROPS"); drops_before=${drops_before:-0}
qf_before=$(prom_query "$QUEUE_FULL"); qf_before=${qf_before:-0}
produced_before=$(prom_query "$PRODUCED"); produced_before=${produced_before:-0}
messages_before=$(prom_query "$MESSAGES"); messages_before=${messages_before:-0}

echo "→ pausing k2-redpanda (predicted ${PREDICTED}s of queue slack for $EXCHANGE)" >&2
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
printf '→ first drop at %ss (predicted %ss, error %s%%)\n' \
  "$t_drop" "$PREDICTED" \
  "$(awk -v a="$t_drop" -v b="$PREDICTED" 'BEGIN { printf "%+.0f", (a - b) / b * 100 }')" >&2
if awk -v a="$qf_mid" -v b="$qf_before" 'BEGIN { exit !(a - b > 0) }'; then
  echo "  reason=queue_full, as designed." >&2
else
  echo "  reason is NOT queue_full — message.timeout.ms expired records before the" >&2
  echo "  queue filled. That is a finding: the cap that binds is time, not bytes." >&2
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
drops_after=$(prom_query "$DROPS"); drops_after=${drops_after:-0}
qf_lost=$(awk -v a="$qf_after" -v b="$qf_before" 'BEGIN { print a - b }')
all_lost=$(awk -v a="$drops_after" -v b="$drops_before" 'BEGIN { print a - b }')
printf '→ data lost: %s records dropped this run, %s of them reason=queue_full.\n' \
  "$all_lost" "$qf_lost" >&2
echo "  Both are deltas over this run's fault window only, on $EXCHANGE only." >&2
echo "  A gap between the two totals is records lost for some other reason —" >&2
echo "  encode, enqueue or delivery — and is its own finding." >&2
echo "  Dropped means gone — no spill-to-disk, and raw was dropped with the rest," >&2
echo "  so reprocessing cannot recover it. Record the window." >&2
echo "→ total elapsed under fault: $((SECONDS - paused_at))s" >&2

# The broker was frozen for minutes on a single-node Raft cluster; whether it
# came back healthy is not an assumption this script gets to make.
echo "→ cluster health after restore:" >&2
docker exec k2-redpanda rpk cluster health 2>&1 | sed 's/^/  /' >&2 \
  || echo "  rpk cluster health did not answer — check k2-redpanda by hand before the next run." >&2

report "capture-queue-full.sh --exchange $EXCHANGE" CaptureProduceErrors "$t_fire" "$t_recover"
