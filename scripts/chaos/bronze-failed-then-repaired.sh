#!/usr/bin/env bash
# W2-15, plan 004's Verification "bronze-failed-then-repaired.sh: the repaired batch reaches silver",
# and docs/runbooks/kafka-batch-blocked.md sections 1 and 2 induced: on ONE throwaway lake with a
# Redpanda (prefix bronze-failed, K2_THROWAWAY_REDPANDA=1) from <release>, the phase 004 chain's first
# cycle, then a batch that fails its audit for a cause outside its bytes, which is then repaired: a
# capture writing a new envelope shape before its schema is registered. One BTC/USD trade frame whose
# body is written under RawMessage with one optional field added, framed with the id the registry
# gives the next schema it registers (scripts/lib/kafka_path_probe.py `unregistered`): the stage
# keeps it undecoded (the registry answers 404 for that id) and parses fails. The repair is that
# schema registered (it gets that id), then the operator's clearing statement the refusal prints,
# run as the Spark key (scripts/lib/run_path_spark.py clear). Each task the release's own, in the
# DAG factory's shape (scripts/lib/run-path-lib.sh); a failed audit ends the run there, as the DAG's
# all_success edges do.
#
#   bash scripts/chaos/bronze-failed-then-repaired.sh <release dir>     # KEEP=1 keeps the scratch directory
#
# Claims, one PASS or FAIL line each, then `<n> PASS, <m> FAIL`; a FAIL stops the run:
#   1 to 3 the chain's first cycle: kraken-instruments, security-master, kraken-trades with
#     canonical-trades and candles (run-path.sh claims 10 to 12)
#   4 kt-2 stages the frame, its audit fails parses naming the unregistered id (exit 3), bronze main
#     and silver unmoved; kt-3 (one more BTC/USD trade frame) lands its frame, the landing advancing,
#     and its stage is refused (exit 2) naming kt-2's batch, bronze's refs unmoved; the schema
#     registered gets the frame's id, the clearing statement resets audit to main; kt-4 restages both
#     frames, audits (every check passes), publishes and builds: silver_trade takes both trades once
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down bronze-failed`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start bronze-failed "${1:-}" 4
rp_image
export K2_THROWAWAY_REDPANDA=1
rp_lake
rp_chain_files
TOPIC=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["connector"]["topic"])' \
  "$STATE/releases/$ID/compiled/products/$KT/pipeline.json")
python3 - "$RP/variant.jsonl" <<'PY' || die "the variant frame could not be made"
# Synthetic, in the fixture's line format, between the fixture's frames and more.jsonl's: one BTC/USD trade.
import json, sys
trade = {"symbol": "BTC/USD", "side": "buy", "price": 999.5, "qty": 0.1, "ord_type": "limit", "trade_id": 7,
         "timestamp": "2001-09-09T01:47:15.000000Z"}
payload = json.dumps({"channel": "trade", "type": "update", "data": [trade]}, separators=(",", ":"))
open(sys.argv[1], "w").write(json.dumps({"recv_ts_ns": 1_000_000_000_000_000_000 + 35 * 1_000_000_000, "payload": payload}) + "\n")
PY
rp_chain                                                                       # 1 to 3
docker exec -i "$SPARK" python3 - unregistered "/k2/releases/$ID" "$KT" "$(cat "$RP/variant.jsonl")" < "$KPROBE" \
  > "$RP/variant.out" 2> "$RP/variant.err" || die "the variant frame was not produced: $(grep -v '^\s' "$RP/variant.err" | tail -2)"
variant() { python3 -c 'import json, sys; print(json.loads([x for x in open(sys.argv[1]) if x.startswith("{")][-1])[sys.argv[2]])' "$RP/variant.out" "$1"; }
echo "$P: one BTC/USD trade frame framed with schema id $(variant id), not registered"
TP=$KT task "$ID" kt-2 load; TP=$KT task "$ID" kt-2 audit; probe chain-state kt-2   # audit exit 3: publish and build do not run
kseed "$RP/more.jsonl"; TP=$KT task "$ID" kt-3 load; probe chain-state kt-3          # exit 2 at the stage: the DAG run ends
# The repair: the schema registered (its id the frame's), then the clearing statement, as the Spark key.
python3 -c 'import json, sys; print(json.dumps({"schema": sys.argv[1]}))' "$(variant schema)" |
  docker exec -i "$P-redpanda" curl -sS -X POST -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
  --data-binary @- "http://localhost:8081/subjects/$TOPIC-value/versions" > "$RP/register.out" 2>&1
echo "$P: registered: $(cat "$RP/register.out")"
TP=$KT spark_py clear clear
echo "$P: cleared: $(tail -1 "$RP/clear.out")"
trades kt-4; probe chain-state kt-4
claim repaired "$ID"                                                           # 4
finish
