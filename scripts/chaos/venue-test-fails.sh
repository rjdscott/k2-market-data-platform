#!/usr/bin/env bash
# W2-16, plan 004's exit "a failed venue test leaves the derived products unchanged" (Verification:
# "venue-test-fails.sh: canonical-trades and candles unchanged"): on ONE throwaway lake with a
# Redpanda (prefix venue-test-fails, K2_THROWAWAY_REDPANDA=1) from <release>, the phase 004 chain's
# first cycle, then one more BTC/USD trade built on a release of this tree in which silver_trade gives
# a new row twice (run-path-lib.sh's PLANT_VENUE_FAIL; its k2_unique_key test catches it, as no frame
# can fail one of silver_trade's data tests: run-path.sh claim 17 says why), then canonical-trades and
# candles on the release under test. run-path.sh claim 17's case, after the first cycle. Each task the
# release's own, in the DAG factory's shape (scripts/lib/run-path-lib.sh); frames synthetic.
#
#   bash scripts/chaos/venue-test-fails.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 to 3 the chain's first cycle: kraken-instruments, security-master, kraken-trades with
#     canonical-trades and candles (run-path.sh claims 10 to 12)
#   4 kt-5 lands, audits and publishes the frame to bronze; its build fails its tests (exit 1) and rolls
#     back, silver_trade and the held table on their audited snapshot, the trade not in silver;
#     canonical-trades and candles then build and change no row
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down venue-test-fails`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start venue-test-fails "${1:-}" 4
rp_fail_release "$PLANT_VENUE_FAIL" venuefail
IDV=$IDF RP_MORE=venuefail IDF=
rp_image
export K2_THROWAWAY_REDPANDA=1
rp_lake
rp_chain_files
rp_chain                                                         # 1 to 3
kseed "$RP/more.jsonl"; trades kt-5 "$IDV"; probe chain-state kt-5
build "$ID" "$CT" ct-6; build "$ID" "$CA" ca-6; probe chain-state failed
claim venue-fail "$ID" "$IDV" ca-1                               # 4, against the chain after ca-1
finish
