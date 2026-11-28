# shellcheck shell=bash
# The throwaway run path, shared by scripts/verify/run-path.sh (spine-e2e's stage run), plan
# 002's verification scripts (scripts/verify/fx-idempotent.sh, scripts/chaos/fx-bad-file.sh,
# dbt-failing-test.sh, trino-stop-mid-build.sh) and plan 004's chaos scripts (scripts/chaos/README.md).
# Sourced, never executed: each script calls rp_start, then the setup it needs, then its steps,
# then finish.
#
# ONE throwaway lake (scripts/lib/throwaway-lake.sh, the script's own prefix), and every task run
# from the release's own code (<release>/platform/) in the shape docker/airflow/dags/factory.py
# gives it:
#
#   task    a Spark task, `python3 /k2/releases/<id>/platform/docker/lake/<connector|expire|maintain>.py
#           ...` through factory.WRAPPER, by `docker exec` into one long-lived Spark container:
#           compose's spark-iceberg image and environment names, Spark's key and the maintenance
#           key, the releases root and the spool read-only
#   build   `k2-dbt build --release <id> --product <p> --run-id <dag>/<run>` in the dbt-runner image
#           built from this tree (rp_image), with oneshot.py's flags and the release's
#           compiled/oneshots.json limits, the product's builder and publisher files mounted
#   seed    (scripts/lib/run_path_probe.py) the spool entry fetch.py leaves after its download
#           (raw.<ext>, fetch.json with the bytes' sha256 and size), then fetch.parse() itself, which
#           runs the release's parser and its declared audits. The fetch is not run: a local
#           fixture server sits on a private address the fetch must refuse (make test-connectors
#           proves the fetch).
#
# One container job: rp_start refuses while a container that is not the stack's runs (the
# rehearsal's k2-rehearsal-* included), or under 10 GiB available. The lake, the image and the
# scratch directory go in the trap; a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down <prefix>` and the image to `docker rmi dbt-runner:<prefix>`.
# Never touches the running stack, never prints a secret (the keys and passwords are the lake's,
# passed by name or file).
RP_LIB=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$RP_LIB/../.." || exit 2
REPO=$PWD
L=scripts/lib/throwaway-lake.sh
ECB=fx-reference.ecb-fx-rates
CAL=reference-data.calendars
FIX=domains/fx-reference/tests/fixtures
IDF=
STATE=
RP_N=0

rp_start() {  # rp_start <prefix> <release dir> <claims>: the guards, the scratch directory and its trap
  P=$1 NET=$1-net SPARK=$1-spark-iceberg IMAGE=dbt-runner:$1 RP_CLAIMS=$3
  REL=$(cd "${2:?usage: $0 <release dir>}" && pwd) || exit 2
  if [ ! -f "$REL/manifest.json" ] || [ ! -d "$REL/platform/docker/lake" ]; then echo "$P: $REL is not a release with run code" >&2; exit 2; fi
  ID=$(basename "$REL")
  local avail others
  avail=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
  [ "$avail" -ge 10 ] || { echo "$P: MemAvailable ${avail} GiB, under 10 GiB: not starting" >&2; exit 2; }
  docker info >/dev/null 2>&1 || { echo "$P: no Docker" >&2; exit 2; }
  others=$(docker ps --format '{{.Names}}' | grep -E -v '^k2-' ; docker ps --format '{{.Names}}' | grep '^k2-rehearsal' || true)  # the rehearsal's names start k2- too
  [ -z "$others" ] || { echo "$P: another container job is running ($others): not starting" >&2; exit 2; }
  W=$(mktemp -d "$HOME/.k2-$P.XXXXXX")  # under $HOME, the one place Docker Desktop shares
  trap rp_cleanup EXIT
  T0=$SECONDS
}

rp_cleanup() {
  [ "${KEEP:-}" = 1 ] && [ -n "$STATE" ] && cp -a "$STATE/rp" "$W/" 2>/dev/null
  bash "$L" down "$P" >/dev/null 2>&1
  docker rmi -f "$IMAGE" >/dev/null 2>&1
  if [ "${KEEP:-}" = 1 ]; then echo "$P: scratch kept in $W"; else chmod -R u+w "$W"; rm -rf "$W"; fi
}

die() { echo "$P: $*" >&2; exit 1; }

finish() {  # finish [why]: the summary line; exit 0 only when every claim passed
  local pass fail
  pass=$(grep -c '^PASS' "$W/claims" 2>/dev/null); fail=$(grep -c '^FAIL' "$W/claims" 2>/dev/null)
  [ -z "${1:-}" ] || echo "$P: $1"
  echo "$P: ${pass:-0} PASS, ${fail:-0} FAIL in $((SECONDS - T0)) s"
  [ "${fail:-0}" = 0 ] && [ "${pass:-0}" = "$RP_CLAIMS" ]
  exit $?
}

rp_fail_release() {  # rp_fail_release <python patch> [name]: a release of this tree with the patch applied (argv[1] the clone), under $W/<name> (default fail); sets IDF
  local d=$W/${2:-fail}
  mkdir -p "$d/clone"
  git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$d/clone"
  python3 - "$d/clone" <<< "$1" || die "the plant no longer applies to this tree (its assert above)"
  python3 - docker-compose.yml > "$W/images.json" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "run-path/placeholder@sha256:" + "0" * 64 for n in names}))
PY
  local git=(git -C "$d/clone" -c user.name=run-path -c user.email=run-path@example.org)
  { "${git[@]}" init -q -b main && "${git[@]}" add -A && "${git[@]}" commit -q -m "$P: a failing test" &&
    "${git[@]}" tag -a v0.0.0-runpath.1 -m "$P"; } || die "the failing tree did not commit"
  local k2gen=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
  "${k2gen[@]}" docset --tag v0.0.0-runpath.1 --repo "$d/clone" --out "$d/set" > "$W/compile.log" 2>&1 &&
    "${k2gen[@]}" release --set "$d/set" --previous none --images "$W/images.json" --out "$d/releases" 2>>"$W/compile.log" \
    | tail -1 > "$d/id"
  IDF=$(cat "$d/id")
  if [ -z "$IDF" ] || [ ! -d "$d/releases/$IDF" ]; then tail -20 "$W/compile.log" >&2; die "the failing release did not compile"; fi
}

rp_image() {  # the dbt-runner image, from this tree
  docker build -q -f docker/dbt-runner/Dockerfile -t "$IMAGE" . > "$W/build.log" 2>&1 \
    || { tail -20 "$W/build.log" >&2; die "the dbt-runner image did not build"; }
}

rp_lake() {  # the lake from the release (and the failing one beside it), the run's files, the Spark container
  bash "$L" up "$P" "$REL" > "$W/up.log" 2>&1 || { tail -20 "$W/up.log" >&2; die "the throwaway lake did not start"; }
  STATE=$(bash "$L" state "$P")
  S=$STATE/secrets/k2
  RP=$STATE/rp  # /work/rp in the tools container: the fixtures, fetch.py, every task's output and the probe's state
  [ -z "$IDF" ] || cp -a "$W/fail/releases/$IDF" "$STATE/releases/"
  for x in ${RP_MORE:-}; do cp -a "$W/$x/releases/$(cat "$W/$x/id")" "$STATE/releases/"; done  # rp_fail_release's other names
  mkdir -p "$RP" "$STATE/spool"
  cp docker/fetch/fetch.py "$RP/fetch.py"  # the fetch image's program (docker/fetch/Dockerfile copies this file)
  cp "$FIX/ecb-90d-synthetic.xml" "$RP/good.xml"
  cp "$FIX/ecb-90d-synthetic-corrupt.xml" "$RP/corrupt.xml"
  python3 - "$RP/good.xml" "$RP/third.xml" <<'PY' || die "the third batch could not be made"
# The third batch: the good fixture and one more synthetic day, the day after its newest, with that
# day's rates (ECB's file lists the newest day first).
import re, sys
from datetime import date, timedelta
text = open(sys.argv[1]).read()
newest = max(re.findall(r'<Cube time="(\d{4}-\d\d-\d\d)">', text))
day = re.search(rf'[ \t]*<Cube time="{newest}">.*?</Cube>\n', text, re.S)
extra = day.group(0).replace(newest, (date.fromisoformat(newest) + timedelta(days=1)).isoformat())
open(sys.argv[2], "w").write(text[:day.start()] + extra + text[day.start():])
PY
  probe setup "$ID" > "$W/setup.log" 2>&1 || { tail -5 "$W/setup.log" >&2; die "the namespaces or the run-record table could not be made"; }
  # compose's service spark-iceberg: its image, its environment names (Spark's pair also under the AWS
  # names, the region), the releases root and the spool read-only; one long-lived container, as the stack's.
  set -a
  # shellcheck source=/dev/null
  . "$STATE/secrets/s3.env"
  set +a
  export K2_S3_REGION=local-01 AWS_ACCESS_KEY_ID=$K2_SPARK_S3_ACCESS_KEY AWS_SECRET_ACCESS_KEY=$K2_SPARK_S3_SECRET_KEY AWS_REGION=us-east-1
  docker run -d --name "$SPARK" --label "k2.throwaway=$P" --network "$NET" --cpus 2 --memory 3g \
    -e K2_SPARK_S3_ACCESS_KEY -e K2_SPARK_S3_SECRET_KEY -e K2_MAINTENANCE_S3_ACCESS_KEY -e K2_MAINTENANCE_S3_SECRET_KEY \
    -e K2_S3_ENDPOINT -e K2_S3_REGION -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION \
    -v "$STATE/releases:/k2/releases:ro" -v "$STATE/spool:/spool:ro" \
    "$(grep -oE 'image: *k2-spark-iceberg:[^ ]*' docker-compose.yml | head -1 | sed 's/image: *//')" sleep infinity > /dev/null \
    || die "the Spark container did not start"
  echo "$P: release $ID${IDF:+ (the failing one $IDF)}; lake and Spark up in $((SECONDS - T0)) s"
}

# shellcheck disable=SC2016  # factory.WRAPPER, verbatim: $@ and $0 are the shell's in the container
WRAPPER='python3 "$@" & echo $! > "$0"; wait $!; rc=$?; rm -f "$0"; exit $rc'
task() {  # task <release id> <run> <load|audit|publish|expire|maintain> [args]: factory.SparkTask's argv; TRY=n labels a rerun, TP=<product> runs another product than ECB
  local id=$1 run=$2 t=$3 label=$2.$3${TRY:+.$TRY} product=${TP:-$ECB} dag t0; shift 3
  dag=${product#*.} t0=$(date +%s.%N)
  local argv=("/k2/releases/$id/platform/docker/lake/connector.py" "$t")
  case $t in expire|maintain) argv=("/k2/releases/$id/platform/docker/lake/$t.py") ;; esac
  docker exec "$SPARK" sh -c "$WRAPPER" "/tmp/k2-airflow-$label.pid" "${argv[@]}" --release "$id" --product "$product" \
    --run-id "${dag//-/_}/$run" "$@" > "$RP/$label.out" 2> "$RP/$label.err"
  echo $? > "$RP/$label.rc"
  timed "$label" "$t0"
  echo "$P: $label exit $(cat "$RP/$label.rc")"
}
timed() {  # timed <label> <start>: the task's wall time in $RP/times, "<label> <exit> <seconds>"
  echo "$1 $(cat "$RP/$1.rc") $(awk "BEGIN {printf \"%.1f\", $(date +%s.%N) - $2}")" >> "$RP/times"
}
export K2_TRINO_HOST=trino K2_TRINO_INTERNAL_PORT=8443 K2_RUNS_USER=svc.runs K2_CATALOG_URI=http://lakekeeper:8181/catalog
build() {  # build <release id> <product> <run>: k2-dbt build as factory.PinnedOneShot and oneshot.py run it
  local id=$1 product=$2 run=$3 dag writer publisher cpus memory t0
  t0=$(date +%s.%N)
  read -r dag writer publisher cpus memory < <(python3 -c '
import json, sys
r, p = sys.argv[1], sys.argv[2]
doc, k = json.load(open(f"{r}/compiled/products/{p}/pipeline.json")), json.load(open(f"{r}/compiled/oneshots.json"))["oneshots"]["dbt-runner"]
print(doc["dagId"], doc["build"]["writer"], doc["build"]["publisher"], k["cpus"], k["memory"])' "$STATE/releases/$id" "$product")
  K2_RELEASE=$id K2_TRINO_USER=$writer K2_PUB_USER=$publisher timeout 900 docker run --rm --name "$P-dbt-runner-$dag" \
    --label "k2.throwaway=$P" --pull never --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL \
    --security-opt no-new-privileges --pids-limit 256 --user "$(id -u):$(id -g)" --cpus "$cpus" --memory "$memory" --network "$NET" \
    -e K2_TRINO_HOST -e K2_TRINO_INTERNAL_PORT -e K2_TRINO_USER -e K2_PUB_USER -e K2_RUNS_USER -e K2_CATALOG_URI -e K2_RELEASE \
    --mount "type=bind,source=$STATE/releases/$id,target=/k2/releases/$id,readonly" \
    --mount "type=bind,source=$S/principals/$writer.password,target=/run/k2-secrets/builder.password,readonly" \
    --mount "type=bind,source=$S/principals/$publisher.password,target=/run/k2-secrets/publisher.password,readonly" \
    --mount "type=bind,source=$S/principals/svc.runs.password,target=/run/k2-secrets/runs.password,readonly" \
    --mount "type=bind,source=$S/tls/ca.pem,target=/run/k2-secrets/tls/ca.pem,readonly" \
    "$IMAGE" k2-dbt build --release "$id" --product "$product" --run-id "$dag/$run" > "$RP/$run.build.out" 2> "$RP/$run.build.err"
  echo $? > "$RP/$run.build.rc"
  timed "$run.build" "$t0"
  echo "$P: $run build ($product, $id) exit $(cat "$RP/$run.build.rc")"
}
spark_py() {  # spark_py <facts|clear> <label>: scripts/lib/run_path_spark.py in the Spark container, on the release; TP=<product> for another than ECB
  docker exec -i "$SPARK" python3 - "/k2/releases/$ID" "${TP:-$ECB}" "$1" < scripts/lib/run_path_spark.py > "$RP/$2.out" 2> "$RP/$2.err"
  echo $? > "$RP/$2.rc"
}
objects() { docker exec "$P-minio" mc ls -r --json local/k2-landing > "$RP/objects-$1.json"; }
probe() { bash "$L" py "$P" scripts/lib/run_path_probe.py "$@"; }
claim() {  # claim <name> [args]: the probe's one line, numbered; a FAIL stops the run
  local out code
  out=$(probe claim "$@" 2>&1)
  code=$?
  claimed "$1" "$out" $code
}
claimed() {  # claimed <name> <output> <exit>: the output's PASS, FAIL and INFO lines, numbered; a FAIL stops the run
  RP_N=$((RP_N + 1))
  printf '%s\n' "$2" | grep -E '^(PASS|FAIL|INFO) ' | sed -E "s/^(PASS|FAIL) /\1 $RP_N /" | tee -a "$W/claims"
  if [ "$3" != 0 ]; then
    grep -qE '^FAIL ' <<< "$2" || echo "FAIL $RP_N $1: the probe failed: $(tail -1 <<< "$2")" | tee -a "$W/claims"
    finish "stopped at claim $RP_N ($1): the claims after it build on the lake it left"
  fi
}

# The phase 004 chain's tasks (scripts/verify/run-path.sh claims 10 to 17, and the chaos scripts
# W2-12 to W2-17): the lake started with K2_THROWAWAY_REDPANDA=1.
KI=market-capture.kraken-instruments SM=reference-data.security-master KT=market-capture.kraken-trades
CT=market-analytics.canonical-trades CA=market-analytics.candles
kseed() {  # kseed <frames.jsonl>: frames onto Kraken's raw topic as capture frames them (throwaway-lake.sh seed)
  bash "$L" seed "$P" "$1" >> "$RP/seed.log" 2>&1 || die "the frames of $1 were not produced: $(tail -3 "$RP/seed.log")"
  echo "$P: $(tail -1 "$RP/seed.log")"
}
listing() {  # listing <run> <response under $RP>: the public-API spool as fetch leaves it, then load, audit, publish
  probe seed-api "$ID" "$KI" "$2" "$1" > "$RP/$1.seed.log" 2>&1 || die "the spool of $1 was not written: $(tail -3 "$RP/$1.seed.log")"
  if grep -q '"exit": 10' "$RP/$1.seed.json"; then  # factory: an unchanged fetch (exit 10) skips load, audit and publish; build runs
    echo "$P: $1 fetch unchanged (exit 10): load, audit and publish skipped"
  else
    for t in load audit publish; do TP=$KI task "$ID" "$1" "$t"; done
  fi
}
rp_chain_files() {  # the chain's frames and listings under $RP: the two synthetic fixtures, and what is made from them here
  cp services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl "$RP/frames.jsonl"
  cp domains/market-capture/tests/fixtures/asset-pairs-synthetic.json "$RP/listing-1.json"
  python3 - "$RP" <<'PY' || die "the chain's frames and listings could not be made"
# Synthetic, made at run time from the two fixtures and never committed: the listing again with one
# more invented pair, DELTA/USD; a trade frame of that symbol, which the first listing lacks; one
# more BTC/USD trade frame. Each frame line is the fixture's, {"recv_ts_ns", "payload"}, after the
# fixture's last frame.
import json, sys
from pathlib import Path
rp = Path(sys.argv[1])
doc = json.loads((rp / "listing-1.json").read_text())
doc["result"]["DELTAUSD"] = dict(doc["result"]["ALPHAUSD"], altname="DELTAUSD", wsname="DELTA/USD", base="DELTA")
(rp / "listing-2.json").write_text(json.dumps(doc, indent=2) + "\n")
def frame(path, at_s, trade):
    payload = json.dumps({"channel": "trade", "type": "update", "data": [trade]}, separators=(",", ":"))
    (rp / path).write_text(json.dumps({"recv_ts_ns": 1_000_000_000_000_000_000 + at_s * 1_000_000_000, "payload": payload}) + "\n")
frame("unknown.jsonl", 30, {"symbol": "DELTA/USD", "side": "buy", "price": 10.5, "qty": 2.0, "ord_type": "limit",
                            "trade_id": 1, "timestamp": "2001-09-09T01:47:10.000000Z"})
frame("more.jsonl", 40, {"symbol": "BTC/USD", "side": "sell", "price": 1000.0, "qty": 0.5, "ord_type": "market",
                        "trade_id": 4, "timestamp": "2001-09-09T01:47:20.000000Z"})
PY
}
rp_times() {  # INFO lines: each chain task's wall time, and one five-minute cycle's sum (kraken-trades' tasks, canonical-trades, candles)
  local f=$RP/times
  grep -E '^(ki|sm|kt|ct|ca)-' "$f" | while read -r label code secs; do echo "INFO time $label exit $code ${secs} s"; done | tee -a "$W/claims"
  for c in 1 4; do
    awk -v c="$c" '$1 ~ "^(kt|ct|ca)-" c "\\." {n++; s += $3} END {printf "INFO time one five-minute cycle (kt-%s, ct-%s, ca-%s): %d tasks, %.1f s\n", c, c, c, n, s}' "$f"
  done | tee -a "$W/claims"
}
trades() {  # trades <run> [release]: kraken-trades' DAG, load, audit, publish, then the build
  local t
  for t in load audit publish; do TP=$KT task "${2:-$ID}" "$1" "$t"; done
  build "${2:-$ID}" "$KT" "$1"
}
rp_chain() {  # the chain's first cycle, each product ending in its claim (run-path.sh claims 10 to 12)
  listing ki-1 listing-1.json; build "$ID" "$KI" ki-1; probe chain-state ki-1
  claim instruments "$ID"
  # security-master reads currency-master too, built on its empty bronze (on run-path.sh's lake claim 9
  # built it already, and this build adds nothing)
  build "$ID" reference-data.currency-master cm-1
  build "$ID" "$SM" sm-1; probe chain-state sm-1
  claim master "$ID"
  kseed "$RP/frames.jsonl"; trades kt-1; probe chain-state kt-1
  build "$ID" "$CT" ct-1; build "$ID" "$CA" ca-1; probe chain-state ca-1
  claim trades "$ID"
}
# rp_fail_release's two plants on kraken-trades (run-path.sh claims 16 and 17, pause-input.sh, venue-test-fails.sh)
PLANT_PAUSED=$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/market-capture/kraken-trades/product.odps.yaml"
doc = p.read_text()
assert doc.count("\nstatus: active\n") == 1 and doc.count("\ncustomProperties:\n") == 1, "kraken-trades' status or customProperties moved: move the plant"
p.write_text(doc.replace("\nstatus: active\n", "\nstatus: paused\n").replace("\ncustomProperties:\n", """
customProperties:
  - property: lifecycleChange
    value:
      since: "2026-11-28"
      reason: The run path pauses the venue to prove its derived product goes partial.
      decision: ADR-056
      resume: From the topic's records still held when it resumes.
"""))
PY
)
PLANT_VENUE_FAIL=$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/market-capture/kraken-trades/models/silver_trade.sql"
sql = p.read_text()
tail = "\nfrom flagged\nwhere is_new or is_duplicate <> was_duplicate\n"
assert sql.endswith(tail) and sql.count(tail) == 1, "silver_trade no longer ends 'from flagged where ...': move the plant"
p.write_text(sql.removesuffix(tail) + "\nfrom (select * from flagged union all (select * from flagged where is_new limit 1)) as flagged\n"
             "where is_new or is_duplicate <> was_duplicate\n")
PY
)

# The Kafka connector on a product of the release (TP, default kraken-trades), for the chaos scripts
# W2-12 and W2-13: scripts/lib/kafka_path_probe.py's facts and claims, as kafka-path.sh runs them on
# its fixture product.
KPROBE=scripts/lib/kafka_path_probe.py
kruns() {  # kruns <run>: load, audit, publish of TP
  local t
  for t in load audit publish; do TP=${TP:-$KT} task "$ID" "$1" "$t"; done
}
kfacts() {  # kfacts <label>: TP's topic, landing, bronze and audit_checks to $RP/facts-<label>.json
  docker exec -i "$SPARK" python3 - facts "/k2/releases/$ID" "${TP:-$KT}" < "$KPROBE" 2> "$RP/facts-$1.err" | tail -1 > "$RP/facts-$1.json"
  [ -s "$RP/facts-$1.json" ] || echo "$P: facts $1 failed: $(grep -v '^\s' "$RP/facts-$1.err" | tail -2)"
}
kclaim() {  # kclaim <name>: kafka_path_probe.py's claim over $RP, numbered as this script's next; a FAIL stops the run
  local out code
  out=$(python3 "$KPROBE" claim "$1" "$RP" 2>&1)
  code=$?
  claimed "$1" "$(sed -E 's/^(PASS|FAIL|INFO) [0-9]+ /\1 /' <<< "$out")" $code
}

# The steps more than one script takes, each ending in its claim.
rp_calendars() {  # calendars, ECB's input, through the wrapper
  build "$ID" "$CAL" rp-cal
  probe state cal
  claim cal
}
rp_good() {  # the good file: seed, load, audit, publish
  probe seed "$ID" good.xml rp-good
  for t in load audit publish; do task "$ID" rp-good "$t"; done
  objects good; spark_py facts facts-good; probe state good
  claim good "$ID"
}
rp_build() {  # the build on the published bronze
  build "$ID" "$ECB" rp-good
  probe state built
  claim build "$ID"
}
rp_again() {  # the same bytes again, a new run, and its build
  probe seed "$ID" good.xml rp-again
  for t in load audit publish; do task "$ID" rp-again "$t"; done
  build "$ID" "$ECB" rp-again
  objects again; probe state again
  claim again "$ID"
}
rp_corrupt() {  # the corrupt file; a third batch refused behind it; the clearing step; the third's load again
  probe seed "$ID" corrupt.xml rp-corrupt
  for t in load audit publish; do task "$ID" rp-corrupt "$t"; done
  probe state corrupt
  probe seed "$ID" third.xml rp-third
  task "$ID" rp-third load
  objects corrupt
  spark_py clear clear
  TRY=2 task "$ID" rp-third load
  spark_py facts facts-corrupt; probe state cleared
  claim corrupt "$ID"
}
rp_every() {  # every dbt product of the release through the wrapper, each after the products it reads; its claim
  local p n=0 runs=()
  for p in $(python3 - "$STATE/releases/$ID" <<'PY'
# governance.quality-results last: it reads every builder's results (model.test_results_writers),
# which no input port declares.
import json, sys
from pathlib import Path
docs = {d["product"]: d for d in (json.loads(f.read_text()) for f in Path(sys.argv[1], "compiled/products").glob("*/pipeline.json"))
        if (d.get("build") or {}).get("engine") == "dbt"}
order = []
def visit(p):
    if p not in order:
        for i in docs[p].get("inputs", []):
            if (q := ".".join(i["contractId"].split(".")[:2])) in docs:
                visit(q)
        order.append(p)
for p in sorted(docs, key=lambda p: p == "governance.quality-results"):
    visit(p)
print("\n".join(order))
PY
  ); do
    n=$((n + 1)); runs+=("$p=rp-every-$n")
    build "$ID" "$p" "rp-every-$n"
  done
  claimed every "$(bash "$L" py "$P" - "$ID" "${runs[@]}" 2>&1 <<'PY'
# Each build exited 0 with XCom exit 0, and each output's audited tag is its main; INFO: main's rows.
import json, sys
from pathlib import Path
sys.path[:0] = ["/src/scripts/verify"]
import catalog_drift as cd
rid, runs = sys.argv[1], [a.split("=") for a in sys.argv[2:]]
lk, rp, bad = json.loads(Path("/work/env.json").read_text())["lakekeeper"] + "/catalog", Path("/work/rp"), []
for product, run in runs:
    rc = (rp / f"{run}.build.rc").read_text().strip()
    out = [x for x in (rp / f"{run}.build.out").read_text().splitlines() if x.strip()]
    err = [x for x in (rp / f"{run}.build.err").read_text().splitlines() if x.startswith("k2-dbt:")]
    if rc != "0" or not out or json.loads(out[-1]).get("exit") != 0:
        bad.append(f"{product} ({run}) exit {rc}: {(err or ['(no k2-dbt line)'])[-1][:300]}")
        continue
    for rel in json.loads((Path("/work/releases") / rid / f"compiled/products/{product}/pipeline.json").read_text())["build"]["outputs"]:
        catalog, ns, table = rel.split(".")
        meta = cd.Catalog(lk, catalog.removesuffix("_build")).table(ns, table) or {}
        refs = {k: r["snapshot-id"] for k, r in (meta.get("refs") or {}).items()}
        main = next((x for x in meta.get("snapshots") or [] if x["snapshot-id"] == refs.get("main")), {})
        print(f"INFO every {product} {rel}: main {refs.get('main')} holds {main.get('summary', {}).get('total-records')} rows")
        if refs.get("main") is None or refs.get("audited") != refs["main"]:
            bad.append(f"{product} {rel}: refs {refs}, not audited on main")
print(f"{'FAIL' if bad else 'PASS'} every dbt product of the release builds through the wrapper and ends audited on main: "
      f"{bad[0] if bad else ', '.join(p for p, _ in runs)}")
sys.exit(1 if bad else 0)
PY
)" $?
}
