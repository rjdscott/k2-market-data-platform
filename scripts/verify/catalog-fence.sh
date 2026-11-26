#!/usr/bin/env bash
# The catalog fence's network probes (ADR-059 part 7, step S22): every `probe` row of
# tests/access/expectations.yaml (or the file named as $1) is dialled from where its `from` says,
# and passes when the TCP connection is open or refused as its `expect` says. One line per row.
# Exit 0 only when every row passed (and there is at least one), 1 otherwise, 2 on usage/no Docker.
# The dial is the one replaceable part: K2_FENCE_DIAL=<cmd> is run as `<cmd> <network> <host:port>`
# and answers by exit code: 0 connected, 1 refused or timed out, 3 name unresolvable, 4 network absent.
set -uo pipefail
cd "$(dirname "$0")/../.."
FILE=${1:-tests/access/expectations.yaml}
IMAGE='alpine@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf'  # alpine:3, busybox nc
[ -f "$FILE" ] || { echo "usage: $0 [expectations.yaml]" >&2; exit 2; }

dial() {  # $1 origin (host|k2-net|k2-catalog|...), $2 host:port
  [ -n "${K2_FENCE_DIAL:-}" ] && { $K2_FENCE_DIAL "$@"; return; }
  local h=${2%:*} p=${2##*:} net out
  if [ "$1" = host ]; then timeout 3 bash -c "exec 3<>/dev/tcp/$h/$p" 2>/dev/null; return; fi
  case "$1" in k2-net|k2-catalog) net="${PROJECT}_$1" ;; *) return 5 ;; esac
  docker network inspect "$net" >/dev/null 2>&1 || return 4
  out=$(docker run --rm --name "k2-fence-probe-$$-$RANDOM" --network "$net" --memory 32m --cpus 0.1 \
        "$IMAGE" nc -z -w 3 "$h" "$p" 2>&1) && return 0
  case "$out" in *"bad address"*) return 3 ;; *) return 1 ;; esac
}

if [ -z "${K2_FENCE_DIAL:-}" ]; then
  docker info >/dev/null 2>&1 || { echo "Docker is unreachable" >&2; exit 2; }
  PROJECT=${K2_COMPOSE_PROJECT:-$(docker inspect k2-trino --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)}
  [ -n "$PROJECT" ] || { echo "no compose project: set K2_COMPOSE_PROJECT" >&2; exit 2; }
fi

rows=$(uv run --project platform/k2gen --locked -q python -c '
import sys
from k2gen import model
for r in model.parse(sys.argv[1], open(sys.argv[1], "rb").read()):
    if "probe" in r: print(r["probe"]["from"], r["probe"]["target"], r["expect"])
' "$FILE") || { echo "cannot parse $FILE" >&2; exit 2; }

pass=0; fail=0
while read -r from target expect; do
  [ -n "$from" ] || continue
  dial "$from" "$target"; rc=$?
  case "$rc:$expect" in
    0:open)    v=PASS d="connected" ;;
    0:refused) v=FAIL d="connected, expected refused" ;;
    1:refused) v=PASS d="refused or timed out" ;;
    1:open)    v=FAIL d="no connection, expected open" ;;
    3:refused) v=PASS d="target name cannot be reached (unresolvable)" ;;
    3:open)    v=FAIL d="target name unresolvable, expected open" ;;
    4:refused) v=PASS d="network $from absent, target cannot be reached from it" ;;
    4:open)    v=FAIL d="network $from absent, expected open" ;;
    *)         v=FAIL d="origin $from cannot be dialled (exit $rc)" ;;
  esac
  [ $v = PASS ] && pass=$((pass+1)) || fail=$((fail+1))
  echo "$v $from -> $target $d"
done <<< "$rows"
echo "catalog-fence: $pass PASS, $fail FAIL"
[ "$fail" -eq 0 ] && [ "$pass" -gt 0 ]
