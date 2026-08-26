#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# make lake-verify — the Phase D exit criteria, as a command.
#
#   1. every committed (topic, partition) carries an offset, and every offset
#      run in raw.messages is gapless
#   2. raw rows on the trades/book topics == rows in the matching bronze table
#   3. running the same bounded window twice adds nothing the second time
#
# Runs against the LIVE stack. It writes real data into the real tables — that
# is the point, since what is being checked is that the ingest is idempotent
# rather than that a copy of it is. It deletes nothing and it never rewrites a
# committed snapshot, so a run that fails leaves the lake exactly as it found it
# plus one ordinary ingest.
#
# Both ingest runs are pinned to the same `--end-timestamp`. Without that the
# second run legitimately picks up the seconds of live traffic that arrived
# while the first was running, and "adds 0" becomes untestable on a live feed
# rather than false. `ingest.py` bounds the read by kafka_ts as well as by
# `endingTimestamp`, because Spark resolves an unmatched timestamp to LATEST and
# a quiet partition would otherwise drift past the window between the two runs —
# which reads here as a false "NOT idempotent".
#
# The 5-minute lake-ingest-5min schedule is paused for the duration and resumed
# from a trap. A scheduled run landing between the two cycles reads records this
# script's window does not cover, and the flock in ingest.py makes an overlapping
# one exit 2 — either way the result would be a failure that says nothing about
# idempotency.
#
#   scripts/lake-verify.sh                 # window ends 60 s ago
#   scripts/lake-verify.sh --end <epoch_ms>
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

SELF="${BASH_SOURCE[0]}"          # captured before the cd below, for --help
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=chaos/lib.sh
. ./chaos/lib.sh

SPARK="${K2_SPARK_CONTAINER:-k2-spark-iceberg}"
LAKE_DIR="${K2_LAKE_DIR:-/home/iceberg/lake}"

END_MS=""
while [ $# -gt 0 ]; do
  case $1 in
    --end) END_MS=$2; shift 2 ;;
    --end=*) END_MS=${1#*=}; shift ;;
    -h|--help) sed -n '3,33p' "$(basename "$SELF")"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# 60 s of slack so the window is closed rather than racing the producers.
[ -n "$END_MS" ] || END_MS=$(( ($(date +%s) - 60) * 1000 ))

die() { printf 'lake-verify: %s\n' "$*" >&2; exit 1; }

docker inspect -f '{{.State.Running}}' "$SPARK" 2>/dev/null | grep -qx true \
  || die "$SPARK is not running. Bring the stack up first: make up"

pause_lake_ingest
trap resume_lake_ingest EXIT

echo "── lake-verify ─────────────────────────────────────────────"
echo "  container : $SPARK"
echo "  window end: $END_MS ($(date -u -d "@$((END_MS/1000))" +%FT%TZ))"
echo

echo "[1/3] ingest cycle 1"
docker exec "$SPARK" python3 "$LAKE_DIR/ingest.py" --end-timestamp "$END_MS" \
  | sed 's/^/      /'

echo "[2/3] ingest cycle 2 — the same window, so it must add nothing"
cycle2=$(docker exec "$SPARK" python3 "$LAKE_DIR/ingest.py" --end-timestamp "$END_MS")
printf '%s\n' "$cycle2" | sed 's/^/      /'
printf '%s\n' "$cycle2" | grep -q 'stage 1: no new records' \
  || die "cycle 2 read new records for a window that was already ingested — NOT idempotent"

echo "[3/3] checks"
# One python program rather than a pile of shell: these are three assertions
# over the same catalog session, and starting the JVM three times to keep them
# in separate files would cost a minute for no clarity.
docker exec -i "$SPARK" python3 - "$LAKE_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])

import offsets as O
from ingest import ALL_TOPICS, BOOK_TABLE, RAW_TABLE, TRADES_TABLE, snapshot_history, topics
from spark_conf import lake_session

spark = lake_session("k2-lake-verify")
failures = []


def check(name, ok, detail):
    print(f"      {'ok  ' if ok else 'FAIL'} {name:<26} {detail}")
    if not ok:
        failures.append(name)


try:
    # (1a) the offsets property is where the exactly-once contract lives. If it
    # is missing, every following run silently restarts from the beginning.
    summary = O.latest_summary(snapshot_history(spark, RAW_TABLE), O.JOB_INGEST)
    check("offsets property", bool(summary and O.KAFKA_OFFSETS in summary),
          "k2.kafka-offsets present on the latest ingest snapshot" if summary
          else "no ingest snapshot on " + RAW_TABLE)

    # Every committed topic is one this ingest knows about. NOT the converse:
    # a v3 topic that has never carried a record produces no row and so no
    # committed offset, and a legitimately quiet topic is not a verification
    # failure. The direction that matters is an offset committed for a topic
    # ALL_TOPICS does not list, which means the two ends disagree about what
    # the lake ingests.
    committed = O.decode(summary[O.KAFKA_OFFSETS]) if summary else {}
    unknown = sorted(set(committed) - set(ALL_TOPICS))
    quiet = sorted(set(ALL_TOPICS) - set(committed))
    check("offsets cover known topics", not unknown,
          f"{len(committed)}/{len(ALL_TOPICS)} topics"
          + (f"; QUIET (no records yet): {quiet}" if quiet else "")
          + (f"; UNKNOWN: {unknown}" if unknown else ""))

    # (1b) gapless, over the whole table so a gap on a days() partition seam is
    # not hidden by the boundary it sits on.
    rows = spark.sql(f"""
        SELECT topic, partition, count(*) AS n, min(offset) AS lo, max(offset) AS hi
        FROM {RAW_TABLE} GROUP BY topic, partition
    """).collect()
    gaps = O.offset_gaps([(r["topic"], r["partition"], r["n"], r["lo"], r["hi"]) for r in rows])
    check("offsets gapless", not gaps,
          f"{len(rows)} partitions clean" if not gaps else f"{len(gaps)}: {gaps[0]['detail']}")

    # (2) every raw row on a decodable topic became exactly one bronze row.
    for kind, table in (("trades", TRADES_TABLE), ("book", BOOK_TABLE)):
        quoted = ", ".join(f"'{t}'" for t in topics(kind))
        raw_n = spark.sql(
            f"SELECT count(*) FROM {RAW_TABLE} WHERE topic IN ({quoted}) AND schema_id IS NOT NULL"
        ).collect()[0][0]
        bronze_n = spark.sql(f"SELECT count(*) FROM {table}").collect()[0][0]
        check(f"raw == {kind}", raw_n == bronze_n, f"raw {raw_n} vs bronze {bronze_n}")

    # (3) the summary agrees with the table. Idempotency itself is asserted by
    # the caller — cycle 2 printing "no new records" is a fatal grep above, and
    # it is a more direct measurement than anything derivable from here. What
    # this adds is the other half: a summary whose total-records disagrees with
    # COUNT(*) means a commit landed rows without the bookkeeping that describes
    # them, which is the one way the offsets could be right and the lake wrong.
    claimed = int(summary.get("total-records", -1)) if summary else -1
    actual = spark.sql(f"SELECT count(*) FROM {RAW_TABLE}").collect()[0][0]
    check("summary matches table", claimed == actual,
          f"snapshot says {claimed} rows, COUNT(*) says {actual}")
finally:
    spark.stop()

if failures:
    print("\n      FAILED: " + ", ".join(failures))
    sys.exit(1)
print("\n      all checks passed")
PY

echo
echo "✓ lake-verify passed"
