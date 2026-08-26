#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Stop the object store under a running ingest.
#
# Proves one row in docs/architecture/failure-modes.md:
#   MinIO / down
#
# Different from the Lakekeeper row and worth its own script. With the catalog
# down the commit cannot even be attempted; with MinIO down the job writes
# Parquet, fails partway, and the commit never happens — so the S3 prefix is
# left holding orphaned data files that no snapshot references. Those are not a
# correctness problem (nothing reads a file no manifest names) but they are a
# disk problem, and `remove_orphan_files` — on the nightly path in
# docker/lake/maintenance.py, with a 24-hour floor — is what clears them.
#
# The orphan number is a DIFFERENCE across the failed write, not `mc du` after
# it. `mc du` reports what the bucket holds; printing that under the heading
# "orphaned data files left behind" labels the whole lake as garbage. Bucket
# size before minus bucket size after the failed run is the number that means
# what the heading says.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

SPARK=k2-spark-iceberg
LAKE=/home/iceberg/lake

preflight "$SPARK" k2-minio k2-lakekeeper k2-prometheus
banner "lake-minio-stop.sh" \
  LakeIngestFailed docs/runbooks/lake-recovery.md \
  "docker stop k2-minio, then run an ingest that must fail without committing"

rows() {
  docker exec -i "$SPARK" python3 - <<'PY' 2>/dev/null | tail -1
import sys
sys.path.insert(0, "/home/iceberg/lake")
from ingest import RAW_TABLE
from spark_conf import lake_session
spark = lake_session("k2-chaos-rows")
try:
    print(spark.sql(f"SELECT count(*) FROM {RAW_TABLE}").collect()[0][0])
finally:
    spark.stop()
PY
}

# bucket_bytes -> total bytes under the lake bucket, as an integer.
# `mc` runs inside k2-minio against its own endpoint: K2_S3_ENDPOINT is set on
# the Spark and lake services, NOT in the MinIO container, so the original
# `mc alias set k2 "$K2_S3_ENDPOINT"` there always fell through to the
# hard-coded fallback — under a `2>/dev/null` that hid it either way.
MC_ENDPOINT="${K2_CHAOS_MC_ENDPOINT:-http://localhost:9000}"
bucket_bytes() {
  docker exec k2-minio sh -c \
    "mc alias set k2 '$MC_ENDPOINT' \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null \
     && mc du --json k2/k2-lake" \
    | python3 -c 'import json,sys; print(sum(json.loads(l)["size"] for l in sys.stdin if l.strip()))'
}

pause_lake_ingest
trap 'docker start k2-minio >/dev/null 2>&1 || true; resume_lake_ingest' EXIT

before=$(rows)
bytes_before=$(bucket_bytes) || die "could not read the bucket size from k2-minio — check MC_ENDPOINT"
echo "→ before: $before rows in raw.messages, $bytes_before bytes in k2/k2-lake" >&2

echo "→ stopping k2-minio" >&2
docker stop k2-minio >/dev/null

set +e
docker exec "$SPARK" python3 "$LAKE/ingest.py" >/tmp/chaos-minio.log 2>&1
rc=$?
set -e
echo "→ ingest exited $rc with the object store down (expected non-zero)" >&2
[ "$rc" -ne 0 ] || die "ingest exited 0 with MinIO stopped — it wrote nowhere and said nothing"
grep -aiE "s3|connect|refused|minio" /tmp/chaos-minio.log | tail -3 | sed 's/^/    /' >&2

echo "→ starting k2-minio" >&2
docker start k2-minio >/dev/null
sleep 20

after=$(rows)
[ "$before" = "$after" ] || die "row count moved across the failed run: $before -> $after"
echo "→ raw.messages unchanged at $after rows — no partial commit" >&2

bytes_after=$(bucket_bytes)
orphan_bytes=$((bytes_after - bytes_before))
echo "→ bucket bytes across the failed write: $bytes_before -> $bytes_after" >&2
echo "→ orphaned by this run: $orphan_bytes bytes" >&2
echo "  Zero is a legitimate answer — MinIO may have refused the very first PUT." >&2
echo "  Files with no manifest referencing them are invisible to every reader and" >&2
echo "  cost only disk. remove_orphan_files runs nightly with a 24 h floor, so the" >&2
echo "  first nightly run AFTER these turn 24 h old clears them. Nothing clears them" >&2
echo "  sooner: Iceberg 1.8.1 refuses --orphan-hours below 24, so a hand-run" >&2
echo "  maintenance pass reclaims nothing this outage just created." >&2

echo "→ recovery: one ingest with the object store back" >&2
start=$SECONDS
docker exec "$SPARK" python3 "$LAKE/ingest.py" | sed 's/^/    /' >&2
t_recover=$((SECONDS - start))

report "lake-minio-stop.sh" LakeIngestFailed 0 "$t_recover"
