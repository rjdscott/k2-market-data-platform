#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Stop the Iceberg catalog under a running ingest.
#
# Proves one row in docs/architecture/failure-modes.md:
#   Lakekeeper / down mid-commit
#
# What is actually being checked is that the failure is CLEAN. Iceberg's commit
# is a single atomic swap of the table's metadata pointer in the catalog, so a
# catalog that is gone cannot half-commit: either the pointer moved or it did
# not. The assertion below is that the snapshot count and the row count are
# identical before and after a failed run — i.e. the ingest lost its work rather
# than leaving a partial table, and the next run reads the same offsets again.
#
# MID-RUN by default, and that is the whole difference from the first version of
# this script. Stopping the catalog *before* the ingest starts kills it on its
# first `SELECT ... FROM raw.messages.snapshots`, before a single Kafka record
# is read and long before a write is attempted — so `before == after` is
# trivially true and the row's "down mid-commit" claim is untested. The default
# now starts the ingest, waits --stop-after seconds for it to get into the write
# window, and stops the catalog under it. `--stop-first` keeps the old shape for
# the "catalog already down when the cycle fires" case, which is also real.
#
# Which phase the outage actually landed in is printed, not assumed: the timing
# is best-effort on a live feed, and a run that hit the read is honest evidence
# of the read case rather than dishonest evidence of the commit case.
#
# TWO alerts are in scope, and the fast one is not LakeIngestFailed.
# LakeExporterStalled fires ~10 minutes in — the exporter stays up, keeps being
# scraped, and stops completing refreshes because the prefix lookup fails.
# LakeIngestFailed needs raw.messages to go 30 minutes without a commit plus a
# 5-minute `for`. Waiting either out is opt-in (--wait-for-alert).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

WAIT_FOR_ALERT=no
STOP_FIRST=no
STOP_AFTER="${K2_CHAOS_STOP_AFTER:-20}"
for arg in "$@"; do
  case $arg in
    --wait-for-alert) WAIT_FOR_ALERT=yes ;;
    --stop-first)     STOP_FIRST=yes ;;
    --stop-after=*)   STOP_AFTER=${arg#*=} ;;
  esac
done

SPARK=k2-spark-iceberg
LAKE=/home/iceberg/lake

preflight "$SPARK" k2-lakekeeper k2-prometheus
banner "lake-lakekeeper-stop.sh stop_first=$STOP_FIRST stop_after=${STOP_AFTER}s" \
  LakeExporterStalled docs/runbooks/lake-recovery.md \
  "stop k2-lakekeeper under a running ingest; it must fail without half-writing"

# `state` prints "<snapshot count> <row count>" straight from the catalog. Run
# while the catalog is up, on both sides of the outage.
state() {
  docker exec -i "$SPARK" python3 - <<'PY' 2>/dev/null | tail -1
import sys
sys.path.insert(0, "/home/iceberg/lake")
from ingest import RAW_TABLE
from spark_conf import lake_session
spark = lake_session("k2-chaos-state")
try:
    snaps = spark.sql(f"SELECT count(*) FROM {RAW_TABLE}.snapshots").collect()[0][0]
    rows = spark.sql(f"SELECT count(*) FROM {RAW_TABLE}").collect()[0][0]
    print(f"{snaps} {rows}")
finally:
    spark.stop()
PY
}

# The trap goes in BEFORE anything it has to undo. `state()` below can die —
# it runs a Spark session against the catalog — and with the trap installed
# after it, that death left the 5-minute schedule paused for good.
# `resume_lake_ingest` is a no-op until `pause_lake_ingest` records ids, so
# installing it first is safe and installing it later is not.
restore() { docker start k2-lakekeeper >/dev/null 2>&1 || true; resume_lake_ingest; }
trap restore EXIT

pause_lake_ingest

before=$(state)
[ -n "$before" ] || die "could not read snapshot/row counts before the fault — the assertion at the end compares against this, so an empty value would make it pass by vacuum"
echo "→ before: snapshots/rows = $before" >&2

if [ "$STOP_FIRST" = yes ]; then
  echo "→ stopping k2-lakekeeper BEFORE the run (catalog-already-down case)" >&2
  docker stop k2-lakekeeper >/dev/null
  set +e
  docker exec "$SPARK" python3 "$LAKE/ingest.py" >/tmp/chaos-ingest.log 2>&1
  ingest_rc=$?
  set -e
else
  echo "→ starting an ingest, then stopping k2-lakekeeper ${STOP_AFTER}s in" >&2
  docker exec -d "$SPARK" sh -c "python3 $LAKE/ingest.py > /tmp/chaos-ingest.log 2>&1"
  sleep "$STOP_AFTER"
  docker stop k2-lakekeeper >/dev/null
  # Wait for the run to notice. It cannot outlive the catalog: every commit and
  # every metadata read goes through it.
  for _ in $(seq 1 60); do
    docker exec "$SPARK" pgrep -f "$LAKE/ingest.py" >/dev/null 2>&1 || break
    sleep 5
  done
  docker exec "$SPARK" pgrep -f "$LAKE/ingest.py" >/dev/null 2>&1 \
    && die "the ingest is still running 5 minutes after the catalog went away"
  # A backgrounded `docker exec -d` gives us no exit status, so the log is the
  # evidence. An ingest that reached neither an error nor a commit is a third
  # outcome worth failing on.
  ingest_rc=1
  # The detached run wrote its log INSIDE the container; bring it host-side
  # before reading it (the first run grepped a file that did not exist).
  docker exec "$SPARK" cat /tmp/chaos-ingest.log >/tmp/chaos-ingest.log 2>/dev/null || true
  grep -qaiE 'error|exception|traceback' /tmp/chaos-ingest.log \
    || die "the ingest left no error in its log with the catalog stopped — it is not talking to the catalog it claims to"
fi

echo "→ ingest failed with the catalog down (expected)" >&2
[ "$ingest_rc" -ne 0 ] || die "ingest exited 0 with Lakekeeper stopped — it is not talking to the catalog it claims to"

# Which phase the outage landed in. `stage 1: committing` is printed
# immediately before the append, so its presence means the run was inside the
# write when the catalog went away — the case this script's FMEA row claims.
if grep -qa 'stage 1: committing' /tmp/chaos-ingest.log 2>/dev/null; then
  phase="commit"
  echo "→ the outage landed inside the commit window" >&2
else
  phase="read"
  echo "→ the outage landed before the commit (catalog read or Kafka read)." >&2
  echo "  Raise --stop-after=N to land inside the write." >&2
fi
tail -3 /tmp/chaos-ingest.log | sed 's/^/    /' >&2

t_fire=0
if [ "$WAIT_FOR_ALERT" = yes ]; then
  echo "→ waiting for LakeExporterStalled (300s stale + for 5m; up to 20m)" >&2
  t_fire=$(wait_for_alert LakeExporterStalled 1200) \
    || echo "→ LakeExporterStalled did not fire within ${t_fire}s" >&2
else
  echo "→ skipping the alert wait. Expected order in a catalog outage:" >&2
  echo "  ~10m LakeExporterStalled  time() - k2_lake_last_refresh_ts_seconds > 300, for: 5m" >&2
  echo "  ~35m LakeIngestFailed     time() - k2_lake_last_commit_ts_seconds{table=\"raw.messages\"} > 1800, for: 5m" >&2
  echo "  LakeScrapeErrors does NOT fire: the prefix lookup throws before any table is read." >&2
fi

echo "→ starting k2-lakekeeper" >&2
docker start k2-lakekeeper >/dev/null
wait_healthy k2-lakekeeper 120

after=$(state)
[ -n "$after" ] || die "could not read snapshot/row counts after the fault — the catalog is healthy but not answering Spark"
echo "→ after: snapshots/rows = $after" >&2
[ "$before" = "$after" ] || die "the failed run changed the table: $before -> $after. A commit half-landed; this is the row's whole claim and it is false"
echo "→ table unchanged across the failed run — the commit is atomic as claimed" >&2

echo "→ recovery: one ingest with the catalog back" >&2
start=$SECONDS
docker exec "$SPARK" python3 "$LAKE/ingest.py" | sed 's/^/    /' >&2
t_recover=$((SECONDS - start))

report "lake-lakekeeper-stop.sh phase=$phase stop_first=$STOP_FIRST" \
  LakeExporterStalled "$t_fire" "$t_recover"
