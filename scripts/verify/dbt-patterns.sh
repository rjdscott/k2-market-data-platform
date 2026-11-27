#!/usr/bin/env bash
# W0-8 (phases 004 and 005): three dbt patterns built through the real wrapper (k2-dbt in its
# image, as run-path.sh runs it) on ONE throwaway lake (prefix dbt-patterns), with the synthetic
# fixture product reference-data.pattern-lab (platform/k2gen/tests/fixtures/dbt-patterns/): two
# releases of this tree with it added, R1 as the fixture is, R2 with second/'s seeds over it (the
# listing's third fetch, which changes one tick size; the symbol the held trades need; one trade).
#
#   bash scripts/verify/dbt-patterns.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# <release dir> is the one run-path.sh takes (rp_start's checks); the lake runs on R1, since the
# fixture's builder and rules are in R1 and R2 only, and R2 differs from R1 in its seeds alone.
# Three builds: dp-1 on R1, dp-2 on R2, dp-3 on R2 again (a table's rebuild, what --full-refresh
# would give: the wrapper passes no such flag). Claims, one PASS or FAIL line each, all evaluated:
#   (a) instrument_history, a `table` with window functions: the first version of a key opens at
#       1970-01-01, the open row ends 9999-12-31 23:59:59, intervals abut; dbt data tests for no
#       overlap, no gap, one open row per key pass; R2 adds exactly one closed and one open row;
#       the rebuild gives the same rows; the publish guard passes the multi-statement table build
#   (b) silver_trade (incremental merge) and silver_trade_unresolved (table, the held trades): held
#       rows are not in silver; once the reference has the symbol they move into silver exactly once
#       and leave the held table; both publish in each build
#   (c) the warn test "held rows = 0" fails on dp-1 and the build publishes; then whether the
#       wrapper's XCom or run records say so (what a later step needs to mark the build degraded)
# INFO lines: each output's statements under the build's client tag and which committed a
# snapshot (Trino's query list as the builder), and dbt's per-node times.
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down dbt-patterns`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start dbt-patterns "${1:-}" 11
FX=platform/k2gen/tests/fixtures/dbt-patterns
PL=reference-data.pattern-lab
plant() {  # plant [second]: the python patch that adds the fixture product to a clone (argv[1])
  cat <<PY
import shutil, sys
from pathlib import Path
clone, fx = Path(sys.argv[1]), Path("$REPO/$FX")
shutil.copytree(fx / "pattern-lab", clone / "contracts/reference-data/pattern-lab")
with open(clone / "tests/access/expectations.yaml", "a") as f:
    f.write((fx / "expectations.yaml").read_text())
if "${1:-}":
    shutil.copytree(fx / "second", clone / "contracts/reference-data/pattern-lab", dirs_exist_ok=True)
PY
}
rp_fail_release "$(plant)" r1; R1=$IDF
rp_fail_release "$(plant second)" r2; R2=$IDF
REL=$W/r1/releases/$R1 ID=$R1 IDF=
rp_image
rp_lake
cp -a "$W/r2/releases/$R2" "$STATE/releases/"
dp() { bash "$L" py "$P" scripts/lib/dbt_patterns_probe.py "$@"; }
for b in "$R1 dp-1" "$R2 dp-2" "$R2 dp-3"; do
  set -- $b
  t=$SECONDS
  build "$1" "$PL" "$2"
  echo "$P: $2 took $((SECONDS - t)) s"
  dp state "$2" > "$RP/$2.state.log" 2>&1 || echo "$P: $2 state: $(tail -1 "$RP/$2.state.log")"
done
dp claims dp-1 dp-2 dp-3 2>&1 | grep -E '^(PASS|FAIL|INFO) ' | tee -a "$W/claims"
finish
