#!/usr/bin/env bash
# W2-14, plan 004's exit "an unknown symbol is held without stopping the venue" (Verification:
# "unknown-symbol.sh: held, alert, instruments run, other symbols publish"): on ONE throwaway lake
# with a Redpanda (prefix unknown-symbol, K2_THROWAWAY_REDPANDA=1) from <release>, the phase 004
# chain's first cycle, then one batch holding a trade of DELTA/USD, which the listing lacks, beside
# a trade of BTC/USD. run-path.sh claim 13's case, with the listed symbol's trade in the same batch.
# Each task the release's own, in the DAG factory's shape (scripts/lib/run-path-lib.sh); frames and
# listings synthetic (rp_chain_files).
#
#   bash scripts/chaos/unknown-symbol.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 to 3 the chain's first cycle: kraken-instruments, security-master, kraken-trades with
#     canonical-trades and candles (run-path.sh claims 10 to 12)
#   4 kt-2: DELTA/USD's trade is held in silver_trade_unresolved, not in silver_trade; BTC/USD's trade
#     of the same batch publishes to silver_trade and canonical-trades; the build is degraded, warned
#     by unresolved_rows_zero alone, k2runs.state degraded; the instruments run starts: the release's
#     kraken-trades `refresh` names kraken-instruments on that rule, and factory.Refresh's own rule
#     over kt-2's result says start. That run (ki-2, a listing naming DELTA/USD) and the security
#     master (sm-2) run, and the next trades run (kt-3) takes DELTA/USD in once, nothing held, state
#     complete
# With no Airflow here, the trigger itself (TriggerDagRunOperator, one run an hour) is
# docker/airflow/tests/test_factory.py's to prove; the alert on the degraded page is the live stack's.
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down unknown-symbol`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start unknown-symbol "${1:-}" 4
rp_image
export K2_THROWAWAY_REDPANDA=1
rp_lake
rp_chain_files
rp_chain                                                  # 1 to 3
cat "$RP/unknown.jsonl" "$RP/more.jsonl" > "$RP/mixed.jsonl"  # DELTA/USD's trade frame, then BTC/USD's
kseed "$RP/mixed.jsonl"; trades kt-2; probe chain-state kt-2
build "$ID" "$CT" ct-2; build "$ID" "$CA" ca-2; probe chain-state ca-2
listing ki-2 listing-2.json; build "$ID" "$KI" ki-2; build "$ID" "$SM" sm-2   # the instruments run the refresh starts
trades kt-3; probe chain-state kt-3
claim unknown "$ID"                                       # 4
finish
