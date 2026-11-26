#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# K2 v3 — one-shot bootstrap of the Iceberg lake (MinIO bucket + Lakekeeper)
#
# Run by the `lake-init` compose service, after `lakekeeper` reports healthy.
# Idempotent: every step treats "already exists" as success, so a re-run on a
# live stack is a no-op.
#
# Runs in the `minio/minio` image — the only image already in this stack that
# ships BOTH `mc` (bucket create) and `curl` (Lakekeeper REST). Using it costs
# no extra pull; a curl-only image would need a second service to make the
# bucket, and `minio/mc` has no curl.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

# Env-driven with today's single-host values as defaults, matching
# docker/lake/spark_conf.py name for name and default for default. Phase D made
# these variables rather than constants because
# docs/research/2026-08-26-v3-requirements-clarification.md Q9 requires that
# pointing this lake at real S3 and a hosted catalog be a change to the
# environment and nothing else. The defaults are what keeps that free: set
# nothing and this is the same script it was.
#
# Change a default here and you must change it in spark_conf.py too — this
# script creates what those jobs connect to.
LK=${K2_LAKEKEEPER_URL:-http://lakekeeper:8181}
WAREHOUSE=${K2_LAKE_WAREHOUSE:-k2}
# One warehouse per data domain for its marts (ADR-044), on the same bucket under
# warehouse/<name>. The list is generated from contracts/domains.yaml by
# `make generate`, which writes the matching Trino catalogs too; never edit it here.
DOMAIN_WAREHOUSES="$(dirname "$0")/generated/warehouses.txt"
BUCKET=${K2_LAKE_BUCKET:-k2-lake}
S3_ENDPOINT=${K2_S3_ENDPOINT:-http://minio:9000}
S3_REGION=${K2_S3_REGION:-local-01}
S3_PATH_STYLE=${K2_S3_PATH_STYLE:-true}
PROJECT_ID=${K2_LAKE_PROJECT_ID:-00000000-0000-0000-0000-000000000000}

# POST $1 with body $2; succeed on 2xx or on any status listed in $3.
# ponytail: no jq in this image and none needed — we only ever branch on the code.
post() {
  local url=$1 body=$2 tolerate=${3:-} code
  code=$(curl -sS -o /tmp/resp -w '%{http_code}' -X POST "$url" \
           -H 'content-type: application/json' -d "$body")
  if [[ $code == 2* ]] || [[ " $tolerate " == *" $code "* ]]; then
    echo "  → $code $(head -c 200 /tmp/resp)"
    return 0
  fi
  echo "  ✗ $code $(cat /tmp/resp)" >&2
  return 1
}

echo "=========================================="
echo "K2 lake bootstrap"
echo "  catalog:   $LK"
echo "  warehouse: $WAREHOUSE"
echo "  bucket:    s3://$BUCKET"
echo "=========================================="

echo "[1/4] MinIO bucket"
mc alias set k2 "$S3_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" > /dev/null
mc mb --ignore-existing "k2/$BUCKET"

# 4xx here means "already bootstrapped" — the endpoint closes after first use.
echo "[2/4] Lakekeeper bootstrap"
post "$LK/management/v1/bootstrap" '{"accept-terms-of-use":true}' "400 401 403 409"

# Ask first, then create. The old version tolerated ANY 400 on create, on the
# grounds that a rerun answers 400 CreateWarehouseStorageProfileOverlap — but
# that also swallowed a 400 from a malformed body, leaving "✓ lake ready" printed
# over a warehouse that was never created. Listing first splits the two cases:
# already-there is a skip, and a create that does not return 201 is now fatal.
#
# ponytail: bash glob match, not grep/jq — this image ships mc, curl and bash and
# NOT grep (verified: `grep: command not found` from this very line, 2026-08-26).
# ensure_warehouse <name>: bucket $BUCKET, key prefix warehouse/<name>.
ensure_warehouse() {
  local name=$1 existing
  echo "  $name"
  existing=$(curl -fsS "$LK/management/v1/warehouse?project-id=$PROJECT_ID")
  if [[ $existing == *"\"name\":\"$name\""* ]]; then
    echo "  → exists, skipping create"
    return 0
  fi
  post "$LK/management/v1/warehouse" "$(cat <<JSON
{
  "warehouse-name": "$name",
  "project-id": "$PROJECT_ID",
  "storage-profile": {
    "type": "s3",
    "bucket": "$BUCKET",
    "key-prefix": "warehouse/$name",
    "endpoint": "$S3_ENDPOINT",
    "region": "$S3_REGION",
    "path-style-access": $S3_PATH_STYLE,
    "flavor": "s3-compat",
    "sts-enabled": false
  },
  "storage-credential": {
    "type": "s3",
    "credential-type": "access-key",
    "aws-access-key-id": "$MINIO_ROOT_USER",
    "aws-secret-access-key": "$MINIO_ROOT_PASSWORD"
  }
}
JSON
)"   # no tolerated codes: 2xx or fail
}

echo "[3/4] Warehouse '$WAREHOUSE'"
ensure_warehouse "$WAREHOUSE"

# Last, after the lake's own warehouse, so a failure here cannot leave it half
# made. It still exits 1, and lake-init failing holds back what waits for it
# (trino): docker/trino/README.md says what to do.
echo "[4/4] Domain warehouses ($DOMAIN_WAREHOUSES)"
domains=()
# `|| [[ -n $w ]]`: a last line with no newline is still read (the generator writes one).
while read -r w || [[ -n $w ]]; do
  [[ -z $w || $w == \#* ]] || domains+=("$w")
done < "$DOMAIN_WAREHOUSES"
for w in "${domains[@]}"; do
  ensure_warehouse "$w"
done

echo "✓ lake ready: catalog $LK/catalog, warehouse $WAREHOUSE, domain warehouses ${domains[*]}"
