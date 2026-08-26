#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# SIGKILL an ingest mid-run, then prove the lake is still exactly-once.
#
# Proves one row in docs/architecture/failure-modes.md:
#   lake ingest / killed mid-run
#
# This is the Phase D exit criterion "kill mid-run -> no dupes/gaps", executed.
# The claim under test is the one docker/lake/offsets.py exists to make: because
# the Kafka offsets are written by the same commit as the data, a process that
# dies at any instant leaves the table in one of exactly two states — committed
# with its offsets, or not committed at all. There is no third state where the
# rows landed and the bookkeeping did not, which is precisely the state a
# separate watermark table can reach.
#
# No alert is expected. A single killed run recovers inside one 5-minute cycle,
# well under LakeIngestFailed's 30-minute threshold; if this fired an alert the
# threshold would be wrong. The measurement is the audit, not the alert.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

SPARK=k2-spark-iceberg
LAKE=/home/iceberg/lake
KILL_AFTER="${K2_CHAOS_KILL_AFTER:-25}"

for arg in "$@"; do
  case $arg in --kill-after=*) KILL_AFTER=${arg#*=} ;; esac
done

preflight "$SPARK" k2-lakekeeper k2-minio k2-prometheus
banner "lake-ingest-kill.sh kill_after=${KILL_AFTER}s" \
  "none expected" docs/runbooks/lake-recovery.md \
  "SIGKILL an ingest ${KILL_AFTER}s in, then audit for duplicates and gaps"

pause_lake_ingest

# The trap covers both halves. Without it an early `die` — the "already
# finished" branch below is the likely one — leaves the background ingest
# running against a lake the operator now thinks is idle, and leaves the
# 5-minute schedule paused.
cleanup() { kill_ingest >/dev/null 2>&1 || true; resume_lake_ingest; }
trap cleanup EXIT

# Both processes. `pkill -f "$LAKE/ingest.py"` matches the Python driver only:
# the child JVM's command line is `... SparkSubmit ... pyspark-shell` and does
# not contain the script path, so SIGKILLing the driver leaves the JVM holding
# the Kafka read and the staged files. pgrep then reports clean while the work
# it was supposed to stop carries on.
kill_ingest() {
  docker exec "$SPARK" pkill -9 -f "$LAKE/ingest.py"
  docker exec "$SPARK" pkill -9 -f 'org.apache.spark.deploy.SparkSubmit' || true
}

echo "→ starting an ingest in the background" >&2
docker exec -d "$SPARK" sh -c "python3 $LAKE/ingest.py > /tmp/killed-ingest.log 2>&1"
sleep "$KILL_AFTER"

echo "→ SIGKILL (driver and JVM)" >&2
kill_ingest \
  || die "no ingest process to kill after ${KILL_AFTER}s — it had already finished; raise --kill-after=N"
sleep 5
docker exec "$SPARK" pgrep -f "$LAKE/ingest.py" >/dev/null \
  && die "an ingest is still running after SIGKILL"
docker exec "$SPARK" pgrep -f 'org.apache.spark.deploy.SparkSubmit' >/dev/null \
  && die "the ingest JVM is still running after SIGKILL"

# WHERE the kill landed decides what this run proved. ingest.py prints
# "stage 1: committing N rows" immediately before the append; a kill before
# that line stopped a Kafka read, which says nothing about commit atomicity.
# The audits below still run — they are cheap and still worth having — but the
# claim on the tin is only earned by the other branch, so the two are named.
if docker exec "$SPARK" grep -q 'stage 1: committing' /tmp/killed-ingest.log 2>/dev/null; then
  echo "→ the kill landed inside the commit window (log reached 'stage 1: committing')" >&2
  phase="commit"
else
  echo "→ the kill landed BEFORE the commit — this run exercised the Kafka read only." >&2
  echo "  Raise --kill-after=N to land inside the write. The audits below still run." >&2
  phase="read"
fi

echo "→ recovery: the next scheduled cycle, run by hand" >&2
start=$SECONDS
docker exec "$SPARK" python3 "$LAKE/ingest.py" | sed 's/^/    /' >&2
t_recover=$((SECONDS - start))

echo "→ audits — this is the assertion, not the exit code of the run above" >&2
docker exec "$SPARK" python3 "$LAKE/maintenance.py" --audit-only | sed 's/^/    /' >&2
echo "  A killed run either committed with its offsets or committed nothing." >&2
echo "  offset_continuity failing here would mean a third state exists." >&2

report "lake-ingest-kill.sh kill_after=${KILL_AFTER}s phase=$phase" "none expected" 0 "$t_recover"
