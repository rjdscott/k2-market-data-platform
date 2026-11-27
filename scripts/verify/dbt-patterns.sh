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
# would give: the wrapper passes no such flag). Then four on R3, R2 with silver_trade's incremental
# filter dropped so that each merge updates every row and leaves delete files (claim 12, W0-9): dp-4
# the wrapper as it is (under its thresholds: no compaction), dp-5 the wrapper's own code through
# the harness below with its delete-file threshold 1 (the fixture's six rows), dp-6 as it is, dp-7
# through the harness's plant. Claims, one PASS or FAIL line each, all evaluated:
#   (a) instrument_history, a `table` with window functions: the first version of a key opens at
#       1970-01-01, the open row ends 9999-12-31 23:59:59, intervals abut; dbt data tests for no
#       overlap, no gap, one open row per key pass; R2 adds exactly one closed and one open row;
#       the rebuild gives the same rows; the publish guard passes the multi-statement table build
#   (b) silver_trade (incremental merge) and silver_trade_unresolved (table, the held trades): held
#       rows are not in silver; once the reference has the symbol they move into silver exactly once
#       and leave the held table; both publish in each build
#   (c) the warn test "held rows = 0" fails on dp-1 and the build publishes; then whether the
#       wrapper's XCom or run records say so (what a later step needs to mark the build degraded)
#   12  after several merges a build compacts silver_trade: its delete files go to zero, its rows are
#       unchanged, `audited` is main, a `replace` of the tested snapshot; the next build's reconcile
#       changes nothing; an INSERT under the run's own client tag after the optimize is refused:
#       exit 3, `audited` unmoved, main rolled back to the tested snapshot
# INFO lines: each output's statements under the build's client tag and which committed a
# snapshot (Trino's query list as the builder), and dbt's per-node times.
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down dbt-patterns`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start dbt-patterns "${1:-}" 12
FX=platform/k2gen/tests/fixtures/dbt-patterns
PL=reference-data.pattern-lab
plant() {  # plant [second [reselect]]: the python patch that adds the fixture product to a clone (argv[1])
  cat <<PY
import shutil, sys
from pathlib import Path
clone, fx = Path(sys.argv[1]), Path("$REPO/$FX")
shutil.copytree(fx / "pattern-lab", clone / "contracts/reference-data/pattern-lab")
with open(clone / "tests/access/expectations.yaml", "a") as f:
    f.write((fx / "expectations.yaml").read_text())
if "${1:-}":
    shutil.copytree(fx / "second", clone / "contracts/reference-data/pattern-lab", dirs_exist_ok=True)
if "${2:-}":
    m = clone / "contracts/reference-data/pattern-lab/models/silver_trade.sql"
    old = "where cast(t.trade_id as bigint) not in (select trade_id from {{ this }})"
    assert m.read_text().count(old) == 1, "silver_trade.sql's incremental filter is not the one R3 drops"
    m.write_text(m.read_text().replace(old, "where t.trade_id is not null  -- R3: every trade again, so the merge updates"))
PY
}
rp_fail_release "$(plant)" r1; R1=$IDF
rp_fail_release "$(plant second)" r2; R2=$IDF
rp_fail_release "$(plant second reselect)" r3; R3=$IDF
REL=$W/r1/releases/$R1 ID=$R1 IDF=
rp_image
rp_lake
cp -a "$W/r2/releases/$R2" "$W/r3/releases/$R3" "$STATE/releases/"
# The wrapper's own code in its image, as dbt-wrapper.sh's harness runs it, argv[1] the mode: its
# delete-file threshold 1, and for `plant` an INSERT under the run's own client tag right after its
# optimize, the write after the tests the publish guard must refuse.
cat > "$W/harness.py" <<'PY'
import sys
sys.path.insert(0, "/opt/k2/dbt-runner")
import k2_dbt as w
mode, argv = sys.argv[1], sys.argv[2:]
w.COMPACT_DELETE_FILES = 1
if mode == "plant":
    real = w.Live.optimize

    def optimize(self, rel, tag):
        qid = real(self, rel, tag)
        cur = self.connect("builder", [tag]).cursor()
        cur.execute(f"INSERT INTO {w.relation(rel)} SELECT trade_id + 1000, symbol, instrument_id, price, quantity "
                    f"FROM {w.relation(rel)} WHERE trade_id = 1")
        cur.fetchall()
        print(f"dbt-patterns harness: planted {cur.query_id} on {rel} after its optimize", file=sys.stderr, flush=True)
        return qid
    w.Live.optimize = optimize
sys.exit(w.main(argv))
PY
hbuild() {  # hbuild <release id> <product> <run> <mode>: run-path-lib.sh's build, through the harness
  local id=$1 product=$2 run=$3 mode=$4 dag writer publisher cpus memory
  read -r dag writer publisher cpus memory < <(python3 -c '
import json, sys
r, p = sys.argv[1], sys.argv[2]
doc, k = json.load(open(f"{r}/compiled/products/{p}/pipeline.json")), json.load(open(f"{r}/compiled/oneshots.json"))["oneshots"]["dbt-runner"]
print(doc["dagId"], doc["build"]["writer"], doc["build"]["publisher"], k["cpus"], k["memory"])' "$STATE/releases/$id" "$product")
  K2_RELEASE=$id K2_TRINO_USER=$writer K2_PUB_USER=$publisher timeout 900 docker run --rm -i --name "$P-dbt-runner-$dag" \
    --label "k2.throwaway=$P" --pull never --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL \
    --security-opt no-new-privileges --pids-limit 256 --user "$(id -u):$(id -g)" --cpus "$cpus" --memory "$memory" --network "$NET" \
    -e K2_TRINO_HOST -e K2_TRINO_INTERNAL_PORT -e K2_TRINO_USER -e K2_PUB_USER -e K2_RUNS_USER -e K2_CATALOG_URI -e K2_RELEASE \
    --mount "type=bind,source=$STATE/releases/$id,target=/k2/releases/$id,readonly" \
    --mount "type=bind,source=$S/principals/$writer.password,target=/run/k2-secrets/builder.password,readonly" \
    --mount "type=bind,source=$S/principals/$publisher.password,target=/run/k2-secrets/publisher.password,readonly" \
    --mount "type=bind,source=$S/principals/svc.runs.password,target=/run/k2-secrets/runs.password,readonly" \
    --mount "type=bind,source=$S/tls/ca.pem,target=/run/k2-secrets/tls/ca.pem,readonly" \
    "$IMAGE" python - "$mode" build --release "$id" --product "$product" --run-id "$dag/$run" \
    < "$W/harness.py" > "$RP/$run.build.out" 2> "$RP/$run.build.err"
  echo $? > "$RP/$run.build.rc"
  echo "$P: $run build through the harness ($mode, $id) exit $(cat "$RP/$run.build.rc")"
}
dp() { bash "$L" py "$P" scripts/lib/dbt_patterns_probe.py "$@"; }
for b in "$R1 dp-1" "$R2 dp-2" "$R2 dp-3" "$R3 dp-4" "$R3 dp-5 compact" "$R3 dp-6" "$R3 dp-7 plant"; do
  set -- $b
  t=$SECONDS
  if [ -n "${3:-}" ]; then hbuild "$1" "$PL" "$2" "$3"; else build "$1" "$PL" "$2"; fi
  echo "$P: $2 took $((SECONDS - t)) s"
  dp state "$2" > "$RP/$2.state.log" 2>&1 || echo "$P: $2 state: $(tail -1 "$RP/$2.state.log")"
done
dp claims dp-1 dp-2 dp-3 dp-4 dp-5 dp-6 dp-7 2>&1 | grep -E '^(PASS|FAIL|INFO) ' | tee -a "$W/claims"
finish
