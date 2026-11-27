#!/usr/bin/env bash
# The catalog fence on the running stack (ADR-059 part 7, step S22), one line per check.
# Structure: the project's Lakekeeper container publishes no port and is on <project>_k2-catalog
# alone, and every container on that network is this project's service named in
# tests/access/catalog-fence-allow.txt. Probes: every `probe` row of tests/access/expectations.yaml
# (or the file named as $1) is dialled from where its `from` says (`fetch`: a container on
# <project>_k2-egress, the network the fetch one-shot runs on), and passes when the TCP
# connection is open or refused as its `expect` says. A name that does not resolve counts as
# refused only when the structure passed; a missing network or a probe container that could not
# start fails. Exit 0 only when every line passed (and a probe did), 1 otherwise, 2 on usage/no Docker.
# Project: $K2_COMPOSE_PROJECT, else k2-trino's compose label. Two replaceable parts, for tests:
#   K2_FENCE_DIAL=<cmd>     run as `<cmd> <origin> <host:port>`; answers by exit code: 0 connected,
#                           1 refused or timed out, 3 name unresolvable, 4 network absent,
#                           125-127 the probe container could not start
#   K2_FENCE_INSPECT=<cmd>  run as `<cmd> lakekeeper` (prints `ports <p>,...` and `networks <n>,...`)
#                           and `<cmd> members` (`<container> <project>/<service>` per member);
#                           exits non-zero when there is no such container or network
set -uo pipefail
cd "$(dirname "$0")/../.."
FILE=${1:-tests/access/expectations.yaml}
ALLOW=$(sed 's/#.*//' tests/access/catalog-fence-allow.txt | xargs)
IMAGE='alpine@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf'  # alpine:3, busybox nc
[ -f "$FILE" ] || { echo "usage: $0 [expectations.yaml]" >&2; exit 2; }

dial() {  # $1 origin (host|k2-net|k2-catalog|fetch|...), $2 host:port
  [ -n "${K2_FENCE_DIAL:-}" ] && { $K2_FENCE_DIAL "$@"; return; }
  local h=${2%:*} p=${2##*:} net out rc
  if [ "$1" = host ]; then
    timeout 3 bash -c "exec 3<>/dev/tcp/$h/$p" 2>/dev/null || return 1
    # The host's loopback is shared by every compose project on it. A listener that only ANOTHER
    # project's containers publish (the live stack beside a rehearsal) is not this stack's port: 6.
    local owners
    owners=$(docker ps --filter "publish=$p" --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null | sort -u | xargs)
    [ -n "$owners" ] && case " $owners " in *" $PROJECT "*) ;; *) FOREIGN=$owners; return 6 ;; esac
    return 0
  fi
  case "$1" in k2-net|k2-catalog) net="${PROJECT}_$1" ;; fetch) net="${PROJECT}_k2-egress" ;; *) return 5 ;; esac
  docker network inspect "$net" >/dev/null 2>&1 || return 4
  out=$(docker run --rm --name "k2-fence-probe-$$-$RANDOM" --network "$net" --memory 32m --cpus 0.1 \
        "$IMAGE" nc -z -w 3 "$h" "$p" 2>&1); rc=$?
  [ "$rc" = 1 ] && case "$out" in *"bad address"*) return 3 ;; esac
  return "$rc"  # nc's 0 or 1, or docker's own 125-127
}

inspect() {  # $1 lakekeeper|members: what Docker says, in the K2_FENCE_INSPECT format
  [ -n "${K2_FENCE_INSPECT:-}" ] && { $K2_FENCE_INSPECT "$1"; return; }
  local ids
  if [ "$1" = lakekeeper ]; then
    ids=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=lakekeeper)
    [ -n "$ids" ] || return 1
    docker inspect $ids --format 'ports {{range $p, $b := .NetworkSettings.Ports}}{{if $b}}{{$p}},{{end}}{{end}}{{range $p, $b := .HostConfig.PortBindings}}{{$p}},{{end}}
networks {{range $n, $v := .NetworkSettings.Networks}}{{$n}},{{end}}'
  else
    ids=$(docker network inspect "${PROJECT}_k2-catalog" --format '{{range $id, $c := .Containers}}{{$id}} {{end}}') || return 1
    [ -z "$ids" ] || docker inspect $ids --format \
      '{{.Name}} {{index .Config.Labels "com.docker.compose.project"}}/{{index .Config.Labels "com.docker.compose.service"}}'
  fi
}

PROJECT=${K2_COMPOSE_PROJECT:-} FOREIGN=another
if [ -z "${K2_FENCE_DIAL:-}" ] || [ -z "${K2_FENCE_INSPECT:-}" ]; then
  docker info >/dev/null 2>&1 || { echo "Docker is unreachable" >&2; exit 2; }
  PROJECT=${PROJECT:-$(docker inspect k2-trino --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)}
fi
[ -n "$PROJECT" ] || { echo "no compose project: set K2_COMPOSE_PROJECT" >&2; exit 2; }
NET="${PROJECT}_k2-catalog"

rows=$(uv run --project platform/k2gen --locked -q python -c '
import sys
from k2gen import model
for r in model.parse(sys.argv[1], open(sys.argv[1], "rb").read())["expectations"]:
    if "probe" in r: print(r["probe"]["from"], r["probe"]["target"], r["expect"])
' "$FILE") || { echo "cannot parse $FILE" >&2; exit 2; }

pass=0; fail=0
say() { if [ "$1" = PASS ]; then pass=$((pass+1)); else fail=$((fail+1)); fi; echo "$*"; }

if lk=$(inspect lakekeeper); then
  ports=$(sed -n 's/^ports //p' <<< "$lk" | tr -d '\n'); nets=$(sed -n 's/^networks //p' <<< "$lk" | tr -d '\n')
  if [ -z "$ports" ]; then say PASS structure lakekeeper publishes no port
  else say FAIL "structure lakekeeper publishes ${ports%,}: the catalog has no authentication"; fi
  if [ "$nets" = "$NET," ]; then say PASS "structure lakekeeper is on $NET only"
  else say FAIL "structure lakekeeper is on ${nets%,}, expected $NET only"; fi
else
  say FAIL "structure no running lakekeeper container in project $PROJECT"
fi
if members=$(inspect members); then
  off=$(while read -r name who; do
          [ -n "$name" ] || continue
          case " $ALLOW " in *" ${who#"$PROJECT"/} "*) [ "${who%%/*}" = "$PROJECT" ] && continue ;; esac
          printf '%s ' "${name#/}=$who"
        done <<< "$members")
  if [ -z "$off" ]; then say PASS "structure $NET holds only catalog-fence-allow.txt services"
  else say FAIL "structure $NET holds containers off catalog-fence-allow.txt: ${off% }"; fi
else
  say FAIL "structure cannot list the members of $NET (no such network)"
fi
structure=$fail probes=0

while read -r from target expect; do
  [ -n "$from" ] || continue
  probes=$((probes+1)); dial "$from" "$target"; rc=$?
  case "$rc:$expect" in
    0:open)    v=PASS d="connected" ;;
    0:refused) v=FAIL d="connected, expected refused" ;;
    1:refused) v=PASS d="refused or timed out" ;;
    1:open)    v=FAIL d="no connection, expected open" ;;
    3:refused) if [ "$structure" -eq 0 ]; then v=PASS d="refused (name does not resolve on $from)"
               else v=FAIL d="name does not resolve on $from, which proves nothing while the structure fails"; fi ;;
    3:open)    v=FAIL d="target name unresolvable, expected open" ;;
    6:refused) v=PASS d="connected, but only project $FOREIGN publishes that port, not $PROJECT" ;;
    6:open)    v=FAIL d="only project $FOREIGN publishes that port, expected open on $PROJECT" ;;
    4:*)       v=FAIL d="network $from absent: nothing was dialled" ;;
    12[567]:*) v=FAIL d="the probe container could not start (docker exit $rc)" ;;
    *)         v=FAIL d="origin $from cannot be dialled (exit $rc)" ;;
  esac
  say $v "$from -> $target $d"
done <<< "$rows"
echo "catalog-fence: $pass PASS, $fail FAIL"
[ "$fail" -eq 0 ] && [ "$probes" -gt 0 ]
