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
# The alert bound is long by design. LakeIngestFailed needs raw.messages to go
# 30 minutes without a commit plus a 5-minute `for`, because a shorter threshold
# would page on every slow backlog slice. Waiting that out is opt-in
# (--wait-for-alert); the default run measures the mechanism in about two
# minutes and says what the alert bound is.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

WAIT_FOR_ALERT=no
for arg in "$@"; do
  [ "$arg" = "--wait-for-alert" ] && WAIT_FOR_ALERT=yes
done

SPARK=k2-spark-iceberg
LAKE=/home/iceberg/lake

preflight "$SPARK" k2-lakekeeper k2-prometheus
banner "lake-lakekeeper-stop.sh wait_for_alert=$WAIT_FOR_ALERT" \
  LakeIngestFailed docs/runbooks/lake-recovery.md \
  "docker stop k2-lakekeeper, then run an ingest that must fail without half-writing"

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

before=$(state)
echo "→ before: snapshots/rows = $before" >&2

echo "→ stopping k2-lakekeeper" >&2
docker stop k2-lakekeeper >/dev/null
trap 'docker start k2-lakekeeper >/dev/null 2>&1 || true' EXIT

set +e
docker exec "$SPARK" python3 "$LAKE/ingest.py" >/tmp/chaos-ingest.log 2>&1
ingest_rc=$?
set -e
echo "→ ingest exited $ingest_rc with the catalog down (expected non-zero)" >&2
[ "$ingest_rc" -ne 0 ] || die "ingest exited 0 with Lakekeeper stopped — it is not talking to the catalog it claims to"
tail -3 /tmp/chaos-ingest.log | sed 's/^/    /' >&2

t_fire=0
if [ "$WAIT_FOR_ALERT" = yes ]; then
  echo "→ waiting for LakeIngestFailed (threshold 30m + for 5m; up to 45m)" >&2
  t_fire=$(wait_for_alert LakeIngestFailed 2700) \
    || echo "→ LakeIngestFailed did not fire within ${t_fire}s" >&2
else
  echo "→ skipping the alert wait. LakeIngestFailed fires ~35m into an outage:" >&2
  echo "  k2_lake_last_commit_age_seconds{table=\"raw.messages\"} > 1800, for: 5m." >&2
fi

echo "→ starting k2-lakekeeper" >&2
docker start k2-lakekeeper >/dev/null
trap - EXIT
sleep 20

after=$(state)
echo "→ after: snapshots/rows = $after" >&2
[ "$before" = "$after" ] || die "the failed run changed the table: $before -> $after. A commit half-landed; this is the row's whole claim and it is false"
echo "→ table unchanged across the failed run — the commit is atomic as claimed" >&2

echo "→ recovery: one ingest with the catalog back" >&2
start=$SECONDS
docker exec "$SPARK" python3 "$LAKE/ingest.py" | sed 's/^/    /' >&2
t_recover=$((SECONDS - start))

report "lake-lakekeeper-stop.sh wait_for_alert=$WAIT_FOR_ALERT" \
  LakeIngestFailed "$t_fire" "$t_recover"
