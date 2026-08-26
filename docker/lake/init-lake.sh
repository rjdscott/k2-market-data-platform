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

LK="${LAKEKEEPER_URL:-http://lakekeeper:8181}"
WAREHOUSE="${LAKE_WAREHOUSE:-k2}"
BUCKET="${LAKE_BUCKET:-k2-lake}"

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
mc alias set k2 http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" > /dev/null
mc mb --ignore-existing "k2/$BUCKET"

# 4xx here means "already bootstrapped" — the endpoint closes after first use.
echo "[2/4] Lakekeeper bootstrap"
post "$LK/management/v1/bootstrap" '{"accept-terms-of-use":true}' "400 401 403 409"

echo "[3/4] Warehouse '$WAREHOUSE'"
post "$LK/management/v1/warehouse" "$(cat <<JSON
{
  "warehouse-name": "$WAREHOUSE",
  "project-id": "00000000-0000-0000-0000-000000000000",
  "storage-profile": {
    "type": "s3",
    "bucket": "$BUCKET",
    "key-prefix": "warehouse/$WAREHOUSE",
    "endpoint": "http://minio:9000",
    "region": "local-01",
    "path-style-access": true,
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
)" "400 409"

# Namespaces over the REST catalog API rather than a Spark `lake-ddl` one-shot:
# three HTTP calls against a service that is already up, versus a ~20 s JVM
# start. Table DDL arrives in Phase C and can pay for a JVM then.
#
# The catalog path prefix is the warehouse UUID, not its name — Lakekeeper hands
# it out as `defaults.prefix` from the config endpoint, which is exactly what the
# Iceberg REST client asks for on connect. Using the name gives a 400
# WarehouseIdIsNotUUID.
#
# ponytail: bash parameter expansion, not jq/grep/sed — this image ships mc, curl
# and coreutils only. `prefix` is a flat string field, so the two trims below are
# the whole parser. If a second field is ever needed, switch images rather than
# growing this.
echo "[4/4] Namespaces"
cfg=$(curl -fsS "$LK/catalog/v1/config?warehouse=$WAREHOUSE")
prefix=${cfg#*\"prefix\":\"}
prefix=${prefix%%\"*}
[[ -n $prefix && $prefix != "$cfg" ]] || {
  echo "✗ no defaults.prefix in config response: $cfg" >&2; exit 1; }
echo "  prefix: $prefix"
for ns in raw bronze audit; do
  echo "  $ns"
  post "$LK/catalog/v1/$prefix/namespaces" "{\"namespace\":[\"$ns\"]}" "409"
done

echo "✓ lake ready — catalog $LK/catalog, warehouse $WAREHOUSE, namespaces raw/bronze/audit"
