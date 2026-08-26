#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Put one un-framed record on a v3 topic and prove it neither crashes the
# ingest nor reaches bronze.
#
# Proves one row in docs/architecture/failure-modes.md:
#   lake ingest / corrupt or un-framed Avro payload
#
# Unlike scripts/chaos/capture-corrupt-frame.sh — which is a SKIP, because TLS
# leaves no seam to flip a byte in a live WebSocket frame — this one is directly
# injectable: `rpk topic produce` puts arbitrary bytes on the topic, which is
# exactly what a foreign producer would do.
#
# The designed behaviour has three parts and all three are asserted:
#   1. the bytes are archived verbatim, with schema_id NULL — raw.messages is
#      the system of record and refusing to record something is not an option
#   2. bronze gains nothing from it — stage 2 filters schema_id IS NULL
#   3. the ingest exits 0 — a poison record that failed the run would block
#      every following cycle on the same offset, turning one bad record into a
#      total outage
#
# LEAVES ONE ROW BEHIND, permanently. raw.messages is never expired, so the
# junk record stays in the archive with its own offset. That is the correct
# outcome (the topic really did carry those bytes) and it is stated here rather
# than discovered later.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

SPARK=k2-spark-iceberg
LAKE=/home/iceberg/lake
TOPIC="${K2_CHAOS_TOPIC:-market.crypto.v3.trades.kraken}"
MARKER="k2-chaos-$(date -u +%s)"

preflight "$SPARK" k2-redpanda k2-lakekeeper k2-prometheus
banner "lake-corrupt-payload.sh topic=$TOPIC" \
  "none expected" docs/runbooks/lake-recovery.md \
  "produce one un-framed JSON record; the ingest must archive it and carry on"

echo "→ producing an un-framed record (magic byte 0x7b '{', not 0x00)" >&2
printf '{"chaos":"%s"}' "$MARKER" \
  | docker exec -i k2-redpanda rpk topic produce "$TOPIC" --brokers redpanda:9092 >&2

echo "→ running an ingest" >&2
start=$SECONDS
set +e
docker exec "$SPARK" python3 "$LAKE/ingest.py" | sed 's/^/    /' >&2
rc=${PIPESTATUS[0]}
set -e
t_recover=$((SECONDS - start))
[ "$rc" -eq 0 ] || die "ingest exited $rc on one un-framed record — a single bad record must not block the pipeline"

echo "→ checking where the record landed" >&2
docker exec -i "$SPARK" python3 - "$MARKER" "$TOPIC" <<'PY' >&2
import sys
sys.path.insert(0, "/home/iceberg/lake")
from ingest import RAW_TABLE, TRADES_TABLE
from spark_conf import lake_session

marker, topic = sys.argv[1], sys.argv[2]
spark = lake_session("k2-chaos-corrupt")
try:
    archived = spark.sql(f"""
        SELECT count(*) FROM {RAW_TABLE}
        WHERE topic = '{topic}' AND schema_id IS NULL
          AND cast(payload AS string) LIKE '%{marker}%'
    """).collect()[0][0]
    print(f"    raw.messages rows with schema_id NULL carrying the marker: {archived}")
    assert archived == 1, "the un-framed record was not archived verbatim — the archive dropped bytes"

    leaked = spark.sql(
        f"SELECT count(*) FROM {TRADES_TABLE} WHERE trade_id LIKE '%{marker}%'"
    ).collect()[0][0]
    print(f"    bronze.trades rows carrying the marker: {leaked}")
    assert leaked == 0, "an un-framed record reached bronze"
    print("    ok: archived verbatim, skipped by the decode, ingest exit 0")
finally:
    spark.stop()
PY

echo "  This record is now a permanent row in raw.messages. The nightly audit" >&2
echo "  counts it; it is not a failure, and offset continuity is unaffected" >&2
echo "  because the offset was consumed exactly once like any other." >&2

report "lake-corrupt-payload.sh topic=$TOPIC" "none expected" 0 "$t_recover"
