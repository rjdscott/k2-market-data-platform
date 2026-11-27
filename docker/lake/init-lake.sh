#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# K2 v3 — one-shot bootstrap of the Iceberg lake (MinIO bucket + Lakekeeper)
#
# Run by the `lake-init` compose service, after `lakekeeper` reports healthy.
# Idempotent: every step treats "already exists" as success, so a re-run on a
# live stack is a no-op. It creates and updates; it never deletes or recreates.
#
# Runs in the MinIO image (the project's mirror, docker-compose.yml): the only image already in this stack that
# ships BOTH `mc` (bucket create) and `curl` (Lakekeeper REST). Using it costs
# no extra pull; a curl-only image would need a second service to make the
# bucket, and `minio/mc` has no curl.
#
# Secrets: the root pair reaches mc through MC_HOST_k2 and Lakekeeper in a
# request body on curl's stdin, each engine's secret reaches mc on stdin: none is
# ever an argument (visible to `ps`) or printed.
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
# Every warehouse of the release Trino runs (its storage plan, compiled by k2gen from
# contracts/domains.yaml: `k2` and one per data domain, ADR-044), on the same bucket
# under warehouse/<name>. Read through the trino pointer, so this service mounts the
# releases root (docker-compose.yml, x-k2-releases-ro).
STORAGE_PLAN=${K2_STORAGE_PLAN:-/k2/releases/pointers/trino/compiled/lake/storage-plan.json}
BUCKET=${K2_LAKE_BUCKET:-k2-lake}
S3_ENDPOINT=${K2_S3_ENDPOINT:-http://minio:9000}
S3_REGION=${K2_S3_REGION:-local-01}
S3_PATH_STYLE=${K2_S3_PATH_STYLE:-true}
PROJECT_ID=${K2_LAKE_PROJECT_ID:-00000000-0000-0000-0000-000000000000}
# One MinIO user per engine, one policy file each (the file's name is the engine's).
# fetch has none and must never get one: it talks to the internet (ADR-059).
here=. && [[ ${BASH_SOURCE[0]} != */* ]] || here=${BASH_SOURCE[0]%/*}
POLICIES=$here/minio/policies

die() { echo "  ✗ $*" >&2; exit 1; }

# Refuse before anything changes. Each engine's pair is K2_<ENGINE>_S3_ACCESS_KEY and
# _SECRET_KEY (.env.example); a pair that is the root's, or a user two engines share,
# would hand one engine another's rights.
: "${MINIO_ROOT_USER:?set MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD:?set MINIO_ROOT_PASSWORD}"
engines=()
declare -A owner=()
for f in "$POLICIES"/*.json; do
  e=${f##*/} e=${e%.json} ak=K2_${e^^}_S3_ACCESS_KEY sk=K2_${e^^}_S3_SECRET_KEY
  [[ -n ${!ak:-} && -n ${!sk:-} ]] || die "$ak and $sk must both be set: $e gets its own MinIO user"
  [[ ${!ak} != "$MINIO_ROOT_USER" ]] || die "$ak equals the root user MINIO_ROOT_USER"
  [[ ${!sk} != "$MINIO_ROOT_PASSWORD" ]] || die "$sk equals the root password MINIO_ROOT_PASSWORD"
  [[ -z ${owner[${!ak}]:-} ]] || die "$ak is also ${owner[${!ak}]}'s access key: one user per engine"
  owner[${!ak}]=$ak engines+=("$e")
done
[[ ${#engines[@]} -gt 0 ]] || die "no policy under $POLICIES"

# mc takes an alias from MC_HOST_<alias>, so the root pair is never on mc's argv (the
# spike lib does the same, scripts/spikes/2026-11-26/platform_lib.py). URL-encoded, so a
# password holding @ : or / still parses.
# ponytail: no python in this image; every byte but the unreserved ones is escaped.
urlenc() {
  local LC_ALL=C s=$1 i c out=
  for ((i = 0; i < ${#s}; i++)); do
    c=${s:i:1}
    case $c in [A-Za-z0-9._~-]) out+=$c ;; *) printf -v c '%%%02X' "'$c"; out+=$c ;; esac
  done
  printf '%s' "$out"
}
MC_HOST_k2="${S3_ENDPOINT%%://*}://$(urlenc "$MINIO_ROOT_USER"):$(urlenc "$MINIO_ROOT_PASSWORD")@${S3_ENDPOINT#*://}"
export MC_HOST_k2

# POST the body on stdin to $1; succeed on 2xx or on any status listed in $2.
# The body goes by stdin, not -d: it carries the root pair.
# ponytail: no jq in this image and none needed — we only ever branch on the code.
post() {
  local url=$1 tolerate=${2:-} code
  code=$(curl -sS -o /tmp/resp -w '%{http_code}' -X POST "$url" \
           -H 'content-type: application/json' --data-binary @-)
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

echo "[1/5] MinIO bucket"
mc mb --ignore-existing "k2/$BUCKET"

# 4xx here means "already bootstrapped" — the endpoint closes after first use.
echo "[2/5] Lakekeeper bootstrap"
printf '%s' '{"accept-terms-of-use":true}' | post "$LK/management/v1/bootstrap" "400 401 403 409"

# lookup <name>: WH_ID and WH_SIGNING (absent, on or off) of warehouse <name>, from
# Lakekeeper's list. Each entry starts at its "warehouse-id" (GetWarehouseResponse in
# v0.13.9's management API: warehouse-id, then name, then storage-profile), so the
# text up to the next "warehouse-id" is that warehouse's. A profile without the field
# signs: Lakekeeper's S3 profile defaults remote-signing-enabled to true.
#
# ponytail: bash glob match, not grep/jq — this image ships mc, curl and bash and
# NOT grep (verified: `grep: command not found` from this very line, 2026-08-26).
lookup() {
  local name=$1 rest entry
  WH_ID="" WH_SIGNING=absent
  rest=$(curl -fsS "$LK/management/v1/warehouse?project-id=$PROJECT_ID")
  while [[ $rest == *'"warehouse-id":"'* ]]; do
    rest=${rest#*\"warehouse-id\":\"}
    entry=${rest%%\"warehouse-id\":\"*}
    [[ $entry == *"\"name\":\"$name\""* ]] || continue
    WH_ID=${entry%%\"*} WH_SIGNING=on
    [[ $entry != *'"remote-signing-enabled":false'* ]] || WH_SIGNING=off
    return 0
  done
}

# storage <name>: the storage profile and credential of warehouse <name>, bucket
# $BUCKET, key prefix warehouse/<name>, remote signing off.
#
# Remote signing off is what makes an engine's own key mean anything: while it is on,
# Lakekeeper signs every storage request with this credential (the root pair), and a
# Spark key with no permission at all still expired and deleted
# (docs/research/2026-11-26-release-spine-spikes.md (b), lines 135-141; the unsigned
# phase set this field, on the Lakekeeper pinned here, v0.13.9, line 114).
storage() {
  cat <<JSON
  "storage-profile": {
    "type": "s3",
    "bucket": "$BUCKET",
    "key-prefix": "warehouse/$1",
    "endpoint": "$S3_ENDPOINT",
    "region": "$S3_REGION",
    "path-style-access": $S3_PATH_STYLE,
    "flavor": "s3-compat",
    "sts-enabled": false,
    "remote-signing-enabled": false
  },
  "storage-credential": {
    "type": "s3",
    "credential-type": "access-key",
    "aws-access-key-id": "$MINIO_ROOT_USER",
    "aws-secret-access-key": "$MINIO_ROOT_PASSWORD"
  }
JSON
}

# ensure_warehouse <name>: create it with remote signing off, or turn signing off on
# the warehouse that exists, then read it back. Asking first keeps a create that does
# not return 201 fatal (an old version tolerated any 400, which swallowed a malformed
# body under "✓ lake ready").
#
# The update is POST /management/v1/warehouse/<id>/storage (update_storage_profile,
# lakekeeper v0.13.9 management API): bucket and key prefix are immutable there, and
# the credential is required, since an update without one leaves the warehouse with
# none (the old one is not reused).
ensure_warehouse() {
  local name=$1
  echo "  $name"
  lookup "$name"
  case $WH_SIGNING in
    absent)
      printf '{"warehouse-name": "%s", "project-id": "%s",\n%s}' "$name" "$PROJECT_ID" "$(storage "$name")" \
        | post "$LK/management/v1/warehouse" ;;  # no tolerated codes: 2xx or fail
    on)
      echo "  → exists with remote signing on: turning it off"
      printf '{\n%s}' "$(storage "$name")" | post "$LK/management/v1/warehouse/$WH_ID/storage" ;;
    off)
      echo "  → exists, remote signing off, skipping" ;;
  esac
  lookup "$name"
  [[ $WH_SIGNING == off ]] || die "warehouse $name still signs (remote signing: $WH_SIGNING)"
}

echo "[3/5] Warehouse '$WAREHOUSE'"
ensure_warehouse "$WAREHOUSE"

# After the lake's own warehouse, so a failure here cannot leave it half
# made. It still exits 1, and lake-init failing holds back what waits for it
# (trino): docker/trino/README.md says what to do.
echo "[4/5] Domain warehouses ($STORAGE_PLAN)"
[[ -f $STORAGE_PLAN ]] || die "no storage plan at $STORAGE_PLAN: is the releases root mounted and a release deployed?"
# ponytail: no jq or python in this image; every "name" in the plan is a warehouse's
# (storage-plan.schema.json: namespaces and prefixes are bare strings).
plan=$(<"$STORAGE_PLAN") domains=()
re='"landing"[[:space:]]*:[[:space:]]*\{[^}]*"bucket"[[:space:]]*:[[:space:]]*"([^"]+)"'
[[ $plan =~ $re ]] || die "$STORAGE_PLAN names no landing bucket"
LANDING=${BASH_REMATCH[1]}
# A plain bucket: no object lock, no versioning. A landing is immutable because the
# connector writes it by conditional PUT, and maintenance expires landings after 14
# days, which a lock would block.
mc mb --ignore-existing "k2/$LANDING"
re='"name"[[:space:]]*:[[:space:]]*"([^"]+)"'
while [[ $plan =~ $re ]]; do
  [[ ${BASH_REMATCH[1]} == "$WAREHOUSE" ]] || domains+=("${BASH_REMATCH[1]}")
  plan=${plan#*"${BASH_REMATCH[0]}"}
done
[[ ${#domains[@]} -gt 0 ]] || die "$STORAGE_PLAN names no domain warehouse"
for w in "${domains[@]}"; do
  ensure_warehouse "$w"
done

# render <engine>: its policy with this lake's names: @LAKE@ the bucket, @LANDING@ the
# plan's landing bucket, @K2@ the lake's warehouse, and every string holding @DOMAIN@
# once per domain warehouse of the plan, so a new domain is covered on the next run.
render() {
  local doc each d re='"([^"]*)@DOMAIN@([^"]*)"'
  doc=$(<"$POLICIES/$1.json")
  doc=${doc//@LAKE@/"$BUCKET"} doc=${doc//@LANDING@/"$LANDING"} doc=${doc//@K2@/"$WAREHOUSE"}
  while [[ $doc =~ $re ]]; do
    each=""
    for d in "${domains[@]}"; do each+="${each:+, }\"${BASH_REMATCH[1]}$d${BASH_REMATCH[2]}\""; done
    doc=${doc/"${BASH_REMATCH[0]}"/"$each"}
  done
  printf '%s\n' "$doc"
}

# One user per engine holding exactly its policy. `policy create` and `user add`
# overwrite, so a rerun converges on the files and the environment; attach runs only
# for a user with no policy yet, and a user holding anything else is refused, not
# trimmed (someone widened it by hand).
echo "[5/5] Engine users (${engines[*]}; fetch none)"
for e in "${engines[@]}"; do
  ak=K2_${e^^}_S3_ACCESS_KEY sk=K2_${e^^}_S3_SECRET_KEY policy=k2-$e
  render "$e" > "/tmp/$policy.json"
  mc admin policy create k2 "$policy" "/tmp/$policy.json" > /dev/null
  printf '%s\n' "${!sk}" | mc admin user add k2 "${!ak}" > /dev/null   # the secret on stdin (spike (b))
  info=$(mc admin user info k2 "${!ak}" --json)
  re='"policyName"[[:space:]]*:[[:space:]]*"([^"]*)"'
  if [[ $info =~ $re ]]; then
    [[ ${BASH_REMATCH[1]} == "$policy" ]] || die "$e's user (\$$ak) holds ${BASH_REMATCH[1]}, not only $policy: detach the rest"
  else
    mc admin policy attach k2 "$policy" --user "${!ak}" > /dev/null
  fi
  echo "  $e → $policy"
done

echo "✓ lake ready: catalog $LK/catalog, warehouse $WAREHOUSE, domain warehouses ${domains[*]}, remote signing off; users ${engines[*]}"
