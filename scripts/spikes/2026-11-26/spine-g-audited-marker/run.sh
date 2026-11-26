#!/usr/bin/env bash
# Spike N0-4(g), owner decision O8 ("readers see only tested data, always"): which "audited"
# marker can readers follow through Trino 484 and Lakekeeper, who can move it, and what it costs.
# Table t gets a tested batch (snapshot s1) and an untested one holding a bad row (s2). Reader
# p_reader, on the read-only catalog only, has a row filter (region <> 'restricted') and a mask
# (secret -> '****') on t; reader p_strict has no grant on t, only on a definer view carrying the
# same policies on itself. Candidates:
#   1. an Iceberg tag `audited` (and a branch), set through the catalog's REST API
#   2. a view naming a snapshot id, replaced on publish
#   3. a writer/reader catalog pair where publish moves a pointer: a second catalog entry for
#      the same table, registered onto the writer's metadata file
# Then who can create or move a tag or branch (Trino, under file rules and allow-all; the REST
# catalog; Spark), the publish step's time, compare-and-set, two tables in one commit, a schema
# change under the views, and a tag under expire_snapshots.
# Writes up in docs/research/2026-11-26-release-spine-spikes.md, section (g).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../spine-lib.sh"
SCHEMAS=(spike_n04dbt_g spike_n04dbt_g_pub)
NS=spike_n04dbt_g W=dbt_spike R=p_reader R2=p_strict
cat > "$WORK/rules.json" <<EOF
{
  "catalogs": [{"catalog": "system", "allow": "read-only"},
               {"user": "$W", "catalog": "spike|spike_ro", "allow": "all"},
               {"catalog": "spike_ro", "allow": "read-only"}],
  "schemas": [{"user": "$W", "catalog": "spike", "owner": true}, {"owner": false}],
  "tables": [
    {"user": "$W", "privileges": ["SELECT", "INSERT", "DELETE", "UPDATE", "OWNERSHIP", "GRANT_SELECT"]},
    {"user": "$R", "catalog": "spike_ro", "schema": "$NS", "table": "t", "privileges": ["SELECT"],
     "filter": "region <> 'restricted'", "columns": [{"name": "secret", "mask": "'****'"}]},
    {"user": "$R", "catalog": "spike_ro", "schema": "$NS", "table": "t_audited|t_pinned|t_audited_w|t_cols", "privileges": ["SELECT"]},
    {"user": "$R2", "catalog": "spike_ro", "schema": "$NS", "table": "t_audited_def", "privileges": ["SELECT"],
     "filter": "region <> 'restricted'", "columns": [{"name": "secret", "mask": "'****'"}]}
  ]
}
EOF
spine_start g 18194 "$WORK/rules.json"
T=spike.$NS.t RT=spike_ro.$NS.t
PREFIX=$(lk_prefix)
# ref <name> <tag|branch> <snapshot> <expected current id|null>: one REST commit with a
# compare-and-set requirement; prints the HTTP status and milliseconds, or the error.
ref() {
  python3 - "$LK/$PREFIX/namespaces/$NS/tables/${5:-t}" "$@" <<'EOF'
import json, sys, time, urllib.error, urllib.request
url, name, kind, snap, expect = sys.argv[1:6]
body = {"requirements": [{"type": "assert-ref-snapshot-id", "ref": name, "snapshot-id": None if expect == "null" else int(expect)}],
        "updates": [{"action": "set-snapshot-ref", "ref-name": name, "type": kind, "snapshot-id": int(snap)}]}
req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
t0 = time.perf_counter()
try:
    status = urllib.request.urlopen(req).status
    print(status, "%.1f ms" % ((time.perf_counter() - t0) * 1000))
except urllib.error.HTTPError as e:
    print(e.code, json.load(e)["error"]["message"][:110])
EOF
}
rows() { q "${2:-$R}" "SELECT array_join(array_agg(format('%s:%s:%s', id, region, secret) ORDER BY id), ' ') FROM $1"; }

q $W "CREATE SCHEMA spike.$NS"
q $W "CREATE TABLE $T (id bigint, region varchar, secret varchar, amount bigint)"
q $W "INSERT INTO $T VALUES (1, 'open', 's-1', 10), (2, 'open', 's-2', 20), (3, 'restricted', 's-3', 30)"
s1=$(snapshot $W spike.$NS t)
echo "tested batch: snapshot $s1 (ids 1-3); tag audited -> it: $(ref audited tag "$s1" null)"
q $W "INSERT INTO $T VALUES (4, 'open', 's-4', -999)"
s2=$(snapshot $W spike.$NS t)
echo "untested build: snapshot $s2 adds id 4, amount -999 (the bad row); main is at $s2, audited at $s1"
echo "refs: $(q $W "SELECT array_join(array_agg(name || '=' || type || ':' || CAST(snapshot_id AS varchar)), ' ') FROM spike.$NS.\"t\$refs\"")"

echo; echo "== 1. reads as $R (row filter and mask on t), the tag and the views"
q $W "CREATE VIEW spike.$NS.t_audited SECURITY INVOKER AS SELECT * FROM $RT FOR VERSION AS OF 'audited'"
q $W "CREATE VIEW spike.$NS.t_pinned SECURITY INVOKER AS SELECT * FROM $RT FOR VERSION AS OF $s1"
q $W "CREATE VIEW spike.$NS.t_audited_w SECURITY INVOKER AS SELECT * FROM $T FOR VERSION AS OF 'audited'"
q $W "CREATE VIEW spike.$NS.t_audited_def SECURITY DEFINER AS SELECT * FROM $T FOR VERSION AS OF 'audited'"
echo "  t (main), directly:                       $(rows $RT)"
echo "  t FOR VERSION AS OF 'audited', directly:   $(rows "$RT FOR VERSION AS OF 'audited'")"
echo "  t FOR VERSION AS OF $s1:    $(rows "$RT FOR VERSION AS OF $s1")"
echo "  view t_audited (invoker, over the tag):   $(rows spike_ro.$NS.t_audited)"
echo "  view t_pinned (invoker, names $s1): $(rows spike_ro.$NS.t_pinned)"
echo "  view t_audited_w (invoker, base in the writer catalog): $(qx $R "SELECT * FROM spike_ro.$NS.t_audited_w")"
echo "  as $R2, definer view t_audited_def (over the tag, base in the writer catalog, policies on the view):"
echo "    the view: $(rows spike_ro.$NS.t_audited_def $R2)"
echo "    t: $(qx $R2 "SELECT * FROM $RT")"
echo "    t\$files: $(qx $R2 "SELECT * FROM spike_ro.$NS.\"t\$files\"")"
bypass=$(q $R "SELECT count(*) FROM $RT WHERE id = 4")

echo; echo "== 2. who can make or move a marker"
echo "  Trino, a tag:           $(qx $W "ALTER TABLE $T CREATE TAG audited2")"
echo "  Trino, a branch:        $(qx $W "CREATE BRANCH audited_b IN TABLE $T")"
trace=$(docker exec "$NAME" trino --user $W --debug --execute "CREATE BRANCH audited_b IN TABLE $T" 2>&1 || true)
echo "    its stack: $(grep -m2 -oE '(java\.lang\.[A-Za-z]+Exception|at io\.trino\.plugin\.base\.security\.FileBasedSystemAccessControl\.[A-Za-z]+)' <<< "$trace" | tr '\n' ' ')"
echo "  REST, a branch:         $(ref audited_b branch "$s1" null)"
echo "  Trino reads the branch: $(rows "$RT FOR VERSION AS OF 'audited_b'")"
echo "  Trino, fast-forward:    $(qx $W "ALTER BRANCH audited_b IN TABLE $T FAST FORWARD TO main")"
echo "  Trino, write a branch:  $(qx $W "INSERT INTO $T @ audited_b VALUES (5, 'open', 's-5', 50)")"
# Spark (Iceberg's SQL extensions) on the same table, through the same REST catalog, in the stack's
# Spark container: only in a gap of the lake ingest (waited for), at 512 MB, without an event log.
busy() { docker exec k2-spark-iceberg pgrep -f ingest.py >/dev/null 2>&1; }
for _ in $(seq 1 60); do busy || break; sleep 5; done   # up to 5 minutes for the ingest's gap
if busy; then
  echo "  Spark:                  not run (the lake ingest ran throughout a 5-minute wait)"
else
  wait_mem
  spark_rc=0
  docker exec -w /tmp -e NS=$NS -e S1="$s1" -e S2="$s2" k2-spark-iceberg sh -c '/opt/spark/bin/spark-sql --driver-memory 512m \
    --conf spark.ui.enabled=false --conf spark.eventLog.enabled=false \
    --conf spark.sql.catalog.spk=org.apache.iceberg.spark.SparkCatalog --conf spark.sql.catalog.spk.type=rest \
    --conf spark.sql.catalog.spk.uri=http://lakekeeper:8181/catalog --conf spark.sql.catalog.spk.warehouse=reference_data \
    --conf spark.sql.catalog.spk.io-impl=org.apache.iceberg.aws.s3.S3FileIO --conf "spark.sql.catalog.spk.s3.endpoint=$AWS_S3_ENDPOINT" \
    --conf spark.sql.catalog.spk.s3.path-style-access=true \
    -e "ALTER TABLE spk.$NS.t CREATE OR REPLACE TAG audited_spark AS OF VERSION $S1; ALTER TABLE spk.$NS.t REPLACE TAG audited_spark AS OF VERSION $S2"' \
    > "$WORK/spark.log" 2>&1 || spark_rc=$?
  echo "  Spark, CREATE OR REPLACE TAG at $s1 then REPLACE TAG at $s2: exit $spark_rc; the tag now:" \
    "$(q $W "SELECT coalesce(max(CAST(snapshot_id AS varchar)), 'absent') FROM spike.$NS.\"t\$refs\" WHERE name = 'audited_spark'")"
fi

echo; echo "== 3. publish: move the tag to $s2 (the build's tests 'passed'), compare-and-set"
echo "  stale publisher (expects audited = $s2):  $(ref audited tag "$s2" "$s2")"
echo "  publisher (expects audited = $s1):        $(ref audited tag "$s2" "$s1")"
echo "  the next read, view t_audited:           $(rows spike_ro.$NS.t_audited)"
moved=$(q $R "SELECT count(*) FROM spike_ro.$NS.t_audited WHERE id = 4")
python3 - "$LK/$PREFIX/namespaces/$NS/tables/t" "$s1" "$s2" <<'EOF' > "$WORK/tag_ms"
import json, statistics, sys, time, urllib.request
url, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
ms, cur = [], b
for i in range(20):
    nxt = a if cur == b else b
    body = {"requirements": [{"type": "assert-ref-snapshot-id", "ref": "audited", "snapshot-id": cur}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": "audited", "type": "tag", "snapshot-id": nxt}]}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.perf_counter(); urllib.request.urlopen(req); ms.append((time.perf_counter() - t0) * 1000); cur = nxt
print("median %.1f ms, max %.1f ms, n=%d" % (statistics.median(ms), max(ms), len(ms)))
EOF
echo "  tag move, REST commit from the host:     $(cat "$WORK/tag_ms")"
py - "$PORT" "$NS" "$s1" "$s2" <<'EOF' > "$WORK/view_ms"
import statistics, sys, time, trino
port, ns, a, b = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cur = trino.dbapi.connect(host="127.0.0.1", port=int(port), user="dbt_spike").cursor()
ms = []
for i in range(20):
    t0 = time.perf_counter()
    cur.execute(f"CREATE OR REPLACE VIEW spike.{ns}.t_pinned SECURITY INVOKER AS "
                f"SELECT * FROM spike_ro.{ns}.t FOR VERSION AS OF {a if i % 2 else b}")
    cur.fetchall(); ms.append((time.perf_counter() - t0) * 1000)
print("median %.1f ms, max %.1f ms, n=%d" % (statistics.median(ms), max(ms), len(ms)))
EOF
echo "  view replace, CREATE OR REPLACE VIEW:    $(cat "$WORK/view_ms")"
# A product with two outputs moves both tags or neither: the catalog's multi-table commit.
q $W "CREATE TABLE spike.$NS.t2 AS SELECT id, amount FROM $T"
p2=$(snapshot $W spike.$NS t2)
pair() {
  python3 - "$LK/$PREFIX/transactions/commit" "$NS" "$@" <<'EOF'
import json, sys, urllib.error, urllib.request
url, ns, s_t, s_t2, expect_t2 = sys.argv[1:]
def change(table, snap, expect):
    return {"identifier": {"namespace": [ns], "name": table},
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": "audited_pair", "snapshot-id": expect}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": "audited_pair", "type": "tag", "snapshot-id": snap}]}
body = {"table-changes": [change("t", int(s_t), None), change("t2", int(s_t2), None if expect_t2 == "null" else int(expect_t2))]}
try:
    print(urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})).status)
except urllib.error.HTTPError as e:
    print(e.code, json.load(e)["error"]["message"][:100])
EOF
}
tags() { echo "t: $(q $W "SELECT count(*) FROM spike.$NS.\"t\$refs\" WHERE name = 'audited_pair'"), t2: $(q $W "SELECT count(*) FROM spike.$NS.\"t2\$refs\" WHERE name = 'audited_pair'")"; }
echo "  two tables, one commit, t2's requirement stale: $(pair "$s2" "$p2" 1); audited_pair tags now $(tags)"
echo "  two tables, one commit:                         $(pair "$s2" "$p2" null); audited_pair tags now $(tags)"
echo "  a tag never set (a product before its first passing build): $(qx $R "SELECT * FROM $RT FOR VERSION AS OF 'never_set'")"
# A schema change under the tag: views store their columns, so a SELECT * view goes stale when the
# audited snapshot gains a column; one naming the contract's columns does not.
q $W "CREATE VIEW spike.$NS.t_cols SECURITY INVOKER AS SELECT id, region, secret, amount FROM $RT FOR VERSION AS OF 'audited'"
q $W "ALTER TABLE $T ADD COLUMN note varchar"
q $W "INSERT INTO $T VALUES (7, 'open', 's-7', 70, 'added')"
echo "  a column added, the tag moved to it: $(ref audited tag "$(snapshot $W spike.$NS t)" "$s2")"
echo "    view t_audited (SELECT *):           $(qx $R "SELECT * FROM spike_ro.$NS.t_audited")"
echo "    view t_cols (the contract's columns): $(rows spike_ro.$NS.t_cols)"

echo; echo "== 4. a writer/reader pair, publish as a pointer: a second catalog entry onto t's metadata file"
q $W "CREATE SCHEMA spike.${NS}_pub"
loc=$(lk_table $NS t | python3 -c 'import json, sys; print(json.load(sys.stdin)["metadata-location"])')
reg=$(curl -s -X POST "$LK/$PREFIX/namespaces/${NS}_pub/register" -H 'Content-Type: application/json' \
  -d "{\"name\": \"t\", \"metadata-location\": \"$loc\"}" | python3 -c 'import json, sys; e = json.load(sys.stdin).get("error"); print(e["code"], e["message"]) if e else print("registered")')
echo "  register ${NS}_pub.t onto t's current metadata file: $reg"

echo; echo "== 5. the tag under expire_snapshots (retention 0 s): two more commits, tag on $s1"
ref audited tag "$s1" "$(snapshot $W spike.$NS t audited)" >/dev/null
q $W "INSERT INTO $T (id, region, secret, amount) VALUES (6, 'open', 's-6', 60)"; q $W "DELETE FROM $T WHERE id = 6"
docker exec "$NAME" trino --user $W --session spike.expire_snapshots_min_retention=0s \
  --execute "ALTER TABLE $T EXECUTE expire_snapshots(retention_threshold => '0s')" 2>&1 | grep -v '^ALTER TABLE' || true
echo "  (refs now: $(q $W "SELECT array_join(array_agg(name || ':' || CAST(snapshot_id AS varchar) ORDER BY name), ' ') FROM spike.$NS.\"t\$refs\""))"
echo "  snapshots left: $(q $W "SELECT array_join(array_agg(CAST(snapshot_id AS varchar) ORDER BY committed_at), ' ') FROM spike.$NS.\"t\$snapshots\"")"
echo "  t FOR VERSION AS OF 'audited' after expiry: $(rows "$RT FOR VERSION AS OF 'audited'")"
kept=$(q $W "SELECT count(*) FROM spike.$NS.\"t\$snapshots\" WHERE snapshot_id = $s1")

echo; echo "== 6. Trino with access-control allow-all (restart), the same branch statements"
echo access-control.name=allow-all > "$WORK/etc/access-control.properties"
docker restart "$NAME" >/dev/null; spine_wait
echo "  CREATE BRANCH:      $(qx $W "CREATE BRANCH audited_c IN TABLE $T")"
echo "  FAST FORWARD:       $(qx $W "ALTER BRANCH audited_b IN TABLE $T FAST FORWARD TO main")"
echo "  INSERT INTO t @ b:  $(qx $W "INSERT INTO $T @ audited_b VALUES (5, 'open', 's-5', 50)")"
echo "  SHOW BRANCHES:      $(q $W "SHOW BRANCHES IN TABLE $T" 2>&1 | tr '\n' ' ')(audited_b exists in the catalog)"

echo
echo "finding (g):"
echo "  A tag works as the marker readers follow: Trino 484 reads FOR VERSION AS OF 'audited' (tag or branch) through the"
echo "  read-only catalog, re-resolved on every query, and row filters and masks on t apply to such a read, directly and"
echo "  through an invoker view, so no definer view is needed to follow it. A view naming a snapshot id behaves the same."
echo "  Trino cannot make or move it: no tag statement; its branch statements fail (file rules: internal error; allow-all:"
echo "  the Iceberg connector does not support them). The catalog's REST API can, and so can Spark, which issues the same"
echo "  commit$(if [ -n "${spark_rc+x}" ]; then [ "$spark_rc" = 0 ] && echo " (run here: exit 0)" || echo " (run here: FAILED, exit $spark_rc)"; else echo " (not run here: no ingest gap)"; fi)."
echo "  Publish = one REST commit with a compare-and-set requirement ($(cat "$WORK/tag_ms")); a stale publisher gets 409,"
echo "  and a product's tables move together or not at all in one multi-table commit. Replacing a view instead: $(cat "$WORK/view_ms")."
[ "$bypass" = 1 ] && echo "  NOT enforced by itself: the same reader reads t at main and saw the untested bad row (id 4); following the tag is a" \
  && echo "  convention unless readers lose SELECT on t, which only a definer view carrying the policies on itself survives."
[ "$moved" = 1 ] && echo "  After the move the view showed the new snapshot at once."
[ "$kept" = 1 ] && echo "  expire_snapshots at 0 s kept every snapshot a ref names: a tag pins the audited snapshot and its files until it moves."
echo "  A view over the tag must name the contract's columns: a SELECT * view went stale when the audited snapshot gained one."
echo "  A tag never set refuses the read (Cannot find snapshot with reference name), so the first passing build creates it."
echo "  A second catalog entry for the same table (a pointer two catalogs could hold apart) is refused: $reg."
