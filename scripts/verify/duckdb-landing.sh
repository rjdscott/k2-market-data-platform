#!/usr/bin/env bash
# W0-11 and W1-18 (phases 004 and 005): DuckDB in the tools image reads a Kafka product's landing at a
# pinned snapshot through Lakekeeper with the tools MinIO key alone, its extensions baked into the
# image; then make parity-silver's and make parity-ohlcv's scripts on tables the chain built, clean
# and planted. ONE throwaway lake with a Redpanda (prefix duckdb-landing, K2_THROWAWAY_REDPANDA=1), the
# phase 004 chain run as run-path-lib.sh runs it (the release's own Spark tasks and the wrapper's
# builds), and the tools image built from this tree as tools:duckdb-landing, run as mk/parity.mk runs
# it: the tools key by -e name, this tree's scripts/ and docker/lake/ read-only, 1 CPU / 2G. Every tools
# run is on <prefix>-sealed, an --internal network holding only Lakekeeper, MinIO and the Redpanda (the
# lake's own network reaches out), and prints its cgroup's memory.peak.
#
#   bash scripts/verify/duckdb-landing.sh <release dir>   # KEEP=1 keeps the scratch directory
#
# <release dir> is the one run-path.sh takes (rp_start's checks). Frames are the chain's synthetic
# ones (rp_chain_files) and four made here: reflag-1.jsonl, a frame of ALPHA/USD trade 70 and DELTA/USD
# trade 71 (two symbols, so no key: any of the 3 partitions), and reflag-2.jsonl, ALPHA/USD trade 70
# again (keyed, so partition 2, the last, by murmur2), seeded after it, so the first is older whatever
# partition it lands on; and extra.jsonl, BTC/USD trades 5 and 6. Claims, one PASS or FAIL line each:
#   1 the image holds DuckDB 1.4.4, and httpfs, avro and iceberg load from /opt/k2/duckdb with no
#     network at all (--network none) and autoinstall off; an extension not baked in (spatial) does not
#   2 after two kraken-trades loads, DuckDB on the tools key reads the landing AT each load's snapshot
#     (its first two connector commits): per partition the rows, distinct offsets and first and last
#     offset its spans (k2.kafka-starts to k2.kafka-offsets) give, the total its total-records; the
#     first read again after the second, unchanged
#   3 that connection holds the tools key and no other storage key, writes nothing (a COPY to the
#     lake's bucket is refused, 403), and the sealed network has no way out (a TCP connect fails)
#   4 kt-2 published ALPHA/USD 70's keyed delivery and held the two-symbol frame (DELTA/USD unknown);
#     listing-2 names DELTA/USD and kt-3 takes the held frame back, flagging the published delivery a
#     duplicate: a MERGE update, so silver_trade's tested snapshot holds position deletes. Pinned by
#     kt-3's build record, parity_silver.py passes, every class empty, and DuckDB's row count of that
#     snapshot is its total-records less its total-position-deletes
#   5 ca-3 rewrote the ALPHA/USD windows at every timeframe (MERGE updates, position deletes):
#     parity_ohlcv.py passes, pinned by ca-3's records, each timeframe's DuckDB count total-records less
#     total-position-deletes, deletes in each
#   6 a release of this tree whose silver_trade writes conn_msg_seq one too high and leaves out BTC/USD
#     trade 5 (its unit tests name neither) builds kt-4 over extra.jsonl: parity_silver.py exits 1,
#     conn_msg_seq and missing the only failing classes
#   7 a release whose ohlcv_5m adds 1 to the volume of every instrument but the unit tests' i-aaa builds
#     ca-4 after ct-4: parity_ohlcv.py exits 1, ohlcv_5m.volume the only failing class
# INFO lines: each tools run's memory.peak against its 2G.
#
# One container job (run-path-lib.sh's guards); a killed run leaves the lake to
# `bash scripts/lib/throwaway-lake.sh down duckdb-landing` and `docker network rm duckdb-landing-sealed`,
# the images to `docker rmi dbt-runner:duckdb-landing tools:duckdb-landing`.
set -uo pipefail
# shellcheck source=scripts/lib/run-path-lib.sh
. "$(dirname "$0")/../lib/run-path-lib.sh"
rp_start duckdb-landing "${1:-}" 7
TOOLS=tools:duckdb-landing SEALED=duckdb-landing-sealed
sealed_down() {
  local c
  for c in lakekeeper minio redpanda; do docker network disconnect -f "$SEALED" "$P-$c" >/dev/null 2>&1; done
  docker network rm "$SEALED" >/dev/null 2>&1
}
trap 'sealed_down; rp_cleanup; docker rmi -f "$TOOLS" >/dev/null 2>&1' EXIT
# claim 6's release: silver_trade's conn_msg_seq one too high, BTC/USD trade 5 left out
rp_fail_release "$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/market-capture/kraken-trades/models/silver_trade.sql"
sql = p.read_text()
seq, tail = "    b.conn_msg_seq,\n", "\nfrom flagged\nwhere is_new or is_duplicate <> was_duplicate\n"
assert sql.count(seq) == 1 and sql.endswith(tail), "silver_trade.sql's conn_msg_seq or its last where moved: move the plant"
sql = sql.replace(seq, "    b.conn_msg_seq + 1 as conn_msg_seq,\n").removesuffix(tail)
p.write_text(sql + "\nfrom flagged\nwhere (is_new or is_duplicate <> was_duplicate) and not (venue_symbol = 'BTC/USD' and trade_id = 5)\n")
PY
)" seq
IDV=$IDF
# claim 7's release: ohlcv_5m's volume one too high off the unit tests' instrument
rp_fail_release "$(cat <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "contracts/market-analytics/candles/models/ohlcv_5m.sql"
sql = p.read_text()
old = "  sum(c.quantity) as volume,\n"
assert sql.count(old) == 1, "ohlcv_5m.sql's volume moved: move the plant"
p.write_text(sql.replace(old, "  sum(c.quantity) + case when c.instrument_id = 'i-aaa' then 0 else 1 end as volume,\n"))
PY
)" c5m
IDC=$IDF IDF= RP_MORE="seq c5m"
rp_image
docker build -q -f docker/tools/Dockerfile -t "$TOOLS" . > "$W/tools-build.log" 2>&1 || { tail -20 "$W/tools-build.log" >&2; die "the tools image did not build"; }
export K2_THROWAWAY_REDPANDA=1
rp_lake  # exports every engine's pair from the lake's s3.env; the tools runs below pass the tools pair alone
docker network create --internal --label "k2.throwaway=$P" "$SEALED" > /dev/null || die "the sealed network was not made"
for c in lakekeeper minio redpanda; do
  docker network connect --alias "$c" "$SEALED" "$P-$c" || die "$P-$c did not join $SEALED"
done
rp_chain_files
python3 - "$RP" <<'PY' || die "the frames made here could not be written"
# Synthetic, in the fixture's line format, after the fixture's frames.
import json, sys
from pathlib import Path
rp = Path(sys.argv[1])
def line(at_s, *trades):
    payload = json.dumps({"channel": "trade", "type": "update", "data": list(trades)}, separators=(",", ":"))
    return json.dumps({"recv_ts_ns": 1_000_000_000_000_000_000 + at_s * 1_000_000_000, "payload": payload}) + "\n"
def trade(symbol, trade_id, at, price):
    return {"symbol": symbol, "side": "buy", "price": price, "qty": 0.5, "ord_type": "limit", "trade_id": trade_id,
            "timestamp": f"2001-09-09T01:{at}.000000Z"}
# two files, seeded one after the other: one seed's records reach a partition in no set order
(rp / "reflag-1.jsonl").write_text(line(60, trade("ALPHA/USD", 70, "48:00", 2.5), trade("DELTA/USD", 71, "48:00", 7.0)))
(rp / "reflag-2.jsonl").write_text(line(61, trade("ALPHA/USD", 70, "48:00", 2.5)))
(rp / "extra.jsonl").write_text(line(70, trade("BTC/USD", 5, "47:30", 1001.0)) + line(71, trade("BTC/USD", 6, "47:31", 1002.0)))
PY

tools() {  # tools <label> <argv...>: the tools image on the sealed network with the tools pair alone; its memory.peak to stderr
  local label=$1; shift
  docker run --rm -i --name "$P-tools-$label" --label "k2.throwaway=$P" --network "$SEALED" --cpus 1 --memory 2g \
    --user "$(id -u):$(id -g)" -e K2_TOOLS_S3_ACCESS_KEY -e K2_TOOLS_S3_SECRET_KEY \
    -v "$STATE/releases:/k2/releases:ro" -v "$REPO/scripts:/opt/k2/scripts:ro" -v "$REPO/docker/lake:/opt/k2/docker/lake:ro" \
    "$TOOLS" sh -c '"$@"; rc=$?; echo "memory.peak $(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo unknown)" >&2; exit $rc' \
    sh "$@" > "$RP/$label.out" 2> "$RP/$label.err"
  echo $? > "$RP/$label.rc"
  echo "$P: tools $label exit $(cat "$RP/$label.rc")"
}
probe_out() {  # probe_out <label>: a tools probe's lines for claimed, its stderr's last lines when it printed no verdict
  grep -E '^(PASS|FAIL|INFO) ' "$RP/$1.out" || tail -4 "$RP/$1.err"
}
# The probe behind claims 4 to 7: a parity script's main in the tools image, its lines as INFO, then
# each table it pinned checked against its catalog summary (DuckDB's count is total-records less
# total-position-deletes, and how many deletes), and one verdict: the exit wanted and the failing
# classes wanted, exactly.
PARITY_PROBE=$(cat <<'PY'
import contextlib, io, json, re, sys
sys.path[:0] = ["/opt/k2/scripts", "/opt/k2/docker/lake"]
from verify.catalog_drift import Catalog
kind, release, want_exit, want_classes, said = sys.argv[1], sys.argv[2], int(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    if kind == "silver":
        import parity_silver as M
        code = M.main(["--release", release, "--product", "kraken-trades"])
    else:
        import parity_ohlcv as M
        code = M.main(["--release", release])
out = buf.getvalue()
print("\n".join(f"INFO   | {x}" for x in out.splitlines()))
failing = sorted(x.split()[1] for x in out.splitlines() if x.startswith("FAIL"))
tables = {"silver_trade": ("market_capture", "kraken_trades__base")} if kind == "silver" else \
    {t: ("market_analytics", "candles__base") for t in ("ohlcv_1m", "ohlcv_5m", "ohlcv_1h", "ohlcv_1d")}
counts = {m[1]: int(m[2]) for m in re.finditer(r"^INFO\s+\S+\s+recomputed \d+, \S+\.(\w+) (\d+)$", out, re.M)}
problems, deletes = [], {}
for t, (wh, ns) in tables.items():
    pin = re.search(rf"{t}@(\d+)", out)
    if not pin:
        problems.append(f"no pin of {t}")
        continue
    meta = Catalog("http://lakekeeper:8181/catalog", wh).table(ns, t)
    [snap] = [s for s in meta["snapshots"] if s["snapshot-id"] == int(pin[1])]
    s = snap["summary"]
    deletes[t] = int(s.get("total-position-deletes", 0))
    live = int(s["total-records"]) - deletes[t] - int(s.get("total-equality-deletes", 0))
    if counts.get(t) != live:
        problems.append(f"DuckDB read {counts.get(t)} rows of {t}@{pin[1]}, its summary {live} live ({s['total-records']} less {deletes[t]} deleted)")
if want_exit == 0 and not all(deletes.values()):
    problems.append(f"a pinned table holds no position delete: {deletes}")
ok = code == want_exit and failing == want_classes and not problems
print(("PASS" if ok else "FAIL") + f" {said} (exit {code}, failing {failing or 'none'}, position deletes {deletes})"
      + ("" if ok else f": want exit {want_exit}, failing {want_classes}; {'; '.join(problems)}"))
PY
)
parity_claim() {  # parity_claim <label> <silver|ohlcv> <exit> <failing classes, JSON> <claim>
  tools "$1" python3 -c "$PARITY_PROBE" "$2" "/k2/releases/$ID" "$3" "$4" "$5"
  claimed "$1" "$(probe_out "$1")" "$(cat "$RP/$1.rc")"
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
except duckdb.Error:
    spatial = None
ok = duckdb.__version__ == "1.4.4" and loaded == ["avro", "httpfs", "iceberg"] and spatial is None
print(("PASS" if ok else "FAIL") + f" DuckDB {duckdb.__version__} loads {loaded} from /opt/k2/duckdb with no network; "
      + (spatial or "spatial, not baked in, does not load"))
PY
)
claimed extensions "$out" $?

listing ki-1 listing-1.json; build "$ID" "$KI" ki-1; build "$ID" reference-data.currency-master cm-1; build "$ID" "$SM" sm-1
kseed "$RP/frames.jsonl"; trades kt-1
kseed "$RP/reflag-1.jsonl"; kseed "$RP/reflag-2.jsonl"; trades kt-2; build "$ID" "$CT" ct-2; build "$ID" "$CA" ca-2

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
claimed landing "$(probe_out landing)" "$(cat "$RP/landing.rc")"

# 3. the key it holds, nothing written with it, no way out of the sealed network
tools key python3 - <<'PY'
import os, socket, sys, tempfile
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
out = []
for host in ("extensions.duckdb.org", "1.1.1.1"):
    try:
        socket.create_connection((host, 443), timeout=5).close()
        out.append(f"{host}:443 answered")
    except OSError:
        pass
ok = keys == ["K2_TOOLS_S3_ACCESS_KEY", "K2_TOOLS_S3_SECRET_KEY"] and n > 0 and refused is None and not out
print(("PASS" if ok else "FAIL") + f" holding {keys} alone, DuckDB reads {n} landing rows, a COPY to the lake's bucket is "
      + (refused or "refused (403)") + "; " + ("; ".join(out) or "no TCP connect leaves the sealed network"))
PY
claimed key "$(probe_out key)" "$(cat "$RP/key.rc")"

# 4 and 5. the held frame taken back: updates, so position deletes, then parity on the clean chain
listing ki-2 listing-2.json; build "$ID" "$KI" ki-2; build "$ID" "$SM" sm-2
trades kt-3; build "$ID" "$CT" ct-3; build "$ID" "$CA" ca-3
parity_claim silver silver 0 '[]' "kt-3's silver_trade, its held frame taken back: parity_silver.py every class empty"
grep -q 'pin run kraken_trades/kt-3 ' "$RP/silver.out" || claimed silver-pin "FAIL parity_silver.py was not pinned by kt-3" 0
parity_claim ohlcv ohlcv 0 '[]' "ca-3's candles, the ALPHA/USD windows rewritten: parity_ohlcv.py every class empty"
grep -q 'pin run candles/ca-3 ' "$RP/ohlcv.out" || claimed ohlcv-pin "FAIL parity_ohlcv.py was not pinned by ca-3" 0

# 6 and 7. the planted releases
kseed "$RP/extra.jsonl"; trades kt-4 "$IDV"
parity_claim planted-silver silver 1 '["conn_msg_seq", "missing"]' "kt-4 by the planted silver_trade: parity_silver.py fails conn_msg_seq and missing alone"
build "$ID" "$CT" ct-4; build "$IDC" "$CA" ca-4
parity_claim planted-ohlcv ohlcv 1 '["ohlcv_5m.volume"]' "ca-4 by the planted ohlcv_5m: parity_ohlcv.py fails ohlcv_5m.volume alone"

for f in "$RP"/*.err; do
  peak=$(sed -n 's/^memory\.peak //p' "$f" | tail -1)
  [ -n "$peak" ] && echo "INFO tools $(basename "$f" .err): memory.peak $([ "$peak" = unknown ] && echo unknown || echo "$((peak / 1048576)) MiB") of 2048 MiB" | tee -a "$W/claims"
done
finish
