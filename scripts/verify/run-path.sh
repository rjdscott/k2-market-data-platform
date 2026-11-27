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
# The phase 004 chain (W2-11), on the same lake, which a Redpanda joins (K2_THROWAWAY_REDPANDA=1),
# rows flowing: each task the release's own, in DAG order, its results read as each product's own
# principals (its builder; the run records as svc.runs). The trade frames are those of
# services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl (three trades, all BTC/USD); the
# listing is domains/market-capture/tests/fixtures/asset-pairs-synthetic.json (four pairs, XBT/USD
# among them), spooled as fetch.py leaves a public-API page; every other frame and listing below is
# made here from those two (rp_chain_files), synthetic, never committed:
#  10 kraken-instruments: the listing loads, audits (every check, and the parser's audits as fetch
#     recorded them), publishes; silver_listing built by the wrapper holds one version per pair,
#     each first_fetched_at the spool's fetchedAt
#  11 security-master on the tested listing: venue_symbol one open row per listed pair spelled as
#     the trades stream spells it (XBT/USD through seeds/code_exceptions.csv is BTC/USD, the
#     frames' own symbol), from 1970-01-01 to 9999-12-31 23:59:59; venue_symbol_held empty
#  12 kraken-trades: load lands every frame and stages the trades, audit's three checks, publish;
#     silver_trade one row per trade of the fixture's trade frames, each instrument resolved, one
#     trade's decimals exact, none a duplicate; silver_trade_unresolved empty
#  13 a frame for a symbol the listing lacks: held, not in silver_trade, the build degraded naming
#     its warn rule, unresolved_rows_zero, k2runs.state degraded; a second listing naming it, instruments and security
#     master again: the next trades build takes it into silver_trade once, held empty, complete
#  14 canonical-trades one row per silver_trade row, no conn_id column; candles' 1m bucket of the
#     fixture's minute equal to the probe's own arithmetic over the frames, the day's 1d row, and
#     the released trade's 1m, 5m, 1h and 1d buckets written by the build after its release
#  15 every product again with nothing new (the listing unchanged: fetch's exit 10 skips load,
#     audit and publish, as the factory does): no row added or changed anywhere
#  16 kraken-trades paused (a release of this tree with its status paused, compiled here):
#     canonical-trades builds, adds nothing, and is `partial: market-capture.kraken-trades.silver_trade`
#  17 a failed venue test (a release of this tree with silver_trade given a duplicate row, which its
#     k2_unique_key test catches: no frame can fail one of its data tests, since each not_null
#     column is NOT NULL in the table and Trino refuses the merge first, and no frame repeats the
#     key): one more trade frame, the venue build fails its tests and rolls back, its audited tag
#     unmoved; canonical-trades and candles then change nothing
#  18 a trade frame the model cannot type (one trade without side, made here) beside a good one: on
#     the tree's own release the build takes the good frame and claim 17's, keeps the bad one whole
#     in silver_trade_rejected (field_missing:side, bronze's bytes), publishes degraded warning
#     rejected_frames_zero by its name; the next build with nothing new adds and changes no row and
#     is degraded again, since the frame still stands
# Each chain task's wall time goes to <rp>/times and is printed as an INFO line, with the sum of
# one five-minute cycle (kraken-trades' four tasks, canonical-trades and candles).
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down run-path`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start run-path "${1:-}" 18
# claim 16's release: this tree with kraken-trades paused
rp_fail_release "$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/market-capture/kraken-trades/product.odps.yaml"
doc = p.read_text()
assert doc.count("\nstatus: active\n") == 1 and doc.count("\ncustomProperties:\n") == 1, "kraken-trades' status or customProperties moved: move the plant"
p.write_text(doc.replace("\nstatus: active\n", "\nstatus: paused\n").replace("\ncustomProperties:\n", """
customProperties:
  - property: lifecycleChange
    value:
      since: "2026-11-28"
      reason: The run path pauses the venue to prove its derived product goes partial.
      decision: ADR-056
      resume: From the topic's records still held when it resumes.
"""))
PY
)" paused
IDP=$IDF IDF=
# claim 17's release: this tree with silver_trade given a duplicate of one new row
rp_fail_release "$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/market-capture/kraken-trades/models/silver_trade.sql"
sql = p.read_text()
tail = "\nfrom flagged\nwhere is_new or is_duplicate <> was_duplicate\n"
assert sql.endswith(tail) and sql.count(tail) == 1, "silver_trade no longer ends 'from flagged where ...': move the plant"
p.write_text(sql.removesuffix(tail) + "\nfrom (select * from flagged union all (select * from flagged where is_new limit 1)) as flagged\n"
             "where is_new or is_duplicate <> was_duplicate\n")
PY
)" venuefail
IDV=$IDF RP_MORE="paused venuefail" IDF=
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
export K2_THROWAWAY_REDPANDA=1
rp_lake
rp_chain_files
python3 - "$RP" <<'PY' || die "claim 18's frames could not be made"
# Synthetic, in the fixture's line format, after more.jsonl's frame: a BTC/USD trade without side,
# which silver_trade cannot type, then a good one.
import json, sys
from pathlib import Path
def line(at_s, trade):
    payload = json.dumps({"channel": "trade", "type": "update", "data": [trade]}, separators=(",", ":"))
    return json.dumps({"recv_ts_ns": 1_000_000_000_000_000_000 + at_s * 1_000_000_000, "payload": payload}) + "\n"
bad = {"symbol": "BTC/USD", "price": 1001.0, "qty": 0.25, "ord_type": "market", "trade_id": 5, "timestamp": "2001-09-09T01:47:30.000000Z"}
good = {"symbol": "BTC/USD", "side": "buy", "price": 1002.0, "qty": 0.75, "ord_type": "limit", "trade_id": 6, "timestamp": "2001-09-09T01:47:31.000000Z"}
(Path(sys.argv[1]) / "malformed.jsonl").write_text(line(50, bad) + line(51, good))
PY
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
# 10 to 17: the phase 004 chain
listing ki-1 listing-1.json; build "$ID" "$KI" ki-1; probe chain-state ki-1
claim instruments "$ID"            # 10
build "$ID" "$SM" sm-1; probe chain-state sm-1
claim master "$ID"                 # 11
kseed "$RP/frames.jsonl"; trades kt-1; probe chain-state kt-1
build "$ID" "$CT" ct-1; build "$ID" "$CA" ca-1; probe chain-state ca-1
claim trades "$ID"                 # 12
kseed "$RP/unknown.jsonl"; trades kt-2; probe chain-state kt-2
build "$ID" "$CT" ct-2; build "$ID" "$CA" ca-2; probe chain-state ca-2
listing ki-2 listing-2.json; build "$ID" "$KI" ki-2; build "$ID" "$SM" sm-2
trades kt-3; probe chain-state kt-3
build "$ID" "$CT" ct-3; build "$ID" "$CA" ca-3; probe chain-state ca-3
claim held "$ID"                   # 13
claim candles "$ID"                # 14
listing ki-3 listing-2.json; build "$ID" "$KI" ki-3; build "$ID" "$SM" sm-3
trades kt-4; build "$ID" "$CT" ct-4; build "$ID" "$CA" ca-4; probe chain-state idle
claim idle "$ID"                   # 15
build "$IDP" "$CT" ct-5; probe chain-state paused
claim paused "$ID" "$IDP"          # 16
kseed "$RP/more.jsonl"; trades kt-5 "$IDV"; probe chain-state kt-5
build "$ID" "$CT" ct-6; build "$ID" "$CA" ca-6; probe chain-state failed
claim venue-fail "$ID" "$IDV"     # 17
kseed "$RP/malformed.jsonl"; trades kt-6; probe chain-state kt-6
trades kt-7; probe chain-state kt-7
claim rejected "$ID"               # 18
rp_times
finish
