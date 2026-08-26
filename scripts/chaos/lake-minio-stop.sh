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
# disk problem, and `remove_orphan_files` is what clears them. This script
# measures whether the row count is unchanged AND reports the orphan bytes.
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

before=$(rows)
echo "→ before: $before rows in raw.messages" >&2

echo "→ stopping k2-minio" >&2
docker stop k2-minio >/dev/null
trap 'docker start k2-minio >/dev/null 2>&1 || true' EXIT

set +e
docker exec "$SPARK" python3 "$LAKE/ingest.py" >/tmp/chaos-minio.log 2>&1
rc=$?
set -e
echo "→ ingest exited $rc with the object store down (expected non-zero)" >&2
[ "$rc" -ne 0 ] || die "ingest exited 0 with MinIO stopped — it wrote nowhere and said nothing"
grep -aiE "s3|connect|refused|minio" /tmp/chaos-minio.log | tail -3 | sed 's/^/    /' >&2

echo "→ starting k2-minio" >&2
docker start k2-minio >/dev/null
trap - EXIT
sleep 20

after=$(rows)
[ "$before" = "$after" ] || die "row count moved across the failed run: $before -> $after"
echo "→ raw.messages unchanged at $after rows — no partial commit" >&2

echo "→ orphaned data files left behind by the failed write:" >&2
docker exec k2-minio sh -c \
  'mc alias set k2 "$K2_S3_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 \
   || mc alias set k2 http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; \
   mc du k2/k2-lake' 2>/dev/null | sed 's/^/    /' >&2
echo "  Files with no manifest referencing them are invisible to every reader and" >&2
echo "  cost only disk. CALL lake.system.remove_orphan_files clears them — the" >&2
echo "  runbook has the command and the reason it is not on the nightly path." >&2

echo "→ recovery: one ingest with the object store back" >&2
start=$SECONDS
docker exec "$SPARK" python3 "$LAKE/ingest.py" | sed 's/^/    /' >&2
t_recover=$((SECONDS - start))

report "lake-minio-stop.sh" LakeIngestFailed 0 "$t_recover"
