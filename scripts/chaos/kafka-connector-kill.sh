#!/usr/bin/env bash
# W2-12, plan 004's exit "a killed Kafka connector resumes exactly once" (Verification:
# kafka-connector-kill.sh): on ONE throwaway lake with a Redpanda (prefix kafka-connector-kill,
# K2_THROWAWAY_REDPANDA=1) from <release>, the release's own kraken-trades load, audit and publish
# (docker/lake/connector.py in the DAG factory's shape, scripts/lib/run-path-lib.sh), its driver
# SIGKILLed inside the task by scripts/lib/kafka_path_probe.py's `kill`: at SparkLanding.land (before
# the landing commit) or at kafka.stage (after it), as a container OOM-kill or the run's timeout
# leaves it. The scenario and its claims are kafka-path.sh group a's 4 to 6, on its fixture product
# there and on kraken-trades here. Frames: services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl
# (1185 frames, 3 trades), produced again before each run. No dbt build: the connector is what dies.
#
#   bash scripts/chaos/kafka-connector-kill.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Runs k3 (a clean run, the baseline) to k8, then each claim, one PASS or FAIL line, then
# `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 a load killed after its landing commit (k4): the next run (k5) stages that range and its own,
#     every trade once, no hole, bronze's through-offsets = the landing's end offsets = the topic's
#   2 a load killed before its landing commit (k6) leaves the landing unchanged; the next (k7) lands
#     the range once, in one commit
#   3 a retry under the same run id after a landing commit (k8) stages the new range; its audit and
#     publish pass, every trade once
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down kafka-connector-kill`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start kafka-connector-kill "${1:-}" 3
export K2_THROWAWAY_REDPANDA=1
rp_lake
rp_chain_files
kkill() {  # kkill <before|after> <run>: kraken-trades' load, its driver killed before or after the landing commit
  docker exec -i "$SPARK" python3 - kill "$1" "/k2/releases/$ID" load --release "$ID" --product "$KT" \
    --run-id "kraken_trades/$2" < "$KPROBE" > "$RP/$2.kill.out" 2> "$RP/$2.kill.err"
  echo $? > "$RP/$2.kill.rc"
  echo "$P: $2 load killed $1 its landing commit, exit $(cat "$RP/$2.kill.rc")"
}
kseed "$RP/frames.jsonl"; kruns k3; kfacts k3     # the baseline (kafka_path_probe.py's claims read k3 to k8)
kseed "$RP/frames.jsonl"; kkill after k4; kfacts k4
kseed "$RP/frames.jsonl"; kruns k5; kfacts k5
kseed "$RP/frames.jsonl"; kkill before k6; kfacts k6
kruns k7; kfacts k7
kseed "$RP/frames.jsonl"; kkill after k8
TRY=2 TP=$KT task "$ID" k8 load; TP=$KT task "$ID" k8 audit; TP=$KT task "$ID" k8 publish; kfacts k8
kclaim kill-after
kclaim kill-before
kclaim retry
finish
