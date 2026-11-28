#!/usr/bin/env bash
# W2-17, plan 004's exit "a paused input makes the derived product partial" (Verification:
# "pause-input.sh kraken-trades: canonical-trades runs and shows partial"): on ONE throwaway lake
# with a Redpanda (prefix pause-input, K2_THROWAWAY_REDPANDA=1) from <release>, the phase 004 chain's
# first cycle, then the product that reads <input> built on a release of this tree with <input>
# paused (status paused, a lifecycleChange). run-path.sh claim 16's case, after the first cycle. Each
# task the release's own, in the DAG factory's shape (scripts/lib/run-path-lib.sh); frames synthetic.
#
#   bash scripts/chaos/pause-input.sh <input> <release dir>     # KEEP=1 keeps the scratch directory
#
# <input>: kraken-trades, which canonical-trades reads. Phase 005 adds ecb-fx-rates, which
# candles-eur reads (W3-11), as a second case below.
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 to 3 the chain's first cycle: kraken-instruments, security-master, kraken-trades with
#     canonical-trades and candles (run-path.sh claims 10 to 12)
#   4 kraken-trades: canonical-trades builds on the paused release (exit 0), its started rows under
#     that release, its input read as paused with no snapshot, no row added or changed, and
#     k2runs.state `partial: market-capture.kraken-trades.silver_trade`
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down pause-input`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
INPUT=${1:-}
case $INPUT in
  kraken-trades) PLANT=$PLANT_PAUSED ;;
  # ecb-fx-rates) phase 005 (W3-11): ECB's pause plant here, its steps and claim in the case below
  *) echo "usage: $0 kraken-trades <release dir> (the input to pause)" >&2; exit 2 ;;
esac
rp_start pause-input "${2:-}" 4
rp_fail_release "$PLANT" paused
IDP=$IDF RP_MORE=paused IDF=
rp_image
export K2_THROWAWAY_REDPANDA=1
rp_lake
rp_chain_files
rp_chain                                                         # 1 to 3
case $INPUT in
  kraken-trades)
    build "$IDP" "$CT" ct-5; probe chain-state paused
    claim paused "$ID" "$IDP" ca-1 ;;                            # 4, against the chain after ca-1
esac
finish
