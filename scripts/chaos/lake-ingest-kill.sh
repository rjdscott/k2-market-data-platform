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

echo "→ starting an ingest in the background" >&2
docker exec -d "$SPARK" sh -c "python3 $LAKE/ingest.py > /tmp/killed-ingest.log 2>&1"
sleep "$KILL_AFTER"

echo "→ SIGKILL" >&2
docker exec "$SPARK" pkill -9 -f "$LAKE/ingest.py" \
  || die "no ingest process to kill after ${KILL_AFTER}s — it had already finished; raise --kill-after=N"
sleep 5
docker exec "$SPARK" pgrep -f "$LAKE/ingest.py" >/dev/null \
  && die "an ingest is still running after SIGKILL"

echo "→ recovery: the next scheduled cycle, run by hand" >&2
start=$SECONDS
docker exec "$SPARK" python3 "$LAKE/ingest.py" | sed 's/^/    /' >&2
t_recover=$((SECONDS - start))

echo "→ audits — this is the assertion, not the exit code of the run above" >&2
docker exec "$SPARK" python3 "$LAKE/maintenance.py" --audit-only | sed 's/^/    /' >&2
echo "  A killed run either committed with its offsets or committed nothing." >&2
echo "  offset_continuity failing here would mean a third state exists." >&2

report "lake-ingest-kill.sh kill_after=${KILL_AFTER}s" "none expected" 0 "$t_recover"
