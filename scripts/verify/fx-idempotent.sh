#!/usr/bin/env bash
# Plan 002's idempotence (Verification: "the same bytes again: no new row"): on ONE throwaway lake
# (prefix fx-idempotent) from <release>, calendars and ECB's good synthetic file built through
# the dbt wrapper, then the same bytes again in a new run. Each task the release's own code in
# the DAG factory's shape, the spool seeded as fetch.py leaves it (scripts/lib/run-path-lib.sh).
# A real fetch exits 10 (unchanged) on bytes it fetched last, and the run skips load, audit and
# publish; this proves the connector and the build on those bytes anyway, as a run whose spool
# lost its earlier entry would meet them.
#
#   bash scripts/verify/fx-idempotent.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 calendars builds through the wrapper and its audited tag moves
#   2 the good file lands, passes every check and is published to bronze main
#   3 the wrapper builds ECB on calendars' tested snapshot; a reader reads gold
#   4 the same bytes again: the conditional PUT finds them landed, no landing_files row, no row
#     staged, bronze main unmoved; the build passes with the same rows
#   5 the counts before and after are equal: k2-landing objects, landing_files rows (main's
#     snapshot summary), bronze, silver and gold rows
# A killed run leaves the lake to `bash scripts/lib/throwaway-lake.sh down fx-idempotent`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start fx-idempotent "${1:-}" 5
rp_image
rp_lake
rp_calendars
rp_good
rp_build
rp_again
claim counts
finish
