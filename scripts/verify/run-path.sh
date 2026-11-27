#!/usr/bin/env bash
# W1-25 and W2-11 (phase 002), stage `run` of make spine-e2e: one product's whole run path on ONE
# throwaway lake (prefix run-path), as a deploy of <release> would run it: every task the
# release's own code in the shape the DAG factory gives it, the fetch's spool seeded as fetch.py
# leaves it (scripts/lib/run-path-lib.sh says how, and why the fetch itself is not run).
#
#   bash scripts/verify/run-path.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run, since every
# later claim builds on the lake the one before left:
#   1 calendars builds through the wrapper and its audited tag moves (ECB's gold reads it)
#   2 ECB's good fixture (domains/fx-reference/tests/fixtures/ecb-90d-synthetic.xml): load lands it
#     in k2-landing at its landing key with one landing_files row and stages its rows on `audit`;
#     audit passes every declared check (its audit_checks rows); publish fast-forwards bronze main
#   3 the wrapper builds ECB (silver, then gold), its input pinned to calendars' tested snapshot:
#     tests pass, audited moves on both outputs, p.analyst-a reads gold through the reader view,
#     silver holds bronze's distinct (rate_date, currency), each gold rate is the fixture's string
#     as DECIMAL(20,10)
#   4 gold through k2-api (the lake's, as p.analyst-a with the key the lake made, read from its
#     file): GET /v1/data/fx-reference/ecb-fx-rates/gold_eur_rates_daily?limit=10000 is exactly
#     the rows the same principal reads over SQL from the reader view, in key order, decimals as
#     their strings, times as scripts/access_verify.py's REST leg puts them; a filter on one
#     currency likewise; no key 401; bronze_daily 403; silver_rates 403, since k2-api asks Trino as
#     the caller before it tells a relation's state and Trino denies silver's view to a reader no
#     role on it is granted to (404 is an output with no reader relation, or one not yet published)
#   5 the same bytes again (a new run): no new landing object or landing_files row, no new bronze
#     row, and the build passes with the same rows (fetch itself would exit 10 here: unchanged)
#   6 the corrupt fixture lands, fails its audit (rate_positive, no_future_date) and leaves bronze
#     main where it was; a third good batch (the good fixture and one more day, made here) is
#     refused at load (exit 2) while `audit` holds the failed batch, its file landed all the same;
#     the operator's clearing step (`audit` reset to main) lets its load proceed
#   7 a failing test (gold given a duplicate row, in a release of this tree compiled here): the
#     wrapper restores silver and lists the build's snapshots in its XCom line; expire.py removes
#     them with the maintenance key; maintain.py runs clean on the product
#   8 every build's run records are in governance_build.run_records.run_record under its release
#   9 every dbt product of the release builds through the wrapper, each after the products it reads
#     and governance.quality-results last, exits 0 and ends with its audited tag on main: a
#     materialization shape the publish guard does not know (a table of no rows: currency-master's
#     bronze is empty here) fails on this lake first, not on live
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down run-path`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start run-path "${1:-}" 9
# claim 6's release: this tree with gold given a duplicate row, which gold's unit test catches
rp_fail_release "$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/fx-reference/ecb-fx-rates/models/gold_eur_rates_daily.sql"
sql = p.read_text()
assert sql.endswith("\nfrom aged\n") and sql.count("\nfrom aged\n") == 1, "the gold model no longer ends 'from aged': move the plant"
p.write_text(sql.removesuffix("from aged\n") + "from (select * from aged union all (select * from aged limit 1)) as aged\n")
PY
)"
rp_image
rp_lake
rp_calendars  # 1
rp_good       # 2
rp_build      # 3
claim rest "$ID"  # 4
rp_again      # 5
rp_corrupt    # 6
# 7. a failing test, its rollback, expire by the XCom list, maintain (one DAG run, pinned to the failing release)
build "$IDF" "$ECB" rp-fail
probe state fail
expire=$(python3 -c 'import json, sys; lines = [x for x in open(sys.argv[1]) if x.strip()]; print(json.dumps(json.loads(lines[-1])["expire"], separators=(",", ":")))' \
  "$RP/rp-fail.build.out" 2>/dev/null)
[ -n "$expire" ] && [ "$expire" != "[]" ] && task "$IDF" rp-fail expire --expire "$expire"  # factory: an empty list skips expire
task "$IDF" rp-fail maintain
probe state expired
claim expire "$ID"
# 8. the run records of every build above
claim records "$ID" "$IDF"
rp_every  # 9
finish
