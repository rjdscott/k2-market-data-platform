#!/usr/bin/env bash
# Spike 2026-11-26: the fixed cost of one product run. Write-up:
# docs/research/2026-11-26-unified-platform-architecture/SPIKE-RUNCOST.md; the numbers
# are published in docs/benchmarks/2026-11-26-product-run-cost.md.
#
#   bash scripts/spikes/2026-11-26/spike-runcost/run.sh
#
# 1. Spark: spike.py in k2-spark-iceberg, one job at a time, never beside a lake job
#    or other Spark driver, with a 512m driver, each job started only when
#    host MemAvailable is at least 6 GiB. Five timed runs on a small table, a second
#    table grown to 200 snapshots, five timed runs on it. Writes only the namespace
#    lake.spike_runcost_20261126, dropped at the end (and first, if an interrupted run left it).
# 2. dbt: seven builds of one small incremental model with two tests, as the writer
#    dbt_market_analytics, into market_analytics.spike_runcost_20261126, dropped at the
#    end. The first build creates the table; the six after it are the timed ones.
#
# Synthetic rows only. Prints min / median / max per step (summarise.py) and exits 0
# when every run completed. Each Spark job waits for a gap between lake_ingest runs: 11
# minutes on 2026-11-26, while lake_ingest took most of every five.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
SPARK=k2-spark-iceberg
TRINO=k2-trino
D=$(mktemp -d)
export DBT_SEND_ANONYMOUS_USAGE_STATS=false DBT_PROFILES_DIR="$D/dbt" DBT_PROJECT_DIR="$D/dbt"
cp -r "$HERE/dbt" "$D/dbt"

# Busy while a lake job or any other Spark driver (a pyspark-shell JVM) runs in the container.
busy() { docker exec "$SPARK" pgrep -f "/home/iceberg/lake/|pyspark-shell" >/dev/null; }
to_next() {  # seconds until the next lake_ingest start (cron 1-59/5, docker/airflow/dags/lake.py)
  local m s n; m=$(date -u +%-M); s=$(date -u +%-S)
  n=$(( ((1 - m) % 5 + 5) % 5 * 60 - s )); (( n <= 0 )) && n=$(( n + 300 )); echo "$n"
}
mem_ok() { (( $(awk '/MemAvailable/{print $2}' /proc/meminfo) >= 6 * 1024 * 1024 )); }
wait_window() {  # $1 = seconds the job needs before the next ingest starts; gives up after 30 min
  local waited=0
  while busy || (( $(to_next) < $1 )) || ! mem_ok; do
    sleep 5; waited=$(( waited + 5 ))
    (( waited < 1800 )) || { echo "FAIL no quiet Spark window with 6 GiB free in 30 min"; exit 1; }
  done
}
spark() {  # spark <spike.py args>: one job, its RESULT line appended to $D/spark-runs.txt
  local t0 out wall
  t0=$(date +%s.%N)
  # The script goes in on stdin, so nothing is copied into the container to clean up.
  out=$(docker exec -i -e PYTHONDONTWRITEBYTECODE=1 -e K2_LAKE_DRIVER_MEMORY=512m -e T0="$t0" "$SPARK" \
    python3 - "$@" < "$HERE/spike.py" 2>>"$D/spark.stderr" | grep '^RESULT' || true)
  [ -n "$out" ] || { echo "FAIL spike.py $* printed no RESULT:"; tail -20 "$D/spark.stderr"; exit 1; }
  wall=$(awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN {print b - a}')
  overlap=no; docker exec "$SPARK" pgrep -f /home/iceberg/lake/ >/dev/null && overlap=yes
  echo "$(date -u +%T) args=$* host_wall=$wall ingest_at_end=$overlap ${out#RESULT }" | tee -a "$D/spark-runs.txt"
  LAST=$out
}
sql() {
  local out
  out=$(docker exec "$TRINO" trino --user dbt_market_analytics --execute "$1" 2>&1) || { echo "FAIL $1: $out"; return 1; }
}
S=market_analytics.spike_runcost_20261126
seed() {  # 1,000 more synthetic rows in the dbt model's source, ids continuing from the last
  sql "INSERT INTO $S.src_tiny SELECT n + $1, 0, current_timestamp(6), 'SYN' || cast(n % 10 AS varchar),
    cast(100 + n AS decimal(18,8)) / 7, cast(n + 1 AS decimal(18,8)) / 3
    FROM UNNEST(sequence(1, 1000)) AS t(n)"
}
TORN=yes  # no while the Spark namespace may exist
cleanup() {
  if [ "$TORN" = no ]; then
    ( wait_window 40; spark teardown > /dev/null ) \
      || echo "FAIL lake.spike_runcost_20261126 may be left; the next run of this script drops it first"
  fi
  docker exec "$TRINO" trino --user dbt_market_analytics --execute "DROP SCHEMA IF EXISTS $S CASCADE" >/dev/null 2>&1 || true
  rm -rf "$D"
}
trap cleanup EXIT

wait_window 40; spark teardown  # what an interrupted run left
TORN=no
wait_window 50; spark setup
for _ in 1 2 3 4 5; do wait_window 45; spark run a; done
for _ in 1 2 3 4 5 6; do
  wait_window 70; spark grow b 200 $(( $(to_next) - 35 ))
  grep -q '"snapshots": 200' <<< "$LAST" && break
done
grep -q '"snapshots": 200' <<< "$LAST" || { echo "FAIL table b never reached 200 snapshots"; exit 1; }
for _ in 1 2 3 4 5; do wait_window 45; spark run b; done
wait_window 40; spark teardown; TORN=yes
grep -q '"namespaces_left": \[[^]]*spike_runcost' <<< "$LAST" && { echo "FAIL the scratch namespace survived"; exit 1; }

sql "DROP SCHEMA IF EXISTS $S CASCADE"
sql "CREATE SCHEMA $S"
sql "CREATE TABLE $S.src_tiny (id bigint, batch_id bigint, ts timestamp(6), symbol varchar,
  price decimal(18,8), qty decimal(18,8))"
for i in 0 1 2 3 4 5 6; do
  seed $(( i * 1000 ))
  t0=$(date +%s.%N)
  rc=0; uv run --project "$ROOT/dbt" --locked -q dbt build > "$D/dbt_run_$i.out" 2>&1 || rc=$?
  wall=$(awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN {print b - a}')
  ingest=no; docker exec "$SPARK" pgrep -f /home/iceberg/lake/ingest.py >/dev/null && ingest=yes
  echo "run=$i rc=$rc wall=$wall ingest_running_at_end=$ingest $(grep -o 'Done\..*' "$D/dbt_run_$i.out")" | tee -a "$D/dbt-runs.txt"
  [ "$rc" = 0 ] || { echo "FAIL dbt build $i:"; tail -20 "$D/dbt_run_$i.out"; exit 1; }
done

python3 "$HERE/summarise.py" "$D"
