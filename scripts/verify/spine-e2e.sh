#!/usr/bin/env bash
# The release and rules spine end to end (make spine-e2e): what the orchestrator runs after every merge.
# From a scratch clone of the current tree (tracked and untracked files, nothing ignored) at a scratch
# tag: a release compiled by k2gen, then each stage below, one line per stage and the total time.
#
#   release        k2gen docset and release of the scratch tag (placeholder image digests: never deployed)
#   access-verify  make access-verify RELEASE=<it>: every tests/access/expectations.yaml row, own lake
#   test-access    make test-access RELEASE=<it>: the proofs behind the suite (tests/access/prove.sh), own lake
#   rules          on ONE throwaway lake: scripts/trino_rules_test.py compile, suite, negative, control,
#                  scripts/trino_rules_c1_test.py and scripts/verify/identity.sh --throwaway
#   rehearsal      make deploy-rehearsal: the deploy chain on compose project k2-rehearsal
#
# Exit 0 when every stage passed. Logs in a directory under $HOME, removed at the end unless KEEP=1.
# ONLY="<stage> ..." runs the named stages alone, after the release they read.
# Never touches the running stack: every stage is a throwaway lake or the k2-rehearsal project.
set -uo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD
W=$(mktemp -d "$HOME/k2-spine-e2e.XXXXXX")  # under $HOME, the one place Docker Desktop shares
L=scripts/lib/throwaway-lake.sh
P=spine-e2e
cleanup() { bash "$L" down "$P" >/dev/null 2>&1; [ "${KEEP:-}" = 1 ] && echo "spine-e2e: logs in $W" || { chmod -R u+w "$W"; rm -rf "$W"; }; }
trap cleanup EXIT
T0=$(date +%s) failed=0
stage() {  # stage <name> <command...>: run it, its output to $W/<name>.log, one line
  local name=$1 t; shift; t=$(date +%s)
  case " ${ONLY:-$name} release " in *" $name "*) ;; *) return 0 ;; esac  # ONLY="rules rehearsal": those stages (and the release they need)
  if "$@" > "$W/$name.log" 2>&1; then r=PASS; else r=FAIL; failed=1; fi
  printf '%s %-14s %5ss  %s\n' "$r" "$name" $(( $(date +%s) - t )) "$(grep -v '^$' "$W/$name.log" | tail -1 | cut -c1-110)"
}

release() {
  mkdir -p "$W/clone" "$W/releases"
  git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$W/clone"
  git -C "$W/clone" init -q -b main
  git -C "$W/clone" add -A
  git -C "$W/clone" -c user.name=spine-e2e -c user.email=spine-e2e@example.org commit -q -m "spine-e2e: the current tree"
  git -C "$W/clone" -c user.name=spine-e2e -c user.email=spine-e2e@example.org tag -a v0.0.0-e2e.1 -m "spine-e2e"
  python3 - "$W/clone/docker-compose.yml" > "$W/images.json" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "spine-e2e/placeholder@sha256:" + "0" * 64 for n in names}))
PY
  local k2gen=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
  "${k2gen[@]}" docset --tag v0.0.0-e2e.1 --repo "$W/clone" --out "$W/set" &&
    "${k2gen[@]}" release --set "$W/set" --previous none --images "$W/images.json" --out "$W/releases" | tail -1 > "$W/id" &&
    echo "release $(cat "$W/id")"
}

rules() {  # trino-rules-test.sh's lake, with the review's C1 rows on it before identity
  bash "$L" down "$P" >/dev/null
  local state id rc=0
  state=$(bash "$L" state "$P")
  id=$(bash "$L" py "$P" scripts/trino_rules_test.py compile /work/build | tail -1)
  bash "$L" up "$P" "$state/build/releases/$id" p.nobody svcxplatform pxanalyst-full k2_release_000000000000 || return 1
  for s in suite negative control; do bash "$L" py "$P" scripts/trino_rules_test.py "$s" || rc=1; done
  bash "$L" py "$P" scripts/trino_rules_c1_test.py || rc=1
  bash scripts/verify/identity.sh --throwaway "$P" || rc=1
  bash "$L" down "$P" >/dev/null
  echo "rules: suite, negative, control, c1 and identity on one lake: exit $rc"
  return $rc
}

stage release release
REL=$W/releases/$(cat "$W/id" 2>/dev/null)
stage access-verify make -s access-verify RELEASE="$REL" PREFIX=spine-e2e-av OUT="$W/access-verify.json"
stage test-access make -s test-access RELEASE="$REL"
stage rules rules
stage rehearsal make -s deploy-rehearsal
echo "spine-e2e: $([ $failed = 0 ] && echo PASS || echo FAIL) in $(( $(date +%s) - T0 )) s"
exit $failed
