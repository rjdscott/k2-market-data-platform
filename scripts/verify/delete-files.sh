#!/usr/bin/env bash
# W2-18 (phases 004 and 005): every dbt output of the live release, or of <release id>, its delete
# files and data files at main from its `$files`, each against BOUND: the soak's baseline at its
# start and its check at its end (plan 004: "after 48 h: every silver table under the bound").
# Against the LIVE stack, by the operator.
#
#   bash scripts/verify/delete-files.sh [<release id>]
#
# The outputs are each compiled pipeline's dbt build outputs (compiled/products/*/pipeline.json,
# build.outputs), <domain>_build.<fence>__base.<table>, under ${K2_RELEASES:-~/k2-releases}; with no
# argument, the release the trino pointer names. They are read as svc.platform, the one platform
# principal the compiled rules let read a base table's `$files` (platform/k2gen/k2gen/emit/trino_rules.py:
# SELECT in every base namespace of every `_build` catalog, for table maintenance; svc.runs reads
# only `$refs`, a builder only its own product's tables), with its password file under
# ${K2_SECRETS:-~/k2-secrets}/principals and the CA there, over TLS on localhost:${K2_TRINO_PORT:-8443},
# through the access suite's client (scripts/access_verify.py, connect). No secret is printed or
# passed on a command line.
#
# One line per output, `<relation> delete_files=<n> data_files=<n>` (or `not built yet`), then
# `<n> output(s), <m> over the bound of <BOUND> delete files`. Exits: 0 every output at or under
# BOUND; 1 one or more over; 2 refused (no release, a table that could not be read).
#
# BOUND, every output's: 24 delete files. The wrapper compacts an incremental output after a build
# whose tests passed once it holds COMPACT_DELETE_FILES (12, docker/dbt-runner/k2_dbt.py), so a
# build leaves fewer than 12, and a `table` model, rebuilt whole, holds none. A table over twice
# that has missed its compaction for at least an hour of five-minute builds that each leave one, or
# its model updates more files a build than a correct one does: a defect to fix, never a reason to
# raise the bound (docs/benchmarks/2026-11-26-product-run-cost.md, F13, whose 288 is the ceiling
# with one compaction a day, the looser bound this one replaces now the wrapper compacts).
#
# Written before its first live run (2026-11-28), from the wrapper's compaction and the rules it
# reads under, and unrun: its first live run is the soak's baseline.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
ROOT=${K2_RELEASES:-$HOME/k2-releases}
RELEASE=${1:-$(readlink "$ROOT/pointers/trino" 2>/dev/null | sed 's|^\.\./||')}
[[ "$RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && [ -d "$ROOT/$RELEASE/compiled/products" ] \
  || { echo "delete-files: no release ${RELEASE:-(the trino pointer names none)} under $ROOT" >&2; exit 2; }
python3 - "$ROOT/$RELEASE" <<'PY'
import json
import re
import sys
from pathlib import Path

sys.path[:0] = ["scripts", "scripts/lib"]
from access_verify import connect  # the access suite's client: HTTPS, each password read from its file

BOUND = 24  # delete files per output: twice k2_dbt.COMPACT_DELETE_FILES, the header's reasoning


def refuse(why):
    print(f"delete-files: {why}", file=sys.stderr)
    sys.exit(2)


outputs = set()
for p in sorted((Path(sys.argv[1]) / "compiled" / "products").glob("*/pipeline.json")):
    build = json.loads(p.read_text()).get("build") or {}
    if build.get("engine") == "dbt":
        outputs |= set(build.get("outputs") or [])
trino, _ = connect(None)
over = 0
for rel in sorted(outputs):
    parts = rel.split(".")
    if len(parts) != 3 or not all(re.fullmatch(r"[a-z][a-z0-9_]*", x) for x in parts):
        refuse(f"{rel!r} is not a <catalog>.<namespace>.<table> of identifiers")
    catalog, ns, table = parts
    try:
        a = trino.run("svc.platform", f'SELECT count_if(content <> 0), count_if(content = 0) FROM "{catalog}"."{ns}"."{table}$files"')
    except Exception as e:  # no password file, no answer: never counted as over
        refuse(f"{rel}: {type(e).__name__}: {e}")
    if a.error == "TABLE_NOT_FOUND":
        print(f"{rel} not built yet")
        continue
    if a.error:
        refuse(f"{rel}: {a.says()}")
    deletes, data = a.rows[0]
    over += deletes > BOUND
    print(f"{rel} delete_files={deletes} data_files={data}{'  OVER' if deletes > BOUND else ''}")
print(f"{len(outputs)} output(s), {over} over the bound of {BOUND} delete files")
sys.exit(1 if over else 0)
PY
code=$?
[ "$code" -le 2 ] || code=2
exit "$code"
