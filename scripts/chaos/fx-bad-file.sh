#!/usr/bin/env bash
# Plan 002's bad file (Verification: "bronze main unmoved, bytes kept, the next file waits"): on
# ONE throwaway lake (prefix fx-bad-file) from <release>, ECB's good synthetic file published, then
# the corrupt one (domains/fx-reference/tests/fixtures/ecb-90d-synthetic-corrupt.xml: a negative
# rate and a far-future day). Each task the release's own code in the DAG factory's shape, the
# spool seeded as fetch.py leaves it (scripts/lib/run-path-lib.sh says how and why).
#
#   bash scripts/chaos/fx-bad-file.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 the good file lands, passes every check and is published to bronze main
#   2 the corrupt file lands and fails its audit (rate_positive, no_future_date), bronze main does
#     not move; the next good file (one more day) is refused at load (exit 2) while `audit` holds
#     the failed batch, its bytes landed all the same; the operator's clearing statement
#     (`ALTER TABLE <bronze> REPLACE BRANCH audit AS OF VERSION <main>`) lets its load proceed
#   3 both landing objects, read back from k2-landing, are their spooled raw files byte for byte
# The dbt build is not part of it: no image is built. A killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down fx-bad-file`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start fx-bad-file "${1:-}" 3
rp_lake
rp_good
rp_corrupt
for run in rp-corrupt rp-third; do  # each object as the load result names it, its bytes to the run's files
  key=$(python3 -c 'import json, sys; print(json.loads([x for x in open(sys.argv[1]) if x.strip()][-1])["objectKey"].removeprefix("s3://"))' \
    "$RP/$run.load.out" 2>/dev/null)
  [ -z "$key" ] || docker exec "$P-minio" mc cat "local/$key" > "$RP/landed-$run.bin"
done
claim bytes
finish
