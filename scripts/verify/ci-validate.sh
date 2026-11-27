#!/usr/bin/env bash
# CI's validation runner (plan 001, "The validation runner in CI"), runnable locally:
#   bash scripts/verify/ci-validate.sh <out dir> [--diff]
# From a scratch clone of the current tree (tracked and untracked files) at a scratch annotated tag:
# k2gen docset, k2gen check, and a trial compile (k2gen release, placeholder image digests, never
# deployed) into <out dir>/releases/<id>; the id is the last line printed. Any failure here fails.
# With --diff: k2gen diff against the release compiled from the newest v3.* tag, as Markdown to
# $GITHUB_STEP_SUMMARY (stdout when unset). A tag that does not compile is not a failure: one line
# says so (the first release).
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD OUT=$(mkdir -p "${1:?usage: ci-validate.sh <out dir> [--diff]}" && cd "$1" && pwd)
K2=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
GIT=(git -c user.name=ci -c user.email=ci@example.org)

images() {  # images <compose file> <out json>: every compose service, a placeholder digest
  python3 - "$1" > "$2" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "ci/placeholder@sha256:" + "0" * 64 for n in names}))
PY
}
compile() {  # compile <name> <repo> <tag>: docset, check, release under $OUT/<name>; prints the id
  local d=$OUT/$1
  "${K2[@]}" docset --tag "$3" --repo "$2" --out "$d/set" >&2
  "${K2[@]}" check --set "$d/set" --previous none >&2
  git -C "$2" show "$3:docker-compose.yml" > "$d/compose.yml"
  images "$d/compose.yml" "$d/images.json"
  "${K2[@]}" release --set "$d/set" --previous none --images "$d/images.json" --out "$d/releases" | tail -1
}

mkdir -p "$OUT/pr/clone"
git ls-files -co --exclude-standard -z | xargs -0 cp --parents -t "$OUT/pr/clone"
git -C "$OUT/pr/clone" init -q -b main
git -C "$OUT/pr/clone" add -A
"${GIT[@]}" -C "$OUT/pr/clone" commit -q -m "ci: the pull request's tree"
"${GIT[@]}" -C "$OUT/pr/clone" tag -a v0.0.0-ci.1 -m ci
id=$(compile pr "$OUT/pr/clone" v0.0.0-ci.1)

if [ "${2:-}" = --diff ]; then
  sum=${GITHUB_STEP_SUMMARY:-/dev/stdout}
  tag=$(git -c versionsort.suffix=- tag --list 'v3.*' --sort=-v:refname | head -1)
  if [ -n "$tag" ] && old=$(compile old "$REPO" "$tag" 2> "$OUT/old.log"); then
    { echo "### k2gen diff: $tag to this tree"; "${K2[@]}" diff "$OUT/old/releases/$old" "$OUT/pr/releases/$id"; } >> "$sum"
  else
    echo "no earlier release compiles from ${tag:-any v3.* tag}: first release" >> "$sum"
  fi
fi
echo "$OUT/pr/releases/$id"
