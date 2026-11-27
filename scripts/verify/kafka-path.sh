#!/usr/bin/env bash
# W0-4a, W0-4b and W0-4c (phases 004 and 005): the Kafka connector's load, audit and publish on ONE
# throwaway lake with a Redpanda (prefix kafka-path, K2_THROWAWAY_REDPANDA=1), each task the
# release's own docker/lake/connector.py in the shape the DAG factory gives it (run-path-lib.sh's
# WRAPPER, by docker exec into one long-lived Spark container), on the synthetic fixture product
# market-capture.kafka-fixture (platform/k2gen/tests/fixtures/kafka-fixture/) in a release of this
# tree compiled here. Its pipeline.json is k2gen's own (W1-2's emitter); the connector reads
# `connector`, `landing`, `bronze[0].table` and `checks` of it. Frames: scripts/lib/kafka_seed.py's,
# from services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl (1185 frames, 3 trades),
# and its 20 heartbeat lines alone for a batch of control frames.
#
#   bash scripts/verify/kafka-path.sh <release dir> [a|b|c]   # KEEP=1 keeps the scratch directory
#
# <release dir> is the one run-path.sh takes (rp_start's checks); the lake runs on the fixture's
# release. A kill is the driver SIGKILLed inside the task by scripts/lib/kafka_path_probe.py's
# `kill` wrapper (the only reader of that switch): at SparkLanding.land (before the landing
# commit) or at kafka.stage (after it). Every step runs, then every claim is evaluated, one PASS
# or FAIL line each:
# group a
#   1 one load lands every seeded frame (each partition contiguous) and stages exactly the trades,
#     staged + skipped = landed; the landing commit's k2.* keys are the frozen ones, its end offsets
#     the topic's
#   2 audit writes three passed rows; publish moves main by the compare-and-set commit; bronze main
#     = the trade frames
#   3 a second run with no new frames adds no landing row and no bronze row, and commits nothing
#   4 more frames, a load killed after its landing commit: the next run stages that range and the
#     new one: every trade once, no hole, through-offsets = landing end offsets
#   5 a load killed before its landing commit leaves the landing unchanged; the next lands once
#   6 a retry under the same run id after a landing commit stages the new range
#   7 the writes used Spark's own storage key (its variable's name, never its value) and warehouse k2
#   8 a batch of control frames only: what the empty append commits (added-records), and a
#     partition with no new records read at start = end with failOnDataLoss true
#   9 a corrupt Avro body (scripts/lib/kafka_path_probe.py corrupt) fails parses naming its
#     partition and offset; audit and publish exit 3, main not moved
#  10 bronze carries the decoded envelope (I-1, 2026-11-28): its columns the contract's, every row
#     stream = trade, schema_id the registered id, payload the seeded line's bytes byte for byte,
#     symbol as the adapter's rule has it; the corrupt frame staged with every envelope column null
#  11 a run killed between stage and audit (k10: its load alone, as the run's timeout leaves it) is
#     healed by the next scheduled run (k11) with no operator action: k10's batch never audited,
#     k11 restages its range with the new one, audits and publishes; every trade once, no hole
#  12 a batch that failed its audit (k9's) is never reset: the next load (k12) exits 2 naming the
#     failed check and the statement that clears it, audit and main unmoved
#  (9 and 10 compare k9 with k11, the run before the corrupt frame)
# group b
#   1 a start below the log start (records deleted by `rpk topic trim-prefix` past the committed end
#     offsets, on every partition that has them) fails loudly naming each partition; nothing lands
#   2 with --accept-data-loss the run lands from the log start, records the gap in k2.kafka-gaps
#     and returns it as offsetGaps (what the run's offset_gap record is written from)
#   3 the audit's accounting passes with the gap excluded, and publish moves main
#   4 after one further clean run the newest landing commit still carries the gap (cumulative)
#   5 bronze main still carries the decoded envelope as seeded (a's claim 10's rule)
#   6 the topic trimmed BEFORE the product's first run (b1; every partition of two records or more
#     halfway): b1 lands from each log start, every offset once, and its audit and publish pass
#     (claims 1 to 5 build on that lake: the landing starts at those log starts, not at 0)
# group c (W0-4c), each maintain pass maintain.py exactly as the factory's task runs it; the
# thresholds met by seeding small files (COMPACT_MIN_FILES has no override): `kafka_path_probe.py
# cycles`, 50 runs of three trade frames in one Spark application, kafka.py's own tasks
#   1 compaction is skipped, its reason in the result, while audit holds an unpublished batch; that
#     batch then publishes (it could not, had main moved under it)
#   2 maintain.py --compact: one replace snapshot on the landing and on bronze, k2.job maintenance,
#     no k2.kafka-offsets; rows unchanged, fewer live files
#   3 the default pass: continuity passes; expiry and remove_orphan_files (24-hour floor, over a
#     listing made with the maintenance key) run, and no live file is deleted
#   4 the same expiry step at now + 30 days (`kafka_path_probe.py expire`), all past retention:
#     the newest connector snapshot of each table and the chain to it survive, older ones go
#   5 the next run resumes from that snapshot's offsets: no re-land, no restage, no hole
#   6 the continuity check passes after it
# Spark task wall times go to <rp>/times and are printed as INFO lines.
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down kafka-path`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
GROUP=${2:-a}
case $GROUP in a) N=12 ;; b) N=6 ;; c) N=6 ;; *) echo "kafka-path: no group $GROUP (a, b or c)" >&2; exit 2 ;; esac
rp_start kafka-path "${1:-}" "$N"
FX=platform/k2gen/tests/fixtures/kafka-fixture
KF=market-capture.kafka-fixture DAG=kafka_fixture
PROBE=scripts/lib/kafka_path_probe.py
rp_fail_release "$(cat <<PY
import shutil, sys
from pathlib import Path
clone, fx = Path(sys.argv[1]), Path("$REPO/$FX")
shutil.copytree(fx / "kafka-fixture", clone / "contracts/market-capture/kafka-fixture")
with open(clone / "tests/access/expectations.yaml", "a") as f:
    f.write((fx / "expectations.yaml").read_text())
PY
)" kf
REL=$W/kf/releases/$IDF ID=$IDF IDF=
export K2_THROWAWAY_REDPANDA=1
rp_lake
FULL=$REPO/services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl
grep -F '\"channel\":\"heartbeat\"' "$FULL" > "$W/heartbeats.jsonl"  # derived at run time from the synthetic fixture, never committed

seed() { bash "$L" seed "$P" "$1" >> "$RP/seed.log" 2>&1 || die "seed failed: $(tail -3 "$RP/seed.log")"; tail -1 "$RP/seed.log"; }
ktask() {  # ktask <run> <load|audit|publish> [args]: factory.SparkTask's argv for the fixture; TRY=n labels a rerun
  local run=$1 t=$2 label=$1.$2${TRY:+.$TRY} t0; shift 2
  t0=$(date +%s.%N)
  docker exec "$SPARK" sh -c "$WRAPPER" "/tmp/k2-airflow-$label.pid" "/k2/releases/$ID/platform/docker/lake/connector.py" \
    "$t" --release "$ID" --product "$KF" --run-id "$DAG/$run" "$@" > "$RP/$label.out" 2> "$RP/$label.err"
  echo $? > "$RP/$label.rc"
  echo "$label $(cat "$RP/$label.rc") $(awk "BEGIN {printf \"%.1f\", $(date +%s.%N) - $t0}")" >> "$RP/times"
  echo "$P: $label exit $(cat "$RP/$label.rc")"
}
run() { for t in load audit publish; do ktask "$1" "$t"; done; }
kkill() {  # kkill <before|after> <run>: the run's load, its driver killed before or after the landing commit
  docker exec -i "$SPARK" python3 - kill "$1" "/k2/releases/$ID" load --release "$ID" --product "$KF" \
    --run-id "$DAG/$2" < "$PROBE" > "$RP/$2.kill.out" 2> "$RP/$2.kill.err"
  echo $? > "$RP/$2.kill.rc"
  echo "$P: $2 load killed $1 its landing commit, exit $(cat "$RP/$2.kill.rc")"
}
facts() {
  docker exec -i "$SPARK" python3 - facts "/k2/releases/$ID" "$KF" < "$PROBE" 2> "$RP/facts-$1.err" | tail -1 > "$RP/facts-$1.json"
  [ -s "$RP/facts-$1.json" ] || echo "$P: facts $1 failed: $(grep -v '^\s' "$RP/facts-$1.err" | tail -2)"
}

claims() {
  for c in "$@"; do
    out=$(python3 "$PROBE" claim "$c" "$RP" 2>&1)
    printf '%s\n' "$out" | grep -E '^(PASS|FAIL|INFO) ' | tee -a "$W/claims"
    grep -qE '^(PASS|FAIL) ' <<< "$out" || echo "FAIL $c: the claim did not evaluate: $(tail -1 <<< "$out")" | tee -a "$W/claims"
  done
}
timings() { while read -r label code secs; do echo "INFO time $label exit $code ${secs} s"; done < "$RP/times" | tee -a "$W/claims"; }

mtask() {  # mtask <run> <maintain|compact>: factory.SparkTask's maintain argv for the fixture
  local run=$1 t=$2 t0 flag=; shift 2
  [ "$t" = compact ] && flag=--compact
  t0=$(date +%s.%N)
  docker exec "$SPARK" sh -c "$WRAPPER" "/tmp/k2-airflow-$run.$t.pid" "/k2/releases/$ID/platform/docker/lake/maintain.py" \
    $flag --release "$ID" --product "$KF" --run-id "$DAG/$run" > "$RP/$run.$t.out" 2> "$RP/$run.$t.err"
  echo $? > "$RP/$run.$t.rc"
  echo "$run.$t $(cat "$RP/$run.$t.rc") $(awk "BEGIN {printf \"%.1f\", $(date +%s.%N) - $t0}")" >> "$RP/times"
  echo "$P: $run.$t exit $(cat "$RP/$run.$t.rc")"
}

if [ "$GROUP" = c ]; then
  seed "$FULL"; run c1
  seed "$FULL"; run c2
  t0=$(date +%s)
  docker exec -i "$SPARK" python3 - cycles "/k2/releases/$ID" "$KF" "$DAG" 50 < "$PROBE" > "$RP/cycles.log" 2>&1 \
    || echo "$P: the cycles failed: $(grep -v '^\s' "$RP/cycles.log" | tail -2)"
  echo "cycles $? $(( $(date +%s) - t0 ))" >> "$RP/times"; tail -1 "$RP/cycles.log"
  seed "$FULL"; ktask c3 load; ktask c3 audit; facts c3
  mtask c3 compact; facts c3s
  ktask c3 publish; facts c3p
  mtask c4 compact; facts c4
  mtask c5 maintain; facts c5
  docker exec -i "$SPARK" python3 - expire "/k2/releases/$ID" "$KF" 30 < "$PROBE" > "$RP/c6.expire.out" 2> "$RP/c6.expire.err"
  echo $? > "$RP/c6.expire.rc"; facts c6
  seed "$FULL"; run c7; facts c7
  mtask c8 maintain
  timings; claims compact-skip compact maintain expire resume continuity
  finish
fi

if [ "$GROUP" = b ]; then
  TOPIC=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["connector"]["topic"])' \
    "$STATE/releases/$ID/compiled/products/$KF/pipeline.json")
  trim() {  # trim <json {partition: new log start}>
    python3 -c 'import json, sys; [print(p, o) for p, o in json.load(open(sys.argv[1])).items()]' "$1" |
      while read -r p o; do docker exec "$P-redpanda" rpk topic trim-prefix "$TOPIC" -p "$p" -o "$o" --no-confirm >> "$RP/trim.log" 2>&1; done
    echo "$P: trimmed $(cat "$1") (partition: new log start)"
  }
  seed "$FULL"
  # every partition with two records or more trimmed halfway BEFORE the product's first run: its log start above 0
  docker exec "$P-redpanda" rpk topic describe "$TOPIC" -p > "$RP/describe0.txt"
  python3 - "$RP/describe0.txt" > "$RP/trims0.json" <<'PY0'
import json, sys
hw = {x.split()[0]: int(x.split()[-1]) for x in open(sys.argv[1]) if x.split() and x.split()[0].isdigit()}
print(json.dumps({p: h // 2 for p, h in hw.items() if h >= 2}))
PY0
  trim "$RP/trims0.json"
  run b1; facts b1
  seed "$FULL"
  # every partition with records past b1's end offsets trimmed halfway into them: a hole after the resume point
  docker exec "$P-redpanda" rpk topic describe "$TOPIC" -p > "$RP/describe.txt"
  python3 - "$RP/facts-b1.json" "$RP/describe.txt" > "$RP/trims.json" <<'PY2'
import json, sys
f = json.load(open(sys.argv[1]))
[ends] = json.loads(next(s for s in f["landing"]["snapshots"] if s[4].get("k2.job") == "connector")[4]["k2.kafka-offsets"]).values()
hw = {x.split()[0]: int(x.split()[-1]) for x in open(sys.argv[2]) if x.split() and x.split()[0].isdigit()}
print(json.dumps({p: ends.get(p, 0) + (h - ends.get(p, 0)) // 2 for p, h in hw.items() if h - ends.get(p, 0) >= 2}))
PY2
  trim "$RP/trims.json"
  ktask b3 load; facts b3
  ktask b4 load --accept-data-loss; ktask b4 audit; ktask b4 publish; facts b4
  seed "$FULL"; run b5; facts b5
  timings; claims below accepted gap-audit carried envelope-b first-trimmed
  finish
fi

seed "$FULL"; run k1; facts k1                                 # 1, 2
run k2; facts k2                                               # 3
seed "$W/heartbeats.jsonl"; run k3; facts k3                   # 8
seed "$FULL"; kkill after k4; facts k4                         # 4
seed "$FULL"; run k5; facts k5
seed "$FULL"; kkill before k6; facts k6                        # 5
run k7; facts k7
seed "$FULL"; kkill after k8; TRY=2 ktask k8 load; ktask k8 audit; ktask k8 publish; facts k8   # 6, 7
seed "$FULL"; ktask k10 load; facts k10                        # 11: its run ended after the stage
seed "$FULL"; run k11; facts k11
docker exec -i "$SPARK" python3 - corrupt "/k2/releases/$ID" "$KF" < "$PROBE" > "$RP/corrupt.log" 2>&1 \
  || echo "$P: the corrupt frame was not produced: $(tail -2 "$RP/corrupt.log")"
run k9; facts k9                                               # 9
ktask k12 load; facts k12                                      # 12

timings; claims land publish idle kill-after kill-before retry key empty corrupt envelope heal blocked
finish
