#!/usr/bin/env bash
# The access rules k2gen compiles (emit/trino_rules.py, ADR-059, ADR-061), attacked on a throwaway
# lake: a compiled release's own Trino etc, rules.json and groups.txt, on Trino 484 over a throwaway
# Lakekeeper, Postgres and MinIO (scripts/lib/throwaway-lake.sh). No running stack is touched.
#
#   make test-trino            # locally
#   CI job "Trino (access rules)" runs the same script.
#
# 1. Compile the test's document set (the good fixture set plus a same-domain twin product whose
#    names extend the other's, scripts/trino_rules_test.py) with k2gen into a release.
# 2. Start the throwaway lake from that release on the real identity path (ADR-060): a CA and
#    Trino's certificate by scripts/tls.sh, and every known principal its own password by
#    scripts/principal-secret.py's code; the release canary and four strangers a line each.
#    Every statement below logs in over HTTPS, by name, verified against that CA.
# 3. The suite, as a table of named rows, non-zero on any FAIL: each role sees exactly its policy's
#    rows and masks, never the untested row; the gate's h1 fence matrix (every base path, both
#    catalogs, every reader and stranger); views, listings, builders and publishers (h1.02, h1.12,
#    h1.13, h1.19, h1.33); statistics and every session property; system tables and query history;
#    impersonation; the release canary; strangers; $refs and the lake; experiment (e)'s 40
#    statements, writer against writer.
# 4. The negative control: one reader rule widened in place in the running copy; the rows it breaks
#    must FAIL, so the suite can fail. Then restored.
# 5. Experiment (e)'s control, a builder on its own table (destructive, so last).
# 6. Identity, row by row (scripts/verify/identity.sh): refusals, rotations and the clients.
#
# Needs Docker, openssl and 6 GiB available; pulls the stack's image pins and the pinned uv image.
set -euo pipefail
cd "$(dirname "$0")/.."
L=scripts/lib/throwaway-lake.sh
P=${K2_RULES_TEST_PREFIX:-k2t-rules}
trap 'bash "$L" down "$P"' EXIT

bash "$L" down "$P" >/dev/null
STATE=$(bash "$L" state "$P")
id=$(bash "$L" py "$P" scripts/trino_rules_test.py compile /work/build | tail -1)
echo "trino-rules-test: compiled release $id"
bash "$L" up "$P" "$STATE/build/releases/$id" p.nobody svcxplatform pxanalyst-full k2_release_000000000000

fail=0
bash "$L" py "$P" scripts/trino_rules_test.py suite || fail=1
bash "$L" py "$P" scripts/trino_rules_test.py negative || fail=1
bash "$L" py "$P" scripts/trino_rules_test.py control || fail=1
bash scripts/verify/identity.sh --throwaway "$P" || fail=1
[ "$fail" = 0 ] || { echo "trino-rules-test: FAIL ($id)"; exit 1; }
echo "trino-rules-test: PASS ($id)"
