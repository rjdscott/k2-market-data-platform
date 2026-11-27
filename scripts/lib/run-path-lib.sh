# shellcheck shell=bash
# The throwaway run path, shared by scripts/verify/run-path.sh (spine-e2e's stage run) and plan
# 002's verification scripts (scripts/verify/fx-idempotent.sh, scripts/chaos/fx-bad-file.sh,
# dbt-failing-test.sh, trino-stop-mid-build.sh). Sourced, never executed: each script calls
# rp_start, then the setup it needs, then its steps, then finish.
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

rp_fail_release() {  # rp_fail_release <python patch>: a release of this tree with the patch applied (argv[1] the clone); sets IDF
  mkdir -p "$W/fail/clone"
  git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$W/fail/clone"
  python3 - "$W/fail/clone" <<< "$1" || die "the plant no longer applies to this tree (its assert above)"
  python3 - docker-compose.yml > "$W/images.json" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "run-path/placeholder@sha256:" + "0" * 64 for n in names}))
PY
  local git=(git -C "$W/fail/clone" -c user.name=run-path -c user.email=run-path@example.org)
  { "${git[@]}" init -q -b main && "${git[@]}" add -A && "${git[@]}" commit -q -m "$P: a failing test" &&
    "${git[@]}" tag -a v0.0.0-runpath.1 -m "$P"; } || die "the failing tree did not commit"
  local k2gen=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
  "${k2gen[@]}" docset --tag v0.0.0-runpath.1 --repo "$W/fail/clone" --out "$W/fail/set" > "$W/compile.log" 2>&1 &&
    "${k2gen[@]}" release --set "$W/fail/set" --previous none --images "$W/images.json" --out "$W/fail/releases" 2>>"$W/compile.log" \
    | tail -1 > "$W/fail/id"
  IDF=$(cat "$W/fail/id")
  if [ -z "$IDF" ] || [ ! -d "$W/fail/releases/$IDF" ]; then tail -20 "$W/compile.log" >&2; die "the failing release did not compile"; fi
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
task() {  # task <release id> <run> <load|audit|publish|expire|maintain> [args]: factory.SparkTask's argv; TRY=n labels a rerun
  local id=$1 run=$2 t=$3 label=$2.$3${TRY:+.$TRY}; shift 3
  local argv=("/k2/releases/$id/platform/docker/lake/connector.py" "$t")
  case $t in expire|maintain) argv=("/k2/releases/$id/platform/docker/lake/$t.py") ;; esac
  docker exec "$SPARK" sh -c "$WRAPPER" "/tmp/k2-airflow-$label.pid" "${argv[@]}" --release "$id" --product "$ECB" \
    --run-id "ecb_fx_rates/$run" "$@" > "$RP/$label.out" 2> "$RP/$label.err"
  echo $? > "$RP/$label.rc"
  echo "$P: $label exit $(cat "$RP/$label.rc")"
}
export K2_TRINO_HOST=trino K2_TRINO_INTERNAL_PORT=8443 K2_RUNS_USER=svc.runs K2_CATALOG_URI=http://lakekeeper:8181/catalog
build() {  # build <release id> <product> <run>: k2-dbt build as factory.PinnedOneShot and oneshot.py run it
  local id=$1 product=$2 run=$3 dag writer publisher cpus memory
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
  echo "$P: $run build ($product, $id) exit $(cat "$RP/$run.build.rc")"
}
spark_py() {  # spark_py <facts|clear> <label>: scripts/lib/run_path_spark.py in the Spark container, on the release
  docker exec -i "$SPARK" python3 - "/k2/releases/$ID" "$ECB" "$1" < scripts/lib/run_path_spark.py > "$RP/$2.out" 2> "$RP/$2.err"
  echo $? > "$RP/$2.rc"
}
objects() { docker exec "$P-minio" mc ls -r --json local/k2-landing > "$RP/objects-$1.json"; }
probe() { bash "$L" py "$P" scripts/lib/run_path_probe.py "$@"; }
claim() {  # claim <name> [args]: the probe's one line, numbered; a FAIL stops the run
  local out code
  out=$(probe claim "$@" 2>&1)
  code=$?
  RP_N=$((RP_N + 1))
  printf '%s\n' "$out" | grep -E '^(PASS|FAIL|INFO) ' | sed -E "s/^(PASS|FAIL) /\1 $RP_N /" | tee -a "$W/claims"
  if [ $code != 0 ]; then
    grep -qE '^FAIL ' <<< "$out" || echo "FAIL $RP_N $1: the probe failed: $(tail -1 <<< "$out")" | tee -a "$W/claims"
    finish "stopped at claim $RP_N ($1): the claims after it build on the lake it left"
  fi
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
