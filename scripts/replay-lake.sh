#!/usr/bin/env bash
# One archived connection out of raw.messages, through `k2-capture replay`,
# hashed, and the (snapshot id, conn_id, crate sha, output sha256) quadruple
# filed in audit.checks — ADR-029's reproducibility record. Run from the host
# with the stack up.
#
#   scripts/replay-lake.sh --exchange kraken --snapshot-id 8675983916383659458 \
#       --conn-id 1dfb9139-ef8d-45cb-a0d9-3c677c1560ee [--out replay.jsonl] [--depth 25] [--interval-ms 100]
#
# Prints the sha256 of the replay output. Reproducing a result six months on is
# re-running this with the same ids and comparing that one line.
set -euo pipefail
cd "$(dirname "$0")/.."

exchange=""; snapshot=""; conn=""; out=""; extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --exchange) exchange=$2; shift 2 ;;
    --snapshot-id) snapshot=$2; shift 2 ;;
    --conn-id) conn=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    --depth|--interval-ms|--until) extra+=("$1" "$2"); shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$exchange" ] && [ -n "$snapshot" ] && [ -n "$conn" ] || { echo "usage: $0 --exchange <venue> --snapshot-id <raw.messages snapshot> --conn-id <uuid> [--out f] [--depth n] [--interval-ms m] [--until ts]" >&2; exit 2; }
out=${out:-"/tmp/k2-replay-${exchange}-${conn}.jsonl"}

# --until is the export's; --depth / --interval-ms are the replay's.
export_args=(); replay_args=()
for ((i = 0; i < ${#extra[@]}; i += 2)); do
  case "${extra[i]}" in
    --until) export_args+=("${extra[i]}" "${extra[i+1]}") ;;
    *) replay_args+=("${extra[i]}" "${extra[i+1]}") ;;
  esac
done

frames=/tmp/k2-replay-${exchange}-${conn}.frames.jsonl
uv run --no-project --with duckdb==1.4.4 --with pytz --with fastavro==1.12.2 \
  python scripts/replay_export.py --exchange "$exchange" --snapshot-id "$snapshot" --conn-id "$conn" \
  --out "$frames" "${export_args[@]}"

# The runtime image: distroless, the binary is the entrypoint, the registry is
# bind-mounted exactly as compose mounts it for `run`.
docker run --rm -i -v "$PWD/config:/app/config:ro" -e RUST_LOG=warn k2-capture:v3 \
  replay --exchange "$exchange" --conn-id "$conn" --fixture - "${replay_args[@]}" < "$frames" > "$out"

sha=$(sha256sum "$out" | cut -d' ' -f1)
records=$(wc -l < "$out")
crate=$(git rev-parse --short HEAD)
echo "replayed $(wc -l < "$frames") frames -> $records records, sha256 $sha ($out)"

docker exec k2-spark-iceberg python3 /home/iceberg/lake/record_check.py \
  --job replay --check replay --scope "market.crypto.v3.raw.${exchange}/${conn}" \
  --observed "$records" \
  --detail "snapshot=${snapshot} sha256=${sha} crate=${crate} frames=$(wc -l < "$frames") args=${replay_args[*]:-default}" >/dev/null
echo "$sha"
