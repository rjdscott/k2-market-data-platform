#!/usr/bin/env bash
# W0-11 and W1-18 (phases 004 and 005): DuckDB in the tools image reads a Kafka product's landing at a
# pinned snapshot through Lakekeeper with the tools MinIO key alone, its extensions baked into the
# image; then make parity-silver's and make parity-ohlcv's scripts on tables the chain built. ONE
# throwaway lake with a Redpanda (prefix duckdb-landing, K2_THROWAWAY_REDPANDA=1), the phase 004 chain
# run as run-path-lib.sh runs it (the release's own Spark tasks and the wrapper's builds), and the tools
# image built from this tree as tools:duckdb-landing, run on the lake's network as mk/parity.mk runs
# it: the tools key by -e name, this tree's scripts/ and docker/lake/ read-only, 1 CPU / 2G.
#
#   bash scripts/verify/duckdb-landing.sh <release dir>   # KEEP=1 keeps the scratch directory
#
# <release dir> is the one run-path.sh takes (rp_start's checks). Claims, one PASS or FAIL line each:
#   1 the image holds DuckDB 1.4.4, and httpfs, avro and iceberg load from /opt/k2/duckdb with no
#     network (--network none) and autoinstall off; an extension not baked in (spatial) does not load
#   2 after two kraken-trades loads, DuckDB on the tools key reads the landing AT each load's
#     snapshot (its first two connector commits): per partition the rows, distinct offsets and first
#     and last offset its spans (k2.kafka-starts to k2.kafka-offsets) give, the total its
#     total-records; the first read again after the second, unchanged
#   3 that connection holds the tools key and no other storage key, and writes nothing: a COPY to the
#     lake's bucket is refused (403)
#   4 parity_silver.py passes on silver_trade, every class empty, pinned by kt-2's build record
#   5 parity_ohlcv.py passes on candles at every timeframe, pinned by ca-2's build records
#   6 a release of this tree whose silver_trade writes conn_msg_seq one too high (its unit tests list
#     no such column, so its build publishes) builds kt-3 over one more frame: parity_silver.py exits
#     1, conn_msg_seq the one class that fails, pinned by kt-3's build record
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down duckdb-landing`, the images to
# `docker rmi dbt-runner:duckdb-landing tools:duckdb-landing`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start duckdb-landing "${1:-}" 6
TOOLS=tools:duckdb-landing
trap 'rp_cleanup; docker rmi -f "$TOOLS" >/dev/null 2>&1' EXIT
# claim 6's release: this tree with silver_trade's conn_msg_seq one too high
rp_fail_release "$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/market-capture/kraken-trades/models/silver_trade.sql"
sql = p.read_text()
assert sql.count("    b.conn_msg_seq,\n") == 1, "silver_trade.sql no longer selects b.conn_msg_seq once: move the plant"
p.write_text(sql.replace("    b.conn_msg_seq,\n", "    b.conn_msg_seq + 1 as conn_msg_seq,\n"))
PY
)" seq
IDV=$IDF IDF= RP_MORE=seq
rp_image
docker build -q -f docker/tools/Dockerfile -t "$TOOLS" . > "$W/tools-build.log" 2>&1 || { tail -20 "$W/tools-build.log" >&2; die "the tools image did not build"; }
export K2_THROWAWAY_REDPANDA=1
rp_lake  # exports every engine's pair from the lake's s3.env; the tools runs below pass the tools pair alone
rp_chain_files
python3 - "$RP" <<'PY' || die "claim 6's frame could not be made"
# Synthetic, in the fixture's line format, after more.jsonl's frame: one more BTC/USD trade.
import json, sys
from pathlib import Path
payload = json.dumps({"channel": "trade", "type": "update", "data": [{"symbol": "BTC/USD", "side": "buy", "price": 1001.0,
                      "qty": 0.25, "ord_type": "limit", "trade_id": 5, "timestamp": "2001-09-09T01:47:30.000000Z"}]}, separators=(",", ":"))
(Path(sys.argv[1]) / "extra.jsonl").write_text(json.dumps({"recv_ts_ns": 1_000_000_050_000_000_000, "payload": payload}) + "\n")
PY

tools() {  # tools <label> [docker flags --] <argv...>: the tools image on the lake's network with the tools pair alone
  local label=$1; shift
  docker run --rm -i --name "$P-tools-$label" --label "k2.throwaway=$P" --network "$NET" --cpus 1 --memory 2g \
    --user "$(id -u):$(id -g)" -e K2_TOOLS_S3_ACCESS_KEY -e K2_TOOLS_S3_SECRET_KEY \
    -v "$STATE/releases:/k2/releases:ro" -v "$REPO/scripts:/opt/k2/scripts:ro" -v "$REPO/docker/lake:/opt/k2/docker/lake:ro" \
    "$TOOLS" "$@" > "$RP/$label.out" 2> "$RP/$label.err"
  echo $? > "$RP/$label.rc"
  echo "$P: tools $label exit $(cat "$RP/$label.rc")"
}
verdict() {  # verdict <label> <exit wanted> <pattern the output holds> <claim>: one PASS or FAIL line
  local rc; rc=$(cat "$RP/$1.rc")
  if [ "$rc" = "$2" ] && grep -qE "$3" "$RP/$1.out"; then
    echo "PASS $4"
  else
    echo "FAIL $4: exit $rc; $(grep -E '^(FAIL|parity)' "$RP/$1.out" | tail -4 | tr '\n' ' ')$(tail -3 "$RP/$1.err" | tr '\n' ' ')"
  fi
}

# 1. the extensions, with no network at all
out=$(docker run --rm -i --network none --user "$(id -u):$(id -g)" "$TOOLS" python3 - 2>&1 <<'PY'
import duckdb
c = duckdb.connect(config={"extension_directory": "/opt/k2/duckdb", "autoinstall_known_extensions": False,
                           "autoload_known_extensions": True})
c.execute("LOAD httpfs")
c.execute("LOAD iceberg")
loaded = sorted(r[0] for r in c.execute("SELECT extension_name FROM duckdb_extensions() WHERE loaded "
                                        "AND extension_name IN ('httpfs', 'avro', 'iceberg')").fetchall())
try:
    c.execute("LOAD spatial")
    spatial = "spatial loaded"
except duckdb.Error as e:
    spatial = None
ok = duckdb.__version__ == "1.4.4" and loaded == ["avro", "httpfs", "iceberg"] and spatial is None
print(("PASS" if ok else "FAIL") + f" DuckDB {duckdb.__version__} loads {loaded} from /opt/k2/duckdb with no network; "
      + (spatial or "spatial, not baked in, does not load"))
PY
)
claimed extensions "$out" $?

listing ki-1 listing-1.json; build "$ID" "$KI" ki-1; build "$ID" reference-data.currency-master cm-1; build "$ID" "$SM" sm-1
kseed "$RP/frames.jsonl"; trades kt-1
kseed "$RP/more.jsonl"; trades kt-2; build "$ID" "$CT" ct-2; build "$ID" "$CA" ca-2

# 2. the landing at each of its first two connector snapshots
tools landing python3 - <<'PY'
import os, sys, tempfile
sys.path[:0] = ["/opt/k2/scripts", "/opt/k2/docker/lake"]
import kafka as K
import parity_silver as P
from product_verify import connector, lineage, summary_of
from verify.catalog_drift import Catalog

import offsets as O

topic = "market.crypto.v3.raw.kraken"
commits = [s for s in lineage(Catalog("http://lakekeeper:8181/catalog", "k2").table("kraken_trades", "landing")) if connector(s)]
want, acc = [], {}
for s in commits[:2]:
    summary = summary_of(s)
    starts = O.decode(summary[K.KAFKA_STARTS]).get(topic, {})
    for p, end in O.decode(summary[O.KAFKA_OFFSETS]).get(topic, {}).items():
        start = starts.get(p, end)
        if end > start:
            n, first, _ = acc.get(p, (0, start, None))
            acc[p] = (n + end - start, first, end - 1)
    want.append((s["snapshot-id"], int(summary["total-records"]), {p: (n, n, a, b) for p, (n, a, b) in acc.items()}))
with tempfile.TemporaryDirectory() as tmp:
    c = P.lake(["k2"], dict(os.environ), tmp)

    def read(snap):
        rows = c.execute(f"""SELECT kafka_partition, count(*), count(DISTINCT kafka_offset), min(kafka_offset), max(kafka_offset)
                             FROM {P.at('"k2"."kraken_trades"."landing"', snap)} GROUP BY 1""").fetchall()
        return {p: tuple(v) for p, *v in rows}

    got = [read(snap) for snap, _, _ in want] + ([read(want[0][0])] if want else [])
ok = len(want) == 2 and all(g == w[2] and sum(v[0] for v in g.values()) == w[1] for g, w in zip(got, want)) \
    and got[2] == got[0] and got[0] != got[1]
print(("PASS" if ok else "FAIL") + " the landing read AT its first two connector snapshots "
      + ", ".join(f"{snap} ({total} rows)" for snap, total, _ in want) + " holds each one's spans exactly; the first again unchanged"
      + ("" if ok else f": want {[w[2] for w in want]}, got {got}"))
PY
claimed landing "$(cat "$RP/landing.out" "$RP/landing.err")" "$(cat "$RP/landing.rc")"

# 3. the key it holds, and nothing written with it
tools key python3 - <<'PY'
import os, sys, tempfile
sys.path[:0] = ["/opt/k2/scripts", "/opt/k2/docker/lake"]
import duckdb
import parity_silver as P

keys = sorted(n for n in os.environ if n.endswith(("_S3_ACCESS_KEY", "_S3_SECRET_KEY")) or n.startswith(("AWS_", "MINIO_")))
with tempfile.TemporaryDirectory() as tmp:
    c = P.lake(["k2"], dict(os.environ), tmp)
    (n,) = c.execute('SELECT count(*) FROM "k2"."kraken_trades"."landing"').fetchone()
    try:
        c.execute("COPY (SELECT 1 AS x) TO 's3://k2-lake/warehouse/k2/duckdb-landing-probe.parquet'")
        refused = "the COPY wrote"
    except duckdb.Error as e:
        refused = None if any(s in str(e) for s in ("403", "Forbidden", "AccessDenied", "Access Denied")) else f"refused otherwise: {str(e)[:200]}"
ok = keys == ["K2_TOOLS_S3_ACCESS_KEY", "K2_TOOLS_S3_SECRET_KEY"] and n > 0 and refused is None
print(("PASS" if ok else "FAIL") + f" holding {keys} alone, DuckDB reads {n} landing rows and a COPY to the lake's bucket is "
      + (refused or "refused (403)"))
PY
claimed key "$(cat "$RP/key.out" "$RP/key.err")" "$(cat "$RP/key.rc")"

# 4 and 5. parity on the chain's tables
tools silver python3 /opt/k2/scripts/parity_silver.py --release "/k2/releases/$ID" --product kraken-trades
claimed parity-silver "$(verdict silver 0 'pin run kraken_trades/kt-2 .*' 'parity_silver.py: every class empty, pinned by kt-2')" 0
grep -E '^(INFO|parity-silver: pin)' "$RP/silver.out"
tools ohlcv python3 /opt/k2/scripts/parity_ohlcv.py --release "/k2/releases/$ID"
claimed parity-ohlcv "$(verdict ohlcv 0 'pin run candles/ca-2 .*' 'parity_ohlcv.py: every class of every timeframe empty, pinned by ca-2')" 0

# 6. a silver that differs in one column
kseed "$RP/extra.jsonl"; trades kt-3 "$IDV"
tools planted python3 /opt/k2/scripts/parity_silver.py --release "/k2/releases/$ID" --product kraken-trades
said="parity_silver.py on kt-3's silver: exit 1, conn_msg_seq the one failing class, pinned by kt-3"
if [ "$(cat "$RP/planted.rc")" = 1 ] && [ "$(grep -c '^FAIL' "$RP/planted.out")" = 1 ] \
   && grep -q '^FAIL  conn_msg_seq ' "$RP/planted.out" && grep -q 'pin run kraken_trades/kt-3 ' "$RP/planted.out"; then
  claimed planted "PASS $said" 0
else
  claimed planted "FAIL $said: exit $(cat "$RP/planted.rc"); $(grep -E '^(FAIL|parity)' "$RP/planted.out" | cut -c1-120 | tr '\n' ' ')$(tail -3 "$RP/planted.err" | tr '\n' ' ')" 0
fi
finish
