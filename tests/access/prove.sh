#!/usr/bin/env bash
# The proofs behind access-verify, the tested-data marker and the catalog drift check (step S20),
# on a throwaway lake started from a compiled release (scripts/lib/throwaway-lake.sh). Nothing of
# the running stack is touched. make test-access RELEASE=<release dir> runs it.
#
#   tests/access/prove.sh <release dir>        prefix ${K2_ACCESS_TEST_PREFIX:-access-test}
#
# 1. The lake staged as the release describes it (access_verify.py stage).
# 2. access-verify passes every row (scope release).
# 3. Negative control: one reader rule loosened in the running copy of the rules; access-verify
#    fails exactly that role's read rows, and passes again once the rule is restored.
# 4. The live scope on this lake: only the planted-view and drift rows skip; a missing canary
#    password fails that reader's rows.
# 5. The drift check is clean, then names each plant and is clean once it is undone: a view with a
#    forged owner, a grafted tag, a table in a reader namespace, a wider view, a column aliased past
#    its mask (the last two also fail the marker check).
# 6. The marker check passes, its schema change included; the drift check and access-verify still
#    pass after it.
# 7. A rules-only release takes effect without a restart, n=5 (scripts/verify/grant-refresh.sh).
# 8. Each principal lands in its resource group (scripts/verify/resource-groups.sh).
# 9. Last, being destructive: the base replaced by a view of constants as its builder (the review
#    of S12, C1); the drift check, the marker check and access-verify each fail, naming it.
#
# Exit 0 when every step ended as stated, 1 at the first that did not. Needs Docker, openssl and
# 6 GiB available; the lake is removed on exit.
set -euo pipefail
cd "$(dirname "$0")/../.."
REL=$(realpath "${1:?usage: tests/access/prove.sh <release dir>}")
ID=$(basename "$REL")
P=${K2_ACCESS_TEST_PREFIX:-access-test}
L=scripts/lib/throwaway-lake.sh
OUT=$(mktemp)
trap 'rm -f "$OUT"; bash "$L" down "$P" >/dev/null' EXIT

py() { bash "$L" py "$P" "$@"; }
av() { py scripts/access_verify.py run --scope release --release "/work/releases/$ID" --env /work/env.json; }
drift() { py scripts/verify/catalog_drift.py --release "/work/releases/$ID"; }
marker() { bash scripts/verify/tested-data-marker.sh "$P" "$@"; }
# expect <exit> <pattern or -> <command ...>: the command exits so, and prints a line matching the pattern.
expect() {
  local want=$1 pattern=$2 got=0; shift 2
  "$@" > "$OUT" 2>&1 || got=$?
  sed 's/^/    /' "$OUT"
  if [ "$got" != "$want" ] || { [ "$pattern" != - ] && ! grep -Eq -- "$pattern" "$OUT"; }; then
    echo "prove: FAIL: '$*' exited $got; expected $want and a line matching '$pattern'"; exit 1
  fi
  echo "prove: ok: '$*' exited $got, as expected"
}

bash "$L" down "$P" >/dev/null
bash "$L" up "$P" "$REL"
py scripts/access_verify.py stage --release "/work/releases/$ID" --env /work/env.json

expect 0 '0 FAIL, 0 SKIP' av
chmod u+w "$(bash "$L" state "$P")/releases/$ID/compiled/trino/rules.json"  # the lake's running copy, from the host
expect 0 'negative control: access-verify exit 1' py tests/access/prove.py negative
expect 0 '0 FAIL' av
expect 0 'live scope without' py tests/access/prove.py live

expect 0 'clean' drift
plants=(
  "view|DRIFT view [a-z_]+\.[a-z_]+\.foreign_view: the release declares no such view"
  "graft|DRIFT tag .*never this table's main"
  "table|DRIFT table [a-z_]+\.[a-z_]+\.planted: in a reader namespace"
  "wider|DRIFT view .*: SQL"
  "alias|DRIFT view .*: SQL .*_unmasked"
)
for p in "${plants[@]}"; do
  kind=${p%%|*}
  py tests/access/prove.py plant "$kind"
  expect 1 "${p#*|}" drift
  if [ "$kind" = wider ] || [ "$kind" = alias ]; then
    expect 1 'FAIL .* SQL' marker --no-schema-change
  fi
  py tests/access/prove.py unplant "$kind"
done
expect 0 'clean' drift

expect 0 'tested-data-marker: PASS' marker
expect 0 'clean' drift
expect 0 '0 FAIL, 0 SKIP' av
expect 0 'grant-refresh: PASS' bash scripts/verify/grant-refresh.sh "$P" 5
expect 0 '0 FAIL' bash scripts/verify/resource-groups.sh "$P"

py tests/access/prove.py plant base-view
expect 1 'DRIFT base .*a VIEW where' drift
expect 1 'FAIL .* base is a table: .* is a VIEW' marker
expect 1 'FAIL' av
echo "prove: PASS ($ID)"
