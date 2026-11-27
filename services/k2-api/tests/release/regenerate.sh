#!/usr/bin/env bash
# Regenerates tests/release/<release id>/: the files k2-api reads, copied from a release that k2gen
# compiles from its good fixture set (platform/k2gen/tests/fixtures/v2/good/, every value invented).
# Nothing here is written by hand. Run from anywhere; it needs no container and no network:
#   bash services/k2-api/tests/release/regenerate.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
WORK=$(mktemp -d)
trap 'chmod -R u+w "$WORK"; rm -rf "$WORK"' EXIT
K2=(uv run --project "$REPO/platform/k2gen" --locked -q k2gen)
GIT=(git -c user.name="Example Owner" -c user.email=owner@example.org)
export GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z  # the goldens' taggedAt

mkdir "$WORK/repo"
cp -r "$REPO/platform/k2gen/tests/fixtures/v2/good/." "$WORK/repo/"
cp "$REPO/docker-compose.yml" "$WORK/repo/"
git -C "$WORK/repo" init -q -b main
git -C "$WORK/repo" add -A
"${GIT[@]}" -C "$WORK/repo" commit -q -m "the good fixture set"
"${GIT[@]}" -C "$WORK/repo" tag -a v3.0.0-alpha.3-rc.1 -m "the goldens' tag"
"${K2[@]}" docset --tag v3.0.0-alpha.3-rc.1 --repo "$WORK/repo" --out "$WORK/set" >&2
"${K2[@]}" check --set "$WORK/set" --previous none >&2
python3 - "$WORK/repo/docker-compose.yml" > "$WORK/images.json" <<'PY'
import json, re, sys
names = re.findall(r"^  ([a-z][a-z0-9-]*):\n", open(sys.argv[1]).read(), re.M)
print(json.dumps({n: "ci/placeholder@sha256:" + "0" * 64 for n in names}))
PY
id=$("${K2[@]}" release --set "$WORK/set" --previous none --images "$WORK/images.json" --out "$WORK/releases" | tail -1)

# What k2-api reads (manifest, principals, catalog, views, contracts), and the rules and groups the
# tests' fake Trino enforces.
mkdir "$WORK/out"
cd "$WORK/releases/$id" && chmod -R u+w .  # sealed read-only; cp --parents copies the modes
find manifest.json compiled/api compiled/catalog compiled/trino/views.json compiled/trino/rules.json \
  compiled/trino/groups.txt documents/contracts -type f -not -path 'documents/contracts/*' -o -name '*.odcs.yaml' \
  | xargs cp --parents -t "$WORK/out"
find "$HERE" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
mv "$WORK/out" "$HERE/$id"
chmod -R u+w "$HERE/$id"
echo "$HERE/$id"
